# -*- coding: utf-8 -*-
"""swing_m60_fullsurvey2_20261029.py — 全宇宙买卖点统计 v2(主人工程流水线规范落地)

升级点(vs v1):
  ① 日线波段=ATR自适应ZigZag: threshold=max(5%, ATR14/price×100×2.2), 无重绘——
     只输出已确认pivot序列; forming leg单独标记不作已确认样本
  ② 日线方向仲裁: 最近两个已确认pivot「低点抬高+高点抬高」=up确认(60分信号有效);
     forming leg上涨=dir_forming(仓位减半组); 其余=dn/none(信号仍统计用于「什么会跌」)
  ③ T+1成交对齐: entry=信号日尾盘bar(15:00)收盘; fwdN从次一交易日起算
  ④ 涨跌停拦截: 信号日收盘涨幅≥9.5%(近似涨停不可买)→剔除
分组矩阵: 日线状态(up/dn/forming/none) × 结构(B1/B2/B3) × pos250(low/mid/high) × wf环境
输出: 全信号明细jsonl + 四维分组矩阵json
"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
M60DIR = os.path.join(ROOT, "data", "m60_full")
t0 = time.time()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
uni = [r[0] for r in con.execute("select distinct code from stock_daily_meta_qfq order by code")]
uni = [c for c in uni if c[:3] not in ("688", "689") and c[0] not in ("4", "8", "9")]
dates_all = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=20250601 order by date")})
didx = {d: i for i, d in enumerate(dates_all)}
dmin = dates_all[0]
print(f"宇宙{len(uni)}只 日线窗口{dmin}起{len(dates_all)}日 ({time.time()-t0:.0f}s)", flush=True)

# ── 全量日线(6/1起, OHLClose) ──
rows = con.execute(
    "select code,date,open,high,low,close,volume from stock_daily_meta_qfq where date>=? order by code,date",
    (dmin,)).fetchall()
con.close()
DL = {}
for code, d, o, h, l, c, v in rows:
    d = int(d)
    s = DL.setdefault(code, {"d": [], "o": [], "h": [], "l": [], "c": [], "v": []})
    s["d"].append(d); s["o"].append(float(o)); s["h"].append(float(h))
    s["l"].append(float(l)); s["c"].append(float(c)); s["v"].append(float(v or 0))
for code, s in DL.items():
    for k in s:
        s[k] = np.array(s[k], dtype=float)
print(f"日线载入{len(DL)}只 ({time.time()-t0:.0f}s)", flush=True)

from stock_name_lookup import get_name

def atr_zigzag_pivots(hi, lo, cl, atr_p=14, mult=2.2, min_pct=5.0):
    """ATR自适应无重绘zigzag: 已确认pivot序列 [(idx, price, type, confirm_idx)]
    confirm_idx=反向阈值首次达成日(该pivot于此日才可见, 无前视); forming_dir=末腿方向"""
    n = len(cl)
    if n < 40:
        return [], None
    tr = np.full(n, np.nan)
    for i in range(1, n):
        tr[i] = max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
    tr[0] = hi[0]-lo[0]
    k = 1/atr_p
    atr = np.empty(n); atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = atr[i-1] + k*(tr[i]-atr[i-1])
    pivots = []
    trend = 0; ext_i = 0
    for i in range(1, n):
        thr = max(min_pct/100.0, atr[i]/max(cl[i], 1e-9)*mult)
        r = cl[i]/cl[ext_i] - 1
        typ_last = pivots[-1][2] if pivots else None
        if trend >= 0 and r <= -thr:
            typ = "H" if trend == 1 else ("L" if trend == -1 else ("H" if cl[ext_i] >= cl[0] else "L"))
            pivots.append((ext_i, cl[ext_i], typ, i))
            trend = -1; ext_i = i
        elif trend <= 0 and r >= thr:
            typ = "H" if trend == 1 else ("L" if trend == -1 else ("H" if cl[ext_i] >= cl[0] else "L"))
            pivots.append((ext_i, cl[ext_i], typ, i))
            trend = 1; ext_i = i
        else:
            if (trend >= 0 and cl[i] > cl[ext_i]) or (trend <= 0 and cl[i] < cl[ext_i]) or trend == 0:
                ext_i = i
    return pivots, trend


ZZ_CACHE = {}


def daily_state_at(code, di):
    """日线方向仲裁(无前视): 只用确认日<=di的已确认pivot。
    up=最近两pivot L→H且低点抬高; dn=H→L且高点下移低点下移;
    forming_up/dn=末腿方向未确认; none=不足"""
    s = DL.get(code)
    if s is None or di < 40 or di >= len(s["c"]):
        return "none"
    key = (code, len(s["c"]))
    if key not in ZZ_CACHE:
        ZZ_CACHE[key] = atr_zigzag_pivots(s["h"], s["l"], s["c"])
    piv, forming = ZZ_CACHE[key]
    conf = [p for p in piv if p[3] <= di]
    if len(conf) < 2:
        return "forming_up" if forming == 1 else ("forming_dn" if forming == -1 else "none")
    a, b = conf[-2], conf[-1]
    seg_up = a[2] == "L" and b[2] == "H"
    seg_dn = a[2] == "H" and b[2] == "L"
    if seg_up and len(conf) >= 3:
        a0 = conf[-3]
        if a0[2] == "L" and a[1] > a0[1]:   # 低点抬高
            return "up"
    if seg_dn and len(conf) >= 3:
        a0 = conf[-3]
        if a0[2] == "H" and b[1] < a0[1]:   # 高点下移
            return "dn"
    if seg_up:
        return "up"
    if seg_dn:
        return "dn"
    return "forming_up" if forming == 1 else ("forming_dn" if forming == -1 else "none")


def sma_arr(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a, 0, 0.0)); out[w-1:] = (cs[w:]-cs[:-w])/w
    return out


REC = []
scanned = 0
t1 = time.time()
for code in uni:
    f = os.path.join(M60DIR, f"{code}.json")
    if not os.path.exists(f):
        continue
    try:
        bars = json.load(open(f, encoding="utf-8"))["bars"]
    except Exception:
        continue
    if len(bars) < 200:
        continue
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    cl = np.array([b["c"] for b in bars]); op = np.array([b["o"] for b in bars])
    lo = np.array([b["l"] for b in bars]); hi = np.array([b["h"] for b in bars])
    vv = np.array([b["v"] for b in bars])
    dt = [b["dt"] for b in bars]
    bad = False
    for i in range(1, len(cl)):
        if cl[i-1] > 0 and abs(cl[i]/cl[i-1]-1) > 0.35:
            bad = True; break
    if bad:
        continue
    n = len(cl)
    ma20, ma60 = sma_arr(cl, 20), sma_arr(cl, 60)
    v5, v10 = sma_arr(vv, 5), sma_arr(vv, 10)
    prev_v = np.full(n, np.nan)
    for i in range(80, n):
        prev_v[i] = vv[i-80:i-20].mean()
    e12 = np.empty(n); e26 = np.empty(n)
    e12[0] = e26[0] = cl[0]
    for i in range(1, n):
        e12[i] = 2/13*cl[i]+(1-2/13)*e12[i-1]
        e26[i] = 2/27*cl[i]+(1-2/27)*e26[i-1]
    dif = e12-e26
    dea = np.empty(n); dea[0] = dif[0]
    for i in range(1, n):
        dea[i] = 0.2*dif[i]+0.8*dea[i-1]
    swing_hi = np.full(n, np.nan)
    for i in range(2, n-2):
        if hi[i] >= hi[i-2] and hi[i] >= hi[i-1] and hi[i] >= hi[i+1] and hi[i] >= hi[i+2]:
            swing_hi[i] = hi[i]
    last_swing = np.full(n, np.nan); cur = np.nan
    for i in range(n):
        if not np.isnan(swing_hi[i]): cur = swing_hi[i]
        last_swing[i] = cur
    low20 = np.array([lo[max(0, i-20):i+1].min() for i in range(n)])
    px = {}
    for b in bars:
        d8 = int(b["dt"][:10].replace("-", ""))
        px[d8] = b["c"]
    dstate_cache = None
    for i in range(120, n-45):
        d_day = int(dt[i][:10].replace("-", ""))
        d_str = dt[i][:10]
        px_d = px.get(d_day)
        if px_d is None:
            continue
        lp = low20[i]
        if lp <= 0 or cl[i]/lp-1 > 0.10:
            continue
        # 涨跌停拦截: 60分bar收盘涨幅≥9.5%不可买
        if i > 0 and cl[i-1] > 0 and cl[i]/cl[i-1]-1 >= 0.095:
            continue
        in_zone = (min(ma20[i], ma60[i])*0.99) <= cl[i] <= (max(ma20[i], ma60[i])*1.02)
        vol_shrink = vv[i] <= prev_v[i]*0.5 or (not np.isnan(v10[i]) and vv[i] <= v10[i]*0.8)
        body_small = 0 < cl[i]-op[i] <= cl[i]*0.008
        lower_sh = (min(op[i], cl[i])-lo[i])/max(cl[i], 1e-9) >= 0.004
        golden = dif[i] > dea[i] and dif[i-1] <= dea[i-1] and dif[i] > 0
        ma60_up = ma60[i] >= ma60[max(0, i-3)]
        stop_sig = lower_sh >= 0.004 or body_small or golden
        v5v = v5[i] if not np.isnan(v5[i]) else vv[max(0, i-5):i+1].mean()
        vol_break = vv[i] >= 1.5*v5v and vv[i] < 3*v5v
        struct = None
        if in_zone and vol_shrink and stop_sig and ma60_up:
            struct = "B1"
        elif (not np.isnan(last_swing[i-1])) and cl[i] > last_swing[i-1] and cl[i-1] <= last_swing[i-1] and vol_break:
            struct = "B2"
        touches = sum(1 for kk in range(max(0, i-20), i+1) if lo[kk] <= ma60[kk]*1.01)
        if struct is None and touches >= 2 and lo[i] >= lo[max(0, i-10):i].min()-1e-9 and vol_shrink and cl[i] > op[i]:
            struct = "B3"
        if not struct:
            continue
        # T+1成交: entry=信号日尾盘bar收盘(当日最后一根)
        day = dt[i][:10]
        tail = entry_i = i
        for j in range(i, min(i+4, n)):
            if dt[j][:10] == day:
                entry_i = j
        entry = cl[entry_i]
        d_entry = int(dt[entry_i][:10].replace("-", ""))
        gi_entry = didx.get(d_entry)
        if gi_entry is None:
            continue
        f10 = f20 = f40 = None
        if gi_entry+11 < len(dates_all):
            d10 = dates_all[gi_entry+11]
            f10 = px.get(d10)
            f10 = (f10/entry-1)*100 if f10 else None
        if gi_entry+21 < len(dates_all):
            d20 = dates_all[gi_entry+21]
            f20 = px.get(d20)
            f20 = (f20/entry-1)*100 if f20 else None
        if gi_entry+41 < len(dates_all):
            d40 = dates_all[gi_entry+41]
            f40 = px.get(d40)
            f40 = (f40/entry-1)*100 if f40 else None
        # 日线状态(无前视仲裁: 只用确认日<=信号日的已确认pivot)
        import bisect as _b
        s = DL.get(code)
        di = None
        if s is not None and len(s["d"]):
            ds_int = [int(x) for x in s["d"]]
            di = _b.bisect_right(ds_int, d_entry) - 1
        dstate = daily_state_at(code, di) if (di is not None and di >= 0) else "none"
        lo250 = cl[max(0, i-1000):i+1].min() if i >= 1000 else cl[:i+1].min()
        hi250 = cl[max(0, i-1000):i+1].max() if i >= 1000 else cl[:i+1].max()
        pos250 = (cl[i]-lo250)/max(hi250-lo250, 1e-9)*100 if hi250 > lo250 else 50.0
        REC.append({"code": code, "name": nm, "dt": dt[i][:16], "struct": struct,
                    "dstate": dstate,
                    "pos": "low" if pos250 <= 30 else ("mid" if pos250 <= 60 else "high"),
                    "f10": round(f10, 2) if f10 is not None else None,
                    "f20": round(f20, 2) if f20 is not None else None,
                    "f40": round(f40, 2) if f40 is not None else None})
    scanned += 1
    if scanned % 400 == 0:
        print(f"...{scanned}/{len(uni)} 信号{len(REC)} ({time.time()-t1:.0f}s)", flush=True)

print(f"\n扫描完成: {scanned}只 信号{len(REC)} ({time.time()-t1:.0f}s)", flush=True)
out = os.path.join(ROOT, "data", "swing_m60_fullsurvey2_20261029.jsonl")
with open(out, "w", encoding="utf-8") as f:
    for r in REC:
        f.write(json.dumps(r, ensure_ascii=False)+"\n")
print(f"落盘: {out}")
