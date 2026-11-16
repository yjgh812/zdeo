#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话直单守卫包装器（2026-09-23 立项）。

背景: 9/23 核验发现当日 19 笔成交全部由交互会话直接调 westock MCP 落单,
pre_trade_check_v2/pre_sell_check 守卫零触发(guard_precheck_log 仅剩雷达源 6 条,
对照 9/21 有 46 条)。因 westock 下单只能由会话 MCP 完成, 本脚本负责"两端留痕":

  1) --precheck  下单前守卫预检: import 复用守卫判定函数(不改其源码),
     guard_precheck_log 落库出口走守卫自带的 log_precheck/_log_presell(保持口径不变)。
     REJECT/HOLD → 会话方不得下单。
  2) --record   成交回报落账: 对齐 trade_executor.py 的 trade_records INSERT 口径
     (14 列 + T8 五元组查重), 写 D:/zcodesj/attribution/attribution.db;
     落账成功后同步写 engine_attribution_log 简归因(event=trade_executed, 幂等)。
  3) 无参数     打印用法。

用法:
    python guarded_paper_trade.py --precheck --code sh600519 --direction buy \
        --price 14.09 --quantity 1000 [--name 贵州茅台 --source manual_confirmed \
        --prev-close 13.99 --sector 食品饮料 --total-cash 1019909 --allow-add-position]

    python guarded_paper_trade.py --record --code sh600519 --direction buy \
        --price 14.09 --quantity 1000 --trade-date 20260923 --fill-time "09:35:12" \
        --source owner_order [--name 贵州茅台 --sector 食品饮料 --reason "备注"]

    python guarded_paper_trade.py                # 打印本用法

退出码: precheck buy  0=通过 / 2=REJECT(不得下单)
        precheck sell 0=可卖  / 1=HOLD(建议保留, 须主人确认)
        record 0=落账或查重跳过 / 1=参数或写入失败
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from argparse import Namespace
from datetime import datetime

if "D:/zcodesj" not in sys.path:
    sys.path.insert(0, "D:/zcodesj")

# ===== 唯一真库路径(根目录同名 attribution.db 为残缺壳, 禁用) =====
RECORD_DB = "D:/zcodesj/attribution/attribution.db"

# 信号源白名单(会话直单合法来源; 与手册口径一致)
_RECORD_SOURCES = ("owner_order", "s5", "auth_package")
# trade_executor._SIGNAL_TYPES 同款映射: 白名单外一律归 manual
_SIGNAL_TYPES = ("probability_threshold", "top_n_ranking", "manual")

# trade_records INSERT 列口径(逐字对齐 trade_executor.py 的 do_buy/do_sell)
_RECORD_COLUMNS = (
    "code", "name", "trade_date", "action", "price", "volume", "amount",
    "strategy_version", "signal_type", "subsystems", "market_env", "sector",
    "notes", "created_at",
)


def _norm_code(code: str) -> str:
    """与 trade_executor.norm 同款: sh/sz/bj 前缀原样(小写), 裸码按板块补前缀。"""
    c = (code or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return c
    num = c.zfill(6)
    if num.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + num
    if num.startswith(("8", "920", "430")):
        return "bj" + num
    return "sz" + num


def _norm_signal_type(src: str) -> str:
    """与 trade_executor._norm_signal_type 同款: 白名单外归 manual。"""
    return src if src in _SIGNAL_TYPES else "manual"


# =====================================================================
# 模式 1: 守卫预检(复用守卫判定函数, 不改守卫源码)
# =====================================================================
def _buy_namespace(a: argparse.Namespace) -> Namespace:
    """构造 pre_trade_check_v2.check(args) 所需 Namespace, 默认值与其 argparse 对齐。

    2026-09-23 接线口径: 会话直单 source 默认 manual_confirmed;
    未显式给 --amount 时用 price*quantity 推导(台账金额一致性; R10 已随
    2026-10-26 主人令废除仓位限定退役, 此处推导仅为信息完整)。
    """
    return Namespace(
        code=a.code, name=(a.name or ""), price=a.price,
        prev_close=a.prev_close, pct_60d=None, holdings=a.holdings,
        total_cash=a.total_cash, source=(a.source or "manual_confirmed"),
        owner_approve_rs5=False, owner_approve_r31=False, owner_approve_r6=False,
        owner_approve_r30a=False, owner_approve_ddgate=False,
        amount=(a.amount if a.amount is not None
                else (round(a.price * a.quantity, 2)
                      if (a.price is not None and a.quantity) else None)),
        close=None, high=None, low=None, amplitude_40d=None,
        sector_breadth=None,
        space_leader_limit_down=False, consecutive_boards=None,
        seal_break_count=None, turnover_rate=None, highest_board_yesterday=None,
        highest_board_today=None, market_env="",
        sh_index_pct=None, cyb_index_pct=None, limit_up_count=None,
        advance_ratio=None, sector_limit_up_count=None,
        sector=(a.sector or ""), is_st=bool(a.is_st), intent="",
        limit_up_buy=False, late_stage=False,
        allow_add_position=bool(a.allow_add_position),
        skip_position_check=False, skip_whitelist=False,
        skip_time_window=False, no_log=bool(a.no_log),
    )


def _sell_namespace(a: argparse.Namespace) -> Namespace:
    """构造 pre_sell_check.check(args) 所需 Namespace, 默认值与其 argparse 对齐。"""
    return Namespace(
        code=a.code, name=(a.name or ""), owner_override_hold=False,
        pe=a.pe, pb=a.pb, dividend_yield=a.dividend_yield,
        earnings_yoy=a.earnings_yoy, earnings_turnaround=False,
        net_inflow_5d=a.net_inflow_5d, loss_pct=a.loss_pct,
        trend_ma5=None, trend_ma20=None, price=a.price, ytd=None,
        owner_emergency=False,
        stop_loss=bool(a.stop_loss),
        halt_resume=False, post_halt_boards=None, post_halt_turnover=None,
        no_auto_risk=False, sector_pct=a.sector_pct,
        hold_days=None, horizon=None, boards=None, chg_20d=None,
        market_pct=None, no_log=bool(a.no_log),
    )


def run_precheck(a: argparse.Namespace) -> tuple[str, dict, int]:
    """执行守卫预检并走守卫自带落库出口。

    返回 (wrapper_verdict: "PASS"|"REJECT", guard原始dict, exit_code)。
    buy  → pre_trade_check_v2.check + log_precheck(REJECT=exit 2);
    sell → pre_sell_check.check + _log_presell(HOLD=建议保留=REJECT, exit 1)。
    --no-log 时守卫自身跳过 guard_precheck_log(守卫原生测试旗标)。
    """
    if a.direction == "buy":
        import pre_trade_check_v2 as guard
        ns = _buy_namespace(a)
        try:
            r = guard.check(ns)
        except Exception as e:  # 与守卫 main() 同款兜底: 崩溃=fail-closed REJECT
            import traceback
            r = {"code": a.code, "name": a.name or "", "board": "",
                 "source": (a.source or ""), "tag": "", "verdict": "REJECT",
                 "rejects": [f"闸门异常(包装器兜底): {type(e).__name__}: {e}"],
                 "warnings": [],
                 "objections": [f"崩溃尾迹: {traceback.format_exc()[-280:]}"],
                 "gate_exception": True}
        guard.log_precheck(ns, r)  # 守卫自带落库出口, 口径不变
        verdict = "PASS" if r["verdict"] in ("PASS", "PASS_WITH_WARN") else "REJECT"
        return verdict, r, (0 if verdict == "PASS" else 2)
    # sell
    import pre_sell_check as guard
    ns = _sell_namespace(a)
    try:
        r = guard.check(ns)
    except Exception as e:
        import traceback
        r = {"code": a.code, "name": a.name or "", "verdict": "HOLD",
             "hold_reasons": [f"守卫异常(包装器兜底): {type(e).__name__}: {e}"],
             "overridden_holds": [], "risk_hints": [], "risk_flags": None,
             "info": {"gate_exception": True}, "time_window": None,
             "checks_run": {}}
    guard._log_presell(ns, r)  # 守卫自带落库出口(source='pre_sell'), 口径不变
    verdict = "PASS" if r["verdict"] == "SELL_OK" else "REJECT"
    return verdict, r, (0 if verdict == "PASS" else 1)


def _print_precheck(a: argparse.Namespace, verdict: str, r: dict, code: int) -> int:
    print("=" * 62)
    print(f"屠龙守卫预检 | {_norm_code(a.code)} | {a.direction} | "
          f"价={a.price} 量={a.quantity} | 判定: {verdict}")
    print("=" * 62)
    if verdict == "REJECT":
        print("结论: REJECT → 会话方不得下单" + (
            "(卖出为建议保留, 须主人显式确认后方可执行)"
            if a.direction == "sell" else ""))
        for x in (r.get("rejects") or r.get("hold_reasons") or []):
            print(f"  ✗ {x}")
    else:
        print("结论: PASS → 允许执行(留意 warnings)")
    for w in (r.get("warnings") or r.get("risk_hints") or []):
        print(f"  ⚠ {w}")
    print("--- 守卫原始输出 ---")
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return code


# =====================================================================
# 模式 2: 成交回报落账(对齐 trade_executor 口径, 写前 T8 五元组查重)
# =====================================================================
def trade_records_duplicate(conn: sqlite3.Connection, code: str, trade_date: str,
                            action: str, price, volume) -> bool:
    """与 trade_executor._trade_records_duplicate(T8, 2026-09-23) 同款:
    五元组(code,trade_date,action,price,volume)参数绑定精确匹配, 纯查询无副作用。"""
    n = conn.execute(
        "SELECT COUNT(*) FROM trade_records "
        "WHERE code=? AND trade_date=? AND action=? AND price=? AND volume=?",
        (code, trade_date, action, price, volume)).fetchone()[0]
    return n > 0


def record_fill(conn: sqlite3.Connection, code: str, direction: str, price: float,
                quantity: int, trade_date: str, fill_time: str, source: str,
                name: str = "", sector: str = "", reason: str = "") -> int | None:
    """成交回报落账。返回新行 id; 五元组重复返回 None(调用方打印 duplicate skipped)。

    口径(逐字对齐 trade_executor do_buy/do_sell):
      strategy_version=trade_date, signal_type=白名单外归 manual,
      market_env="", created_at=当前时间 isoformat(秒), amount=round(price*vol,2)。
    与 executor 差异仅两处(留痕语义): subsystems='guarded_paper_trade' 标记写入方;
    notes 记 source/fill_time/reason(成交时刻真源)。
    """
    action = "BUY" if direction == "buy" else "SELL"
    code = _norm_code(code)
    now = datetime.now().isoformat(timespec="seconds")
    if not name:
        try:
            from stock_name_lookup import get_name
            name = get_name(code[2:]) or ""
        except Exception:
            name = ""
    note = f"会话直单守卫包装 | source={source} | 成交时间={fill_time}"
    if reason:
        note += f" | {reason}"
    row = (code, name, trade_date, action, float(price), int(quantity),
           round(float(price) * int(quantity), 2), trade_date,
           _norm_signal_type(source), "guarded_paper_trade", "", sector or "",
           note, now)
    if trade_records_duplicate(conn, code, trade_date, action,
                               row[4], row[5]):
        print(f"duplicate skipped: {code} {trade_date} {action} "
              f"{price} x{quantity} (五元组已存在, T8 幂等查重命中)")
        return None
    placeholders = ",".join("?" * len(_RECORD_COLUMNS))
    cur = conn.execute(
        f"INSERT INTO trade_records ({','.join(_RECORD_COLUMNS)}) "
        f"VALUES ({placeholders})", row)
    conn.commit()
    # M6 前向落地: 成交归因简行同步落 engine_attribution_log(断链修补 2026-09-23)
    _log_trade_attribution(conn, code, name, trade_date, direction, source)
    return cur.lastrowid


# engine_attribution_log 简归因: event 命名对齐既有 limit_up/big_drop 蛇形风格。
# 归因分析字段(ar/fwd3/fwd5/predictability 等)全部留空, 待归因引擎回填, 不编造数值。
_TRADE_ATTRIBUTION_EVENT = "trade_executed"
# 与既有 limit_up/big_drop 行 created_at 同款(空格分隔, 秒级)
_ATTRIBUTION_COLUMNS = ("event_date", "code", "name", "event",
                        "main_attribution", "sub_attribution", "created_at")


def trade_attribution_duplicate(conn: sqlite3.Connection, code: str,
                                event_date: str, sub_attribution: str) -> bool:
    """按 (code,event_date,event,sub_attribution) 参数绑定查重, 纯查询无副作用。"""
    n = conn.execute(
        "SELECT COUNT(*) FROM engine_attribution_log "
        "WHERE code=? AND event_date=? AND event=? AND sub_attribution=?",
        (code, event_date, _TRADE_ATTRIBUTION_EVENT, sub_attribution)
    ).fetchone()[0]
    return n > 0


def _log_trade_attribution(conn: sqlite3.Connection, code: str, name: str,
                           trade_date: str, direction: str, source: str) -> None:
    """向 engine_attribution_log 追加一行成交简归因(幂等; 失败不阻断落账主流程)。

    填充口径: event=trade_executed, event_date=trade_date,
    main_attribution=source(owner_order/s5/auth_package), sub_attribution=direction(buy/sell);
    分析字段 confidence/conflict_ratio/ar/stock_pct/benchmark_pct/dimension_scores/
    contribution_pct/predictability/controllability/feature_tags/fwd3/fwd5/
    outcome_updated_at 一律留 NULL 待归因引擎回填。
    """
    action = "buy" if direction == "buy" else "sell"  # sub_attribution 统一小写
    if trade_attribution_duplicate(conn, code, trade_date, action):
        print(f"duplicate skipped: attribution {code} {trade_date} "
              f"{_TRADE_ATTRIBUTION_EVENT} {action} (归因行已存在, 跳过)")
        return
    row = (trade_date, code, name, _TRADE_ATTRIBUTION_EVENT, source, action,
           datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    try:
        conn.execute(
            f"INSERT INTO engine_attribution_log ({','.join(_ATTRIBUTION_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(_ATTRIBUTION_COLUMNS))})", row)
        conn.commit()
        print(f"[归因简行] {code} {trade_date} {_TRADE_ATTRIBUTION_EVENT} "
              f"main={source} sub={action} -> engine_attribution_log")
    except sqlite3.Error as e:
        # 归因失败仅告警, 不回滚已落账的 trade_records(主账本优先)
        print(f"[警告] 归因简行写入失败(不阻断落账): {type(e).__name__}: {e}")


# =====================================================================
# CLI
# =====================================================================
USAGE = """用法(三段式):

  1) 下单前守卫预检(必跑; REJECT 时会话方不得下单):
     python guarded_paper_trade.py --precheck --code sh600519 --direction buy \\
         --price 14.09 --quantity 1000 \\
         [--name 名称 --source manual_confirmed --prev-close 13.99 \\
          --sector 行业 --total-cash N --holdings N --is-st \\
          --allow-add-position --no-log]
     卖出可加: [--loss-pct -0.095 --stop-loss --pe --pb --dividend-yield \\
               --earnings-yoy --net-inflow-5d --sector-pct]

  2) 成交回报落账(对齐 trade_executor 口径, 五元组查重幂等):
     python guarded_paper_trade.py --record --code sh600519 --direction buy \\
         --price 14.09 --quantity 1000 --trade-date 20260923 \\
         --fill-time "09:35:12" --source owner_order \\
         [--name 名称 --sector 行业 --reason 备注]
     source 仅限: owner_order | s5 | auth_package

  3) python guarded_paper_trade.py   # 无参数打印本用法
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="会话直单守卫包装器: 下单前守卫预检 + 成交回报落账")
    p.add_argument("--precheck", action="store_true", help="守卫预检模式")
    p.add_argument("--record", action="store_true", help="成交回报落账模式")
    p.add_argument("--code", default="", help="股票代码(带或不带sh/sz前缀)")
    p.add_argument("--direction", choices=["buy", "sell"], default=None)
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--quantity", type=int, default=None)
    # precheck 透传项(其余守卫参数保持守卫默认, 与其 argparse 对齐)
    p.add_argument("--name", default="")
    p.add_argument("--source", default="manual_confirmed",
                   help="信号源(precheck 默认 manual_confirmed; record 仅限 owner_order/s5/auth_package)")
    p.add_argument("--prev-close", type=float, default=None)
    p.add_argument("--sector", default="")
    p.add_argument("--total-cash", type=float, default=None)
    p.add_argument("--holdings", type=int, default=None)
    p.add_argument("--amount", type=float, default=None)
    p.add_argument("--is-st", action="store_true")
    p.add_argument("--allow-add-position", action="store_true")
    p.add_argument("--loss-pct", type=float, default=None)
    p.add_argument("--stop-loss", action="store_true")
    p.add_argument("--pe", type=float, default=None)
    p.add_argument("--pb", type=float, default=None)
    p.add_argument("--dividend-yield", type=float, default=None)
    p.add_argument("--earnings-yoy", type=float, default=None)
    p.add_argument("--net-inflow-5d", type=float, default=None)
    p.add_argument("--sector-pct", type=float, default=None)
    p.add_argument("--no-log", action="store_true",
                   help="跳过守卫自带 guard_precheck_log 落库(测试用)")
    # record 专有
    p.add_argument("--trade-date", default="",
                   help="成交交易日 YYYYMMDD(record 必填)")
    p.add_argument("--fill-time", default="",
                   help='成交时刻 "HH:MM:SS"(record 必填)')
    p.add_argument("--reason", default="")
    a = p.parse_args(argv)

    if not (a.precheck or a.record):
        print(USAGE)
        return 0
    if not (a.code and a.direction and a.price is not None and a.quantity):
        print("[错误] --code/--direction/--price/--quantity 为必填项", file=sys.stderr)
        print(USAGE)
        return 1

    if a.precheck:
        verdict, r, code = run_precheck(a)
        return _print_precheck(a, verdict, r, code)

    # ---- record 模式 ----
    if a.source not in _RECORD_SOURCES:
        print(f"[错误] --source 仅限 {_RECORD_SOURCES}", file=sys.stderr)
        return 1
    td = (a.trade_date or "").strip()
    ft = (a.fill_time or "").strip()
    if not (re.fullmatch(r"\d{8}", td) and re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", ft)):
        print("[错误] --trade-date 须为 YYYYMMDD, --fill-time 须为 HH:MM:SS",
              file=sys.stderr)
        return 1
    conn = sqlite3.connect(RECORD_DB)
    try:
        new_id = record_fill(conn, a.code, a.direction, a.price, a.quantity,
                             td, ft, a.source, a.name, a.sector, a.reason)
    finally:
        conn.close()
    if new_id is None:
        return 0
    print(f"[已落账] id={new_id} {_norm_code(a.code)} "
          f"{'BUY' if a.direction == 'buy' else 'SELL'} {a.price} x{a.quantity} "
          f"@{td} {ft} source={a.source} -> {RECORD_DB}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
