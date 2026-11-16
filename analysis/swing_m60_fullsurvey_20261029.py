# -*- coding: utf-8 -*-
"""swing_m60_fullsurvey_20261029.py — 全宇宙60分K买卖点统计管道(主人令·5000+全量)

用法:
  python swing_m60_fullsurvey_20261029.py pull     # 阶段1: 批量拉60分K(断点续传, 限速)
  python swing_m60_fullsurvey_20261029.py analyze  # 阶段2: 全历史三结构扫描+前向收益+条件矩阵
"""
import os, sys, json, sqlite3, time, urllib.request
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
M60DIR = os.path.join(ROOT, "data", "m60_full")
os.makedirs(M60DIR, exist_ok=True)
URL = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
       "symbol={sym}&scale=60&ma=no&datalen=500")
MODE = sys.argv[1] if len(sys.argv) > 1 else "pull"

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
codes = [r[0] for r in con.execute("select distinct code from stock_daily_meta_qfq order by code")]
con.close()
uni = [c for c in codes if c[:3] not in ("688", "689") and c[0] not in ("4", "8", "9")]
print(f"全宇宙: {len(uni)}只", flush=True)

if MODE == "pull":
    ok = skip = fail = 0
    t0 = time.time()
    for i, code in enumerate(uni, 1):
        out = os.path.join(M60DIR, f"{code}.json")
        if os.path.exists(out):
            skip += 1
            continue
        sym = ("sh" if code.startswith(("6", "9")) else "sz") + code
        got = False
        for attempt in range(2):
            try:
                req = urllib.request.Request(URL.format(sym=sym),
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    kl = json.loads(r.read().decode("utf-8"))
                if not kl:
                    raise ValueError("empty")
                bars = [{"dt": b["day"], "o": float(b["open"]), "c": float(b["close"]),
                         "h": float(b["high"]), "l": float(b["low"]), "v": float(b["volume"])} for b in kl]
                json.dump({"code": code, "bars": bars}, open(out, "w", encoding="utf-8"))
                got = True
                break
            except Exception:
                time.sleep(1.5)
        if got:
            ok += 1
        else:
            fail += 1
        if i % 200 == 0:
            el = time.time()-t0
            eta = el/i*(len(uni)-i)
            print(f"...{i}/{len(uni)} ok={ok} skip={skip} fail={fail} 已用{el/60:.0f}分 预计剩{eta/60:.0f}分", flush=True)
        time.sleep(0.25)
    print(f"拉取完成: OK={ok} SKIP={skip} FAIL={fail} 用时{(time.time()-t0)/60:.0f}分", flush=True)
    sys.exit(0)

# ══════ analyze ══════
from stock_name_lookup import get_name
dmin = None
sys.path.insert(0, ROOT)

t0 = time.time()
con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates_all = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=20250101 order by date")})
didx = {d: i for i, d in enumerate(dates_all)}
# 宇宙等权日收益(基准)
rows = con.execute(
    "select code,date,close from stock_daily_meta_qfq where date>=? order by code,date", (dates_all[0],)).fetchall()
codes_all = sorted({r[0] for r in rows})
ph = ",".join("?"*len(codes_all))
# 名称(ST过滤)
names = {}
try:
    for code in codes_all:
        names[code] = ""
except Exception:
    pass
# 批量取日线收盘(基准+方向闸用): 按月分段取太大, 改为逐票流式处理时不预载, 基准用增量累加
eq = np.zeros(len(dates_all)); cnt = np.zeros(len(dates_all))
px_cache = {}
for r in rows:
    code, d, c = r[0], int(r[1]), float(r[2])
    gi = didx[d]
    eq[gi] += c; cnt[gi] += 1
    px_cache.setdefault(code, {})[d] = c
for i in range(1, len(eq)):
    if cnt[i] == 0:
        eq[i] = eq[i-1]; cnt[i] = cnt[i-1]
eq = eq/np.maximum(cnt, 1)
# 日线基准fwd20: 全市场平均(逐日)
base_f20 = {}
for i in range(len(dates_all)-20):
    base_f20[dates_all[i]] = eq[i+20]/eq[i]-1
print(f"基准构建完成 ({time.time()-t0:.0f}s)", flush=True)

# wf标签
sw = sqlite3.connect("file:D:/zcodesj/data/swing_labels.db?mode=ro", uri=True)
wf = {int(r[0]): r[1] for r in sw.execute("select date,label from mkt_state_wf")}
sw.close()
LAB = {"上涨趋势": 0, "下跌趋势": 1, "宽幅震荡": 2, "窄幅震荡": 3}

def sma(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a, 0, 0.0)); out[w-1:] = (cs[w:]-cs[:-w])/w
    return out

REC = []   # 每信号一行
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
    # 复权断点卫生
    bad = False
    for i in range(1, len(cl)):
        if cl[i-1] > 0 and abs(cl[i]/cl[i-1]-1) > 0.35:
            bad = True; break
    if bad:
        continue
    n = len(cl)
    ma20, ma60 = sma(cl, 20), sma(cl, 60)
    v5, v10 = sma(vv, 5), sma(vv, 10)
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
    hi60 = np.array([hi[max(0, i-60):i+1].max() if i >= 0 else np.nan for i in range(n)])
    dn_days = np.zeros(n, dtype=bool)
    for i in range(80, n):
        d_day = dt[i][:10].replace("-", "")
        dn_days[i] = d_day in px_cache.get(code, {})
    px = px_cache.get(code, {})
    # 日线方向闸输入: 最近日线MA20(用60分聚合近似: MA80根60分≈MA20日)
    ma20d = sma(cl, 80)
    for i in range(120, n-45):
        d_day = int(dt[i][:10].replace("-", ""))
        lb = wf.get(d_day)
        if lb is None:
            lb_s = "NA"
        else:
            lb_s = lb
        px_d = px.get(d_day)
        # 防追高
        lp = low20[i]
        if lp <= 0 or cl[i]/lp-1 > 0.10:
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
        # 前向收益(60分收盘→对应日线后10/20/40日收盘)
        d_sig = int(dt[i][:10].replace("-", ""))
        if d_sig not in px:
            continue
        gi = didx.get(d_sig)
        if gi is None or gi+40 >= len(dates_all):
            continue
        c0 = px[d_sig]
        f10 = f20 = f40 = None
        d10 = dates_all[gi+10]; d20 = dates_all[gi+20]; d40 = dates_all[gi+40]
        f10 = px.get(d10); f20 = px.get(d20); f40 = px.get(d40)
        f10 = f10/c0-1 if f10 else None
        f20 = f20/c0-1 if f20 else None
        f40 = f40/c0-1 if f40 else None
        # 条件维度
        pos250_ = None
        if len(px) >= 250:
            dd_sorted = sorted(px.keys())
            last250 = px[d_sig] if False else None
        # 用60分近似
        lo250 = cl[max(0, i-1000):i+1].min() if i >= 1000 else cl[:i+1].min()
        hi250 = cl[max(0, i-1000):i+1].max() if i >= 1000 else cl[:i+1].max()
        pos250 = (cl[i]-lo250)/max(hi250-lo250, 1e-9)*100 if hi250 > lo250 else 50.0
        above_ma = cl[i] > ma20d[i] if not np.isnan(ma20d[i]) else None
        REC.append({"code": code, "name": nm, "dt": dt[i][:16], "struct": struct,
                    "pos": "low" if pos250 <= 30 else ("mid" if pos250 <= 60 else "high"),
                    "env": lb_s, "dir_ok": above_ma,
                    "volr": round(float(vv[i]/max(v5v, 1e-9)), 2),
                    "f10": round(f10*100, 2) if f10 is not None else None,
                    "f20": round(f20*100, 2) if f20 is not None else None,
                    "f40": round(f40*100, 2) if f40 is not None else None})
    scanned += 1
    if scanned % 400 == 0:
        print(f"...{scanned}/{len(uni)} 信号累计{len(REC)} ({time.time()-t1:.0f}s)", flush=True)

print(f"\n扫描完成: {scanned}只 信号{len(REC)}个 ({time.time()-t1:.0f}s)", flush=True)
out = os.path.join(ROOT, "data", "swing_m60_fullsurvey_20261029.jsonl")
with open(out, "w", encoding="utf-8") as f:
    for r in REC:
        f.write(json.dumps(r, ensure_ascii=False)+"\n")
print(f"落盘: {out}")

# ══════ 统计矩阵 ══════
def st(key, flt):
    a = [r[key] for r in REC if flt(r) and r[key] is not None]
    a = np.array(a)
    if not len(a): return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean()), 2), "win": round(float((a > 0).mean())*100, 1),
            "med": round(float(np.median(a)), 2)}

K = "f20"
report = {}
report["overall"] = st(K, lambda r: True)
for sv in ("B1", "B2", "B3"):
    report[f"struct_{sv}"] = st(K, lambda r, sv=sv: r["struct"] == sv)
for e in ("上涨趋势", "下跌趋势", "宽幅震荡", "窄幅震荡"):
    report[f"env_{e}"] = st(K, lambda r, e=e: r["env"] == e)
for p in ("low", "mid", "high"):
    report[f"pos_{p}"] = st(K, lambda r, p=p: r["pos"] == p)
for d in (True, False):
    report[f"dir_{'above' if d else 'below'}"] = st(K, lambda r, d=d: r["dir_ok"] is d)
# 组合: 结构×位置
for sv in ("B1", "B2", "B3"):
    for p in ("low", "mid", "high"):
        report[f"{sv}_{p}"] = st(K, lambda r, sv=sv, p=p: r["struct"] == sv and r["pos"] == p)
# 组合: 环境×结构(最优/最差区)
report["up_B3"] = st(K, lambda r: r["env"] == "上涨趋势" and r["struct"] == "B3")
report["dn_B1"] = st(K, lambda r: r["env"] == "下跌趋势" and r["struct"] == "B1")
report["up_all"] = st(K, lambda r: r["env"] == "上涨趋势")
report["dn_all"] = st(K, lambda r: r["env"] == "下跌趋势")
# 高量能vs低量能
report["volr_le1"] = st(K, lambda r: r["volr"] is not None and r["volr"] <= 1)
report["volr_gt2"] = st(K, lambda r: r["volr"] is not None and r["volr"] > 2)

out2 = os.path.join(ROOT, "data", "swing_m60_fullsurvey_matrix_20261029.json")
json.dump({"n_signals": len(REC), "n_scanned": scanned, "matrix": report},
          open(out2, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n=== 统计矩阵(fwd20) ===")
for k, v in report.items():
    if v.get("n"):
        print(f"  {k:<24} n={v['n']:>6} 均值{v['mean']:>+7}% 胜率{v['win']:>5}% 中位{v['med']:>+6}%")
print(f"落盘: {out2}")
