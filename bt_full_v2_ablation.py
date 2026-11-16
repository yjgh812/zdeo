# -*- coding: utf-8 -*-
"""bt_full_v2_ablation.py — 优化杠杆消融对照(主人令「按建议继续」2026-11-08)
同一信号集(sig/dn7复用bt_full口径), 只改注码/持有期/名额倾斜, 四版对照:
  V1 基线: 100股固定, 持5日, 段内名额(6/4/4/4), 阴跌区停买
  V2 = V1 + 动态注码(风险预算2000元/8%止损距 -> 约2.5万/笔, 按现金浮动)
  V3 = V2 + 持有期分层(超跌族up/fb持20日, 回踩/横盘族ret/cons持10日)
  V4 = V3 + 子集倾斜(黄金带起涨扩额优先, 弱档递减)
输出: 四版逐年收益/胜率/年化/回撤对照表 + V4逐年明细CSV
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
OUT = os.path.join(ROOT, "data", "bt_full")
START_DATE = 20160101
CAPITAL = 100000.0
RISK_PER_TRADE = 2000.0   # 杠杆1: 单笔风险预算
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
    return g, st_set


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
                golden = 1 if 0.15 <= rebr <= 0.25 else 0
                sig[d[i]].append(("up", code, rebr, golden, volr))
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


def daily_pick(lst, tilt):
    ret = [x for x in lst if x[0] == "ret"]
    fb = [x for x in lst if x[0] == "fb"]
    up = [x for x in lst if x[0] == "up"]
    cons = [x for x in lst if x[0] == "cons"]
    ret.sort(key=lambda x: (0 if 0.02 <= x[3] <= 0.05 else 1,
                            0 if 1.5 <= x[4] <= 2.5 else 1, x[4]))
    if tilt:
        up.sort(key=lambda x: (-x[3], -abs(x[2] - 0.20)))  # 黄金带优先
        picks = ([("起涨型v2黄金带", x[1]) for x in up if x[3] == 1][:6]
                 + [("底部反转首板", x[1]) for x in fb[:4]]
                 + [("回踩确认型", x[1]) for x in ret[:4]]
                 + [("起涨型v2", x[1]) for x in up if x[3] == 0][:2]
                 + [("首板缩量横盘", x[1]) for x in cons[:2]])
        return picks
    up.sort(key=lambda x: -abs(x[2] - 0.20))
    cons.sort(key=lambda x: x[2])
    out = [("回踩确认型", x[1]) for x in ret[:6]]
    out += [("底部反转首板", x[1]) for x in fb[:4]]
    out += [("起涨型v2", x[1]) for x in up[:4]]
    out += [("首板缩量横盘", x[1]) for x in cons[:4]]
    return out


HOLD_BY_SEG_TILTED = {"起涨型v2黄金带": 20, "起涨型v2": 20, "底部反转首板": 20,
                      "回踩确认型": 10, "首板缩量横盘": 10}


def simulate(g, sig, dn7, dynamic_size, hold_tilt, tilt):
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
            if dynamic_size:
                budget = min(RISK_PER_TRADE / (1 - STOP), cash * 0.5)
                shares = int(budget / px / 100) * 100
                if shares < 100:
                    shares = 100 if cash >= px * 100 else 0
            else:
                shares = 100
            if shares <= 0 or cash < px * shares:
                continue
            cost = px * shares
            cash -= cost
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
            hold = (HOLD_BY_SEG_TILTED.get(ps["seg"], 5) if hold_tilt else 5)
            reason = None
            if c <= ps["ent_px"] * STOP:
                reason = "收盘判-8%止损"
            elif j - ps["j0"] >= hold:
                reason = "持有%d日到期" % hold
            if reason:
                ret = c / ps["ent_px"] - 1.0
                cash += ps["shares"] * c
                trades.append({"seg": ps["seg"], "code": ps["code"], "ent_date": ps["ent_date"],
                               "ent_px": ps["ent_px"], "exit_date": D, "exit_px": c,
                               "shares": ps["shares"], "ret": ret, "reason": reason})
                closed_idx.append(pi)
                continue
            ps["mark"] = c
            ps["j"] = j + 1
        for pi in sorted(closed_idx, reverse=True):
            pos.pop(pi)
        total_val = cash + sum(p["shares"] * p["mark"] for p in pos)
        eq.append((D, total_val))
        k = dn7.get(D, 0)
        prev_pend = [] if 15 < k <= 50 else daily_pick(sig.get(D, []), tilt)
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
    g, st_set = load()
    print("数据载入完成", file=sys.stderr)
    sig, dn7 = gen_signals(g, st_set)
    print("信号完成", file=sys.stderr)
    results = {}
    all_trades = {}
    for ver, cfg in (("V1基线", dict(dynamic_size=False, hold_tilt=False, tilt=False)),
                     ("V2加动态注码", dict(dynamic_size=True, hold_tilt=False, tilt=False)),
                     ("V3加持有分层", dict(dynamic_size=True, hold_tilt=True, tilt=False)),
                     ("V4加子集倾斜", dict(dynamic_size=True, hold_tilt=True, tilt=True))):
        trades, eq = simulate(g, sig, dn7, **cfg)
        s = summarize(trades, eq)
        results[ver] = s
        all_trades[ver] = trades
        print("%s: 总收益%+.1f%% 年化%+.1f%% 回撤%.1f%% 胜率%.1f%% 笔数%d" % (
            ver, 100 * s["total"], 100 * s["cagr"], 100 * s["dd"], 100 * s["win"], s["n"]))
    json.dump({v: {"total": r["total"], "cagr": r["cagr"], "dd": r["dd"],
                   "win": r["win"], "n": r["n"]} for v, r in results.items()},
              open(os.path.join(OUT, "v2_ablation_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(all_trades["V4加子集倾斜"],
              open(os.path.join(OUT, "v4_trades.json"), "w", encoding="utf-8"),
              ensure_ascii=False, default=str)
    print("落盘: v2_ablation_summary.json + v4_trades.json", file=sys.stderr)
