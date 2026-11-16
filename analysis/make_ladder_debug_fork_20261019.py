# -*- coding: utf-8 -*-
"""
框架级issue#0203调试fork生成器：从ladder fork再派生debug变体
- 窗口缩短 2025-06-01 ~ 2026-04-30（覆盖0122-0203疑点窗）
- monkey-patch运行时打点（零框架文件修改）：
  * SingleStrategyBacktest._enqueue_sell 每次调用打印 date/code/reason/当时浮亏
  * engine.check_risk_controls 每日打印 is_ok/warnings（疑点窗口全打）
"""
SRC = "D:/zcodesj/analysis/bt_full_report_ladder_20261019.py"
DST = "D:/zcodesj/analysis/bt_full_report_ladder_debug_20261019.py"

src = open(SRC, encoding="utf-8").read()

# 1) 缩短窗口
import re
src2 = re.sub(r'START_DATE = "[^"]*"', 'START_DATE = "2025-06-01"', src, count=1)
src2 = re.sub(r'END_DATE = "[^"]*"(?=  #)', 'END_DATE = "2026-04-30"', src2, count=1)
assert 'START_DATE = "2025-06-01"' in src2 and 'END_DATE = "2026-04-30"' in src2, "窗口锚点替换失败"

# 2) 注入 monkey-patch（插在 cfg = BacktestConfig( 之前）
patch_code = '''
# ==== DEBUG PATCH (issue#0203排查, 运行时打点, 零框架文件修改) ====
import functools as _ft
from core.backtest_system import strategy as _strat
from core.backtest_system import engine as _eng

_orig_enqueue = _strat.SingleStrategyBacktest._enqueue_sell
def _dbg_enqueue(self, code, prices, opens, date, reason="signal"):
    pos = self.engine.portfolio.get_position(code)
    _u = pos.unrealized_pnl_pct if pos and pos.shares > 0 else None
    _p = prices.get(code)
    print(f"[ENQUEUE] date={date} code={code} reason={reason} unreal={_u} px={_p}", flush=True)
    return _orig_enqueue(self, code, prices, opens, date, reason=reason)
_strat.SingleStrategyBacktest._enqueue_sell = _dbg_enqueue

_orig_risk = _eng.BacktestEngineV4.check_risk_controls
def _dbg_risk(self, prices):
    ok, warns = _orig_risk(self, prices)
    if (not ok) or warns:
        print(f"[RISK] date={getattr(self, '_dbg_date', '?')} ok={ok} warns={warns}", flush=True)
    return ok, warns
_eng.BacktestEngineV4.check_risk_controls = _dbg_risk

_orig_settle = _eng.BacktestEngineV4.settle_day
def _dbg_settle(self, date, prices):
    self._dbg_date = date
    return _orig_settle(self, date, prices)
_eng.BacktestEngineV4.settle_day = _dbg_settle
# ==== END DEBUG PATCH ====

'''
anchor = "cfg = BacktestConfig("
assert src2.count(anchor) == 1
src2 = src2.replace(anchor, patch_code + anchor)

open(DST, "w", encoding="utf-8").write(src2)
import py_compile
py_compile.compile(DST, doraise=True)
print("DEBUG_FORK_OK", DST)
