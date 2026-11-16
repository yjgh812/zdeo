# -*- coding: utf-8 -*-
"""
p1_execution_audit_20261023.py — P1 执行层真实数据审计（旗舰+规则腿逐笔档案, 只读）
====================================================================================
验收套件 T1-T6 合成层全过后的 P1 延伸：用已验证的执行机制（口径B=收盘确认触发→次日开盘成交）
对真实逐笔档案做对账与机制量化：

  A. T+1 全量对账（期末强平=估值性收口单列, 不计违例）
  B. 涨停不可买全量对账（buy_px(raw) 连 raw 库前收）
  C. 止损实现分布（规则腿 M1/M6, 核「亏损单P10<−12%」+ 口径B缺口量化）
  D. 年度盈亏结构（旗舰 BP 腿 + M6 规则腿）

列口径事实（旗舰源码）: buy_px=RAW价, sell_px=QFQ×(1−滑点), pnl_pct=QFQ空间含费。
全部只读，零写入零交易。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
FILES = {
    "U100BP_r30a3(旗舰BP腿)": "trades_U100BP_r30a3.csv",
    "M6_wfa_top20(规则腿)": "trades_M6_wfa_top20.csv",
    "M1_lowconsol_main(规则腿)": "trades_M1_lowconsol_main.csv",
}
DB_RAW = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
OUT = r"D:\zcodesj\reports\p1_execution_audit_20261023_result.json"
RESULT = {"generated": datetime.now().isoformat(timespec="seconds"), "files": {}}


def price_limit(code: str) -> float:
    return 0.20 if code.startswith(("300", "301", "688", "689")) else 0.10


con = sqlite3.connect(f"file:{DB_RAW}?mode=ro", uri=True)

for tag, fn in FILES.items():
    path = f"{BASE}\\{fn}"
    df = pd.read_csv(path)
    df["code"] = df["code"].astype(str).str.zfill(6)
    n = len(df)
    print("=" * 78)
    print(f"◆ {tag}: {fn}  总笔数={n}  区间 {int(df['buy_date'].min())}~{int(df['sell_date'].max())}")

    # ---- A. T+1（期末强平=估值收口单列） ----
    same_real = df[(df["sell_date"] <= df["buy_date"]) & (~df["reason"].astype(str).str.contains("期末强平"))]
    same_eop = df[(df["sell_date"] <= df["buy_date"]) & (df["reason"].astype(str).str.contains("期末强平"))]
    a_ok = len(same_real) == 0
    print(f"[A] T+1: 真实同日往返={len(same_real)}  {'✅PASS' if a_ok else '❌FAIL'} | "
          f"期末强平同日收口(估值性, 数据右端点)={len(same_eop)}笔单列")

    # ---- B. 涨停不可买 ----
    viols = near = missing = 0
    seen = {}
    for code, bdate, bpx in zip(df["code"], df["buy_date"], df["buy_px"]):
        key = (code, int(bdate))
        if key not in seen:
            row = con.execute(
                "SELECT close FROM stock_daily_raw WHERE code=? AND date<? "
                "ORDER BY date DESC LIMIT 1", (code, int(bdate))).fetchone()
            seen[key] = float(row[0]) if row and row[0] else None
        prev = seen[key]
        if not prev:
            missing += 1
            continue
        up_lim = round(prev * (1 + price_limit(code)), 2)
        if bpx >= up_lim - 1e-6:
            viols += 1
        elif bpx >= up_lim * 0.97:
            near += 1
    b_ok = viols == 0
    print(f"[B] 涨停不可买: 违例={viols}  {'✅PASS' if b_ok else '❌FAIL'} "
          f"(贴近涨停97%线={near}笔紧但合法, 前收缺失={missing}未判)")

    # ---- C. 止损实现分布（仅规则腿有止损） ----
    stop = df[df["reason"].astype(str).str.startswith("止损")]
    c_block = "无止损路径(BP月度轮换腿)"
    if len(stop):
        p = stop["pnl_pct"] * 100
        gap = (-p[p <= -8]) - 8.0
        loss_all = df.loc[df["pnl_pct"] < 0, "pnl_pct"] * 100
        c_block = (f"止损{len(stop)}笔({len(stop)/n*100:.0f}%): min={p.min():.1f}% P10={p.quantile(.1):.2f}% "
                   f"中位={p.median():.2f}% | ≤−12%占比={(p<=-12).mean()*100:.1f}% | "
                   f"口径B缺口(超−8%线): 中位={gap.median():.2f}pp P90={gap.quantile(.9):.2f}pp max={gap.max():.2f}pp | "
                   f"全体亏损单P10={loss_all.quantile(.1):.2f}%")
        s26 = stop[stop["buy_date"] // 10000 == 2026]
        if len(s26):
            p26 = s26["pnl_pct"] * 100
            c_block += f" || 2026止损{len(s26)}笔: 中位={p26.median():.2f}% P10={p26.quantile(.1):.2f}% ≤−12%占比={(p26<=-12).mean()*100:.0f}%"
    print(f"[C] {c_block}")

    # ---- D. 年度盈亏结构 ----
    d_lines = []
    for yr in (2024, 2025, 2026):
        sub = df[df["buy_date"] // 10000 == yr]
        if sub.empty:
            continue
        pp = sub["pnl_pct"] * 100
        win, lose = pp[pp > 0], pp[pp <= 0]
        plr = win.mean() / abs(lose.mean()) if len(win) and len(lose) else float("nan")
        d_lines.append(f"{yr}: {len(sub)}笔 胜率={len(win)/len(sub)*100:.1f}% 均盈={win.mean() if len(win) else 0:+.2f}% "
                       f"均亏={lose.mean() if len(lose) else 0:+.2f}% 盈亏比={plr:.2f} "
                       f"亏损单P10={lose.quantile(.1) if len(lose) else float('nan'):.2f}%")
    print("[D] " + " || ".join(d_lines))

    RESULT["files"][tag] = {
        "file": fn, "n_trades": int(n),
        "A_t1_real_violations": int(len(same_real)), "A_eop_same_day": int(len(same_eop)),
        "B_limit_up_buy_violations": int(viols), "B_near_limit": int(near),
        "C_stop": c_block,
        "D_years": d_lines,
        "verdict": {"A": "PASS" if a_ok else "FAIL", "B": "PASS" if b_ok else "FAIL"},
    }

con.close()
all_ok = all(v["verdict"]["A"] == "PASS" and v["verdict"]["B"] == "PASS"
             for v in RESULT["files"].values())
RESULT["overall"] = "ALL_PASS" if all_ok else "HAS_FAIL"
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(RESULT, f, ensure_ascii=False, indent=2)
print("=" * 78)
print(f"总体: {RESULT['overall']} | 证据已落盘: {OUT}")
