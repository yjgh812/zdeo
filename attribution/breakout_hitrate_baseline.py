#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
横盘突破命中率 20 日滚动基线（2026-08-18 落地，复盘线索③）

========== 用途 ==========
单日横盘突破命中率(8/17: 18/102=17.6%)需滚动基线校准战法适用窗口。
本脚本扫描最近 N 个交易日（默认20）每日涨停池，对每只涨停股做
20 日形态扫描（振幅≤25% 且 涨幅≤15% = 横盘型），统计：
  - 每日横盘型/横盘+放量突破 数量与占比
  - 20 日滚动均值/波动区间（作为战法有效基线）

========== 输出 ==========
D:/zcodesj/reports/breakout_baseline_YYYYMMDD.json
字段: daily[{date, zt_count, hz_count, hz_pct, breakout_count, breakout_pct}], baseline{avg_pct, std, min, max}

用法:
    python breakout_hitrate_baseline.py                # 近20交易日(自动取最近交易日)
    python breakout_hitrate_baseline.py --days 10      # 近10日
    python breakout_hitrate_baseline.py --date 20260821  # 显式指定截止交易日(周末/节假日防错位)
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

try:
    import requests
    _HAS_REQ = True
except ImportError:
    _HAS_REQ = False

HDRS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
OUT_DIR = r"D:/zcodesj/reports"
CACHE_DIR = r"D:/zcodesj/_cache"


def fetch(url):
    req = urllib.request.Request(url, headers=HDRS)
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(2)
    return None


def get_zt_pool(date: str):
    """当日涨停池。push2ex 支持历史日期(返回该日曾涨停股票), 直接返回 pool"""
    url = (f"https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
           f"&dpt=wz.ztzt&Pageindex=0&pagesize=400&sort=fbt%3Aasc&date={date}")
    d = fetch(url)
    if d and d.get("data") and d["data"].get("pool"):
        return d["data"]["pool"]
    return None


_QFQ_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"


def get_kline(code, n=40):
    """本地 qfq 库读取日K（零网络，最快最稳）。code 为纯6位;返回 [date,open,close,high,low,volume]"""
    import sqlite3
    try:
        conn = sqlite3.connect(_QFQ_DB)
        rows = conn.execute(
            "SELECT date, open, close, high, low, volume FROM stock_daily_meta_qfq "
            "WHERE code=? ORDER BY date DESC LIMIT ?", (code, n)).fetchall()
        conn.close()
    except Exception:
        return None
    if not rows:
        return None
    return [[str(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
            for r in reversed(rows)]


def is_hengpan_breakout(code, asof=None):
    """20日横盘+放量突破判定。K线截断至 asof 日(YYYYMMDD)为止, 用该日作为'当日'
    asof=None 时使用最新一根K线作为当日"""
    try:
        kl = get_kline(code, 40)
        if not kl:
            return None, None
        if asof:
            kl = [k for k in kl if k[0] <= asof]
        if len(kl) < 22:
            return None, None
        prev = kl[-22:-1]
        closes = [float(k[2]) for k in prev]
        highs = [float(k[3]) for k in prev]
        lows = [float(k[4]) for k in prev]
        vols = [float(k[5]) for k in prev]
        rng = (max(highs) - min(lows)) / min(lows) * 100
        chg = (closes[-1] - closes[0]) / closes[0] * 100
        t_vol = float(kl[-1][5])
        avg_vol = sum(vols) / len(vols)
        vr = t_vol / avg_vol if avg_vol > 0 else 0
        hz = rng <= 25 and chg <= 15
        bo = hz and vr >= 1.5
        return hz, bo
    except Exception:
        return None, None


def scan_day_codes(codes, asof, workers=6):
    """并发扫描当日代码列表, 返回 (hz_n, bo_n, ok)。future.result(timeout) 防悬挂"""
    hz_n = bo_n = ok = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(is_hengpan_breakout, c, asof): c for c in codes}
        for f in as_completed(futs):
            try:
                hz, bo = f.result(timeout=10)
            except Exception:
                continue
            if hz is None:
                continue
            ok += 1
            hz_n += 1 if hz else 0
            bo_n += 1 if bo else 0
    return hz_n, bo_n, ok


def prev_trade_dates(end_date: str, n: int):
    """end_date 向前取 n 个交易日。
    push2ex 历史涨停池仅覆盖近 ~15 个交易日, 连续 MISS_LIMIT 天无数据即停止(尽力而为)"""
    dt = datetime.strptime(end_date, "%Y%m%d")
    dates, cnt, miss = [], 0, 0
    while cnt < n:
        d4 = dt.strftime("%Y%m%d")
        pool = get_zt_pool(d4)
        if pool:
            dates.append(d4)
            cnt += 1
            miss = 0
        else:
            miss += 1
            if miss >= 5:  # 连续5天无数据 = 超出数据源历史覆盖
                break
        dt -= timedelta(days=1)
        time.sleep(0.05)
    return dates


def latest_trade_date() -> str:
    """本地 qfq 库取最近交易日(YYYYMMDD)。比日历日推导可靠——周末/节假日日历日非交易日。"""
    import sqlite3
    try:
        conn = sqlite3.connect(_QFQ_DB)
        d = conn.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        conn.close()
        return str(d) if d else None
    except Exception:
        return None


def main(days: int = 20, date: str | None = None):
    # end 取最近完整交易日：优先 --date 显式传，否则 qfq 库最近交易日（勿用日历日 now-1day，
    # 周末/节假日会落到非交易日，导致文件名与 daily_trade_plan 候选日期(前一交易日)不匹配）
    if date:
        end = str(date).replace("-", "")
    else:
        end = latest_trade_date()
        if not end:
            # 兜底：回到原日历日逻辑
            now = datetime.now()
            end = (now - timedelta(days=1)).strftime("%Y%m%d") if now.hour < 16 else now.strftime("%Y%m%d")
    dates = prev_trade_dates(end, days)
    print(f"扫描 {len(dates)} 个交易日(截至{end}): {dates[0]} ~ {dates[-1]}", flush=True)

    daily = []
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"breakout_daily_{end}.json")
    done = {}
    if os.path.exists(cache_path):
        try:
            done = {r["date"]: r for r in json.load(open(cache_path, encoding="utf-8"))}
            print(f"缓存续跑: 已完成 {len(done)} 天", flush=True)
        except Exception:
            pass
    for di, date in enumerate(dates):
        if date in done:
            daily.append(done[date])
            continue
        pool = get_zt_pool(date)
        if not pool:
            continue
        codes = [p["c"] for p in pool if not p["c"].startswith(("688", "8", "4"))]
        hz_n, bo_n, ok = scan_day_codes(codes, asof=date)
        rec = {
            "date": date,
            "zt_count": len(pool),
            "valid_count": ok,
            "hz_count": hz_n,
            "hz_pct": round(hz_n / ok * 100, 1) if ok else 0,
            "breakout_count": bo_n,
            "breakout_pct": round(bo_n / ok * 100, 1) if ok else 0,
        }
        daily.append(rec)
        done[date] = rec
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(list(done.values()), f, ensure_ascii=False)
        print(f"[{di+1}/{len(dates)}] {date}: 涨停{rec['zt_count']} 横盘{rec['hz_count']}({rec['hz_pct']}%) "
              f"突破{rec['breakout_count']}({rec['breakout_pct']}%)", flush=True)

    pcts = [d["breakout_pct"] for d in daily if d["valid_count"] > 0]
    baseline = None
    if pcts:
        avg = sum(pcts) / len(pcts)
        var = sum((p - avg) ** 2 for p in pcts) / len(pcts)
        baseline = {
            "days": len(pcts),
            "avg_breakout_pct": round(avg, 1),
            "std": round(var ** 0.5, 1),
            "min": min(pcts),
            "max": max(pcts),
            "recent": pcts[-5:],
        }
    out = {"end_date": end, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "method": "20日形态扫描: 振幅<=25%且涨幅<=15%=横盘型; 横盘且量比>=1.5=放量突破",
           "daily": daily, "baseline": baseline}
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"breakout_baseline_{end}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("baseline:", baseline)
    print("saved:", path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20)
    ap.add_argument("--date", type=str, default=None, help="截止交易日 YYYYMMDD（默认自动取最近交易日）")
    a = ap.parse_args()
    main(a.days, a.date)