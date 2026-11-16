# -*- coding: utf-8 -*-
"""
P1-L0a: 信号层理论alpha（信号日收盘买入、持有20交易日收盘卖出，不可交易口径）
名单=raw口径（无close<=30过滤、纯ep/bp rank Top15）；调仓日序列=10/18 fork L4实际buy_date全集（各层统一对照）
数据：factor_pit.db (PIT) + qfq日线。只读。
"""
import csv, json, sqlite3, os
from collections import defaultdict
import pandas as pd

BASE = "D:/zcodesj/reports/backtest_full_20261018_dose_n15"
PIT = r"D:/zcodesj/factor_pit.db"
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
CAL = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
OUT_JSON = "D:/zcodesj/analysis/ladder_L0a_20261019_out.json"
OUT_MD = "D:/zcodesj/analysis/ladder_L0a_20261019_out.md"

conn_c = sqlite3.connect(f"file:{CAL}?mode=ro", uri=True)
cal8 = [r[0].replace("-", "") for r in conn_c.execute(
    "SELECT date_str FROM trading_calendar WHERE date_str BETWEEN '2015-06-01' AND '2026-10-16' ORDER BY date_str")]
idx8 = {d: i for i, d in enumerate(cal8)}

# L4 实际买入日全集 → 信号日=前一交易日
trades = list(csv.DictReader(open(f"{BASE}/bp_ep_core_trades_full.csv", encoding="utf-8-sig")))
buy_dates = sorted({t["买入日期"].replace("-", "") for t in trades})
signal_dates = []
for bd in buy_dates:
    i = idx8.get(bd)
    if i and i > 0:
        signal_dates.append(cal8[i - 1])
print("调仓期数:", len(signal_dates))

# PIT 因子表（裁剪列）
conn_p = sqlite3.connect(f"file:{PIT}?mode=ro", uri=True)
pit = pd.read_sql(
    "SELECT code, pit_avail_date, ep, bp FROM stock_factors_fundamental_pit_v2", conn_p)
conn_p.close()
pit = pit.dropna(subset=["ep", "bp"]).copy()
pit["pad"] = pit["pit_avail_date"].astype(str).str[:8].str.replace("-", "")
pit = pit.sort_values(["pad"]).reset_index(drop=True)
print("PIT行数:", len(pit))

conn_q = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
qfq_close = {}  # date8 -> {code: close}
codes_all = sorted(pit["code"].unique())
for i in range(0, len(codes_all), 400):
    chunk = codes_all[i:i+400]
    ph = ",".join("?" * len(chunk))
    for c, d, cl in conn_q.execute(
            f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20150601'", chunk):
        qfq_close.setdefault(d, {})[c] = cl
conn_q.close()

# 宇宙过滤（与fork一致：剔科创/北交/ST标记——PIT表无ST，用code前缀剔688/689/8/4/92/43；次新<60天略=理论口径声明）
def ok_code(c):
    return not (c.startswith(("688", "689", "8", "4", "92", "43")))

def top15_raw(sd):
    """raw名单: 无close过滤, 纯score Top15（与fork bp_ep_signal raw模式对齐）"""
    sub = pit[pit["pad"] <= sd]
    # 每 code 取 pad 最新一行
    latest = sub.groupby("code").tail(1)
    latest = latest[latest["code"].map(ok_code)]
    if len(latest) < 20:
        return []
    latest = latest.copy()
    latest["score"] = (latest["ep"].rank(pct=True) + latest["bp"].rank(pct=True)) / 2
    return latest.nlargest(15, "score")["code"].tolist()

def fwd_n(sd, n=20):
    i = idx8.get(sd)
    if i is None or i + n >= len(cal8):
        return cal8[-1] if i is not None else None
    return cal8[i + n]

records = []
for k, sd in enumerate(signal_dates):
    names = top15_raw(sd)
    if not names:
        continue
    sell_d = fwd_n(sd, 20)
    for c in names:
        p_in = qfq_close.get(sd, {}).get(c)
        p_out = qfq_close.get(sell_d, {}).get(c) if sell_d else None
        if p_in and p_out and p_in > 0:
            r = (p_out / p_in - 1) * 100
            records.append({"code": c, "signal_date": sd, "sell_date": sell_d,
                            "year": sd[:4], "r20": round(r, 3)})
    if (k + 1) % 20 == 0:
        print(f"  进度 {k+1}/{len(signal_dates)}")

print("L0a逐笔:", len(records))

def stats(items):
    n = len(items)
    if not n:
        return {"n": 0}
    rs = sorted(x["r20"] for x in items)
    wins = [x for x in items if x["r20"] > 0]
    losses = [x for x in items if x["r20"] < 0]
    aw = sum(x["r20"] for x in wins) / len(wins) if wins else 0
    al = sum(x["r20"] for x in losses) / len(losses) if losses else 0
    return {
        "n": n,
        "win_pct": round(len(wins) / n * 100, 2),
        "pl_ratio": round(aw / abs(al), 3) if al else None,
        "avg_r20": round(sum(rs) / n, 3),
        "med_r20": round(rs[n // 2], 3),
        "p90": round(rs[int(n * 0.9)], 3),
        "p10": round(rs[int(n * 0.1)], 3),
    }

result = {"all": stats(records)}
for y in ["2024", "2025", "2026"]:
    result[y] = stats([r for r in records if r["year"] == y])
result["n_periods"] = len(signal_dates)

json.dump(result, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(records, open("D:/zcodesj/analysis/ladder_L0a_20261019_trades.json", "w", encoding="utf-8"),
          ensure_ascii=False)
md = ["# P1-L0a 信号层理论alpha（raw名单，信号日收盘→20日收盘）", ""]
md.append(f"- 调仓期数 {len(signal_dates)}（序列=L4实际buy_date前一交易日），逐笔 {len(records)}")
md.append("| 段 | n | 胜率% | 盈亏比 | avg_r20 | med | p90 | p10 |")
md.append("|---|---|---|---|---|---|---|---|")
for k in ["all", "2024", "2025", "2026"]:
    s = result[k]
    if s.get("n"):
        md.append(f"| {k} | {s['n']} | {s['win_pct']} | {s['pl_ratio']} | {s['avg_r20']} | {s['med_r20']} | {s['p90']} | {s['p10']} |")
open(OUT_MD, "w", encoding="utf-8").write("\n".join(md))
print("\n".join(md))
