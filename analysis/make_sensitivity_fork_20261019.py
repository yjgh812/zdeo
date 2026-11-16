# -*- coding: utf-8 -*-
"""make_sensitivity_fork_20261019.py — 呈请①B 三参数敏感性 fork 生成器（2026-10-19「按推荐继续」）

克隆 analysis/bt_full_report_dose_20261018.py（10/18 剂量 fork，N15 生产等价口径），
注入三个环境旋钮（默认值=基线零变化，fail-closed 断言替换次数）：
  TULONG_SENS_COST_MULT  佣金倍率（1.5 = 费用×1.5 情景）
  TULONG_SENS_SLIP_ADD   滑点加码（0.0010 = 滑点+10bp 情景）
  TULONG_SENS_GAP        买入侧开盘缺口（0.01 = 开盘缺口+1% 情景，单边买入实现于 SlippageConfig.calculate）
产物目录由 TULONG_BT_OUT 控制。正本零改动。
"""
import py_compile
import sys

SRC = r"D:\zcodesj\analysis\bt_full_report_dose_20261018.py"
DST = r"D:\zcodesj\analysis\bt_full_report_sens_20261019.py"

OLD_COST = "    commission_rate=0.00025,"
NEW_COST = ('    commission_rate=0.00025 * float(os.environ.get("TULONG_SENS_COST_MULT", "1") or "1"),'
            '  # SENS fork: 佣金倍率旋钮(默认1=基线零变化)')

OLD_SLIP = "    slippage_rate=0.0003,"
NEW_SLIP = ('    slippage_rate=0.0003 + float(os.environ.get("TULONG_SENS_SLIP_ADD", "0") or "0"),'
            '  # SENS fork: 滑点加码旋钮(默认0=基线零变化)')

ANCHOR_IMPORT = "from core.backtest_system.export import export_results\n"
GAP_BLOCK = '''
# SENS fork: 买入侧开盘缺口旋钮(默认不启用=基线零变化; 单边买入口径, 对齐材料"开盘缺口+1%")
if os.environ.get("TULONG_SENS_GAP"):
    _SENS_GAP = float(os.environ["TULONG_SENS_GAP"])
    from core.backtest_system import costs as _sens_costs
    _sens_orig_slip = _sens_costs.SlippageConfig.calculate

    def _sens_slip_with_gap(self, price, volume, side, daily_amount=None):
        if side == "buy":
            fp = price * (1 + _SENS_GAP)
            return round(fp, 3), round(abs(fp - price) * volume, 2)
        return _sens_orig_slip(self, price, volume, side, daily_amount)

    _sens_costs.SlippageConfig.calculate = _sens_slip_with_gap
'''


def main():
    with open(SRC, "r", encoding="utf-8") as f:
        src = f.read()
    for old in (OLD_COST, OLD_SLIP, ANCHOR_IMPORT):
        n = src.count(old)
        if n != 1:
            raise SystemExit(f"[SENS-FORK] patch 锚点非唯一({n}): {old[:60]!r}")
    src = src.replace(OLD_COST, NEW_COST)
    src = src.replace(OLD_SLIP, NEW_SLIP)
    src = src.replace(ANCHOR_IMPORT, ANCHOR_IMPORT + GAP_BLOCK)
    with open(DST, "w", encoding="utf-8") as f:
        f.write(src)
    py_compile.compile(DST, doraise=True)
    print("[SENS-FORK] 生成+编译 OK:", DST)


if __name__ == "__main__":
    sys.exit(main())
