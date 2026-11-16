# -*- coding: utf-8 -*-
"""swing_m60_v4_failside_20261029.py — v4: 失败波段样本的防守检验

v3教训: 40只全是成功up段(幸存者样本), 风控链在「事后全涨」环境里必然净损伤(提前离场都是错)。
风控链的真正考场=失败波段: dn段/震荡段起点前后, 60分结构信号同样会触发(接刀场景),
此时 hold20 vs ATR风控链 的差异才是风控的真实价值。
合成: 成功样本(v3) + 失败样本(v4) 按出现频率加权 → 风控链净值判断。
"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
M60 = os.path.join(ROOT, "data", "m60_samples_fail")
os.makedirs(M60, exist_ok=True)
CUT = None  # 按每票dn起点动态

con = sqlite3.connect("file:D:/zcodesj/data/swing_labels.db?mode=ro", uri=True)
# 失败样本: 最近的dn段起点(2025-10后), 每票取最近一段
rows = con.execute('''
  select code, start_date, end_date, ret, days from swing_segments
  where seg_type='dn' and revised=0 and start_date>='20250901'
  order by start_date desc''').fetchall()
seen = {}
for r in rows:
    code = r[0]
    if code[:2] in ("68",) or code[0] in ("4","8","9"):
        continue
    if code not in seen:
        seen[code] = r
fails = list(seen.values())[:40]
codes = [r[0] for r in fails]
ph = ",".join("?"*len(codes))
con.close()
con2 = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
drows = con2.execute(f"select code,date,close from stock_daily_meta_qfq where code in ({ph}) and date>=20260301 order by code,date",
                     codes).fetchall()
con2.close()
daily = defaultdict(list)
for code, d, c in drows:
    daily[code].append((int(d), float(c)))

# 拉60分K(新浪)
URL = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
       "symbol={sym}&scale=60&ma=no&datalen=500")
ok = 0
for code, sd, ed, ret, days in fails:
    out = os.path.join(M60, f"{code}.json")
    if os.path.exists(out):
        ok += 1; continue
    sym = ("sh" if code.startswith(("6","9")) else "sz") + code
    try:
        req = urllib.request.Request(URL.format(sym=sym), headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=15) as rr:
            kl = json.loads(rr.read().decode("utf-8"))
        bars = [{"dt": b["day"], "o": float(b["open"]), "c": float(b["close"]),
                 "h": float(b["high"]), "l": float(b["low"]), "v": float(b["volume"])} for b in kl]
        json.dump({"code": code, "bars": bars}, open(out, "w", encoding="utf-8"))
        ok += 1
    except Exception as e:
        print(f"{code} FAIL {e}")
    time.sleep(0.3)
print(f"失败样本60分K: {ok}/{len(fails)}", flush=True)


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

fail_hold = []
fail_chain = []
fail_details = []
for code, sd, ed, ret, days in fails:
    f = os.path.join(M60, f"{code}.json")
    if not os.path.exists(f): continue
    bars = json.load(open(f, encoding="utf-8"))["bars"]
    dt = [b["dt"] for b in bars]
    sd_i = int(sd)
    cut_pos = next((i for i, d in enumerate(dt) if d[:10].replace("-","") == str(sd) and i >= 90), None)
    if cut_pos is None: continue
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
    b = fwd20(code, sd_i)
    if b is not None:
        fail_hold.append(b)
    lo_b, hi_b = max(cut_pos-20, 100), min(cut_pos+120, len(cl)-85)
    fired = None
    for i in range(lo_b, hi_b):
        if np.isnan(prev_v[i]) or np.isnan(ma20[i]) or np.isnan(ma60[i]) or np.isnan(atr14[i]) or atr14[i] <= 0:
            continue
        lp = low20[i]
        if lp > 0 and cl[i]/lp - 1 > 0.10: continue
        day_off = (i - cut_pos) / 4.0
        if day_off > 8 or cl[i] > cut_close*1.05:
            continue
        in_zone = min(ma20[i], ma60[i])*0.99 <= cl[i] <= max(ma20[i], ma60[i])*1.02
        vol_shrink = vv[i] <= prev_v[i]*0.5
        body_small = 0 < cl[i]-op[i] <= cl[i]*0.008
        lower_sh = (min(op[i],cl[i])-lo[i])/max(cl[i],1e-9) >= 0.004
        vol_break = vv[i] >= 1.5*(v5[i] if not np.isnan(v5[i]) else vv[max(0,i-5):i+1].mean())
        struct = None
        if in_zone and vol_shrink and (lower_sh or body_small):
            struct = "B1"
        elif (not np.isnan(last_swing[i-1])) and cl[i] > last_swing[i-1] and cl[i-1] <= last_swing[i-1] and vol_break:
            struct = "B2"
        touches = sum(1 for kk in range(max(0,i-20), i+1) if lo[kk] <= ma60[kk]*1.01)
        if struct is None and touches >= 2 and lo[i] >= lo[max(0,i-10):i].min()-1e-9 and vv[i] < prev_v[i]*0.6 and cl[i] > op[i]:
            struct = "B3"
        if struct:
            fired = (i, struct); break   # 失败样本取首信号
    if not fired:
        continue
    i, struct = fired
    entry = cl[i]
    r_hold = fwd20(code, int(dt[i][:10].replace("-","")))
    if r_hold is not None:
        fail_hold.append(r_hold)
    atr0 = atr14[i]
    stop = entry - 2*atr0
    exit_r = None; reason = None
    breach_bar = None
    j_end = min(i + 80, len(cl)-1)
    for j in range(i+1, j_end+1):
        gain = cl[j]/entry - 1
        if gain >= 3*atr0/entry:
            stop = max(stop, entry + 1*atr0)
        if lo[j] < lo[j-1] and vv[j] >= 1.2*(v5[j] if not np.isnan(v5[j]) else vv[max(0,j-5):j+1].mean()):
            if breach_bar is None:
                breach_bar = j
            if j - breach_bar >= 3 and cl[j] < lo[j-1]:
                exit_r = cl[j]/entry - 1; reason = "结构止损"; break
        else:
            if breach_bar is not None and cl[j] >= lo[j-1]:
                breach_bar = None
        if j - i >= 15 and gain < 0.01:
            exit_r = cl[j]/entry - 1; reason = "时间止损"; break
        if lo[j] <= stop:
            exit_r = stop/entry - 1; reason = "移动/初始止损"; break
        if j == j_end:
            exit_r = cl[j]/entry - 1; reason = "到期20日"; break
    if exit_r is not None:
        fail_chain.append(exit_r)
        fail_details.append({"code": code, "struct": struct, "ret": round(exit_r*100,2), "reason": reason})

def st(a):
    a = np.array(a)
    if not len(a): return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean())*100, 2), "win": round(float((a>0).mean())*100, 1),
            "worst": round(float(a.min())*100, 2), "med": round(float(np.median(a))*100, 2)}

R = {"built_at": "20261029",
     "fail_side": {"hold20": st(fail_hold), "atr_chain": st(fail_chain),
                    "defense_pp": round(float(np.mean(fail_chain)-np.mean(fail_hold))*100, 2) if fail_chain and fail_hold else None},
     "fail_exit_mix": {},
     "n_fail_trades": len(fail_chain)}
mix = defaultdict(int)
for d in fail_details:
    mix[d["reason"]] += 1
R["fail_exit_mix"] = dict(mix)
R["details"] = fail_details
out = os.path.join(ROOT, "data", "swing_m60_v4_failside_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("=== 失败波段侧(dn起点窗口) ===")
print(f"hold20     : {R['fail_side']['hold20']}")
print(f"ATR风控链  : {R['fail_side']['atr_chain']}")
print(f"防守增量   : {R['fail_side']['defense_pp']}pp")
print(f"离场构成: {R['fail_exit_mix']}")
print(f"落盘: {out}")
