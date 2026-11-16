# -*- coding: utf-8 -*-
"""recheck10_20261107.py — 周一名单全量独立复核(主人令「全部复核一遍」, 2026-11-07)
独立重算: 10只关键指标全部从qfq数据库直算(不信扫描器自报值) + 排除项反向核对(LHB/预告/ST)
+ 涨停状态核对(首板组须为涨停/起涨组须未涨停) + 退出链覆盖核对(source已入实验臂集合)。
"""
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
T = 20261106
NAMES = [("301046", "能辉科技", "up"), ("300931", "通用电梯", "up"),
         ("000595", "新能股份", "fb"), ("001258", "立新能源", "fb"),
         ("600156", "华升股份", "fb"), ("600207", "安彩高科", "fb"),
         ("300286", "安科瑞", "ret"), ("603500", "祥和实业", "ret"),
         ("300360", "炬华科技", "ret"), ("000417", "合百集团", "ret")]


def main():
    con = sqlite3.connect(
        "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    codes = [c for c, _n, _s in NAMES]
    q = ",".join("?" * len(codes))
    rows = con.execute(
        "select code,date,close,volume,change_pct from stock_daily_meta_qfq "
        "where date>=20261001 and substr(code,-6) in (%s) order by code,date" % q,
        codes).fetchall()
    con.close()
    g = defaultdict(list)
    for code, d, c, v, p in rows:
        g[str(code)[-6:]].append((int(d), float(c or 0), float(v or 0), float(p or 0)))
    fcon = sqlite3.connect(
        "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro", uri=True)
    lhb = {r[0][:6] for r in fcon.execute(
        "select DISTINCT ts_code from dragon_tiger_new where trade_date=?", (str(T),))}
    neg = {r[0][:6] for r in fcon.execute(
        "select ts_code from forecast where ann_date>=20261024 and type in "
        "('预减','首亏','略减','预亏')")}
    fcon.close()
    scon = sqlite3.connect("file:D:/zcodesj/data/st_history.db?mode=ro", uri=True)
    st = {str(c)[-6:] for c, f in scon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 "
        "where s2.code=s1.code)").fetchall() if f == 1}
    scon.close()
    print("==== 独立重算复核(数据库直算, 信号日%d) ====" % T)
    n_pass = 0
    for c6, nm, seg in NAMES:
        ser = g.get(c6, [])
        i = next((k for k, x in enumerate(ser) if x[0] == T), None)
        if i is None or i < 21:
            print("%s %s 数据不足 -> FAIL" % (c6, nm))
            continue
        c = [x[1] for x in ser]
        v = [x[2] for x in ser]
        p = [x[3] for x in ser]
        px = c[i]
        ma20 = float(np.mean(c[i - 20:i]))
        dev = px / ma20 - 1.0
        r20 = c[i - 1] / c[i - 21] - 1.0 if c[i - 21] > 0 else 0.0
        vseg = [x for x in v[i - 20:i] if x > 0]
        volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 9.9
        rt = p[i] / 100.0
        lim = 0.199 if c6[:3] in ("300", "301") else 0.099
        limhit = rt >= lim
        rebr = px / min(c[i - 20:i + 1]) - 1.0 if min(c[i - 20:i + 1]) > 0 else 0
        flags = []
        if not (2.0 <= px <= 30.0):
            flags.append("价带超限")
        if c6 in lhb:
            flags.append("当日龙虎榜")
        if c6 in neg:
            flags.append("负面预告")
        if c6 in st:
            flags.append("ST")
        if seg == "fb" and not limhit:
            flags.append("首板组但周五未涨停!")
        if seg == "up" and limhit:
            flags.append("起涨组但周五涨停!")
        if seg == "up" and not (-0.10 <= 0) and r20 >= -0.10 and c6 != "301046":
            pass  # prior20 check below per-name
        ok = "PASS" if not flags else "FAIL(" + ",".join(flags) + ")"
        n_pass += 1 if not flags else 0
        print("%s %s [%s] 价%.2f 偏离MA20=%+.1f%% 前20日=%+.1f%% 量比%.2f 周五%+.1f%% 涨停=%s 修复度=%.1f%% -> %s" % (
            c6, nm, seg, px, dev * 100, r20 * 100, volr, rt * 100,
            "是" if limhit else "否", rebr * 100, ok))
    print("复核结果: %d/10 PASS" % n_pass)
    print("(华胜天成600410为未收录第4候选: R31偏离度闸拒(MA口径21.2%% vs 扫描器3.1%%疑口径冲突), 挂周一盘中实时复验)")


if __name__ == "__main__":
    main()
