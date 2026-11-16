# -*- coding: utf-8 -*-
"""revival_xushi_resonance_20261028.py — 涨前蓄势15维共振全宇宙排名"""
import importlib.util, os, sys, json
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name

import sqlite3
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
dates = sorted({int(r[0]) for r in con.execute("select distinct date from stock_daily_meta_qfq order by date desc limit 300")})
dmin = str(dates[0])
rows = con.execute("select code,date,open,high,low,close,volume,amount from stock_daily_meta_qfq where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, o, h, l, c, v, a in rows:
    s = series.setdefault(code, {"open": [], "high": [], "low": [], "close": [], "vol": [], "amt": []})
    s["open"].append(o); s["high"].append(h); s["low"].append(l)
    s["close"].append(c); s["vol"].append(v or 0); s["amt"].append(a or 0)
for code, s in series.items():
    for k in s: s[k] = np.array(s[k], dtype=float)
T = dates[-1]
print(f"信号日={T} 载入={len(series)}")

doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh","sz","bj"):
        if cc.startswith(pref): cc = cc[len(pref):]
    held.add(cc.zfill(6))

results = []
for code, s in series.items():
    if len(s["close"]) < 260 or code in held:
        continue
    if code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    cl = s["close"]; vol = s["vol"]; op = s["open"]
    Tc = cl[-1]; prev = cl[-2]
    if not (0.5 < Tc <= 30):
        continue
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    r5 = Tc / cl[-6] - 1
    r20 = Tc / cl[-21] - 1
    hi10 = float(np.max(cl[-10:])); lo10 = float(np.min(cl[-10:]))
    amp10 = (hi10 - lo10) / max(lo10, 1e-9) if lo10 > 0 else 999
    tags = {}; score = 0
    v5 = float(np.mean(vol[-5:])); v60 = float(np.mean(vol[-60:]))
    t1 = v5 / max(v60, 1) < 0.6
    if t1: tags["V1地量"] = 1; score += 1
    v3a = float(np.mean(vol[-3:])); v3b = float(np.mean(vol[-6:-3]))
    t2 = v3a > v3b
    if t2: tags["V2量恢复"] = 1; score += 1
    hi60 = float(np.max(cl[-60:])); lo60 = float(np.min(cl[-60:]))
    amp60 = (hi60 - lo60) / max(lo60, 1e-9) if lo60 > 0 else 999
    t3 = amp60 <= 0.25
    if t3: tags["P1压缩60"] = 1; score += 1
    hi10 = float(np.max(cl[-10:])); lo10 = float(np.min(cl[-10:]))
    amp10 = (hi10 - lo10) / max(lo10, 1e-9) if lo10 > 0 else 999
    t4 = amp10 <= 0.08
    if t4: tags["P2窄幅10"] = 1; score += 1
    min20 = float(np.min(cl[-20:])); min250 = float(np.min(cl))
    t5 = min20 > min250 * 1.005
    if t5: tags["P3止跌"] = 1; score += 1
    mn250 = float(np.min(cl[-250:])); mx250 = float(np.max(cl[-250:]))
    pos250 = (Tc - mn250) / max(mx250 - mn250, 1e-9) * 100
    t6 = 10 <= pos250 <= 50
    if t6: tags["P4位置"] = 1; score += 1
    ma5 = float(np.mean(cl[-5:])); ma10 = float(np.mean(cl[-10:]))
    ma20 = float(np.mean(cl[-20:])); ma60 = float(np.mean(cl[-60:]))
    mas = [ma5, ma10, ma20, ma60]
    glue = (max(mas) - min(mas)) / max(min(mas), 1e-9)
    t7 = glue <= 0.03
    if t7: tags["M1拧绳"] = 1; score += 1
    ma20_5a = float(np.mean(cl[-25:-5]))
    t8 = ma20 >= ma20_5a * 0.998
    if t8: tags["M2微翘"] = 1; score += 1
    def _ema(arr, n):
        k = 2.0/(n+1); e = arr[0]; out = [e]
        for x in arr[1:]:
            e = k*x + (1-k)*e; out.append(e)
        return np.array(out)
    e12 = _ema(cl, 12); e26 = _ema(cl, 26)
    dif_n = (e12[-1] - e26[-1]) / max(Tc, 1e-9)
    t9 = abs(dif_n) <= 0.005
    if t9: tags["T1零轴"] = 1; score += 1
    d = np.diff(cl[-61:])
    up = d[d>0]; dn = -d[d<0]
    au = up.mean() if len(up) else 1e-9
    ad = dn.mean() if len(dn) else 1e-9
    rsi = 100*au/(au+ad)
    t10 = 45 <= rsi <= 55
    if t10: tags["T2RSI"] = 1; score += 1
    std20 = float(np.std(cl[-20:])) / max(float(np.mean(cl[-20:])), 1e-9)
    std_ref = float(np.std(cl[-120:-20])) / max(float(np.mean(cl[-120:-20])), 1e-9)
    t11 = std20 <= std_ref * 0.7
    if t11: tags["T3收口"] = 1; score += 1
    r1d = Tc / prev - 1
    t12 = r1d > -0.07
    if t12: tags["S1无铡"] = 1; score += 1
    yang_cnt = sum(1 for i in range(-5, 0) if cl[i] >= op[i])
    t13 = yang_cnt >= 3
    if t13: tags["S2多方优"] = 1; score += 1
    # MVP融合: bp_ep+ep价值倾向加分
    bonus = 0
    if t6 and t7: bonus += 1
    if t3 and t5: bonus += 1
    eff_score = score + bonus
    results.append({"code": code, "name": nm, "close": round(Tc, 2), "pos250": round(pos250),
                    "score": eff_score, "raw_score": score, "bonus": bonus, "tags": tags,
                    "r5": round(r5*100, 1), "r20": round(r20*100, 1), "amp10": round(amp10*100, 1)})

results.sort(key=lambda x: (-x["score"], x["code"]))
print(f"\n=== 涨前蓄势15维共振排名(≥8分) ===")
shown = 0
for r in results:
    if r["score"] >= 8:
        shown += 1
        t = ",".join(k for k, v in r["tags"].items() if v)
        print(f"  {r['name']:<8} {r['code']} @{r['close']:.2f} 分{r['score']}/15 pos250={r['pos250']}% [{t}]")
        if shown >= 15: break
top10 = [r for r in results if r["score"] >= 10]
print(f"\n≥10分高共振: {len(top10)}只")
for r in top10[:8]:
    t = ",".join(k for k, v in r["tags"].items() if v)
    print(f"  ★ {r['name']:<8} {r['code']} @{r['close']:.2f} 分{r['score']}/15 pos250={r['pos250']}% [{t[:80]}]")
# 保存
out = os.path.join(ROOT, "data", "revival_xushi_resonance_20261028.json")
json.dump({"signal_date": T, "n_scanned": len(series), "n_results": len(results),
           "top15": [r for r in results if r["score"] >= 8][:20]}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("产物:", out)
