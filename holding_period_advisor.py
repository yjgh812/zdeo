# -*- coding: utf-8 -*-
"""
holding_period_advisor.py — 屠龙系统·R5 持仓周期分层顾问（2026-08-11 知微落地）

【证据基础 — W14 + W15 双重铁证】
  数据源：dragon_quant.db.stock_daily，2021-12 ~ 2026-07 全量横盘突破信号
  策略口径：F_AMP40 ∈ [0.15, 0.80] 横盘过滤 + 突破日买入 + T+N 卖出

  W14 持仓周期验证（同一信号集，按持仓天数分组）：
    - T+1 名义均值 +0.968%（最优持仓周期）
    - T+3 +0.531%, T+5 +0.472%, T+10 +0.356%（单调递减）
    - 风险回报比（均值/绝对值标准差）：T+1=0.51, T+3=0.39, T+5=0.36
    → T+1 为数学最优解，每多持一天都在消耗期望。

  W15 滑点敏感度（T+1 口径，6 档成本情景）：
    - 零成本：         +1.364%, 45.5% 胜率, 5/6 年正
    - 固定成本 0.10%：  +1.263%, 44.8%, 5/6
    - 低滑点 0.1%单边： +1.063%, 42.1%, 4/6
    - 中滑点 0.3%单边： +0.663%, 37.3%, 4/6  ← 可行临界
    - 高滑点 0.5%单边： +0.263%, 33.4%, 3/6  ← 不可行
    - 极端 1.0%单边：  -0.737%, 25.8%, 2/6
    - 盈亏平衡滑点：    0.63% 单边（数学红线）

  振幅分层（W15 决定性发现）：
    - 甜区(15-25%)：毛收益 +2.124%，临界滑点 1.01%（极宽容，市价单安全）
    - 中波(25-50%)：临界滑点 0.46 ~ 0.83%（需限价单控制滑点）
    - 高波(50-80%)：毛收益 -0.016%（已为负）→ 非滑点问题，策略级失败

【R5 分层规则】
  R5a 甜区(15-25%)：T+1 到期退出已停用（主人 2026-09-07 裁决"持仓无持有
                    时间限制"，advisor 单点生效；持仓天数降为观察）
  R5b 中波(25-50%)：破 5 日线强制退出已停用（主人 2026-09-03 裁决"破5日线
                    不卖出"）；T+3 超持退出已停用（同 9/7 裁决）
  R5c 高波(>50%)：  入场禁令（毛收益 -0.016%，不是持仓优化能救的）
  → 卖出侧现存触发器仅 R2 止损(-8%) 与主人裁量

【模块边界】
  - R5c 属于"入场禁令"：由 check_entry_amplitude() 输出，应集成进
    execution_guard.ExecutionGuard.check_buy() 的短路链
  - R5a/R5b 属于"持仓管理"：由 advise_exit() 输出建议，由每日环路 /
    定时任务调用，不直接下单（与 enforce_stop_loss.py 同层级）
  - 本模块不读数据库、不下单，只做规则裁决（纯函数式，可单测）

使用方式：
  from holding_period_advisor import HoldingPeriodAdvisor
  advisor = HoldingPeriodAdvisor()

  # 入场阶段（R5c 入场禁令）
  ok, msg, band = advisor.check_entry_amplitude(amplitude_40d)
  if not ok:
      # 拒单

  # 持仓阶段（R5a/R5b 持仓建议）
  advice = advisor.advise_exit(band, holding_days, current_price, ma5)
  if advice.should_exit:
      # 执行退出

  # 命令行自测
  python D:\\zcodesj\\holding_period_advisor.py --selftest
"""
import logging
from dataclasses import dataclass, field
from typing import Tuple, Optional

logger = logging.getLogger('holding_period_advisor')

# ===== 振幅分档阈值（来自 W15 实证，禁随意修改）=====
SWEET_LOW = 0.15         # 横盘下限（F_AMP40 标准）
SWEET_HIGH = 0.25        # 甜区上限（W15 最优子集）
MID_HIGH = 0.50          # 中波上限；>0.50 即高波禁令
AMP_HARD_UPPER = 0.80    # 策略口径上限（超出视为异常数据）

# ===== 滑点红线（来自 W15 数学推导）=====
BREAKEVEN_SLIPPAGE_SINGLE = 0.0063   # 盈亏平衡滑点 0.63% 单边
SWEET_CRITICAL_SLIPPAGE = 0.0101     # 甜区临界滑点 1.01%（市价单安全阈值）
MID_CRITICAL_SLIPPAGE_LOW = 0.0046   # 中波下沿临界滑点
MID_CRITICAL_SLIPPAGE_HIGH = 0.0083  # 中波上沿临界滑点

# ===== 持仓周期阈值（来自 W14 实证）=====
T1_OPTIMAL = 1            # T+1 为数学最优持仓周期
T3_TOLERABLE = 3          # 中波允许延长的上限（W14: T+3 仍正期望）


@dataclass
class AmplitudeBand:
    """振幅分档结果。

    band 取值：
      'sweet'  甜区 15-25%（最优子集，毛收益 +2.124%）
      'mid'    中波 25-50%（可交易，需限价单）
      'high'   高波 >50%（毛收益 -0.016%，入场禁令）
      'low'    低于 15%（不满足横盘过滤，不属本策略）
      'invalid' 数据异常
    """
    band: str
    low: float
    high: float
    label: str
    gross_return_pct: float      # 该档历史毛收益均值（%）
    critical_slippage_pct: float # 该档临界滑点（%，单边）


@dataclass
class ExitAdvice:
    """持仓退出建议（R5a/R5b 输出）。"""
    should_exit: bool
    rule: str           # R5a_t1 / R5b_ma5 / R5b_overhold / hold
    reason: str
    urgency: str        # immediate / eod / monitor
    detail: dict = field(default_factory=dict)


class HoldingPeriodAdvisor:
    """R5 持仓周期分层顾问。

    与 ExecutionGuard 解耦：
      - check_entry_amplitude() 供 ExecutionGuard.check_buy() 调用（R5c）
      - advise_exit()            供每日环路 / 定时任务调用（R5a/R5b）
    """

    def __init__(self,
                 sweet_low: float = SWEET_LOW,
                 sweet_high: float = SWEET_HIGH,
                 mid_high: float = MID_HIGH,
                 amp_hard_upper: float = AMP_HARD_UPPER):
        self.sweet_low = sweet_low
        self.sweet_high = sweet_high
        self.mid_high = mid_high
        self.amp_hard_upper = amp_hard_upper

    # ===== 振幅分档 =====
    def classify(self, amplitude: float) -> AmplitudeBand:
        """根据 F_AMP40 振幅值分档。

        Args:
            amplitude: F_AMP40 振幅值（小数，如 0.21 表示 21%）

        Returns:
            AmplitudeBand 对象
        """
        if amplitude is None or amplitude != amplitude:  # NaN 检查
            return AmplitudeBand('invalid', 0, 0, '数据异常(NaN)',
                                 gross_return_pct=0.0,
                                 critical_slippage_pct=0.0)

        if amplitude < 0 or amplitude > 1.5:
            return AmplitudeBand('invalid', 0, 0, f'数据异常(值={amplitude:.3f})',
                                 gross_return_pct=0.0,
                                 critical_slippage_pct=0.0)

        if amplitude < self.sweet_low:
            # 低于横盘下限，不是本策略的票（execution_guard 不拦，由选股层处理）
            return AmplitudeBand('low', 0, self.sweet_low,
                                 f'未达横盘下限(<{self.sweet_low*100:.0f}%)',
                                 gross_return_pct=0.0,
                                 critical_slippage_pct=0.0)

        if amplitude <= self.sweet_high:
            # 甜区 15-25%
            return AmplitudeBand(
                'sweet', self.sweet_low, self.sweet_high,
                f'甜区({self.sweet_low*100:.0f}-{self.sweet_high*100:.0f}%)',
                gross_return_pct=2.124,
                critical_slippage_pct=SWEET_CRITICAL_SLIPPAGE * 100)

        if amplitude <= self.mid_high:
            # 中波 25-50%
            return AmplitudeBand(
                'mid', self.sweet_high, self.mid_high,
                f'中波({self.sweet_high*100:.0f}-{self.mid_high*100:.0f}%)',
                # 中波整体毛收益均值（W15：T+1 中滑点情景下仍 +0.663% 净期望，
                # 倒推毛收益约 +1.2%，这里用保守下限）
                gross_return_pct=1.20,
                critical_slippage_pct=MID_CRITICAL_SLIPPAGE_LOW * 100)

        # 高波 >50%（含 50%-80% 和 >80% 异常）
        label = (f'高波(>{self.mid_high*100:.0f}%)'
                 if amplitude <= self.amp_hard_upper
                 else f'超高波(>{self.amp_hard_upper*100:.0f}%, 数据存疑)')
        return AmplitudeBand(
            'high', self.mid_high, 1.5, label,
            gross_return_pct=-0.016,  # W15 实证毛收益已为负
            critical_slippage_pct=-0.06)  # 负值表示毛收益已亏，无临界可言

    # ===== R5c: 入场振幅检查（入场禁令）=====
    def check_entry_amplitude(self,
                              amplitude: float,
                              code: str = "",
                              name: str = "") -> Tuple[bool, str, AmplitudeBand]:
        """R5c 入场振幅检查。高波(>50%)直接拒单。

        Args:
            amplitude: F_AMP40 振幅值
            code/name: 用于日志（代码必带名称铁律）

        Returns:
            (通过, 原因, 分档对象)
            通过=True 可下单（band ∈ {sweet, mid, low}）
            通过=False 拒单（band = high 或 invalid）
        """
        band = self.classify(amplitude)
        tag = f"{code} {name}" if code or name else "(未提供代码)"

        if band.band == 'invalid':
            # fail-closed: 数据异常，拒单
            return (False,
                    f"R5c 入场禁令: {tag} 振幅数据异常 ({band.label})，"
                    f"fail-closed 拒单",
                    band)

        if band.band == 'high':
            return (False,
                    f"R5c 入场禁令: {tag} 振幅 {amplitude*100:.1f}% 落入 {band.label}，"
                    f"历史毛收益 {band.gross_return_pct:+.3f}%（已为负），"
                    f"非滑点问题而是策略级失败，禁止入场",
                    band)

        if band.band == 'low':
            # 未达横盘下限，不是本策略该选的票；但严格说属于选股层而非执行层
            # 这里返回 True 但打告警，让调用方决定是否拦截
            logger.info(
                f"R5c 告警: {tag} 振幅 {amplitude*100:.1f}% < 横盘下限 "
                f"{self.sweet_low*100:.0f}%，建议选股层过滤（执行层放行）")
            return (True,
                    f"R5c 通过(告警): {tag} 振幅低于横盘下限，"
                    f"应由选股层处理（执行层不拦）",
                    band)

        # sweet / mid：放行
        slip_note = (f"临界滑点 {band.critical_slippage_pct:.2f}%"
                     if band.critical_slippage_pct > 0
                     else "临界滑点 N/A")
        return (True,
                f"R5c 通过: {tag} 振幅 {amplitude*100:.1f}% ∈ {band.label}，"
                f"毛收益 {band.gross_return_pct:+.3f}%，{slip_note}",
                band)

    # ===== R5a/R5b: 持仓退出建议 =====
    def advise_exit(self,
                    band: AmplitudeBand,
                    holding_days: int,
                    current_price: float,
                    ma5: Optional[float] = None,
                    entry_price: Optional[float] = None,
                    code: str = "",
                    name: str = "") -> ExitAdvice:
        """根据振幅档位和持仓状态给出退出建议。

        Args:
            band: classify() 或 check_entry_amplitude() 返回的分档对象
            holding_days: 已持仓天数（T+1 = 持仓 1 天）
            current_price: 当前价
            ma5: 5 日均线（中波 R5b 用到，甜区可None）
            entry_price: 入场价（用于上下文记录）
            code/name: 日志标签

        Returns:
            ExitAdvice：should_exit=True 时应执行退出
        """
        tag = f"{code} {name}" if code or name else "(未提供代码)"
        detail = {
            'band': band.band,
            'holding_days': holding_days,
            'current_price': current_price,
            'ma5': ma5,
            'entry_price': entry_price,
        }

        # 异常档位：交给上层止损规则处理（R5 不越权）
        if band.band in ('invalid', 'low', 'high'):
            return ExitAdvice(
                should_exit=False,
                rule='hold',
                reason=f"R5 不适用 {tag}（档位={band.band}），交 R2 止损规则管理",
                urgency='monitor',
                detail=detail)

        # ===== R5a 甜区：T+1 默认退出 =====
        # 2026-09-07 主人裁决"对持有个股没有持有时间上的限制"：R5a_t1 持有到期退出
        # 分支停用（与 2026-09-03 停用 R5b_ma5 同模式，advisor 单点生效，消费方
        # holding_period_check/pre_sell 自动生效）。持有时长不构成卖出理由，
        # 持仓天数保留在 reason 供人工观察，退出交 R2 止损(-8%)与主人裁量。
        if band.band == 'sweet':
            if holding_days >= T1_OPTIMAL:
                return ExitAdvice(
                    should_exit=False,
                    rule='hold',
                    reason=(f"R5 持有: {tag} 甜区持仓 {holding_days} 天"
                            f"（已达 T+1 参考值，W14 实证名义 +0.968%）。"
                            f"持有到期不构成卖出理由"
                            f"（主人 2026-09-07 裁决：持仓无持有时间限制），"
                            f"交 R2 止损规则管理"),
                    urgency='monitor',
                    detail=detail)
            # 持仓不足 T+1，继续持有
            return ExitAdvice(
                should_exit=False,
                rule='hold',
                reason=(f"R5a 持有: {tag} 甜区持仓 {holding_days} 天 < T+1，"
                        f"未达最优退出点，继续持有"),
                urgency='monitor',
                detail=detail)

        # ===== R5b 中波：T+3 上限（W14: T+3 仍正期望但开始递减） =====
        # 2026-09-03 主人裁决"破5日线不卖出"：R5b_ma5 强制退出分支停用，破5日线降为
        # 持有观察（MA5 含入场前价格，对短龄仓位偏离常为噪声；8/28 卖出端回放亦实证
        # 趋势型早卖踏空）。MA5 状态保留在 reason 供人工观察，不构成卖出触发。
        if band.band == 'mid':
            if ma5 is not None and current_price < ma5:
                deviation = (current_price / ma5 - 1.0) * 100
                return ExitAdvice(
                    should_exit=False,
                    rule='hold',
                    reason=(f"R5 持有: {tag} 当前 {current_price:.2f} < "
                            f"MA5 {ma5:.2f}（偏离 {deviation:.2f}%），"
                            f"破5日线不再触发卖出（主人 2026-09-03 裁决），"
                            f"交 R2 止损规则管理"),
                    urgency='monitor',
                    detail={**detail, 'ma5_deviation_pct': deviation})

            # 优先级 2：超过 T+3 上限 —— 2026-09-07 主人裁决"持仓无持有时间限制"：
            # R5b_overhold 超持退出分支停用（advisor 单点生效），超持天数降为持有观察
            if holding_days >= T3_TOLERABLE:
                return ExitAdvice(
                    should_exit=False,
                    rule='hold',
                    reason=(f"R5 持有: {tag} 中波持仓 {holding_days} 天"
                            f"（超 T+3 参考值，W14: T+3 均值 +0.531%）。"
                            f"超持不构成卖出理由"
                            f"（主人 2026-09-07 裁决：持仓无持有时间限制），"
                            f"交 R2 止损规则管理"),
                    urgency='monitor',
                    detail=detail)

            # 未破5日线且未超 T+3，继续持有
            ma5_note = (f"MA5 {ma5:.2f}（当前 {current_price:.2f} 在上）"
                        if ma5 else "MA5 数据缺失"
                        )
            return ExitAdvice(
                should_exit=False,
                rule='hold',
                reason=(f"R5b 持有: {tag} 中波持仓 {holding_days} 天 < T+3，"
                        f"{ma5_note}，未触发退出条件"),
                urgency='monitor',
                detail=detail)

        # 兜底（理论上不会到这里）
        return ExitAdvice(
            should_exit=False,
            rule='hold',
            reason=f"R5 兜底: {tag} 未匹配任何规则",
            urgency='monitor',
            detail=detail)

    # ===== 辅助：从 K 线序列计算 F_AMP40 =====
    @staticmethod
    def calc_amp40(highs, lows, pre_close_40d_ago: float) -> Optional[float]:
        """计算 F_AMP40 振幅因子。

        F_AMP40 = (max(high_40) - min(low_40)) / close[-41]
            即过去 40 日最高价与最低价之差，除以 41 个交易日前的收盘价。

        Args:
            highs: 最近 40 日的最高价序列（长度应 = 40）
            lows:  最近 40 日的最低价序列（长度应 = 40）
            pre_close_40d_ago: 40 日窗口起始前一日的收盘价
                （即 close[-41] 或等价的"窗口前收盘"）

        Returns:
            振幅值（小数），或 None（数据不足/异常）
        """
        if (not highs or not lows
                or len(highs) < 40 or len(lows) < 40
                or not pre_close_40d_ago or pre_close_40d_ago <= 0):
            return None

        hh = max(highs[-40:])
        ll = min(lows[-40:])
        amp = (hh - ll) / pre_close_40d_ago
        if amp < 0 or amp > 1.5:
            logger.warning(f"F_AMP40 异常值 {amp:.4f}（hh={hh}, ll={ll}, "
                           f"pre_close={pre_close_40d_ago}），返回 None")
            return None
        return amp


# ===== 自测 =====
def _selftest():
    """R5 顾问自测。验证三档分层规则正确性。"""
    print("=" * 68)
    print(" holding_period_advisor 自测（R5 持仓周期分层顾问）")
    print(" 证据：W14 持仓周期 + W15 滑点敏感度")
    print("=" * 68)

    advisor = HoldingPeriodAdvisor()

    # ===== 振幅分档 classify() =====
    print("\n--- 振幅分档 classify() ---")

    # 甜区
    band = advisor.classify(0.20)
    assert band.band == 'sweet', f"0.20 应为甜区: {band}"
    print(f"  ✅ 振幅 20% → {band.band} {band.label}，"
          f"毛收益 {band.gross_return_pct:+.3f}%，"
          f"临界滑点 {band.critical_slippage_pct:.2f}%")

    # 甜区边界 15%
    band = advisor.classify(0.15)
    assert band.band == 'sweet', f"0.15 应为甜区: {band}"
    print(f"  ✅ 振幅 15%（甜区下界）→ {band.band}")

    # 甜区边界 25%
    band = advisor.classify(0.25)
    assert band.band == 'sweet', f"0.25 应为甜区: {band}"
    print(f"  ✅ 振幅 25%（甜区上界）→ {band.band}")

    # 中波
    band = advisor.classify(0.35)
    assert band.band == 'mid', f"0.35 应为中波: {band}"
    print(f"  ✅ 振幅 35% → {band.band} {band.label}，"
          f"毛收益 {band.gross_return_pct:+.3f}%")

    # 中波边界 50%
    band = advisor.classify(0.50)
    assert band.band == 'mid', f"0.50 应为中波: {band}"
    print(f"  ✅ 振幅 50%（中波上界）→ {band.band}")

    # 高波（入场禁令）
    band = advisor.classify(0.65)
    assert band.band == 'high', f"0.65 应为高波: {band}"
    assert band.gross_return_pct < 0, "高波毛收益应为负"
    print(f"  ✅ 振幅 65% → {band.band} {band.label}，"
          f"毛收益 {band.gross_return_pct:+.3f}%（已为负 → 入场禁令）")

    # 超高波（数据存疑）
    band = advisor.classify(1.20)
    assert band.band == 'high', f"1.20 应为高波: {band}"
    print(f"  ✅ 振幅 120%（超高波）→ {band.band} {band.label}")

    # 低档（未达横盘下限）
    band = advisor.classify(0.08)
    assert band.band == 'low', f"0.08 应为 low: {band}"
    print(f"  ✅ 振幅 8%（未达下限）→ {band.band} {band.label}")

    # NaN
    band = advisor.classify(float('nan'))
    assert band.band == 'invalid', "NaN 应为 invalid"
    print(f"  ✅ NaN → {band.band} {band.label}")

    # ===== R5c 入场禁令 check_entry_amplitude() =====
    print("\n--- R5c 入场禁令 check_entry_amplitude() ---")

    # 高波 → 拒单
    ok, msg, band = advisor.check_entry_amplitude(0.65, 'sz300534', '陇神戎发')
    assert not ok, "高波应拒单"
    assert 'R5c' in msg and '入场禁令' in msg
    print(f"  ✅ 陇神戎发(sz300534) 振幅 65% 拒单: {msg[:70]}...")

    # 甜区 → 放行
    ok, msg, band = advisor.check_entry_amplitude(0.20, 'sz002537', '海联金汇')
    assert ok, "甜区应放行"
    assert band.band == 'sweet'
    print(f"  ✅ 海联金汇(sz002537) 振幅 20% 放行: {msg[:70]}...")

    # 中波 → 放行
    ok, msg, band = advisor.check_entry_amplitude(0.35, 'sh600519', '贵州茅台')
    assert ok, "中波应放行"
    assert band.band == 'mid'
    print(f"  ✅ 贵州茅台(sh600519) 振幅 35% 放行: {msg[:70]}...")

    # 数据异常 → fail-closed 拒单
    ok, msg, band = advisor.check_entry_amplitude(float('nan'), 'sz000001', '平安银行')
    assert not ok, "NaN 应 fail-closed 拒单"
    print(f"  ✅ 平安银行(sz000001) NaN fail-closed 拒单: {msg[:60]}...")

    # 未达横盘下限 → 放行+告警
    ok, msg, band = advisor.check_entry_amplitude(0.10, 'sh601288', '农业银行')
    assert ok, "低于横盘下限应放行（交选股层）"
    print(f"  ✅ 农业银行(sh601288) 振幅 10% 放行+告警: {msg[:60]}...")

    # ===== R5a 甜区 T+1 退出 advise_exit() =====
    print("\n--- R5a 甜区 T+1 退出 advise_exit() ---")

    # 甜区 T+1 → 持有（2026-09-07 主人裁决：持仓无持有时间限制，到期不卖出）
    sweet_band = advisor.classify(0.20)
    advice = advisor.advise_exit(sweet_band, holding_days=1,
                                  current_price=10.50, ma5=10.30,
                                  entry_price=10.00,
                                  code='sz002537', name='海联金汇')
    assert not advice.should_exit, "甜区 T+1 按主人裁决不再卖出"
    assert advice.rule == 'hold'
    assert advice.urgency == 'monitor'
    print(f"  ✅ 海联金汇(sz002537) 甜区 T+1 持有（主人9/7裁决）: {advice.reason[:70]}...")

    # 甜区 T+0（当日）→ 继续持有
    advice = advisor.advise_exit(sweet_band, holding_days=0,
                                  current_price=10.50, ma5=10.30,
                                  code='sz002537', name='海联金汇')
    assert not advice.should_exit, "甜区 T+0 应持有"
    assert advice.rule == 'hold'
    print(f"  ✅ 海联金汇(sz002537) 甜区 T+0 持有: {advice.reason[:60]}...")

    # 甜区 T+3（超持）→ 持有（主人 9/7 裁决：超持不构成卖出理由）
    advice = advisor.advise_exit(sweet_band, holding_days=3,
                                  current_price=10.50, ma5=10.30,
                                  code='sz002537', name='海联金汇')
    assert not advice.should_exit, "甜区 T+3 按主人裁决不再卖出"
    assert advice.rule == 'hold'
    print(f"  ✅ 海联金汇(sz002537) 甜区 T+3 持有（超持观察，主人9/7裁决）")

    # ===== R5b 中波破5日线/超持 =====
    print("\n--- R5b 中波破5日线/超持 advise_exit() ---")

    mid_band = advisor.classify(0.35)

    # 中波 T+1，未破5日线 → 持有
    advice = advisor.advise_exit(mid_band, holding_days=1,
                                  current_price=10.50, ma5=10.30,
                                  code='sh600519', name='贵州茅台')
    assert not advice.should_exit, "中波 T+1 未破5日线应持有"
    assert advice.rule == 'hold'
    print(f"  ✅ 贵州茅台(sh600519) 中波 T+1 未破5日线 持有")

    # 中波 T+1，破5日线 → 持有（2026-09-03 主人裁决：破5日线不卖出）
    advice = advisor.advise_exit(mid_band, holding_days=1,
                                  current_price=10.10, ma5=10.30,
                                  code='sh600519', name='贵州茅台')
    assert not advice.should_exit, "破5日线按主人裁决不再卖出"
    assert advice.rule == 'hold'
    print(f"  ✅ 贵州茅台(sh600519) 中波破5日线 持有（主人9/3裁决）: {advice.reason[:60]}...")

    # 中波 T+3，未破5日线 → 持有（主人 9/7 裁决：超持不构成卖出理由）
    advice = advisor.advise_exit(mid_band, holding_days=3,
                                  current_price=10.50, ma5=10.30,
                                  code='sh600519', name='贵州茅台')
    assert not advice.should_exit, "中波 T+3 按主人裁决不再卖出"
    assert advice.rule == 'hold'
    print(f"  ✅ 贵州茅台(sh600519) 中波 T+3 持有（超持观察，主人9/7裁决）")

    # 中波 T+5，未破5日线 → 持有（超持观察，主人 9/7 裁决）
    advice = advisor.advise_exit(mid_band, holding_days=5,
                                  current_price=10.50, ma5=10.30,
                                  code='sh600519', name='贵州茅台')
    assert not advice.should_exit
    assert advice.rule == 'hold'
    print(f"  ✅ 贵州茅台(sh600519) 中波 T+5 持有（超持观察，主人9/7裁决）")

    # 中波，MA5 缺失 → 不触发破5日线，只看超持
    advice = advisor.advise_exit(mid_band, holding_days=1,
                                  current_price=10.50, ma5=None,
                                  code='sh600519', name='贵州茅台')
    assert not advice.should_exit, "MA5 缺失+T+1 应持有"
    print(f"  ✅ 贵州茅台(sh600519) 中波 MA5缺失 T+1 持有（不触发破5日线）")

    # ===== R5b 破5日线优先级已由主人裁决停用（2026-09-03） =====
    print("\n--- R5b：T+5 且破5日线 → 持有（裁决后破5日线不构成卖出） ---")
    advice = advisor.advise_exit(mid_band, holding_days=5,
                                  current_price=10.10, ma5=10.30,
                                  code='sh600519', name='贵州茅台')
    assert not advice.should_exit
    assert advice.rule == 'hold', "破5日线按主人裁决返回持有"
    print(f"  ✅ T+5 且破5日线 → hold（破5日线不卖出，裁决生效）")

    # ===== calc_amp40 工具方法 =====
    print("\n--- calc_amp40 振幅计算 ---")
    import random
    random.seed(42)
    # 模拟 40 日数据：价格在 9.5-10.5 之间波动
    highs = [10.0 + random.random() for _ in range(40)]
    lows = [9.5 + random.random() * 0.3 for _ in range(40)]
    pre_close = 10.0
    amp = HoldingPeriodAdvisor.calc_amp40(highs, lows, pre_close)
    assert amp is not None and 0.05 < amp < 0.20, f"振幅应在合理范围: {amp}"
    print(f"  ✅ 40日振幅计算: {amp:.4f}（{amp*100:.2f}%）")

    # 数据不足
    amp = HoldingPeriodAdvisor.calc_amp40([10, 11], [9, 10], 10.0)
    assert amp is None, "数据不足应返回 None"
    print(f"  ✅ 数据不足返回 None")

    # ===== 不适用档位 =====
    print("\n--- 不适用档位（invalid/low/high 交 R2 处理）---")
    for amp_val, label in [(float('nan'), 'NaN'), (0.08, 'low'), (0.65, 'high')]:
        b = advisor.classify(amp_val)
        adv = advisor.advise_exit(b, holding_days=1, current_price=10.0,
                                   code='sz000001', name='平安银行')
        assert not adv.should_exit, f"{label} 档不应由 R5 触发退出"
        assert 'R2' in adv.reason or '不适用' in adv.reason
        print(f"  ✅ {label} 档 → {adv.rule}: {adv.reason[:50]}...")

    # ===== 复刻 W15 决定性场景 =====
    print("\n--- 复刻 W15 决定性场景 ---")
    # 甜区 + 持有 T+1：W15 毛收益 +2.124%（历史证据保留；退出已按 9/7 裁决停用）
    ok, _, sweet_band = advisor.check_entry_amplitude(0.20, 'sz002537', '海联金汇')
    assert ok and sweet_band.gross_return_pct == 2.124
    adv = advisor.advise_exit(sweet_band, holding_days=1,
                               current_price=10.20, entry_price=10.00,
                               code='sz002537', name='海联金汇')
    assert not adv.should_exit, "甜区 T+1 按主人 2026-09-07 裁决不再卖出"
    print(f"  ✅ 海联金汇(sz002537) 甜区 T+1：毛收益证据 +2.124% 保留，"
          f"退出触发已按主人 9/7 裁决停用（→ {adv.rule}）")

    # 高波入场禁令：W15 毛收益 -0.016%
    ok, msg, high_band = advisor.check_entry_amplitude(0.65, 'sz300534', '陇神戎发')
    assert not ok
    assert high_band.gross_return_pct == -0.016
    print(f"  ✅ 陇神戎发(sz300534) 高波：毛收益 {high_band.gross_return_pct:+.3f}% "
          f"→ 入场禁令生效（策略级失败，非滑点问题）")

    print("\n" + "=" * 68)
    print(" holding_period_advisor 自测全绿 ✓")
    print(" 卖出侧现存触发器仅 R2 止损(-8%)：R5a_t1/R5b_ma5/R5b_overhold 已按")
    print(" 主人裁决停用（9/3 破5日线 + 9/7 持有时间）/ R5c 高波入场禁令保留")
    print(" 证据链：W14 持仓周期 + W15 滑点敏感度（历史证据保留）")
    print("=" * 68)


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO,
                        format='%(levelname)s %(name)s: %(message)s')
    if '--selftest' in sys.argv or len(sys.argv) == 1:
        _selftest()
