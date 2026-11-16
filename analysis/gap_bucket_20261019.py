# -*- coding: utf-8 -*-
"""
P2: T+1开盘缺口五桶分层 + VWAP30反事实（只读，零生产改动）
数据：10/18 fork L4生产档逐笔 + qfq日线（dragon_quant_qfq.db）+ 5分钟库（dragon_quant_minute.db）
口径：
  gap = 买入日开盘 / 信号日(前一交易日)收盘 - 1
  前瞻收益 = 固定持有口径（买入价→买入日后第N交易日收盘），剥离出场规则，纯测入场质量
  VWAP30 = 买入日09:30-10:00六根5分钟K的成交额/成交量（2024-2026段做）
输出：analysis/gap_bucket_20261019_out.json/.md（脚本直出）
"""
import csv, json, sqlite3, os
from collections import defaultdict
import pandas as pd

BASE = "D:/zcodesj/reports/backtest_full_20261018_dose_n15"
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
CAL = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
MIN5 = r"D:/workbuddyspace/factor_engine/data/dragon_quant_minute.db"
OUT_JSON = "D:/zcodesj/analysis/gap_bucket_20261019_out.json"
OUT_MD = "D:/zcodesj/analysis/gap_bucket_20261019_out.md"

conn_q = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
conn_c = sqlite3.connect(f"file:{CAL}?mode=ro", uri=True)

# 交易日历
cal = [r[0] for r in conn_c.execute(
    "SELECT date_str FROM trading_calendar WHERE date_str BETWEEN '2016-01-01' AND '2026-10-16' ORDER BY date_str")]
cal_set = set(cal)
cal_idx = {d: i for i, d in enumerate(cal)}
# 规范为 YYYYMMDD 便于qfq查询
cal8 = [d.replace("-", "") for d in cal]

# 逐笔
trades = list(csv.DictReader(open(f"{BASE}/bp_ep_core_trades_full.csv", encoding="utf-8-sig")))
print("L4逐笔总数:", len(trades))

# 批量取所需 (code, buy_date) 的开盘/收盘 + 前瞻收盘
need = set()
for t in trades:
    bd = t["买入日期"].replace("-", "")
    need.add((t["股票代码"], bd))

# 信号日 = 买入日前一交易日
codes = sorted({c for c, _ in need})
print("涉及股票数:", len(codes))

# 拉这些股票的qfq日线（批量）
ph = ",".join("?" * len(codes))
rows = []
for i in range(0, len(codes), 400):
    chunk = codes[i:i+400]
    ph_c = ",".join("?" * len(chunk))
    rows.extend(conn_q.execute(
        f"SELECT code, date, open, close FROM stock_daily_meta_qfq WHERE code IN ({ph_c}) AND date >= '20160101'",
        chunk).fetchall())
px = defaultdict(dict)  # code -> {date8: (open, close)}
for c, d, o, cl in rows:
    px[c][d] = (o, cl)
print("价格行数:", len(rows))

# 日历8位索引
idx8 = {d: i for i, d in enumerate(cal8)}

def fwd_close(code, buy_d8, n):
    """买入日后第n个交易日收盘价"""
    i = idx8.get(buy_d8)
    if i is None:
        return None
    for j in range(i+1, min(i+n+1, len(cal8))):
        v = px.get(code, {}).get(cal8[j])
        if v is not None:
            if j - i == n:
                return v[1]
        # 跳过停牌缺失日，按日历数n
    return None

def prev_cal(d8, k=1):
    i = idx8.get(d8)
    if i is None or i < k:
        return None
    return cal8[i-k]

BUCKETS = [("<-2%", -999, -2.0), ("-2~0", -2.0, 0.0), ("0~2", 0.0, 2.0), ("2~4", 2.0, 4.0), (">4%", 4.0, 999)]

def bucket_of(g):
    for name, lo, hi in BUCKETS:
        if lo <= g < hi:
            return name
    return None

def stats_of(items):
    n = len(items)
    if n == 0:
        return {"n": 0}
    wins5 = [x for x in items if x["r5"] is not None and x["r5"] > 0]
    loss5 = [x for x in items if x["r5"] is not None and x["r5"] < 0]
    wins20 = [x for x in items if x["r20"] is not None and x["r20"] > 0]
    loss20 = [x for x in items if x["r20"] is not None and x["r20"] < 0]
    def pl(w, l):
        aw = sum(x["r20"] for x in w) / len(w) if w else 0
        al = sum(x["r20"] for x in l) / len(l) if l else 0
        return round(aw / abs(al), 3) if al else None
    r20s = sorted(x["r20"] for x in items if x["r20"] is not None)
    return {
        "n": n,
        "win5_pct": round(len(wins5) / max(1, sum(1 for x in items if x["r5"] is not None)) * 100, 2),
        "win20_pct": round(len(wins20) / max(1, sum(1 for x in items if x["r20"] is not None)) * 100, 2),
        "pl20": pl(wins20, loss20),
        "avg_r5": round(sum(x["r5"] for x in items if x["r5"] is not None) / max(1, sum(1 for x in items if x["r5"] is not None)), 3),
        "avg_r20": round(sum(x["r20"] for x in items if x["r20"] is not None) / max(1, sum(1 for x in items if x["r20"] is not None)), 3),
        "med_r20": round(r20s[len(r20s)//2], 3) if r20s else None,
        "p90_r20": round(r20s[int(len(r20s)*0.9)], 3) if r20s else None,
    }

records = []
miss_px = 0
for t in trades:
    code = t["股票代码"]
    bd = t["买入日期"].replace("-", "")
    sd = prev_cal(bd, 1)  # 信号日
    pb = px.get(code, {}).get(bd)
    ps = px.get(code, {}).get(sd) if sd else None
    if not pb or not ps:
        miss_px += 1
        continue
    gap = (pb[0] / ps[1] - 1) * 100
    buy_px = pb[0]
    r5 = r20 = None
    c5 = fwd_close(code, bd, 5)
    c20 = fwd_close(code, bd, 20)
    if c5 is not None:
        r5 = (c5 / buy_px - 1) * 100
    if c20 is not None:
        r20 = (c20 / buy_px - 1) * 100
    records.append({
        "code": code, "name": t["股票名称"], "buy_date": bd,
        "year": bd[:4], "gap": round(gap, 3), "bucket": bucket_of(gap),
        "r5": round(r5, 3) if r5 is not None else None,
        "r20": round(r20, 3) if r20 is not None else None,
    })
print("有效逐笔:", len(records), "缺价跳过:", miss_px)

result = {"total": len(records), "miss_px": miss_px, "buckets": {}, "by_year": {}}
for name, _, _ in BUCKETS:
    items = [r for r in records if r["bucket"] == name]
    result["buckets"][name] = stats_of(items)
for y in ["2024", "2025", "2026"]:
    byb = {}
    for name, _, _ in BUCKETS:
        items = [r for r in records if r["bucket"] == name and r["year"] == y]
        byb[name] = stats_of(items)
    result["by_year"][y] = byb

# 高开(>2%) vs 非高开(<=2%) 两桶对照（主人判定口径：高开>2~3%桶显著低）
for label, cond in [("gap>2", lambda g: g > 2), ("gap<=2", lambda g: g <= 2)]:
    items = [r for r in records if cond(r["gap"])]
    result[label] = stats_of(items)

json.dump(result, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

md = ["# P2 开盘缺口五桶分层（固定持有口径，剥离出场）", "",
      f"- 数据：L4生产档逐笔 {len(trades)} 笔，有效 {len(records)}，缺价跳过 {miss_px}",
      "- gap=买入日开盘/信号日收盘-1；r5/r20=买入价→第5/20交易日收盘", ""]
md.append("| 桶 | n | 胜率5日% | 胜率20日% | 盈亏比20 | avg_r5 | avg_r20 | med_r20 | p90_r20 |")
md.append("|---|---|---|---|---|---|---|---|---|")
for name, _, _ in BUCKETS:
    s = result["buckets"][name]
    if s.get("n"):
        md.append(f"| {name} | {s['n']} | {s['win5_pct']} | {s['win20_pct']} | {s['pl20']} | {s['avg_r5']} | {s['avg_r20']} | {s['med_r20']} | {s['p90_r20']} |")
md.append("")
for y in ["2024", "2025", "2026"]:
    md.append(f"## {y}")
    md.append("| 桶 | n | 胜率20日% | 盈亏比20 | avg_r20 | med_r20 |")
    md.append("|---|---|---|---|---|---|")
    for name, _, _ in BUCKETS:
        s = result["by_year"][y][name]
        if s.get("n"):
            md.append(f"| {name} | {s['n']} | {s['win20_pct']} | {s['pl20']} | {s['avg_r20']} | {s['med_r20']} |")
    md.append("")
open(OUT_MD, "w", encoding="utf-8").write("\n".join(md))
print(open(OUT_MD, encoding="utf-8").read())
