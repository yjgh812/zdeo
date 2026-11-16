#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pre_risk_check.py — 屠龙系统 买入前置风险核查件（纯只读）

买入验证单「解禁雷 / 业绩预告 / 风险警示」三查自动化，替代人工核对。

数据源（全部本地 SQLite，只读连接 file:...?mode=ro，SQL 全参数绑定）：
  ① 解禁    D:/zcodesj/event_calendar.db  -> restricted_release
             code(6位裸码) / release_date('YYYY-MM-DD') / release_shares(股)
             release_market_value(元) / pct_of_float_mv(小数, 1.32=132%)
             覆盖: release_date 2026-08-24 ~ 2026-12-22（refresh_log 2026-09-23 刷新）
  ② 业绩预告 D:/zcodesj/event_calendar.db  -> earnings_forecast
             announce_date('YYYY-MM-DD') / report_period / forecast_type
             growth_min/max(%) / value_min/max(元)（表内含重复行，输出前去重）
  ③ 风险警示 D:/zcodesj/eastmoney_quant/stock/stock_data.db -> stock_basic(symbol,name)
             名称含 'ST'/*ST/'退' 即判警示；若票不在名单内 -> 输出
             「本地无此数据源，需会话侧MCP核对」

判定红线：
  ① 解禁窗口内有记录 且 (占流通比 > 5% 或 解禁市值 > 1亿) -> FAIL(红)
     有记录但未达线 -> PASS(绿，注明小额解禁)；无记录 -> PASS(绿)
  ② 业绩预告 announce_date 落在窗口内 -> WARN(黄，附类型与区间)；否则 PASS(绿)
  ③ 命中风险警示名称 -> FAIL(红)；查无此票 -> UNKNOWN 提示会话侧 MCP 核对

总评级: FAIL > WARN > PASS。

用法:
  python pre_risk_check.py --codes sh600519,sz300667 [--days 15] [--asof 2026-09-23]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

BASE = Path("D:/zcodesj")
EVENT_DB = BASE / "event_calendar.db"
STOCK_DB = BASE / "eastmoney_quant/stock/stock_data.db"

UNLOCK_PCT_RED = 5.0        # 占流通比红线 %
UNLOCK_MV_RED = 1.0e8       # 解禁市值红线 1亿元
ST_NAME_PAT = re.compile(r"ST|退", re.IGNORECASE)


def norm_code(raw: str) -> str:
    """sh600519 / SZ300667 / 600519 -> 6位裸码；无法解析返回原串"""
    digits = re.sub(r"[^0-9]", "", raw)
    return digits[-6:] if len(digits) >= 6 else raw.strip()


def ro_connect(db: Path) -> sqlite3.Connection:
    if not db.exists():
        raise FileNotFoundError(f"本地库不存在: {db}")
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def fetch_unlock(code: str, d0: str, d1: str):
    with ro_connect(EVENT_DB) as con:
        cur = con.execute(
            "SELECT release_date, name, release_shares, release_market_value,"
            " pct_of_float_mv, release_type"
            " FROM restricted_release WHERE substr(code,1,6) = ?"
            " AND release_date >= ? AND release_date <= ?"
            " ORDER BY release_date",
            (code, d0, d1),
        )
        return cur.fetchall()


def fetch_forecast(code: str, d0: str, d1: str):
    with ro_connect(EVENT_DB) as con:
        cur = con.execute(
            "SELECT DISTINCT announce_date, report_period, forecast_type,"
            " growth_min, growth_max, value_min, value_max"
            " FROM earnings_forecast WHERE substr(code,1,6) = ?"
            " AND announce_date >= ? AND announce_date <= ?"
            " ORDER BY announce_date",
            (code, d0, d1),
        )
        return cur.fetchall()


def fetch_st_name(code: str):
    """返回 (命中bool, 名称或None)。票不在本地名单 -> (None, None)"""
    with ro_connect(STOCK_DB) as con:
        row = con.execute(
            "SELECT name FROM stock_basic WHERE symbol = ?", (code,)
        ).fetchone()
    if row is None:
        return None, None
    return bool(ST_NAME_PAT.search(row[0] or "")), row[0]


def fmt_range(gmin, gmax, vmin, vmax) -> str:
    parts = []
    if gmin is not None or gmax is not None:
        lo = "-" if gmin is None else f"{gmin:g}%"
        hi = "-" if gmax is None else f"{gmax:g}%"
        parts.append(f"净利同比{lo}~{hi}")
    if vmin is not None or vmax is not None:
        def yi(x):
            return "-" if x is None else f"{float(x)/1e8:.2f}亿"
        parts.append(f"净利{yi(vmin)}~{yi(vmax)}")
    return "，".join(parts) if parts else "区间未披露"


def check_one(code: str, days: int, asof: date) -> dict:
    d0 = asof.strftime("%Y-%m-%d")
    d1 = (asof + timedelta(days=days)).strftime("%Y-%m-%d")
    reasons, verdict = [], "PASS"

    # ① 解禁
    unlocks = fetch_unlock(code, d0, d1)
    red_hits = [
        r for r in unlocks
        if float(r[4] or 0) * 100 > UNLOCK_PCT_RED or float(r[3] or 0) > UNLOCK_MV_RED
    ]
    if red_hits:
        verdict = "FAIL"
        for r in red_hits:
            reasons.append(
                f"解禁雷: {r[0]} 解禁{float(r[3])/1e8:.2f}亿 "
                f"占流通{float(r[4])*100:.1f}% ({r[5] or '类型未披露'})"
            )
    elif unlocks:
        small = "；".join(
            f"{r[0]} {float(r[3])/1e8:.2f}亿/占流通{float(r[4])*100:.1f}%" for r in unlocks
        )
        reasons.append(f"窗口内有小额解禁未达红线: {small}")

    # ② 业绩预告
    fcs = fetch_forecast(code, d0, d1)
    if fcs:
        if verdict == "PASS":
            verdict = "WARN"
        for f in fcs:
            reasons.append(
                f"业绩预告: {f[0]} 报告期{f[1] or '?'} {f[2] or '类型未披露'}，{fmt_range(f[3], f[4], f[5], f[6])}"
            )

    # ③ 风险警示
    st_hit, name = fetch_st_name(code)
    if st_hit is None:
        reasons.append("风险警示: 本地无此数据源，需会话侧MCP核对")
    elif st_hit:
        verdict = "FAIL"
        reasons.append(f"风险警示: 名称含ST/退 ({name})")

    return {"code": code, "name": name or "", "verdict": verdict, "reasons": reasons}


def main() -> int:
    ap = argparse.ArgumentParser(description="买入前置风险核查（解禁/业绩预告/风险警示）")
    ap.add_argument("--codes", required=True, help="逗号分隔，如 sh600519,sz300667")
    ap.add_argument("--days", type=int, default=15, help="核查窗口天数，默认15")
    ap.add_argument("--asof", default="", help="基准日 YYYY-MM-DD，默认今天")
    args = ap.parse_args()

    asof = date.fromisoformat(args.asof) if args.asof else date.today()
    codes = [norm_code(c) for c in args.codes.split(",") if c.strip()]
    if not codes:
        print("[错误] --codes 为空", file=sys.stderr)
        return 2

    try:
        with ro_connect(EVENT_DB) as con:
            unlock_fresh = con.execute(
                "SELECT MAX(fetch_date), MIN(release_date), MAX(release_date), COUNT(*)"
                " FROM restricted_release"
            ).fetchone()
            fc_fresh = con.execute(
                "SELECT MAX(fetch_date), MAX(announce_date), COUNT(*) FROM earnings_forecast"
            ).fetchone()
        with ro_connect(STOCK_DB) as con:
            st_count = con.execute("SELECT COUNT(*) FROM stock_basic").fetchone()[0]
    except FileNotFoundError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2

    print("=" * 72)
    print(f"买入前置风险核查 | 基准日 {asof.isoformat()} | 窗口 {args.days} 日"
          f"（{asof.isoformat()} ~ {(asof + timedelta(days=args.days)).isoformat()}）")
    print(f"数据源①解禁 restricted_release: fetch {unlock_fresh[0]}, "
          f"release_date 覆盖 {unlock_fresh[1]}~{unlock_fresh[2]}, {unlock_fresh[3]} 行")
    print(f"数据源②业绩预告 earnings_forecast: fetch {fc_fresh[0]}, "
          f"最新 announce_date {fc_fresh[1]}, {fc_fresh[2]} 行")
    print(f"数据源③风险警示 stock_basic（名称匹配ST/退）: {st_count} 只")
    print(f"红线: 解禁占流通>{UNLOCK_PCT_RED}% 或 解禁市值>{UNLOCK_MV_RED/1e8:.0f}亿 -> FAIL")
    print("-" * 72)

    results = [check_one(c, args.days, asof) for c in codes]
    for r in results:
        reason = "；".join(r["reasons"]) if r["reasons"] else "窗口内无解禁/无业绩预告/非风险警示"
        print(f"{r['code']} {r['name']:<6} | {r['verdict']} | {reason}")
    print("-" * 72)

    n_fail = sum(1 for r in results if r["verdict"] == "FAIL")
    n_warn = sum(1 for r in results if r["verdict"] == "WARN")
    n_pass = sum(1 for r in results if r["verdict"] == "PASS")
    print("【买入验证单·前置风险核查汇总】")
    print(f"  核查日期: {asof.isoformat()}  窗口: 未来{args.days}日  票数: {len(results)}")
    print(f"  结果: PASS {n_pass} / WARN {n_warn} / FAIL {n_fail}")
    for r in results:
        brief = "；".join(r["reasons"]) if r["reasons"] else "三查全绿"
        print(f"  [{r['verdict']}] {r['code']} {r['name']} — {brief}")
    print("  结论: " + ("存在 FAIL，禁止买入！" if n_fail else
                       "存在 WARN，人工确认后再买入" if n_warn else "三查全绿，可进入后续验证"))
    print("=" * 72)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
