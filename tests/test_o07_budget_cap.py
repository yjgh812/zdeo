#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_o07_budget_cap.py — O07 金额硬闸回归（2026-09-03, A6 误挂三单收口验证）

四态断言（stub run_gate/westock, dry-run 路径, 零真实下单零落库）:
  A. --amount 50000 + --price 10 + --budget 20000 → 折算5000股, 预算硬顶截断至 3000 股
     （2026-10-31 对齐 10/26 主人令: 显式 amount+price 折算路径下 O07 仍生效）
  B. --amount 5000（行情价可用）→ 整手折算绑定500股；无可用价格时触发「无显式股数拒绝」守卫
  C. 无 volume/amount/budget → 结构化拒绝（10/26 主人令废除隐式默认 sizing）
  D. --volume 5000 + --budget 20000 → 仍被预算硬顶截断至 3000 股
运行: python tests/test_o07_budget_cap.py （--no-log 语义: 本测试不触 guard_precheck_log）
"""
import argparse
import sys

sys.path.insert(0, r"D:\zcodesj")
import trade_executor as te  # noqa: E402


class StubWS:
    def positions(self):
        return {"data": {"positions": [{"code": "sz000983", "quantity": 1600}]}}

    def portfolio(self):
        return {"data": {"portfolio": {"totalAssets": 960712.3}}}

    def quote(self, code):
        return {"data": {code: {"price": 10.0}}}


def mk_args(**kw):
    base = dict(code="000983", name="山西焦煤", source="manual_confirmed", sector="",
                price=10.0, volume=None, amount=None, budget=None, execute=False,
                prev_close=None, pct_60d=None, amplitude_40d=None, sector_breadth=None,
                intent="", allow_add_position=True, limit_price=None,
                # 2026-11-05: ZONE-GATE上线后用例票不在位置池会先拦O07断言——本套件只测
                # 预算帽层, 携带位置池特批豁免(与 owner_override_circuit 同先例)
                owner_approve_zone=True,
                # 2026-11-07: PAPER-OBS-GATE登记上线后manual_confirmed源先拦O07断言——
                # 同样只测预算帽层, 携带paper-obs特批豁免
                owner_approve_paper_obs=True)
    base.update(kw)
    return argparse.Namespace(**base)


def main() -> int:
    # 桩: 闸门恒 PASS + sizing 默认 19200 股(=960712×20%/10); 关闭归因查询避免 DB 依赖
    te.run_gate = lambda a, h, c, q, ws=None: {
        "verdict": "PASS", "rejects": [], "warnings": [],
        "sizing": {"shares": 19200, "budget": 192142.46}}
    te.query_signal_quality = None
    ws = StubWS()
    fails = []

    r = te.do_buy(mk_args(amount=50000, budget=20000), ws)
    ("3000股" in r.get("reason", "")) or fails.append(f"A amount+price折算截断: {r.get('reason')}")
    (r.get("vol_capped_by_budget", {}).get("capped_vol") == 3000) or fails.append(
        f"A capped_vol字段: {r.get('vol_capped_by_budget')}")

    # B = amount+行情价折算回归（2026-10-31 对齐 10/26 新合同: 有可用价格时 amount 单独合法,
    #     整手向下折算=vol_locked_by_amount; 无可用价格才触发「无显式股数拒绝」守卫）
    r = te.do_buy(mk_args(amount=5000), ws)
    ("500股" in r.get("reason", "")) or fails.append(f"B amount绑定: {r.get('reason')}")
    (r.get("vol_locked_by_amount", {}).get("vol") == 500) or fails.append(
        f"B locked字段: {r.get('vol_locked_by_amount')}")

    # C = 10/26 主人令守卫回归: 无 volume/amount/budget → 结构化拒绝（隐式默认 sizing 已废除）
    r = te.do_buy(mk_args(), ws)
    ("无显式股数拒绝" in r.get("reason", "")) or fails.append(f"C 无股数参数拒绝: {r.get('reason')}")

    r = te.do_buy(mk_args(volume=5000, budget=20000), ws)
    ("3000股" in r.get("reason", "")) or fails.append(f"D volume+budget截断: {r.get('reason')}")

    if fails:
        for f in fails:
            print(f"  ❌ {f}")
        print("O07 回归 FAIL")
        return 1
    print("O07 金额硬闸回归 4/4 PASS（amount+price折算截断/amount绑定/无股数拒绝/volume+budget截断）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
