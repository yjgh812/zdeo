#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持仓风控卡（屠龙标准 v1.0 第8章止盈止损落地，2026-08-13 建）。

从 westock 实时拉取持仓，逐只计算止损线/阶梯止盈线/浮盈档位/破位提示，
生成每日盘中风控依据。满仓+新仓浮亏时，这是 14:30/14:45 执行的唯一标准。

屠龙标准 v1.0 止盈止损（唯一标准，裁决6套矛盾）：
  止损（硬底线）：固定 -8%（成本×0.92，无例外）
  移动止损：max(成本, 持有期最高收盘)×0.92，盘中破线且14:40未涨回即卖
  阶梯止盈：浮盈<10%不止盈(仅止损) / 10-30%峰值回撤10% / >30%峰值回撤6%
  T+1新仓：当天不止损，延至次日开盘第一笔

用法：
    python position_risk_card.py            # 全持仓风控卡
    python position_risk_card.py --json     # JSON 输出
    python position_risk_card.py --alert    # 只显示触发预警的持仓
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, r"D:/workbuddyspace")
from westock_client import WeStockClient  # noqa: E402

FIXED_STOP = 0.08        # 固定止损 -8% (卖出三铁律不变)
TRAIL_10_30 = 0.10       # 浮盈10-30%档：峰值回撤10%
TRAIL_30 = 0.06          # 浮盈>30%档：峰值回撤6%
# 高位移动止盈提示（R-20260828-01 配套, 2026-08-28 落地；提示性，不动-8%铁律）
HIGH_CHG20 = 0.30        # 20日涨幅≥30% = 高位（科森科技603626 +64%/国民技术300077 +29% 依据）
HIGH_DD = 0.10           # 自20日高点回撤≥10% → 触发提示
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def _norm6(code: str) -> str:
    c = str(code or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[2:]
            break
    return "".join(ch for ch in c if ch.isdigit()).zfill(6)[-6:]


def _qfq_high_stats(code: str) -> dict | None:
    """读 qfq（系统价格唯一真源）最近 21 个交易日收盘：20日涨幅 + 20日高点。

    fail-open：库/数据异常 → None（调用侧静默跳过该提示，不阻断风控卡）。
    注：盘中运行 qfq 最新=上一交易日收盘，属 T-1 口径（提示性够用）。"""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        rows = [float(r[0]) for r in con.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 21",
            (_norm6(code),)).fetchall() if r[0]]
        con.close()
        if len(rows) < 21 or rows[0] <= 0 or rows[20] <= 0:
            return None
        return {"chg20": rows[0] / rows[20] - 1, "hi20": max(rows)}
    except Exception:
        return None


def build_card(ws: WeStockClient) -> list[dict]:
    # 2026-08-24 P1修复(fail-closed): 此前取数失败(token失效/接口异常)得到空列表
    # → 输出「持仓0只全正常」卡片(伪健康)。失败直接抛结构化错误，由 main 转 exit 3。
    resp = ws.positions()
    if not resp.get("ok"):
        raise RuntimeError(f"westock持仓取数失败(ok=false)——禁止以0持仓冒充全正常: {str(resp)[:200]}")
    positions = (resp.get("data") or {}).get("positions", [])
    cards = []
    for p in positions:
        qty = int(p.get("quantity", 0))
        if qty <= 0:
            continue
        code = p.get("code", "")
        name = p.get("name", "")
        cost = float(p.get("costPrice", 0))
        cur = float(p.get("currentPrice", 0))
        if cost <= 0 or cur <= 0:
            continue
        pnl_pct = cur / cost - 1
        stop_line = round(cost * (1 - FIXED_STOP), 3)

        # 阶梯止盈档（峰值≈成本与现价的较高者近似，精确峰值需历史，这里用 cost/cur 估算触发逻辑）
        if pnl_pct < 0.10:
            tp_rule = "浮盈<10%：不止盈，仅看-8%止损"
            tp_line = None
        elif pnl_pct < 0.30:
            tp_line = round(cur * (1 - TRAIL_10_30), 3)  # 近似：以现价为峰值参考
            tp_rule = "浮盈10-30%：峰值回撤10%止盈"
        else:
            tp_line = round(cur * (1 - TRAIL_30), 3)
            tp_rule = "浮盈>30%：峰值回撤6%止盈"

        # 预警判定
        alerts = []
        if pnl_pct <= -FIXED_STOP:
            alerts.append("🔴触发-8%硬止损")
        elif pnl_pct <= -0.06:
            alerts.append("🟠接近止损线(≤-6%)")
        elif pnl_pct <= -0.04:
            alerts.append("🟡浮亏>-4%关注")

        # 高位移动止盈提示（R-20260828-01 配套, 2026-08-28；提示性不动铁律）
        hs = _qfq_high_stats(code)
        high_tag = ""
        if hs and hs["chg20"] >= HIGH_CHG20:
            dd = cur / hs["hi20"] - 1 if hs["hi20"] > 0 else 0.0
            if dd <= -HIGH_DD:
                alerts.append(f"🔶高位移动止盈(20日+{hs['chg20']*100:.0f}%, 自高点回撤{dd*100:.0f}%)")
            else:
                high_tag = f"高位(+{hs['chg20']*100:.0f}%)"

        cards.append({
            "code": code, "name": name, "qty": qty, "cost": cost, "current": cur,
            "pnl_pct": round(pnl_pct * 100, 2),
            "stop_line": stop_line,
            "take_profit_rule": tp_rule, "take_profit_line": tp_line,
            "high_position_tag": high_tag,
            "alert": " ".join(alerts) if alerts else "🟢正常",
        })
    # 按浮盈升序（最亏的在前，优先关注）
    cards.sort(key=lambda x: x["pnl_pct"])
    return cards


def main() -> int:
    ap = argparse.ArgumentParser(description="持仓风控卡")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--alert", action="store_true", help="只显示触发预警的持仓")
    a = ap.parse_args()

    ws = WeStockClient()
    try:
        cards = build_card(ws)
    except RuntimeError as e:
        # 2026-08-24 P1修复配套: 取数失败禁止输出「0只全正常」，exit 3 结构化失败
        print(f"[FAIL] {e}")
        if a.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 3
    if a.alert:
        cards = [c for c in cards if c["alert"] != "🟢正常"]

    if a.json:
        print(json.dumps(cards, ensure_ascii=False, indent=2))
        return 0

    print("=" * 90)
    print(f"持仓风控卡（{len(cards)}只）  止损线=成本×0.92  阶梯止盈=10-30%回撤10%/>30%回撤6%")
    print("=" * 90)
    hdr = f'{"名称":<8}{"代码":<10}{"成本":>8}{"现价":>8}{"浮盈%":>8}{"止损线":>8}{"止盈线":>8}  状态'
    print(hdr)
    print("-" * 90)
    for c in cards:
        tp = f"{c['take_profit_line']}" if c["take_profit_line"] else "-"
        status = c["alert"]
        if c.get("high_position_tag"):
            status = f"{status} [{c['high_position_tag']}]"
        print(f'{c["name"]:<8}{c["code"]:<10}{c["cost"]:>8}{c["current"]:>8}'
              f'{c["pnl_pct"]:>8}{c["stop_line"]:>8}{tp:>8}  {status}')
    print("-" * 90)
    n_danger = sum(1 for c in cards if "🔴" in c["alert"])
    n_warn = sum(1 for c in cards if "🟠" in c["alert"] or "🟡" in c["alert"])
    print(f"预警: 🔴止损{n_danger}只 / 🟠🟡关注{n_warn}只 / 🟢正常{len(cards)-n_danger-n_warn}只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
