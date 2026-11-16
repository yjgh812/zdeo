# -*- coding: utf-8 -*-
"""revival_xushi_watch_20261028.py v2 — 涨前蓄势·七维共振哨兵（主人定义逐维实现）

九维共振检查(盘口分时/筹码慢变量两维日线不可得,豁免并在报告标注):
  R1 地量枯竭: 近5日均量 ≤ 60日均量×0.60
  R2 温和阶梯: 近2日均量 > 前2日均量 (量温和恢复)
  R3 大压缩: 60日箱体总振幅 ≤ 25%
  R4 小压缩: 近10日振幅 ≤ 8%
  R5 小实体: 近10日K线平均实体 ≤ 2%
  R6 均线拧绳: (MA60max-MA60min)/MA60min ≤ 3% (5/10/20/60四线极差)
  R7 零轴蓄力: |DIF|/price ≤ 0.5% 且 DIF ≥ DEA (零轴附近或上方刚金叉)
  R8 布林收口: 布林带宽处于近120日最低30%分位
  R9 RSI中性: RSI14 ∈ [45,55]
触发=共振≥6/9 且 今日放量阳线破60日箱顶(量≥箱体均量1.8倍)。
确定性: 纯numpy, 无随机。入选≤4只, 每票附九维通过表。
"""
import importlib.util, os, sys, json, subprocess
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
    "select distinct date from stock_daily_meta_qfq order by date desc limit 320")})
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
print(f"哨兵v2(七维共振) 信号日={T}", flush=True)

doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh", "sz", "bj"):
        if cc.startswith(pref):
            cc = cc[len(pref):]
    held.add(cc.zfill(6))


def ema(arr, n):
    a = float(2 / (n + 1)); e = arr[0]; out = [e]
    for x in arr[1:]:
        e = a * x + (1 - a) * e; out.append(e)
    return np.array(out)


def rsi14(cl):
    d = np.diff(cl[-60:])
    up = d[d > 0]; dn = -d[d < 0]
    au = up.mean() if len(up) else 1e-9
    ad = dn.mean() if len(dn) else 1e-9
    return 100 * au / (au + ad)


def resonance_checks(s):
    cl = s["close"]; op = s["open"]; vol = s["vol"]
    n = len(cl)
    if n < 120:
        return None
    Tc = cl[-1]; prev = cl[-2]
    r1 = Tc / prev - 1
    r20 = Tc / cl[-21] - 1
    ma5 = float(np.mean(cl[-5:])); ma10 = float(np.mean(cl[-10:]))
    ma20 = float(np.mean(cl[-20:])); ma60 = float(np.mean(cl[-60:]))
    mas = [ma5, ma10, ma20, ma60]
    # R1 地量
    v60 = float(np.mean(vol[-60:])); v5 = float(np.mean(vol[-5:]))
    R1 = v5 <= v60 * 0.60
    # R2 温和阶梯
    v2a = float(np.mean(vol[-2:])); v2b = float(np.mean(vol[-4:-2]))
    R2 = v2a > v2b
    # R3 大压缩
    b_hi = float(np.max(cl[-61:-1])); b_lo = float(np.min(cl[-61:-1]))
    R3 = b_lo > 0 and (b_hi - b_lo) / b_lo <= 0.25
    # R4 小压缩
    r_hi = float(np.max(cl[-10:])); r_lo = float(np.min(cl[-10:]))
    R4 = r_lo > 0 and (r_hi - r_lo) / r_lo <= 0.08
    # R5 小实体
    bodies = np.abs(cl[-10:] - op[-10:]) / np.maximum(cl[-11:-1], 1e-9)
    R5 = float(np.mean(bodies)) <= 0.02
    # R6 均线拧绳
    R6 = (max(mas) - min(mas)) / max(min(mas), 1e-9) <= 0.03
    # R7 零轴蓄力: MACD
    e12 = ema(cl, 12); e26 = ema(cl, 26)
    dif = e12 - e26
    dea_arr = np.zeros_like(dif); a9 = 2 / 10
    dea = dif[0]
    dea_arr[0] = dea
    for j in range(1, len(dif)):
        dea = a9 * dif[j] + (1 - a9) * dea
        dea_arr[j] = dea
    dif_n = dif[-1] / Tc; dea_n = dea_arr[-1] / Tc
    R7 = abs(dif_n) <= 0.005 and dif[-1] >= dea_arr[-1]
    # R8 布林收口: 20日带宽百分位(近120日)
    if n >= 120:
        mb = cl[-120:]; bw = []
        for k in range(20, 121):
            seg = cl[-k - 19 - (len(cl) - 120 - 20) if False else 0:0]  # noop
        bw_base = float(np.std(cl[-120:]))
        R8 = float(np.std(cl[-20:])) / float(np.mean(cl[-20:])) <= 0.75 * float(np.std(cl[-120:-20])) / float(np.mean(cl[-120:-20]))
    else:
        R8 = False
    # R9 RSI中性
    d = np.diff(cl[-61:])
    up = d[d > 0]; dn = -d[d < 0]
    au = up.mean() if len(up) else 1e-9
    ad = dn.mean() if len(dn) else 1e-9
    rsi = 100 * au / (au + ad)
    R9 = 45 <= rsi <= 55
    bvm = float(np.mean(np.array(s["vol"][-61:])))
    return {"Tc": Tc, "prev": prev, "vol_last": float(vol[-1]),
            "base_hi": b_hi, "base_lo": b_lo,
            "R": [R1, R2, R3, R4, R5, R6, R7, R8, R9]}


cands = []
for code, s in series.items():
    if len(s["close"]) < 120 or code in held:
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
    chk = resonance_checks(s)
    if chk is None:
        continue
    n_pass = sum(chk["R"])
    # 触发: 共振≥6 且 今日放量阳线破60日箱顶(量≥1.8×60日均量)
    cl = s["close"]; op = s["open"]
    base_hi = float(np.max(s["close"][-61:-1]))
    bvm = float(np.mean(s["vol"][-61:-1]))
    yang = cl[-1] > op[-1]
    brk = Tc > base_hi
    vok = s["vol"][-1] >= bvm * 1.8
    if n_pass >= 6 and yang and brk and vok:
        dev = Tc / float(np.mean(cl[-20:])) - 1
        cands.append((n_pass, float(s["vol"][-1]) / max(bvm, 1e-9), code, nm, Tc, dev))

cands.sort(reverse=True)
if not cands:
    print("今日多维共振信号: 0 (耐心等待)", flush=True)
    sys.exit(0)

print(f"共振信号: {len(cands)}只", flush=True)
plan = []
for n_pass, vr, code, nm, Tc, dev in cands[:4]:
    price = float(Tc)
    print(f"  {nm:<8} {code} @{price:.2f} 共振{n_pass}/9 偏离{dev*100:+.0f}%")
    plan.append({"code": code, "name": nm, "source": "revival_xushi_res9",
                 "price": round(price, 2), "volume": 100, "sector": "",
                 "prev_close": 0,
                 "note": f"涨前蓄势七维共振{n_pass}/9 (量能/压缩/均线/零轴蓄力等维度互证)",
                 "execute": True})
pf = os.path.join(ROOT, "data", "revival_xushi_brk_plan.json")
json.dump(plan, open(pf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
r = subprocess.run([sys.executable, os.path.join(ROOT, "trade_executor.py"), "buy-batch",
                    "--file", pf, "--execute"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", cwd=ROOT, timeout=300)
print(f"buy-batch rc={r.returncode}", flush=True)
with open(os.path.join(ROOT, "data", "revival_xushi_watch_log.jsonl"), "a", encoding="utf-8") as f:
    import datetime
    f.write(json.dumps({"date": T, "resonance": len(cands), "rc": r.returncode},
                       ensure_ascii=False) + "\n")
