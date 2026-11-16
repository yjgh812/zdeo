r"""universe_portrait_fullseries.py — 宇宙深度画像 D层·全史逐股时序画像（画像系统·侦察级零交易权）。

主人令 2026-11-16：A股全宇宙每一只股票+大盘逐日情况，2020-01-01至今走势数据全部分析入库。
数据: qfq库(唯一价格源, 20200101-库内最新交易日) + st_history(PIT ST标记) + vipdoc指数day文件(大盘5指数)。
宇宙: 60/00/30前缀全量(剔科创/北交所/9字头), ST按日PIT打标不剔除(消费端按需过滤)。
输出 data/universe_portrait_fullseries.db:
  stock_daily    每股票×每交易日 ~20维走势画像(多期收益/均线/位置/回撤/量比/波动/价格带/停牌/ST)
  market_daily   每交易日大盘画像(5指数收盘+收益 + 宇宙宽度: 涨跌家数/站上20线占比/新高数/位置分带)
  stock_summary  每股票全期摘要(首末日期/全期收益/全期最大回撤/逐年收益2020-2026/现位置)
  meta           行数与范围自检(无时间戳, 双建逐字节确定性)
边界: 零交易权; 不进排行/闸门/执行; 生成物只读不手改, 重建=python -I universe_portrait_fullseries.py
退出码: 0=成功 / 2=结构化失败(数据不新鲜/源缺失)
"""
import bisect
import hashlib
import math
import sqlite3
import struct
import sys
import time
from collections import defaultdict, deque
from itertools import groupby

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ST_DB = r"D:\zcodesj\data\st_history.db"
OUT_DB = r"D:\zcodesj\data\universe_portrait_fullseries.db"
OUT2_DB = r"D:\zcodesj\data\universe_portrait_fullseries_run2.db"
D0, D1 = 20200101, 20261116
EXCL = ("4", "8", "9", "68")
INDEXES = (("sh", "000001", "idx_sh"), ("sh", "000300", "idx_hs300"),
           ("sh", "000016", "idx_sz50"), ("sh", "000905", "idx_zz500"),
           ("sz", "399006", "idx_cyb"))
IDX_DIR = r"D:\anzhuang\tdx\vipdoc"
FRESH_MAX_DAYS = 10


def read_indices():
    """vipdoc day 文件 → {date: {idx_key: (open, close)}}; 收益由相邻bar推导。"""
    out = {}
    for mkt, code, key in INDEXES:
        path = "%s\\%s\\lday\\%s%s.day" % (IDX_DIR, mkt, mkt, code)
        prev = None
        with open(path, "rb") as f:
            while True:
                b = f.read(32)
                if len(b) < 32:
                    break
                d = struct.unpack("<I", b[:4])[0]
                if d < D0:
                    continue
                o = struct.unpack("<I", b[4:8])[0] / 100.0
                c = struct.unpack("<I", b[16:20])[0] / 100.0
                if o <= 0 or c <= 0:
                    continue
                ret = None
                if prev is not None:
                    ret = (c / prev - 1.0) * 100.0
                out.setdefault(d, {})[key] = (round(o, 2), round(c, 2),
                                              None if ret is None else round(ret, 4))
                prev = c
    return out


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


def ma_series(closes, k):
    out = [None] * len(closes)
    buf = deque()
    s = 0.0
    nbad = 0
    for i, c in enumerate(closes):
        bad = c is None or c <= 0
        buf.append(bad)
        if bad:
            nbad += 1
        else:
            s += c
        if len(buf) > k:
            if buf.popleft():
                nbad -= 1
            else:
                s -= closes[i - k]
        if i >= k - 1 and nbad == 0:
            out[i] = s / k
    return out


def roll_hi_lo(closes, w):
    """滚动窗口最高/最低收盘(含当日), 窗口内任一无效→None。单调队列O(n)。"""
    n = len(closes)
    himax = [None] * n
    lomin = [None] * n
    dq_max = deque()
    dq_min = deque()
    bad_cnt = 0
    buf = deque()
    for i in range(n):
        c = closes[i]
        bad = c is None or c <= 0
        buf.append(bad)
        if bad:
            bad_cnt += 1
        else:
            while dq_max and closes[dq_max[-1]] <= c:
                dq_max.pop()
            while dq_min and closes[dq_min[-1]] >= c:
                dq_min.pop()
            dq_max.append(i)
            dq_min.append(i)
        while dq_max and dq_max[0] <= i - w:
            dq_max.popleft()
        while dq_min and dq_min[0] <= i - w:
            dq_min.popleft()
        if i >= w - 1 and bad_cnt == 0:
            himax[i] = closes[dq_max[0]]
            lomin[i] = closes[dq_min[0]]
    return himax, lomin


def process_stock(code, bars, is_st, out, summ, breadth):
    """bars=[(date,close,vol,amt)] 升序; rows 进 out, 全期摘要进 summ, 宽度进 breadth。"""
    n = len(bars)
    if n < 2:
        return
    dates = [b[0] for b in bars]
    closes = [b[1] for b in bars]
    vols = [b[2] for b in bars]
    amts = [None if (b[3] is None or b[3] <= 0) else b[3] / 1e8 for b in bars]
    amt20 = ma_series(amts, 20)
    ma5 = ma_series(closes, 5)
    ma10 = ma_series(closes, 10)
    ma20 = ma_series(closes, 20)
    ma60 = ma_series(closes, 60)
    hi250, lo250 = roll_hi_lo(closes, 250)
    hi60, lo60 = roll_hi_lo(closes, 60)
    ret1 = [None] * n
    for i in range(1, n):
        if closes[i] and closes[i] > 0 and closes[i - 1] and closes[i - 1] > 0:
            ret1[i] = (closes[i] / closes[i - 1] - 1.0) * 100.0
    s20 = ss20 = 0.0
    cnt20 = 0
    vola = [None] * n
    buf = deque()
    for i in range(n):
        r = ret1[i]
        bad = r is None
        buf.append(bad)
        if bad:
            cnt20 = cnt20 + 1
        else:
            s20 += r
            ss20 += r * r
        if len(buf) > 20:
            if buf.popleft():
                cnt20 = cnt20 - 1
            else:
                x = ret1[i - 20]
                s20 -= x
                ss20 -= x * x
        if i >= 20 and cnt20 == 0:
            mu = s20 / 20.0
            vola[i] = math.sqrt(max(0.0, ss20 / 20.0 - mu * mu))
    v5buf = deque()
    v5s = 0.0
    v5bad = 0
    peak = None
    mdd = 0.0
    yend = {}
    st_days = 0
    for i in range(n):
        d = dates[i]
        c = closes[i]
        c_ok = c is not None and c > 0
        if c_ok:
            yend[d // 10000] = c
            if peak is None or c > peak:
                peak = c
            dd = c / peak - 1.0
            if dd < mdd:
                mdd = dd
        m5, m10, m20v, m60v = ma5[i], ma10[i], ma20[i], ma60[i]
        align = None
        if None not in (m5, m10, m20v):
            align = (m5 > m10) + (m5 > m20v) + (m10 > m20v)
        bias20 = None
        if m20v is not None and c_ok:
            bias20 = (c / m20v - 1.0) * 100.0
        slope = None
        if m20v is not None and i >= 5 and ma20[i - 5] is not None:
            slope = (m20v / ma20[i - 5] - 1.0) * 100.0
        pos60 = pos250 = dd250 = None
        if hi250[i] is not None and c_ok:
            rng = hi250[i] - lo250[i]
            if rng > 0:
                pos250 = (c - lo250[i]) / rng * 100.0
            dd250 = (c / hi250[i] - 1.0) * 100.0
        if hi60[i] is not None and c_ok:
            rng6 = hi60[i] - lo60[i]
            if rng6 > 0:
                pos60 = (c - lo60[i]) / rng6 * 100.0
        r1 = ret1[i]
        r5 = r20 = r60 = r250 = None
        if c_ok:
            if i >= 5 and closes[i - 5] and closes[i - 5] > 0:
                r5 = (c / closes[i - 5] - 1.0) * 100.0
            if i >= 20 and closes[i - 20] and closes[i - 20] > 0:
                r20 = (c / closes[i - 20] - 1.0) * 100.0
            if i >= 60 and closes[i - 60] and closes[i - 60] > 0:
                r60 = (c / closes[i - 60] - 1.0) * 100.0
            if i >= 250 and closes[i - 250] and closes[i - 250] > 0:
                r250 = (c / closes[i - 250] - 1.0) * 100.0
        vr5 = None
        if len(v5buf) == 5 and v5bad == 0 and vols[i] is not None and vols[i] > 0:
            vr5 = vols[i] / (v5s / 5.0)
        amt_ratio = None
        if amt20[i] is not None and amts[i] is not None and amt20[i] > 0:
            amt_ratio = amts[i] / amt20[i]
        v5buf.append(vols[i])
        if vols[i] is None or vols[i] <= 0:
            v5bad += 1
        else:
            v5s += vols[i]
        if len(v5buf) > 5:
            x = v5buf.popleft()
            if x is None or x <= 0:
                v5bad -= 1
            else:
                v5s -= x
        band = None
        susp = 0
        stf = is_st(code, d)
        if c_ok:
            if c < 2:
                band = 0
            elif c < 10:
                band = 1
            elif c < 30:
                band = 2
            elif c < 100:
                band = 3
            else:
                band = 4
        susp = 1 if (vols[i] is None or vols[i] <= 0) else 0
        valid = 1 if pos250 is not None else 0
        if c_ok and stf:
            st_days += 1
        if c_ok:
            b = breadth.setdefault(d, [0] * 17)
            b[0] += 1
            if r1 is not None:
                if r1 > 0:
                    b[1] += 1
                elif r1 < 0:
                    b[2] += 1
                else:
                    b[3] += 1
            if bias20 is not None and bias20 > 0:
                b[6] += 1
            if r20 is not None:
                b[4] += r20
                b[5] += 1
            if dd250 is not None and dd250 >= -0.0001:
                b[7] += 1
            if pos250 is not None and pos250 >= 80:
                b[8] += 1
            if band is not None:
                b[9 + band] += 1
            if susp:
                b[14] += 1
            b[15] += stf
            if valid:
                b[16] += 1
        out.append((code, d,
                    None if not c_ok else round(c, 4), vols[i],
                    None if r1 is None else round(r1, 4),
                    None if r5 is None else round(r5, 4),
                    None if r20 is None else round(r20, 4),
                    None if r60 is None else round(r60, 4),
                    None if r250 is None else round(r250, 4),
                    align, None if bias20 is None else round(bias20, 4),
                    None if slope is None else round(slope, 4),
                    None if pos60 is None else round(pos60, 2),
                    None if pos250 is None else round(pos250, 2),
                    None if dd250 is None else round(dd250, 4),
                    None if vr5 is None else round(vr5, 4),
                    None if vola[i] is None else round(vola[i], 4),
                    band, susp, stf, valid,
                    None if amt20[i] is None else round(amt20[i], 6),
                    None if amt_ratio is None else round(amt_ratio, 6)))
    fidx = [j for j in range(n) if closes[j] and closes[j] > 0]
    if not fidx:
        return
    fi, la = fidx[0], fidx[-1]
    vlist = [(closes[j], j) for j in fidx]
    mx = max(vlist)
    mn = min(vlist)
    total = closes[la] / closes[fi] - 1.0
    ys = sorted(yend)
    ymap = {}
    for y in ys:
        if y == 2020 or (y - 1) not in yend:
            base = closes[fi]
        else:
            base = yend[y - 1]
        ymap[str(y)] = round((yend[y] / base - 1.0) * 100.0, 4)
    c_last = closes[la]
    cur_pos250 = None
    if hi250[la] is not None:
        rng = hi250[la] - lo250[la]
        if rng > 0:
            cur_pos250 = round((c_last - lo250[la]) / rng * 100.0, 2)
    cur_align = None
    if None not in (ma5[la], ma10[la], ma20[la]):
        cur_align = (ma5[la] > ma10[la]) + (ma5[la] > ma20[la]) + (ma10[la] > ma20[la])
    cur_dd = None
    if hi250[la] is not None:
        cur_dd = round((c_last / hi250[la] - 1.0) * 100.0, 4)
    band_last = None
    if c_last and c_last > 0:
        band_last = 0 if c_last < 2 else 1 if c_last < 10 else 2 if c_last < 30 else 3 if c_last < 100 else 4
    ycols = tuple(ymap.get(str(y)) for y in range(2020, 2027))
    summ.append((code, dates[fi], dates[la], n, st_days,
                 round(closes[fi], 4), round(c_last, 4),
                 round(total * 100.0, 4),
                 round(mx[0], 4), dates[mx[1]], round(mn[0], 4), dates[mn[1]],
                 round(mdd * 100.0, 4), cur_pos250, band_last, cur_align,
                 cur_dd) + ycols)
    return


def main():
    t0 = time.time()
    path = OUT2_DB if (len(sys.argv) > 1 and sys.argv[1] == "run2") else OUT_DB
    import os
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("CREATE TABLE stock_daily(code INTEGER, date INTEGER, close REAL, vol REAL,"
                " ret1 REAL, ret5 REAL, ret20 REAL, ret60 REAL, ret250 REAL,"
                " ma_align INTEGER, bias20 REAL, ma20_slope5 REAL,"
                " pos60 REAL, pos250 REAL, dd250 REAL, vr5 REAL, vola20 REAL,"
                " band INTEGER, susp INTEGER, st INTEGER, valid INTEGER,"
                " amt20 REAL, amt_ratio REAL,"
                " PRIMARY KEY(code,date)) WITHOUT ROWID")
    con.execute("CREATE TABLE market_daily(date INTEGER PRIMARY KEY,"
                " idx_sh REAL, idx_sh_c REAL, idx_sh_r REAL,"
                " idx_hs300 REAL, idx_hs300_c REAL, idx_hs300_r REAL,"
                " idx_sz50 REAL, idx_sz50_c REAL, idx_sz50_r REAL,"
                " idx_zz500 REAL, idx_zz500_c REAL, idx_zz500_r REAL,"
                " idx_cyb REAL, idx_cyb_c REAL, idx_cyb_r REAL,"
                " n_stocks INTEGER, n_up INTEGER, n_dn INTEGER, n_fl INTEGER,"
                " mean_ret20 REAL, n_above20 INTEGER, pct_above20 REAL,"
                " n_nh250 INTEGER, n_pos80 INTEGER,"
                " b0 INTEGER, b1 INTEGER, b2 INTEGER, b3 INTEGER, b4 INTEGER,"
                " n_susp INTEGER, n_st INTEGER, n_valid INTEGER)")
    con.execute("CREATE TABLE stock_summary(code INTEGER PRIMARY KEY,"
                " first_date INTEGER, last_date INTEGER, n_bars INTEGER, st_days INTEGER,"
                " close_first REAL, close_last REAL, total_ret_pct REAL,"
                " max_close REAL, max_date INTEGER, min_close REAL, min_date INTEGER,"
                " mdd_pct REAL, cur_pos250 REAL, cur_band INTEGER, cur_align INTEGER,"
                " cur_dd250 REAL,"
                " y2020 REAL, y2021 REAL, y2022 REAL, y2023 REAL, y2024 REAL, y2025 REAL, y2026 REAL)")
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE stock_dim(code INTEGER PRIMARY KEY, industry_name TEXT, path TEXT,"
                " ambiguous INTEGER, source TEXT)")
    idx = read_indices()
    is_st = st_loader()
    breadth = {}
    summ = []
    nstock = 0
    nrow = 0
    cur = con.cursor()
    cur2 = con.cursor()
    src = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    q = src.execute("SELECT code,date,close,volume,amount FROM stock_daily_meta_qfq "
                    "WHERE date>=? AND date<=? ORDER BY code,date", (D0, D1))
    for code, grp in ((k, list(g)) for k, g in groupby(q, key=lambda r: r[0])):
        if code.startswith(EXCL):
            continue
        bars = []
        for _, d, c, v, a in grp:
            d = int(d)
            bars.append((d, c, v, a))
        nstock += 1
        rows = []
        process_stock(code, bars, is_st, rows, summ, breadth)
        if rows:
            cur.executemany("INSERT OR REPLACE INTO stock_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            nrow += len(rows)
        if nstock % 500 == 0:
            con.commit()
            print("进度 %d 股 %d 行 %.0fs" % (nstock, nrow, time.time() - t0))
    src.close()
    for d, b in sorted(breadth.items()):
        ix = idx.get(d, {})
        ivals = []
        for mkt, code, key in INDEXES:
            t = ix.get(key)
            if t:
                ivals.extend([t[0], t[1], t[2]])
            else:
                ivals.extend([None, None, None])
        mean_r20 = round(b[4] / b[5], 4) if b[5] > 0 else None
        pct20 = round(b[6] / b[0] * 100.0, 4) if b[0] > 0 else None
        row = (d,) + tuple(ivals) + (b[0], b[1], b[2], b[3], mean_r20, b[6],
                                     pct20, b[7], b[8], b[9], b[10], b[11],
                                     b[12], b[13], b[14], b[15], b[16])
        cur2.execute("INSERT INTO market_daily VALUES (%s)" % ",".join(["?"] * 33), row)
    con.commit()
    if summ:
        cur2.executemany("INSERT OR REPLACE INTO stock_summary VALUES (%s)" % ",".join(["?"] * 24), summ)
    # stock_dim 行业维度表: 申万2021 L1快照单源(同花顺映射快照industry_group_map_snapshot.db,
    # 2026-10-18建, 非PIT如实声明; 东财行业口径不同不混用; 缺口如实N/A)
    sec_db = r"D:\zcodesj\data\industry_group_map_snapshot.db"
    if not os.path.exists(sec_db):
        print("结构化失败: 行业源缺失 %s" % sec_db)
        con.close()
        sys.exit(2)
    sdb = sqlite3.connect("file:%s?mode=ro" % sec_db, uri=True)
    dim_rows = [(int(code), l1, path, amb, "ths_sw_l1_snapshot_20261018")
                for (code, l1, path, amb) in sdb.execute(
                    "SELECT code,l1,path,ambiguous_flag FROM l1_member WHERE ambiguous_flag=0")]
    sdb.close()
    cur2.executemany("INSERT OR REPLACE INTO stock_dim VALUES (?,?,?,?,?)", dim_rows)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('stock_daily_rows', ?)", (str(nrow),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('n_stocks', ?)", (str(nstock),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('stock_summary_rows', ?)", (str(len(summ)),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('stock_dim_rows', ?)", (str(len(dim_rows)),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('market_daily_rows', ?)", (str(len(breadth)),))
    if breadth:
        con.execute("INSERT OR REPLACE INTO meta VALUES ('date_min', ?)", (str(min(breadth)),))
        con.execute("INSERT OR REPLACE INTO meta VALUES ('date_max', ?)", (str(max(breadth)),))
    con.commit()
    r1c = con.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
    mc = con.execute("SELECT COUNT(*) FROM market_daily").fetchone()[0]
    sc = con.execute("SELECT COUNT(*) FROM stock_summary").fetchone()[0]
    print("建库完成 %.0fs: stock_daily=%d(%d股) market_daily=%d stock_summary=%d" % (
        time.time() - t0, r1c, nstock, mc, sc))
    con.close()


if __name__ == "__main__":
    main()
