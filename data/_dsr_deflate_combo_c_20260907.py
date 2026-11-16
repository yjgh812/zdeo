# -*- coding: utf-8 -*-
"""DSR 定量通缩：组合 C 显著性的多重试验校正（D1, 2026-09-07）

方法：Bailey & López de Prado (2014) Deflated Sharpe Ratio 的 t 值近似版。
E[max t] ≈ σ_t * ((1-γ)·Z(1-1/N) + γ·Z(1-1/(N·e)))，γ=0.5772（欧拉常数）
判定：t_obs > E[max t] 则认为该结果在 N 次试验的最优选择偏差下仍显著。

口径声明（反臆想）：
- t_obs 取组合 C 档案在案值：v1 基础配方 t=3.78、v2 无前视臂 t=3.75（主口径，前视污染已修）。
- N_narrow=12：组合 C 配方族档案可数的已评估变体（v1基础/扩正池/L2联动2倍权/cap20/纯联动组/
  ind H1/v2无前视/9变体敏感性等；其中 5 个有在案 t 值）。
- N_wide=200：全项目信号试验池保守下界 = falsified_rules 33键 + prereg 19份 + 时序26臂
  + skill sweep 60组 + morph 56特征 = 194，取整 200。
- σ_t 三档：族内 5 个在案 t 值的样本 sd（偏差声明：入档者偏正，sd 偏小=对本判定偏严还是偏松
  在报告中讨论）、保守 1.0（零假设标准档）、悲观 2.0。
- 只读统计，零生产侵入，无任何接线行为。
"""
import json
import math
from statistics import stdev, mean

OUT_JSON = "data/_dsr_deflate_combo_c_20260907.json"

EULER_GAMMA = 0.5772156649
E = math.e


def z(p):
    """标准正态分位数（逆CDF），用 Acklam 近似足够。"""
    # Acklam's rational approximation
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def expected_max_t(n, sigma):
    """试验池 N、t 值标准差 sigma 下的期望最大 |t|（de Prado 式一阶近似）。"""
    if n <= 1:
        return 0.0
    term1 = (1 - EULER_GAMMA) * z(1 - 1.0 / n)
    term2 = EULER_GAMMA * z(1 - 1.0 / (n * E))
    return sigma * (term1 + term2)


def max_trials_survived(t_obs, sigma, n_lo=2, n_hi=1_000_000):
    """固定 t_obs 与 sigma，二分求最大可承受试验数 N 使 E[max t] < t_obs。"""
    if expected_max_t(n_lo, sigma) >= t_obs:
        return 0
    lo, hi = n_lo, n_hi
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if expected_max_t(mid, sigma) < t_obs:
            lo = mid
        else:
            hi = mid
    return lo


def main():
    # 族内档案在案 t 值（报告/登记出处见 DSR 报告）
    family_t = {
        "v1_base": 3.78,            # 组合C v1 基础配方（combo-c-t378-pass-candidate）
        "expand_pos_pool": 1.74,    # 扩正池臂（同上档案）
        "l2_synergy_2x": 3.84,      # 联动2倍权臂（combo-c-ind-synergy 档案）
        "ind_h1_between": 4.11,     # 联动组间差（同上档案）
        "v2_no_lookahead": 3.75,    # v2 无前视臂（combo-c-2016-backtest/影子 v2 档案）
    }
    t_values = list(family_t.values())
    sigma_emp = stdev(t_values)
    sigma_candidates = {
        "family_empirical": sigma_emp,
        "conservative_1.0": 1.0,
        "pessimistic_2.0": 2.0,
    }
    trials = {
        "narrow_family_12": 12,   # 组合C 配方族已评估变体（含无 t 记录的 cap20/纯联动/9敏感性变体）
        "wide_project_200": 200,  # 全项目保守下界（33+19+26+60+56=194）
        "wide_project_500": 500,  # 压力档：含未来滚动试验预算
    }
    t_obs_candidates = {"v2_no_lookahead": 3.75, "v1_base": 3.78}

    table = []
    for n_name, n in trials.items():
        for s_name, s in sigma_candidates.items():
            emt = expected_max_t(n, s)
            for t_name, t_obs in t_obs_candidates.items():
                table.append({
                    "N": n, "N_label": n_name, "sigma": round(s, 4),
                    "sigma_label": s_name, "t_obs": t_obs, "t_obs_label": t_name,
                    "E_max_t": round(emt, 4),
                    "survive": bool(t_obs > emt),
                    "margin": round(t_obs - emt, 4),
                })

    # 通过所需最低 sigma 档下的最大可承受试验数
    survival_capacity = {}
    for s_name, s in sigma_candidates.items():
        for t_name, t_obs in t_obs_candidates.items():
            survival_capacity[f"{t_name}@sigma={s_name}"] = max_trials_survived(t_obs, s)

    result = {
        "method": "Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio, t-value approximation",
        "formula": "E[max t] = sigma * ((1-gamma)*Z(1-1/N) + gamma*Z(1-1/(N*e))), gamma=0.5772",
        "family_t_values_in_archive": family_t,
        "sigma_family_empirical": round(sigma_emp, 4),
        "sigma_family_mean": round(mean(t_values), 4),
        "trials_N_basis": {
            "narrow": "组合C配方族12变体（5个有在案t值：v1 3.78/扩正池1.74/L2 3.84/组间差4.11/v2 3.75；另7个无t记录变体计入N）",
            "wide": "全项目保守下界200（falsified_rules 33 + prereg 19 + 时序26臂 + skill_sweep 60 + morph 56 = 194）",
        },
        "decision_table": table,
        "survival_capacity_max_trials": survival_capacity,
        "generated": "2026-09-07",
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"sigma_family_empirical = {sigma_emp:.4f} (family t: {t_values})")
    print(f"{'N':>5} {'sigma':>8} {'E[max t]':>10} {'t_obs':>7} {'survive':>8} {'margin':>8}")
    for r in table:
        print(f"{r['N']:>5} {r['sigma']:>8.3f} {r['E_max_t']:>10.3f} {r['t_obs']:>7.2f} "
              f"{str(r['survive']):>8} {r['margin']:>8.3f}")
    print("\nsurvival capacity (max trials survived):")
    for k, v in survival_capacity.items():
        print(f"  {k}: N_max = {v}")
    print(f"\nsaved -> {OUT_JSON}")


if __name__ == "__main__":
    main()
