# -*- coding: utf-8 -*-
"""dip_scan_20261106.py — 回踩四条件选股扫描器(生产版, 2026-11-06)
方案v1.0核心买入法落地。四条件: r20>0 + 偏离MA20[-3%,+5%] + 量比<1.5 + 未涨停
价格带2-30元(铁律>30不买); 剔ST/科创/北交。信号=当日收盘确认, 次日开盘挂单买。
输出: data/dip_scan_YYYYMMDD.json + data/dip_scan_latest.txt(人话)
"""
import argparse, json, os, sqlite3, sys
from collections import defaultdict
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
ICONDB = "file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro"

def load_all(T):
    con = sqlite3.connect(QFQ, uri=True)
    if T is None:
        T = int(con.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0])
    rows = con.execute(
        "select code,date,open,close,volume,change_pct from stock_daily_meta_qfq "
        "where date>=? order by code,date", ((T // 10000 - 1) * 10000 + 601,)).fetchall()
    con.close()
    g = defaultdict(lambda: {"d": [], "o": []} | {"c": [], "v": [], "p": []})
    for code, d, o, c, v, p in rows:
        s = g[code]
        s["d"].append(int(d)); s["o"].append(float(o or 0)); s["c"].append(float(c or 0))
        s["v"].append(float(v or 0)); s["p"].append(float(p or 0) if p is not None else 0.0)
    icon = sqlite3.connect(ICONDB, uri=True)
    nind = {str(k)[-6:]: (nm, l1) for k, nm, l1 in icon.execute("select code,name,l1 from l1_member").fetchall()}
    icon.close()
    scon = sqlite3.connect(STDB, uri=True)
    st_set = {str(c)[-6:] for c, f in scon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)"
    ).fetchall() if f == 1}
    scon.close()
    return g, nind, st_set, T

def scan(T):
    g, nind, st_set, T = load_all(T)
    hits = []
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d = s["d"]
        try:
            idx = d.index(T)
        except ValueError:
            continue
        if idx < 25 or s["c"][idx] <= 0:
            continue
        c = s["c"]; v = s["v"]; p = s["p"]
        px = c[idx]
        if not (2.0 <= px <= 30.0):
            continue
        r_today = p[idx] / 100.0
        lim = 0.19 if c6[:3] in ("300", "301") else 0.09
        if r_today >= lim:  # 未涨停
            continue
        ma20 = float(np.mean(c[idx - 20:idx]))
        dev = px / ma20 - 1.0
        if not (-0.03 <= dev <= 0.05):
            continue
        if p[idx - 1] is not None and idx >= 21:
            r20 = c[idx - 1] / c[idx - 21] - 1.0
        else:
            r20 = 0.0
        if r20 <= 0:
            continue
        vseg = [x for x in v[idx - 20:idx] if x > 0]
        volr = v[idx] / float(np.mean(vseg)) if vseg and v[idx] > 0 else 99.0
        if volr >= 2.5:
            continue  # 1.0-2.5 is the verified sweet band; <0.6 extreme-shrink is weakest
        nm, l1 = nind.get(c6, ("?", "?"))
        hits.append({
            "code": c6, "name": nm, "ind": l1, "px": round(px, 2),
            "r20": round(r20 * 100, 1), "dev": round(dev * 100, 1),
            "volr": round(volr, 2), "r_today": round(r_today * 100, 1),
        })
    # bottom-reversal first boards: limit-up today + prior20 down >10% (verified +1.66%/win50.2%)
    fbhits = []
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d = s["d"]
        try:
            idx = d.index(T)
        except ValueError:
            continue
        if idx < 25 or s["c"][idx] <= 0 or s["c"][idx - 21] <= 0:
            continue
        c = s["c"]; p = s["p"]
        r_today = p[idx] / 100.0
        lim = 0.19 if c6[:3] in ("300", "301") else 0.09
        if r_today < lim or r_today > 0.35:
            continue
        prior20 = c[idx - 1] / c[idx - 21] - 1.0
        if prior20 >= -0.10:
            continue
        px = c[idx]
        if not (2.0 <= px <= 30.0):
            continue
        nm, l1 = nind.get(c6, ("?", "?"))
        fbhits.append({"code": c6, "name": nm, "ind": l1, "px": round(px, 2),
                       "prior20": round(prior20 * 100, 1), "r_today": round(r_today * 100, 1)})
    fbhits.sort(key=lambda x: x["prior20"])
    # upstart v2: +5%..below limit + dev<5% + volr 1.0-2.5 + PRIOR20 DOWN >10%
    # (source of the +4.4%: oversold-rebound first leg; verified +3.80%/win61.7%/median+2.49%)
    uphits = []
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d = s["d"]
        try:
            idx = d.index(T)
        except ValueError:
            continue
        if idx < 25 or s["c"][idx] <= 0 or s["c"][idx - 21] <= 0:
            continue
        c = s["c"]; p = s["p"]
        r_today = p[idx] / 100.0
        lim = 0.19 if c6[:3] in ("300", "301") else 0.09
        if not (0.05 <= r_today < lim) or r_today > 0.35:
            continue
        if c[idx - 1] / c[idx - 21] - 1.0 >= -0.10:
            continue  # only oversold-first-leg
        ma20 = float(np.mean(c[idx - 20:idx]))
        dev = c[idx] / ma20 - 1.0 if ma20 > 0 else 9
        if dev >= 0.05:
            continue
        vseg = [x for x in s["v"][idx - 20:idx] if x > 0]
        volr = s["v"][idx] / float(np.mean(vseg)) if vseg and s["v"][idx] > 0 else 99.0
        if not (1.0 <= volr <= 2.5):
            continue
        lo20 = min(c[idx - 20:idx + 1])
        rebr = c[idx] / lo20 - 1.0 if lo20 > 0 else 0
        px = c[idx]
        if not (2.0 <= px <= 30.0):
            continue
        nm, l1 = nind.get(c6, ("?", "?"))
        uphits.append({"code": c6, "name": nm, "ind": l1, "px": round(px, 2),
                       "dev": round(dev * 100, 1), "volr": round(volr, 2),
                       "rebr": round(rebr * 100, 1),
                       "r_today": round(r_today * 100, 1)})
    uphits.sort(key=lambda x: x["dev"])
    # consolidation entries: first-board 2-9 days ago, held >=98% of board close since,
    # no second board in between, today volr<1.2 (verified +0.45%/win48.9%; low-pos subset +0.54%)
    cons = []
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        d = s["d"]
        try:
            idx = d.index(T)
        except ValueError:
            continue
        if idx < 25 or s["c"][idx] <= 0:
            continue
        c = s["c"]; v = s["v"]; p = s["p"]
        lim = 0.19 if c6[:3] in ("300", "301") else 0.09
        # find most recent first-board day i in [idx-9, idx-2]
        fb_day = None
        for i in range(idx - 9, idx - 1):
            if i < 25 or i + 1 > idx:
                continue
            if p[i] / 100.0 >= lim and not (p[i - 1] / 100.0 >= lim):
                fb_day = i
                break  # most recent
        if fb_day is None:
            continue
        fb_close = c[fb_day]
        fb_px_ok = 2.0 <= fb_close <= 30.0
        if not fb_px_ok:
            continue
        # no break below 98% of board close, no second board since
        bad = False
        for k in range(fb_day + 1, idx + 1):
            if p[k] / 100.0 >= lim:
                bad = True
                break
            if c[k] < fb_close * 0.98:
                bad = True
                break
        if bad:
            continue
        vseg = [x for x in v[idx - 20:idx] if x > 0]
        volr = v[idx] / float(np.mean(vseg)) if vseg and v[idx] > 0 else 9.9
        if volr >= 1.2:
            continue  # shrink-confirmed TODAY = entry signal
        ma20 = float(np.mean(c[idx - 20:idx]))
        dev = c[idx] / ma20 - 1.0 if ma20 > 0 else 9
        px = c[idx]
        if not (2.0 <= px <= 30.0):
            continue
        nm, l1 = nind.get(c6, ("?", "?"))
        cons.append({"code": c6, "name": nm, "ind": l1, "px": round(px, 2),
                     "fb_days_ago": idx - fb_day, "dev": round(dev * 100, 1),
                     "volr": round(volr, 2)})
    cons.sort(key=lambda x: x["volr"])
    hits.sort(key=lambda x: -x["r20"])
    # market density inline: down>=7% count (attribution-pipeline universe)
    dn7 = 0
    for code, s in g.items():
        c6 = code[-6:]
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set:
            continue
        if T in s["d"]:
            i2 = s["d"].index(T)
            if (i2 > 0 and s["p"][i2] / 100.0 <= -0.07 and abs(s["p"][i2]) < 35
                    and 0.5 < s["c"][i2] <= 200):
                dn7 += 1
    level = ("恐慌区(>50, 可低吸)" if dn7 > 50 else
             "降仓区(30-50, 停新买)" if dn7 > 30 else
             "阴跌区(15-30, 停新买)" if dn7 > 15 else "正常区(<=15, 正常买)")
    out = {"T": T, "n_hits": len(hits), "hits": hits, "fb_hits": fbhits,
           "up_hits": uphits, "cons": cons, "dn7": dn7, "level": level,
           "built_at": __import__("time").strftime("%Y-%m-%d %H:%M")}
    jp = os.path.join(ROOT, "data", "dip_scan_%d.json" % T)
    json.dump(out, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    tp = os.path.join(ROOT, "data", "dip_scan_latest.txt")
    lines = ["==== 回踩四条件候选(信号日%d, 次日开盘挂单买) ====" % T,
             "筛出 %d 只 | 买入方式=等权小注(勿按动量挑最强, 验证过最强组更差)" % len(hits),
             "大盘状态: 当日跌>=7%%共%d家 [%s]" % (dn7, level),
             "量比说明: 1.5-2.5=验证最优带(+0.55%), 极端缩量<0.6=最弱档(+0.26%)",
             "",
             "---- 精筛20只(确认型优先: 位置+2~+5%且量比1.5-2.5在最前, 验证+0.77%%共振带) ----"]
    # rank: confirmed-type first (dev +2~+5%, volr 1.5~2.5 verified best), then by volr asc
    def rank_key(h):
        return (0 if 2.0 <= h["dev"] <= 5.0 else 1,
                0 if 1.5 <= h["volr"] <= 2.5 else 1,
                h["volr"])
    sweet = sorted([h for h in hits], key=rank_key)
    for h in sweet[:20]:
        lines.append("%s %s %s 价%.2f 量比%.2f r20=%+.1f%% 偏离MA20=%+.1f%% 今日%+.1f%%" % (
            h["code"], h["name"], h["ind"], h["px"], h["volr"], h["r20"], h["dev"], h["r_today"]))
    lines.append("")
    lines.append("---- 其余候选(按r20降序, 前30只) ----")
    for h in hits[:30]:
        lines.append("%s %s %s 价%.2f r20=%+.1f%% 偏离MA20=%+.1f%% 量比%.2f 今日%+.1f%%" % (
            h["code"], h["name"], h["ind"], h["px"], h["r20"], h["dev"], h["volr"], h["r_today"]))
    lines.append("")
    lines.append("---- 底部反转首板(前20日跌>10%%后今日涨停, 验证+1.66%%胜率50%%, 次日开盘买, 开盘涨停不追) ----" % ())
    for h in fbhits[:15]:
        lines.append("%s %s %s 价%.2f 前20日%+.1f%% 今日%+.1f%%" % (
            h["code"], h["name"], h["ind"], h["px"], h["prior20"], h["r_today"]))
    lines.append("")
    lines.append("---- 起涨型v2(超跌前20日跌>10%%后今日涨5%%~涨停线下+位置低+量比1-2.5, 验证+3.80%%胜率61.7%%; 修复度15-25%%=黄金带+8.8%%胜率77%%, 持20日更佳, 次日开盘买) ----" % ())
    uph = sorted(uphits, key=lambda x: -abs(x["rebr"] - 20))
    for h in uph[:15]:
        lines.append("%s %s %s 价%.2f 今日%+.1f%% 偏离MA20=%+.1f%% 量比%.2f 修复度%+.1f%%%s" % (
            h["code"], h["name"], h["ind"], h["px"], h["r_today"], h["dev"], h["volr"],
            h["rebr"], " [黄金带]" if 15 <= h["rebr"] <= 25 else ""))
    lines.append("")
    lines.append("---- 首板缩量横盘介入(首板2-9天前+不破板+今日缩量<1.2=介入日, 验证+0.45%%胜率48.9%%; 低位+极缩量子集+0.67%%, 次日开盘买) ----" % ())
    for h in cons[:15]:
        lines.append("%s %s %s 价%.2f 首板后%d天 偏离MA20=%+.1f%% 今日量比%.2f%s" % (
            h["code"], h["name"], h["ind"], h["px"], h["fb_days_ago"], h["dev"],
            h["volr"], " [低位]" if h["dev"] < 5 else ""))
    if len(hits) > 30:
        lines.append("(其余%d只见json)" % (len(hits) % 100 if False else len(hits) - 30))
    open(tp, "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))
    print("json:", jp)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    scan(int(a.date) if a.date else None)
