# -*- coding: utf-8 -*-
"""
p1_risk_exit_audit_20261023.py — P1 风控退出后验质量审计（真实逐笔×qfq后验路径, 只读）
==========================================================================================
对规则腿每一笔风控退出（止损/止盈/熔断），实测退出价之后的真实路径：
  fwd_K = close[sell_date + K交易日] / open[sell_date] − 1   (QFQ空间, 基=退出当日qfq开盘≈成交价)
  fwd>0 = 卖飞(退出后上涨); fwd<0 = 卖对(退出后继续跌)
输出分退出族×分年代的中位/p25/p75/卖飞占比 + M6 2026熔断15笔明细。
边界(如实): 单票后验≠组合反事实(不含资金再利用/再买入/指数环境); 严格"无熔断"结论需重跑仿真(待裁决)。
全部只读，零写入零交易。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
FILES = {"M6_wfa_top20(规则腿)": "trades_M6_wfa_top20.csv",
         "M1_lowconsol_main(规则腿)": "trades_M1_lowconsol_main.csv"}
DB_QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\reports\p1_risk_exit_audit_20261023_result.json"
KS = (5, 10, 20)

# ---- 载入风控退出逐笔 ----
exits = []
for tag, fn in FILES.items():
    df = pd.read_csv(f"{BASE}\\{fn}")
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["fam"] = df["reason"].astype(str).str[:2]
    sub = df[df["fam"].isin(["止损", "止盈", "熔断"]) & ~df["reason"].astype(str).str.contains("ATR缓冲")]
    for _, r in sub.iterrows():
        exits.append({"leg": tag.split("(")[0], "code": r["code"], "name": r["name"],
                      "sell_date": int(r["sell_date"]), "fam": r["fam"],
                      "pnl_pct": r["pnl_pct"] * 100})
ex = pd.DataFrame(exits)
codes = sorted(ex["code"].unique())
print(f"风控退出笔数={len(ex)} (止损={int((ex['fam']=='止损').sum())} 止盈={int((ex['fam']=='止盈').sum())} "
      f"熔断={int((ex['fam']=='熔断').sum())}), 涉及{len(codes)}只")

# ---- 逐码载入 qfq date/open/close 序列 ----
con = sqlite3.connect(f"file:{DB_QFQ}?mode=ro", uri=True)
series = {}
for c in codes:
    rows = con.execute("SELECT date, open, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
                       (c,)).fetchall()
    if rows:
        d = np.array([int(r[0]) for r in rows])  # DB date 为 TEXT, 统一转 int 键
        series[c] = (d, np.array([float(r[1]) for r in rows]), np.array([float(r[2]) for r in rows]))
con.close()

# ---- 逐笔后验路径 ----
recs = []
miss = 0
for _, r in ex.iterrows():
    s = series.get(r["code"])
    if s is None:
        miss += 1
        continue
    d, o, cl = s
    pos = int(np.searchsorted(d, r["sell_date"]))
    if pos >= len(d) or d[pos] != r["sell_date"] or not (o[pos] > 0):
        miss += 1
        continue
    base = float(o[pos])
    row = {"leg": r["leg"], "fam": r["fam"], "yr": r["sell_date"] // 10000,
           "pnl": r["pnl_pct"]}
    for k in KS:
        j = pos + k
        row[f"fwd{k}"] = (float(cl[j]) / base - 1) * 100 if j < len(d) else None
    recs.append(row)
t = pd.DataFrame(recs)
print(f"可配对 {len(t)}/{len(ex)} (缺历史{miss}笔)")


def blk(sub: pd.DataFrame, label: str) -> dict:
    out = {"n": int(len(sub))}
    if sub.empty:
        return out
    for k in KS:
        col = sub[f"fwd{k}"].dropna()
        out[f"fwd{k}_median"] = round(float(col.median()), 2) if len(col) else None
        out[f"fwd{k}_p25"] = round(float(col.quantile(0.25)), 2) if len(col) else None
        out[f"fwd{k}_p75"] = round(float(col.quantile(0.75)), 2) if len(col) else None
        out[f"fwd{k}_fly_gt3"] = round(float((col > 3).mean()), 3) if len(col) else None
        out[f"fwd{k}_safe_lt-3"] = round(float((col < -3).mean()), 3) if len(col) else None
    return out


RESULT = {"generated": datetime.now().isoformat(timespec="seconds"),
          "n_exits": int(len(ex)), "n_paired": int(len(t)), "files": {}}
for leg in sorted(t["leg"].unique()):
    for fam in ("止损", "止盈", "熔断"):
        for period, sel in (("2024-2026", t[(t["leg"] == leg) & (t["fam"] == fam) & (t["yr"] >= 2024)]),
                            ("全期", t[(t["leg"] == leg) & (t["fam"] == fam)])):
            if len(sel) < 5:
                continue
            b = blk(sel, f"{leg}/{fam}/{period}")
            RESULT["files"][f"{leg}|{fam}|{period}"] = b
            fly = b.get("fwd10_fly_gt3")
            safe = b.get("fwd10_safe_lt-3")
            print(f"[{leg}|{fam}|{period}] n={b['n']} "
                  f"fwd5中位={b.get('fwd5_median')}% fwd10中位={b.get('fwd10_median')}% "
                  f"fwd20中位={b.get('fwd20_median')}% | "
                  f"10日后+3%以上(卖飞)={fly} 10日后−3%以下(卖对)={safe}")

# ---- M6 2026 熔断明细 ----
m6cb = t[(t["leg"] == "M6_wfa_top20") & (t["fam"] == "熔断") & (t["yr"] == 2026)]
if not m6cb.empty:
    print("-" * 80)
    print("M6 2026 熔断退出明细 (fwd=退出后路径%):")
    detail = m6cb.assign(fwd10=m6cb["fwd10"].round(2), fwd20=m6cb["fwd20"].round(2),
                         pnl=m6cb["pnl"].round(2))[["yr", "pnl", "fwd5", "fwd10", "fwd20"]]
    print(f"  中位: pnl={detail['pnl'].median()}% fwd5={detail['fwd5'].median()}% "
          f"fwd10={detail['fwd10'].median()}% fwd20={detail['fwd20'].median()}% (n={len(detail)})")
    RESULT["M6_2026_CB"] = {"n": int(len(detail)),
                            "median": {k: round(float(detail[k].median()), 2) for k in ("pnl", "fwd5", "fwd10", "fwd20")}}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(RESULT, f, ensure_ascii=False, indent=2)
print("=" * 80)
print(f"证据已落盘: {OUT}")
