# -*- coding: utf-8 -*-
"""trade_day_guard.py — A股交易日判定共用守卫 (2026-09-28, 中秋假ERROR噪音根治)

背景: 9/25中秋休市日三个schtasks任务(Sentinel_1430/GateWeeklyWinrate/TulongMissedCost)
以rc=2/-1073741510失败, 巡检记ERROR=假期噪音。本工具给任务脚本提供休市日优雅跳过判定。

数据源: tushare trade_cal(TUSHARE_TOKEN: env优先→config/tushare_token.txt)。
缓存: data/trade_cal_cache.json(90日窗口, 24h刷新)。境内网络失败→返回None=fail-open
(调用方按原逻辑执行: 止损/守卫类宁可误报不可漏报, 本工具绝不阻断交易相关检查)。

用法:
    from trade_day_guard import is_trading_day
    td = is_trading_day()            # 默认今天YYYYMMDD
    if td is False:
        print("[trade-day-guard] 休市日, 优雅跳过"); sys.exit(0)
    # td is None → 日历不可用, 不跳过(继续原逻辑)
"""
import json
import os
from datetime import date as _date, datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "trade_cal_cache.json")
TOKEN_FILES = [os.path.join(HERE, "config", "tushare_token.txt")]


def _token():
    t = os.environ.get("TUSHARE_TOKEN", "").strip()
    if t:
        return t
    for p in TOKEN_FILES:
        if os.path.exists(p):
            return open(p, encoding="utf-8").read().strip()
    return ""


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cal):
    tmp = {"fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "cal": cal}
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(tmp, f, ensure_ascii=False)


def is_trading_day(day=None):
    """返回 True(交易日)/False(休市日)/None(日历不可用, 调用方fail-open)。"""
    d = day or _date.today().strftime("%Y%m%d")
    d = str(d)[:10].replace("-", "")
    if datetime.strptime(d, "%Y%m%d").weekday() >= 5:
        return False  # 周末离线判定, 无需网络
    cal = _load_cache()
    fetched = cal.get("fetched_at", "")
    fresh = fetched[:10] == _date.today().strftime("%Y-%m-%d") and cal.get("cal")
    if not fresh:
        items = _fetch_tushare() or _fetch_baostock()
        if items:
            m = {str(i[0]): int(i[1]) for i in items}
            _save_cache(m)          # 传映射本身(文件结构={"fetched_at","cal":映射})
            cal["cal"] = m
    m = cal.get("cal", {}).get(d)
    if m is None:
        return None
    return m == 1


def _fetch_tushare():
    """tushare trade_cal; 失败/空结果返回None(不污染缓存)。"""
    tok = _token()
    if not tok:
        return None
    for _ in range(2):
        try:
            end = (_date.today() + timedelta(days=1)).strftime("%Y%m%d")
            start = (_date.today() - timedelta(days=90)).strftime("%Y%m%d")
            r = requests.post("http://api.tushare.pro", timeout=10, json={
                "api_name": "trade_cal", "token": tok,
                "params": {"exchange": "SSE", "start_date": start, "end_date": end},
                "fields": "cal_date,is_open"})
            items = (r.json().get("data") or {}).get("items") or []
            if items:
                return items
        except Exception:
            pass
    return None


def _fetch_baostock():
    """baostock query_trade_dates 备源; 失败返回None。
    惯例=无参bs.login()(与baostock_mcp.py/日更任务一致, 匿名即可查trade_dates)。"""
    try:
        import baostock as bs
        lg = bs.login()
        try:
            if getattr(lg, "error_code", "1") != "0":
                return None
            end = (_date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
            start = (_date.today() - timedelta(days=90)).strftime("%Y-%m-%d")
            rs = bs.query_trade_dates(start, end)
            items = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()  # get_row_data自带游标推进, 每轮仅可调用一次
                items.append([row[0].replace("-", ""), 1 if row[1] == "1" else 0])
            return items or None
        finally:
            bs.logout()
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else None
    print(is_trading_day(d))
