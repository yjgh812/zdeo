# -*- coding: utf-8 -*-
"""revival_xushi_select_20261028.py — 涨前蓄势修正定义选股（主人令「仔细认真的了解,找对位置,找对介入时机」）

涨前蓄势修正定义 v1（对比官方低位横盘口径的盲区修复）:
  ①低位: pos250 < 30 (250日分位低30%)                    [沿用官方]
  ②止跌筑底: 近20日最低价 > 250日最低价 (近20日未创新低)   [新增——城投式下跌中继剔除关键]
  ③横盘窄幅: 近10日区间振幅 ≤ 10%                         [收紧]
  ④量能萎缩: 近5日均量 < 近60日均量 (浮筹沉淀)            [新增]
  ⑤MA20走平或上翘: ma20_now >= ma20_5日前                 [新增]
  ⑥非当日暴跌: r1 > -5%
排序: 10日振幅升序(窄=弹簧压缩越紧) 取前4。
介入时机两模式(呈主人定夺):
  潜伏式=横盘期间入场(时间成本未知); 突破式=平台放量突破日入场(时机准但有假突破风险)。
"""
import importlib.util, os, sys, json
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "s27", os.path.join(ROOT, "analysis", "revival_scanner_20261027.py"))
s27 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s27)

from stock_name_lookup import get_name

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
import sqlite3
con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq order by date desc limit 300")})
dmin = str(dates[0])  # 300日窗口起点(dates升序)
T = dates[0]
rows = con.execute(
    "select code,date,open,high,low,close,volume,amount from stock_daily_meta_qfq "
    "where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, o, h, l, c, v, a in rows:
    s = series.setdefault(code, {"close": [], "low": [], "high": [], "vol": [], "amt": [], "date": []})
    s["date"].append(int(d)); s["close"].append(c); s["low"].append(l)
    s["high"].append(h); s["vol"].append(v or 0); s["amt"].append(a or 0)
for code, s in series.items():
    for k in s:
        s[k] = np.array(s[k], dtype=float)
T = dates[-1]  # 最新信号日
print(f"信号日={T} 载入{len(series)}只")

# 排除集: 持仓 + ST/科创北交(名称与代码) — 与 s27.base_universe 排除逻辑同源
doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
import re as _re
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh", "sz", "bj"):
        if cc.startswith(pref):
            cc = cc[len(pref):]
    held.add(cc.zfill(6))

cands = []
for code, s in series.items():
    if len(s["close"]) < 250 or code in held:
        continue
    if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        continue
    cl = s["close"]; vol = s["vol"]
    Tc = cl[-1]
    if not (0.5 < Tc <= 30):
        continue
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    mn250, mx250 = float(np.min(cl[-250:])), float(np.max(cl[-250:]))
    if mx250 <= mn250:
        continue
    pos250 = (Tc - mn250) / (mx250 - mn250) * 100
    if pos250 >= 30:
        continue
    # ②止跌筑底: 近20日最低 > 250日最低(未在近20日创250日新低)
    min20 = float(np.min(cl[-20:])); min250 = float(np.min(cl))
    if min20 <= mn250 + 1e-9:
        continue
    # ③横盘窄幅: 近10日区间振幅 ≤ 10%
    r10_hi, r10_lo = float(np.max(cl[-10:])), float(np.min(cl[-10:]))
    if r10_lo <= 0 or (r10_hi - r10_lo) / r10_lo > 0.10:
        continue
    # ④量能萎缩: 近5日均量 < 近60日均量
    if float(np.mean(vol[-5:])) >= float(np.mean(vol[-60:])):
        continue
    # ⑤MA20走平或上翘
    ma20_now = float(np.mean(cl[-20:])); ma20_5a = float(np.mean(cl[-25:-5]))
    if ma20_now < ma20_5a * 0.995:
        continue
    # ⑥非当日暴跌
    if cl[-1] / cl[-2] - 1 < -0.05:
        continue
    tight = (r10_hi - r10_lo) / r10_lo
    cands.append((tight, code, nm, pos250, Tc))

cands.sort()
print(f"通过全部条件: {len(cands)}")
plan = []
for tight, code, nm, pos250, Tc in cands[:4]:
    print(f"  {nm:<8} {code} @{Tc:.2f} 10日振幅{tight*100:.1f}% pos250={pos250:.0f}%")
    plan.append({"code": code, "name": nm, "source": "revival_xushi",
                 "price": round(Tc, 2), "volume": 100, "sector": "",
                 "prev_close": 0, "note": f"涨前蓄势修正定义: 振幅{tight*100:.1f}% pos250={pos250:.0f}%",
                 "execute": True})
out = os.path.join(ROOT, "data", "revival_xushi4_plan_20261029.json")
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("产物:", out, f"({len(plan)}只)")
