# -*- coding: utf-8 -*-
"""
P2b: 开盘30分钟VWAP反事实成交（2024-2026，只读）
口径：VWAP30 = 买入日09:35-10:00六根5分钟K Σ(amount)/Σ(vol)（tdx lc5, tdx_reader读取层在役）
对照：实际成交=开盘价；反事实收益 r20_vwap = c20/VWAP30-1 vs 实际 r20_open = c20/open
判读：改善=avg_r20提升且Top5%右尾保留≥95%（协议门槛）
"""
import csv, json, sys
from collections import defaultdict
import sqlite3

sys.path.insert(0, "D:/zcodesj")
from tdx_reader import read_min5

BASE = "D:/zcodesj/reports/backtest_full_20261018_dose_n15"
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
CAL = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
OUT_JSON = "D:/zcodesj/analysis/vwap_counterfactual_20261019_out.json"
OUT_MD = "D:/zcodesj/analysis/vwap_counterfactual_20261019_out.md"

conn_c = sqlite3.connect(f"file:{CAL}?mode=ro", uri=True)
cal = [r[0].replace("-", "") for r in conn_c.execute(
    "SELECT date_str FROM trading_calendar WHERE date_str BETWEEN '2016-01-01' AND '2026-10-16' ORDER BY date_str")]
idx8 = {d: i for i, d in enumerate(cal)}

conn_q = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
trades = list(csv.DictReader(open(f"{BASE}/bp_ep_core_trades_full.csv", encoding="utf-8-sig")))
# 2024-2026段
trades_2426 = [t for t in trades if t["买入日期"][:4] in ("2024", "2025", "2026")]
codes = sorted({t["股票代码"] for t in trades_2426})
print("2024-2026逐笔:", len(trades_2426), "股票:", len(codes))

# qfq 前瞻收盘缓存
need_dates = sorted({t["买入日期"].replace("-", "") for t in trades_2426})
fwd_cache = {}
for c in codes:
    rows = conn_q.execute(
        "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>='20240101'", (c,)).fetchall()
    m = {d: cl for d, cl in rows}
    dates_sorted = sorted(m)
    pos = {d: i for i, d in enumerate(dates_sorted)}
    fwd_cache[c] = (m, dates_sorted, pos)

def fwd20(code, buy_d8):
    m, ds, pos = fwd_cache[code]
    i = pos.get(buy_d8)
    if i is None or i + 20 >= len(ds):
        return None
    return ds[i + 20], m[ds[i + 20]]

# tdx min5 逐股读
vwap_cache = {}
miss = 0
for c in codes:
    market = "sh" if c.startswith("6") else "sz"
    try:
        bars = read_min5(market=market, code=c)
    except Exception:
        bars = []
    m = defaultdict(lambda: [0.0, 0.0])  # date8 -> [sum_amount, sum_vol]
    for b in bars:
        t = int(b["time"])
        if 935 <= t <= 1000:
            d8 = str(b["date"])
            m[d8][0] += float(b["amount"])
            m[d8][1] += float(b["vol"])
    vwap_cache[c] = {d: (a / v if v > 0 else None) for d, (a, v) in m.items()}

# 对照统计
recs = []
for t in trades_2426:
    code = t["股票代码"]
    bd = t["买入日期"].replace("-", "")
    v = vwap_cache.get(code, {}).get(bd)
    f = fwd20(code, bd)
    if v is None or f is None:
        miss += 1
        continue
    c20 = f[1]
    # 实际买价用 qfq 开盘（与 P2 口径一致），从qfq取
    rows = conn_q.execute("SELECT open FROM stock_daily_meta_qfq WHERE code=? AND date=?", (code, bd)).fetchone()
    if not rows:
        miss += 1
        continue
    op = rows[0]
    recs.append({
        "code": code, "buy_date": bd, "year": bd[:4],
        "r20_open": (c20 / op - 1) * 100,
        "r20_vwap": (c20 / v - 1) * 100,
    })
print("有效:", len(recs), "缺VWAP/前瞻:", miss)

def agg(vals):
    vals = sorted(vals)
    n = len(vals)
    return {
        "n": n,
        "avg": round(sum(vals) / n, 3),
        "med": round(vals[n // 2], 3),
        "win_pct": round(sum(1 for v in vals if v > 0) / n * 100, 2),
        "p95_sum": round(sum(v for v in vals if v >= vals[int(n * 0.95)]), 2),
    }

res = {
    "segment": "2024-2026",
    "open": agg([r["r20_open"] for r in recs]),
    "vwap": agg([r["r20_vwap"] for r in recs]),
}
# 右尾保留率: vwap口径Top5%盈利单贡献 / open口径Top5%贡献
def tail5(vals):
    vals = sorted(vals, reverse=True)
    k = max(1, int(len(vals) * 0.05))
    return sum(vals[:k])
res["tail5_open"] = round(tail5([r["r20_open"] for r in recs]), 2)
res["tail5_vwap"] = round(tail5([r["r20_vwap"] for r in recs]), 2)
res["tail5_retention_pct"] = round(res["tail5_vwap"] / res["tail5_open"] * 100, 2) if res["tail5_open"] else None
res["miss"] = miss

json.dump(res, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
md = ["# P2b 开盘30分钟VWAP反事实（2024-2026）", "",
      f"- 有效笔数 {res['open']['n']}（缺VWAP/前瞻 {miss} 笔跳过）",
      f"- 实际开盘成交: avg_r20={res['open']['avg']}% med={res['open']['med']}% 胜率={res['open']['win_pct']}%",
      f"- VWAP30反事实: avg_r20={res['vwap']['avg']}% med={res['vwap']['med']}% 胜率={res['vwap']['win_pct']}%",
      f"- 改善幅度: {round(res['vwap']['avg'] - res['open']['avg'], 3)}pp/笔",
      f"- Top5%右尾贡献: open={res['tail5_open']} vwap={res['tail5_vwap']} 保留率={res['tail5_retention_pct']}%（门槛≥95%）",
      f"- 判读: {'改善且右尾达标' if (res['vwap']['avg'] > res['open']['avg'] and (res['tail5_retention_pct'] or 0) >= 95) else '不满足接线条件'}", ""]
open(OUT_MD, "w", encoding="utf-8").write("\n".join(md))
print("\n".join(md))
