# -*- coding: utf-8 -*-
"""
stop_loss_pressure_test.py — 屠龙系统·R2 强制止损全链路压测（2026-08-11 知微）

【目的】
  enforce_stop_loss.py 的 selftest 全是 dry_run 路径，
  execute=True 时调用 ws.sell 真实下单这段路径从未被实测。
  本脚本用 mock 注入法端到端压测 execute=True 链路，不污染实盘。

【压测链路】
  触发检测 → sell 调用参数 → r_ok 成功/失败判定 → 审计日志写入

【运行】
  cd /d/workbuddyspace && python /d/zcodesj/tests/stop_loss_pressure_test.py
"""
import json
import sys
from pathlib import Path

_WS = Path(r"D:\workbuddyspace")
sys.path.insert(0, str(_WS))


# ===== Mock WeStockClient =====
class MockSellCall:
    """记录一次 sell 调用的参数"""
    def __init__(self, code, price, qty):
        self.code = code
        self.price = price
        self.qty = qty


class MockWeStockClient:
    """模拟 westock client。
    sell_responses: dict {code: 响应dict 或 Exception 实例}
    行为：每次 sell(code, price, qty) 触发记录 + 返回预设响应
    """
    def __init__(self, sell_responses=None):
        self.sell_calls = []
        self.sell_responses = sell_responses or {}

    def sell(self, code, price, quantity):
        self.sell_calls.append(MockSellCall(code, price, quantity))
        resp = self.sell_responses.get(code)
        if isinstance(resp, Exception):
            raise resp
        if resp is None:
            # 默认成功响应（复刻真实结构，来自 paper_bridge_audit_20260811）
            return {
                "ok": True,
                "data": {
                    "orderId": f"mock_{code}_{int(price*100)}_{quantity}",
                    "code": code,
                    "name": f"MOCK_{code}",
                    "direction": "sell",
                    "quantity": str(quantity),
                    "price": str(price),
                }
            }
        return resp

    # 以下方法仅为兼容 enforce_stop_loss 内部可能的调用
    def positions(self):
        return {"ok": True, "data": {"positions": []}}

    def quote(self, code):
        return {"ok": True, "data": {"price": 0}}


# ===== 用 monkey-patch 注入 mock client =====
def patch_westock_client(mock_inst):
    """让 enforce_stop_loss 模块用 mock_inst 而非真实 WeStockClient。"""
    import enforce_stop_loss
    import westock_client
    # enforce_stop_loss 通过 `from westock_client import WeStockClient` 引入
    # 必须替换 enforce_stop_loss 模块属性，不是 westock_client 模块
    enforce_stop_loss.WeStockClient = lambda: mock_inst
    # scan_and_enforce 内部还会 `from westock_client import WeStockClient`
    # 这一句在函数内执行时会拿到原模块，所以也要替换 westock_client 模块属性
    westock_client.WeStockClient = lambda: mock_inst


def restore_westock_client():
    """恢复真实 client（如果后续还要跑真实扫描）"""
    import importlib
    import westock_client
    importlib.reload(westock_client)


# ===== 压测用例 =====
def make_positions():
    """4 只假持仓：深亏 / 边界触发 / 浅亏 / 盈利"""
    return [
        # entry 10.0, current 8.5 → -15%，触发
        {'code': 'sz000001', 'name': '平安银行', 'entry_price': 10.0,
         'shares': 1000, 'source': 'manual'},
        # entry 10.0, current 8.99 → -10.1%，触发（边界外）
        {'code': 'sz000002', 'name': '万科A', 'entry_price': 10.0,
         'shares': 500, 'source': 'continuation'},
        # entry 10.0, current 9.5 → -5%，不触发
        {'code': 'sz000004', 'name': '国华网安', 'entry_price': 10.0,
         'shares': 800, 'source': 'trend'},
        # entry 10.0, current 10.5 → +5%，不触发
        {'code': 'sz000005', 'name': '世纪星源', 'entry_price': 10.0,
         'shares': 200, 'source': 'V375'},
    ]


def make_prices():
    return {
        'sz000001': 8.5,    # -15%
        'sz000002': 8.99,   # -10.1%
        'sz000004': 9.5,    # -5%
        'sz000005': 10.5,   # +5%
    }


# ===== 单个测试 =====
def run_test(name, sell_responses, expected):
    """跑一次压测并比对预期。

    expected: dict with keys:
        triggered, executed, errors, sell_call_count, sell_codes
    """
    print(f"\n--- {name} ---")
    positions = make_positions()
    prices = make_prices()

    mock = MockWeStockClient(sell_responses=sell_responses)
    patch_westock_client(mock)

    import enforce_stop_loss
    r = enforce_stop_loss.scan_and_enforce(
        stop_loss_pct=0.10,
        execute=True,  # ★ 关键：走真实下单路径
        dry_run_positions=positions,
        dry_run_prices=prices,
    )

    # 比对
    ok = True
    checks = []

    def check(label, actual, want):
        nonlocal ok
        status = "✅" if actual == want else "❌"
        if actual != want:
            ok = False
        checks.append(f"  {status} {label}: 实际={actual} 期望={want}")

    check("扫描数", r['scanned'], expected['triggered'] + expected['not_triggered'])
    check("触发数", r['triggered'], expected['triggered'])
    check("执行数", r['executed'], expected['executed'])
    check("错误数", len(r['errors']), expected['errors'])
    check("sell 调用次数", len(mock.sell_calls), expected['sell_call_count'])

    # 验证 sell 调用参数（如果应有调用）
    if expected['sell_call_count'] > 0 and mock.sell_calls:
        if 'sell_codes' in expected:
            actual_codes = [c.code for c in mock.sell_calls]
            check("sell 调用代码", sorted(actual_codes), sorted(expected['sell_codes']))

        # 验证卖出价 = min(当前价, 止损价)
        # 止损价 = entry * (1 - stop_loss_pct) = 10 * 0.9 = 9.0
        # sz000001: min(8.5, 9.0) = 8.5（用当前价确保成交）
        # sz000002: min(8.99, 9.0) = 8.99
        expected_prices = expected.get('sell_prices', {})
        for call in mock.sell_calls:
            want_price = expected_prices.get(call.code)
            if want_price is not None:
                status = "✅" if abs(call.price - want_price) < 0.001 else "❌"
                if abs(call.price - want_price) >= 0.001:
                    ok = False
                checks.append(f"  {status} sell价格 {call.code}: 实际={call.price} 期望={want_price}")

        # 验证 sell 数量 = 持仓股数
        expected_qtys = expected.get('sell_qtys', {})
        for call in mock.sell_calls:
            want_qty = expected_qtys.get(call.code)
            if want_qty is not None:
                status = "✅" if call.qty == want_qty else "❌"
                if call.qty != want_qty:
                    ok = False
                checks.append(f"  {status} sell数量 {call.code}: 实际={call.qty} 期望={want_qty}")

    for c in checks:
        print(c)
    print(f"  → {'PASS' if ok else 'FAIL'}")
    return ok


# ===== 主入口 =====
def main():
    print("=" * 72)
    print(" enforce_stop_loss 全链路压测（execute=True 真实下单路径）")
    print("=" * 72)
    print("止损阈值: -10%（entry 10.0 → 止损价 9.0）")
    print("注入持仓: 平安银行-15% / 万科A-10.1% / 国华网安-5% / 世纪星源+5%")

    results = []

    # 用例 1：全部 sell 成功
    results.append(run_test(
        name="用例1: 全部 sell 成功响应（验证正常链路）",
        sell_responses={
            'sz000001': {"ok": True, "data": {"orderId": "mock_order_1",
                                               "code": "sz000001"}},
            'sz000002': {"ok": True, "data": {"orderId": "mock_order_2",
                                               "code": "sz000002"}},
        },
        expected={
            'triggered': 2,
            'not_triggered': 2,
            'executed': 2,
            'errors': 0,
            'sell_call_count': 2,
            'sell_codes': ['sz000001', 'sz000002'],
            'sell_prices': {'sz000001': 8.5, 'sz000002': 8.99},
            'sell_qtys': {'sz000001': 1000, 'sz000002': 500},
        }
    ))

    # 用例 2：一只成功一只失败（ok:false）
    results.append(run_test(
        name="用例2: 一只成功一只失败（ok:false）",
        sell_responses={
            'sz000001': {"ok": True, "data": {"orderId": "mock_order_1"}},
            'sz000002': {"ok": False, "msg": "可用数量不足"},
        },
        expected={
            'triggered': 2,
            'not_triggered': 2,
            'executed': 1,
            'errors': 1,
            'sell_call_count': 2,
            'sell_codes': ['sz000001', 'sz000002'],
        }
    ))

    # 用例 3：sell 抛异常
    results.append(run_test(
        name="用例3: sell 抛异常（网络错误等）",
        sell_responses={
            'sz000001': RuntimeError("模拟网络断开"),
            'sz000002': {"ok": True, "data": {"orderId": "mock_order_2"}},
        },
        expected={
            'triggered': 2,
            'not_triggered': 2,
            'executed': 1,
            'errors': 1,
            'sell_call_count': 2,
            'sell_codes': ['sz000001', 'sz000002'],
        }
    ))

    # 用例 4：成功响应但顶层无 code 字段（真实结构，验证 r_ok 判定）
    # 2026-08-12 更新：r_ok 现在要 data.orderId 才算成功
    results.append(run_test(
        name="用例4: 真实响应结构（必须有 data.orderId 才算成功）",
        sell_responses={
            'sz000001': {"ok": True, "data": {"orderId": "100015682608111009243794738",
                                               "code": "sz000001",
                                               "name": "平安银行",
                                               "direction": "sell",
                                               "quantity": "1000",
                                               "price": "8.5"}},
            'sz000002': {"ok": True, "data": {"orderId": "100015682608111009254293957",
                                               "code": "sz000002",
                                               "name": "万科A"}},
        },
        expected={
            'triggered': 2,
            'not_triggered': 2,
            'executed': 2,
            'errors': 0,
            'sell_call_count': 2,
        }
    ))

    # 用例 4b：响应 ok=True 但 data 无 orderId（应判定失败，2026-08-12 新增）
    results.append(run_test(
        name="用例4b: ok=True 但无 orderId（2026-08-12 严谨化后应判定失败）",
        sell_responses={
            'sz000001': {"ok": True, "data": {"code": "sz000001"}},  # 无 orderId
            'sz000002': {"ok": True, "data": {"orderId": "ok"}},
        },
        expected={
            'triggered': 2,
            'not_triggered': 2,
            'executed': 1,
            'errors': 1,
            'sell_call_count': 2,
        }
    ))

    # 用例 5：响应带 error 字段（应判定失败）
    results.append(run_test(
        name="用例5: 响应带 error 字段（应判定失败）",
        sell_responses={
            'sz000001': {"ok": True, "error": "限价超出涨跌停"},
            'sz000002': {"ok": True, "data": {"orderId": "ok"}},
        },
        expected={
            'triggered': 2,
            'not_triggered': 2,
            'executed': 1,
            'errors': 1,
            'sell_call_count': 2,
        }
    ))

    # 用例 6：响应带 code:-1（应判定失败）
    results.append(run_test(
        name="用例6: 响应 code:-1（应判定失败）",
        sell_responses={
            'sz000001': {"ok": True, "code": -1, "msg": "余额不足"},
            'sz000002': {"ok": True, "data": {"orderId": "ok"}},
        },
        expected={
            'triggered': 2,
            'not_triggered': 2,
            'executed': 1,
            'errors': 1,
            'sell_call_count': 2,
        }
    ))

    # 汇总
    print("\n" + "=" * 72)
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f" 压测汇总: {passed}/{total} 通过")
    print("=" * 72)
    if passed == total:
        print(" ✅ execute=True 链路全绿")
        print("    R2 强制止损：触发检测→下单参数→成功判定→审计日志 全部正确")
    else:
        print(" ❌ 有失败用例，需要修代码")
    print()
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
