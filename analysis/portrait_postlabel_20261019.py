# -*- coding: utf-8 -*-
"""
P1-建议5: 画像事后分层（协议最后一项，纯只读）
操作：对 L4 生产档 1612 笔实际交易，按买入日现算画像同口径特征（桶边界=entry_portrait.db dim_stats 先验定义，
不新增格子），格内/格外胜率对照，BH FDR q<0.1 校正。
特征：vol_ratio(当日量/前5日均量,<0.8缩量/0.8-1.2平/1.2-2放/≥2爆)、zt_entry(涨幅≥9.85%/19.85%)、
ma_bull(MA5>MA10>MA20)、nh60(收盘创60日新高)、pos60(<40/40-80/≥80)、王者格(涨停×缩量)
判定（协议）：格内 n≥30 且 FDR q<0.1 且分年方向一致 才有资格谈影子；否则归档。
"""
import csv, json, sqlite3, math
from collections import defaultdict
from datetime import datetime

BASE = "D:/zcodesj/reports/backtest_full_20261018_dose_n15"
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
CAL = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
OUT_JSON = "D:/zcodesj/analysis/portrait_postlabel_20261019_out.json"
OUT_MD = "D:/zcodesj/analysis/portrait_postlabel_20261019_out.md"

conn_c = sqlite3.connect(f"file:{CAL}?mode=ro", uri=True)
cal8 = [r[0].replace("-", "") for r in conn_c.execute(
    "SELECT date_str FROM trading_calendar WHERE date_str BETWEEN '2015-01-01' AND '2026-10-16' ORDER BY date_str")]
idx8 = {d: i for i, d in enumerate(cal8)}

trades = list(csv.DictReader(open(f"{BASE}/bp_ep_core_trades_full.csv", encoding="utf-8-sig")))
codes = sorted({t["股票代码"] for t in trades})

conn_q = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
px = defaultdict(list)  # code -> [(date8, open, close, volume)]
for i in range(0, len(codes), 400):
    chunk = codes[i:i+400]
    ph = ",".join("?" * len(chunk))
    for c, d, o, cl, v in conn_q.execute(
            f"SELECT code, date, open, close, volume FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20150101'", chunk):
        px[c].append((d, o, cl, v))
for c in px:
    px[c].sort()
conn_q.close()
pos_in_px = {c: {row[0]: i for i, row in enumerate(rows)} for c, rows in px.items()}

def feat(code, bd):
    rows = px.get(code)
    if not rows:
        return None
    i = pos_in_px[code].get(bd)
    if i is None or i < 60:
        return None
    _, o, c, v = rows[i]
    pc = rows[i-1][2]
    if pc <= 0 or v is None:
        return None
    chg = (c / pc - 1) * 100
    thr = 19.85 if code.startswith("3") else 9.85
    zt = 1 if chg >= thr else 0
    # 量比=当日量/前5日均量
    vols = [rows[j][3] for j in range(i-5, i)]
    vr = v / (sum(vols)/5) if all(x is not None for x in vols) and sum(vols) > 0 else None
    # 均线多头
    closes = [rows[j][2] for j in range(i-19, i+1)]
    ma5 = sum(closes[-5:])/5; ma10 = sum(closes[-10:])/10; ma20 = sum(closes)/20
    bull = 1 if ma5 > ma10 > ma20 else 0
    # 60日新高/位置
    c60 = [rows[j][2] for j in range(i-59, i+1)]
    nh60 = 1 if c >= max(c60) else 0
    lo, hi = min(c60), max(c60)
    pos60 = (c - lo) / (hi - lo) * 100 if hi > lo else 50
    return {"chg": chg, "zt": zt, "vr": vr, "bull": bull, "nh60": nh60, "pos60": pos60}

def vol_band(vr):
    if vr is None: return None
    if vr < 0.8: return "缩量"
    if vr < 1.2: return "平量"
    if vr < 2: return "放量"
    return "爆量"

def pos_band(p):
    if p < 40: return "低区"
    if p < 80: return "中带"
    return "高区"

recs = []
miss = 0
for t in trades:
    code = t["股票代码"]
    bd = t["买入日期"].replace("-", "")
    f = feat(code, bd)
    if f is None:
        miss += 1
        continue
    pnl_pct = float(t["盈亏比例%"])
    recs.append({
        "code": code, "year": bd[:4], "win": 1 if pnl_pct > 0 else 0,
        "pnl": pnl_pct, "volb": vol_band(f["vr"]), "zt": f["zt"],
        "bull": f["bull"], "nh60": f["nh60"], "posb": pos_band(f["pos60"]),
        "king": 1 if (f["zt"] and f["vr"] is not None and f["vr"] < 0.8) else 0,
    })
print(f"有效 {len(recs)} 笔（缺特征跳过 {miss}）")

def stat_of(items):
    n = len(items)
    if not n:
        return {"n": 0}
    wins = [x for x in items if x["win"]]
    losses = [x for x in items if not x["win"]]
    aw = sum(x["pnl"] for x in wins)/len(wins) if wins else 0
    al = sum(x["pnl"] for x in losses)/len(losses) if losses else 0
    return {"n": n, "win": round(len(wins)/n*100, 2),
            "pl": round(aw/abs(al), 3) if al else None,
            "avg": round(sum(x["pnl"] for x in items)/n, 2)}

def z_test_prop(p1, n1, p0, n0):
    if n1 == 0 or n0 == 0:
        return 0.0
    pp = (p1*n1 + p0*n0)/(n1+n0)
    se = math.sqrt(pp*(1-pp)*(1/n1+1/n0))
    if se == 0: return 0.0
    return (p1-p0)/se

# 格子定义（画像先验）
CELLS = {
    "缩量": lambda r: r["volb"] == "缩量",
    "平量": lambda r: r["volb"] == "平量",
    "放量": lambda r: r["volb"] == "放量",
    "爆量": lambda r: r["volb"] == "爆量",
    "买入日涨停": lambda r: r["zt"] == 1,
    "均线多头": lambda r: r["bull"] == 1,
    "创60日新高": lambda r: r["nh60"] == 1,
    "低区<40": lambda r: r["posb"] == "低区",
    "高区≥80": lambda r: r["posb"] == "高区",
    "王者格(涨停×缩量)": lambda r: r["king"] == 1,
}

# 全史统计 + 假设检验
base = stat_of(recs)
tests = []
cell_stats = {}
for name, cond in CELLS.items():
    inside = [r for r in recs if cond(r)]
    outside = [r for r in recs if not cond(r)]
    s_in = stat_of(inside)
    s_out = stat_of(outside)
    z = z_test_prop(s_in.get("win", 0)/100 if s_in.get("n") else 0, s_in.get("n", 0),
                    s_out.get("win", 0)/100 if s_out.get("n") else 0, s_out.get("n", 0))
    pval = math.erfc(abs(z)/math.sqrt(2))
    cell_stats[name] = {"in": s_in, "out": s_out, "z": round(z, 2), "p": round(pval, 4)}
    tests.append((name, pval))

# BH FDR
m = len(tests)
ranked = sorted(tests, key=lambda x: x[1])
fdr = {}
prev_ok = True
for k, (name, p) in enumerate(ranked, 1):
    q = p * m / k
    ok = prev_ok and q < 0.10
    fdr[name] = {"p": p, "q_bh": round(min(q, 1.0), 4), "sig": ok}
    prev_ok = ok

# 分年方向一致性（对通过FDR的格子）
yearly = {}
for y in ["2024", "2025", "2026"]:
    seg = [r for r in recs if r["year"] == y]
    yb = {"base": stat_of(seg)}
    for name, cond in CELLS.items():
        yb[name] = {"in": stat_of([r for r in seg if cond(r)]), "out": stat_of([r for r in seg if not cond(r)])}
    yearly[y] = yb

result = {"base": base, "n_valid": len(recs), "n_miss": miss,
          "cells": cell_stats, "fdr": fdr, "yearly": yearly}
json.dump(result, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

md = ["# 画像事后分层（建议5，L4生产档实际交易，画像先验格子）", "",
      f"- 有效 {len(recs)}/{len(trades)} 笔（缺特征 {miss} 跳过）；基准：胜率{base['win']}% PL{base['pl']} avg{base['avg']}%",
      "- 格子=entry_portrait.db dim_stats 先验桶边界，无新增格子", "",
      "| 格子 | 格内n | 格内胜率% | 格内PL | 格外n | 格外胜率% | z | p | BH-q | 显著 |",
      "|---|---|---|---|---|---|---|---|---|---|"]
for name in CELLS:
    s = cell_stats[name]
    f = fdr[name]
    md.append(f"| {name} | {s['in'].get('n',0)} | {s['in'].get('win','-')} | {s['in'].get('pl','-')} | "
              f"{s['out'].get('n',0)} | {s['out'].get('win','-')} | {s['z']} | {s['p']} | {f['q_bh']} | {'✓' if f['sig'] else '×'} |")
md.append("")
md.append("## 分年（2024-2026 格内 vs 格外 胜率）")
md.append("| 格子 | 2024格内/外 | 2025格内/外 | 2026格内/外 |")
md.append("|---|---|---|---|")
for name in CELLS:
    row = [name]
    for y in ["2024", "2025", "2026"]:
        si = yearly[y][name]["in"]; so = yearly[y][name]["out"]
        row.append(f"{si.get('win','-')}({si.get('n',0)})/{so.get('win','-')}({so.get('n',0)})")
    md.append("| " + " | ".join(row) + " |")
open(OUT_MD, "w", encoding="utf-8").write("\n".join(md))
print("\n".join(md))
