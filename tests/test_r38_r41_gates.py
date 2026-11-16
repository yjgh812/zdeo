# -*- coding: utf-8 -*-
"""R38期望闸/R41主线对齐闸 单元测试(R39落刀闸已退役 2026-11-06 主人令废除, 用例同批移除)。

纯函数分支全覆盖; 注入 gauge/state_info/feats/layer, 不触网不触生产台账。
运行: python tests/test_r38_r41_gates.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date as _date  # noqa: E402

import pre_trade_check_v2 as ptc  # noqa: E402

# 20261023 夹具动态化: 原硬编码 asof="20260930", 跨10天陈旧阈值后四用例系统性
# fail-open（保护正确触发=夹具腐烂）; 改当日戳, 陈旧分支保留 20250101 对照。
TODAY_STAMP = _date.today().strftime("%Y%m%d")

GAUGE_NEG = {"asof": TODAY_STAMP,
             "cells": {"A低位横盘": {"t20e_med_pct": -1.14, "sample_ok20": True}}}
STATE_WEAK = {"state": "LOW_VOL", "stale": False, "asof": TODAY_STAMP}
STATE_NORMAL = {"state": "NORMAL", "stale": 3, "asof": TODAY_STAMP}
FEATS_A = {"pos250": 16.0, "chg5": 1.2}   # → A低位横盘

R41_LAYER_OK = {"target_date": TODAY_STAMP, "source1_mainline": {"c1_volume": ["房地产"]}}


# ---- R38 期望闸 ----

def test_r38_reject_weak_negative():
    r = ptc.check_r38_expectancy("600603", gauge=GAUGE_NEG, state_info=STATE_WEAK, feats=FEATS_A)
    assert r["status"] == "reject", r


def test_r38_owner_approve_downgrades_to_warn():
    r = ptc.check_r38_expectancy("600603", owner_approved=True, gauge=GAUGE_NEG,
                                 state_info=STATE_WEAK, feats=FEATS_A)
    assert r["status"] == "warn" and "owner-approve" in r["note"], r


def test_r38_normal_regime_negative_is_warn():
    r = ptc.check_r38_expectancy("600603", gauge=GAUGE_NEG, state_info=STATE_NORMAL, feats=FEATS_A)
    assert r["status"] == "warn", r


def test_r38_positive_expectancy_pass():
    g = {"asof": TODAY_STAMP,
         "cells": {"A低位横盘": {"t20e_med_pct": 0.5, "sample_ok20": True}}}
    r = ptc.check_r38_expectancy("600603", gauge=g, state_info=STATE_WEAK, feats=FEATS_A)
    assert r["status"] == "pass", r


# ---- R41 主线对齐闸(WARN-only) ----

def test_r41_mainline_in_pass():
    r = ptc.check_r41_mainline("房地产", layer=R41_LAYER_OK)
    assert r["status"] == "pass", r


def test_r41_mainline_out_warn():
    r = ptc.check_r41_mainline("医药生物", layer=R41_LAYER_OK)
    assert r["status"] == "warn" and "非主线候选提示" in r["note"], r


def test_r41_empty_c1_fail_open():
    layer = {"target_date": "20260930", "source1_mainline": {"c1_volume": []}}
    r = ptc.check_r41_mainline("房地产", layer=layer)
    assert r["status"] == "fail_open", r


def test_r41_stale_fail_open():
    layer = {"target_date": "20250101", "source1_mainline": {"c1_volume": ["房地产"]}}
    r = ptc.check_r41_mainline("房地产", layer=layer)
    assert r["status"] == "fail_open", r


def test_r41_no_sector_fail_open():
    r = ptc.check_r41_mainline(None, layer=R41_LAYER_OK)
    assert r["status"] == "fail_open", r


if __name__ == "__main__":
    tests = {k: v for k, v in sorted(globals().items()) if k.startswith("test_")}
    fails = 0
    for name, fn in tests.items():
        try:
            fn()
            print("PASS", name)
        except AssertionError as e:
            fails += 1
            print("FAIL", name, e)
    print("%d/%d ALL PASS" % (len(tests) - fails, len(tests)) if not fails
          else "%d FAIL of %d" % (fails, len(tests)))
    sys.exit(1 if fails else 0)
