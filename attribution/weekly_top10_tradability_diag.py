r"""weekly_top10_tradability_diag.py — T2(周频Top10)可成交性诊断。
假说: +200%年化系涨停封死不可成交的延续收益幻象(选股日收盘计量=假设收盘价能成交)。
分层: 选股日收盘封涨停 / 未封但次日开盘涨停(无法成交) / 完全可成交。
"""
import sqlite3
import sys
import time
from collections import defaultdict
from itertools import groupby

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ST_DB = r"D:\zcodesj\data\st_history.db"
START_DATA, SIG_END = "20100101", "20261113"
SPLIT = 20160000
PERIOD = 5
TOPN = 10
EXCL = ("4", "8", "9", "68")


def st_loader():
    con = sqlite3.connect("file:%s?mode=ro" % ST_DB, uri=True)
    cache = {}

    def state(code, date):
        if code not in cache:
            pfx = "sh" if code.startswith("6") else "sz"
            rows = con.execute("SELECT date,is_st FROM st_daily WHERE code=? ORDER BY date",
                               (pfx + "." + code,)).fetchall()
            cache[code] = rows
        rows = cache[code]
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] <= date:
                lo = mid + 1
            else:
                hi = mid
        return rows[lo - 1][1] if lo > 0 else 0
    return state


def main():
    t0 = time.time()
    m = sqlite3.connect(r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro", uri=True)
    irows = m.execute("SELECT date,close FROM index_daily_meta WHERE code='000001' AND date>=? ORDER BY date",
                      (START_DATA,)).fetchall()
    m.close()
    idt = [int(r[0]) for r in irows]
    icl = [r[1] for r in irows]
    idx5 = {idt[i]: icl[i] / icl[i - 5] - 1.0 for i in range(5, len(idt))}

    is_st = st_loader()
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    bd = sorted({int(r[0]) for r in con.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? AND date<=?", (START_DATA, SIG_END))})
    bidx = {d: i for i, d in enumerate(bd)}
    sel_idx = list(range(24, len(bd) - PERIOD, PERIOD))
    sel_set = set(sel_idx)
    sel_dates = [bd[i] for i in sel_idx]

    # 全池5日基准(近似: 用等权全池算对照亦可, 这里诊断只需分层收益, 与主测同法)
    # 直接逐码扫描, 分层记录
    grp_stats = defaultdict(lambda: [0.0, 0])   # (层, 年) -> [sum, cnt]  层∈sealed/gapup_open/tradable
    grp_stats_all = defaultdict(lambda: [0.0, 0])
    nsig = 0
    cur = con.execute("SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        if code.startswith(EXCL):
            continue
        lp = 0.20 if code.startswith(("300", "301")) else 0.10
        closes = [(int(r[1]), r[4]) for r in grp]
        opens = {int(r[1]): r[2] for r in grp}
        opens = {int(r[1]): r[2] for r in grp}
        for i in range(5, len(closes)):
            d, c = closes[i]
            bi = bidx.get(d)
            if bi not in sel_set:
                continue
            if c is None or c <= 0:
                continue
            if not (2.0 <= c <= 30.0):
                continue
            if d >= SPLIT and is_st(code, d):
                continue
            p5 = closes[i - 5][1]
            ir = idx5.get(d)
            if p5 is None or p5 <= 0 or ir is None:
                continue
            excess = c / p5 - 1.0 - ir
            # 仅复现Top10截断: 需要全截面, 改为两遍扫描太重 -> 用超额阈值近似? 不可。
            # 正确做法: 先收集截面再选TopN。改为收集后统一处理。
            panel.setdefault(d, []).append((code, excess, c, opens.get(d + 1) if (d + 1) in opens else None,
                                            closes[i + 1][1] if i + 1 < len(closes) else None,
                                            closes[i + PERIOD][1] if i + PERIOD < len(closes) else None))
    # Top10选择 + 分层
    sealed_n = gap_n = trad_n = 0
    sealed_r = defaultdict(list)
    gap_r = defaultdict(list)
    trad_r = defaultdict(list)
    for d, rows in panel.items():
        if len(rows) < TOPN:
            continue
        rows_sorted = sorted(rows, key=lambda r: r[1], reverse=True)
        sel = rows_sorted[:TOPN]
        y = str(d)[:4]
        for (code, exc, c_sel, o_next, c_next1, c_next5) in sel:
            if o_next is None or c_next5 is None or c_next1 is None:
                continue
            # 选股日是否封涨停: 用次日开盘>=选股日收盘*(1+lp)-0.005 近似无法判当日封板,
            # 用当日收益>=阈值近似封板判定: 5日超额极高且次日开盘继续>=+9.5% =>
            # 判据改为: o_next >= c_sel*(1+lp)*0.995 => 次日开盘即涨停(无法以次日开盘买入)
            # 而本口径隐含以选股日收盘买入: 若选股日收盘本身封板(c_sel>=日内涨停价)无法判(缺high) -> 用次日开盘涨停+选股日涨停近似
            sealed_like = o_next is not None and o_next >= c_sel * (1 + lp) * 0.995
            ret5 = c_next5 / c_sel - 1.0  # 隐含选股日收盘买入
            nsig += 1
            if sealed_like:
                grp = sealed_r
                sealed_n += 1
            elif o_next is not None and o_next >= c_sel * (1 + lp * 0.5):
                grp = gap_r
                gap_n += 1
            else:
                grp = trad_r
                trad_n += 1
            grp[y].append(ret5 * 100)
    print("样本%d | 选股日封板/次日开盘涨停:%d | 高开:%d | 可成交:%d" % (nsig, sealed_n, gap_n, trad_n))

    def seg(grp, lo, hi):
        ylo, yhi = lo // 10000, hi // 10000
        xs = [x for y, v in grp.items() if ylo <= int(y) < yhi for x in v]
        if len(xs) < 26:
            return None
        mu = float(np.mean(xs))
        sd = float(np.std(xs, ddof=1))
        t = mu / (sd / len(xs) ** 0.5) if sd > 0 else None
        return round(mu, 3), None if t is None else round(t, 2), len(xs)

    for name, grp in (("封板/开盘涨停(不可买)", sealed_r), ("大幅高开", gap_r), ("可成交", trad_r)):
        p = seg(grp, SPLIT, 20990000)
        h = seg(grp, 20100000, SPLIT)
        print("%s: 主窗周均=%s%% | 留出窗=%s" % (name, p, h))
    print("elapsed %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    panel = defaultdict(list)
    main()
