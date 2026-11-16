# -*- coding: utf-8 -*-
"""revival_xushi_v2_20261028.py — 涨前蓄势·放量突破确认日选股（主人定义v2）

三前提全部满足=蓄势被确认打破=介入日:
  ①长期压缩: 突破前60日箱体总振幅 ≤ 30%（扁平弹簧）
  ②地量蓄势: 箱体尾段均量 ≤ 60日均量×0.75（抛压枯竭）
  ③突破确认: 今日=放量阳线, 收盘突破箱体上沿, 量 ≥ 箱体均量×1.8（≥地量一倍以上口径近似）
安全: R31偏离≤20% / 非ST / ≤30元 / r1>-5% / 剔持仓
止损: 箱体下沿(突破前60日最低价) — 计划note携带
"""
import importlib.util, os, sys, json
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "w23", os.path.join(ROOT, "analysis", "revival_wave23_scanner_20261028.py"))
w23 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w23)

from stock_name_lookup import get_name

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
import sqlite3
con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq order by date desc limit 300")})
dmin = str(dates[0])
rows = con.execute(
    "select code,date,open,high,low,close,volume,amount from stock_daily_meta_qfq "
    "where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, o, h, l, c, v, a in rows:
    s = series.setdefault(code, {"open": [], "close": [], "low": [], "vol": [], "date": []})
    s["date"].append(int(d)); s["open"].append(o); s["low"].append(l)
    s["close"].append(c); s["vol"].append(v or 0)
for code, s in series.items():
    for k in s:
        s[k] = np.array(s[k], dtype=float)
T = dates[-1]
print(f"信号日={T} 载入{len(series)}只", flush=True)

doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh", "sz", "bj"):
        if cc.startswith(pref):
            cc = cc[len(pref):]
    held.add(cc.zfill(6))

cands = []
for code, s in series.items():
    if len(s["close"]) < 90 or code in held:
        continue
    if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        continue
    cl = s["close"]; op = s["open"]; vol = s["vol"]
    Tc = cl[-1]; prev = cl[-2]
    if not (0.5 < Tc <= 30):
        continue
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    # ①突破前60日压缩箱体(剔除今日): 总振幅≤30%
    base_hi = float(np.max(cl[-61:-1])); base_lo = float(np.min(cl[-61:-1]))
    if base_lo <= 0:
        continue
    base_amp = (base_hi - base_lo) / base_lo
    if base_amp > 0.30:
        continue
    # ②地量蓄势: 箱体尾段(前10日)均量 ≤ 箱体期均量×0.75
    base_vol_mean = float(np.mean(vol[-61:-1]))
    tail_vol = float(np.mean(vol[-11:-1]))
    if tail_vol > base_vol_mean * 0.75:
        continue
    # ③突破确认: 今日阳线 + 收盘破箱体上沿 + 放量(≥箱体均量×1.8)
    body_yang = cl[-1] > op[-1]
    brk = Tc > base_hi
    volr_break = vol[-1] / max(base_vol_mean, 1e-9)
    if not (body_yang and brk and vol[-1] >= base_vol_mean * 1.8):
        continue
    # 安全: R31偏离≤20% / r1>-5%
    ma20 = float(np.mean(cl[-20:]))
    dev = Tc / ma20 - 1
    if dev > 0.20:
        continue
    if Tc / prev - 1 < -0.05:
        continue
    stop_ref = base_lo  # 箱体下沿=止损参考
    cands.append((volr_break, code, nm, Tc, dev, stop_ref, base_amp, tail_vol / max(base_vol_mean, 1e-9)))

cands.sort(reverse=True)
print(f"蓄势突破确认: {len(cands)}只")
plan = []
for volr_break, code, nm, Tc, dev, stop_ref, base_amp, dry in cands[:4]:
    price = float(Tc)
    print(f"  {nm:<8} {code} @{price:.2f} 突破量比={volr_break:.1f} 箱体振幅{base_amp*100:.0f}% 偏离{dev*100:+.0f}%")
    plan.append({"code": code, "name": nm, "source": "revival_xushi_brk",
                 "price": round(price, 2), "volume": 100, "sector": "",
                 "prev_close": 0,
                 "note": f"涨前蓄势突破确认(主人定义v2): 60日箱体振幅{base_amp*100:.0f}% 地量蓄势{dry:.2f} 放量突破量比{volr_break:.1f} 止损参考箱体下沿{stop_ref:.2f}",
                 "execute": True})
out = os.path.join(ROOT, "data", "revival_xushi4_plan_20261029.json")
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("产物:", out, f"({len(plan)}只)")
