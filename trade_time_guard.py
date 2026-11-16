#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易时间窗口硬约束 v1.0（8/13全维度自省产出S-2/S-4）

========== 核心设计 ==========
本模块是屠龙系统所有交易的"时间总闸"，任何交易下单前必须过此闸。
它提供三个函数（分别对应S-2/S-4/S-5），以及一个全量检查函数。

========== 时间窗口规则（P0硬化） ==========
[买入窗口] 10:00-11:00 / 13:30-14:30
  - 09:30-10:00 → ✅ PASS（可以买卖，闸门R1-R18进行质量拦截）
  - 11:00-13:00 → 🟡 SOFT_REJECT（午休时段，非标准交易窗口）
  - 14:30-15:00 → 🟡 仅限尾盘低吸（慎入，T+1无对冲空间）

[卖出窗口] 14:45-14:55（S-2：尾盘卖出物理锁）
  - 之外任何时间 → 🟡 仅限-8%止损紧急单（须主人确认豁免）

========== 使用场景 ==========
1. 任何买入路径（paper_bridge/v375_satellite/手动）在 pre_trade_check_v2 之前调用
2. 任何卖出路径在 pre_sell_check 之前调用
3. 14:30 cron 自动止损扫描调用 check_buy_window_emergency
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time

# ===== 窗口定义 =====
BUY_WINDOW_START = time(10, 0)     # 10:00 开始允许买入
BUY_WINDOW_END = time(14, 30)      # 14:30 关闭买入（尾盘除外）
BUY_INTRADAY_START = time(10, 0)   # 10:00
BUY_INTRADAY_END = time(11, 0)     # 11:00 第一段
BUY_AFTERNOON_START = time(13, 30) # 13:30 第二段
BUY_AFTERNOON_END = time(14, 30)   # 14:30

SELL_WINDOW_START = time(14, 45)   # 14:45 开始允许卖出
SELL_WINDOW_END = time(14, 55)     # 14:55 关闭卖出窗口

FIRST_HOUR_END = time(10, 0)       # 09:30-10:00 = 开盘第一小时
MORNING_CLOSE = time(11, 30)       # 11:30 上午收盘
AFTERNOON_START = time(13, 0)      # 13:00 下午开盘

# ===== 例外判定阈值 =====
EMERGENCY_STOP_LOSS_PCT = -0.08    # -8% 止损可豁免卖出窗口限制 (卖出三铁律不变)


def now() -> datetime:
    """获取当前时间（可 mock 覆写测试用）。"""
    return datetime.now()


def check_buy_window(stock_name: str = "") -> dict:
    """
    买入时间窗口检查（主人8/14裁决：全天09:30-15:00任何时间均可买卖）。

    返回:
        verdict: PASS / SOFT_REJECT / HARD_REJECT
        reason: 理由字符串
        current_time: "HH:MM:SS"
    """
    t = now()
    current = t.time()
    current_str = t.strftime("%H:%M:%S")

    # 15:00 之后 → 硬拒绝（收盘后禁止下单，下一交易日再操作）
    if current > time(15, 0):
        return {
            "action": "buy",
            "verdict": "HARD_REJECT",
            "reason": (
                f"收盘后: 当前 {current_str} 已过15:00收盘。"
                f"下一交易日才能操作。"
            ),
            "current_time": current_str,
        }

    # 开盘前 → 硬拒绝（尚未开盘）
    if current < time(9, 30):
        return {
            "action": "buy",
            "verdict": "HARD_REJECT",
            "reason": (
                f"未开盘: 当前 {current_str} 早于09:30开盘。"
                f"开盘后才能操作。"
            ),
            "current_time": current_str,
        }

    # 11:30-13:00 → 软拒绝（午休，市场物理关闭）
    if MORNING_CLOSE <= current < AFTERNOON_START:
        return {
            "action": "buy",
            "verdict": "SOFT_REJECT",
            "reason": (
                f"午休时段: 当前 {current_str} 在11:30-13:00午休区间（市场关闭）。"
                f"13:00恢复交易后再操作。"
            ),
            "current_time": current_str,
        }

    # 其余全部交易时段（09:30-11:30 / 13:00-15:00）→ PASS
    # 主人8/14裁决：全天任何时间均可买卖，无禁买时段（S-4已废除）
    return {
        "action": "buy",
        "verdict": "PASS",
        "reason": (
            f"当前 {current_str} 在交易时段内，允许操作"
            f"（主人8/14裁决全天可交易，质量拦截由闸门R1-R18负责）。"
        ),
        "current_time": current_str,
    }


def check_sell_window(
    loss_pct: float | None = None,
    owner_confirmed_emergency: bool = False,
) -> dict:
    """
    卖出时间窗口检查（S-2：尾盘卖出物理锁）。
    
    Args:
        loss_pct: 当前亏损百分比（负值），如 -0.095 表示亏9.5%
        owner_confirmed_emergency: 主人是否已确认紧急卖出
        
    返回:
        verdict: PASS / SOFT_REJECT / PASS
        reason: 理由
        current_time: "HH:MM:SS"
        is_stop_loss_emergency: bool
    """
    t = now()
    current = t.time()
    current_str = t.strftime("%H:%M:%S")
    
    # 检查是否为 -8% 止损紧急单
    is_emergency = (
        loss_pct is not None
        and loss_pct <= EMERGENCY_STOP_LOSS_PCT
        and owner_confirmed_emergency
    )
    
    # 14:45-14:55 → 通过（尾盘标准窗口）
    if SELL_WINDOW_START <= current < SELL_WINDOW_END:
        return {
            "action": "sell",
            "verdict": "PASS",
            "reason": (
                f"当前 {current_str} 在尾盘卖出窗口(14:45-14:55)内，允许操作。"
            ),
            "current_time": current_str,
            "is_stop_loss_emergency": False,
        }
    
    # -8%止损紧急单 → 可通过（须主人确认）
    if is_emergency:
        return {
            "action": "sell",
            "verdict": "PASS",
            "reason": (
                f"⚠️ 止损紧急单豁免: 当前亏损 {loss_pct*100:.1f}% 已超 -8% 止损线，"
                f"经主人确认后允许在 {current_str}（非尾盘窗口）执行。"
            ),
            "current_time": current_str,
            "is_stop_loss_emergency": True,
        }
    
    # -8%止损但主人未确认 → 软拒绝（建议主人确认使用紧急通道）
    if loss_pct is not None and loss_pct <= EMERGENCY_STOP_LOSS_PCT:
        return {
            "action": "sell",
            "verdict": "SOFT_REJECT",
            "reason": (
                f"当前亏损 {loss_pct*100:.1f}% 已超 -8% 止损线，但主人未确认紧急卖出。"
                f"请主人确认是否使用紧急止损通道。否则等待14:45-14:55尾盘窗口。"
            ),
            "current_time": current_str,
            "is_stop_loss_emergency": False,
        }
    
    # 15:00后 → 硬拒绝（收盘后禁止下单，下一交易日再操作）
    if current >= time(15, 0):
        return {
            "action": "sell",
            "verdict": "HARD_REJECT",
            "reason": f"收盘后: 当前 {current_str} 已过15:00收盘，下一交易日才能操作。",
            "current_time": current_str,
            "is_stop_loss_emergency": False,
        }
    
    # 其他所有时段 → PASS（主人 8/14 裁决：不限制卖出时段，任何时间可买卖）
    return {
        "action": "sell",
        "verdict": "PASS",
        "reason": (
            f"当前 {current_str} 不在尾盘窗口内，但主人 8/14 裁决不限制卖出时段，允许操作。"
            f"（原 S-2 尾盘卖出物理锁已按主人裁决解除）"
        ),
        "current_time": current_str,
        "is_stop_loss_emergency": False,
    }


def check_trade(
    action: str,
    stock_name: str = "",
    loss_pct: float | None = None,
    owner_confirmed_emergency: bool = False,
) -> dict:
    """
    交易时间窗口全量检查（buy/sell 统一入口）。
    
    用法:
        result = check_trade("buy", "中恒电气")
        if result["verdict"] == "PASS":
            print(f"禁止买入: {result['reason']}")
            return
    
        result = check_trade("sell", "陇神戎发", loss_pct=-0.129)
        if result["verdict"] == "PASS":
            print(f"禁止卖出: {result['reason']}")
            return
    """
    if action == "sell":
        return check_sell_window(
            loss_pct=loss_pct,
            owner_confirmed_emergency=owner_confirmed_emergency,
        )
    else:
        return check_buy_window(stock_name=stock_name)


def cli_check_buy() -> int:
    """CLI入口：买入时间窗口检查"""
    r = check_buy_window()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 2 if r["verdict"] == "PASS" else (1 if r["verdict"] == "SOFT_REJECT" else 0)


def cli_check_sell() -> int:
    """CLI入口：卖出时间窗口检查"""
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--loss-pct", type=float, help="当前亏损百分比(负值)，如 -0.095 表示亏9.5%")
    p.add_argument("--emergency", action="store_true", help="主人确认紧急止损通道")
    a = p.parse_args()
    # 单位防御：契约小数，|值|>1 视为误传百分数（与 pre_sell_check 同型防御）
    if a.loss_pct is not None and abs(a.loss_pct) > 1:
        print(f"[warn] --loss-pct={a.loss_pct} 超出小数域，按百分数自动归一为 "
              f"{a.loss_pct / 100}（契约: -0.095 表示亏9.5%）", file=sys.stderr)
        a.loss_pct = a.loss_pct / 100
    r = check_sell_window(loss_pct=a.loss_pct, owner_confirmed_emergency=a.emergency)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 2 if r["verdict"] == "PASS" else (1 if r["verdict"] == "SOFT_REJECT" else 0)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="交易时间窗口硬约束 v1.0")
    p.add_argument("action", choices=["check-buy", "check-sell"], help="检查类型")
    a, remaining = p.parse_known_args()
    sys.argv = [sys.argv[0]] + remaining
    if a.action == "check-buy":
        raise SystemExit(cli_check_buy())
    else:
        raise SystemExit(cli_check_sell())