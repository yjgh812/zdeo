# -*- coding: utf-8 -*-
"""revival_fulltest_20261028.py — 涨前蓄势全量测试·修复优化迭代(主人令20261028)

全宇宙5690只逐票检查七维共振, 输出全部匹配票; 修复过程中发现的bug一并入库。
"""
import importlib.util, os, sys, json
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
import sqlite3
con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq order by date desc limit 320")})
dmin = str(dates[0])
rows = con.execute(
    "select code,date,open,high,low,close,volume,amount from stock_daily_meta_qfq "
    "where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, o, h, l, c, v, a in rows:
    s = series.setdefault(code, {"date": [], "open": [], "close": [], "low": [], "vol": []})
    s["date"].append(int(d)); s["close"].append(c); s["low"].append(l); s["vol"].append(v or 0)
for code, s in series.items():
    for k in s: s[k] = np.array(s[k], dtype=float)

doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh","sz","bj"):
        if cc.startswith(pref): cc = cc[len(pref):]
    held.add(cc.zfill(6))

def ema(a, n):
    k = 2.0/(n+1); e = a[0]; out = [e]
    for x in a[1:]:
        e = k*x + (1-k)*e; out.append(e)
    return np.array(out)

results = []
funnel = {}
for code, s in series.items():
    funnel["全量"] = funnel.get("全量", 0) + 1
    cl = s["close"]; vol = s["vol"]
    if len(cl) < 260: funnel["<260行"] = funnel.get("<260行",0)+1; continue
    funnel["行✓"] = funnel.get("行✓",0)+1
    if code[:3] in ("688","689") or code[0] in ("4","8","9"): funnel["板别✗"] = funnel.get("板别✗",0)+1; continue
    funnel["板别✓"] = funnel.get("板别✓",0)+1
    Tc = cl[-1]
    if not (0.5 < Tc <= 30): funnel["价✗"] = funnel.get("价✗",0)+1; continue
    funnel["价✓"] = funnel.get("价✓",0)+1
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm: funnel["ST✗"] = funnel.get("ST✗",0)+1; continue
    funnel["ST✓"] = funnel.get("ST✓",0)+1
    if code in held: funnel["持仓✗"] = funnel.get("持仓✗",0)+1; continue
    funnel["非持仓✓"] = funnel.get("非持仓✓",0)+1

    # 七维共振
    v5 = float(np.mean(vol[-5:])); v60 = float(np.mean(vol[-60:]))
    v1_dry = v5 / max(v60, 1e-9) <= 0.5
    v2_recover = float(np.mean(vol[-3:])) > float(np.mean(vol[-6:-3]))
    hi60 = float(np.max(cl[-60:])); lo60 = float(np.min(cl[-60:]))
    p1_comp = lo60 > 0 and (hi60-lo60)/max(lo60,1e-9) <= 0.25
    hi10 = float(np.max(cl[-10:])); lo10 = float(np.min(cl[-10:]))
    p2_narrow = lo10 > 0 and (hi10-lo10)/max(lo10,1e-9) <= 0.08
    min20 = float(np.min(cl[-20:])); min250 = float(np.min(cl))
    p3_stop = min20 > min250 * 1.005
    mn250 = float(np.min(cl[-250:])); mx250 = float(np.max(cl[-250:]))
    pos250 = (Tc - mn250) / max(mx250 - mn250, 1e-9) * 100 if mx250 > mn250 else 50
    p4_pos = 10 <= pos250 <= 50
    ma5 = float(np.mean(cl[-5:])); ma10 = float(np.mean(cl[-10:]))
    ma20 = float(np.mean(cl[-20:])); ma60 = float(np.mean(cl[-60:]))
    mas = [ma5, ma10, ma20, ma60]
    m1_glue = (max(mas)-min(mas))/max(min(mas),1e-9) <= 0.03
    ma20_5a = float(np.mean(cl[-25:-5]))
    m2_up = ma20 >= ma20_5a * 0.998
    e12 = ema(cl, 12); e26 = ema(cl, 26)
    dif = e12 - e26
    a9 = 2/10; dea = dif[0]
    for j in range(1, len(dif)):
        dea = a9*dif[j] + (1-a9)*dea
    dif_n = dif[-1] / max(Tc, 1e-9)
    t1_zero = abs(dif_n) <= 0.005
    d_p = np.diff(cl[-61:])
    up = d_p[d_p>0]; dn = -d_p[d_p<0]
    au = up.mean() if len(up) else 1e-9
    ad = dn.mean() if len(dn) else 1e-9
    rsi = 100*au/(au+ad)
    t2_rsi = 45 <= rsi <= 55
    std20 = float(np.std(cl[-20:])) / max(float(np.mean(cl[-20:])), 1e-9)
    std_ref = float(np.std(cl[-120:-20])) / max(float(np.mean(cl[-120:-20])), 1e-9)
    t3_squeeze = std20 <= std_ref * 0.7
    r1d = Tc / cl[-2] - 1
    s1_ok = r1d > -0.07

    dims = {"地量": v1_dry, "量恢复": v2_recover, "压缩60": p1_comp, "窄幅10": p2_narrow,
            "止跌": p3_stop, "位置": p4_pos, "拧绳": m1_glue, "微翘": m2_up,
            "零轴": t1_zero, "RSI中": t2_rsi, "收口": t3_squeeze, "无铡": s1_ok}
    n_pass = sum(1 for v in dims.values() if v)
    funnel["共振≥6"] = funnel.get("共振≥6",0)+1 if n_pass >= 6 else funnel.get("共振≥6",0)
    if n_pass < 6:
        continue
    funnel["入选✓"] = funnel.get("入选✓",0)+1

    prev_close = float(cl[-2])
    r20_pct = (Tc / cl[-21] - 1) * 100
    results.append({"code": code, "name": nm, "price": round(Tc, 2),
                    "r5": round((Tc/cl[-6]-1)*100, 1), "r20": round(r20_pct, 1),
                    "pos250": round(pos250), "n_pass": n_pass,
                    "dims": {k: v for k, v in dims.items() if v},
                    "prev_close": round(prev_close, 2)})

results.sort(key=lambda x: (-x["n_pass"], x["code"]))
out = os.path.join(ROOT, "data", "xushi_fulltest_20261028.json")
json.dump({"signal_date": "20261028", "n_results": len(results), "results": json.loads(json.dumps(results, default=str))},
          open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

from collections import Counter
sc = Counter(r["n_pass"] for r in results)
print("共振分布:", dict(sorted(sc.items(), reverse=True)))
print(f"入选: {len(results)}只")
print("\n=== 前20名 ===")
for r in results[:20]:
    d = " ".join(k for k, v in r["dims"].items() if v)
    print(f"  {r['name']:<8} {r['code']} @{r['price']:.2f} 共振{r['n_pass']}/12 [{d}]")
print(f"\n落盘: {out}")
