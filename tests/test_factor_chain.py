# -*- coding: utf-8 -*-
"""test_factor_chain.py — 因子多源链回归 fixture（20261011批5尾随 c459f29 落地）。

覆盖合同：
  F1 fetch_factor 正常路径：sina DataFrame 原样返回
  F2 EMPTY 透传：sina 返回 "EMPTY" 且他源亦无 → "EMPTY"（worker 走 factor=1.0 ok 路径）
  F3 sina 超时→baostock 回退：sina 挂起(>FACTOR_SINA_TIMEOUT_S 模拟为抛 TimeoutError
     同型结果 None)时整串切换 baostock
  F4 连续性闸拒收：回退源因子含超板限悬崖 → 拒收→failed(None)，不收错基准
  F5 连续性闸放行：正常因子回退 → 收下
  F6 run 级降级：sina 连续 FACTOR_DEMOTE_AFTER 次失败后，后续股票不再调用 sina

全部 monkeypatch 模块内取数器与 read_tdx_day，零网络零写库。--no-log 不适用
（本测试不产 guard_precheck_log 噪音）。
"""
import os
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build_qfq_meta_tdx as b


def _fac(dates, factors):
    df = pd.DataFrame({"date": pd.to_datetime(dates, format="%Y%m%d"), "factor": factors})
    return df.set_index("date")


def _raw(dates, closes):
    df = pd.DataFrame({"date": pd.to_datetime(dates, format="%Y%m%d"), "close": closes,
                       "open": closes, "high": closes, "low": closes,
                       "volume": [1e5] * len(closes)})
    return df.set_index("date")


@pytest.fixture(autouse=True)
def _reset_state():
    b._factor_demoted.clear()
    b._factor_slow_streak.clear()
    yield
    b._factor_demoted.clear()
    b._factor_slow_streak.clear()


D4 = ["20260105", "20260106", "20260107", "20260108"]


def test_f1_normal_sina_passthrough(monkeypatch):
    f = _fac(D4, [1.0, 1.0, 0.99, 0.99])
    monkeypatch.setattr(b, "fetch_sina_qfq", lambda c: f)
    monkeypatch.setattr(b, "fetch_tushare_qfq_factor", lambda c: pytest.fail("不应触达备源"))
    monkeypatch.setattr(b, "fetch_baostock_qfq_factor", lambda c: pytest.fail("不应触达备源"))
    assert b.fetch_factor("600519") is f


def test_f2_empty_passthrough(monkeypatch):
    monkeypatch.setattr(b, "fetch_sina_qfq", lambda c: "EMPTY")
    monkeypatch.setattr(b, "fetch_tushare_qfq_factor", lambda c: "EMPTY")
    monkeypatch.setattr(b, "fetch_baostock_qfq_factor", lambda c: "EMPTY")
    assert b.fetch_factor("600519") == "EMPTY"


def test_f3_sina_fail_falls_to_baostock(monkeypatch):
    monkeypatch.setattr(b, "fetch_sina_qfq", lambda c: None)   # 超时/失败同型
    f = _fac(D4, [1.0, 1.0, 1.0, 1.0])
    seen = {}
    monkeypatch.setattr(b, "read_tdx_day",
                        lambda c: _raw(D4, [10.0, 10.1, 10.2, 10.15]))  # 合成平价raw, 隔离真史除权跳变
    monkeypatch.setattr(b, "fetch_tushare_qfq_factor",
                        lambda c: pytest.fail("tushare在baostock之前不应触达"))
    monkeypatch.setattr(b, "fetch_baostock_qfq_factor", lambda c: (seen.setdefault("hit", 1), f)[1])
    out = b.fetch_factor("600519")
    assert out is f and seen.get("hit") == 1


def test_f4_gate_rejects_cliff(monkeypatch):
    # 回退源因子漏调 10转10 → qfq 出现 ~-50% 悬崖 → 闸拒收
    monkeypatch.setattr(b, "fetch_sina_qfq", lambda c: None)
    monkeypatch.setattr(b, "fetch_baostock_qfq_factor",
                        lambda c: _fac(D4, [1.0, 1.0, 1.0, 2.0]))   # 错基准: 尾部因子翻倍
    monkeypatch.setattr(b, "read_tdx_day",
                        lambda c: _raw(D4, [10.0, 10.1, 10.2, 5.1]))  # raw 已除权对齐→qfq悬崖
    monkeypatch.setattr(b, "fetch_tushare_qfq_factor", lambda c: None)
    assert b._qfq_continuity_ok("000626", _fac(D4, [1.0, 1.0, 1.0, 2.0])) is False
    assert b.fetch_factor("000626") is None                          # 拒收→failed


def test_f5_gate_passes_normal(monkeypatch):
    monkeypatch.setattr(b, "read_tdx_day",
                        lambda c: _raw(D4, [10.0, 10.1, 10.2, 10.15]))
    assert b._qfq_continuity_ok("600519", _fac(D4, [1.0, 1.0, 0.995, 0.995])) is True
    monkeypatch.setattr(b, "fetch_sina_qfq", lambda c: None)
    f = _fac(D4, [1.0, 1.0, 0.995, 0.995])
    monkeypatch.setattr(b, "fetch_baostock_qfq_factor", lambda c: f)
    assert b.fetch_factor("600519") is f


def test_f6_run_level_demotion(monkeypatch):
    calls = {"sina": 0}
    monkeypatch.setattr(b, "fetch_sina_qfq",
                        lambda c: calls.__setitem__("sina", calls["sina"] + 1) or None)
    monkeypatch.setattr(b, "fetch_baostock_qfq_factor",
                        lambda c: _fac(D4, [1.0, 1.0, 1.0, 1.0]))
    monkeypatch.setattr(b, "read_tdx_day",
                        lambda c: _raw(D4, [10.0, 10.1, 10.2, 10.15]))
    for _ in range(b.FACTOR_DEMOTE_AFTER):
        assert b.fetch_factor("600519") is not None
    # 2026-10-21 断言对齐: 2026-10-13 sina 分支加单次退避重试后, 每次 fetch_factor 产生
    # 2 次 fetch_sina_qfq 尝试(降级阈值按 streak 计), 旧断言 8 未随语义更新致恒挂。
    assert calls["sina"] == b.FACTOR_DEMOTE_AFTER * 2
    assert "sina" in b._factor_demoted
    assert b.fetch_factor("600519") is not None
    assert calls["sina"] == b.FACTOR_DEMOTE_AFTER * 2               # 降级后零再调用


def test_gate_threshold_by_board():
    f_flat = _fac(D4, [1.0, 1.0, 1.0, 1.0])
    raw_cliff = _raw(D4, [10.0, 10.0, 10.0, 7.0])                  # -30% 真实崩盘日
    monkeypatch_free = b._qfq_continuity_ok
    import build_qfq_meta_tdx as _b
    orig = _b.read_tdx_day
    _b.read_tdx_day = lambda c: raw_cliff
    try:
        assert monkeypatch_free("600519", f_flat) is False          # 主板 15% 线
        assert monkeypatch_free("300750", f_flat) is False          # 创业板 22% 线同样拒
    finally:
        _b.read_tdx_day = orig


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
