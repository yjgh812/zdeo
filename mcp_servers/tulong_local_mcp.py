# -*- coding: utf-8 -*-
"""
tulong_local_mcp.py — 屠龙系统本地库只读 MCP (2026-08-15 M1 补缺)
=================================================================
解决: factor_pit/attribution/event_calendar 等 9 个本地库只能靠临时脚本直连,
会话内归因查询/事件检索/因子调取每次现写代码,且有误写风险。

安全设计:
  - 全部连接使用 SQLite URI mode=ro(只读),物理杜绝写库
  - 表名/列名必须命中白名单(schema 校验),不支持任意 SQL 字符串
  - query 工具用结构化条件(列/操作符/参数),LIMIT 上限 500

覆盖库:
  attribution(主归因) / attribution_aux / event_calendar / factor_pit /
  erp / pit_shadow / droe_pit_final / factor_gpoa / factor_idvol / factor_pv / chan_signals

工具:
  list_tables(db)                列出库的表+列
  query(db, table, ...)          结构化查询(where 条件列表, AND 连接)
  get_attribution_history(code)  单票归因+验证+交易历史
  get_upcoming_events(days)      未来N日解禁/业绩预告
  get_pit_factor(code, asof)     指定时点 PIT 财务因子
  get_erp(days)                  最近 ERP 序列
  get_weight_config()            归因引擎当前维度权重
"""
import json
import sqlite3
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tulong-local")

DBS = {
    "attribution": r"D:\zcodesj\attribution\attribution.db",
    "attribution_aux": r"D:\zcodesj\attribution.db",
    "event_calendar": r"D:\zcodesj\event_calendar.db",
    "factor_pit": r"D:\zcodesj\factor_pit.db",
    "erp": r"D:\zcodesj\erp_history.db",
    "pit_shadow": r"D:\zcodesj\pit_shadow_track.db",
    "droe_pit_final": r"D:\zcodesj\droe_pit_final.db",
    "factor_gpoa": r"D:\zcodesj\factor_gpoa.db",
    "factor_idvol": r"D:\zcodesj\factor_idvol.db",
    "factor_pv": r"D:\zcodesj\factor_pv.db",
    "chan_signals": r"D:\zcodesj\chanlun_engine\chan_signals_strict.db",
}
# 白名单表(全部业务表;sqlite_sequence/sqlite_stat* 系统表禁止)
ALLOWED = {
    "attribution": {"attribution_monthly", "combo_experiment", "engine_attribution_log",
                    "engine_weight_config", "experience_library", "factor_performance",
                    "guard_precheck_log", "lhb_seat_library", "limit_down_risks",
                    "limit_up_patterns", "position_history", "r8_forward_tracking",
                    "signal_snapshots", "trade_attribution", "trade_records", "verification_trades"},
    "attribution_aux": {"monitor_events", "seal_snapshot"},
    "event_calendar": {"earnings_forecast", "refresh_log", "restricted_release", "restricted_release_daily"},
    "factor_pit": {"build_meta", "stock_factors_fundamental_pit_v2"},
    "erp": {"erp_daily"},
    "pit_shadow": {"shadow_lists"},
    "droe_pit_final": {"caliber_summary", "ic_monthly", "meta", "port_monthly", "quintile_monthly"},
    "factor_gpoa": {"factor_monthly", "ic_monthly", "meta", "nav_monthly", "quintile_monthly"},
    "factor_idvol": {"factor_price_volume"},
    "factor_pv": {"factor_price_volume"},
    "chan_signals": {"chan_signals_strict"},
}
OPS = {"=", "!=", ">", ">=", "<", "<=", "LIKE"}
MAX_LIMIT = 500
_schema_cache = {}


def _ro(db):
    path = DBS[db].replace("\\", "/")
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def _schema(db):
    """{table: set(cols)} 缓存。"""
    key = db
    if key not in _schema_cache:
        con = _ro(db)
        try:
            tabs = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            sc = {}
            for t in tabs:
                if t in ALLOWED.get(db, set()) and not t.startswith("sqlite_"):
                    cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
                    sc[t] = set(cols)
            _schema_cache[key] = sc
        finally:
            con.close()
    return _schema_cache[key]


def _check(db, table, columns):
    if db not in DBS:
        raise ValueError("未知库: %s (可选: %s)" % (db, ", ".join(DBS)))
    sc = _schema(db)
    if table not in sc:
        raise ValueError("库 %s 无表或不在白名单: %s" % (db, table))
    for c in columns:
        if c not in sc[table]:
            raise ValueError("列 %s 不存在于 %s.%s" % (c, db, table))


def _json(rows, cols):
    return json.dumps([dict(zip(cols, r)) for r in rows], ensure_ascii=False, default=str)


@mcp.tool()
def list_tables(db: str) -> str:
    """列出指定库的白名单表及各表列名。db 可选: attribution/attribution_aux/event_calendar/factor_pit/erp/pit_shadow/droe_pit_final/factor_gpoa/factor_idvol/factor_pv/chan_signals"""
    if db not in DBS:
        return json.dumps({"error": "未知库: %s" % db, "available": list(DBS)}, ensure_ascii=False)
    return json.dumps({t: sorted(cols) for t, cols in _schema(db).items()}, ensure_ascii=False)


@mcp.tool()
def query(db: str, table: str, columns: str = "*", where: list = None,
          order_by: str = "", limit: int = 50) -> str:
    """结构化只读查询。where: [{"col":"trade_date","op":">=","value":20260814}, ...] AND 连接;
    order_by: "col" 或 "col DESC"; limit ≤500。列名与表名必须命中白名单。"""
    limit = min(max(int(limit), 1), MAX_LIMIT)
    # 表名强制白名单校验(无论是否指定列)
    if table not in _schema(db):
        raise ValueError("库 %s 无表或不在白名单: %s" % (db, table))
    col_list = [c.strip() for c in columns.split(",") if c.strip()]
    if columns != "*":
        _check(db, table, col_list)
        cols_sql = ", ".join('"%s"' % c for c in col_list)
    else:
        cols_sql = "*"
        col_list = None
    where_sql, params = "", []
    for cond in (where or []):
        col, op = cond["col"], cond.get("op", "=").upper()
        if op not in OPS:
            raise ValueError("非法操作符: %s" % op)
        _check(db, table, [col])
        where_sql += (" AND " if where_sql else " WHERE ") + '"%s" %s ?' % (col, op)
        params.append(cond["value"])
    order_sql = ""
    if order_by:
        parts = order_by.split()
        _check(db, table, [parts[0]])
        order_sql = " ORDER BY \"%s\" %s" % (parts[0], parts[1].upper() if len(parts) > 1 else "ASC")
    sql = 'SELECT %s FROM "%s"%s%s LIMIT %d' % (cols_sql, table, where_sql, order_sql, limit)
    con = _ro(db)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return _json(rows, cols)
    finally:
        con.close()


@mcp.tool()
def get_attribution_history(code: str) -> str:
    """单票全史: 归因日志(engine_attribution_log) + 验证交易(verification_trades) + 交易记录(trade_records),按时间倒序。code 传6位码。"""
    out = {}
    con = _ro("attribution")
    try:
        q = ("SELECT event_date, name, event, main_attribution, confidence, ar, fwd3, fwd5 "
             "FROM engine_attribution_log WHERE code=? ORDER BY event_date DESC LIMIT 30")
        cur = con.execute(q, (code,))
        out["attribution_log"] = [dict(zip([d[0] for d in cur.description], r))
                                  for r in cur.fetchall()]
        q2 = ("SELECT trade_date, stock_name, strategy_id, entry_price, actual_ret_t1, actual_ret_t3, "
              "actual_ret_t5, result FROM verification_trades WHERE stock_code=? ORDER BY trade_date DESC LIMIT 30")
        cur = con.execute(q2, (code,))
        out["verification_trades"] = [dict(zip([d[0] for d in cur.description], r))
                                      for r in cur.fetchall()]
        q3 = ("SELECT trade_date, action, price, volume, amount, signal_type, market_env "
              "FROM trade_records WHERE code=? ORDER BY trade_date DESC LIMIT 30")
        cur = con.execute(q3, (code,))
        out["trade_records"] = [dict(zip([d[0] for d in cur.description], r))
                                for r in cur.fetchall()]
    finally:
        con.close()
    return json.dumps(out, ensure_ascii=False, default=str)


@mcp.tool()
def get_upcoming_events(days: int = 5) -> str:
    """未来 N 日解禁(restricted_release)与业绩预告(earnings_forecast)。days≤30。"""
    days = min(max(int(days), 1), 30)
    today = datetime.now()
    end = today + timedelta(days=days)
    # event_calendar 两表日期为 TEXT 'YYYY-MM-DD' 格式,必须用同格式比较
    # (紧凑格式YYYYMMDD与横线格式字符串比较恒不相等→静默假空)
    d0, d1 = today.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    out = {}
    con = _ro("event_calendar")
    try:
        try:
            q = ("SELECT * FROM restricted_release WHERE release_date BETWEEN ? AND ? "
                 "ORDER BY release_date LIMIT 30")
            cur = con.execute(q, (d0, d1))
            cols = [d[0] for d in cur.description]
            out["restricted_release"] = [dict(zip(cols, r)) for r in cur.fetchall()]
        except sqlite3.Error as e:
            out["restricted_release"] = {"error": str(e)}
        try:
            q2 = ("SELECT * FROM earnings_forecast WHERE announce_date BETWEEN ? AND ? "
                  "ORDER BY announce_date LIMIT 30")
            cur = con.execute(q2, (d0, d1))
            cols = [d[0] for d in cur.description]
            out["earnings_forecast"] = [dict(zip(cols, r)) for r in cur.fetchall()]
        except sqlite3.Error as e:
            out["earnings_forecast"] = {"error": str(e)}
    finally:
        con.close()
    return json.dumps(out, ensure_ascii=False, default=str)


@mcp.tool()
def get_pit_factor(code: str, asof: str = "", factors: str = "ep,bp,roe,eps") -> str:
    """指定时点(asof YYYYMMDD,默认最新)该股 PIT 财务因子(取自 stock_factors_fundamental_pit_v2,
    pit_avail_date ≤ asof 的最新一期,无前视)。factors 逗号分隔。"""
    f_list = [f.strip() for f in factors.split(",") if f.strip()]
    _check("factor_pit", "stock_factors_fundamental_pit_v2", f_list + ["pit_avail_date", "report_date", "code"])
    cols_sql = ", ".join('"%s"' % f for f in f_list)
    asof_sql = ""
    params = []
    if asof:
        asof_sql = "AND pit_avail_date <= ?"
        params.append(int(asof))
    sql = ("SELECT code, report_date, pit_avail_date, %s FROM stock_factors_fundamental_pit_v2 "
           "WHERE code=? %s ORDER BY report_date DESC LIMIT 1") % (cols_sql, asof_sql)
    con = _ro("factor_pit")
    try:
        cur = con.execute(sql, [code] + params)
        cols = [d[0] for d in cur.description]
        return _json(cur.fetchall(), cols)
    finally:
        con.close()


@mcp.tool()
def get_erp(days: int = 250) -> str:
    """最近 N 日 ERP(1/PE − 十债收益率)序列,按日期倒序。"""
    days = min(max(int(days), 1), 2000)
    con = _ro("erp")
    try:
        cur = con.execute("SELECT * FROM erp_daily ORDER BY date DESC LIMIT ?", (days,))
        cols = [d[0] for d in cur.description]
        return _json(cur.fetchall(), cols)
    finally:
        con.close()


@mcp.tool()
def get_weight_config() -> str:
    """归因引擎当前维度权重配置(engine_weight_config,月度校准后生效)。"""
    con = _ro("attribution")
    try:
        cur = con.execute("SELECT dim, weight, n, hit_rate, updated_at FROM engine_weight_config ORDER BY dim")
        cols = [d[0] for d in cur.description]
        return _json(cur.fetchall(), cols)
    finally:
        con.close()


if __name__ == "__main__":
    mcp.run()
