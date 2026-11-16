# -*- coding: utf-8 -*-
"""integ_backtest_20261107.py — 四法组合集成回测(生产扫描器口径, 2026-11-07)
四段名单每日同时产出 → 次日开盘等权买入 → -8%止损+峰值回撤8%波动止盈+20日到期退出。
输出: 组合总收益/年化/最大回撤/胜率/分段贡献 + 大盘状态闸(阴跌区15-50停新买)对照。
简化声明: 等权记收益(组合日收益=持仓票日收益均值), 衡量信号质量与叠加, 不含资金容量约束。
"""
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


def load():
    con = sqlite3.connect(QFQ, uri=True)
    rows = con.execute(
        "select code,date,open,high,low,close,volume,change_pct from stock_daily_meta_qfq "
        "where date>=20221001 order by code,date").fetchall()
    con.close()
    scon = sqlite3.connect(STDB, uri=True)
    st_set = {str(c)[-6:] for c, f in scon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 "
        "where s2.code=s1.code)").fetchall() if f == 1}
    scon.close()
    g = defaultdict(lambda: {"d": [], "o": [], "h": [], "l": [], "c": [], "v": [], "p": []})
    for code, d, o, h, l, c, v, p in rows:
        s = g[code]
        s["d"].append(int(d)); s["o"].append(float(o or 0)); s["h"].append(float(h or 0))
        s["l"].append(float(l or 0)); s["c"].append(float(c or 0))
        s["v"].append(float(v or 0)); s["p"].append(float(p or 0) if p is not None else 0.0)
    return g, st_set


def is_lim(c6, r):
    return r >= (0.199 if c6[:3] in ("300", "301") else 0.09)


def gen_signals(g, st_set, pband=(2.0, 30.0)):
    sig = defaultdict(list)
    dn7 = defaultdict(int)
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        for i, dd in enumerate(s["d"]):
            if (s["p"][i] / 100.0 <= -0.07 and abs(s["p"][i]) < 35
                    and 0.5 < s["c"][i] <= 200):
                dn7[dd] += 1
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, o, h, l, c, v, p = s["d"], s["o"], s["h"], s["l"], s["c"], s["v"], s["p"]
        n = len(d)
        limf = [is_lim(c6, p[i] / 100.0) if c[i] > 0 and abs(p[i]) < 35 else False
                for i in range(n)]
        for i in range(25, n - 22):
            if c[i] <= 0 or not (pband[0] <= c[i] <= pband[1]):
                continue
            vseg = [x for x in v[i - 20:i] if x > 0]
            volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 9.9
            ma20 = float(np.mean(c[i - 20:i]))
            dev = c[i] / ma20 - 1.0 if ma20 > 0 else 9
            rt = p[i] / 100.0
            if c[i - 21] <= 0:
                continue
            r20 = c[i - 1] / c[i - 21] - 1.0
            # seg1 pullback-confirm
            if (not limf[i] and -0.03 <= dev <= 0.05 and 1.0 <= volr <= 2.5
                    and 0 < r20 <= 0.30):
                sig[d[i]].append(("ret", code, r20, dev, volr))
            # seg2 bottom-reversal first board
            if limf[i] and i >= 1 and not limf[i - 1] and r20 < -0.10:
                sig[d[i]].append(("fb", code, 0, 0, 0))
            # seg3 upstart v2
            lim = 0.19 if c6[:3] in ("300", "301") else 0.09
            if (not limf[i] and 0.05 <= rt < lim and dev < 0.05 and 1.0 <= volr <= 2.5
                    and r20 < -0.10):
                lo20 = min(c[i - 20:i + 1])
                rebr = c[i] / lo20 - 1.0 if lo20 > 0 else 0
                sig[d[i]].append(("up", code, rebr, 0, 0))
            # seg4 consolidation entry
            if not limf[i] and volr < 1.2:
                fb_day = None
                for j in range(i - 9, i - 1):
                    if j < 25:
                        continue
                    if limf[j] and not limf[j - 1]:
                        fb_day = j
                        break
                if fb_day is not None:
                    fbc = c[fb_day]
                    ok = True
                    for k in range(fb_day + 1, i + 1):
                        if limf[k] or c[k] < fbc * 0.98:
                            ok = False
                            break
                    if ok and pband[0] <= fbc <= pband[1]:
                        sig[d[i]].append(("cons", code, volr, 0, 0))
    return sig, dn7


def daily_pick(lst, caps=None):
    ret = [x for x in lst if x[0] == "ret"]
    fb = [x for x in lst if x[0] == "fb"]
    up = [x for x in lst if x[0] == "up"]
    cons = [x for x in lst if x[0] == "cons"]
    if caps:
        ret.sort(key=lambda x: (0 if 0.02 <= x[3] <= 0.05 else 1,
                                0 if 1.5 <= x[4] <= 2.5 else 1, x[4]))
        up.sort(key=lambda x: -abs(x[2] - 0.20))
        cons.sort(key=lambda x: x[2])
        ret = ret[:caps[0]]
        fb = fb[:caps[1]]
        up = up[:caps[2]]
        cons = cons[:caps[3]]
    out = [("ret", x[1]) for x in ret]
    out += [("fb", x[1]) for x in fb]
    out += [("up", x[1]) for x in up]
    out += [("cons", x[1]) for x in cons]
    return out


def simulate(g, sig, dn7, gate, caps=None, hold=20, use_stops=True, close_stop=False):
    dates = sorted({d for s in g.values() for d in s["d"] if d >= 20230101})
    pos = []
    daily = []
    seg_stat = defaultdict(list)
    prev_pend = []
    for D in dates:
        # 1) open entries decided yesterday
        for seg, code in prev_pend:
            s = g[code]
            if D in s["d"]:
                j = s["d"].index(D)
                if s["o"][j] > 0:
                    pos.append({"code": code, "s": s, "j": j, "j0": j, "ent": s["o"][j],
                                "peak": s["o"][j], "seg": seg, "mark": s["o"][j]})
        prev_pend = []        # 2) mark & exits
        rets = []
        closed = []
        for pi, ps in enumerate(pos):
            s = ps["s"]
            d = s["d"]
            if ps["j"] >= len(d) or d[ps["j"]] != D:
                continue  # suspended today, mark unchanged
            j = ps["j"]
            o, h, l, c = s["o"][j], s["h"][j], s["l"][j], s["c"][j]
            ent = ps["ent"]
            slpx = ent * 0.92
            ps["peak"] = max(ps["peak"], h)
            trailpx = ps["peak"] * 0.92
            exit_px = None
            if close_stop:
                # option-2 regime: judge on CLOSE (not intraday touch), exit at close same day
                if c <= ent * 0.92:
                    exit_px = c
                elif j - ps["j0"] >= hold:
                    exit_px = c
            elif use_stops:
                if l <= slpx:
                    exit_px = slpx
                elif ps["peak"] >= ent * 1.05 and l <= trailpx:
                    exit_px = max(trailpx, slpx)
                elif j - ps["j0"] >= hold:
                    exit_px = c
            else:
                if j - ps["j0"] >= hold:
                    exit_px = c
            if exit_px is not None:
                rets.append(exit_px / ps["mark"] - 1.0 if ps["mark"] > 0 else 0.0)
                seg_stat[ps["seg"]].append(exit_px / ent - 1.0)
                closed.append(pi)
            else:
                rets.append(c / ps["mark"] - 1.0 if ps["mark"] > 0 else 0.0)
                ps["mark"] = c
                ps["j"] = j + 1
        for pi in sorted(closed, reverse=True):
            pos.pop(pi)
        daily.append(float(np.mean(rets)) if rets else 0.0)
        # 3) today's picks -> tomorrow entries
        prev_pend = daily_pick(sig.get(D, []), caps)
        if gate:
            k = dn7.get(D, 0)
            if 15 < k <= 50:
                prev_pend = []
    return daily, seg_stat, len(dates), len(pos)


if __name__ == "__main__":
    g, st_set = load()
    sig, dn7 = gen_signals(g, st_set, pband=(2.0, 30.0))
    for hold, cs, lab in ((5, True, "选项2口径: 收盘判-8%%+持5日"),
                          (10, True, "选项2口径: 收盘判-8%%+持10日"),
                          (20, True, "选项2口径: 收盘判-8%%+持20日")):
        daily, seg_stat, ndays, open_n = simulate(g, sig, dn7, gate=False, caps=None,
                                                  hold=hold, close_stop=cs)
        arr = np.array(daily)
        eq = np.cumprod(1.0 + arr)
        peak = np.maximum.accumulate(eq)
        dd = float(np.min(eq / peak - 1.0))
        total = float(eq[-1] - 1.0)
        yrs = ndays / 250.0
        cagr = (1.0 + total) ** (1.0 / max(yrs, 1e-9)) - 1.0
        daywin = float(np.mean(arr > 0))
        print("==== %s ====" % lab)
        print("交易日%d 组合总收益%+.1f%% 年化%+.1f%% 最大回撤%.1f%% 日胜率%.1f%%" % (
            ndays, 100 * total, 100 * cagr, 100 * dd, 100 * daywin))
        for seg, lab2 in (("ret", "回踩确认型"), ("fb", "底部反转首板"), ("up", "起涨型v2"),
                          ("cons", "横盘介入")):
            a = np.array(seg_stat[seg])
            if len(a):
                print("  %-8s n=%-6d 单笔均%+.2f%% 单笔胜率%.1f%%" % (
                    lab2, len(a), 100 * a.mean(), 100 * float(np.mean(a > 0))))
