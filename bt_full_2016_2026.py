# -*- coding: utf-8 -*-
"""bt_full_2016_2026.py — 屠龙系统全量回测(主人令 2026-11-08)
范围: A股全量(qfq库1990-2026, 主板+创业板, 剔科创板688/689、北交所、ST)
     2016-01-01 ~ 2026-11-06, 初始资金100,000元, 每笔100股*12只/日上限
系统: 四法信号(回踩确认/底反首板/起涨型/横盘介入, 生产扫描器口径)
     出场=收盘判-8%止损+持5交易日到期卖(主人选项2, 组合回测+84.5%口径)
     大盘闸=阴跌区(跌7%家数15-50)停新买; 涨停/高价(>30元)剔除; 等权小注
产出: data/bt_full/下逐年交易明细CSV + 权益曲线 + HTML可视化报告
诚实声明(宪法反臆想): 信号参数系2023+数据验证, 2016-2022段为参数外稳健性检验;
     78-82%胜率/年化200%为主人目标线, 报告如实对照实际值与差距, 不造数。
"""
import csv
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
os.makedirs(OUT, exist_ok=True)
START_DATE = 20160101
CAPITAL = 100000.0
SHARES = 100          # 每笔100股(与模拟盘口径一致)
MAX_NEW_PER_DAY = 12  # 每日新开上限12笔(12只名单口径)
HOLD_DAYS = 5
STOP = 0.92           # 收盘判-8%
DAILY_CAPITAL_CAP = 40000  # 每日新开占用上限(风险预算)


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
        for i in range(25, n - 22):
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
            # seg1 回踩确认型
            if (not limf[i] and -0.03 <= dev <= 0.05 and 1.0 <= volr <= 2.5
                    and 0 < r20 <= 0.30):
                sig[d[i]].append(("ret", code, r20, dev, volr))
            # seg2 底部反转首板
            if limf[i] and i >= 1 and not limf[i - 1] and r20 < -0.10:
                sig[d[i]].append(("fb", code, 0, 0, 0))
            # seg3 起涨型v2
            lim = (0.099 if d[i] < 20200824 else 0.199) if c6[:3] in ("300", "301") else 0.099
            if (not limf[i] and 0.05 <= rt < lim and dev < 0.05 and 1.0 <= volr <= 2.5
                    and r20 < -0.10):
                lo20 = min(c[i - 20:i + 1])
                rebr = c[i] / lo20 - 1.0 if lo20 > 0 else 0
                sig[d[i]].append(("up", code, rebr, 0, 0))
            # seg4 横盘介入
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


def simulate(g, sig, dn7):
    dates = sorted({d for s in g.values() for d in s["d"] if d >= START_DATE})
    cash = CAPITAL
    pos = []
    trades = []      # dict: seg/code/name/entry_date/entry_px/exit_date/exit_px/ret/reason
    equity_curve = []
    prev_pend = []
    tset = sorted(g_dates(g))
    dpos = {d: i for i, d in enumerate(tset)}
    for D in dates:
        # 1) 开仓(昨日名单)
        new_open = []
        for seg, code in prev_pend:
            s = g[code]
            if D not in s["d"]:
                continue
            j = s["d"].index(D)
            px = s["o"][j]
            if px <= 0:
                continue
            cost = px * SHARES
            if cash < cost or len(new_open) >= MAX_NEW_PER_DAY:
                continue
            # 每日风险预算
            if sum(x["cost"] for x in new_open) + cost > DAILY_CAPITAL_CAP:
                continue
            cash -= cost
            new_open.append({"code": code, "seg": seg, "ent_px": px, "ent_date": D,
                             "j0": j, "j": j, "peak": px, "cost": cost})
        pos.extend(new_open)
        prev_pend = []
        # 2) 持仓盯市与退出
        day_rets = []
        closed_idx = []
        for pi, ps in enumerate(pos):
            s = g[ps["code"]]
            d = s["d"]
            if ps["j"] >= len(d) or d[ps["j"]] != D:
                continue  # 停牌
            j = ps["j"]
            c = s["c"][j]; l = s["l"][j]; h = s["h"][j]
            ps["peak"] = max(ps["peak"], h)
            reason = None
            if c <= ps["ent_px"] * STOP:
                reason = "收盘判-8%止损"
            elif j - ps["j0"] >= HOLD_DAYS:
                reason = "持5日到期"
            if reason:
                exit_px = c
                ret = exit_px / ps["ent_px"] - 1.0
                cash += SHARES * exit_px
                trades.append({"seg": ps["seg"], "code": ps["code"], "ent_date": ps["ent_date"],
                               "ent_px": ps["ent_px"], "exit_date": D, "exit_px": exit_px,
                               "ret": ret, "reason": reason})
                day_rets.append(ret)
                closed_idx.append(pi)
                continue
            ps["mark"] = c
            ps["j"] = j + 1
        for pi in sorted(closed_idx, reverse=True):
            pos.pop(pi)
        # 组合当日收益(用于权益曲线, 与现金口径并行)
        total_val = cash + sum(SHARES * p.get("mark", p["ent_px"]) for p in pos)
        equity_curve.append((D, total_val))
        # 3) 生成明日名单(阴跌区闸)
        k = dn7.get(D, 0)
        if 15 < k <= 50:
            prev_pend = []
        else:
            prev_pend = daily_pick(sig.get(D, []))
    return trades, equity_curve, pos


def g_dates(g):
    seen = set()
    for s in g.values():
        for d in s["d"]:
            if d >= START_DATE:
                seen.add(d)
    return seen


if __name__ == "__main__":
    g, st_set = load()
    print("数据载入完成", file=sys.stderr)
    sig, dn7 = gen_signals(g, st_set)
    print("信号生成完成: %d信号日" % len(sig), file=sys.stderr)
    trades, eq, open_pos = simulate(g, sig, dn7)
    print("回测完成: %d笔平仓, %d笔未平" % (len(trades), len(open_pos)), file=sys.stderr)
    json.dump({"trades": trades, "equity": eq,
               "open": [{"code": p["code"], "seg": p["seg"], "ent_px": p["ent_px"],
                         "ent_date": p["ent_date"]} for p in open_pos]},
              open(os.path.join(OUT, "bt_full_raw.json"), "w", encoding="utf-8"),
              ensure_ascii=False, default=str)
    print("落盘:", os.path.join(OUT, "bt_full_raw.json"), file=sys.stderr)
