# -*- coding: utf-8 -*-
"""swing_10y_daily_stats_20261029.py — 日线十年全量买卖区域统计(2015-2026, 主人令十年真实走势)

口径:
  全宇宙4941只 × 日线全历史(~2900日) × ATR自适应zigzag(max(5%, ATR14×2.2) 无重绘)
  每日方向仲裁(up/dn/forming/none, 只用确认日≤当日的pivot)
  买卖区域事件(日线版):
    B1 回踩MA20缩量不破前低(涨波段回踩/跌段低位的区别由方向维度承担)
    B2 放量突破60日前高(量≥1.5×均量, 涨停日剔除)
    B3 二次探底不破(20日内两触前低区, 第二次缩量不更低+阳线)
  防追高: 距20日低点≤10%
  收益: fwd10/20/40/60 (收盘→收盘)
分组: 方向(dstate) × 结构(B1/B2/B3) × 位置(pos250三分) × 年份段(2015-17/18-20/21-23/24-26)
"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
t0 = time.time()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
uni = [r[0] for r in con.execute("select distinct code from stock_daily_meta_qfq order by code")]
uni = [c for c in uni if c[:3] not in ("688", "689") and c[0] not in ("4", "8", "9")]
rows = con.execute(
    "select code,date,open,high,low,close,volume from stock_daily_meta_qfq order by code,date").fetchall()
con.close()
DL = {}
for code, d, o, h, l, c, v in rows:
    s = DL.setdefault(code, {"d": [], "o": [], "h": [], "l": [], "c": [], "v": []})
    s["d"].append(int(d)); s["o"].append(float(o)); s["h"].append(float(h))
    s["l"].append(float(l)); s["c"].append(float(c)); s["v"].append(float(v or 0))
for code, s in DL.items():
    for k in s:
        s[k] = np.array(s[k], dtype=float)
print(f"日线全量: {len(DL)}只 ({time.time()-t0:.0f}s)", flush=True)

def sma_arr(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a, 0, 0.0)); out[w-1:] = (cs[w:]-cs[:-w])/w
    return out


def atr_zz(hi, lo, cl, atr_p=14, mult=2.2, min_pct=5.0):
    """返回 pivots[(idx,price,typ,confirm_idx)], trend"""
    n = len(cl)
    tr = np.full(n, np.nan)
    for i in range(1, n):
        tr[i] = max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
    tr[0] = hi[0]-lo[0]
    k = 1/atr_p
    atr = np.empty(n); atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = atr[i-1] + k*(tr[i]-atr[i-1])
    piv = []
    trend = 0; ext_i = 0
    for i in range(1, n):
        thr = max(min_pct/100.0, atr[i]/max(cl[i], 1e-9)*mult)
        r = cl[i]/cl[ext_i] - 1
        typ_l = piv[-1][2] if piv else None
        if trend >= 0 and r <= -thr:
            typ = "H" if trend == 1 else ("L" if trend == -1 else ("H" if cl[ext_i] >= cl[0] else "L"))
            piv.append((ext_i, cl[ext_i], typ, i))
            trend = -1; ext_i = i
        elif trend <= 0 and r >= thr:
            typ = "H" if trend == 1 else ("L" if trend == -1 else ("H" if cl[ext_i] >= cl[0] else "L"))
            piv.append((ext_i, cl[ext_i], typ, i))
            trend = 1; ext_i = i
        else:
            if (trend >= 0 and cl[i] > cl[ext_i]) or (trend <= 0 and cl[i] < cl[ext_i]) or trend == 0:
                ext_i = i
    return piv, trend


ZZ = {}

def state_at(code, di):
    key = (code, di // 500)
    zz = ZZ.get(code)
    if zz is None:
        s = DL[code]
        if len(s["c"]) < 60:
            ZZ[code] = ([], 0)
            return "none"
        ZZ[code] = atr_zz(s["h"], s["l"], s["c"])
        zz = ZZ[code]
    piv, forming = zz
    conf = [p for p in piv if p[3] <= di]
    if len(conf) < 2:
        return "forming_up" if forming == 1 else ("forming_dn" if forming == -1 else "none")
    a, b = conf[-2], conf[-1]
    seg_up = a[2] == "L" and b[2] == "H"
    seg_dn = a[2] == "H" and b[2] == "L"
    if seg_up and len(conf) >= 3:
        a0 = conf[-3]
        if a0[2] == "L" and a[1] > a0[1]:
            return "up"
    if seg_dn and len(conf) >= 3:
        a0 = conf[-3]
        if a0[2] == "H" and b[1] < a0[1]:
            return "dn"
    if seg_up: return "up"
    if seg_dn: return "dn"
    return "forming_up" if forming == 1 else ("forming_dn" if forming == -1 else "none")


REC = []
scanned = 0
for code, s in DL.items():
    o, h, l, c, v, dd = s["o"], s["h"], s["l"], s["c"], s["v"], s["d"]
    n = len(c)
    if n < 300:
        continue
    piv, forming = atr_zz(h, l, c)
    ZZ[code] = (piv, forming)
    ma20 = sma_arr(c, 20); ma60 = sma_arr(c, 60)
    v5 = sma_arr(v, 5); v20 = sma_arr(v, 20)
    tr = np.full(n, np.nan)
    for i in range(1, n):
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    k = 1/14
    atr = np.empty(n); atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = atr[i-1] + k*(tr[i]-atr[i-1])
    # pivots -> 结构位
    swing_hi = np.full(n, np.nan)
    for p in piv:
        if p[2] == "H":
            swing_hi[p[0]] = p[1]
    last_swing = np.full(n, np.nan); cur = np.nan
    for i in range(n):
        if not np.isnan(swing_hi[i]): cur = swing_hi[i]
        last_swing[i] = cur
    swing_lo = np.full(n, np.nan)
    for p in piv:
        if p[2] == "L":
            swing_lo[p[0]] = p[1]
    last_swing_lo = np.full(n, np.nan); cur = np.nan
    for i in range(n):
        if not np.isnan(swing_lo[i]): cur = swing_lo[i]
        last_swing_lo[i] = cur
    conf_flag = np.zeros(n, dtype=bool)
    pi = 0
    for i in range(n):
        while pi < len(piv) and piv[pi][3] <= i:
            pi += 1
        conf_flag[i] = pi >= 2
    # 前低: 最近已确认swing低点(前60日内最低的低点pivot)
    prev_low = np.full(n, np.nan)
    plo = np.nan
    pi = 0
    for i in range(n):
        while pi < len(piv) and piv[pi][3] <= i:
            if piv[pi][2] == "L":
                plo = piv[pi][1]
            pi += 1
        prev_low[i] = plo
    hi60 = sma_arr(h, 1)
    hi60v = np.full(n, np.nan)
    for i in range(60, n):
        hi60v[i] = h[i-60:i].max()
    lo20 = np.full(n, np.nan)
    for i in range(19, n):
        lo20[i] = l[i-19:i+1].min()
    lo250v = np.full(n, np.nan); hi250v = np.full(n, np.nan)
    for i in range(249, n):
        lo250v[i] = l[i-249:i+1].min(); hi250v[i] = h[i-249:i+1].max()
    cnt_up = np.cumsum((c > np.concatenate([[c[0]], c[:-1]])).astype(int))
    for i in range(260, n-60):
        if not conf_flag[i]:
            continue
        cl_i = c[i]
        if cl_i <= 0:
            continue
        dstate = state_at(code, i)
        if dstate == "none":
            continue
        # 防追高
        lp = lo20[i]
        if lp > 0 and cl_i/lp - 1 > 0.10:
            continue
        # 涨停剔除
        if i > 0 and c[i-1] > 0 and c[i]/c[i-1]-1 >= 0.095:
            continue
        pos = (cl_i-lo250v[i])/max(hi250v[i]-lo250v[i], 1e-9)*100 if hi250v[i] > lo250v[i] else 50.0
        pos_b = "low" if pos <= 30 else ("mid" if pos <= 60 else "high")
        year = dd[i]//10000
        yr_seg = "2015-17" if year <= 2017 else ("2018-20" if year <= 2020 else ("2021-23" if year <= 2023 else "2024-26"))
        # 结构检测
        struct = None
        # B1 回踩MA20缩量不破前低
        if (not np.isnan(ma20[i])) and abs(cl_i/ma20[i]-1) <= 0.03 and cl_i >= ma20[i]*0.97:
            if v[i] <= v20[i]*0.8 and (np.isnan(prev_low[i]) or l[i] >= prev_low[i]*0.97):
                struct = "B1"
        # B2 突破60日前高放量
        if struct is None and (not np.isnan(hi60v[i])) and cl_i > hi60v[i]:
            if v[i] >= 1.5*v5[i] if not np.isnan(v5[i]) else False:
                struct = "B2"
        # B3 二次探底不破
        if struct is None and i >= 60:
            w = slice(i-60, i)
            t2 = sum(1 for kk in range(i-60, i) if not np.isnan(prev_low[kk]) and l[kk] <= prev_low[kk]*1.02)
            if t2 >= 2 and l[i] >= np.nanmin(l[i-10:i]) and v[i] <= v20[i]*0.8 and cl_i > o[i]:
                struct = "B3"
        if not struct:
            continue
        # 收益
        fr = {}
        for hold, key in ((10, "f10"), (20, "f20"), (40, "f40"), (60, "f60")):
            j = i + hold
            fr[key] = round(float(c[j]/cl_i-1)*100, 2) if j < n else None
        REC.append({"code": code, "d": dd[i], "struct": struct, "dstate": dstate,
                    "pos": pos_b, "yr": yr_seg,
                    "f10": fr["f10"], "f20": fr["f20"], "f40": fr["f40"], "f60": fr["f60"]})
    scanned += 1
    if scanned % 500 == 0:
        print(f"...{scanned}/{len(DL)} 事件{len(REC)} ({time.time()-t0:.0f}s)", flush=True)

print(f"\n日线十年扫描完成: {scanned}只 事件{len(REC)} ({time.time()-t0:.0f}s)", flush=True)

def st(rows, key):
    a = np.array([r[key] for r in rows if r.get(key) is not None], dtype=float)
    if not len(a): return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean()), 2), "win": round(float((a > 0).mean())*100, 1),
            "med": round(float(np.median(a)), 2), "p10": round(float(np.percentile(a, 10)), 2)}

R = {"n_events": len(REC), "overall_f20": st(REC, "f20"), "overall_f40": st(REC, "f40"),
     "overall_f60": st(REC, "f60"), "groups": {}}
for ds in ("up", "dn", "forming_up", "forming_dn"):
    rows_d = [r for r in REC if r["dstate"] == ds]
    R["groups"][f"dir_{ds}"] = st(rows_d, "f20")
    for p in ("low", "mid", "high"):
        R["groups"][f"{ds}_{p}"] = st([r for r in rows_d if r["pos"] == p], "f20")
    for sv in ("B1", "B2", "B3"):
        R["groups"][f"{ds}_{sv}"] = st([r for r in rows_d if r["struct"] == sv], "f20")
    for ys in ("2015-17", "2018-20", "2021-23", "2024-26"):
        R["groups"][f"{ds}_{ys}"] = st([r for r in rows_d if r["yr"] == ys], "f20")
out = os.path.join(ROOT, "data", "swing_10y_daily_stats_20261029.json")
json.dump({"built_at": "20261029", "summary": R, "n_scanned": scanned},
          open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
outl = os.path.join(ROOT, "data", "swing_10y_daily_events_20261029.jsonl")
with open(outl, "w", encoding="utf-8") as f:
    for r in REC:
        f.write(json.dumps(r, ensure_ascii=False)+"\n")

s = R
print("\n=== 总体(日线十年) ===")
print(f"f20: {R['overall_f20']} | f40: {R['overall_f40']} | f60: {R['overall_f60']}")
print("\n=== 分组矩阵(fwd20) ===")
for k, v in R["groups"].items():
    if v.get("n"):
        print(f"  {k:<24} n={v['n']:>6} 均值{v['mean']:>+7}% 胜率{v['win']:>5}% p10{v['p10']:>+7}%")
print(f"\n落盘: {out} + {outl}")
