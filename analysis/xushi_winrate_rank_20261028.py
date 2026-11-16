# -*- coding: utf-8 -*-
"""xushi_winrate_rank_20261028.py — 蓄势1747名单按胜率排名(主人令20261028)

口径: 对每只入选股票, 在其自身历史(≤1000根qfq日K)逐bar复算与
revival_fulltest_20261028.py 完全一致的12维蓄势闸门; 每个共振≥6的历史时点
记一个样本, 样本后市 = 之后第20个交易日收盘/当日收盘 - 1。
胜率 = 样本中 fwd20 > 0 的占比。样本<3只的历史不输出确定性胜率(N/A)。

说明(反臆想): 这是【个股自身历史的后验统计】, 样本量普遍偏小(0~20个),
连续共振日样本存在重叠; 胜率排名仅作观察池排序参考, 非买入依据。
"""
import os, sys, json
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
import sqlite3
con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq order by date desc limit 1000")})
dmin = str(dates[0])

sel = json.load(open(os.path.join(ROOT, "data", "xushi_fulltest_20261028.json"), encoding="utf-8"))["results"]
sel_codes = [r["code"] for r in sel]
sel_names = {r["code"]: r["name"] for r in sel}

# 分批取数(IN上限保护)
rows = []
for i in range(0, len(sel_codes), 400):
    chunk = sel_codes[i:i+400]
    q = ",".join("?"*len(chunk))
    rows += con.execute(
        f"select code,date,close,volume from stock_daily_meta_qfq "
        f"where date>=? and code in ({q}) order by code,date", (dmin, *chunk)).fetchall()
con.close()
series = {}
for code, d, c, v in rows:
    s = series.setdefault(code, {"close": [], "vol": []})
    s["close"].append(float(c)); s["vol"].append(float(v or 0))
for code, s in series.items():
    s["close"] = np.array(s["close"]); s["vol"] = np.array(s["vol"])


def roll_mean(a, w):
    if len(a) < w:
        return np.full(len(a), np.nan)
    sw = sliding_window_view(a, w)
    out = np.full(len(a), np.nan)
    out[w-1:] = sw.mean(axis=1)
    return out


def roll_max(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = sliding_window_view(a, w).max(axis=1)
    return out


def roll_min(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = sliding_window_view(a, w).min(axis=1)
    return out


def roll_std(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = sliding_window_view(a, w).std(axis=1)
    return out


def ema_arr(a, n):
    k = 2.0/(n+1); e = a[0]; out = np.empty(len(a)); out[0] = e
    for i in range(1, len(a)):
        e = k*a[i] + (1-k)*e; out[i] = e
    return out


rank = []
no_sample = []
for idx, (code, s) in enumerate(series.items(), 1):
    cl, vol = s["close"], s["vol"]
    n = len(cl)
    if n < 280:
        no_sample.append(code); continue
    # --- 逐bar指标(全部因果: 只用<=t数据) ---
    ma5, ma10, ma20, ma60 = roll_mean(cl,5), roll_mean(cl,10), roll_mean(cl,20), roll_mean(cl,60)
    v5, v60 = roll_mean(vol,5), roll_mean(vol,60)
    hi60, lo60 = roll_max(cl,60), roll_min(cl,60)
    hi10, lo10 = roll_max(cl,10), roll_min(cl,10)
    lo20 = roll_min(cl,20)
    lo250, hi250 = roll_min(cl,250), roll_max(cl,250)
    std20 = roll_std(cl,20)
    # std_ref = std of 100 bars ending at t-20 → roll_std(cl,100) shifted by 20
    std100 = roll_std(cl,100)
    std_ref = np.full(n, np.nan)
    std_ref[20:] = std100[:-20]
    # RSI(60日差分)
    d1 = np.diff(cl, prepend=cl[0])
    up = np.where(d1 > 0, d1, 0.0); dn = np.where(d1 < 0, -d1, 0.0)
    su, sd = roll_mean(up,60), roll_mean(dn,60)
    rsi = 100*su/np.maximum(su+sd, 1e-12)
    # MACD DIF/DEA
    e12, e26 = ema_arr(cl,12), ema_arr(cl,26)
    dif = e12 - e26
    a9 = 0.2; dea = np.empty(n); dea[0] = dif[0]
    for i in range(1, n):
        dea[i] = a9*dif[i] + (1-a9)*dea[i]
    dif_n = dif/np.maximum(cl, 1e-9)
    r1d = cl/np.concatenate([[cl[0]], cl[:-1]]) - 1

    t_idx = np.arange(n)
    ok = (t_idx >= 259)  # 需要250窗+前视完备
    dims_ok = np.ones(n, dtype=bool)
    dims_ok &= ~np.isnan(v5) & ~np.isnan(v60)
    g_v1 = (v5/np.maximum(v60,1e-9)) <= 0.5
    # v3a = mean(vol[t-2..t]) = roll3[t]; v3b = mean(vol[t-5..t-3]) = roll3[t-3]
    v3a_s = roll_mean(vol,3)
    v3b_s = np.full(n, np.nan); v3b_s[3:] = v3a_s[:-3]
    g_v2 = v3a_s > v3b_s
    g_p1 = (~np.isnan(lo60)) & (lo60 > 0) & ((hi60-lo60)/np.maximum(lo60,1e-9) <= 0.25)
    g_p2 = (~np.isnan(lo10)) & (lo10 > 0) & ((hi10-lo10)/np.maximum(lo10,1e-9) <= 0.08)
    g_p3 = (~np.isnan(lo250)) & (lo20 > np.minimum.accumulate(cl)*1.005)  # 复查修正20261029: P3对齐fulltest全窗最低点口径=因果expanding min(原trailing-250偏宽松多判止跌)
    with np.errstate(invalid="ignore"):
        pos250 = np.where(hi250 > lo250, (cl-lo250)/np.maximum(hi250-lo250,1e-9)*100, 50.0)
    g_p4 = (pos250 >= 10) & (pos250 <= 50)
    mas_min = np.nanmin(np.stack([ma5, ma10, ma20, ma60]), axis=0)
    mas_max = np.nanmax(np.stack([ma5, ma10, ma20, ma60]), axis=0)
    g_m1 = (~np.isnan(mas_min)) & ((mas_max-mas_min)/np.maximum(mas_min,1e-9) <= 0.03)
    ma20_5a = np.full(n, np.nan); ma20_5a[5:] = ma20[:-5]
    g_m2 = (~np.isnan(ma20_5a)) & (ma20 >= ma20_5a*0.998)
    g_t1 = np.abs(dif_n) <= 0.005
    g_t2 = (rsi >= 45) & (rsi <= 55)
    m20_ = roll_mean(cl,20)
    std20_n = std20/np.maximum(m20_,1e-9)
    mref_ = roll_mean(cl,100)
    std_ref_n = std_ref/np.maximum(mref_,1e-9)
    g_t3 = (~np.isnan(std_ref_n)) & (std20_n <= std_ref_n*0.7)
    g_s1 = r1d > -0.07

    G = np.stack([g_v1, g_v2, g_p1, g_p2, g_p3, g_p4, g_m1, g_m2, g_t1, g_t2, g_t3, g_s1])
    res_cnt = np.nansum(G, axis=0)
    hit = ok & dims_ok & (res_cnt >= 6)
    hits = np.where(hit)[0]
    # 非重叠采样: 每个信号计入样本后, 之后20bar内的后续信号不重复计
    samples = []
    last_kept = -999
    for t in hits:
        if t < last_kept + 20:
            continue
        if t + 20 <= n - 1:
            fwd20 = cl[t+20]/cl[t] - 1
            fwd10 = cl[t+10]/cl[t] - 1
            samples.append((float(fwd20), float(fwd10), int(res_cnt[t])))
            last_kept = t
    # 无条件基线: 全部合法bar的fwd20上涨占比(含重叠, 仅作对照)
    valid = np.arange(259, n-20)
    base_f20 = cl[valid+20]/cl[valid] - 1
    base_win = float((base_f20 > 0).mean())*100 if len(base_f20) else float("nan")
    if len(samples) < 3:
        no_sample.append(code); continue
    f20 = np.array([x[0] for x in samples]); f10 = np.array([x[1] for x in samples])
    hi = [x for x in samples if x[2] >= 8]
    hi_f20 = np.array([x[0] for x in hi]) if len(hi) >= 3 else None
    rank.append({"code": code, "name": sel_names.get(code, get_name(code) or ""),
                 "n_samples": len(samples), "win20": round(float((f20 > 0).mean())*100, 1),
                 "mean_f20": round(float(f20.mean())*100, 2),
                 "med_f20": round(float(np.median(f20))*100, 2),
                 "win10": round(float((f10 > 0).mean())*100, 1),
                 "base_win": round(base_win, 1),
                 "edge": round(float((f20 > 0).mean())*100 - base_win, 1),
                 "win_hi": round(float((hi_f20 > 0).mean())*100, 1) if hi_f20 is not None else None,
                 "n_hi": len(hi),
                 "last_sig": "近期命中" if hits[-1] >= n-8 else ""})
    if idx % 300 == 0:
        print(f"...{idx}/{len(series)}", flush=True)

rank.sort(key=lambda x: (-x["win20"], -x["mean_f20"], -x["n_samples"]))
out = os.path.join(ROOT, "data", "xushi_winrate_rank_20261028.json")
json.dump({"signal_date": "20261028", "method": "个股自身历史共振≥6后验(非重叠采样), fwd20胜率, 样本≥3, 附无条件基线对照",
           "n_ranked": len(rank), "n_no_sample": len(no_sample),
           "ranked": rank, "no_sample_codes": no_sample},
          open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print(f"\n可排名(样本≥3): {len(rank)}只 | 无历史样本(<3): {len(no_sample)}只")
print("\n=== 胜率TOP50 (win20=蓄势后20日胜率 | base=同股无条件基线 | edge=增量) ===")
for i, r in enumerate(rank[:50], 1):
    print(f"{i:>2}. {r['name']:<8} {r['code']} 胜率{r['win20']:>5}% 基线{r['base_win']:>5}% "
          f"增量{r['edge']:>+6}% 均值{r['mean_f20']:>+6}% 样本{r['n_samples']} "
          f"高共振胜率{r['win_hi'] if r['win_hi'] is not None else 'N/A'}% {r['last_sig']}")
print(f"\n落盘: {out}")
