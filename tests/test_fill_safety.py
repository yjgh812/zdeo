#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""F1/F2 伪成交防护单元测试(纯函数级, 零网络零写库)。

覆盖: apply_sell_mkt_floor 触发/带内/边界/行情缺失/止损/跌停板/负例,
      BUY_MKT_CAP 与 SELL_MKT_FLOOR 常量契约。
运行: python tests/test_fill_safety.py [--no-log]
exit: 0=全过 1=有失败
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trade_executor import SELL_MKT_FLOOR, BUY_MKT_CAP, apply_sell_mkt_floor  # noqa: E402


def t_floor_triggers():
    """焦煤9/3实锤场景: 挂6.53市价6.97 → 上修6.90, info完整"""
    p, info = apply_sell_mkt_floor(6.53, 6.97)
    assert p == 6.90, f"期望6.90实得{p}"
    assert info and info["orig"] == 6.53 and info["floored"] == 6.90
    assert info["mkt"] == 6.97 and info["mult"] == SELL_MKT_FLOOR


def t_noop_in_band():
    """带内挂价不动: 6.95 ≥ 6.97×0.99=6.9003"""
    p, info = apply_sell_mkt_floor(6.95, 6.97)
    assert p == 6.95 and info is None


def t_boundary_exact_floor_noop():
    """恰好等于下限(含round后)不动作: 6.90 vs floor 6.90"""
    p, info = apply_sell_mkt_floor(6.90, 6.97)
    assert p == 6.90 and info is None


def t_market_equiv_noop():
    """默认取实时价的卖出(价==市价)零影响"""
    p, info = apply_sell_mkt_floor(7.27, 7.27)
    assert p == 7.27 and info is None


def t_stop_loss_at_market_noop():
    """-8%止损单按市价提交: 市价已跌, 价≈市价 → 不触发"""
    p, info = apply_sell_mkt_floor(18.24, 18.25)
    assert p == 18.24 and info is None


def t_limit_down_board_sell_noop():
    """跌停板卖出: 价==跌停价==市价 → 不触发(合法唯一出场)"""
    p, info = apply_sell_mkt_floor(5.12, 5.12)
    assert p == 5.12 and info is None


def t_mkt_none_passthrough():
    """行情缺失: 原样返回不误伤(调用方负责告警)"""
    p, info = apply_sell_mkt_floor(6.53, None)
    assert p == 6.53 and info is None


def t_price_none_passthrough():
    p, info = apply_sell_mkt_floor(None, 6.97)
    assert p is None and info is None


def t_price_above_mkt_noop():
    """挂价高于市价(激进卖,立即成交更优): 不动"""
    p, info = apply_sell_mkt_floor(7.10, 6.97)
    assert p == 7.10 and info is None


def t_constants_contract():
    """常量契约: 买封顶>1 / 卖下限<1; 0.99下限保证'保必成'语义(限价低于市价必成交)"""
    assert BUY_MKT_CAP == 1.005
    assert SELL_MKT_FLOOR == 0.99
    assert BUY_MKT_CAP > 1 > SELL_MKT_FLOOR > 0.95


# ---- 端到端集成(do_sell 真实路径, stub 行情+拦截 pre_sell 子进程, dry-run 零外部副作用) ----

class _StubWS:
    """最小 westock 桩: positions/quote 按构造参数返回"""

    def __init__(self, mkt_price):
        self._mkt = mkt_price

    def positions(self):
        return {"data": {"positions": [
            {"code": "sh603626", "name": "科森科技", "quantity": 700,
             "availableQty": 700, "costPrice": 19.95}]}}

    def quote(self, code):
        return {"data": {code: {"price": self._mkt, "prev_close": self._mkt}}}


def _run_do_sell(price, mkt, is_stop_loss=False, cmd_out=None):
    """驱动 do_sell dry-run: 拦截 pre_sell 子进程返回 SELL_OK, 返回 result。
    cmd_out: 传入 list 时捕获 pre_sell sc_cmd 供断言。"""
    import argparse
    import json as _json
    import trade_executor as te

    def _fake_run(cmd, *args, **kwargs):
        if cmd_out is not None:
            cmd_out.extend(cmd)
        class _P:
            stdout = _json.dumps({"verdict": "SELL_OK", "hold_reasons": [], "risk_hints": []})
        return _P()

    orig_run = te.subprocess.run
    te.subprocess.run = _fake_run
    try:
        a = argparse.Namespace(code="sh603626", name="科森科技", price=price,
                               volume=None, reason="test", execute=False,
                               confirmed=False, is_stop_loss=is_stop_loss,
                               owner_emergency=False, owner_override_hold=False,
                               owner_override_session=False, no_c6_halve=False,
                               limit_price=None, amount=None)
        return te.do_sell(a, _StubWS(mkt))
    finally:
        te.subprocess.run = orig_run


def t_do_sell_floor_integration():
    """端到端: 挂17.50 vs 市价19.00 → dry-run result 价格上修18.81+留痕字段"""
    r = _run_do_sell(17.50, 19.00)
    assert r["price"] == 18.81, f"期望上修18.81实得{r['price']}"
    assert r.get("price_floored_by_mkt", {}).get("orig") == 17.50
    assert r.get("price_floored_by_mkt", {}).get("floored") == 18.81
    # pnl 用上修后价格(result 内 round4): 18.81/19.95-1 ≈ -5.71% (非挂低价时的-12.28%)
    assert abs(r["pnl_pct"] - round(18.81 / 19.95 - 1, 4)) < 1e-9


def t_do_sell_no_floor_in_band():
    """端到端负例: 带内挂价18.95(市价19.00)不触发, 无留痕字段"""
    r = _run_do_sell(18.95, 19.00)
    assert r["price"] == 18.95
    assert "price_floored_by_mkt" not in r


def t_do_sell_quote_missing_warn_only():
    """端到端负例: 行情空壳时不误伤(原价放行)——stub返回无price"""
    import argparse
    import json as _json
    import trade_executor as te

    class _NoQuoteWS(_StubWS):
        def quote(self, code):
            return {"data": {code: {}}}

    def _fake_run(*args, **kwargs):
        class _P:
            stdout = _json.dumps({"verdict": "SELL_OK", "hold_reasons": [], "risk_hints": []})
        return _P()

    orig_run = te.subprocess.run
    te.subprocess.run = _fake_run
    try:
        a = argparse.Namespace(code="sh603626", name="科森科技", price=17.50,
                               volume=None, reason="test", execute=False,
                               confirmed=False, is_stop_loss=False,
                               owner_emergency=False, owner_override_hold=False,
                               owner_override_session=False, no_c6_halve=False,
                               limit_price=None, amount=None)
        r = te.do_sell(a, _NoQuoteWS(0))
        assert r["price"] == 17.50, "行情缺失时应原样放行不误伤"
        assert "price_floored_by_mkt" not in r
    finally:
        te.subprocess.run = orig_run


def t_do_sell_passes_stoploss_flag():
    """A案(2026-09-05主人裁决): is_stop_loss=True → pre_sell收到--stop-loss"""
    cmd = []
    _run_do_sell(18.24, 18.25, is_stop_loss=True, cmd_out=cmd)
    assert "--stop-loss" in cmd, f"sc_cmd缺--stop-loss: {cmd[-8:]}"


def t_do_sell_no_flag_when_not_stoploss():
    """A案负例: 普通卖(is_stop_loss=False)不传--stop-loss, 守卫行为不变"""
    cmd = []
    _run_do_sell(18.95, 19.00, is_stop_loss=False, cmd_out=cmd)
    assert "--stop-loss" not in cmd, "普通卖不应携带--stop-loss"


def t_presell_s5_lane_real_subprocess():
    """A案真源回归(--no-log防污染): 科森9/4场景 --stop-loss达线 → SELL_OK+s5标记。
    9/22 契约更新: 8/14 主人裁决废 S-2 尾盘锁后 pre_sell_check 无时间窗 HOLD 可豁免,
    overridden_holds 留痕通道保留但允许为空(仅当存在 hold_reasons 时要求覆盖)。"""
    import subprocess as sp
    r = sp.run([sys.executable, "pre_sell_check.py", "--code", "603626",
                "--name", "科森科技", "--loss-pct", "-0.081", "--stop-loss", "--no-log"],
               capture_output=True, text=True, encoding="utf-8", cwd=os.path.dirname(
                   os.path.dirname(os.path.abspath(__file__))))
    out = _json_load(r.stdout)
    assert out["verdict"] == "SELL_OK" and out["info"].get("s5_stoploss_pass") is True
    holds = out.get("hold_reasons") or []
    overridden = out.get("overridden_holds") or []
    if holds:
        assert len(overridden) >= 1, "被豁免的holds应留审计"


def t_presell_no_flag_current_contract_sell_ok():
    """无--stop-loss → 现行契约 SELL_OK(8/14裁决废尾盘锁后时间窗仅为提示不拦截)。
    历史: 9/4 科森 HOLD 原文消息场景已随裁决退役, 保留用例名改写现行语义。"""
    import subprocess as sp
    r = sp.run([sys.executable, "pre_sell_check.py", "--code", "603626",
                "--name", "科森科技", "--loss-pct", "-0.081", "--no-log"],
               capture_output=True, text=True, encoding="utf-8", cwd=os.path.dirname(
                   os.path.dirname(os.path.abspath(__file__))))
    out = _json_load(r.stdout)
    assert out["verdict"] == "SELL_OK" and out["info"].get("s5_stoploss_pass") is None


def _json_load(s):
    import json
    # stdout 含 stderr 弹性: 取首个 '{' 起解析
    return json.loads(s[s.index("{"):])


def t_presell_log_persists_overridden():
    """审计链补全: S-5放行的overridden_holds必须落guard_precheck_log(rejects带标记前缀)。
    monkeypatch sqlite3.connect, 零真实写库。"""
    import argparse
    import json
    import sqlite3 as _sq
    import pre_sell_check as psc
    captured = {}

    class _FakeCon:
        def execute(self, sql, params):
            captured["sql"], captured["params"] = sql, params

        def commit(self):
            pass

        def close(self):
            pass

    orig = _sq.connect
    _sq.connect = lambda *a, **k: _FakeCon()
    try:
        args = argparse.Namespace(code="603626", name="科森科技", price=17.86, no_log=False)
        result = {"code": "603626", "name": "科森科技", "verdict": "SELL_OK",
                  "hold_reasons": [], "overridden_holds": ["尾盘建议: 当前亏损-8.1%已超线"],
                  "risk_hints": [], "info": {"s5_stoploss_pass": True}}
        psc._log_presell(args, result)
    finally:
        _sq.connect = orig
    rej = json.loads(captured["params"][7])
    assert rej and rej[0].startswith("[S-5放行豁免]") and "尾盘建议" in rej[0], f"rejects落库异常: {rej}"
    assert json.loads(captured["params"][8]) == [], "warnings应为risk_hints"


def t_presell_log_normal_sell_unchanged():
    """负例: 普通SELL_OK(无豁免)落库行为不变(rejects=holds原样, 无标记前缀)"""
    import argparse
    import json
    import sqlite3 as _sq
    import pre_sell_check as psc
    captured = {}

    class _FakeCon:
        def execute(self, sql, params):
            captured["params"] = params

        def commit(self):
            pass

        def close(self):
            pass

    orig = _sq.connect
    _sq.connect = lambda *a, **k: _FakeCon()
    try:
        args = argparse.Namespace(code="603626", name="科森科技", price=18.95, no_log=False)
        result = {"code": "603626", "name": "科森科技", "verdict": "SELL_OK",
                  "hold_reasons": [], "overridden_holds": [],
                  "risk_hints": ["C8提示"], "info": {}}
        psc._log_presell(args, result)
    finally:
        _sq.connect = orig
    assert json.loads(captured["params"][7]) == []
    assert json.loads(captured["params"][8]) == ["C8提示"]


TESTS = [t_floor_triggers, t_noop_in_band, t_boundary_exact_floor_noop,
         t_market_equiv_noop, t_stop_loss_at_market_noop,
         t_limit_down_board_sell_noop, t_mkt_none_passthrough,
         t_price_none_passthrough, t_price_above_mkt_noop, t_constants_contract,
         t_do_sell_floor_integration, t_do_sell_no_floor_in_band,
         t_do_sell_quote_missing_warn_only, t_do_sell_passes_stoploss_flag,
         t_do_sell_no_flag_when_not_stoploss, t_presell_s5_lane_real_subprocess,
         t_presell_no_flag_current_contract_sell_ok, t_presell_log_persists_overridden,
         t_presell_log_normal_sell_unchanged]


def main():
    fails = []
    for t in TESTS:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except AssertionError as e:
            fails.append(t.__name__)
            print(f"  ❌ {t.__name__}: {e}")
    print(f"\n{len(TESTS) - len(fails)}/{len(TESTS)} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
