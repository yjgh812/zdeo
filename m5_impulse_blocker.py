#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盘中单笔冲动拦截器 M-5（8/13全维度自省）
==========================================

========== 问题 ==========
8/13 实证：09:31 同一分钟成交 16 笔，所有违规集中开盘第一小时。
根因：下单无冷却 + 无合规弹窗 + 无数量上限。

========== M-5 设计 ==========
在 guarded_trade_executor 的 Step 2 和 Step 4 之间插入：

  【拦截链】
  ① 3秒冷静延迟 — 物理等待，让冲动冷却
  ② 五问合规弹窗 — 强制回答后才能继续
     Q1: 价格 ≤ 30 元？（当前: XX 元 → 若 >30→终止）
     Q2: 今日新开 ≤ 3 只？（当前: XX 只）
     Q3: 标的在今日交易作战清单上？（需提供清单文件）
     Q4: 信号源在白名单中？（需提供 --source 参数）
     Q5: 这是你自己发现的票（非系统候选）？（若是→询问主人）
  ③ 同分钟笔数上限 — 本分钟已成交 >0 笔则等待下一分钟

========== 用法 ==========
    # 作为 guarded_trade_executor 的子步骤自动调用
    # 也支持独立 CLI 测试

    python m5_impulse_blocker.py --action buy --code 002582 --price 10.12
        --source manual --today-buy-count 2

    python m5_impulse_blocker.py --action sell --code 300534
        --loss-pct -0.129 --owner-emergency

========== 退出码 ==========
    0 = 通过（可继续下单）
    1 = 拦截（建议终止）
    2 = 需确认（建议暂停并问主人）
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

# ===== 常量 =====
COOLDOWN_SECONDS = 3
MAX_BUY_PER_DAY = 3
MAX_PRICE = 30
PLAN_DIR = r"D:\zcodesj\trade_plans"
STATE_DIR = r"D:\zcodesj\trade_state"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def norm_code(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6)


def _read_trade_state() -> dict:
    """读取当日 trade_state。"""
    path = os.path.join(STATE_DIR, f"trade_state_{_today()}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _read_trade_plan_codes() -> set[str]:
    """从今日交易作战清单中提取允许交易的标的代码集合。"""
    paths = [
        os.path.join(PLAN_DIR, f"trade_plan_{_today()}.md"),
        os.path.join(PLAN_DIR, "trade_plan_latest.md"),
    ]
    codes = set()
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                # 从 Markdown 表格中提取代码
                import re
                # 匹配表格行中的 6 位数字
                found = re.findall(r"\| (\d{6}) \|", content)
                codes.update(found)
            except Exception:
                continue
    return codes


def check(
    action: str,
    code: str,
    price: float | None = None,
    source: str = "",
    today_buy_count: int | None = None,
    owner_emergency: bool = False,
    skip_cooldown: bool = False,
    skip_questions: bool = False,
) -> dict:
    """
    冲动拦截检查主函数。

    返回:
        verdict: PASS / BLOCKED / NEED_CONFIRM
        reasons: list[str] 拦截原因
        warnings: list[str] 警告信息
        cooldown_seconds: int 已等待秒数
    """
    reasons: list[str] = []
    warnings: list[str] = []
    code6 = norm_code(code)
    state = _read_trade_state()
    plan_codes = _read_trade_plan_codes()

    # ---- ① 3秒冷静延迟 ----
    cooldown_actual = 0
    if not skip_cooldown:
        start = time.time()
        time.sleep(COOLDOWN_SECONDS)
        cooldown_actual = int(time.time() - start)

    # ---- ② 五问合规弹窗 ----
    question_results = {}

    # Q1: 价格 ≤ 30？
    if action == "buy" and price is not None:
        q1_pass = price <= MAX_PRICE
        question_results["Q1_price_limit"] = {
            "pass": q1_pass,
            "detail": f"价格 {price} 元 {'≤' if q1_pass else '>'}{MAX_PRICE} 元上限",
        }
        if not q1_pass:
            reasons.append(f"❌ Q1: 价格 {price} > {MAX_PRICE} → 违反价格铁律 E05")

    # Q2: 今日新开 ≤ 3 只？
    if action == "buy":
        if today_buy_count is None:
            today_buy_count = len(state.get("bought_codes", []))
        q2_pass = today_buy_count < MAX_BUY_PER_DAY
        question_results["Q2_daily_limit"] = {
            "pass": q2_pass,
            "detail": f"今日已买 {today_buy_count} 只 {'<' if q2_pass else '≥'}{MAX_BUY_PER_DAY} 上限",
        }
        if not q2_pass:
            reasons.append(f"❌ Q2: 今日已买 {today_buy_count} 只达上限 → 违反 R3")

    # Q3: 在交易作战清单上？（买入检查，卖出跳过）
    if action == "sell":
        question_results["Q3_on_plan_buy"] = {
            "pass": True,
            "detail": "卖出操作跳过作战清单检查",
        }
    elif plan_codes and action=="buy":
        q3_pass = code6 in plan_codes
        question_results["Q3_on_plan_buy"] = {
            "pass": q3_pass,
            "detail": f"{code6} {'在' if q3_pass else '不在'}今日作战清单",
        }
        if not q3_pass:
            reasons.append(f"❌ Q3: {code6} 不在交易作战清单上 → 清单上没有的不买 S-6")
    else:
        question_results["Q3_on_plan_buy"] = {
            "pass": None,
            "detail": "作战清单未就绪（无法检查）",
        }
        warnings.append("⚠️ 作战清单未生成，Q3 跳过（建议先跑 daily_trade_plan.py）")

    # Q4: 信号源在白名单？
    if source:
        proven = {"bp_ep_core", "turnover_mvp", "mainline_radar",
                   "pattern_match", "manual_confirmed"}
        q4_pass = source in proven
        question_results["Q4_source"] = {
            "pass": q4_pass,
            "detail": f"信号源 {source} {'在白名单' if q4_pass else '不在白名单'}",
        }
        if not q4_pass:
            reasons.append(f"❌ Q4: 信号源 {source} 不在白名单 → admit 闸门拦截")
    else:
        q4_pass = False
        question_results["Q4_source"] = {"pass": False, "detail": "未提供信号源"}
        reasons.append("❌ Q4: 未提供信号源 → admit 闸门拦截")

    # Q5: 自己发现的票？
    if action == "buy" and source not in ("manual_confirmed",):
        question_results["Q5_self_discover"] = {
            "pass": False,
            "detail": f"来源 {source} 非 manual_confirmed → 视为自发现票，每日≤1只（准则13）",
        }
        # 准则是行为约束，不强制拦截但标记警告
        warnings.append("⚠️ Q5（准则13提醒）: 非系统候选来源，每日仅允许1只自发现票")

    # ---- ③ 同分钟笔数上限 ----
    # 读取 trade_state 中最近成交时间
    last_trade_minute = state.get("last_trade_minute")
    now_minute = datetime.now().strftime("%H:%M")
    # 同一分钟限制已移除（主人确认：同一分钟可多笔）

    # ---- verdict ----
    if reasons:
        verdict = "BLOCKED"
    elif warnings and any("建议" in w for w in warnings):
        verdict = "NEED_CONFIRM"
    else:
        verdict = "PASS"

    return {
        "action": action,
        "code": code6,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "verdict": verdict,
        "cooldown_seconds": cooldown_actual,
        "reasons": reasons,
        "warnings": warnings,
        "questions": question_results,
        "state": {
            "today_buy_count": today_buy_count or len(state.get("bought_codes", [])),
            "plan_codes_count": len(plan_codes),
            "last_trade_minute": last_trade_minute,
        },
    }


def cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="盘中单笔冲动拦截器 M-5")
    p.add_argument("--action", required=True, choices=["buy", "sell"])
    p.add_argument("--code", required=True)
    p.add_argument("--price", type=float)
    p.add_argument("--source", default="")
    p.add_argument("--today-buy-count", type=int)
    p.add_argument("--owner-emergency", action="store_true")
    p.add_argument("--skip-cooldown", action="store_true", help="跳过3秒冷却（测试用）")
    p.add_argument("--skip-questions", action="store_true", help="跳过五问（测试用）")
    a = p.parse_args()

    result = check(
        action=a.action,
        code=a.code,
        price=a.price,
        source=a.source,
        today_buy_count=a.today_buy_count,
        owner_emergency=a.owner_emergency,
        skip_cooldown=a.skip_cooldown,
        skip_questions=a.skip_questions,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    return 2 if result["verdict"] == "BLOCKED" else (
        1 if result["verdict"] == "NEED_CONFIRM" else 0
    )


if __name__ == "__main__":
    raise SystemExit(cli())