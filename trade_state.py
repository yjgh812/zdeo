#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""当日交易状态跟踪器（屠龙标准 v1.0 R3/R8/重复买入 的状态载体）。

问题背景：pre_trade_check.py 是无状态校验器，无法判定"同日已买几只""该标的是否
已下过单"。2026-08-13 高德红外(sz002414) 9:34/9:36 双单 7200 股重复买入事件，
根因即无状态跟踪。本模块提供当日状态文件，供闸门校验前读写。

状态文件：D:\\zcodesj\\trade_state\\trade_state_YYYYMMDD.json
结构：
{
  "date": "20260813",
  "buy_count": 3,                       # 当日已成交买入只数（R3：上限3，分化日2）
  "bought_codes": ["002414", "600726"], # 当日已买标的（重复买入检查）
  "sell_count": 2,
  "pending_orders": {"002414": ["orderId1"]},  # 在途单（重报必先撤检查）
  "sector_breadth": 0.35,               # 当日板块宽度（R8 分化日判定，<0.20为分化日）
  "market_env": "EXTREME_STRUCTURE",     # 市场环境标签（R18 极端结构行情判定）
  "highest_board_today": 5,              # 今日最高连板数（R17 BOARD_COLLAPSE_ALERT）
  "highest_board_yesterday": 7,          # 昨日最高连板数（R17 判定退潮）
  "space_leader_limit_down": true,       # 空间龙今日是否跌停（R14 禁追连板）
  "limit_up_count": 62,                  # 今日涨停总数（R18 极端结构行情参数）
  "advance_ratio": 0.20                  # 今日上涨家数占比（R18 极端结构行情参数）
}

用法：
    # 查询当日状态（不存在则初始化空状态）
    python trade_state.py --show

    # 记录一笔买入成交
    python trade_state.py --record-buy 002414

    # 记录一笔卖出成交
    python trade_state.py --record-sell 600726

    # 登记/清除在途单
    python trade_state.py --add-pending 002414 orderId123
    python trade_state.py --clear-pending 002414

    # 设置当日板块宽度（盘前/盘中由主线雷达或宽度计算脚本写入）
    python trade_state.py --set-breadth 0.35

    # 输出 JSON 供其他脚本消费
    python trade_state.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

STATE_DIR = r"D:\zcodesj\trade_state"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def state_path(date: str | None = None) -> str:
    d = date or _today()
    return os.path.join(STATE_DIR, f"trade_state_{d}.json")


def load_state(date: str | None = None) -> dict:
    """加载当日状态；不存在则返回初始化空状态（不落盘，由首次写入时落盘）。"""
    p = state_path(date)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "date": date or _today(),
        "buy_count": 0,
        "bought_codes": [],
        "sell_count": 0,
        "pending_orders": {},
        "sector_breadth": None,
        "market_env": None,
        "highest_board_today": None,
        "highest_board_yesterday": None,
        "space_leader_limit_down": False,
        "limit_up_count": None,
        "advance_ratio": None,
    }


def save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(state["date"]), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def norm_code(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6) if c.isdigit() else c


def record_buy(code: str) -> dict:
    s = load_state()
    c = norm_code(code)
    if c not in s["bought_codes"]:
        s["bought_codes"].append(c)
    s["buy_count"] = len(s["bought_codes"])  # 按标的数计，同标的加仓不重复计数
    save_state(s)
    return s


def record_sell(code: str) -> dict:
    s = load_state()
    s["sell_count"] = int(s.get("sell_count", 0)) + 1
    save_state(s)
    return s


def add_pending(code: str, order_id: str) -> dict:
    s = load_state()
    c = norm_code(code)
    s.setdefault("pending_orders", {}).setdefault(c, [])
    if order_id not in s["pending_orders"][c]:
        s["pending_orders"][c].append(order_id)
    save_state(s)
    return s


def clear_pending(code: str) -> dict:
    s = load_state()
    c = norm_code(code)
    s.setdefault("pending_orders", {}).pop(c, None)
    save_state(s)
    return s


def set_breadth(breadth: float) -> dict:
    s = load_state()
    s["sector_breadth"] = breadth
    save_state(s)
    return s


def set_market_env(
    env: str | None = None,
    highest_today: int | None = None,
    highest_yesterday: int | None = None,
    space_leader_down: bool | None = None,
    limit_up_count: int | None = None,
    advance_ratio: float | None = None,
) -> dict:
    """盘前写入市场环境参数（R14/R17/R18 状态源）。

    可部分更新——只传需要变更的字段，其余保持不变。
    典型用法（盘前由复盘脚本/cron写入）::

        # 写入昨日最高7板→今日预估5板 + 空间龙跌停
        set_market_env(highest_yesterday=7, highest_today=5, space_leader_down=True)
        # 写入极端结构行情
        set_market_env(env="EXTREME_STRUCTURE", limit_up_count=62, advance_ratio=0.20)
    """
    s = load_state()
    if env is not None:
        s["market_env"] = env
    if highest_today is not None:
        s["highest_board_today"] = highest_today
    if highest_yesterday is not None:
        s["highest_board_yesterday"] = highest_yesterday
    if space_leader_down is not None:
        s["space_leader_limit_down"] = space_leader_down
    if limit_up_count is not None:
        s["limit_up_count"] = limit_up_count
    if advance_ratio is not None:
        s["advance_ratio"] = advance_ratio
    save_state(s)
    return s


def main() -> int:
    p = argparse.ArgumentParser(description="屠龙当日交易状态跟踪器")
    p.add_argument("--show", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--record-buy", metavar="CODE")
    p.add_argument("--record-sell", metavar="CODE")
    p.add_argument("--add-pending", nargs=2, metavar=("CODE", "ORDER_ID"))
    p.add_argument("--clear-pending", metavar="CODE")
    p.add_argument("--set-breadth", type=float)
    p.add_argument("--set-market-env", nargs="*", help="设置市场环境: env TAG / highest-today N / highest-yesterday N / space-leader-down / limit-up N / advance-ratio 0.X")
    p.add_argument("--date", default=None, help="指定日期 YYYYMMDD（默认今日）")
    a = p.parse_args()

    if a.record_buy:
        s = record_buy(a.record_buy)
    elif a.record_sell:
        s = record_sell(a.record_sell)
    elif a.add_pending:
        s = add_pending(a.add_pending[0], a.add_pending[1])
    elif a.clear_pending:
        s = clear_pending(a.clear_pending)
    elif a.set_breadth is not None:
        s = set_breadth(a.set_breadth)
    elif a.set_market_env is not None:
        kw = {}
        tokens = a.set_market_env
        i = 0
        while i < len(tokens):
            key = tokens[i]
            if key == "space-leader-down":
                kw["space_leader_down"] = True
                i += 1
            elif i + 1 < len(tokens):
                val = tokens[i + 1]
                if key == "env":
                    kw["env"] = val
                elif key == "highest-today":
                    kw["highest_today"] = int(val)
                elif key == "highest-yesterday":
                    kw["highest_yesterday"] = int(val)
                elif key == "limit-up":
                    kw["limit_up_count"] = int(val)
                elif key == "advance-ratio":
                    kw["advance_ratio"] = float(val)
                i += 2
            else:
                i += 1
        s = set_market_env(**kw)
    else:
        s = load_state(a.date)

    if a.show and not a.json:
        print(f"日期: {s['date']}")
        print(f"当日已买 {s['buy_count']} 只: {s['bought_codes']}")
        print(f"当日已卖 {s['sell_count']} 笔")
        print(f"在途单: {s.get('pending_orders', {})}")
        breadth = s.get("sector_breadth")
        print(f"板块宽度: {breadth} ({'分化日' if breadth is not None and breadth < 0.20 else '正常' if breadth is not None else '未设置'})")
    else:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
