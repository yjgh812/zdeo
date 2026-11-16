# -*- coding: utf-8 -*-
"""
因子择时引擎 Phase 2.5
================================
问题: 拥挤度预警触发后怎么办 / IC衰减时该不该切换 —— 当前监控无消费规则。

实证设计（最小可行）:
  轨道: bp+ep(PIT) 与 droe(PIT) 双轨道月度IC序列 2019-2026
  规则A(IC动量): 因子6月滚动IC均值 < 0 → 下月该轨道权重0(切至另一轨道), >0.03恢复
  规则B(IC+拥挤): 规则A + 拥挤度代理(多头组内相关性>90%分位)→强制降权
  基准: 双轨道静态50/50
  组合模拟: 月度Top20等权, next-bar, 单边0.3%

产出: reports/FACTOR_TIMING_20260816.md
"""
from __future__ import annotations

import pickle
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from strategy_test_harness import (load_qfq, load_raw_close, load_st_codes,  # noqa
                                   clean_returns, load_pit_wide, month_ends,
                                   COST_ONE_SIDE)

CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")


def build_factor_ic(start="20190101", end="20260814"):
    """bp+ep与droe的月度IC序列 + 各自Top20月度收益序列。"""
    cache = CACHE / "factor_timing_ic.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    q = load_qfq(start, end, fields=("close",))
    close = q["close"]
    ret, _ = clean_returns(close)
    raw_close = load_raw_close(start, end)
    st = load_st_codes()
    bp = load_pit_wide("bp", start, end)
    ep = load_pit_wide("ep", start, end)
    droe = load_pit_wide("droe", start, end)
    # 月度评估
    cal = ret.index
    me = pd.Series(cal, index=cal).groupby(cal.to_period("M")).last().sort_values()
    me = [d for d in me if d > cal[30]]
    fwd20 = (1 + ret).rolling(20, min_periods=15).apply(
        lambda x: np.prod(x) - 1, raw=True).shift(-20)

    rows = []
    prev_picks = {}
    for i, d in enumerate(me[:-1]):
        next_d = me[i + 1]
        loc0, loc1 = cal.get_loc(d), cal.get_loc(next_d)
        seg = ret.iloc[loc0 + 1:loc1 + 1]
        fwd = fwd20.loc[d] if d in fwd20.index else None
        row = {"date": d}
        for name, fac in (("bpep", None), ("droe", droe)):
            if name == "bpep":
                if bp.empty or ep.empty:
                    continue
                bpv = bp.loc[:d].iloc[-1] if (bp.index <= d).any() else None
                epv = ep.loc[:d].iloc[-1] if (ep.index <= d).any() else None
                if bpv is None or epv is None:
                    continue
                common = bpv.dropna().index.intersection(epv.dropna().index)
                z = bpv.loc[common].rank() + epv.loc[common].rank()
            else:
                if droe.empty or not (droe.index <= d).any():
                    continue
                z = droe.loc[:d].iloc[-1].dropna().rank()
            if fwd is not None and len(z) > 100:
                common = z.index.intersection(fwd.dropna().index)
                row[f"ic_{name}"] = z.loc[common].corr(fwd.loc[common], method="spearman")
            # ≤30元Top20
            if d in raw_close.index:
                px = raw_close.loc[d]
                ok = [c for c in z.index if c in px.index and 0 < px[c] <= 30 and c not in st]
                z = z.loc[ok]
            picks = list(z.sort_values(ascending=False).head(20).index)
            row[f"picks_{name}"] = picks
            if len(picks) >= 8:
                sub = seg[seg.columns.intersection(picks)]
                row[f"ret_{name}"] = sub.mean(axis=1).mean()
                # 换手
                pp = prev_picks.get(name)
                row[f"to_{name}"] = (1 - len(set(pp) & set(picks)) / len(picks)) if pp else 0.0
                prev_picks[name] = picks
        rows.append(row)
        if (i + 1) % 12 == 0:
            print(f"    {i+1}/{len(me)-1}月 ({time.time():.0f})", flush=True)
    df = pd.DataFrame(rows)
    df.to_pickle(cache)
    return df


def simulate(df: pd.DataFrame) -> dict:
    """三策略: 静态50/50 / 规则A(IC动量) / 规则B(IC+拥挤度代理)。"""
    d = df.set_index("date")
    # 拥挤度代理: Top20组内月收益无法算相关 → 用两轨道IC差代理风格分化(简化,如实标注)
    ic_b = d["ic_bpep"]
    ic_d = d["ic_droe"]
    roll6_b = ic_b.rolling(6, min_periods=4).mean().shift(1)   # 上月末可知
    roll6_d = ic_d.rolling(6, min_periods=4).mean().shift(1)

    out = {"static": [], "ruleA": [], "ruleB": []}
    for k in range(len(d)):
        rb = d["ret_bpep"].iloc[k]
        rd = d["ret_droe"].iloc[k]
        if np.isnan(rb) or np.isnan(rd):
            continue
        rb_n = rb - COST_ONE_SIDE * d["to_bpep"].iloc[k]
        rd_n = rd - COST_ONE_SIDE * d["to_droe"].iloc[k]
        out["static"].append(0.5 * rb_n + 0.5 * rd_n)
        # 规则A: 6月IC<0 → 该轨道0权重
        wA_b = 0.0 if (not np.isnan(roll6_b.iloc[k]) and roll6_b.iloc[k] < 0) else 0.5
        wA_d = 0.0 if (not np.isnan(roll6_d.iloc[k]) and roll6_d.iloc[k] < 0) else 0.5
        s = wA_b + wA_d
        if s == 0:
            wA_b = wA_d = 0.5
        else:
            wA_b, wA_d = wA_b / s, wA_d / s
        out["ruleA"].append(wA_b * rb_n + wA_d * rd_n)
        # 规则B: A + 风格分化极端(|IC差|>0.15)时反向倾斜(强者降权)
        icdiff = ic_b.iloc[k - 1] - ic_d.iloc[k - 1] if k > 0 else 0
        if abs(icdiff) > 0.15:
            if icdiff > 0:  # bpep强 → 降权防拥挤
                wB_b, wB_d = max(wA_b - 0.3, 0), min(wA_d + 0.3, 1)
            else:
                wB_b, wB_d = min(wA_b + 0.3, 1), max(wA_d - 0.3, 0)
            ss = wB_b + wB_d or 1
            wB_b, wB_d = wB_b / ss, wB_d / ss
        else:
            wB_b, wB_d = wA_b, wA_d
        out["ruleB"].append(wB_b * rb_n + wB_d * rd_n)

    res = {}
    for name, seq in out.items():
        s = pd.Series(seq).dropna()
        nav = (1 + s).cumprod()
        res[name] = {"cagr": float(nav.iloc[-1] ** (12 / len(s)) - 1),
                     "sharpe": float(s.mean() / (s.std() + 1e-12) * np.sqrt(12)),
                     "mdd": float((nav / nav.cummax() - 1).min()),
                     "n": len(s)}
    return res


def main():
    t0 = time.time()
    print("=== 因子择时引擎 ===")
    df = build_factor_ic()
    print(f"  月度IC样本: {len(df)}月 ({time.strftime('%H:%M:%S')}, {time.time()-t0:.0f}s)")
    res = simulate(df)
    ic_b = df["ic_bpep"].dropna()
    ic_d = df["ic_droe"].dropna()
    # IC衰减段统计
    b_neg6 = (ic_b.rolling(6).mean() < 0).mean()
    d_neg6 = (ic_d.rolling(6).mean() < 0).mean()
    lines = [
        "# 因子择时引擎 — 实证报告",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 轨道: bp+ep(PIT) 与 droe(PIT), 2019-2026月度IC",
        f"- bp+ep IC均值{ic_b.mean():.4f}/ICIR{ic_b.mean()/(ic_b.std()+1e-12):.2f}/负6月段占比{b_neg6:.0%}",
        f"- droe IC均值{ic_d.mean():.4f}/ICIR{ic_d.mean()/(ic_d.std()+1e-12):.2f}/负6月段占比{d_neg6:.0%}",
        "",
        "| 策略 | CAGR | Sharpe | MDD | 月数 |",
        "|------|------|--------|-----|------|",
    ]
    for k, v in res.items():
        lines.append(f"| {k} | {v['cagr']:.2%} | {v['sharpe']:.3f} | {v['mdd']:.2%} | {v['n']} |")
    best = max(res, key=lambda k: res[k]["sharpe"])
    gain = res[best]["sharpe"] - res["static"]["sharpe"]
    lines += ["",
              f"**结论**: 最优={best}, Sharpe相对静态50/50 {gain:+.3f}",
              "",
              "## 落地规则(若择时有效)",
              "- 6月滚动IC<0的轨道 → 下月权重0(实际操作=暂停该轨道新开仓)",
              "- 6月滚动IC>0.03 → 恢复",
              "- |两轨道IC差|>0.15(风格极端分化) → 强者降权30%(拥挤防护,简化代理)",
              "- 已有挂载点: 每月1日09:00因子衰减cron三合一扩展",
              "",
              "## ⚠️ 局限",
              "- 拥挤度用IC差代理(真实换手率比率拥挤度见factor_crowding_monitor)",
              "- 双轨道相关性高时择时空间有限",
              f"- 耗时 {time.time()-t0:.0f}s",
              ""]
    p = REPORT / f"FACTOR_TIMING_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")


if __name__ == "__main__":
    main()
