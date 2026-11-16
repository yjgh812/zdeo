#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账户级回撤守卫（2026-08-21 交付，模拟盘改进方案v2 风控第二层 / round6 M4项）。

规则：账户净值自峰值回撤 ≥ 20% → 触发告警，要求总仓位降至半仓并停止新开仓。
用法：由盘前/盘中 cron 传入当日净值（来自 westock MCP 实时接口，知微侧传入）：
    python account_drawdown_guard.py --net-value 772000
峰值状态持久化于 reports/account_peak.json（幂等；回撤修复后自动更新峰值）。
退出码：0=正常 / 2=触发20%回撤告警 / 3=参数错误或首次无基线。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

BASE = r"D:\zcodesj"
PEAK_FILE = os.path.join(BASE, "reports", "account_peak.json")
DEFAULT_THRESHOLD = 0.20


def load_peak() -> dict:
    if not os.path.exists(PEAK_FILE):
        return {}
    try:
        with open(PEAK_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_peak(peak: float, net: float, date_str: str) -> None:
    with open(PEAK_FILE, "w", encoding="utf-8") as f:
        json.dump({"peak": round(peak, 2), "updated": date_str,
                   "last_net_value": round(net, 2)}, f, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser(description="账户级20%回撤守卫")
    ap.add_argument("--net-value", type=float, required=True, help="账户当前总资产（westock MCP实时）")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--account-name", default="模拟盘", help="账户标识（日志用）")
    a = ap.parse_args()

    date_str = datetime.now().strftime("%Y%m%d")
    peak_state = load_peak()
    peak = peak_state.get("peak")

    if not peak:
        save_peak(a.net_value, a.net_value, date_str)
        print(f"[首次] {a.account_name} 净值 {a.net_value:,.0f} 记为基线峰值（无回撤判定）")
        return 0

    if a.net_value > peak:
        save_peak(a.net_value, a.net_value, date_str)
        print(f"[更新峰值] {a.account_name} 净值 {a.net_value:,.0f} > 旧峰值 {peak:,.0f}，刷新基线")
        return 0

    drawdown = (peak - a.net_value) / peak
    if drawdown >= a.threshold:
        print(
            f"🔴 [回撤告警] {a.account_name} 净值 {a.net_value:,.0f}，"
            f"峰值 {peak:,.0f}，回撤 {drawdown*100:.1f}% ≥ {a.threshold*100:.0f}% "
            f"→ 总仓位须降至半仓，停止新开仓（round6 M4 账户级风控）")
        return 2

    print(f"[正常] {a.account_name} 净值 {a.net_value:,.0f}，回撤 {drawdown*100:.1f}% < {a.threshold*100:.0f}%（峰值 {peak:,.0f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
