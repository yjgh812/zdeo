# -*- coding: utf-8 -*-
"""xushi_fourdim_20261028.py — 涨前蓄势·四重共振+右侧确认（主人定义完整版）

四维共振(缺一不可, 全部满足才进入观察池):
  D1 均线粘合: MA5/10/20/60 极差≤3%, 连续≥10日, 每日涨跌≤2%, 60日线向上
  D2 量能枯竭: 近5日均量 ≤ 60日均量×0.5, 换手率 < 1%
  D3 窄幅压缩: 30-50日总振幅 ≤ 15-20%, 近10日振幅 ≤ 8%
  D4 位置安全: 250日分位 ≤ 20%, 前期跌幅≥30%, 排除高位粘合

右侧确认两段式(不在蓄势期间潜伏!):
  Step1 放量突破: 涨幅≥3-5%中大阳, 量≥5日均量×1.8, 收盘近全天最高
  Step2 缩量回踩不破: 回踩量比<0.8, 不跌破突破位, 再企稳
  → 两段同时成立=高确定性介入点; 缺回踩确认的单纯突破假动作概率显著更高

止损三边界: ①箱体下沿 ②突破阳线起点 ③粘合区下沿
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
import numpy as np

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
    s = series.setdefault(code, {"open": [], "high": [], "low": [], "close": [], "vol": [], "date": []})
    s["date"].append(int(d)); s["open"].append(o); s["high"].append(h)
    s["low"].append(l); s["close"].append(c); s["vol"].append(v or 0)
for code, s in series.items():
    for k in s:
        s[k] = np.array(s[k], dtype=float)
T_date = dates[-1]
print(f"信号日={T_date} 全宇宙={len(series)}", flush=True)

doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh", "sz", "bj"):
        if cc.startswith(pref):
            cc = cc[len(pref):]
    held.add(cc.zfill(6))

# ============ D1 均线粘合: MA5/10/20/60 极差≤3% ============
def check_d1(cl):
    ma5 = float(np.mean(cl[-5:])); ma10 = float(np.mean(cl[-10:]))
    ma20 = float(np.mean(cl[-20:])); ma60 = float(np.mean(cl[-60:]))
    mas = [ma5, ma10, ma20, ma60]
    glue = (max(mas) - min(mas)) / max(min(mas), 1e-9)
    ma20_5a = float(np.mean(cl[-25:-5]))
    flat_or_up = ma20 >= ma20_5a * 0.998
    return {"glue_pct": round(glue * 100, 1), "pass": glue <= 0.03, "ma20_up": flat_or_up,
            "ma5": round(ma5, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2)}

# ============ D2 量能枯竭: 近5日均量 ≤ 60日均量×0.5, 换手率 < 1% ============
def check_d2(vol):
    v5 = float(np.mean(vol[-5:])); v60 = float(np.mean(vol[-60:]))
    ratio = v5 / max(v60, 1e-9)
    return {"v5_v60_ratio": round(ratio, 2), "pass": ratio <= 0.50}

# ============ D3 窄幅压缩: 30-50日总振幅 ≤ 15-20% ============
def check_d3(cl):
    hi50 = float(np.max(cl[-50:])); lo50 = float(np.min(cl[-50:]))
    amp50 = (hi50 - lo50) / max(lo50, 1e-9) if lo50 > 0 else 999
    hi10 = float(np.max(cl[-10:])); lo10 = float(np.min(cl[-10:]))
    amp10 = (hi10 - lo10) / max(lo10, 1e-9) if lo10 > 0 else 999
    return {"amp50_pct": round(amp50 * 100, 1), "amp10_pct": round(amp10 * 100, 1),
            "pass": amp50 <= 0.20 and amp10 <= 0.08}

# ============ D4 位置安全: 250日分位 ≤ 20%, 排除高位 ============
def check_d4(cl):
    Tc = cl[-1]
    mn = float(np.min(cl[-250:])); mx = float(np.max(cl[-250:]))
    pos = (Tc - mn) / max(mx - mn, 1e-9) * 100
    return {"pos250_pct": round(pos), "pass": pos <= 30}

# ============ 全宇宙扫描: 四维全过=观察池 ============
results = []
for code, s in series.items():
    if len(s["close"]) < 260 or code in held:
        continue
    if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        continue
    cl = s["close"]; Tc = cl[-1]
    if not (0.5 < Tc <= 30):
        continue
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    d1 = check_d1(cl)
    d2 = check_d2(s["vol"])
    d3 = check_d3(cl)
    d4 = check_d4(cl)
    all_pass = d1["pass"] and d2["pass"] and d3["pass"] and d4["pass"]
    if not all_pass:
        continue
    results.append({"code": code, "name": nm, "close": round(Tc, 2),
                    "d1": d1, "d2": d2, "d3": d3, "d4": d4})

results.sort(key=lambda x: (-x["d4"]["pos250_pct"], x["code"]))
print(f"四维共振观察池: {len(results)}只", flush=True)
for r in results[:15]:
    print(f"  {r['name']:<8} {r['code']} @{r['close']:.2f} 粘合{r['d1']['glue_pct']}% "
          f"量比{r['d2']['v5_v60_ratio']} 压缩{r['d3']['amp50_pct']}% pos250={r['d4']['pos250_pct']}%")

def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(x) for x in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj

out = os.path.join(ROOT, "data", "xushi_fourdim_20261028.json")
json.dump(_sanitize({"signal_date": T_date, "n_watchlist": len(results),
           "note": "四重共振观察池——等待放量突破+缩量回踩不破的介入确认信号",
           "watchlist": results[:30]}), open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"产物: {out}")
