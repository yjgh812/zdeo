# -*- coding: utf-8 -*-
"""swing_m60_v5_exec_20261029.py — v5: 执行层完整闭环(主人参数级文档·双侧)

新增约束:
  ① T+1对齐: 信号任意bar触发 → 成交价=当日尾盘bar(15:00)收盘; 无尾盘bar(信号即尾盘)用信号bar收盘
  ② 假突破过滤(B2): 突破bar量≥1.5×且<3×前5均量; 突破后3根60分K收盘站稳突破位才成交(第3根尾盘价入场)
  ③ 棘轮细颗粒: 浮盈每+1×ATR → 止损上移0.5×ATR(只上移不下移)
  ④ 分档止盈: +3×ATR减半仓锁定, 剩余半仓棘轮跟踪
  ⑤ 结构止损: 放量破前低3根不收回; 时间止损: 15根浮盈<1%; 到期: 80根
双侧: 成功侧40只(7/22 up起点) + 失败侧40只(dn起点), 对照=hold20与v3旧风控链
"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
CUT_UP, CUT_DN = 20260722, None

con = sqlite3.connect("file:D:/zcodesj/data/swing_labels.db?mode=ro", uri=True)
up_rows = con.execute('''select code,start_date,end_date,ret,days from swing_segments
  where seg_type='up' and revised=0 and start_date>='20251001' order by start_date desc''').fetchall()
dn_rows = con.execute('''select code,start_date,end_date,ret,days from swing_segments
  where seg_type='dn' and revised=0 and start_date>='20250901' order by start_date desc''').fetchall()
sw = con
seen = {}
up_s = []
for r in up_rows:
    if r[0][:2] == "68" or r[0][0] in "489": continue
    if r[0] not in seen: seen[r[0]] = r
up_s = list(seen.values())[:40]
seen = {}
dn_s = []
for r in dn_rows:
    if r[0][:2] == "68" or r[0][0] in "489": continue
    if r[0] not in seen: seen[r[0]] = r
dn_s = list(seen.values())[:40]
codes = [r[0] for r in up_s + dn_s]
ph = ",".join("?"*len(codes))
con2 = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
drows = con2.execute(f"select code,date,close from stock_daily_meta_qfq where code in ({ph}) and date>=20260401 order by code,date",
                     codes).fetchall()
con2.close()
daily = defaultdict(list)
for code, d, c in drows:
    daily[code].append((int(d), float(c)))

URL = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
       "symbol={sym}&scale=60&ma=no&datalen=500")
M60 = os.path.join(ROOT, "data", "m60_samples")
M60F = os.path.join(ROOT, "data", "m60_samples_fail")

def get_bars(code, ddir):
    p = os.path.join(ddir, f"{code}.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))["bars"]
    sym = ("sh" if code.startswith(("6","9")) else "sz") + code
    try:
        req = urllib.request.Request(URL.format(sym=sym), headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=15) as r:
            kl = json.loads(r.read().decode("utf-8"))
        bars = [{"dt": b["day"], "o": float(b["open"]), "c": float(b["close"]),
                 "h": float(b["high"]), "l": float(b["low"]), "v": float(b["volume"])} for b in kl]
        json.dump({"code": code, "bars": bars}, open(p, "w", encoding="utf-8"))
        return bars
    except Exception:
        return None

def fwd20(code, d_signal):
    v = daily.get(code, [])
    ds = [d for d, _ in v]; dd = dict(v)
    if d_signal not in dd:
        prv = [x for x in ds if x <= d_signal]
        if not prv: return None
        d_signal = prv[-1]
    i = ds.index(d_signal)
    return dd[ds[i+20]]/dd[ds[i]] - 1 if i+20 < len(ds) else None

def sma(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a, 0, 0.0)); out[w-1:] = (cs[w:]-cs[:-w])/w
    return out

def detect_and_sim(bars, cut_date, side):
    dt = [b["dt"] for b in bars]
    # 数据卫生: 相邻bar收盘跳变>35%(60分K不存在此波动)=复权断点脏数据 → 剔除该票
    cl_all = [b["c"] for b in bars]
    for i in range(1, len(cl_all)):
        if cl_all[i-1] > 0 and abs(cl_all[i]/cl_all[i-1]-1) > 0.35:
            return None
    cut_pos = next((i for i, d in enumerate(dt) if d[:10].replace("-","") == str(cut_date)), None)
    if cut_pos is None or cut_pos < 90:
        return None
    cl = np.array([b["c"] for b in bars]); op = np.array([b["o"] for b in bars])
    lo = np.array([b["l"] for b in bars]); hi = np.array([b["h"] for b in bars])
    vv = np.array([b["v"] for b in bars])
    cut_close = cl[cut_pos]
    ma20, ma60, v5 = sma(cl,20), sma(cl,60), sma(cl,5)
    prev_v = np.full(len(cl), np.nan)
    for i in range(80, len(cl)):
        prev_v[i] = vv[i-80:i-20].mean()
    swing_hi = np.full(len(cl), np.nan)
    for i in range(2, len(cl)-2):
        if hi[i] >= hi[i-2] and hi[i] >= hi[i-1] and hi[i] >= hi[i+1] and hi[i] >= hi[i+2]:
            swing_hi[i] = hi[i]
    last_swing = np.full(len(cl), np.nan); cur = np.nan
    for i in range(len(cl)):
        if not np.isnan(swing_hi[i]): cur = swing_hi[i]
        last_swing[i] = cur
    low20 = np.array([lo[max(0,i-20):i+1].min() for i in range(len(cl))])
    tr = np.full(len(cl), np.nan)
    for i in range(1, len(cl)):
        tr[i] = max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
    atr14 = sma(np.nan_to_num(tr, nan=0.0), 14)
    # 日线方向闸(pivot仲裁近似): 波段起点即"已确认pivot"——up段起点=已确认低点, 窗口内视为方向允许
    fired = None
    for i in range(max(cut_pos-20, 100), min(cut_pos+130, len(cl)-85)):
        if np.isnan(prev_v[i]) or np.isnan(ma20[i]) or np.isnan(ma60[i]) or np.isnan(atr14[i]) or atr14[i] <= 0:
            continue
        lp = low20[i]
        if lp > 0 and cl[i]/lp - 1 > 0.10: continue
        day_off = (i - cut_pos) / 4.0
        if day_off > 8 or cl[i] > cut_close*1.05: continue
        in_zone = min(ma20[i], ma60[i])*0.99 <= cl[i] <= max(ma20[i], ma60[i])*1.02
        vol_shrink = vv[i] <= prev_v[i]*0.5
        body_small = 0 < cl[i]-op[i] <= cl[i]*0.008
        lower_sh = (min(op[i],cl[i])-lo[i])/max(cl[i],1e-9) >= 0.004
        ma60_up = ma60[i] >= ma60[max(0,i-3)]
        vol_break = vv[i] >= 1.5*(v5[i] if not np.isnan(v5[i]) else vv[max(0,i-5):i+1].mean())
        vol_not_blow = vv[i] < 3*(v5[i] if not np.isnan(v5[i]) else vv[max(0,i-5):i+1].mean())
        struct = None
        if in_zone and vol_shrink and (lower_sh or body_small) and ma60_up:
            struct = "B1"
        elif (not np.isnan(last_swing[i-1])) and cl[i] > last_swing[i-1] and cl[i-1] <= last_swing[i-1] \
                and vol_break and vol_not_blow:
            # B2需3根站稳确认→入场推迟到第3根尾盘
            ok3 = i+3 < len(cl) and all(cl[i+k] > last_swing[i-1] for k in (1,2,3))
            if ok3:
                struct = "B2确认"   # 入场bar=i+3
        touches = sum(1 for kk in range(max(0,i-20), i+1) if lo[kk] <= ma60[kk]*1.01)
        if struct is None and touches >= 2 and lo[i] >= lo[max(0,i-10):i].min()-1e-9 and vv[i] < prev_v[i]*0.6 and cl[i] > op[i]:
            struct = "B3"
        if struct:
            entry_i = i+3 if struct == "B2确认" else i
            if entry_i >= len(cl)-85:
                continue
            fired = (entry_i, struct, i)
            break
    if not fired:
        return None
    entry_i, struct, sig_i = fired
    # T+1尾盘执行: 当日最后一根bar收盘价(15:00 bar)
    day_tail = dt[entry_i][:10]
    tail_idx = max((j for j in range(entry_i, min(entry_i+4, len(cl))) if dt[j][:10] == day_tail), default=entry_i)
    entry = cl[tail_i] if (tail_i := tail_idx) is not None else cl[entry_i]
    entry = cl[tail_i]
    d_entry = int(dt[tail_i][:10].replace("-",""))
    r_hold = fwd20(code, d_entry)
    atr0 = atr14[entry_i]
    stop = entry - 1.75*atr0
    lock_half = None
    r_leg1 = None
    exit_leg2 = None; reason = None
    breach = None
    j_end = min(tail_i + 80, len(cl)-1)
    result_leg2 = None
    for j in range(tail_i+1, j_end+1):
        gain_atr = (cl[j]-entry)/max(atr0, 1e-9)
        # 棘轮细颗粒: 每+1×ATR上移0.5×ATR
        if gain_atr >= 1:
            stop = max(stop, entry + (int(gain_atr))*0.5*atr0)
        # 分档: +3×ATR减半
        if lock_half is None and gain_atr >= 3:
            lock_half = entry + 3*atr0
        # 结构止损
        if lo[j] < lo[j-1] and vv[j] >= 1.2*(v5[j] if not np.isnan(v5[j]) else vv[max(0,j-5):j+1].mean()):
            if breach is None: breach = j
            if j - breach >= 3 and cl[j] < lo[j-1]:
                exit_leg2 = cl[j]; reason = "结构止损"; break
        else:
            if breach is not None and cl[j] >= lo[j-1]: breach = None
        if j - tail_i >= 15 and gain_atr < 0.25:
            exit_leg2 = cl[j]; reason = "时间止损"; break
        if lo[j] <= stop:
            exit_leg2 = stop; reason = "棘轮止损"; break
        if j == j_end:
            exit_leg2 = cl[j]; reason = "到期20日"; break
    if lock_half is not None:
        total = 0.5*(lock_half/entry-1) + 0.5*(exit_leg2/entry-1)
    else:
        total = exit_leg2/entry - 1
    return {"struct": struct, "entry": round(entry,2), "entry_dt": dt[tail_i][:16],
            "r_hold": r_hold, "r_chain": round(total*100, 2), "reason": reason,
            "locked": lock_half is not None}

R = {"up_side": {"hold": [], "chain": []}, "dn_side": {"hold": [], "chain": []}, "detail": []}
for code, sd, ed, ret, days in up_s:
    bars = get_bars(code, M60)
    if not bars: continue
    r = detect_and_sim(bars, sd, "up")
    if r and r["r_hold"] is not None:
        R["up_side"]["hold"].append(r["r_hold"]); R["up_side"]["chain"].append(r["r_chain"]/100)  # 复查修: chain存百分数,st再×100会双重乘,统一小数口径
        R["detail"].append({"side": "up", "code": code, **r})
for code, sd, ed, ret, days in dn_s:
    bars = get_bars(code, M60F)
    if not bars:
        bars = get_bars(code, M60)
    if not bars: continue
    r = detect_and_sim(bars, sd, "dn")
    if r and r["r_hold"] is not None:
        R["dn_side"]["hold"].append(r["r_hold"]); R["dn_side"]["chain"].append(r["r_chain"]/100)
        R["detail"].append({"side": "dn", "code": code, **r})

def st(a):
    a = np.array(a)
    if not len(a): return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean())*100, 2), "win": round(float((a>0).mean())*100, 1),
            "worst": round(float(a.min())*100, 2), "med": round(float(np.median(a))*100, 2)}

R["summary"] = {
    "up_hold": st(R["up_side"]["hold"]), "up_chain": st(R["up_side"]["chain"]),
    "dn_hold": st(R["dn_side"]["hold"]), "dn_chain": st(R["dn_side"]["chain"]),
}
out = os.path.join(ROOT, "data", "swing_m60_v5_exec_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
s = R["summary"]
print("=== v5 执行层闭环(尾盘成交+站稳确认+棘轮0.5/1.0+分档止盈) ===")
print(f"成功侧 hold20: {s['up_hold']}")
print(f"成功侧 v5链  : {s['up_chain']}")
print(f"失败侧 hold20: {s['dn_hold']}")
print(f"失败侧 v5链  : {s['dn_chain']}")
n_locked = sum(1 for d in R["detail"] if d.get("locked"))
print(f"分档止盈触发: {n_locked}/{len(R['detail'])}笔")
print(f"落盘: {out}")
