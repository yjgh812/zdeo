# -*- coding: utf-8 -*-
"""
report_generator.py — 屠龙系统报表统一导出入口 (2026-08-15 M6+M7 补缺)
=====================================================================
背景: 交易/因子/回测结果只有 md/csv, openpyxl 已装但全盘零调用;月度/年度
统计与 Excel 出口缺失。本脚本提供统一入口,产出 .xlsx 三件套。

子命令:
  python report_generator.py trades [--year 2026] [--out xxx.xlsx]
      交易明细(attribution.db trade_records 全部/按年) + 年度统计 + 信号源胜率(verification_trades)
  python report_generator.py factors [--code 002582|--date 20260331] [--limit 500] [--out ...]
      factor_pit 因子表导出(PIT口径,可按股/按报告期过滤)
  python report_generator.py nav --file <nav.pkl|nav.csv> [--out ...]
      净值曲线数据 + 年度收益 + 绩效指标(年化/回撤/夏普)
  python report_generator.py list
      列出 reports/export 下的导出文件

默认输出: D:\\zcodesj\\reports\\export\\<type>_<YYYYMMDD_HHMM>.xlsx
"""
import json
import os
import pickle
import sqlite3
import sys
from datetime import datetime

import pandas as pd

BASE = "D:/zcodesj"
ATTR_DB = os.path.join(BASE, "attribution", "attribution.db")
EXPORT_DIR = os.path.join(BASE, "reports", "export")


def out_path(kind, custom=None):
    os.makedirs(EXPORT_DIR, exist_ok=True)
    if custom:
        return custom if custom.lower().endswith(".xlsx") else custom + ".xlsx"
    return os.path.join(EXPORT_DIR, "%s_%s.xlsx" % (kind, datetime.now().strftime("%Y%m%d_%H%M")))


def _read_trades(year=None):
    con = sqlite3.connect(ATTR_DB)
    try:
        sql = ("SELECT id, code, name, trade_date, action, price, volume, amount, "
               "signal_type, strategy_version, market_env, sector, list_membership, "
               "created_at FROM trade_records")
        params = []
        if year:
            sql += " WHERE trade_date LIKE ?"
            params.append("%d%%" % year)
        sql += " ORDER BY trade_date"
        df = pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()
    return df


def _read_verification():
    con = sqlite3.connect(ATTR_DB)
    try:
        df = pd.read_sql_query(
            "SELECT trade_date, stock_code, stock_name, strategy_id, entry_price, "
            "actual_ret_t1, actual_ret_t3, actual_ret_t5, result FROM verification_trades",
            con)
    finally:
        con.close()
    return df


def cmd_trades(year, custom):
    df = _read_trades(year)
    if df.empty:
        print("trade_records 为空(或该年无记录)")
        return 1
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    # 年度统计
    df["yr"] = df["trade_date"].astype(str).str[:4]
    yr = df.groupby("yr").agg(
        笔数=("id", "count"),
        买入笔数=("action", lambda s: (s == "buy").sum()),
        卖出笔数=("action", lambda s: (s == "sell").sum()),
        买入金额=("amount", lambda s: s[s.index.isin(df.index[df["action"] == "buy"])].sum()),
    ).reset_index()
    # 信号源胜率(T+3口径)
    v = _read_verification()
    sig = pd.DataFrame()
    if not v.empty:
        v["t3"] = pd.to_numeric(v["actual_ret_t3"], errors="coerce")
        g = v.dropna(subset=["t3"]).groupby("strategy_id")
        sig = g.agg(
            闭合样本=("t3", "count"),
            T3胜率=("t3", lambda s: (s > 0).mean()),
            T3均值=("t3", "mean"),
            最早日期=("trade_date", "min"),
            最晚日期=("trade_date", "max"),
        ).reset_index()
        sig["T3胜率"] = (sig["T3胜率"] * 100).round(1)
        sig["T3均值"] = (sig["T3均值"] * 100).round(2)
    path = out_path("trades", custom)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.drop(columns=["yr"]).to_excel(w, sheet_name="交易明细", index=False)
        yr.to_excel(w, sheet_name="年度统计", index=False)
        if not sig.empty:
            sig.to_excel(w, sheet_name="信号源胜率", index=False)
    print("已导出:", path, "| 明细%d行 年度%d行 信号源%d行" % (len(df), len(yr), len(sig)))
    return 0


def cmd_factors(code, date, limit, custom):
    con = sqlite3.connect(os.path.join(BASE, "factor_pit.db"))
    try:
        sql = "SELECT * FROM stock_factors_fundamental_pit_v2"
        conds, params = [], []
        if code:
            conds.append("code=?")
            params.append(code)
        if date:
            conds.append("report_date=?")
            params.append(str(date))
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY code, report_date DESC LIMIT ?"
        params.append(min(max(limit, 1), 5000))
        df = pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()
    if df.empty:
        print("无匹配因子数据")
        return 1
    path = out_path("factors", custom)
    df.to_excel(path, index=False)
    print("已导出:", path, "| %d 行" % len(df))
    return 0


def _nav_series(obj):
    """把 pkl/csv 内容规整为 [(名称, Series), ...]。"""
    if isinstance(obj, dict):
        return [(k, v) for k, v in obj.items() if isinstance(v, pd.Series)]
    if isinstance(obj, pd.DataFrame):
        col = [c for c in obj.columns if c.lower() in ("nav", "net", "value", "close")]
        s = obj[col[0]] if col else obj.iloc[:, 0]
        if "date" in obj.columns:
            s = s.copy()
            s.index = pd.to_datetime(obj["date"])
        return [("nav", s)]
    if isinstance(obj, pd.Series):
        return [("nav", obj)]
    return []


def _sheet_name(name):
    import re
    s = re.sub(r'[\\/*?:\[\]]', '_', str(name))[:28]
    return s or "sheet"


def cmd_nav(nav_file, custom):
    if not os.path.exists(nav_file):
        print("净值文件不存在:", nav_file)
        return 1
    with open(nav_file, "rb") as f:
        obj = pickle.load(f)
    series_list = _nav_series(obj)
    if not series_list:
        print("无法识别的净值结构:", type(obj))
        return 1

    path = out_path("nav", custom)
    perf_rows = []
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for name, raw in series_list:
            nav = pd.to_numeric(raw, errors="coerce").dropna().astype(float)
            ret = nav.pct_change().dropna()
            total = nav.iloc[-1] / nav.iloc[0] - 1
            years = (nav.index[-1] - nav.index[0]).days / 365.25
            cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else None
            dd = (nav / nav.cummax() - 1).min()
            sharpe = None
            try:
                import empyrical
                sharpe = empyrical.sharpe_ratio(ret, annualization=252)
            except Exception:
                pass
            ann = ret.resample("YE").apply(lambda s: (1 + s).prod() - 1) if not ret.empty else pd.Series(dtype=float)
            sheet = _sheet_name(name)
            pd.DataFrame({"date": nav.index.strftime("%Y-%m-%d"), "nav": nav.values}).to_excel(
                w, sheet_name=sheet, index=False)
            ann.reset_index().rename(columns={"index": "年度", 0: "年度收益"}).to_excel(
                w, sheet_name=sheet + "_年", index=False)
            perf_rows.append({"曲线": name, "累计收益": total, "年化CAGR": cagr,
                              "最大回撤": dd, "夏普(252)": sharpe, "年数": round(years, 2)})
        if perf_rows:
            pd.DataFrame(perf_rows).to_excel(w, sheet_name="绩效汇总", index=False)
    print("已导出:", path, "| 曲线 %d 条: %s" % (len(series_list), ", ".join(str(n) for n, _ in series_list)))
    return 0


def cmd_list():
    if not os.path.isdir(EXPORT_DIR):
        print("导出目录为空")
        return 0
    files = sorted(os.listdir(EXPORT_DIR))
    for f in files:
        p = os.path.join(EXPORT_DIR, f)
        print("%10.1fKB  %s" % (os.path.getsize(p) / 1024, f))
    return 0


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]

    def opt(name, default=None):
        if name in args:
            i = args.index(name)
            return args[i + 1] if i + 1 < len(args) else default
        return default

    if cmd == "trades":
        return cmd_trades(int(opt("--year")) if opt("--year") else None, opt("--out"))
    if cmd == "factors":
        return cmd_factors(opt("--code"), opt("--date"),
                           int(opt("--limit", 500)), opt("--out"))
    if cmd == "nav":
        return cmd_nav(opt("--file"), opt("--out"))
    if cmd == "list":
        return cmd_list()
    print("未知子命令:", cmd)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
