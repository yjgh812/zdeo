# -*- coding: utf-8 -*-
"""fill_requote_executor 真值校验(fail-closed)单元测试 · 2026-10-27

缺陷背景: 2026-10-26 盘中实证——本地 open_orders 滞后于 westock 真值
(39 笔提交本地全 OPEN, 其中 6 笔真值已成交/已撤单), 旧逻辑直接 cancel+重挂
= 对已成交单重复买入。修复=行动前拉 westock 订单真值做终态过滤。

四用例(全部 mock, 零网络零真实台账写入):
  T1 真值已成交/已撤单残影 → SKIP_LIVE, 绝不调用 cancel/trade
  T2 真值无引用(NO_LIVE_REF) → SKIP_LIVE 不动
  T3 真值待成交+价格上脱 → 正常 REQ 路径不受修复影响
  T4 真值拉取失败 → main 返回 3 拒跑(fail-closed)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, r"D:/zcodesj")
import fill_requote_executor as fr  # noqa: E402

DATE = "20990101"   # 虚构日期, 报告产物写临时目录不污染真实 reports/


def _row(oid, code, price, qty, ref):
    return {"code": code, "name": f"测试{code}", "action": "BUY", "price": price,
            "qty": qty, "confirmed": 0, "status": "OPEN", "order_id": oid,
            "trade_date": DATE, "ref_price": ref, "requoted": False, "note": ""}


def _setup(monkey_rows, live_map, quotes=None, live_raises=False, calls=None):
    """打桩: 台账/真值/行情/落盘 全部 mock, 记录 RPC 动作调用。

    注意: main() 内部运行时 `from executor_order_ledger import load_open_rows`
    → 台账桩必须打在 executor_order_ledger 模块上(打 fr.load_open_rows 无效)。
    """
    import executor_order_ledger as eol
    fr.REPORT_DIR = tempfile.mkdtemp()
    fr.DATA_DIR = tempfile.mkdtemp()

    def fake_load_open_rows(statuses):
        return [dict(r) for r in monkey_rows]

    def fake_live(rpc, date6):
        if live_raises:
            raise RuntimeError("模拟真值拉取失败")
        return live_map

    def fake_quote(rpc, codes):
        return quotes or {}

    class FakeRPC:
        def call(self, name, args):
            if calls is not None:
                calls.append((name, json.dumps(args, ensure_ascii=False)))
            if name == "portfolio_paper_cancel":
                return {"ok": True, "data": {"success": True}}
            if name == "portfolio_paper_trade":
                return {"ok": True, "data": {"orderId": "TEST_NEW"}}
            raise RuntimeError(f"意外RPC {name}")

    eol.load_open_rows = fake_load_open_rows
    fr.live_order_map = fake_live
    fr.quote_map = fake_quote
    fr.WsRPC = lambda: FakeRPC()


def _run():
    old = sys.argv
    sys.argv = ["fill_requote_executor.py", "--date", DATE]
    try:
        rc = fr.main()
    finally:
        sys.argv = old
    return rc


def main() -> int:
    fails = []

    # T1: 真值终态残影 → SKIP_LIVE, 零 cancel/trade 调用
    calls = []
    _setup(
        monkey_rows=[_row("OID_F", "sz300017", 15.01, 100, 15.08),
                     _row("OID_C", "sh603636", 8.63, 500, 8.67)],
        live_map={"OID_F": "已成交", "OID_C": "系统撤单"},
        quotes={"300017": 15.29, "603636": 8.77},
        calls=calls)
    rc = _run()
    out = json.load(open(os.path.join(fr.DATA_DIR, f"fill_requote_exec_{DATE}.json"),
                         encoding="utf-8"))
    st = {d["code"]: d["status"] for d in out["rows"]}
    if rc != 0 or st.get("sz300017") != "SKIP_LIVE" or st.get("sh603636") != "SKIP_LIVE":
        fails.append(f"T1 终态过滤失败: rc={rc} rows={st}")
    if calls:
        fails.append(f"T1 不应有任何RPC动作却调用: {calls}")

    # T2: 真值无引用 → SKIP_LIVE 不动
    calls2 = []
    _setup(monkey_rows=[_row("OID_X", "sh600000", 9.0, 100, 9.05)],
           live_map={}, quotes={"600000": 9.2}, calls=calls2)
    rc = _run()
    out2 = json.load(open(os.path.join(fr.DATA_DIR, f"fill_requote_exec_{DATE}.json"),
                          encoding="utf-8"))
    if rc != 0 or out2["rows"][0]["status"] != "SKIP_LIVE" or out2["rows"][0]["live"] != "NO_LIVE_REF":
        fails.append(f"T2 无引用跳过失败: rc={rc} row={out2['rows'][0]}")
    if calls2:
        fails.append(f"T2 不应有任何RPC动作却调用: {calls2}")

    # T3: 真值待成交 + 现价高于挂单价低于计划价 → REQ 正常路径(dry-run 只读)
    calls3 = []
    _setup(monkey_rows=[_row("OID_L", "sz300017", 15.01, 100, 15.30)],
           live_map={"OID_L": "待成交"}, quotes={"300017": 15.20}, calls=calls3)
    rc = _run()
    out3 = json.load(open(os.path.join(fr.DATA_DIR, f"fill_requote_exec_{DATE}.json"),
                          encoding="utf-8"))
    r3 = out3["rows"][0]
    if rc != 0 or r3["status"] != "REQ" or r3.get("new_price") != 15.23 or calls3:
        fails.append(f"T3 REQ正路失败: rc={rc} row={r3} calls={calls3}")

    # T4: 真值拉取失败 → exit 3 拒跑
    _setup(monkey_rows=[_row("OID_E", "sh600000", 9.0, 100, 9.05)],
           live_map={}, live_raises=True)
    rc = _run()
    if rc != 3:
        fails.append(f"T4 fail-closed拒跑失败: rc={rc}(期望3)")

    if fails:
        print("❌ FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("✅ fill_requote 真值校验 4/4 ALL PASS (T1终态过滤/T2无引用不动/T3REQ正路/T4拉取失败拒跑)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
