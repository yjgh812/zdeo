# -*- coding: utf-8 -*-
"""右侧启动呈请2: 限定三维组合交叉表 + CSCV PBO (2026-10-11)

呈请来源: rightside_spec_coverage_20261011 §三呈请2 (主人「继续」令按序推进)
边界(预冻结, 禁自由挖掘): 仅三维、各取收官报告§五已单维验证的分带
  pos60介入上限   t1 ∈ {25, 40, 80}   (深位/深位+灰带/中带以下; §五四档口径)
  pre_shrink上限  t2 ∈ {0.5, 0.75, 1.0} (极致缩量/组合域在用/≈无过滤)
  成交额上限(亿)  t3 ∈ {0.5, 1.0, 999} (小票最优层/>1亿温和/无过滤; spec"市值档"实为成交额档, 收官"小票<0.5亿"即此)
  = 3×3×3 = 27配置; 判据 = f60_net(净h60, 收官主口径, n=3,873完整样本)
PBO: CSCV S=16 → C(16,8)=12,870 splits, statlib/pbo_cscv.py 唯一owner, 逐事件行按日期排序
自检: ①复现收官§五pos60四档表(n=79/422/3092/280, +11.72/+7.13/+2.78/-0.59, 胜率57.0/55.2/46.5/42.5)
      ②pre_shrink≤0.5 与 成交额<0.5亿/>5亿 单维行对表(§五 line83-84)
判读锚: PBO≈0.5=掷硬币(纯过拟合); 本地先例=沪深300×790双均线PBO 0.659>0.5否决
输出: stdout + reports/rightside_startup_20261011/grid_pbo_summary.json
性质: 描述性稳健件零交易权, 不新增信号——组合域前向验证已由 PREREG_COMBODOMAIN_SHADOW_20261011(ACTIVE) 承担
"""
import os, sys, json
from itertools import product
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from statlib.pbo_cscv import probability_of_backtest_overfitting

EV = r"D:\zcodesj\reports\rightside_startup_20261011\events_main.csv"
OUT = r"D:\zcodesj\reports\rightside_startup_20261011\grid_pbo_summary.json"

T1 = [25, 40, 80]
T2 = [0.5, 0.75, 1.0]
T3 = [0.5, 1.0, 999]
FEE_NOTE = "净口径f60_net已含0.45%费"


def stat(v):
    v = np.asarray(v, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0:
        return {"n": 0}
    return {"n": int(len(v)), "mean": round(float(v.mean()), 3),
            "win": round(float((v > 0).mean() * 100), 1),
            "plr": (round(float(v[v > 0].mean() / -v[v < 0].mean()), 2) if (v < 0).any() and (v > 0).any() else None)}


def main():
    em = pd.read_csv(EV, dtype={"code": str})
    em = em[em.f60.notna()].copy()          # 净h60完整样本(收官§五同口径)
    em = em.sort_values(["d_date", "code"]).reset_index(drop=True)
    y = em.f60_net.values
    pos, shr, amt = em.pos60.values, em.pre_shrink.values, em.amount_mv.values
    print(f"[load] h60完整 n={len(em)} span={em.d_date.min()}-{em.d_date.max()}")

    # 自检①: 复现收官§五 pos60四档
    bands = [("深位(<=25)", pos <= 25), ("灰带(25-40)", (pos > 25) & (pos <= 40)),
             ("中带(40-80)", (pos > 40) & (pos <= 80)), ("高区(>80)", pos > 80)]
    chk, tgt = [], [("深位(<=25)", 79, 11.72, 57.0), ("灰带(25-40)", 422, 7.13, 55.2),
                    ("中带(40-80)", 3092, 2.78, 46.5), ("高区(>80)", 280, -0.59, 42.5)]
    for (nm, m), (tn, tn_, tm, tw) in zip(bands, tgt):
        s = stat(y[m])
        # 容差说明: 端点归属约定(<vs<=)致±1~3事件差(灰带422逐位命中/缩量与大额行逐位命中=同源确认), 量级不影响判读
        ok = abs(s["n"] - tn_) <= 3 and abs(s["mean"] - tm) <= 0.35 and abs(s["win"] - tw) <= 1.0
        chk.append(ok)
        print(f"[check1] {nm}: n={s['n']}(目标{tn_}) mean={s['mean']}(目标{tm}) win={s['win']}(目标{tw}) {'PASS' if ok else 'FAIL'}")
    # 自检②: 单维行对表(§五 line83-84)
    s_shr = stat(y[shr <= 0.5]); s_a1 = stat(y[amt < 0.5]); s_a2 = stat(y[amt > 5])
    print(f"[check2] 缩量<=0.5: n={s_shr['n']}(目标1449) mean={s_shr['mean']}(目标5.26) win={s_shr['win']}(目标49.7)")
    print(f"[check2] 额<0.5亿: n={s_a1['n']}(目标1779) mean={s_a1['mean']}(目标5.69) win={s_a1['win']}(目标55.0)")
    print(f"[check2] 额>5亿  : n={s_a2['n']} mean={s_a2['mean']}(目标-4.60) win={s_a2['win']}(目标27.1)")

    # 27配置交叉表 + 逐事件矩阵
    cfgs = list(product(T1, T2, T3))
    M = np.full((len(em), len(cfgs)), np.nan)
    rows = []
    for j, (t1, t2, t3) in enumerate(cfgs):
        m = (pos <= t1) & (shr <= t2) & (amt <= t3)
        M[m, j] = y[m]
        s = stat(y[m])
        rows.append({"t1_pos60": t1, "t2_shrink": t2, "t3_amt亿": t3, **s})
    print("\n=== 27配置交叉表(净h60, %) ===")
    print(f"{'pos60<=':>8} {'shrink<=':>8} {'额<=':>6} {'n':>6} {'mean':>7} {'win':>6}")
    for r, (t1, t2, t3) in zip(rows, cfgs):
        print(f"{t1:>8} {t2:>8} {t3:>6} {r['n']:>6} {r['mean']:>7} {r['win']:>6}")

    # CSCV PBO (S=16)
    res = probability_of_backtest_overfitting(M, n_blocks=16)
    pbo = round(res.pbo, 4)
    print(f"\n[PBO] configs=27 blocks=16 splits={res.n_splits} PBO={pbo} median_logit={round(res.summary()['median_logit'],3)}")
    print(f"[判读] PBO≈0.5=掷硬币过拟合; 本地先例: 沪深300×790双均线PBO=0.659(否决)")

    # IS最优配置频次(全splits重算太重, 以全样本Top3代替呈现)
    full = sorted([r for r in rows if r.get("mean") is not None], key=lambda r: -r["mean"])
    top3 = [{k: r[k] for k in ("t1_pos60", "t2_shrink", "t3_amt亿", "n", "mean", "win")} for r in full[:3]]

    out = {"meta": {"date": "2026-10-11", "spec": "rightside_spec_coverage §三呈请2",
                    "n_events": int(len(em)), "fee": FEE_NOTE, "grid": "3x3x3=27(预冻结)",
                    "pbo_tool": "statlib/pbo_cscv.py", "splits": int(res.n_splits)},
           "self_check": {"pos60四档复现": bool(all(chk)),
                          "shrink0.5": s_shr, "amt_lt0.5": s_a1, "amt_gt5": s_a2},
           "pbo": {"pbo": pbo, "median_logit": res.summary()["median_logit"],
                   "anchor_0.5": "纯过拟合掷硬币", "anchor_local": "沪深300×790双均线=0.659否决"},
           "top3_full_sample": top3, "grid": rows}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT}")


if __name__ == "__main__":
    main()
