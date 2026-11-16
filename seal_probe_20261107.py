# -*- coding: utf-8 -*-
"""seal_probe_20261107.py — 三项待验探针一次验完(2026-11-07)
台账二百七十九/二百八十一遗留: ①封单额台阶 ②封板时间因子 ③炸板率开关
数据源: tushare limit_list_d 涨停池U(fd_amount封单额/first_time首封时间/open_times炸板次数)
        + 炸板池Z(炸板家数→炸板率=Z/(U+Z))
口径: 首板样本(前一日非涨停)→次日晋级率(qfq change_pct>=涨停线)+次日开盘买持5日
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
UCACHE = os.path.join(ROOT, "data", "seal_probe_U_202606.csv")
ZCACHE = os.path.join(ROOT, "data", "seal_probe_Z_202606.csv")


def pull_tushare():
    import tushare as ts
    tok = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("TUSHARE_TOKEN missing")
    pro = ts.pro_api(tok)
    fields = ("ts_code,trade_date,name,close,pct_chg,fd_amount,first_time,last_time,"
              "open_times,limit_times,limit_amount")
    segs = [("20260601", "20260831"), ("20260901", "20261106")]
    with open(UCACHE, "w", encoding="utf-8") as fu, open(ZCACHE, "w", encoding="utf-8") as fz:
        fu.write(fields + "\n")
        fz.write("ts_code,trade_date\n")
        for a, b in segs:
            df = pro.limit_list_d(start_date=a, end_date=b, limit_type="U", fields=fields)
            df.to_csv(fu, header=False, index=False)
            dz = pro.limit_list_d(start_date=a, end_date=b, limit_type="Z",
                                  fields="ts_code,trade_date")
            dz.to_csv(fz, header=False, index=False)
            print("pulled %s-%s: U=%d Z=%d" % (a, b, len(df), len(dz)))


def main():
    if not (os.path.exists(UCACHE) and os.path.exists(ZCACHE)):
        pull_tushare()
    import csv
    urows = []
    with open(UCACHE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            urows.append(r)
    zcnt = defaultdict(int)
    ucnt = defaultdict(int)
    with open(ZCACHE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            zcnt[r["trade_date"]] += 1
    con = sqlite3.connect(QFQ, uri=True)
    scon = sqlite3.connect(STDB, uri=True)
    st_set = {str(c)[-6:] for c, f2 in scon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 "
        "where s2.code=s1.code)").fetchall() if f2 == 1}
    scon.close()
    need = sorted({r["ts_code"][:6] for r in urows})
    qmarks = ",".join("?" * len(need))
    rows = con.execute(
        "select code,date,open,close,change_pct from stock_daily_meta_qfq "
        "where date>=20260501 and substr(code,-6) in (%s) order by code,date" % qmarks,
        need).fetchall()
    con.close()
    g = defaultdict(lambda: {"d": [], "o": [], "c": [], "p": []})
    for code, d, o, c, p in rows:
        s = g[str(code)[-6:]]
        s["d"].append(int(d)); s["o"].append(float(o or 0)); s["c"].append(float(c or 0))
        s["p"].append(float(p or 0) if p is not None else 0.0)
    for r in urows:
        ucnt[r["trade_date"]] += 1

    def is_lim(c6, r):
        return r >= (0.199 if c6[:3] in ("300", "301") else 0.099)

    A = defaultdict(lambda: [0, 0])   # fd bucket -> [promo, total]
    A_ret = defaultdict(list)         # fd bucket -> ret5
    B = defaultdict(lambda: [0, 0])   # first-time band -> [promo, total]
    C = defaultdict(lambda: [0, 0])   # open_times band -> [promo, total]
    D = defaultdict(list)             # 炸板率 band -> ret5
    for r in urows:
        c6 = r["ts_code"][:6]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        s = g.get(c6)
        if not s:
            continue
        td = int(r["trade_date"])
        try:
            i = s["d"].index(td)
        except ValueError:
            continue
        if i < 1 or i + 6 >= len(s["d"]):
            continue
        if is_lim(c6, s["p"][i - 1] / 100.0):
            continue  # first-board only
        promo = 1 if is_lim(c6, s["p"][i + 1] / 100.0) else 0
        fd = float(r["fd_amount"] or 0)
        fbk = ("a<1亿" if fd < 1e8 else "b1-3亿" if fd < 3e8 else
               "c3-10亿" if fd < 10e8 else "d>10亿" if fd > 0 else "e缺")
        A[fbk][0] += promo
        A[fbk][1] += 1
        ft = (r["first_time"] or "")
        if ft:
            tb = ("a早封<=10点" if ft <= "100000" else
                  "b10-14点" if ft <= "140000" else "c尾盘>14点")
            B[tb][0] += promo
            B[tb][1] += 1
        ot = int(float(r["open_times"] or 0))
        obk = "a0次" if ot == 0 else ("b1次" if ot == 1 else "c>=2次")
        C[obk][0] += promo
        C[obk][1] += 1
        o1, c6c = s["o"][i + 1], s["c"][i + 6]
        if o1 > 0 and c6c > 0:
            ret5 = c6c / o1 - 1.0
            A_ret[fbk].append(ret5)
            zb, ub = zcnt.get(r["trade_date"], 0), ucnt.get(r["trade_date"], 0)
            rate = zb / max(ub + zb, 1)
            rbk = ("a<25%%" if rate < 0.25 else "b25-40%%" if rate < 0.40 else
                   "c40-50%%" if rate < 0.50 else "d>50%%")
            D[rbk].append(ret5)
    print("==== 探针A. 封单额台阶 x 首板次日晋级率(文中22/34/58/79%%) ====")
    for bk in sorted(A):
        pr, tt = A[bk]
        arr = np.array(A_ret[bk]) if A_ret[bk] else np.array([0.0])
        print("%-8s n=%-6d 晋级率%.1f%% | 次日买持5日 mean=%+.2f%% win=%.1f%%" % (
            bk, tt, 100.0 * pr / tt if tt else 0, 100 * arr.mean(),
            100 * float(np.mean(arr > 0))))
    print("==== 探针B. 封板时间(文中早封59%% vs 尾盘16%%) ====")
    for bk in sorted(B):
        pr, tt = B[bk]
        print("%-10s n=%-6d 晋级率%.1f%%" % (bk, tt, 100.0 * pr / tt if tt else 0))
    print("==== 探针C. 炸板次数(文中多炸<28%%) ====")
    for bk in sorted(C):
        pr, tt = C[bk]
        print("%-6s n=%-6d 晋级率%.1f%%" % (bk, tt, 100.0 * pr / tt if tt else 0))
    print("==== 探针D. 炸板率开关 x 当日涨停次日开盘买持5日(文中>40%%=弱) ====")
    for bk in sorted(D):
        arr = np.array(D[bk])
        print("%-8s n=%-6d mean=%+.3f%% median=%+.3f%% win=%.1f%%" % (
            bk, len(arr), 100 * arr.mean(), 100 * np.median(arr),
            100 * float(np.mean(arr > 0))))


if __name__ == "__main__":
    main()
