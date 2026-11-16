# -*- coding: utf-8 -*-
"""
backfill_other_tables_20260808.py — 回补 fundamental/sw 库其余陈旧表

1. industry_kline_ths（sw_industry.db）：同花顺行业K线 2026-06-04 → 2026-08-07
   数据源：akshare stock_board_industry_index_ths（真实接口，已实测可用）
2. northbound_daily：2026-04-03 → 2026-08-07，仅补 hs300_close/hs300_chg
   （2024-08 起北向净买额已停止盘中披露，net 字段保持 NULL，与库内后期惯例一致）
   数据源：本地 dragon_quant.db index_daily（真实行情）
3. feature_market_sentiment：2026-04-07 → 2026-08-07，本地全量重算
   口径（与原表历史值存在口径差异，见 README 备注）：
     股票池 = stock_daily_hsj 当日全部 A 股（60/00/30/68/8/9/43 前缀）
     涨停/跌停 = 收盘价达到板块幅度（主板10%/创业科创20%/北交30%）
     avg_amplitude = mean((high-low)/close)
     limit_up_ratio = limit_up/total*100；sentiment_score = limit_up_ratio - limit_down_ratio
"""
import sqlite3
import time
from pathlib import Path

FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
SWDB = r"D:\workbuddyspace\databases\sw_industry.db"
MAIN = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
LOG = Path(r"D:\workbuddyspace\backfill_others_20260808.log")

START_SENT = "20260404"   # sentiment 现有到 2026-04-03
END = "20260811"


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def limit_pct(code: str) -> float:
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("8", "9", "43")):
        return 30.0
    return 10.0


def is_a_stock(code: str) -> bool:
    return code[:2] in ("60", "00", "30", "68") or code[0] in ("8", "9") or code.startswith("43")


def backfill_sentiment():
    log("== feature_market_sentiment 重算 ==")
    m = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    dates = [str(r[0]) for r in m.execute(
        "SELECT DISTINCT date FROM stock_daily_hsj WHERE date>=? AND date<=? ORDER BY date",
        (START_SENT, END)).fetchall()]
    f = sqlite3.connect(FUND, timeout=60)
    f.execute("PRAGMA busy_timeout=60000")
    f.execute("PRAGMA journal_mode=WAL")
    have = {r[0].replace("-", "") for r in f.execute(
        "SELECT trade_date FROM feature_market_sentiment").fetchall()}
    n = 0
    for d in dates:
        if d in have:
            continue
        rows = m.execute(
            "SELECT code, high, low, close FROM stock_daily_hsj WHERE date=?", (d,)).fetchall()
        # 昨收：上一交易日整表
        prev_date = m.execute("SELECT MAX(date) FROM stock_daily_hsj WHERE date<?", (d,)).fetchone()[0]
        if not prev_date:
            continue
        prev = dict(m.execute("SELECT code, close FROM stock_daily_hsj WHERE date=?",
                              (prev_date,)).fetchall())
        tot = lu = ld = 0
        amp_sum = 0.0
        for code, h, l, c in rows:
            if not is_a_stock(code):
                continue
            pc = prev.get(code)
            if not pc or pc <= 0 or c <= 0:
                continue
            tot += 1
            chg = (c - pc) / pc * 100
            lp = limit_pct(code)
            if chg >= lp - 0.1:
                lu += 1
            elif chg <= -lp + 0.1:
                ld += 1
            amp_sum += (h - l) / c
        if tot == 0:
            continue
        lu_r, ld_r = lu / tot * 100, ld / tot * 100
        f.execute("INSERT OR REPLACE INTO feature_market_sentiment VALUES (?,?,?,?,?,?,?,?)",
                  (f"{d[:4]}-{d[4:6]}-{d[6:]}", lu, ld, tot, amp_sum / tot,
                   lu_r, ld_r, lu_r - ld_r))
        n += 1
        if n % 10 == 0:
            f.commit()
            log(f"  sentiment {d} done ({n})")
    f.commit()
    f.close()
    m.close()
    log(f"sentiment 完成 {n} 天")


def backfill_northbound():
    log("== northbound_daily 补 hs300 ==")
    META = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
    m = sqlite3.connect(f"file:{META}?mode=ro", uri=True)
    rows = m.execute(
        "SELECT date, close FROM index_daily_meta WHERE code='000300' AND date>=? AND date<=? ORDER BY date",
        ("20260403", END)).fetchall()
    f = sqlite3.connect(FUND, timeout=60)
    f.execute("PRAGMA busy_timeout=60000")
    n = 0
    prev_close = None
    # 起始日前一收盘
    if rows:
        p = m.execute(
            "SELECT close FROM index_daily_meta WHERE date<? ORDER BY date DESC LIMIT 1",
            (rows[0][0],)).fetchone()
        prev_close = p[0] if p else None
    for d, close in rows:
        chg = (close - prev_close) / prev_close * 100 if prev_close else None
        f.execute("INSERT OR REPLACE INTO northbound_daily VALUES (?,?,?,?,?,?,?,?)",
                  (d, None, None, None, None, None, close, chg))
        prev_close = close
        n += 1
    f.commit()
    f.close()
    m.close()
    log(f"northbound 完成 {n} 天（net 字段按披露规则留 NULL）")


def backfill_industry_ths():
    log("== industry_kline_ths 回补 ==")
    import akshare as ak
    s = sqlite3.connect(f"file:{SWDB}?mode=ro", uri=True)
    names = [r[0] for r in s.execute("SELECT name FROM industry_names").fetchall()]
    s.close()
    f = sqlite3.connect(SWDB, timeout=60)
    f.execute("PRAGMA busy_timeout=60000")
    f.execute("PRAGMA journal_mode=WAL")
    total = 0
    for i, name in enumerate(names, 1):
        for attempt in range(3):
            try:
                df = ak.stock_board_industry_index_ths(symbol=name, start_date="20260808",
                                                       end_date="20260811")
                break
            except Exception as e:
                if attempt == 2:
                    log(f"  [跳过] {name}: {type(e).__name__} {str(e)[:80]}")
                    df = None
                time.sleep(1 + attempt)
        if df is None or df.empty:
            continue
        recs = [(name, str(r["日期"])[:10], r["开盘价"], r["最高价"], r["最低价"],
                 r["收盘价"], r["成交量"], r["成交额"]) for _, r in df.iterrows()]
        f.executemany(
            "INSERT OR REPLACE INTO industry_kline_ths"
            "(industry_name, trade_date, open, high, low, close, vol, amount)"
            " VALUES (?,?,?,?,?,?,?,?)", recs)
        total += len(recs)
        if i % 15 == 0:
            f.commit()
            log(f"  行业 {i}/{len(names)}  已写 {total} 行")
    f.commit()
    f.close()
    log(f"industry_kline_ths 完成 {total} 行")


if __name__ == "__main__":
    which = __import__("sys").argv[1] if len(__import__("sys").argv) > 1 else "all"
    if which in ("all", "sentiment"):
        backfill_sentiment()
    if which in ("all", "northbound"):
        backfill_northbound()
    if which in ("all", "ths"):
        backfill_industry_ths()
    log("全部完成")
