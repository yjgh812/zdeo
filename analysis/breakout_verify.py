# -*- coding: utf-8 -*-
"""突破主线/A-B实验·对账追踪件(prereg PREREG_BREAKOUT_MAINLINE_20261026 + PREREG_BREAKOUT_AB_FILTER_20261027) — 只读零交易权。
逐臂输出: 计划批次席位 vs executor台账当日提交(BUY, source匹配) → 已提交/未提交;
累计追踪: 每臂历史BUY提交数/已成交数(主人框架强制项=累计笔数+当前功效);
三态判决带: 当前n对照 breakout_power_latest.json 表C边界(PASS/FAIL/INDETERMINATE)。
成交真值=westock MCP(会话层 fill_confirm_monitor --reconcile 对账), 本件只读台账文件不冒充真值。
用法: python analysis/breakout_verify.py [--date YYYYMMDD]
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")

BASE = r"D:\zcodesj"
ARMS = [("B", "breakout_mainline", os.path.join(BASE, "data", "batch_exec_breakout.json")),
        ("A", "breakout_ab_A", os.path.join(BASE, "data", "batch_exec_breakout_a.json"))]
LEDGER = os.path.join(BASE, "data", "executor_orders.jsonl")
POWER = os.path.join(BASE, "data", "breakout_power_latest.json")


def main():
    date = datetime.now().strftime("%Y%m%d")
    if "--date" in sys.argv:
        date = sys.argv[sys.argv.index("--date") + 1]
    subs = {"B": [], "A": []}
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("action") != "BUY" or str(d.get("trade_date")) != date:
                    continue
                for tag, src, _ in ARMS:
                    if d.get("source") == src:
                        subs[tag].append(d)
    # 去重: 每order_id取最后状态
    for tag in subs:
        latest = {}
        for d in subs[tag]:
            latest[d["order_id"]] = d
        subs[tag] = list(latest.values())

    power = json.load(open(POWER, encoding="utf-8")) if os.path.exists(POWER) else {}
    p0 = (power.get("baseline") or {}).get("win_rate")
    table_c = power.get("table_c_three_outcome") or {}

    print(f"突破主线A/B对账追踪 | 交易日={date} | regime基线p0={p0}")
    summary = {"date": date, "arms": {}}
    for tag, src, batch_f in ARMS:
        plan = []
        if os.path.exists(batch_f):
            plan = json.load(open(batch_f, encoding="utf-8"))
        plan_codes = [e["code"] for e in plan]
        sub_codes = [str(d.get("code"))[-6:] for d in subs[tag]]
        filled = [str(d.get("code"))[-6:] for d in subs[tag] if "成交" in str(d.get("status", "")) and "未" not in str(d.get("status", ""))]
        missing = [c for c in plan_codes if c not in sub_codes]
        cum_n = len({d["order_id"] for d in subs[tag]})
        cum_all = 0
        if os.path.exists(LEDGER):
            seen = set()
            with open(LEDGER, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    oid = d.get("order_id")
                    if d.get("action") == "BUY" and d.get("source") == src and oid not in seen:
                        seen.add(oid)
                        cum_all += 1
        band = "n=0 无判决带"
        if p0 is not None and cum_all > 0:
            key = str(cum_all)
            if key in table_c:
                c = table_c[key]
                band = (f"PASS需胜≥{c['pass_min_wins']}/{cum_all}" if c.get("pass_min_wins") is not None else "PASS不可达")
                band += f" | FAIL需胜≤{c['fail_max_wins']}/{cum_all}" if c.get("fail_max_wins") is not None else " | FAIL不可达"
                band += " | 其余=INDETERMINATE"
            else:
                band = f"n={cum_all} 无预注册边界行(最近邻={min(table_c, key=lambda k: abs(int(k)-cum_all))})"
        print(f"\n[{tag}臂] source={src}")
        print(f"  计划席位: {plan_codes or '无批次文件'}")
        print(f"  当日提交: {len(subs[tag])}笔 {sub_codes or '—'}")
        print(f"  计划未提交: {missing or '—'}")
        print(f"  台账标记已成交: {filled or '—'}(真值以westock对账为准)")
        print(f"  累计提交笔数: {cum_all} | 当前判决带: {band}")
        summary["arms"][tag] = {"planned": plan_codes, "submitted": sub_codes,
                                "filled_ledger": filled, "cum_submitted": cum_all, "band": band}
    summary["regime_now"] = json.load(open(os.path.join(BASE, "data", "market_regime_latest.json"),
                                           encoding="utf-8")).get("regime", "N/A") if os.path.exists(
        os.path.join(BASE, "data", "market_regime_latest.json")) else "N/A"
    print(f"\n当前regime: {summary['regime_now']} (判决分层依据)")
    out = os.path.join(BASE, "data", "breakout_verify_latest.json")
    json.dump(summary, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"已写 {os.path.relpath(out, BASE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
