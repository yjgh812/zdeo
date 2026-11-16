#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_fill_confirm_monitor.py — 兼容壳（2026-08-28 债务清偿）

原 5 用例已并入唯一权威套件 tests/test_fill_confirm_regression.py（重复场景去重、
tearDown 生产路径还原缺陷已修、补 REPORT_DIR 隔离）。本文件保留为兼容入口：
旧引用（SOP/记忆/肌肉记忆）直接运行本文件 = 运行权威套件。
"""
from __future__ import annotations

import sys
import unittest

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\tests")

import test_fill_confirm_regression as canonical  # noqa: E402

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(canonical)
    ok = unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()
    sys.exit(0 if ok else 1)
