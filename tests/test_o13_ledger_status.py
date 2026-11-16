#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_o13_ledger_status.py — O13 台账撤单状态字段回归（2026-09-03, B13 收口验证）

五断言（stub 临时台账路径, 零真实台账写入）:
  A. log_submitted 新行携带 status=OPEN（写侧单一规范字段）
  B. mark_order_status → CANCELLED 回填生效（is_cancelled=True + note 落行）
  C. mark_order_status 对不在台账的 order_id 返回 False（NOT_FOUND 不静默）
  D. is_cancelled 兼容旧 cancelled 布尔（9/2 口径）且不误判 OPEN/空行
  E. 坏行原样保留（mark_order_status 原子重写不丢行）
运行: python tests/test_o13_ledger_status.py（--no-log 语义: 本测试不触 guard_precheck_log/真实台账）
"""
import os
import sys
import tempfile

sys.path.insert(0, r"D:\zcodesj")
import executor_order_ledger as ledger  # noqa: E402


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="o13_test_")
    fake = os.path.join(tmpdir, "executor_orders.jsonl")
    ledger.EXEC_ORDERS = fake  # stub: 隔离真实台账

    # A. log_submitted 携带 status=OPEN
    oid = ledger.log_submitted({"data": {"orderId": "T1"}}, "002811", "郑中设计",
                               "BUY", 15.58, 7000)
    rows = ledger.load_orders()
    assert oid == "T1" and len(rows) == 1, f"A1 失败: {rows}"
    assert rows[0].get("status") == "OPEN", f"A2 失败: {rows[0]}"

    # E(前置). 坏行原样保留：追加垃圾行后再回填
    with open(fake, "a", encoding="utf-8") as f:
        f.write("NOT_JSON_GARBAGE_LINE\n")
    ok = ledger.mark_order_status("T1", "CANCELLED", "test cancel")
    assert ok is True, "B1 失败: 回填返回 False"
    with open(fake, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    assert any(l == "NOT_JSON_GARBAGE_LINE" for l in lines), "E 失败: 坏行丢失"
    rows = ledger.load_orders()
    assert rows[0].get("status") == "CANCELLED", f"B2 失败: {rows[0]}"
    assert rows[0].get("status_note") == "test cancel", "B3 失败: note 未落"
    assert ledger.is_cancelled(rows[0]) is True, "B4 失败: is_cancelled 未识别"

    # C. NOT_FOUND 不静默
    assert ledger.mark_order_status("T9", "CANCELLED") is False, "C 失败"

    # D. 兼容旧 cancelled 布尔
    assert ledger.is_cancelled({"cancelled": True}) is True, "D1 失败"
    assert ledger.is_cancelled({"status": "OPEN"}) is False, "D2 失败"
    assert ledger.is_cancelled({"status": "CANCELLED"}) is True, "D3 失败"
    assert ledger.is_cancelled({}) is False, "D4 失败"

    print("O13 回归 5/5 PASS (A/B/C/D/E)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
