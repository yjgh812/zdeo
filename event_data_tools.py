#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事件数据统一工具 v1.0（P0 落地 2026-08-15）。

数据源（本机 akshare 1.18.55 实测）：
  1. 解禁日历
     - stock_restricted_release_queue_em(symbol)   单股解禁批次历史+未来（东财 dxf/q）
     - stock_restricted_release_summary_em         全市场按日汇总（东财 marketStatistics）
     - stock_restricted_release_detail_em          全市场解禁详情（东财 dxf/detail，含占解禁前流通市值比例）
     - stock_restricted_release_stockholder_em     单股解禁股东明细（钻取用，不入库）
     实测说明：queue_em 为单股接口（无股票代码列），不能作全市场主源；
              refresh 主源= detail_em（逐股明细），summary_em 存市场日度汇总表。
  2. 业绩预告
     - stock_yjyg_em(date=报告期)  东财业绩预告（最近3个报告期入库，供趋势对比）

入库：D:\\zcodesj\\event_calendar.db
  - restricted_release        逐股解禁明细（refresh 拉取 今-30天 ~ 今+90天）
  - restricted_release_daily  全市场解禁日度汇总
  - earnings_forecast         业绩预告（含中值/解析出的min-max）
  - refresh_log               每次刷新的元数据（失败也如实记录）

CLI:
  python event_data_tools.py --refresh                      # 拉最新数据入库
  python event_data_tools.py --check 600288 [--days 20]     # 单股：未来N日解禁 + 业绩预告超预期评估
  python event_data_tools.py --upcoming --days 20 [--top 20]# 全市场未来N日解禁Top清单

超预期评估（简化SUE）：
  当前报告期预告增速中值 - 上一报告期预告增速中值 → 超预期/符合/低于 三档；
  连续加速判定需3期数据（上上期/上期/当期增速逐级抬升）。
  铁律：实事求是——接口失败如实记录并尝试备选源，禁止编造。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta

DB_PATH = r"D:\zcodesj\event_calendar.db"
FETCH_DATE = date.today().isoformat()

# 与 pre_trade_check_v2.py R19 保持一致的风险口径
R19_LOOKAHEAD_DAYS = 20          # 未来20个自然日
R19_RELEASE_RATIO_BLOCK = 0.03   # 解禁市值占流通市值 >3% → 高危(BLOCK)
R19_RELEASE_RATIO_WARN = 0.01    # 1%~3% → 关注

# 简化SUE阈值（百分点）
SUE_DELTA_SURPRISE = 15.0        # 增速中值环比抬升≥15pct → 超预期
SUE_DELTA_MISS = -15.0           # 增速中值环比下降≥15pct → 低于预期
SUE_ACCEL_STEP = 5.0             # 连续加速：每期抬升≥5pct

# 预告类型 → 方向分（无历史对比数据时的兜底口径）
FORECAST_TYPE_SCORE = {
    "扭亏": 1.5, "预增": 1.0, "略增": 0.5, "续盈": 0.0,
    "略减": -0.5, "预减": -1.0, "续亏": -0.5, "首亏": -1.5,
}

# 业绩预告指标优先级（评估口径）
INDICATOR_PRIORITY = [
    "归属于上市公司股东的净利润",
    "扣除非经常性损益后的净利润",
    "净利润",
    "营业收入",
]


# ---------------------------------------------------------------- 基础工具
def norm_code(code: str) -> str:
    """600288 / sz000001 / 000001.SZ → 6位数字代码。"""
    digits = re.sub(r"\D", "", str(code))
    return digits[-6:] if len(digits) >= 6 else digits


def get_conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS restricted_release (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            release_date TEXT NOT NULL,          -- 解禁日 YYYY-MM-DD
            release_type TEXT,                   -- 限售股类型
            release_shares REAL,                 -- 解禁数量(股)
            actual_release_shares REAL,          -- 实际解禁数量(股)
            release_market_value REAL,           -- 实际解禁市值(元)
            pct_of_float_mv REAL,                -- 占解禁前流通市值比例 0~1
            prev_close REAL,                     -- 解禁前一交易日收盘价
            source TEXT,
            fetch_date TEXT,
            UNIQUE(code, release_date, release_type)
        );
        CREATE INDEX IF NOT EXISTS idx_rr_code_date ON restricted_release(code, release_date);

        CREATE TABLE IF NOT EXISTS restricted_release_daily (
            release_date TEXT PRIMARY KEY,
            stock_count INTEGER,                 -- 当日解禁股票家数
            release_shares REAL,
            actual_release_shares REAL,
            release_market_value REAL,
            source TEXT,
            fetch_date TEXT
        );

        CREATE TABLE IF NOT EXISTS earnings_forecast (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            report_period TEXT NOT NULL,         -- 报告期 YYYY-MM-DD
            announce_date TEXT,                  -- 公告日期
            indicator TEXT,                      -- 预测指标(归母净利润/扣非/营业收入...)
            forecast_type TEXT,                  -- 预告类型(预增/预减/扭亏...)
            forecast_text TEXT,                  -- 业绩变动原文
            forecast_value REAL,                 -- 预测数值中值(元, akshare已折算)
            change_pct REAL,                     -- 业绩变动幅度中值(%)
            prev_year_value REAL,                -- 上年同期值(元)
            value_min REAL,                      -- 解析:净利润/营收下限(元)
            value_max REAL,                      -- 解析:上限(元)
            growth_min REAL,                     -- 解析:同比增速下限(%)
            growth_max REAL,                     -- 解析:同比增速上限(%)
            reason TEXT,                         -- 业绩变动原因
            source TEXT,
            fetch_date TEXT,
            UNIQUE(code, report_period, indicator)
        );
        CREATE INDEX IF NOT EXISTS idx_ef_code_period ON earnings_forecast(code, report_period);

        CREATE TABLE IF NOT EXISTS refresh_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            dataset TEXT,
            source TEXT,
            status TEXT,                         -- OK / SKIPPED / FAILED
            rows INTEGER,
            date_min TEXT,
            date_max TEXT,
            note TEXT
        );
        """
    )
    con.commit()


def log_refresh(con: sqlite3.Connection, dataset: str, source: str, status: str,
                rows: int = 0, date_min: str = "", date_max: str = "", note: str = "") -> None:
    con.execute(
        "INSERT INTO refresh_log(ts, dataset, source, status, rows, date_min, date_max, note) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), dataset, source, status,
         rows, date_min, date_max, note[:500]),
    )
    con.commit()


# ---------------------------------------------------------------- 文本解析（业绩变动 min-max）
_NUM = r"([\d,]+(?:\.\d+)?)"


def _to_f(s: str) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def parse_forecast_text(text: str) -> dict:
    """从业绩变动原文解析 增速min-max 与 净利润/营收min-max（解析失败返回None字段，绝不编造）。

    样例:
      预计2026年1-6月营业收入:21,000万元至25,500万元,同比上年下降:39.71%至50.35%,...
      预计...净利润盈利:5,000万元至6,000万元,同比上年增长:120%至150%...
      预计...净利润亏损:15,000万元至17,800万元,同比上年增长:14.98%至28.35%...
    """
    out = {"value_min": None, "value_max": None, "growth_min": None, "growth_max": None}
    if not isinstance(text, str) or not text:
        return out
    t = text.replace("，", ",").replace("：", ":")

    m = re.search(r"增长:?" + _NUM + r"%至" + _NUM + "%", t)
    if m:
        a, b = _to_f(m.group(1)), _to_f(m.group(2))
        if a is not None and b is not None:
            out["growth_min"], out["growth_max"] = min(a, b), max(a, b)
    else:
        m = re.search(r"下降:?" + _NUM + r"%至" + _NUM + "%", t)
        if m:
            a, b = _to_f(m.group(1)), _to_f(m.group(2))
            if a is not None and b is not None:
                out["growth_min"], out["growth_max"] = -max(a, b), -min(a, b)
        else:
            m = re.search(r"增长:?" + _NUM + r"%", t)
            if m and _to_f(m.group(1)) is not None:
                out["growth_min"] = out["growth_max"] = _to_f(m.group(1))
            else:
                m = re.search(r"下降:?" + _NUM + r"%", t)
                if m and _to_f(m.group(1)) is not None:
                    out["growth_min"] = out["growth_max"] = -_to_f(m.group(1))

    # 金额区间（万元→元）；亏损语境取负
    m = re.search(r"(盈利:?|亏损:?|净利润:?|营业收入:?)" + _NUM + r"万元至" + _NUM + r"万元", t)
    if m:
        a, b = _to_f(m.group(2)), _to_f(m.group(3))
        if a is not None and b is not None:
            sign = -1.0 if "亏损" in m.group(1) else 1.0
            lo, hi = sign * min(a, b) * 1e4, sign * max(a, b) * 1e4
            out["value_min"], out["value_max"] = min(lo, hi), max(lo, hi)
    return out


# ---------------------------------------------------------------- refresh：解禁
def _f(df, col):
    """df列转float，列不存在返回None列表。"""
    if col not in df.columns:
        return None
    return df[col]


def refresh_restricted(con: sqlite3.Connection, days_back: int = 30, days_fwd: int = 90) -> dict:
    """全市场解禁入库。按指令顺序尝试 queue_em→summary_em→(stockholder_em)→detail_em。

    实测（akshare 1.18.55）：
      - queue_em 无 symbol 只有单股默认数据、无"股票代码"列 → 不能作全市场源，SKIPPED如实记录
      - summary_em 成功 → 存 restricted_release_daily（日度汇总）
      - detail_em 成功 → 存 restricted_release（逐股明细，主表）
    """
    import akshare as ak

    start = (date.today() - timedelta(days=days_back)).strftime("%Y%m%d")
    end = (date.today() + timedelta(days=days_fwd)).strftime("%Y%m%d")
    report = {"window": [start, end], "sources": []}

    # --- 1) queue_em（按指令优先尝试；实测为单股接口）---
    try:
        df = ak.stock_restricted_release_queue_em()
        if "股票代码" not in df.columns:
            raise ValueError(f"单股接口(默认symbol=600000, {len(df)}行, 无股票代码列)，不能作全市场解禁源")
    except Exception as e:
        report["sources"].append({"source": "stock_restricted_release_queue_em",
                                  "status": "SKIPPED", "error": str(e)[:200]})
        log_refresh(con, "restricted_release", "stock_restricted_release_queue_em",
                    "SKIPPED", 0, "", "", f"单股批次接口，留作--check实时增强: {e}")

    # --- 2) summary_em（全市场日度汇总）---
    try:
        df = ak.stock_restricted_release_summary_em(symbol="全部股票", start_date=start, end_date=end)
        for _, r in df.iterrows():
            con.execute(
                "INSERT OR REPLACE INTO restricted_release_daily"
                "(release_date, stock_count, release_shares, actual_release_shares, "
                " release_market_value, source, fetch_date) VALUES (?,?,?,?,?,?,?)",
                (str(r.get("解禁时间")), int(_to_f(r.get("当日解禁股票家数")) or 0),
                 _to_f(r.get("解禁数量")), _to_f(r.get("实际解禁数量")),
                 _to_f(r.get("实际解禁市值")), "summary_em", FETCH_DATE),
            )
        con.commit()
        dmin = str(df["解禁时间"].min()) if len(df) else ""
        dmax = str(df["解禁时间"].max()) if len(df) else ""
        report["sources"].append({"source": "stock_restricted_release_summary_em",
                                  "status": "OK", "rows": len(df)})
        log_refresh(con, "restricted_release_daily", "stock_restricted_release_summary_em",
                    "OK", len(df), dmin, dmax)
    except Exception as e:
        report["sources"].append({"source": "stock_restricted_release_summary_em",
                                  "status": "FAILED", "error": str(e)[:200]})
        log_refresh(con, "restricted_release_daily", "stock_restricted_release_summary_em",
                    "FAILED", 0, "", "", str(e))

    # --- 3) detail_em（逐股明细，主表；stockholder_em 为单股钻取接口不适用全市场刷新）---
    try:
        df = ak.stock_restricted_release_detail_em(start_date=start, end_date=end)
        n = 0
        for _, r in df.iterrows():
            code = norm_code(r.get("股票代码"))
            if not code:
                continue
            con.execute(
                "INSERT OR REPLACE INTO restricted_release"
                "(code, name, release_date, release_type, release_shares, actual_release_shares,"
                " release_market_value, pct_of_float_mv, prev_close, source, fetch_date) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (code, r.get("股票简称"), str(r.get("解禁时间")), r.get("限售股类型"),
                 _to_f(r.get("解禁数量")), _to_f(r.get("实际解禁数量")),
                 _to_f(r.get("实际解禁市值")), _to_f(r.get("占解禁前流通市值比例")),
                 _to_f(r.get("解禁前一交易日收盘价")), "detail_em", FETCH_DATE),
            )
            n += 1
        con.commit()
        dmin = str(df["解禁时间"].min()) if len(df) else ""
        dmax = str(df["解禁时间"].max()) if len(df) else ""
        report["sources"].append({"source": "stock_restricted_release_detail_em",
                                  "status": "OK", "rows": n})
        report["restricted_release_rows"] = n
        log_refresh(con, "restricted_release", "stock_restricted_release_detail_em",
                    "OK", n, dmin, dmax)
    except Exception as e:
        report["sources"].append({"source": "stock_restricted_release_detail_em",
                                  "status": "FAILED", "error": str(e)[:200]})
        log_refresh(con, "restricted_release", "stock_restricted_release_detail_em",
                    "FAILED", 0, "", "", str(e))
    return report


# ---------------------------------------------------------------- refresh：业绩预告
def last_report_periods(n: int = 3) -> list[str]:
    """最近n个已结束的季度末报告期（YYYYMMDD）。今天2026-08-15 → 20260630/20260331/20251231。"""
    today = date.today()
    qends = []
    y = today.year
    for yy in (y, y - 1):
        for md in ("1231", "0930", "0630", "0331"):
            d = datetime.strptime(f"{yy}{md}", "%Y%m%d").date()
            if d < today:
                qends.append(d.strftime("%Y%m%d"))
    qends.sort(reverse=True)
    return qends[:n]


def refresh_earnings(con: sqlite3.Connection, periods: list[str] | None = None) -> dict:
    """业绩预告入库（默认最近3个报告期，供增速趋势/连续加速判定）。"""
    import akshare as ak

    periods = periods or last_report_periods(3)
    report = {"periods": periods, "sources": []}
    for period in periods:
        try:
            df = ak.stock_yjyg_em(date=period)
            n = 0
            rp = f"{period[:4]}-{period[4:6]}-{period[6:]}"
            for _, r in df.iterrows():
                code = norm_code(r.get("股票代码"))
                if not code:
                    continue
                parsed = parse_forecast_text(r.get("业绩变动"))
                con.execute(
                    "INSERT OR REPLACE INTO earnings_forecast"
                    "(code, name, report_period, announce_date, indicator, forecast_type,"
                    " forecast_text, forecast_value, change_pct, prev_year_value,"
                    " value_min, value_max, growth_min, growth_max, reason, source, fetch_date) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (code, r.get("股票简称"), rp, str(r.get("公告日期")),
                     r.get("预测指标"), r.get("预告类型"), r.get("业绩变动"),
                     _to_f(r.get("预测数值")), _to_f(r.get("业绩变动幅度")),
                     _to_f(r.get("上年同期值")),
                     parsed["value_min"], parsed["value_max"],
                     parsed["growth_min"], parsed["growth_max"],
                     r.get("业绩变动原因"), "stock_yjyg_em", FETCH_DATE),
                )
                n += 1
            con.commit()
            amin = str(df["公告日期"].min()) if len(df) else ""
            amax = str(df["公告日期"].max()) if len(df) else ""
            report["sources"].append({"source": "stock_yjyg_em", "period": period,
                                      "status": "OK", "rows": n})
            log_refresh(con, f"earnings_forecast[{rp}]", "stock_yjyg_em", "OK", n, amin, amax)
        except Exception as e:
            report["sources"].append({"source": "stock_yjyg_em", "period": period,
                                      "status": "FAILED", "error": str(e)[:200]})
            log_refresh(con, f"earnings_forecast[{period}]", "stock_yjyg_em",
                        "FAILED", 0, "", "", str(e))
    return report


def cmd_refresh(args) -> int:
    con = get_conn()
    init_db(con)
    rr = refresh_restricted(con, days_back=args.back, days_fwd=args.fwd)
    ef = refresh_earnings(con)
    # 表统计
    stats = {}
    for t in ("restricted_release", "restricted_release_daily", "earnings_forecast"):
        cur = con.execute(f"SELECT COUNT(*), MIN(release_date), MAX(release_date) FROM {t}" if t != "earnings_forecast"
                          else f"SELECT COUNT(*), MIN(announce_date), MAX(announce_date) FROM {t}")
        row = cur.fetchone()
        stats[t] = {"rows": row[0], "date_min": row[1], "date_max": row[2]}
    cur = con.execute("SELECT COUNT(*), MIN(report_period), MAX(report_period) FROM earnings_forecast")
    row = cur.fetchone()
    stats["earnings_forecast"]["period_min"] = row[1]
    stats["earnings_forecast"]["period_max"] = row[2]
    con.close()
    print(json.dumps({"cmd": "refresh", "restricted": rr, "earnings": ef, "tables": stats},
                     ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- check：单股
def query_restricted_events(con: sqlite3.Connection, code: str, days: int) -> list[dict]:
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    cur = con.execute(
        "SELECT release_date, name, release_type, release_shares, actual_release_shares,"
        " release_market_value, pct_of_float_mv, prev_close, source "
        "FROM restricted_release WHERE code=? AND release_date>=? AND release_date<=? "
        "ORDER BY release_date",
        (code, start, end),
    )
    cols = ["release_date", "name", "release_type", "release_shares", "actual_release_shares",
            "release_market_value", "pct_of_float_mv", "prev_close", "source"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def live_queue_enrich(code: str) -> tuple[list[dict], str]:
    """实时增强：单股解禁批次(queue_em)，含更远未来。失败返回([], 错误信息)——如实报告。"""
    try:
        import akshare as ak
        df = ak.stock_restricted_release_queue_em(symbol=code)
        events = []
        for _, r in df.iterrows():
            d = str(r.get("解禁时间"))
            events.append({
                "release_date": d, "name": None, "release_type": r.get("限售股类型"),
                "release_shares": _to_f(r.get("解禁数量")), "actual_release_shares": None,
                "release_market_value": _to_f(r.get("实际解禁数量市值")),
                "pct_of_float_mv": _to_f(r.get("占流通市值比例")),
                "prev_close": None, "source": "queue_em_live",
            })
        return events, ""
    except Exception as e:
        return [], str(e)[:200]


def classify_release(e: dict) -> str:
    p = e.get("pct_of_float_mv")
    if p is None:
        return "比例缺失"
    if p > R19_RELEASE_RATIO_BLOCK:
        return f"高危(>{R19_RELEASE_RATIO_BLOCK*100:.0f}%,R19 BLOCK)"
    if p > R19_RELEASE_RATIO_WARN:
        return f"关注({R19_RELEASE_RATIO_WARN*100:.0f}~{R19_RELEASE_RATIO_BLOCK*100:.0f}%)"
    return "轻微"


def evaluate_earnings(con: sqlite3.Connection, code: str) -> dict:
    """简化SUE：当期预告增速中值 vs 上期预告增速中值 → 三档 + 连续加速判定。"""
    cur = con.execute(
        "SELECT report_period, indicator, forecast_type, forecast_value, change_pct,"
        " growth_min, growth_max, announce_date, prev_year_value, forecast_text "
        "FROM earnings_forecast WHERE code=? ORDER BY report_period DESC",
        (code,),
    )
    rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    if not rows:
        return {"status": "no_data",
                "message": "无业绩预告数据（可能未发预告；fail-open，仅供参考）"}

    # 指标口径选择：按优先级取覆盖报告期最多的指标
    ind_periods: dict[str, set] = {}
    for r in rows:
        ind_periods.setdefault(r["indicator"], set()).add(r["report_period"])
    ind = max(INDICATOR_PRIORITY, key=lambda k: len(ind_periods.get(k, set()))) \
        if any(k in ind_periods for k in INDICATOR_PRIORITY) else rows[0]["indicator"]

    by_period: dict[str, dict] = {}
    for r in rows:
        if r["indicator"] == ind and r["report_period"] not in by_period:
            by_period[r["report_period"]] = r
    periods = sorted(by_period, reverse=True)
    cur_r = by_period[periods[0]]

    growth_series = []
    for p in periods[:4]:
        r = by_period[p]
        g = r["change_pct"] if r["change_pct"] is not None else \
            ((r["growth_min"] + r["growth_max"]) / 2 if r["growth_min"] is not None and r["growth_max"] is not None else None)
        growth_series.append({"report_period": p, "growth_mid_pct": g,
                              "forecast_type": r["forecast_type"],
                              "announce_date": r["announce_date"],
                              "forecast_value": r["forecast_value"],
                              "growth_range": [r["growth_min"], r["growth_max"]]})

    g_curr = growth_series[0]["growth_mid_pct"] if growth_series else None
    g_prev = growth_series[1]["growth_mid_pct"] if len(growth_series) > 1 else None
    g_prev2 = growth_series[2]["growth_mid_pct"] if len(growth_series) > 2 else None

    verdict, basis = None, []
    if g_curr is not None and g_prev is not None:
        delta = g_curr - g_prev
        basis.append(f"当期({periods[0][:7]})增速中值{g_curr:.1f}% vs 上期({periods[1][:7]}){g_prev:.1f}%，Δ={delta:+.1f}pct")
        verdict = "超预期" if delta >= SUE_DELTA_SURPRISE else ("低于预期" if delta <= SUE_DELTA_MISS else "符合预期")
    elif g_curr is not None and g_prev is None:
        # 无同口径历史 → 用上年同期值折算兜底
        if cur_r["prev_year_value"] and cur_r["forecast_value"] and cur_r["prev_year_value"] > 0:
            implied = (cur_r["forecast_value"] / cur_r["prev_year_value"] - 1) * 100
            basis.append(f"无上期预告，按上年同期折算增速{implied:.1f}%（口径:{cur_r['forecast_type']}）")
            verdict = "超预期" if implied >= SUE_DELTA_SURPRISE else ("低于预期" if implied <= SUE_DELTA_MISS else "符合预期")
        else:
            score = FORECAST_TYPE_SCORE.get(cur_r["forecast_type"], 0.0)
            basis.append(f"无历史对比，按预告类型[{cur_r['forecast_type']}]方向分{score:+.1f}兜底")
            verdict = "超预期" if score >= 1.0 else ("低于预期" if score <= -1.0 else "符合预期")
    else:
        verdict = "符合预期"
        basis.append("无可用增速数值")

    acceleration = "数据不足"
    if g_curr is not None and g_prev is not None and g_prev2 is not None:
        if g_prev > g_prev2 + SUE_ACCEL_STEP and g_curr > g_prev + SUE_ACCEL_STEP:
            acceleration = "连续加速(两期抬升)"
        elif g_curr > g_prev + SUE_ACCEL_STEP:
            acceleration = "当期加速"
        elif g_curr < g_prev - SUE_ACCEL_STEP:
            acceleration = "当期减速"
        else:
            acceleration = "平稳/波动"

    return {
        "status": "ok",
        "indicator": ind,
        "latest_period": periods[0],
        "latest_forecast_type": cur_r["forecast_type"],
        "announce_date": cur_r["announce_date"],
        "forecast_value_mid": cur_r["forecast_value"],
        "growth_mid_pct": g_curr,
        "growth_range": [cur_r["growth_min"], cur_r["growth_max"]],
        "growth_series": growth_series,
        "sue_verdict": verdict,
        "acceleration": acceleration,
        "basis": basis,
    }


def cmd_check(args) -> int:
    code = norm_code(args.check)
    con = get_conn()
    try:
        events = query_restricted_events(con, code, args.days)
        db_err = ""
    except sqlite3.OperationalError as e:
        events, db_err = [], f"event_calendar.db表不存在: {e}"
    finally:
        con.close()

    live_events, live_err = ([], "")
    if args.live:
        live_events, live_err = live_queue_enrich(code)
        # queue_em返回全部历史批次 → 仅保留检查窗口内的未来事件
        start, end = date.today().isoformat(), (date.today() + timedelta(days=args.days)).isoformat()
        live_events = [e for e in live_events if start <= str(e["release_date"])[:10] <= end]

    # 合并去重（release_date+release_type）
    seen, merged = set(), list(events)
    for e in events:
        seen.add((e["release_date"], e["release_type"]))
    for e in live_events:
        k = (e["release_date"], e["release_type"])
        if k not in seen:
            merged.append(e)
    merged.sort(key=lambda x: x["release_date"])

    for e in merged:
        e["risk_level"] = classify_release(e)
    has_block = any(e["risk_level"].startswith("高危") for e in merged)

    con = get_conn()
    try:
        earnings = evaluate_earnings(con, code)
    except sqlite3.OperationalError as e:
        earnings = {"status": "no_data", "message": f"earnings_forecast表不存在: {e}"}
    finally:
        con.close()

    name = next((e.get("name") for e in merged if e.get("name")), "")
    result = {
        "cmd": "check",
        "code": code,
        "name": name,
        "check_date": date.today().isoformat(),
        "window_days": args.days,
        "restricted_release": {
            "events_in_window": merged,
            "event_count": len(merged),
            "r19_block": has_block,
            "note": db_err or (f"库内{len(events)}条" + (f"；实时增强{len(live_events)}条" if args.live else "")
                    + (f"；实时增强失败:{live_err}" if args.live and live_err else "")),
        },
        "earnings_forecast": earnings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if has_block else 0


# ---------------------------------------------------------------- upcoming：全市场解禁Top
def cmd_upcoming(args) -> int:
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=args.days)).isoformat()
    con = get_conn()
    try:
        cur = con.execute(
            "SELECT code, name, release_date, release_type, release_market_value, "
            "pct_of_float_mv, release_shares FROM restricted_release "
            "WHERE release_date>=? AND release_date<=? "
            "ORDER BY release_market_value DESC LIMIT ?",
            (start, end, args.top),
        )
        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        cur = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(release_market_value),0) FROM restricted_release "
            "WHERE release_date>=? AND release_date<=?", (start, end))
        total_n, total_v = cur.fetchone()
    finally:
        con.close()

    print(f"# 未来{args.days}个自然日全市场解禁Top{args.top}（{start} ~ {end}，按解禁市值降序）")
    print(f"窗口内合计 {total_n} 家 / 解禁市值 {total_v/1e8:.1f} 亿元\n")
    print(f"{'解禁日':<12}{'代码':<8}{'名称':<10}{'解禁市值(亿)':>10}{'占流通市值%':>10}  类型")
    for r in rows:
        v = r["release_market_value"]
        p = r["pct_of_float_mv"]
        print(f"{r['release_date']:<12}{r['code']:<8}{str(r['name'] or '')[:9]:<10}"
              f"{(v or 0)/1e8:>10.2f}{(p*100 if p is not None else float('nan')):>10.2f}  {r['release_type'] or ''}")
    if not rows:
        print("（无数据——请先运行 --refresh；若已刷新仍为空，检查akshare接口返回）")
    return 0


# ---------------------------------------------------------------- main
def main() -> int:
    p = argparse.ArgumentParser(description="事件数据统一工具：解禁日历+业绩预告 (event_calendar.db)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--refresh", action="store_true", help="拉取最新解禁+业绩预告入库")
    g.add_argument("--check", metavar="CODE", help="单股检查：未来N日解禁+业绩预告超预期评估")
    g.add_argument("--upcoming", action="store_true", help="未来N日全市场解禁Top清单")
    p.add_argument("--days", type=int, default=R19_LOOKAHEAD_DAYS,
                   help="检查/清单窗口天数（默认20，与R19一致）")
    p.add_argument("--top", type=int, default=20, help="--upcoming 输出条数（默认20）")
    p.add_argument("--back", type=int, default=30, help="refresh解禁回看天数（默认30）")
    p.add_argument("--fwd", type=int, default=90, help="refresh解禁前瞻天数（默认90）")
    p.add_argument("--live", action="store_true", help="--check 时实时拉单股解禁批次增强")
    args = p.parse_args()

    if args.refresh:
        return cmd_refresh(args)
    if args.check:
        return cmd_check(args)
    return cmd_upcoming(args)


if __name__ == "__main__":
    raise SystemExit(main())
