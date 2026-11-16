# -*- coding: utf-8 -*-
"""tulong_math_toolkit.py —— 屠龙共享数学工具箱（跨脚本数学语义唯一 owner）

来源: reports/math_knowledge_full_study_20260920.md（A股数学知识六域学习落地件）
定位: 纯函数库, 零 IO 零全局态; 生产/研究脚本 import 使用, 禁止在其他脚本散落复制公式
      （宪法§5 共享语义单一真源）。所有函数为教科书级标准数学, 报告§一~六有推导与来源。
版本: v1 2026-09-20 首版——Wilson区间/Newey-West/多重检验(E[max]/FWER/Bonferroni/BH)/
      PSR-DSR/GARCH-GJR半衰期/HHI有效持仓/等ρ组合波动与风险贡献/回撤恢复/波动率拖累/
      止损几何期望(Δg)核心。
"""
from __future__ import annotations
import math
from statistics import NormalDist

_ND = NormalDist()
_EULER_GAMMA = 0.5772156649015329  # 欧拉-马歇罗尼常数


# ============ 1. 区间估计 ============

def wilson_ci(k: int, n: int, z: float = 1.959963985) -> tuple[float, float]:
    """胜率 Wilson 置信区间（默认95%）。小样本/极端比例下优于 Wald 正态近似。
    例: wilson_ci(18, 30) ≈ (0.423, 0.754) —— 30笔60%胜率连抛硬币都无法排除。"""
    if n <= 0:
        raise ValueError("n<=0")
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


# ============ 2. HAC 推断（重叠收益/IC 序列必用） ============

def nw_lag_auto(T: int) -> int:
    """Newey-West 自动带宽 m = ⌊4(T/100)^(2/9)⌋（Bartlett 核惯例）。"""
    return max(1, int(4 * (T / 100.0) ** (2.0 / 9.0)))


def newey_west_tstat(x: list[float], lags: int | None = None) -> float:
    """H0: 均值=0 的 Newey-West(Bartlett核) t 统计量。
    重叠持有期收益、自相关 IC 序列的显著性检验必须用本函数, 裸 t 会虚高。"""
    n = len(x)
    if n < 5:
        raise ValueError("样本<5")
    m = min(lags or nw_lag_auto(n), n - 2)
    xb = sum(x) / n
    e = [v - xb for v in x]
    g0 = sum(v * v for v in e) / n
    lrv = g0
    for j in range(1, m + 1):
        gj = sum(e[t] * e[t + j] for t in range(n - j)) / n
        lrv += 2 * (1 - j / (m + 1)) * gj
    if lrv <= 0:
        lrv = g0 if g0 > 0 else 1e-12
    return xb / math.sqrt(lrv / n)


# ============ 3. 多重检验 ============

def fwer(alpha: float, N: int) -> float:
    """族错误率 = 1-(1-alpha)^N; N 个独立检验至少一个假阳性的概率。"""
    return 1.0 - (1.0 - alpha) ** N


def bonferroni_alpha(alpha: float, N: int) -> float:
    """Bonferroni 校正后单检验显著性水平（保守, 控制 FWER）。"""
    return alpha / N


def emax_tstat(N: int) -> float:
    """N 个 iid 标准正态最大值的期望 ≈ b_N + γ/b_N。
    纯噪音下试 N 个信号的最佳者期望 t; N=50 → ≈2.33（t=2.0 低于噪音冠军）。"""
    if N < 2:
        raise ValueError("N<2")
    b = _ND.inv_cdf(1 - 1.0 / N)
    return b + _EULER_GAMMA / b


def bh_reject(pvals: list[float], q: float = 0.05) -> list[int]:
    """BH-FDR 程序: 返回拒绝(=发现)的原始下标列表。适合因子海选场景。"""
    m = len(pvals)
    if m == 0:
        return []
    idx = sorted(range(m), key=lambda i: pvals[i])
    k_max = 0
    for rank, i in enumerate(idx, 1):
        if pvals[i] <= q * rank / m:
            k_max = rank
    return sorted(idx[:k_max])


# ============ 4. PSR / DSR（回测显著性折价, Bailey-López de Prado） ============

def psr(sr: float, n_obs: int, skew: float = 0.0, kurt: float = 3.0,
        sr_star: float = 0.0) -> float:
    """概率夏普: P(真实SR > sr_star), 罚短样本/负偏/肥尾。sr 为每期(非年化)口径。"""
    den = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if den <= 0:
        return float("nan")
    z = (sr - sr_star) * math.sqrt(n_obs - 1) / math.sqrt(den)
    return _ND.cdf(z)


def dsr_threshold(N: int, sr_std: float) -> float:
    """压缩夏普阈值 = 试 N 次能撞出的期望最大 Sharpe。
    SR* = sr_std·[(1-γ)Φ⁻¹(1-1/N) + γΦ⁻¹(1-1/(N·e))]; N=独立尝试数, sr_std=各次试验 SR 的标准差。"""
    if N < 2 or sr_std <= 0:
        raise ValueError("N>=2 且 sr_std>0")
    b1 = _ND.inv_cdf(1 - 1.0 / N)
    b2 = _ND.inv_cdf(1 - 1.0 / (N * math.e))
    return sr_std * ((1 - _EULER_GAMMA) * b1 + _EULER_GAMMA * b2)


def dsr(sr: float, n_obs: int, N: int, sr_std: float,
        skew: float = 0.0, kurt: float = 3.0) -> float:
    """压缩夏普 = PSR(SR*=期望最大SR)。判据: DSR>0.95 才算在 N 次尝试后仍显著。"""
    return psr(sr, n_obs, skew, kurt, dsr_threshold(N, sr_std))


# ============ 5. GARCH 族半衰期 ============

def garch_halflife(persistence: float) -> float:
    """波动率事件衰减半程天数 = ln(0.5)/ln(persistence)。
    GARCH: persistence=α+β; GJR: persistence=α+γ/2+β（I(ε<0)期望=0.5）。
    0.98→34.3日, 0.99→68.7日; ≥1 非平稳(IGARCH)返回 inf。"""
    if persistence <= 0:
        return float("nan")
    if persistence >= 1:
        return float("inf")
    return math.log(0.5) / math.log(persistence)


def gjr_persistence(alpha: float, gamma: float, beta: float) -> float:
    """GJR(1,1,1) 持续性 = α + γ/2 + β。"""
    return alpha + gamma / 2.0 + beta


# ============ 6. 复利数学 ============

def drawdown_recovery(d: float) -> float:
    """回撤 d 后回本所需涨幅 = d/(1-d)。-8%→+8.70%, -20%→+25%, -50%→+100%。"""
    if d >= 1:
        raise ValueError("d>=1 无法回本")
    return d / (1 - d)


def vol_drag(mu_annual: float, sigma_annual: float) -> float:
    """几何收益 ≈ μ - σ²/2（波动率拖累）。+50%/-50% 算术均 0、终值 -25%。"""
    return mu_annual - sigma_annual ** 2 / 2


# ============ 7. 组合结构（等ρ近似） ============

def hhi(weights: list[float]) -> float:
    """Herfindahl 集中度 = Σw²。"""
    return sum(w * w for w in weights)


def effective_positions(weights: list[float]) -> float:
    """有效持仓数 = 1/HHI。名义50只+单票26% → 有效≈12.7只。"""
    h = hhi(weights)
    if h <= 0:
        raise ValueError("HHI<=0")
    return 1.0 / h


def portfolio_vol_equicor(weights: list[float], sigmas: list[float], rho: float) -> float:
    """等相关近似组合σ: σ_p = √[(1-ρ)Σ(w_iσ_i)² + ρ(Σw_iσ_i)²]。"""
    if len(weights) != len(sigmas):
        raise ValueError("长度不一致")
    s = [w * sg for w, sg in zip(weights, sigmas)]
    ssum = sum(s)
    var = (1 - rho) * sum(v * v for v in s) + rho * ssum * ssum
    return math.sqrt(max(var, 0.0))


def equicor_risk_shares(weights: list[float], sigmas: list[float], rho: float) -> list[float]:
    """等相关近似下各持仓的方差贡献份额 RC_i/σ_p²（和=1）。
    RC_i = w_iσ_i·[ρ·Σw_jσ_j + (1-ρ)·w_iσ_i]; 26%权重@ρ=0.3 → ≈35%组合风险。"""
    if len(weights) != len(sigmas):
        raise ValueError("长度不一致")
    s = [w * sg for w, sg in zip(weights, sigmas)]
    ssum = sum(s)
    var = (1 - rho) * sum(v * v for v in s) + rho * ssum * ssum
    if var <= 0:
        raise ValueError("零方差")
    return [si * (rho * ssum + (1 - rho) * si) / var for si in s]


# ============ 8. 止损几何期望（S-5 Δg 核心） ============

def path_log_return(returns: list[float]) -> float:
    """全路径对数收益 g_full = lnΠ(1+r)。"""
    v = 1.0
    for r in returns:
        v *= (1 + r)
    return math.log(v) if v > 0 else float("-inf")


def stop_loss_geometric(returns: list[float], stop: float = 0.92,
                        lows: list[float] | None = None) -> tuple[float, bool]:
    """止损模拟下的路径对数收益。返回 (g, stopped)。
    returns: 逐期简单收益（相对前收盘, 从锚点次日起算）。
    lows: 可选逐期低点简单收益（相对前收盘）。给出时低点优先触发（累计低点≤stop,
          成交价=stop, 止损限价单近似, 磁吸/跳空下滑未计）; 低点未触但收盘≤stop
          → 收盘触发（成交=触发日收盘, 保守口径）。未给出时仅收盘触发（close-through）。
          未触发 → (g_full, False)。"""
    v = 1.0
    for i, r in enumerate(returns):
        if lows is not None:
            if v * (1 + lows[i]) <= stop:
                return math.log(stop), True
        v *= (1 + r)
        if v <= stop:
            return math.log(v), True
    return math.log(v) if v > 0 else float("-inf"), False


def stop_loss_delta_g(returns: list[float], stop: float = 0.92,
                      lows: list[float] | None = None) -> tuple[float, bool]:
    """Δg = g_stop - g_full: 止损对该路径的几何 alpha。>0=止损改善, <0=鞭打成本。
    未触发路径 Δg=0。"""
    g_s, stopped = stop_loss_geometric(returns, stop, lows)
    if not stopped:
        return 0.0, False
    return g_s - path_log_return(returns), True


if __name__ == "__main__":
    print("[tulong_math_toolkit] 纯函数库, 无 main; 请 import 使用或跑 tests/test_tulong_math_toolkit.py")
