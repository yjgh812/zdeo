# -*- coding: utf-8 -*-
"""R38期望闸/R39落刀闸 单元测试(2026-10-08落地, 主人授权「修复、优化、迭代」)。

纯函数分支全覆盖; 注入 gauge/state_info/feats/quote_fetch, 不触网不触生产台账。
运行: python tests/test_r38_r39_gates.py
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
STATE_NORMAL = {"state": "NORMAL", "stale": False, "asof": TODAY_STAMP}
FEATS_A = {"pos250": 16.0, "chg5": 1.2}   # → A低位横盘


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
         "cells": {"A低位横盘": {"t20e_med_pct": 2.5, "sample_ok20": True}}}
    r = ptc.check_r38_expectancy("600603", gauge=g, state_info=STATE_WEAK, feats=FEATS_A)
    assert r["status"] == "pass", r


def test_r38_stale_gauge_fail_open():
    g = {"asof": "20250101", "cells": GAUGE_NEG["cells"]}
    r = ptc.check_r38_expectancy("600603", gauge=g, state_info=STATE_WEAK, feats=FEATS_A)
    assert r["status"] == "fail_open", r


def test_r38_unclassifiable_fail_open():
    r = ptc.check_r38_expectancy("600603", gauge=GAUGE_NEG, state_info=STATE_WEAK,
                                 feats={"pos250": None, "chg5": None})
    assert r["status"] == "fail_open", r


def test_r38_missing_gauge_fail_open():
    # 合同: gauge=None=自动读盘(盘上有真实缓存); 无缓存分支用空 asof 字典表达
    r = ptc.check_r38_expectancy("600603", gauge={"asof": "", "cells": {}},
                                 state_info=STATE_WEAK, feats=FEATS_A)
    assert r["status"] == "fail_open", r


# ---- R39 落刀闸退役回归(2026-11-06 主人令废除; 判定函数已从源码移除) ----

def test_r39_retired_no_function():
    assert not hasattr(ptc, "check_r39_fallingknife"), "R39判定函数应已移除"


def test_r39_retired_no_flag():
    import inspect
    lines = inspect.getsource(ptc).splitlines()
    active = [l for l in lines
              if l.strip() and not l.strip().startswith("#") and "--owner-approve-r39" in l]
    assert not active, "R39特批口应已从活代码移除(墓碑注释留档允许): %s" % active[:2]


def test_classify_boundaries():
    assert ptc._r38_classify(16.0, 1.2) == "A低位横盘"
    assert ptc._r38_classify(70.0, 7.0) == "B启动确认"
    assert ptc._r38_classify(50.0, 20.0) == "C追高禁区"


def test_classify_boundaries():
    assert ptc._r38_classify(16.0, 1.2) == "A低位横盘"
    assert ptc._r38_classify(70.0, 7.0) == "B启动确认"
    assert ptc._r38_classify(50.0, 20.0) == "C追高禁区"
    assert ptc._r38_classify(97.0, 2.0) == "D高位滞涨"
    assert ptc._r38_classify(50.0, 12.0) == "R其余"


# ---- R41 主线对齐闸(WARN-only) ----
# 20261023 同夹具腐烂修复: target_date 原硬编码 20260930, 陈旧阈值同样命中 fail-open。
LAYER_OK = {"target_date": TODAY_STAMP, "source1_mainline": {"c1_volume": ["房地产"]}}


def test_r41_mainline_in_pass():
    r = ptc.check_r41_mainline("房地产", layer=LAYER_OK)
    assert r["status"] == "pass", r


def test_r41_mainline_out_warn():
    r = ptc.check_r41_mainline("医药生物", layer=LAYER_OK)
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
    r = ptc.check_r41_mainline(None, layer=LAYER_OK)
    assert r["status"] == "fail_open", r


if __name__ == "__main__":
    tests = {k: v for k, v in sorted(globals().items()) if k.startswith("test_")}
    fails = 0
    for name, fn in tests.items():
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL {name}: {e}")
        except Exception as e:
            fails += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print("R38/R39 单元测试:", "ALL PASS" if fails == 0 else f"{fails} FAIL")
    sys.exit(1 if fails else 0)
