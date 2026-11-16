# -*- coding: utf-8 -*-
"""
illiq20×bp+ep 复测 Phase 3 #13 (原判: 不采纳, 重合0.5%换血非增量)
================================================
复测: 月末双因子组合Top20并联 vs bp+ep单独Top20, 重合度+增量分解。
口径: next-bar·≤30元·0.3%·2019-2026
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from strategy_test_harness import (load_qfq, load_raw_close, load_st_codes,  # noqa
                                   clean_returns, load_pit_wide, month_ends,
                                   COST_ONE_SIDE)

PV_DB = r"D:\zcodesj\factor_pv.db"


def load_illiq() -> pd.DataFrame:
    import sqlite3
    conn = sqlite3.connect(f"file:{PV_DB}?mode=ro", uri=True, timeout=120)
    df = pd.read_sql_query(
        "SELECT date, code, value FROM factor_price_volume WHERE factor='illiq20'",
        conn)
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    return df.pivot(index="date", columns="code", values="value").sort_index()


def month_asof(wide, d):
    if not (wide.index <= d).any():
        return None
    return wide.loc[:d].iloc[-1].dropna()


def main():
    t0 = time.time()
    print("=== illiq20×bp+ep 复测 ===")
    q = load_qfq("20190101", "20260814", fields=("close",))
    ret, _ = clean_returns(q["close"])
    raw_close = load_raw_close("20190101", "20260814")
    st = load_st_codes()
    cal = ret.index
    me = [d for d in month_ends(cal) if d > cal[0]]
    bp = load_pit_wide("bp", "20190101", "20260814")
    ep = load_pit_wide("ep", "20190101", "20260814")
    illiq = load_illiq()
    print(f"  数据就绪 ({time.time()-t0:.0f}s)")

    picks_bpep, picks_combo, overlaps = {}, {}, []
    for d in me[:-1]:
        bpv, epv, iv = month_asof(bp, d), month_asof(ep, d), month_asof(illiq, d)
        if bpv is None or epv is None or iv is None:
            continue
        common = bpv.index.intersection(epv.index)
        z = bpv.loc[common].rank() + epv.loc[common].rank()
        # 30元过滤
        if d in raw_close.index:
            px = raw_close.loc[d]
            z = z[[c for c in z.index if c in px.index and 0 < px[c] <= 30 and c not in st]]
        p1 = set(z.sort_values(ascending=False).head(20).index)
        # combo: bp+ep rank + illiq rank
        ii = iv.reindex(z.index).rank()
        zc = z + ii
        p2 = set(zc.sort_values(ascending=False).head(20).index)
        picks_bpep[d] = list(p1)
        picks_combo[d] = list(p2)
        overlaps.append(len(p1 & p2) / 20)

    def bt(picks):
        months = sorted(picks)
        monthly, prev = [], set()
        for i, d in enumerate(months[:-1]):
            nxt = months[i + 1]
            codes = [c for c in picks[d] if c not in st]
            try:
                loc0, loc1 = cal.get_loc(d), cal.get_loc(nxt)
            except KeyError:
                continue
            seg = ret.iloc[loc0 + 1:loc1 + 1]
            sub = seg[seg.columns.intersection(codes)]
            if sub.empty:
                continue
            mr = (1 + sub.mean(axis=1).fillna(0)).prod() - 1
            to = 1 - len(prev & set(codes)) / max(len(codes), 1)
            mr -= COST_ONE_SIDE * to
            prev = set(codes)
            monthly.append(mr)
        s = pd.Series(monthly)
        nav = (1 + s).cumprod()
        return {"cagr": float(nav.iloc[-1] ** (12 / len(s)) - 1),
                "sharpe": float(s.mean() / (s.std() + 1e-12) * np.sqrt(12)),
                "mdd": float((nav / nav.cummax() - 1).min()), "n": len(s)}

    r1, r2 = bt(picks_bpep), bt(picks_combo)
    ov = float(np.mean(overlaps))
    print(f"  bp+ep:    CAGR={r1['cagr']:.2%} Sharpe={r1['sharpe']:.3f}")
    print(f"  illiq混合: CAGR={r2['cagr']:.2%} Sharpe={r2['sharpe']:.3f}")
    print(f"  平均重合度: {ov:.1%}")
    verdict = "维持不采纳(混合未超越)" if r2["sharpe"] <= r1["sharpe"] else "⚠️翻案信号"
    if ov > 0.3:
        verdict += " [重合度高=同源]"
    lines = [
        "# illiq20×bp+ep 复测",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        "- 口径: 月末Top20并联·next-bar·≤30元·0.3%·2019-2026",
        "",
        "| 组合 | CAGR | Sharpe | MDD | 月数 |",
        "|------|------|--------|-----|------|",
        f"| bp+ep单独 | {r1['cagr']:.2%} | {r1['sharpe']:.3f} | {r1['mdd']:.2%} | {r1['n']} |",
        f"| illiq20混合 | {r2['cagr']:.2%} | {r2['sharpe']:.3f} | {r2['mdd']:.2%} | {r2['n']} |",
        "",
        f"- 平均重合度: {ov:.1%}",
        f"- 判定: **{verdict}**",
        f"- 耗时 {time.time()-t0:.0f}s", "",
    ]
    p = Path(r"D:\zcodesj\reports") / f"RETEST_ILLIQ_BPEP_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")


if __name__ == "__main__":
    main()
