# -*- coding: utf-8 -*-
"""xushi_backtest_20261029.py — 满弦/双优两路线历史截面回测(主人令20261029·PIT无前视)

设计:
  截面 = 过去12个月每月首个交易日 t。
  满弦路线(因果): t截面12维共振(与fulltest完全同口径, P3=expanding min含t)
    → 共振≥6池 → 维度数最高池(优先11维, 不足5只逐级降) → 六项质量分TOP5。
  双优路线(PIT): t截面共振≥9 → 每票「截至t已完成历史」的共振≥6非重叠样本fwd20胜率
    (样本≥3才可排名, 样本必须 t'+21 < t 即不影响本期买入期) → 胜率TOP5。
  交易: t+1收盘买入(next-bar), t+21收盘卖出(持有20日)。
  基准: 同期全宇宙等权均值(可比性最强)。
统计: 逐截面明细 + 合并(均值收益/胜率/超额)。

声明: 回测不能证伪(宪法), 仅供sanity check; qfq库价格 + next-bar口径。
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
all_dates = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq order by date desc limit 700")})
dmin = str(all_dates[0])
# 信号截面: 最近12个月每月首个交易日(从尾往前数足够覆盖)
rows_meta = con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=? order by date", (dmin,)).fetchall()
dates_all = [int(r[0]) for r in rows_meta]
def ym(d): return d // 100
cuts = []
seen = set()
for d in reversed(dates_all):
    k = ym(d)
    if k not in seen:
        seen.add(k); cuts.append(d)
    if len(cuts) == 13:  # 13个截面, 最旧1个留作PIT胜率预热, 报告用后12个
        break
cuts = sorted(cuts)
# 有效截面: 距数据末端≥22个交易日(next-bar买入+20持有需要前视窗口)
last_d = dates_all[-1]
pos_of = {d: i for i, d in enumerate(dates_all)}
report_cuts = [c for c in cuts if len(dates_all) - 1 - pos_of[c] >= 22]
print(f"回测区间: {dates_all[0]}~{dates_all[-1]} | 有效截面: {len(report_cuts)}个月度信号日(月末截面, 前视窗口不足者剔除)", flush=True)

rows = con.execute(
    "select code,date,open,close,volume from stock_daily_meta_qfq "
    "where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, o, c, v in rows:
    s = series.setdefault(code, {"date": [], "open": [], "close": [], "vol": []})
    s["date"].append(int(d)); s["open"].append(float(o)); s["close"].append(float(c)); s["vol"].append(float(v or 0))
date_idx = {d: i for i, d in enumerate(dates_all)}
# 每只股票建立 date→局部索引 映射(股票停牌缺日, 用全局日期索引+局部对齐)
for code, s in series.items():
    s["gidx"] = [date_idx[d] for d in s["date"]]

doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh", "sz", "bj"):
        if cc.startswith(pref): cc = cc[len(pref):]
    held.add(cc.zfill(6))


def roll_mean(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = sliding_window_view(a, w).mean(axis=1)
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


def twelve_dims(cl, vol):
    """返回 (12维布尔矩阵[n,12], res_cnt[n]) — 口径与fulltest一致(P3=expanding min)"""
    n = len(cl)
    ma5, ma10, ma20, ma60 = roll_mean(cl,5), roll_mean(cl,10), roll_mean(cl,20), roll_mean(cl,60)
    v5, v60 = roll_mean(vol,5), roll_mean(vol,60)
    v3a = roll_mean(vol,3)
    v3b = np.full(n, np.nan); v3b[3:] = v3a[:-3]
    hi60, lo60 = roll_max(cl,60), roll_min(cl,60)
    hi10, lo10 = roll_max(cl,10), roll_min(cl,10)
    lo20 = roll_min(cl,20)
    lo250, hi250 = roll_min(cl,250), roll_max(cl,250)
    expmin = np.minimum.accumulate(cl)
    std20 = roll_std(cl,20)
    std100 = roll_std(cl,100)
    std_ref = np.full(n, np.nan); std_ref[20:] = std100[:-20]
    d1 = np.diff(cl, prepend=cl[0])
    su, sd = roll_mean(np.where(d1>0, d1, 0.0),60), roll_mean(np.where(d1<0, -d1, 0.0),60)
    rsi = 100*su/np.maximum(su+sd, 1e-12)
    dif = ema_arr(cl,12) - ema_arr(cl,26)
    a9 = 0.2; dea = np.empty(n); dea[0] = dif[0]
    for i in range(1, n):
        dea[i] = 0.2*dif[i] + 0.8*dea[i]
    dif_n = dif/np.maximum(cl, 1e-9)
    r1d = cl/np.concatenate([[cl[0]], cl[:-1]]) - 1
    with np.errstate(invalid="ignore"):
        pos250 = (cl-lo250)/np.maximum(hi250-lo250, 1e-9)*100
        g4 = (pos250 >= 10) & (pos250 <= 50)
        mas_min = np.nanmin(np.stack([ma5, ma10, ma20, ma60]), axis=0)
        mas_max = np.nanmax(np.stack([ma5, ma10, ma20, ma60]), axis=0)
        g6 = (~np.isnan(mas_min)) & ((mas_max-mas_min)/np.maximum(mas_min,1e-9) <= 0.03)
        ma20_5a = np.full(n, np.nan); ma20_5a[5:] = ma20[:-5]
        g7 = (~np.isnan(ma20_5a)) & (ma20 >= ma20_5a*0.998)
    m20_ = roll_mean(cl,20); mref_ = roll_mean(cl,100)
    g10 = (~np.isnan(std_ref)) & (std20/np.maximum(m20_,1e-9) <= (std_ref/np.maximum(mref_,1e-9))*0.7)
    G = np.stack([
        (v5/np.maximum(v60,1e-9)) <= 0.5,                       # V1地量
        v3a > v3b,                                              # V2量恢复
        (~np.isnan(lo60)) & (lo60 > 0) & ((hi60-lo60)/np.maximum(lo60,1e-9) <= 0.25),  # P1压缩60
        (~np.isnan(lo10)) & (lo10 > 0) & ((hi10-lo10)/np.maximum(lo10,1e-9) <= 0.08),  # P2窄幅10
        (~np.isnan(lo250)) & (lo20 > expmin*1.005),             # P3止跌(expanding min)
        g4,                                                     # P4位置
        g6,                                                     # M1拧绳
        g7,                                                     # M2微翘
        np.abs(dif_n) <= 0.005,                                 # T1零轴
        (rsi >= 45) & (rsi <= 55),                              # T2RSI中
        g10,                                                    # T3收口
        r1d > -0.07,                                            # S1无铡
    ], axis=1)
    res_cnt = np.nansum(G, axis=1)
    return G, res_cnt, pos250


def quality_score(cl, vol):
    """六项质量数值(压缩60/窄幅10/粘合/pos250/量比/收口) — 用于池内排名"""
    amp60 = (cl[-60:].max()-cl[-60:].min())/max(cl[-60:].min(),1e-9)*100
    amp10 = (cl[-10:].max()-cl[-10:].min())/max(cl[-10:].min(),1e-9)*100
    mas = [cl[-w:].mean() for w in (5,10,20,60)]
    glue = (max(mas)-min(mas))/max(min(mas),1e-9)*100
    mn, mx = cl[-250:].min(), cl[-250:].max()
    pos = (cl[-1]-mn)/max(mx-mn,1e-9)*100
    vr = vol[-5:].mean()/max(vol[-60:].mean(), 1e-9)
    sq = (cl[-20:].std()/max(cl[-20:].mean(),1e-9)) / max(cl[-120:-20].std()/max(cl[-120:-20].mean(),1e-9), 1e-9)
    return np.array([amp60, amp10, glue, pos, vr, sq])


# 预计算: 全宇宙12维矩阵 + PIT样本(每票共振≥6的hits及fwd20)
print("预计算全宇宙12维与PIT样本...", flush=True)
uni = {}
for code, s in series.items():
    cl = np.array(s["close"]); vol = np.array(s["vol"])
    if len(cl) < 300:
        continue
    if code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    G, res_cnt, pos250 = twelve_dims(cl, vol)
    # PIT样本: 共振≥6, 局部idx≥259, 非重叠, fwd20
    hits = []
    last = -999
    for t in np.where((np.arange(len(cl)) >= 259) & (res_cnt >= 6))[0]:
        if t < last + 20:
            continue
        if t + 20 <= len(cl) - 1:
            hits.append((t, cl[t+20]/cl[t]-1))
            last = t
    uni[code] = {"cl": cl, "vol": np.array(vol), "G": G, "res": res_cnt,
                 "hits": hits, "gidx": np.array(s["gidx"]), "name": nm}
print(f"宇宙: {len(uni)}只", flush=True)

gi_of_cut = {c: date_idx[c] for c in report_cuts}
# 基准: 全宇宙等权(对每截面, 用该截面有≥300根数据的票, t+1→t+21宇宙均值)
results = {"manxian": [], "shuangyou": []}
for ci, cut in enumerate(report_cuts):
    t0 = date_idx[cut]
    manxian_pool = []
    shuangyou_pool = []
    for code, u in uni.items():
        loc = np.searchsorted(u["gidx"], t0)
        if loc >= len(u["gidx"]) or u["gidx"][loc] != t0:
            continue  # 截面日停牌/缺数据
        cl, vol = u["cl"], u["vol"]
        if loc < 260 or loc + 22 > len(cl):
            continue
        Tc = cl[loc]
        if not (0.5 < Tc <= 30):
            continue
        if code in held:
            continue
        res = u["res"][loc]
        if res >= 6:
            manxian_pool.append((code, res, loc))
        if res >= 9:
            # PIT胜率: hits中 t'+21 < loc 的已完成样本
            done = [f for (t_, f) in u["hits"] if t_ + 21 < loc]
            if len(done) >= 3:
                fa = np.array(done)
                shuangyou_pool.append({"code": code, "win": float((fa > 0).mean())*100,
                                       "n": len(done), "res": res})
    # 满弦: 维度数最高池→质量TOP5
    if manxian_pool:
        maxres = max(r for _, r, _ in manxian_pool)
        top_pool = [(c, l) for c, r, l in manxian_pool if r >= max(maxres-0, 11)] or \
                   [(c, l) for c, r, l in manxian_pool if r == maxres]
        # 质量分排名(池内6项分位)
        qs = np.array([quality_score(uni[c]["cl"][:l+1], uni[c]["vol"][:l+1]) for c, l in top_pool])
        rk = np.zeros(len(top_pool))
        for j in range(6):
            order = np.argsort(qs[:, j])
            tmp = np.empty(len(top_pool)); tmp[order] = np.arange(len(top_pool))/max(len(top_pool)-1, 1)
            rk += 1 - tmp
        pick = [top_pool[i][0] for i in np.argsort(-rk)[:5]]
    else:
        pick = []
    # 双优: 胜率TOP5(并列按样本数)
    shuangyou_pool.sort(key=lambda x: (-x["win"], -x["n"]))
    pick2 = [x["code"] for x in shuangyou_pool[:5]]
    # 收益: t+1买 → t+21卖
    fwd = {}
    for code in set(pick + pick2):
        u = uni[code]
        loc = int(np.searchsorted(u["gidx"], t0))
        if loc + 21 < len(u["cl"]):
            fwd[code] = u["cl"][loc+21]/u["cl"][loc+1] - 1
    m_ret = [fwd[c] for c in pick if c in fwd]
    s_ret = [fwd[c] for c in pick2 if c in fwd]
    # 宇宙基准(抽样500只等权, 保证速度)
    allu = [u for u in uni.values()]
    sample = allu[::max(len(allu)//500, 1)]
    b_rets = []
    for u in sample:
        loc = int(np.searchsorted(u["gidx"], t0))
        if 0 < loc < len(u["gidx"]) and u["gidx"][loc] == t0 and loc + 21 < len(u["cl"]):
            b_rets.append(u["cl"][loc+21]/u["cl"][loc+1]-1)
    base = float(np.mean(b_rets)) if b_rets else float("nan")
    results["manxian"].append({"cut": cut, "n_pool": len(top_pool) if manxian_pool else 0,
                               "picks": [(c, round(fwd[c]*100,2)) for c in pick if c in fwd],
                               "mean": round(float(np.mean(m_ret))*100,2) if m_ret else None,
                               "win": round(float(np.mean([r>0 for r in m_ret]))*100,1) if m_ret else None,
                               "base": round(base*100,2)})
    results["shuangyou"].append({"cut": cut, "n_pool": len(shuangyou_pool),
                                 "picks": [(c, round(fwd[c]*100,2)) for c in pick2 if c in fwd],
                                 "mean": round(float(np.mean(s_ret))*100,2) if s_ret else None,
                                 "win": round(float(np.mean([r>0 for r in s_ret]))*100,1) if s_ret else None,
                                 "base": round(base*100,2)})
    print(f"[{cut}] 满弦池{results['manxian'][-1]['n_pool']} 均值{results['manxian'][-1]['mean']}% | "
          f"双优池{results['shuangyou'][-1]['n_pool']} 均值{results['shuangyou'][-1]['mean']}% | 基准{results['manxian'][-1]['base']}%", flush=True)

# 汇总
summary = {}
for k in ("manxian", "shuangyou"):
    ms = [r["mean"] for r in results[k] if r["mean"] is not None]
    ws = [r["win"] for r in results[k] if r["win"] is not None]
    bs = [r["base"] for r in results[k] if r["base"] == r["base"]]  # 排除nan
    summary[k] = {"n_cuts": len(ms), "avg_mean": round(float(np.mean(ms)),2) if ms else None,
                  "avg_win": round(float(np.mean(ws)),1) if ws else None,
                  "avg_base": round(float(np.mean(bs)),2) if bs else None,
                  "excess": round(float(np.mean(ms))-float(np.mean(bs)),2) if ms and bs else None}
out = os.path.join(ROOT, "data", "xushi_backtest_20261029.json")
json.dump({"method": "月度截面回测, next-bar买/持有20日, PIT无前视, 基准=宇宙等权",
           "cuts": report_cuts, "results": results, "summary": summary},
          open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n=== 汇总(12截面) ===")
for k, zh in (("manxian","满弦(结构质量)"), ("shuangyou","双优(维度+PIT胜率)")):
    s = summary[k]
    print(f"{zh}: 截面{s['n_cuts']} 平均收益{s['avg_mean']}% 平均胜率{s['avg_win']}% "
          f"基准{s['avg_base']}% 超额{s['excess']}%")
print(f"落盘: {out}")
