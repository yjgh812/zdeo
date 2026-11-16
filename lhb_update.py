#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
lhb_update.py — 龙虎榜家族日更唯一写入方（榜单三表 + 席位明细表）

库：D:\workbuddyspace\databases\fundamental_data.db
表：
  1. longhubang_daily    东财原生结构（中文列名，序号递增）
  2. dragon_tiger_new    tushare top_list 结构（trade_date 紧凑无横线，ts_code 带后缀）
  3. feature_longhubang  按 日+代码 聚合上榜次数（trade_date 为 datetime 字符串）
  4. lhb_seat_detail     席位级明细（日期×个股×席位×买卖净额，补席位净买额缺口）
源：东财 datacenter
  榜单  RPT_DAILYBILLBOARD_DETAILSNEW（过滤可转债，保留股票）
  席位  RPT_BILLBOARD_DAILYDETAILSBUY / RPT_BILLBOARD_DAILYDETAILSSELL（历史实测 2010 起）
分层：attribution.db lhb_seat_library（席位聚合档案）为独立消费层，不在此写。
历史：接管 backfill_lhb_20260813.py（榜单三表手动回填器）职责，此后日更与本脚本统一。
幂等：四表按 trade_date 窗口 DELETE 后 INSERT（lhb_seat_detail 另有主键兜底），
     可安全重复运行；默认增量自动从表内 MAX 次日补到今天。

用法：
  python lhb_update.py                                   # 增量（默认）
  python lhb_update.py --date 2026-09-07                 # 单日
  python lhb_update.py --start 2010-01-01 --end 2026-09-07  # 历史回填
  python lhb_update.py --check                           # 只读新鲜度检查
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
EM_URL = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName={report}"
          "&columns=ALL&filter={filt}&pageSize=500&pageNumber={page}"
          "&sortColumns={sort}&sortTypes={stype}&source=WEB&client=WEB")
RPT_BOARD = "RPT_DAILYBILLBOARD_DETAILSNEW"
RPT_SEAT = {"BUY": "RPT_BILLBOARD_DAILYDETAILSBUY", "SELL": "RPT_BILLBOARD_DAILYDETAILSSELL"}
STALE_WARN_DAYS = 5    # 落后≥5天 WARN（覆盖周末+长假尾）
STALE_FAIL_DAYS = 14   # 落后≥14天 exit 2（守卫口径，照 ml_scores staleness 先例）

SEAT_DDL = """
CREATE TABLE IF NOT EXISTS lhb_seat_detail (
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    security_code TEXT,
    security_name TEXT,
    side TEXT NOT NULL,
    seat_rank INTEGER NOT NULL,
    operate_dept_code TEXT,
    operate_dept_name TEXT,
    buy REAL, sell REAL, net REAL,
    total_buy_ratio REAL, total_sell_ratio REAL,
    explanation TEXT,
    change_rate REAL, close REAL, accum_amount REAL,
    PRIMARY KEY (trade_date, ts_code, side, seat_rank)
);
"""
SEAT_IDX = "CREATE INDEX IF NOT EXISTS idx_lhb_seat_code_date ON lhb_seat_detail(ts_code, trade_date)"


def log(msg):
    print(msg, flush=True)


def is_stock(code: str) -> bool:
    """剔除可转债（11/12/13 开头），保留股票（60/68/00/30/8/4/920）——先例口径"""
    return not code.startswith(("11", "12", "13"))


def fetch(report: str, day: str, sort_col: str, stype: str, retries: int = 2):
    """拉某日某报表全部行，自动翻页。无数据返回 []；重试耗尽抛异常。"""
    filt = urllib.parse.quote(f"(TRADE_DATE='{day}')")
    rows, page = [], 1
    while True:
        url = EM_URL.format(report=report, filt=filt, page=page,
                            sort=sort_col, stype=stype)
        last_err = None
        for i in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=25) as r:
                    d = json.loads(r.read().decode("utf-8"))
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(1.5 * (i + 1))
        if last_err is not None:
            raise RuntimeError(f"{report} {day} page{page}: {type(last_err).__name__} {last_err}")
        res = d.get("result")
        data = (res or {}).get("data") or []
        rows.extend(data)
        if not data or (res.get("count") or 0) <= len(rows):
            return rows
        page += 1
        time.sleep(0.3)


def seat_rows(day: str, side: str, raw, name_map):
    """席位原始行 → 表行；seat_rank 按 SECURITY_CODE 组内披露顺序编号。
    口径：BUY/SELL 两榜各按东财披露原样入库（对侧字段可能为 null），同席位完整
    买卖画像 = 两侧按 (trade_date, ts_code, operate_dept_name) 合并（ftshare 同视图）。"""
    out, counter, cur_code = [], 0, None
    for r in raw:
        code = r.get("SECURITY_CODE")
        if code != cur_code:
            cur_code, counter = code, 0
        counter += 1
        out.append((
            day, r.get("SECUCODE"), code, name_map.get(code), side, counter,
            r.get("OPERATEDEPT_CODE"), r.get("OPERATEDEPT_NAME"),
            r.get("BUY"), r.get("SELL"), r.get("NET"),
            r.get("TOTAL_BUYRIO"), r.get("TOTAL_SELLRIO"), r.get("EXPLANATION"),
            r.get("CHANGE_RATE"), r.get("CLOSE_PRICE"), r.get("ACCUM_AMOUNT"),
        ))
    return out


def update_day(conn, day: str):
    """更新单日四表：榜单+席位同源同事务，窗口 DELETE 后 INSERT。"""
    board_raw = [r for r in fetch(RPT_BOARD, day, "BILLBOARD_NET_AMT", "-1") if is_stock(r.get("SECURITY_CODE", ""))]
    buy_raw = fetch(RPT_SEAT["BUY"], day, "SECURITY_CODE", "1")
    sell_raw = fetch(RPT_SEAT["SELL"], day, "SECURITY_CODE", "1")
    if not board_raw and not buy_raw and not sell_raw:
        return "empty", 0
    name_map = {r.get("SECURITY_CODE"): r.get("SECURITY_NAME_ABBR") for r in board_raw}

    d_dash = day                      # 2026-09-07
    d_compact = day.replace("-", "")  # 20260907
    d_dt = day + " 00:00:00"

    seq = conn.execute('SELECT MAX("序号") FROM longhubang_daily').fetchone()[0] or 0

    lhb_recs, dt_recs, counts = [], [], {}
    for r in board_raw:
        code = r.get("SECURITY_CODE")
        counts[code] = counts.get(code, 0) + 1
        seq += 1
        lhb_recs.append((
            seq, code, r.get("SECURITY_NAME_ABBR"), d_dash, r.get("EXPLAIN"),
            r.get("CLOSE_PRICE"), r.get("CHANGE_RATE"),
            r.get("BILLBOARD_NET_AMT"), r.get("BILLBOARD_BUY_AMT"), r.get("BILLBOARD_SELL_AMT"),
            r.get("BILLBOARD_DEAL_AMT"), r.get("ACCUM_AMOUNT"),
            r.get("DEAL_NET_RATIO"), r.get("DEAL_AMOUNT_RATIO"), r.get("TURNOVERRATE"),
            r.get("FREE_MARKET_CAP"), r.get("EXPLANATION"),
            r.get("D1_CLOSE_ADJCHRATE"), r.get("D2_CLOSE_ADJCHRATE"),
            r.get("D5_CLOSE_ADJCHRATE"), r.get("D10_CLOSE_ADJCHRATE"), d_dash,
        ))
        dt_recs.append((
            d_compact, r.get("SECUCODE"), r.get("SECURITY_NAME_ABBR"), r.get("CLOSE_PRICE"),
            r.get("CHANGE_RATE"), r.get("TURNOVERRATE"), r.get("ACCUM_AMOUNT"),
            r.get("BILLBOARD_SELL_AMT"), r.get("BILLBOARD_BUY_AMT"), r.get("BILLBOARD_DEAL_AMT"),
            r.get("BILLBOARD_NET_AMT"), r.get("DEAL_NET_RATIO"), r.get("DEAL_AMOUNT_RATIO"),
            r.get("FREE_MARKET_CAP"), r.get("EXPLANATION"),
        ))

    c = conn.cursor()
    c.execute("DELETE FROM longhubang_daily WHERE trade_date=?", (d_dash,))
    c.execute("DELETE FROM dragon_tiger_new WHERE trade_date=?", (d_compact,))
    c.execute("DELETE FROM feature_longhubang WHERE trade_date=?", (d_dt,))
    c.execute("DELETE FROM lhb_seat_detail WHERE trade_date=?", (d_dash,))
    if lhb_recs:
        c.executemany(
            'INSERT INTO longhubang_daily ("序号","代码","名称","上榜日","解读","收盘价","涨跌幅",'
            '"龙虎榜净买额","龙虎榜买入额","龙虎榜卖出额","龙虎榜成交额","市场总成交额",'
            '"净买额占总成交比","成交额占总成交比","换手率","流通市值","上榜原因",'
            '"上榜后1日","上榜后2日","上榜后5日","上榜后10日",trade_date) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', lhb_recs)
        c.executemany(
            "INSERT INTO dragon_tiger_new (trade_date,ts_code,name,close,pct_change,turnover_rate,"
            "amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", dt_recs)
        c.executemany(
            "INSERT INTO feature_longhubang (trade_date,ts_code_simple,lhb_count) VALUES (?,?,?)",
            [(d_dt, code, n) for code, n in counts.items()])
    if buy_raw or sell_raw:
        c.executemany(
            "INSERT OR REPLACE INTO lhb_seat_detail "
            "(trade_date,ts_code,security_code,security_name,side,seat_rank,"
            "operate_dept_code,operate_dept_name,buy,sell,net,total_buy_ratio,total_sell_ratio,"
            "explanation,change_rate,close,accum_amount) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            seat_rows(day, "BUY", buy_raw, name_map) + seat_rows(day, "SELL", sell_raw, name_map))
    conn.commit()
    return "ok", len(lhb_recs) * 2 + len(buy_raw) + len(sell_raw)


def day_range(start: str, end: str):
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    d = d0
    while d <= d1:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def run_span(conn, start: str, end: str):
    n_days = n_empty = n_rows = 0
    failed = []
    t0 = time.time()
    for day in day_range(start, end):
        try:
            status, n = update_day(conn, day)
        except Exception as e:
            failed.append(day)
            log(f"[WARN] {day} 失败: {e}")
            time.sleep(1.0)
            continue
        n_days += 1
        if status == "empty":
            n_empty += 1
        else:
            n_rows += n
        if n_days % 100 == 0:
            log(f"  进度 {day} 累计{n_days}日/{n_rows}行 失败{len(failed)} {time.time()-t0:.0f}s")
        time.sleep(0.3)
    log(f"[{'OK' if not failed else 'WARN'}] 区间 {start}~{end}: 有数据日{max(0, n_days - n_empty)} "
        f"空日(非交易日/无上榜){n_empty} 累计行{n_rows} 失败日{len(failed)} 耗时{time.time()-t0:.0f}s")
    if failed:
        log(f"[WARN] 失败日期(重跑本命令自动补): {','.join(failed[:20])}"
            + ("..." if len(failed) > 20 else ""))
        return 1
    return 0


def cmd_check(conn):
    lags = []
    for tbl, col in [("longhubang_daily", "trade_date"), ("lhb_seat_detail", "trade_date")]:
        if not (str(tbl).isidentifier() and str(col).isidentifier()):
            raise ValueError(f"非法标识符: {tbl}/{col}")
        mx = conn.execute(f"SELECT MAX({col}) FROM {tbl}").fetchone()[0]
        if not mx:
            log(f"[WARN] {tbl} 为空表")
            lags.append(STALE_FAIL_DAYS)
            continue
        lag = (date.today() - datetime.strptime(mx, "%Y-%m-%d").date()).days
        lags.append(lag)
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        tag = "OK" if lag < STALE_WARN_DAYS else "WARN"
        log(f"[{tag}] {tbl} MAX={mx} 落后{lag}天 {n}行")
    return 2 if max(lags) >= STALE_FAIL_DAYS else 0


def main():
    ap = argparse.ArgumentParser(description="龙虎榜家族日更（榜单三表+席位明细）")
    ap.add_argument("--date", help="单日 YYYY-MM-DD")
    ap.add_argument("--start", help="区间起点 YYYY-MM-DD（回填）")
    ap.add_argument("--end", help="区间终点 YYYY-MM-DD（默认今天）")
    ap.add_argument("--check", action="store_true", help="只读新鲜度检查")
    args = ap.parse_args()

    conn = sqlite3.connect(FUND_DB, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute(SEAT_DDL)
    conn.execute(SEAT_IDX)
    conn.commit()

    if args.check:
        rc = cmd_check(conn)
        conn.close()
        return rc

    today = date.today().strftime("%Y-%m-%d")
    if args.date:
        rc = run_span(conn, args.date, args.date)
    elif args.start:
        rc = run_span(conn, args.start, args.end or today)
    else:
        mx = conn.execute("SELECT MAX(trade_date) FROM longhubang_daily").fetchone()[0]
        if not mx:
            log("[WARN] 空表拒绝盲增量，请显式指定 --start 做首次回填")
            conn.close()
            return 2
        nxt = mx  # 从 MAX 当日重拉(幂等): 防东财半日出榜被固化为半日数据, 次日链自动补全
        rc = run_span(conn, nxt, today)
    conn.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
