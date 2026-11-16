# -*- coding: utf-8 -*-
"""一次性台账修复（2026-09-10 晚，知微自主续跑）——open_orders 已成交行回填 FILLED。

背景：9/10 应用未运行窗口致 14:30 链缺席（其第五步B fill_confirm **poll 未跑**），而
22:00 链成交回报兜底只做了 trade_records 归属判定（reclassify，22:02:31 写 4 行 async），
未回填 open_orders 的 confirmed/status → 随后 `--expire` 把这 4 笔**实际已成交**的委托
标成 EXPIRED_READ（pending_review_20260910.md 因此把已持仓买单列为"未成交候选"，
纪律看板"挂单成交率"失真）。

证据（westock 唯一真值源 portfolio_paper_history 2026-09-10，order_id 逐笔精确匹配）：
  100015682609100947414221252710  山西焦煤 BUY  1700 @7.00  已成交 09:47:57
  100015682609100947543717886861  中远海控 BUY  1500 @16.45 已成交 09:48:00
  100015682609100948243720298715  冀中能源 BUY  5300 @4.70  已成交 09:48:33
  100015682609100943163719492711  熙菱信息 SELL  300 @22.48 已成交 09:43:22

纪律：只改 open_orders 状态（confirmed=qty, status=FILLED），**不写 trade_records**
（成交记录已由 reclassify 在案；重复写=双记，上海银行 9/7 幻影成交同族教训）。
幂等：已是 FILLED 的行跳过；qty 不匹配即 fail-closed 中止。备份：
data/open_orders.jsonl.bak_20260910_2318（修复前 26,747B）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, r"D:\zcodesj")
import fill_confirm_monitor as fcm  # noqa: E402  (唯一 owner：load/save_open_rows)

TARGETS = {
    "100015682609100947414221252710": ("山西焦煤", 1700),
    "100015682609100947543717886861": ("中远海控", 1500),
    "100015682609100948243720298715": ("冀中能源", 5300),
    "100015682609100943163719492711": ("熙菱信息", 300),
}
NOTE = ("9/10 14:30 poll 缺席, 22:00 兜底仅写 trade_records 未回填状态; "
        "经 westock portfolio_paper_history order_id 逐笔核对为已成交 → 回填 FILLED "
        "(一次性修复脚本 data/_open_orders_fill_repair_20260910.py)")


def main() -> int:
    all_rows = fcm.load_open_rows(None)
    hit, skipped = 0, 0
    for r in all_rows:
        oid = r.get("order_id")
        if oid not in TARGETS:
            continue
        name, qty = TARGETS[oid]
        if r.get("name") != name or int(r.get("qty") or 0) != qty:
            print(f"[FAIL-CLOSED] {oid} 台账内容与证据不符: name={r.get('name')} qty={r.get('qty')} "
                  f"(期望 {name} {qty}) → 中止，不修改任何行")
            return 2
        if r.get("status") == "FILLED":
            skipped += 1
            continue
        r["confirmed"] = qty
        r["status"] = "FILLED"
        r.pop("qty_left_for_review", None)
        r["fill_confirm_note"] = NOTE
        hit += 1
        print(f"[FIXED] {name} {oid} → FILLED confirmed={qty}")
    if hit:
        fcm.save_open_rows(all_rows)
    print(f"summary: fixed={hit} already={skipped} (total rows={len(all_rows)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
