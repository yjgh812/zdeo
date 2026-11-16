#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反馈闭环四表灌数脚本（屠龙系统 M2 机制，2026-08-13 建）。

背景（自省报告 E13/D3）：attribution.db 的 signal_snapshots / factor_performance /
r8_forward_tracking / verification_trades 四张反馈表长期 0 行，归因→选股的反向
赋能链路断裂。本脚本打通"信号→表现→归因→反馈"闭环。

数据源：
  - attribution.db : trade_records（BUY 记录，含入场价/信号/板块）
  - fundamental_data.db : daily_basic（close/pe_ttm/pb/dv_ratio/volume_ratio，315万行）

三模式：
  sync      从 trade_records 的 BUY 记录同步到 verification_trades + r8_forward_tracking（去重）
  snapshot  为指定标的/日期生成 signal_snapshots（MA20/MA60/RSI14/MACD/volume_ratio）
  update    每日收盘后更新所有未闭合记录的 T+1/T+3/T+5 收益（ret 口径：close_tN/entry-1）

用法：
    python feed_feedback_tables.py sync                      # 同步新买入记录
    python feed_feedback_tables.py snapshot --date 20260813  # 为当日买入标的生成信号快照
    python feed_feedback_tables.py update                    # 收盘后更新前瞻收益
    python feed_feedback_tables.py sync && python feed_feedback_tables.py update

建议 cron：工作日 16:35（daily_chain 之后）跑 sync + update。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
import numpy as np
import talib

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"


# ---------- 代码格式转换 ----------
def attr_to_ts(code: str) -> str:
    """sz300790 -> 300790.SZ ; sh600519 -> 600519.SH ; 已是 300790.SZ 则原样。"""
    c = str(code).strip()
    if "." in c and not c.lower().startswith(("sh", "sz", "bj")):
        return c.upper()
    cl = c.lower()
    num = cl[2:] if cl[:2] in ("sh", "sz", "bj") else cl
    if cl.startswith("sh") or num.startswith(("600", "601", "603", "605", "688", "689")):
        return f"{num}.SH"
    if cl.startswith("bj") or num.startswith(("8", "920", "430")):
        return f"{num}.BJ"
    return f"{num}.SZ"


def attr_to_plain(code: str) -> str:
    """sz300790 -> 300790"""
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return c[2:]
    return c.split(".")[0]


# ---------- 技术指标（TA-Lib 实现）----------
def sma(vals: list[float], n: int) -> float | None:
    arr = np.array(vals, dtype=float)
    if len(arr) < n:
        return None
    result = talib.SMA(arr, timeperiod=n)
    return float(round(result[-1], 4))


def rsi14(closes: list[float]) -> float | None:
    arr = np.array(closes, dtype=float)
    if len(arr) < 15:
        return None
    result = talib.RSI(arr, timeperiod=14)
    return float(round(result[-1], 2))


def macd(closes: list[float]) -> tuple[float | None, float | None]:
    arr = np.array(closes, dtype=float)
    if len(arr) < 35:
        return None, None
    dif, dea, _ = talib.MACD(arr, fastperiod=12, slowperiod=26, signalperiod=9)
    return float(round(dif[-1], 4)), float(round(dea[-1], 4))


# ---------- 数据读取 ----------
def get_close_series(conn: sqlite3.Connection, ts_code: str, end_date: str, n: int = 120) -> list[float]:
    rows = conn.execute(
        "SELECT close FROM daily_basic WHERE ts_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
        (ts_code, end_date, n),
    ).fetchall()
    return [r[0] for r in reversed(rows) if r[0] is not None]


def get_trade_dates_after(conn: sqlite3.Connection, date: str, n: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM daily_basic WHERE trade_date>? ORDER BY trade_date LIMIT ?",
        (date, n),
    ).fetchall()
    return [r[0] for r in rows]


def get_close_on(conn: sqlite3.Connection, ts_code: str, date: str) -> float | None:
    r = conn.execute(
        "SELECT close FROM daily_basic WHERE ts_code=? AND trade_date=?", (ts_code, date)
    ).fetchone()
    return r[0] if r else None


# ---------- 模式1: sync（trade_records → verification_trades / r8_forward_tracking）----------
def do_sync(attr: sqlite3.Connection) -> dict:
    buys = attr.execute(
        "SELECT code, name, trade_date, price, volume, signal_type, sector, subsystems "
        "FROM trade_records WHERE action='BUY' ORDER BY trade_date"
    ).fetchall()
    ins_v, ins_r, skip = 0, 0, 0
    skipped_noprice = 0
    for code, name, td, price, vol, sig, sector, subsys in buys:
        plain = attr_to_plain(code)
        # 2026-09-10: 源 price NULL/<=0 的行不落库(华天科技 002185 8/15 price=0.0 曾致
        # verification_trades 两行 entry_price NULL/0 永久卡死), 如实计数留痕
        if price is None or float(price) <= 0:
            skipped_noprice += 1
            continue
        # 信号源优先级：subsystems(真实信号源,guarded_trade写入) > signal_type(manual兜底)
        strat = (subsys or "").strip() or (sig or "manual")
        # verification_trades 去重（同 code+date+price+volume——2026-09-10 补 volume:
        # 同码同日同价不同量两笔委托此前第二笔被吞, 归因前瞻缺行）
        ex = attr.execute(
            "SELECT COUNT(*) FROM verification_trades WHERE stock_code=? AND trade_date=? AND entry_price=? AND volume=?",
            (plain, td, price, vol),
        ).fetchone()[0]
        if ex == 0:
            attr.execute(
                "INSERT INTO verification_trades (trade_date, stock_code, stock_name, strategy_id, "
                "hypothesis, entry_price, volume, signal_snapshot, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (td, plain, name, strat, subsys or "", price, vol,
                 f'{{"sector":"{sector or ""}"}}', datetime.now().isoformat(timespec="seconds")),
            )
            ins_v += 1
        # r8_forward_tracking 去重（同 code+signal_date）
        ex2 = attr.execute(
            "SELECT COUNT(*) FROM r8_forward_tracking WHERE stock_code=? AND signal_date=?",
            (plain, td),
        ).fetchone()[0]
        if ex2 == 0:
            attr.execute(
                "INSERT INTO r8_forward_tracking (signal_date, stock_code, stock_name, sector, "
                "entry_price, recorded_at) VALUES (?,?,?,?,?,?)",
                (td, plain, name, sector or "", price, datetime.now().isoformat(timespec="seconds")),
            )
            ins_r += 1
        else:
            skip += 1
    attr.commit()
    return {"verification_trades_inserted": ins_v, "r8_forward_inserted": ins_r,
            "skipped_dup": skip, "skipped_noprice": skipped_noprice}


# ---------- 模式2: snapshot（signal_snapshots 技术指标快照）----------
def do_snapshot(attr: sqlite3.Connection, fund: sqlite3.Connection, date: str) -> dict:
    # 取当日有 BUY 记录的标的
    codes = attr.execute(
        "SELECT DISTINCT code FROM trade_records WHERE action='BUY' AND trade_date=?", (date,)
    ).fetchall()
    n_ins = 0
    for (code,) in codes:
        plain = attr_to_plain(code)
        ts = attr_to_ts(code)
        ex = attr.execute(
            "SELECT COUNT(*) FROM signal_snapshots WHERE code=? AND date=?", (plain, date)
        ).fetchone()[0]
        if ex > 0:
            continue
        closes = get_close_series(fund, ts, date, 120)
        if len(closes) < 35:
            continue
        ma20, ma60 = sma(closes, 20), sma(closes, 60)
        rsi = rsi14(closes)
        dif, dea = macd(closes)
        vr = fund.execute(
            "SELECT volume_ratio FROM daily_basic WHERE ts_code=? AND trade_date=?", (ts, date)
        ).fetchone()
        attr.execute(
            "INSERT INTO signal_snapshots (code, date, close_price, ma20, ma60, rsi14, "
            "macd_dif, macd_dea, volume_ratio, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (plain, date, closes[-1], ma20, ma60, rsi, dif, dea,
             vr[0] if vr else None, datetime.now().isoformat(timespec="seconds")),
        )
        n_ins += 1
    attr.commit()
    return {"signal_snapshots_inserted": n_ins, "for_date": date, "codes_seen": len(codes)}


# ---------- 模式3: update（收盘后更新 T+1/T+3/T+5 前瞻收益）----------
def do_update(attr: sqlite3.Connection, fund: sqlite3.Connection) -> dict:
    upd_v, upd_r = 0, 0
    # verification_trades
    # 2026-09-10: entry_price IS NOT NULL → entry_price > 0(0.0 价行如华天科技 id=139
    # 每次 update 都会被捞起但永远算不出 rets=永久卡死噪音; 已一次性标注 notes)
    rows = attr.execute(
        "SELECT id, stock_code, trade_date, entry_price FROM verification_trades "
        "WHERE entry_price > 0 AND (actual_ret_t1 IS NULL OR actual_ret_t3 IS NULL OR actual_ret_t5 IS NULL)"
    ).fetchall()
    for rid, code, td, entry in rows:
        ts = attr_to_ts(code)
        future = get_trade_dates_after(fund, td, 5)
        rets = {}
        for idx, lbl in ((0, "actual_ret_t1"), (2, "actual_ret_t3"), (4, "actual_ret_t5")):
            if len(future) > idx:
                c = get_close_on(fund, ts, future[idx])
                if c is not None and entry:
                    rets[lbl] = round(c / entry - 1, 4)
        if rets:
            sets = ", ".join(f"{k}=?" for k in rets)
            attr.execute(f"UPDATE verification_trades SET {sets} WHERE id=?", (*rets.values(), rid))
            upd_v += 1
    # r8_forward_tracking
    # 2026-09-10: 同款 0 价行排除
    rows = attr.execute(
        "SELECT id, stock_code, signal_date, entry_price FROM r8_forward_tracking "
        "WHERE entry_price > 0 AND (ret_t1 IS NULL OR ret_t3 IS NULL OR ret_t5 IS NULL)"
    ).fetchall()
    for rid, code, td, entry in rows:
        ts = attr_to_ts(code)
        future = get_trade_dates_after(fund, td, 5)
        rets = {}
        for idx, lbl in ((0, "ret_t1"), (2, "ret_t3"), (4, "ret_t5")):
            if len(future) > idx:
                c = get_close_on(fund, ts, future[idx])
                if c is not None and entry:
                    rets[lbl] = round(c / entry - 1, 4)
        if rets:
            sets = ", ".join(f"{k}=?" for k in rets)
            attr.execute(f"UPDATE r8_forward_tracking SET {sets} WHERE id=?", (*rets.values(), rid))
            upd_r += 1
    # 2026-09-28 S1(全维度自省): result 标注并入本 owner。原归属 attribution/afternoon_fillback.py
    # 从未挂任何调度（22:00链第六步只调本脚本），且其日期解析口径('%Y-%m-%d' vs 表内 YYYYMMDD)
    # 必败 → result 全表 294 行 0 标注。口径沿用原合同: T+1 收益 >0.5%=confirmed / <-0.5%=refuted /
    # 其余 inconclusive；幂等 WHERE result IS NULL；消费方=strategy_decay_monitor.py。
    upd_res = attr.execute(
        "UPDATE verification_trades SET result = CASE "
        "WHEN actual_ret_t1 > 0.005 THEN 'confirmed' "
        "WHEN actual_ret_t1 < -0.005 THEN 'refuted' "
        "ELSE 'inconclusive' END "
        "WHERE actual_ret_t1 IS NOT NULL AND (result IS NULL OR result='')"
    ).rowcount
    attr.commit()
    return {"verification_updated": upd_v, "r8_forward_updated": upd_r, "result_labeled": upd_res}


def main() -> int:
    p = argparse.ArgumentParser(description="反馈闭环四表灌数脚本")
    p.add_argument("mode", choices=["sync", "snapshot", "update", "all"])
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="snapshot 模式目标日期")
    a = p.parse_args()

    attr = sqlite3.connect(ATTR_DB)
    fund = sqlite3.connect(FUND_DB)
    out = {}
    if a.mode in ("sync", "all"):
        out["sync"] = do_sync(attr)
    if a.mode in ("snapshot", "all"):
        out["snapshot"] = do_snapshot(attr, fund, a.date)
    if a.mode in ("update", "all"):
        out["update"] = do_update(attr, fund)
    attr.close()
    fund.close()

    import json
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
