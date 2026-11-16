# -*- coding: utf-8 -*-
"""tests/test_tulong_math_toolkit.py —— 数学工具箱契约测试（已知值锁定）

验收: python tests/test_tulong_math_toolkit.py  → 全 PASS 退出码0
已知值来源: reports/math_knowledge_full_study_20260920.md 各域计算示范
（Wilson(18,30)/半衰期0.98/回撤恢复/E[max]50/DSR阈值/有效持仓12.7/RC35.3% 等）。
无 IO 无日志写入。
"""
from __future__ import annotations
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tulong_math_toolkit import (  # noqa: E402
    bh_reject, bonferroni_alpha, dsr, dsr_threshold, drawdown_recovery,
    effective_positions, emax_tstat, equicor_risk_shares, fwer,
    garch_halflife, gjr_persistence, hhi, newey_west_tstat, path_log_return,
    portfolio_vol_equicor, psr, stop_loss_delta_g, stop_loss_geometric,
    vol_drag, wilson_ci, nw_lag_auto)

FAILS: list[str] = []
TOTAL = [0]


def chk(name: str, cond: bool, detail: str = "") -> None:
    TOTAL[0] += 1
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" | {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def close(a, b, tol=1e-3):
    return abs(a - b) <= tol


# 1. Wilson 区间（报告域4示范: 30笔60% → [42%,75%]）
lo, hi = wilson_ci(18, 30)
chk("wilson_ci(18,30) 下界≈0.423", close(lo, 0.4232, 0.005), f"{lo:.4f}")
chk("wilson_ci(18,30) 上界≈0.754", close(hi, 0.7541, 0.005), f"{hi:.4f}")
chk("wilson_ci 边界 k=0/n=10 下界=0", wilson_ci(0, 10)[0] == 0.0)

# 2. Newey-West（确定性序列）
sine = [math.sin(2 * math.pi * t / 50.0) for t in range(500)]
t_sine = newey_west_tstat(sine)
chk("NW t: 正弦零均值 |t|<1", abs(t_sine) < 1.0, f"t={t_sine:.3f}")
drift = [0.001 * (1 + 0.1 * math.sin(t)) for t in range(300)]
t_drift = newey_west_tstat(drift)
chk("NW t: 正漂移序列 t>5", t_drift > 5.0, f"t={t_drift:.2f}")
chk("nw_lag_auto(250)=4", nw_lag_auto(250) == 4, f"{nw_lag_auto(250)}")

# 3. 多重检验（报告域4示范: N=50）
chk("E[max t](50)≈2.335", close(emax_tstat(50), 2.3348, 0.01), f"{emax_tstat(50):.4f}")
chk("FWER(0.05,50)≈0.923", close(fwer(0.05, 50), 0.9231, 0.005), f"{fwer(0.05, 50):.4f}")
chk("Bonferroni(0.05,50)=0.001", close(bonferroni_alpha(0.05, 50), 0.001, 1e-9))
chk("BH: [0.01,0.02,0.5]@q=0.05 拒绝前两个", bh_reject([0.01, 0.02, 0.5]) == [0, 1])
chk("BH: 全大 p 无拒绝", bh_reject([0.4, 0.5, 0.6]) == [])

# 4. PSR/DSR（报告域4示范: 阈值0.68 / n=250 t=2.0 → PSR≈0.91）
chk("DSR阈值(50,0.3)≈0.683", close(dsr_threshold(50, 0.3), 0.6832, 0.005), f"{dsr_threshold(50, 0.3):.4f}")
sr_d = 2.0 / math.sqrt(250)  # t=2.0, n=250 日频
p = psr(sr_d, 250, 0.0, 3.0, dsr_threshold(50, 0.3) / math.sqrt(252))
chk("PSR(n=250,t=2,N=50)≈0.91<0.95", close(p, 0.907, 0.02) and p < 0.95, f"{p:.4f}")
chk("PSR 零假设=0.5", close(psr(0.0, 100), 0.5, 1e-9))
d = dsr(sr_d, 250, 50, 0.3 / math.sqrt(252), 0.0, 3.0)
chk("DSR 与手动 PSR 一致", close(d, p, 1e-12))

# 5. GARCH/GJR 半衰期（报告域2: 0.98→34日）
chk("半衰期(0.98)≈34.3", close(garch_halflife(0.98), 34.31, 0.05), f"{garch_halflife(0.98):.2f}")
chk("半衰期(0.99)≈68.97", close(garch_halflife(0.99), 68.967, 0.05), f"{garch_halflife(0.99):.2f}")
chk("半衰期(≥1)=inf", garch_halflife(1.0) == float("inf"))
chk("GJR持续性=α+γ/2+β", close(gjr_persistence(0.06, 0.08, 0.91), 1.01, 1e-12))

# 6. 复利（报告域3: -8%→+8.70%）
chk("回本(-8%)≈8.70%", close(drawdown_recovery(0.08), 0.086956, 1e-4), f"{drawdown_recovery(0.08):.5f}")
chk("回本(-20%)=25%", close(drawdown_recovery(0.20), 0.25, 1e-9))
chk("回本(-50%)=100%", close(drawdown_recovery(0.50), 1.0, 1e-9))
chk("波动率拖累(10%,30%)=5.5%", close(vol_drag(0.10, 0.30), 0.055, 1e-12))

# 7. 组合结构（报告域3计算1/计算3: 等权49+单票26%）
w = [0.26] + [0.0151] * 49
sig = [0.02] * 50
chk("HHI≈0.0788", close(hhi(w), 0.078778, 1e-4), f"{hhi(w):.5f}")
chk("有效持仓≈12.69", close(effective_positions(w), 12.686, 0.02), f"{effective_positions(w):.3f}")
chk("σ_p@ρ=0.3（σ=1）≈0.596", close(portfolio_vol_equicor(w, [1.0] * 50, 0.3), 0.5959, 0.005),
    f"{portfolio_vol_equicor(w, [1.0] * 50, 0.3):.4f}")
rc = equicor_risk_shares(w, [1.0] * 50, 0.3)
chk("RC大头@ρ=0.3≈35.3%", close(rc[0], 0.3529, 0.005), f"{rc[0]:.4f}")
rc7 = equicor_risk_shares(w, [1.0] * 50, 0.7)
chk("RC大头@ρ=0.7≈28.0%", close(rc7[0], 0.2800, 0.005), f"{rc7[0]:.4f}")
chk("RC 份额和=1", close(sum(rc), 1.0, 1e-9))
# 域3计算1: 等权50票 σ=30% ρ=0.3 → 16.8%; ρ=0.7 → 25.2%
we = [1.0 / 50] * 50
chk("等权50@ρ0.3 σ_p≈16.8%", close(portfolio_vol_equicor(we, [0.30] * 50, 0.3) * 100, 16.81, 0.05))
chk("等权50@ρ0.7 σ_p≈25.2%", close(portfolio_vol_equicor(we, [0.30] * 50, 0.7) * 100, 25.19, 0.05))

# 8. 止损几何（S-5 Δg）
r_safe = [0.01, -0.02, 0.015, 0.01, -0.01, 0.02]
g0, st0 = stop_loss_geometric(r_safe)
chk("安全路径不触发", st0 is False and close(g0, path_log_return(r_safe), 1e-12))
dg0, s0 = stop_loss_delta_g(r_safe)
chk("安全路径 Δg=0", s0 is False and dg0 == 0.0)
r_crash = [0.01, 0.01, -0.20, 0.02, 0.03]
g1, st1 = stop_loss_geometric(r_crash)   # 收盘触发: V=1.01*1.01*0.80=0.8161≤0.92
chk("崩盘路径收盘触发", st1 is True and close(g1, math.log(0.81608), 1e-4), f"g={g1:.4f}")
dg1, _ = stop_loss_delta_g(r_crash)
chk("鞭打路径 Δg<0（触发后剩余上涨被放弃）", dg1 < 0, f"Δg={dg1:.4f}")
r_cont = [0.01, 0.01, -0.20, -0.05, -0.05]
dg2, s2 = stop_loss_delta_g(r_cont)
chk("续跌路径 Δg>0（左尾截断收益>鞭打成本）", s2 is True and dg2 > 0, f"Δg={dg2:.4f}")
# 低点触发: 低点击穿但收盘收回 → 成交在 stop 价
g2, st2 = stop_loss_geometric([0.01, -0.05, 0.08], lows=[0.0, -0.15, 0.08])
chk("低点触发成交在stop价", st2 is True and close(g2, math.log(0.92), 1e-9), f"g={g2:.4f}")
# 低点未击穿但收盘触发口径差异
g3, st3 = stop_loss_geometric([0.05, -0.13], lows=[0.0, -0.10])
chk("收盘触发(low≤-8%日不触发, 收盘-13%触发)", st3 is True, f"g={g3:.4f}")

n = len(FAILS)
print(f"\n[test_tulong_math_toolkit] {'ALL PASS' if n == 0 else f'{n} FAIL'} (共 {TOTAL[0]} 项断言)")
sys.exit(0 if n == 0 else 1)
