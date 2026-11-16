# -*- coding: utf-8 -*-
"""bt_full_v3_bear.py — 熊市回避开关对照(优化杠杆4, 主人令「继续」2026-11-08)
V2口径(动态注码+统一5日+等权名额) ± 熊市开关:
  开关 = 上证指数(000001)收盘 < MA250(年线) → 停新买(持仓按规则正常退出)
对照: 无开关(V2, 已测+288.7%) vs 带开关; 重点观察 2017/2018/2022 熊市段改善
     与 2019/2020/2024 牛段启动延迟损失(开关的天然代价)。
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
IDXDB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = os.path.join(ROOT, "data", "bt_full")
START_DATE = 20160101
CAPITAL = 100000.0
RISK_PER_TRADE = 2000.0
STOP = 0.92


def load():
    con = sqlite3.connect(QFQ, uri=True)
    rows = con.execute(
        "select code,date,open,high,low,close,volume,change_pct from stock_daily_meta_qfq "
        "where date>=20150601 order by code,date").fetchall()
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
    icon = sqlite3.connect(IDXDB, uri=True)
    irows = icon.execute(
        "select date,close from index_daily_meta where code='000001' and date>=20140101 "
        "order by date").fetchall()
    icon.close()
    idx_dates = [int(r[0]) for r in irows]
    idx_close = [float(r[1]) for r in irows]
    bear = {}
    for i in range(250, len(idx_dates)):
        ma250 = float(np.mean(idx_close[i - 250:i]))
        bear[idx_dates[i]] = idx_close[i] < ma250
    return g, st_set, bear


def gen_signals(g, st_set):
    sig = defaultdict(list)
    dn7 = defaultdict(int)
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        for i, dd in enumerate(s["d"]):
            if s["p"][i] / 100.0 <= -0.07 and abs(s["p"][i]) < 35 and 0.5 < s["c"][i] <= 200:
                dn7[dd] += 1
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d, o, h, l, c, v, p = s["d"], s["o"], s["h"], s["l"], s["c"], s["v"], s["p"]
        n = len(d)
        limf = []
        for i in range(n):
            dd = d[i]
            r = p[i] / 100.0
            if abs(r) >= 35 or c[i] <= 0:
                limf.append(False)
                continue
            if c6[:3] in ("300", "301"):
                limf.append(r >= (0.099 if dd < 20200824 else 0.199))
            else:
                limf.append(r >= 0.099)
        for i in range(25, n - 25):
            if c[i] <= 0 or not (2.0 <= c[i] <= 30.0):
                continue
            if d[i] < START_DATE:
                continue
            vseg = [x for x in v[i - 20:i] if x > 0]
            volr = v[i] / float(np.mean(vseg)) if vseg and v[i] > 0 else 9.9
            ma20 = float(np.mean(c[i - 20:i]))
            dev = c[i] / ma20 - 1.0 if ma20 > 0 else 9
            rt = p[i] / 100.0
            if c[i - 21] <= 0:
                continue
            r20 = c[i - 1] / c[i - 21] - 1.0
            if (not limf[i] and -0.03 <= dev <= 0.05 and 1.0 <= volr <= 2.5
                    and 0 < r20 <= 0.30):
                sig[d[i]].append(("ret", code, r20, dev, volr))
            if limf[i] and i >= 1 and not limf[i - 1] and r20 < -0.10:
                sig[d[i]].append(("fb", code, 0, 0, 0))
            lim = (0.099 if d[i] < 20200824 else 0.199) if c6[:3] in ("300", "301") else 0.099
            if (not limf[i] and 0.05 <= rt < lim and dev < 0.05 and 1.0 <= volr <= 2.5
                    and r20 < -0.10):
                lo20 = min(c[i - 20:i + 1])
                rebr = c[i] / lo20 - 1.0 if lo20 > 0 else 0
                sig[d[i]].append(("up", code, rebr, 0, 0))
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
                    if ok and 2.0 <= fbc <= 30.0:
                        sig[d[i]].append(("cons", code, volr, 0, 0))
    return sig, dn7


def daily_pick(lst):
    ret = [x for x in lst if x[0] == "ret"]
    fb = [x for x in lst if x[0] == "fb"]
    up = [x for x in lst if x[0] == "up"]
    cons = [x for x in lst if x[0] == "cons"]
    ret.sort(key=lambda x: (0 if 0.02 <= x[3] <= 0.05 else 1,
                            0 if 1.5 <= x[4] <= 2.5 else 1, x[4]))
    up.sort(key=lambda x: -abs(x[2] - 0.20))
    cons.sort(key=lambda x: x[2])
    out = [("回踩确认型", x[1]) for x in ret[:6]]
    out += [("底部反转首板", x[1]) for x in fb[:4]]
    out += [("起涨型v2", x[1]) for x in up[:4]]
    out += [("首板缩量横盘", x[1]) for x in cons[:4]]
    return out


def simulate(g, sig, dn7, bear):
    dates = sorted({d for s in g.values() for d in s["d"] if d >= START_DATE})
    cash = CAPITAL
    pos = []
    trades = []
    eq = []
    prev_pend = []
    for D in dates:
        for seg, code in prev_pend:
            s = g[code]
            if D not in s["d"]:
                continue
            j = s["d"].index(D)
            px = s["o"][j]
            if px <= 0:
                continue
            budget = min(RISK_PER_TRADE / (1 - STOP), cash * 0.5)
            shares = int(budget / px / 100) * 100
            if shares < 100:
                shares = 100 if cash >= px * 100 else 0
            if shares <= 0 or cash < px * shares:
                continue
            cash -= px * shares
            pos.append({"code": code, "seg": seg, "ent_px": px, "ent_date": D,
                        "j0": j, "j": j, "peak": px, "shares": shares, "mark": px})
        prev_pend = []
        closed_idx = []
        for pi, ps in enumerate(pos):
            s = g[ps["code"]]
            d = s["d"]
            if ps["j"] >= len(d) or d[ps["j"]] != D:
                continue
            j = ps["j"]
            c = s["c"][j]; h = s["h"][j]
            ps["peak"] = max(ps["peak"], h)
            reason = None
            if c <= ps["ent_px"] * STOP:
                reason = "收盘判-8%止损"
            elif j - ps["j0"] >= 5:
                reason = "持5日到期"
            if reason:
                ret = c / ps["ent_px"] - 1.0
                cash += ps["shares"] * c
                trades.append({"seg": ps["seg"], "code": ps["code"], "ent_date": ps["ent_date"],
                               "exit_date": D, "ret": ret, "reason": reason})
                closed_idx.append(pi)
                continue
            ps["mark"] = c
            ps["j"] = j + 1
        for pi in sorted(closed_idx, reverse=True):
            pos.pop(pi)
        eq.append((D, cash + sum(p["shares"] * p["mark"] for p in pos)))
        k = dn7.get(D, 0)
        if 15 < k <= 50:
            prev_pend = []
            continue
        if bear.get(D, False):
            prev_pend = []  # 熊市开关: 年线下停新买
            continue
        prev_pend = daily_pick(sig.get(D, []))
    return trades, eq


def summarize(trades, eq):
    vals = np.array([v for _d, v in eq])
    total = vals[-1] / CAPITAL - 1.0
    yrs = len(eq) / 250.0
    cagr = (vals[-1] / CAPITAL) ** (1.0 / max(yrs, 1e-9)) - 1.0
    peak = np.maximum.accumulate(vals)
    dd = float(np.min(vals / peak - 1.0))
    win = float(np.mean([t["ret"] > 0 for t in trades])) if trades else 0
    return {"total": total, "cagr": cagr, "dd": dd, "win": win, "n": len(trades)}


if __name__ == "__main__":
    g, st_set, bear = load()
    print("数据+指数年线载入完成(熊市日%d/%d)" % (
        sum(1 for v in bear.values() if v), len(bear)), file=sys.stderr)
    sig, dn7 = gen_signals(g, st_set)
    print("信号完成", file=sys.stderr)
    out = {}
    for use_bear, lab in ((False, "V2无开关(对照)"), (True, "V2+年线熊市开关")):
        trades, eq = simulate(g, sig, dn7, bear if use_bear else {})
        s = summarize(trades, eq)
        out[lab] = s
        print("%s: 总收益%+.1f%% 年化%+.1f%% 回撤%.1f%% 胜率%.1f%% 笔数%d" % (
            lab, 100 * s["total"], 100 * s["cagr"], 100 * s["dd"], 100 * s["win"], s["n"]))
        byy = defaultdict(list)
        for t in trades:
            byy[str(t["ent_date"])[:4]].append(t["ret"])
        lo = sorted((y, 100 * float(np.mean(v))) for y, v in byy.items())[:3]
        print("   单笔均值最差三年:", ", ".join("%s(%+.2f%%)" % (y, m) for y, m in lo))
    json.dump(out, open(os.path.join(OUT, "v3_bear_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("落盘: v3_bear_summary.json", file=sys.stderr)
