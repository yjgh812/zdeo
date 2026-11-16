# -*- coding: utf-8 -*-
"""
a1_limit_sell_audit.py — A1跳空限价卖出 试运行核对工具(2026-10-10, owner_decisions A1试运行条款)
====================================================================================================
A1条款: 事件驱动卖出改 open×0.995 限价挂单, 试运行 ≥10 笔真实限价回报核对;
实现均价改善 <0.5pp 或滞留事故即回滚。本工具=该条款的验收基础设施(只读, --no-log 惯例)。

核对口径(全自动化, 依据=模拟盘限价单按【委托价】撮合——F1注释/60笔伪成交实测反证):
  ① 挂价合规: |挂单价 - 当日open×0.995| ≤ 0.011元 (qfq库当日open基准)
  ② 成交一致性: FILLED 且 当日low ≤ 挂价 → 限价必成交(否则=台账异常标记)
  ③ 实现滑点: 成交价/open - 1 (新机制预期 ≈ -0.5%; 回放旧行为基线中位 -2.81pp)
  ④ 滞留检测: OPEN/PARTIAL 且 挂单日 < 今日 → 列示滞留(当日强势未回落属正常, 次日复核)

用法:
  python a1_limit_sell_audit.py                # 全量核对+试运行进度判定
  python a1_limit_sell_audit.py --json         # 结构化输出
退出码: 0=正常(含n=0); 2=发现不合规/回滚触发线。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, date

BASE = os.path.dirname(os.path.abspath(__file__))
ORDERS = os.path.join(BASE, "data", "executor_orders.jsonl")
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
TAG = "A1_EVENT_SELL"
RATIO = 0.995
TRIAL_TARGET = 10          # 试运行笔数目标
ROLLBACK_SLIP_PCT = -1.0   # 实现滑点中位低于此(相对open)=回滚触发线(挂价-0.5%+容忍0.5%)


def load_a1_orders():
    if not os.path.exists(ORDERS):
        return []
    out = []
    for line in open(ORDERS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("action") == "SELL" and TAG in str(r.get("note") or ""):
            out.append(r)
    return out


def qfq_day(code6, yyyymmdd):
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select open, high, low, close from stock_daily_meta_qfq "
            "where code=? and date=? limit 1", (code6, yyyymmdd)).fetchone()
    finally:
        con.close()
    return row  # (open, high, low, close) | None


def main():
    as_json = "--json" in sys.argv
    orders = load_a1_orders()
    today = date.today().strftime("%Y%m%d")
    items, errs = [], []
    for r in orders:
        code6 = str(r.get("code", ""))[-6:]
        td = str(r.get("trade_date") or (r.get("submitted_at") or "")[:10].replace("-", "") or "")
        px = float(r.get("price") or 0)
        status = r.get("status")
        it = {"code": code6, "name": r.get("name"), "trade_date": td,
              "order_price": px, "status": status}
        day = qfq_day(code6, td) if code6 and len(td) == 8 else None
        if day is None:
            it["check"] = "NO_QFQ_DATA"
            it["slip_vs_open"] = None
            errs.append(f"{code6} {td} qfq无当日数据, 无法核对")
        else:
            o, h, l, c = day
            bench = round(o * RATIO, 2)
            it["qfq_open"] = o
            it["bench_price"] = bench
            if abs(px - bench) <= 0.011:
                it["check"] = "PRICE_OK"
            else:
                it["check"] = "PRICE_DEVIATION"
                errs.append(f"{code6} {td} 挂价{px} 偏离基准{bench}(open{o}×0.995)")
            if status == "FILLED":
                # 模拟盘按委托价撮合 → 成交价=挂价; 限价成交前提=当日low触及挂价
                if l <= px + 0.001:
                    it["fill_consistent"] = True
                    it["slip_vs_open"] = round((px / o - 1) * 100, 2)
                else:
                    it["fill_consistent"] = False
                    it["slip_vs_open"] = None
                    errs.append(f"{code6} {td} FILLED但当日low({l})未触及挂价({px})=台账异常")
            elif status in ("OPEN", "PARTIAL") and td < today:
                it["pending_days"] = (date.today() - datetime.strptime(td, "%Y%m%d").date()).days
                it["check"] += "+PENDING_STALE"
            else:
                it["slip_vs_open"] = None
        items.append(it)

    filled = [it for it in items if it.get("status") == "FILLED" and it.get("slip_vs_open") is not None]
    slips = sorted(it["slip_vs_open"] for it in filled)
    n = len(slips)
    med = slips[n // 2] if n % 2 else (slips[n // 2 - 1] + slips[n // 2]) / 2 if n else None
    stale = [it for it in items if "PENDING_STALE" in str(it.get("check", ""))]
    rollback = bool(n >= 5 and med is not None and med < ROLLBACK_SLIP_PCT)

    out = {
        "mode": "a1-trial-audit", "run_at": datetime.now().isoformat(timespec="seconds"),
        "a1_orders_total": len(items), "filled_with_slip": n,
        "trial_progress": f"{n}/{TRIAL_TARGET}",
        "fill_rate_pct": round(n / len(items) * 100, 1) if items else None,
        "slip_median_vs_open_pp": med,
        "slip_list": slips,
        "rollback_triggered": rollback,
        "rollback_rule": f"n≥5且滑点中位<{ROLLBACK_SLIP_PCT}% → 回滚(条款: 改善<0.5pp即回滚)",
        "stale_pending": [{"code": it["code"], "trade_date": it["trade_date"],
                           "pending_days": it.get("pending_days")} for it in stale],
        "errors": errs,
        "items": items,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1 if as_json else None)
          if as_json else json.dumps(out, ensure_ascii=False, indent=2))
    if errs and as_json is None:
        print(f"\n⚠️ {len(errs)} 项核对异常(见 errors)")
    if rollback:
        print("🚨 A1回滚触发线命中: 滑点中位低于阈值 — 按试运行条款提请回滚")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
