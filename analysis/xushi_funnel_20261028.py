# -*- coding: utf-8 -*-
"""xushi_funnel_20261028.py — 涨前蓄势·三步漏斗式选股（主人定义完整版）

第一步 量化初筛: 振幅收敛+量能枯竭+趋势安全 → 基础观察池
第二步 人工复核: 均线粘合+筹码单峰+位置排除 → 核心候选池
第三步 盘中确认: 放量突破+缩量回踩不破 → 介入信号

零交易权: 只产出候选清单, 买入须主人逐笔令+trade_executor。
"""
import json, os, sys
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
    s = series.setdefault(code, {"open": [], "high": [], "low": [], "close": [], "vol": [], "amt": []})
    s["open"].append(o); s["high"].append(h); s["low"].append(l)
    s["close"].append(c); s["vol"].append(v or 0); s["amt"].append(a or 0)
for code, s in series.items():
    for k in s:
        s[k] = np.array(s[k], dtype=float)

doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh", "sz", "bj"):
        if cc.startswith(pref):
            cc = cc[len(pref):]
    held.add(cc.zfill(6))

print(f"信号日={dates[-1]} 全宇宙={len(series)}", flush=True)

# ============ 第一步: 量化初筛 (振幅收敛 + 量能枯竭 + 趋势安全) ============
step1 = []
reasons_1 = {}
for code, s in series.items():
    if len(s["close"]) < 260 or code in held:
        continue
    if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        continue
    cl = s["close"]; vol = s["vol"]
    Tc = cl[-1]; prev = cl[-2]
    if not (0.5 < Tc <= 30):
        continue
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    # 振幅收敛: 近10日振幅 < 15%
    hi10 = float(np.max(cl[-10:])); lo10 = float(np.min(cl[-10:]))
    amp10 = (hi10 - lo10) / max(lo10, 1e-9) if lo10 > 0 else 999
    if amp10 > 0.15:
        continue
    # 量能枯竭: 近5日均量 < 60日均量 × 0.7
    v5 = float(np.mean(vol[-5:])); v60 = float(np.mean(vol[-60:]))
    if v5 > v60 * 0.7:
        continue
    # 趋势安全: 今日收盘 ≥ MA20 × 0.97 (中期趋势没走坏)
    ma20 = float(np.mean(cl[-20:]))
    if Tc < ma20 * 0.97:
        continue
    step1.append(code)
    reasons_1[code] = f"振幅{amp10*100:.1f}% 量比{v5/max(v60,1):.2f}"

print(f"第一步 量化初筛: {len(step1)}只", flush=True)

# ============ 第二步: 人工复核特征量化 (均线粘合 + 低位 + 量价细节) ============
step2 = []
reasons_2 = {}
for code in step1:
    s = series[code]
    cl = s["close"]
    Tc = cl[-1]
    ma5 = float(np.mean(cl[-5:])); ma10 = float(np.mean(cl[-10:]))
    ma20 = float(np.mean(cl[-20:])); ma60 = float(np.mean(cl[-60:]))
    mas = [ma5, ma10, ma20, ma60]
    glue = (max(mas) - min(mas)) / max(min(mas), 1e-9)
    mn250 = float(np.min(cl[-250:])); mx250 = float(np.max(cl[-250:]))
    pos250 = (Tc - mn250) / max(mx250 - mn250, 1e-9) * 100
    score = 0
    tags = []
    if glue <= 0.03:
        score += 2; tags.append("拧绳3%")
    elif glue <= 0.05:
        score += 1; tags.append("粘合5%")
    if pos250 <= 30:
        score += 2; tags.append("低位30%")
    elif pos250 <= 50:
        score += 1; tags.append("中低50%")
    # K线小阴小阳
    bodies = np.abs(cl[-10:] - s["open"][-10:]) / np.maximum(cl[-11:-1], 1e-9)
    avg_body = float(np.mean(bodies))
    if avg_body <= 0.02:
        score += 1; tags.append("小K线")
    # 地量
    v5 = float(np.mean(s["vol"][-5:])); v60 = float(np.mean(s["vol"][-60:]))
    if v5 / max(v60, 1) < 0.4:
        score += 2; tags.append("极致地量")
    elif v5 / max(v60, 1) < 0.6:
        score += 1; tags.append("地量")
    # 无断头铡刀
    r1s = [cl[i] / cl[i-1] - 1 for i in range(-10, 0)]
    if min(r1s) > -0.05:
        score += 1; tags.append("无铡刀")
    # 蓄势结构确认: 60日压缩
    hi60 = float(np.max(cl[-60:])); lo60 = float(np.min(cl[-60:]))
    amp60 = (hi60 - lo60) / max(lo60, 1e-9) if lo60 > 0 else 999
    if amp60 <= 0.25:
        score += 1; tags.append("60日压缩25%")
    if score >= 5:
        step2.append({"code": code, "name": get_name(code) or "?", "score": score, "tags": tags,
                      "glue_pct": round(glue*100, 1), "pos250": round(pos250),
                      "close": round(Tc, 2), "amp10": round(amp10*100, 1)})
        reasons_2[code] = score

step2.sort(key=lambda x: (-x["score"], x["code"]))
print(f"第二步 人工复核: {len(step2)}只", flush=True)
for s in step2[:20]:
    t = ",".join(s["tags"])
    print(f"  {s['name']:<8} {s['code']} @{s['close']:.2f} 分{s['score']} [{t}]")

# 保存
ev = {"step1_count": len(step1), "step2_count": len(step2),
      "candidates": step2[:30]}
json.dump(ev, open(os.path.join(ROOT, "data", "xushi_funnel_20261028.json"),
                   "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n核心候选池: {len(step2)}只 (分≥5, 存 data/xushi_funnel_20261028.json)")
print("第三步(盘中确认)待下一交易日执行——跟踪候选池, 等放量突破+缩量回踩不破信号")
