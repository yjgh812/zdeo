# -*- coding: utf-8 -*-
"""
backfill_lhb_20260813.py — 龙虎榜三表回填（东财 datacenter 直连）

目标表（fundamental_data.db）：
1. longhubang_daily    东财原生结构（序号递增，中文列名）
2. dragon_tiger_new    tushare top_list 结构（trade_date 无横线，ts_code 带后缀）
3. feature_longhubang  按 日+代码 聚合成 lhb_count（trade_date 为 datetime 字符串）

数据源：东财 datacenter RPT_DAILYBILLBOARD_DETAILSNEW（与 stock-sdk 同源，已实测 8/12 返回 57 条一致）
过滤：剔除可转债（代码 11/12/13 开头），保留 A股/北交所股票。
幂等：先 DELETE 目标日期再 INSERT，可安全重复运行。

用法：python backfill_lhb_20260813.py [start_date] [end_date]   默认 2026-08-11 2026-08-12
"""
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

FUND = r"D:\workbuddyspace\databases\fundamental_data.db"

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}


def fetch_lhb(date_str: str):
    """东财龙虎榜明细，date_str='2026-08-12'，返回全部行（自动翻页）"""
    filt = urllib.parse.quote(f"(TRADE_DATE<='{date_str}')(TRADE_DATE>='{date_str}')")
    rows, page = [], 1
    while True:
        url = (f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
               f"reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&filter={filt}"
               f"&pageSize=500&pageNumber={page}&sortColumns=BILLBOARD_NET_AMT&sortTypes=-1")
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        res = d.get("result") or {}
        data = res.get("data") or []
        rows.extend(data)
        if len(rows) >= (res.get("count") or 0) or not data:
            break
        page += 1
        time.sleep(0.3)
    return rows


def is_stock(code: str) -> bool:
    """剔除可转债（11/12/13开头），保留股票（60/68/00/30/8/4/920）"""
    return not code.startswith(("11", "12", "13"))


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2026-08-11"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-08-12"

    conn = sqlite3.connect(FUND, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    c = conn.cursor()

    # 序号续接
    seq = c.execute('SELECT MAX("序号") FROM longhubang_daily').fetchone()[0] or 0

    # 生成日期序列
    from datetime import datetime, timedelta
    d0 = datetime.strptime(start, "%Y-%m-%d")
    d1 = datetime.strptime(end, "%Y-%m-%d")
    days = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range((d1 - d0).days + 1)]

    for day in days:
        raw = fetch_lhb(day)
        rows = [r for r in raw if is_stock(r.get("SECURITY_CODE", ""))]
        n_skip = len(raw) - len(rows)
        print(f"[{day}] 东财返回 {len(raw)} 条, 剔除可转债 {n_skip} 条, 有效 {len(rows)} 条")
        if not rows:
            continue

        d_compact = day.replace("-", "")          # 20260812
        d_dash = day                               # 2026-08-12
        d_dt = day + " 00:00:00"                   # datetime 字符串

        # ---- 幂等：先清目标日 ----
        c.execute('DELETE FROM longhubang_daily WHERE trade_date=?', (d_dash,))
        c.execute('DELETE FROM dragon_tiger_new WHERE trade_date=?', (d_compact,))
        c.execute('DELETE FROM feature_longhubang WHERE trade_date=?', (d_dt,))

        # ---- 1. longhubang_daily ----
        lhb_recs = []
        for r in rows:
            seq += 1
            lhb_recs.append((
                seq,
                r.get("SECURITY_CODE"),
                r.get("SECURITY_NAME_ABBR"),
                d_dash,                                # 上榜日
                r.get("EXPLAIN"),                      # 解读
                r.get("CLOSE_PRICE"),                  # 收盘价
                r.get("CHANGE_RATE"),                  # 涨跌幅
                r.get("BILLBOARD_NET_AMT"),            # 龙虎榜净买额
                r.get("BILLBOARD_BUY_AMT"),            # 龙虎榜买入额
                r.get("BILLBOARD_SELL_AMT"),           # 龙虎榜卖出额
                r.get("BILLBOARD_DEAL_AMT"),           # 龙虎榜成交额
                r.get("ACCUM_AMOUNT"),                 # 市场总成交额
                r.get("DEAL_NET_RATIO"),               # 净买额占总成交比
                r.get("DEAL_AMOUNT_RATIO"),            # 成交额占总成交比
                r.get("TURNOVERRATE"),                 # 换手率
                r.get("FREE_MARKET_CAP"),              # 流通市值
                r.get("EXPLANATION"),                  # 上榜原因
                r.get("D1_CLOSE_ADJCHRATE"),
                r.get("D2_CLOSE_ADJCHRATE"),
                r.get("D5_CLOSE_ADJCHRATE"),
                r.get("D10_CLOSE_ADJCHRATE"),
                d_dash,                                # trade_date
            ))
        c.executemany(
            'INSERT INTO longhubang_daily ("序号","代码","名称","上榜日","解读","收盘价","涨跌幅",'
            '"龙虎榜净买额","龙虎榜买入额","龙虎榜卖出额","龙虎榜成交额","市场总成交额",'
            '"净买额占总成交比","成交额占总成交比","换手率","流通市值","上榜原因",'
            '"上榜后1日","上榜后2日","上榜后5日","上榜后10日",trade_date) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', lhb_recs)

        # ---- 2. dragon_tiger_new ----
        dt_recs = []
        for r in rows:
            dt_recs.append((
                d_compact,                             # trade_date
                r.get("SECUCODE"),                     # ts_code 带后缀
                r.get("SECURITY_NAME_ABBR"),           # name
                r.get("CLOSE_PRICE"),
                r.get("CHANGE_RATE"),                  # pct_change
                r.get("TURNOVERRATE"),                 # turnover_rate
                r.get("ACCUM_AMOUNT"),                 # amount 当日总成交额
                r.get("BILLBOARD_SELL_AMT"),           # l_sell
                r.get("BILLBOARD_BUY_AMT"),            # l_buy
                r.get("BILLBOARD_DEAL_AMT"),           # l_amount
                r.get("BILLBOARD_NET_AMT"),            # net_amount
                r.get("DEAL_NET_RATIO"),               # net_rate
                r.get("DEAL_AMOUNT_RATIO"),            # amount_rate
                r.get("FREE_MARKET_CAP"),              # float_values
                r.get("EXPLANATION"),                  # reason
            ))
        c.executemany(
            "INSERT INTO dragon_tiger_new (trade_date,ts_code,name,close,pct_change,turnover_rate,"
            "amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", dt_recs)

        # ---- 3. feature_longhubang（按日+代码聚合成上榜次数）----
        counts = {}
        for r in rows:
            code = r.get("SECURITY_CODE")
            counts[code] = counts.get(code, 0) + 1
        fl_recs = [(d_dt, code, n) for code, n in counts.items()]
        c.executemany(
            "INSERT INTO feature_longhubang (trade_date,ts_code_simple,lhb_count) VALUES (?,?,?)",
            fl_recs)

        conn.commit()
        print(f"  → longhubang_daily +{len(lhb_recs)} | dragon_tiger_new +{len(dt_recs)} | "
              f"feature_longhubang +{len(fl_recs)}")
        time.sleep(0.5)

    # ---- 验证 ----
    print("\n==== 验证 ====")
    for tbl, col in [("longhubang_daily", "trade_date"),
                     ("dragon_tiger_new", "trade_date"),
                     ("feature_longhubang", "trade_date")]:
        v = c.execute(f"SELECT MAX({col}) FROM {tbl}").fetchone()[0]
        n = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl}: MAX={v} ({n}行)")
    conn.close()


if __name__ == "__main__":
    main()
