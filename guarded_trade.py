#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交易门禁 wrapper（屠龙验证体系，2026-08-13 建）。

目的：把"过闸门 → 下单 → 记录 → 入验证闭环"固化为不可绕过的两步 SOP，
确保每一笔模拟盘交易：① 强制过 pre_trade_check_v2.py；② 强制带信号源标记；
③ 成交后自动进入 verification_trades / r8_forward_tracking 前瞻跟踪。

主人 8/13 指示：需要大量交易/跟踪数据验证屠龙系统。本脚本是数据积累的入口闸门。

SOP（知微在会话中执行，下单动作用 westock MCP 完成）：
  第1步 买入前检查（不下单，只判定+给仓位建议）：
    python guarded_trade.py precheck --code 002414 --name 高德红外 \
        --price 14.09 --prev-close 13.99 --source mainline_radar \
        --holdings 19 --total-cash 1019909 --amount 50000 [--sector 军工] \
        [--pct-60d 0.45] [--close 14.09 --high 14.2 --low 13.9] [--amplitude-40d 0.32]
    → 输出 JSON: verdict=PASS/REJECT + 建议股数 + 止损价 + 闸门明细
    → REJECT 则终止，禁止下单

  第2步 成交后记录（知微用 MCP 下单成交后调用）：
    python guarded_trade.py postrecord --code 002414 --name 高德红外 \
        --price 14.09 --volume 3600 --source mainline_radar --sector 军工 --direction buy
    → 自动: trade_state.record_buy + trade_records 落库(信号源入subsystems)
           + feed_feedback sync 进 verification_trades/r8_forward_tracking

  卖出记录（卖出经 pre_sell_check 后成交）：
    python guarded_trade.py postrecord --code 600726 --name 华电能源 \
        --price 6.80 --volume 8300 --direction sell --reason 趋势走坏

信号源白名单（写入 trade_records.subsystems，供验证报表分组）：
  bp_ep_core / turnover_mvp / mainline_radar / pattern_match / manual_confirmed / 实验源
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, r"D:/zcodesj")
from trade_state import record_buy, record_sell, load_state  # noqa: E402

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
PRE_TRADE = r"D:\zcodesj\pre_trade_check_v2.py"
TOP20_DIR = r"D:\zcodesj\multifactor\reports\daily_top20"


def bp_ep_membership(code6: str) -> str:
    """双账本名单判定：读最新 bp+ep Top20 csv，返回 bp_ep_top20 / outside_list。"""
    import glob
    files = sorted(glob.glob(os.path.join(TOP20_DIR, "bp_ep_monthly_*.csv")))
    if not files:
        return "unknown"
    with open(files[-1], encoding="utf-8-sig") as f:
        f.readline()
        top = {ln.split(",")[0].strip().zfill(6) for ln in f if ln.split(",")[0].strip().isdigit()}
    return "bp_ep_top20" if code6 in top else "outside_list"

VALID_SOURCES = {
    "bp_ep_core", "turnover_mvp", "mainline_radar", "pattern_match", "manual_confirmed",
    "early_board", "early_board_r1", "dragon_r3",
}


def run_precheck(a) -> dict:
    """调用 pre_trade_check_v2 做全量闸门判定。"""
    cmd = [sys.executable, PRE_TRADE,
           "--code", a.code, "--name", a.name or "", "--source", a.source]
    for flag, val in [
        ("--price", a.price), ("--prev-close", a.prev_close), ("--amount", a.amount),
        ("--pct-60d", a.pct_60d), ("--holdings", a.holdings), ("--total-cash", a.total_cash),
        ("--close", a.close), ("--high", a.high), ("--low", a.low),
        ("--amplitude-40d", a.amplitude_40d), ("--sector-breadth", a.sector_breadth),
    ]:
        if val is not None:
            cmd += [flag, str(val)]
    if a.is_st:
        cmd.append("--is-st")
    if a.limit_up_buy:
        cmd.append("--limit-up-buy")
    if a.late_stage:
        cmd.append("--late-stage")
    if a.intent:
        cmd += ["--intent", a.intent]
    if a.allow_add_position:
        cmd.append("--allow-add-position")

    # 2026-09-22: 子进程PYTHONUTF8=1(GBK坑族, 宏桥S-5实弹事故同源)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONUTF8="1"))
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result = {"verdict": "REJECT", "rejects": [f"闸门脚本异常: {proc.stderr[:200]}"]}
    result["_exit_code"] = proc.returncode
    result["_state"] = load_state()
    return result


def run_postrecord(a) -> dict:
    """成交后落库：trade_state + trade_records + 反馈闭环 sync。"""
    code = a.code.strip()
    now = datetime.now().isoformat(timespec="seconds")
    today = datetime.now().strftime("%Y%m%d")
    direction = a.direction.lower()

    # 1) 当日状态
    if direction == "buy":
        state = record_buy(code)
    else:
        state = record_sell(code)

    # 2) trade_records 落库（信号源写 subsystems，signal_type 受 CHECK 约束写 manual；
    #    双账本 list_membership 自动判定：bp_ep_top20 / outside_list）
    conn = sqlite3.connect(ATTR_DB)
    # 2026-08-24 P2修复: 空值不再伪造 manual_confirmed(主人逐笔特批语义), 落 unknown 并告警
    src = a.source if a.source in VALID_SOURCES else (a.source or "unknown")
    if src == "unknown":
        print("  [warn] 买入 source 为空或不在 VALID_SOURCES, 落库 unknown(非 manual_confirmed)", file=sys.stderr)
    amount = round((a.price or 0) * (a.volume or 0), 2)
    membership = bp_ep_membership(code)
    if direction == "buy":
        # 2026-09-23 T8(幂等查重): 去重键补volume对齐五元组口径(同码同日同价不同量=两笔合法委托)
        ex = conn.execute(
            "SELECT COUNT(*) FROM trade_records WHERE code=? AND trade_date=? AND price=? AND volume=? AND action='BUY'",
            (code, today, a.price, a.volume),
        ).fetchone()[0]
        if ex:
            print(f"  [skip] trade_records duplicate skipped: {code} {today} "
                  f"BUY {a.price} x{a.volume}(幂等查重T8)")
        if ex == 0:
            conn.execute(
                "INSERT INTO trade_records (code, name, trade_date, action, price, volume, amount, "
                "strategy_version, signal_type, subsystems, market_env, sector, notes, list_membership, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, a.name or "", today, "BUY", a.price, a.volume, amount,
                 today, "manual", src, "", a.sector or "", a.notes or "guarded_trade录入",
                 membership, now),
            )
    else:
        # 2026-09-23 T8(幂等查重): 五元组(code,trade_date,action,price,volume)精确匹配, 命中跳过
        ex_s = conn.execute(
            "SELECT COUNT(*) FROM trade_records WHERE code=? AND trade_date=? AND action=? AND price=? AND volume=?",
            (code, today, "SELL", a.price, a.volume),
        ).fetchone()[0]
        if ex_s:
            print(f"  [skip] trade_records duplicate skipped: {code} {today} "
                  f"SELL {a.price} x{a.volume}(幂等查重T8)")
        else:
            conn.execute(
                "INSERT INTO trade_records (code, name, trade_date, action, price, volume, amount, "
                "strategy_version, signal_type, subsystems, market_env, sector, notes, list_membership, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, a.name or "", today, "SELL", a.price, a.volume, amount,
                 today, "manual", src, "", a.sector or "", f"卖出:{a.reason or ''} {a.notes or ''}".strip(),
                 membership, now),
            )
    conn.commit()
    conn.close()

    # 3) 反馈闭环 sync（灌入 verification_trades / r8_forward_tracking）
    sync_proc = subprocess.run(
        [sys.executable, r"D:\zcodesj\feed_feedback_tables.py", "sync"],
        capture_output=True, text=True, encoding="utf-8",
        env=dict(os.environ, PYTHONUTF8="1"),
    )
    sync_out = {}
    try:
        sync_out = json.loads(sync_proc.stdout)
    except Exception:
        sync_out = {"raw": sync_proc.stdout[:200], "err": sync_proc.stderr[:200]}

    return {
        "recorded": f"{direction} {code} {a.name or ''} @{a.price} x{a.volume}",
        "signal_source": src,
        "today_state": {"buy_count": state.get("buy_count"), "bought": state.get("bought_codes")},
        "feedback_sync": sync_out,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="屠龙交易门禁 wrapper（过闸门+记录+入闭环）")
    sub = p.add_subparsers(dest="mode", required=True)

    for sp in (sub.add_parser("precheck"), sub.add_parser("postrecord")):
        sp.add_argument("--code", required=True)
        sp.add_argument("--name", default="")
        sp.add_argument("--price", type=float)
        sp.add_argument("--source", default="manual_confirmed")
        sp.add_argument("--sector", default="")
        sp.add_argument("--notes", default="")
    # precheck 专属
    pre = sub.choices["precheck"]
    pre.add_argument("--prev-close", type=float)
    pre.add_argument("--amount", type=float)
    pre.add_argument("--pct-60d", type=float)
    pre.add_argument("--holdings", type=int)
    pre.add_argument("--total-cash", type=float)
    pre.add_argument("--close", type=float)
    pre.add_argument("--high", type=float)
    pre.add_argument("--low", type=float)
    pre.add_argument("--amplitude-40d", type=float)
    pre.add_argument("--sector-breadth", type=float)
    pre.add_argument("--intent", default="")
    pre.add_argument("--is-st", action="store_true")
    pre.add_argument("--limit-up-buy", action="store_true")
    pre.add_argument("--late-stage", action="store_true")
    pre.add_argument("--allow-add-position", action="store_true")
    # postrecord 专属
    post = sub.choices["postrecord"]
    post.add_argument("--volume", type=int)
    post.add_argument("--direction", choices=["buy", "sell"], required=True)
    post.add_argument("--reason", default="")

    a = p.parse_args()
    if a.mode == "precheck":
        out = run_precheck(a)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("verdict") != "REJECT" else 2
    out = run_postrecord(a)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
