# -*- coding: utf-8 -*-
"""swing_walkforward_20261029.py — Phase4 walk-forward验证(主人令「继续」)

目标: 判定 Phase3 发现「20日跌≥15% × 上涨趋势环境 → +8.09%/71.1%」是否过拟合/前视幻觉。
  ① 滚动HMM: 评估年Y用近3年观测重拟合, 解码[Y,Y+1) → 纯OOS状态标签(消除全期参数前视)
  ② 超跌(纯因果) × wf状态 分年OOS表
  ③ 敏感性: 阈值{-12,-15,-18,-20}% × 持有{10,20,40}日 + 止跌确认(超跌后5日内收复5日线再入场, 持有20)
  ④ wf与全期标签一致率
  ⑤ 周线突破假信号率v2(真周K重采样, 突破后3周内收盘跌回=失败)
"""
import os, sys, json, sqlite3, time, datetime
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
t0 = time.time()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates_all = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=20150101 order by date")})
dmin = dates_all[0]
gidx = {d: i for i, d in enumerate(dates_all)}
rows = con.execute(
    "select code,date,close,volume from stock_daily_meta_qfq where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, c, v in rows:
    d = int(d)
    s = series.setdefault(code, {"g": [], "c": [], "v": []})
    s["g"].append(gidx[d]); s["c"].append(float(c)); s["v"].append(float(v or 0))
for s in series.values():
    for k in ("g", "c", "v"):
        s[k] = np.array(s[k])
N = len(dates_all)
print(f"数据: {N}日 {len(series)}只 ({time.time()-t0:.0f}s)", flush=True)

# 宇宙等权 + 观测特征(OBS[k] 对应日期 dates_all[k+20])
eq = np.zeros(N); cnt = np.zeros(N)
for code, s in series.items():
    if len(s["c"]) < 130 or code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    eq[s["g"]] += s["c"]; cnt[s["g"]] += 1
eq = eq/np.maximum(cnt, 1)
for i in range(1, N):
    if np.isnan(eq[i]): eq[i] = eq[i-1]
lr = np.diff(np.log(eq))
vol20 = np.array([np.std(lr[max(0, i-19):i+1])*np.sqrt(250)*100 for i in range(len(lr))])
OBS = np.column_stack([lr*100, vol20])[20:]
OBS_DATE = dates_all[21:]
obs_of_gidx = np.arange(N) - 20   # 全局idx -> OBS索引(可为负)

from hmmlearn.hmm import GaussianHMM

def fit_and_map(X_fit):
    h = GaussianHMM(n_components=4, covariance_type="full", n_iter=200, random_state=42)
    h.fit(X_fit)
    st = h.predict(X_fit)
    mr = {k: X_fit[st == k, 0].mean() for k in range(4)}
    order = sorted(range(4), key=lambda k: -mr[k])
    up_s, dn_s = order[0], order[-1]
    mid = order[1:3]
    mv = {k: X_fit[st == k, 1].mean() for k in mid}
    wide = max(mid, key=lambda k: mv[k]); narrow = min(mid, key=lambda k: mv[k])
    return h, {up_s: "上涨趋势", dn_s: "下跌趋势", wide: "宽幅震荡", narrow: "窄幅震荡"}

# ① 滚动HMM
wf_label = {}
for y in range(2018, 2027):
    y0 = y*10000+101; y1 = (y+1)*10000+101
    fit_days = [d for d in dates_all if d < y0]
    if not fit_days:
        continue
    fit_end_g = gidx[fit_days[-1]]           # 拟合窗最后一天全局idx
    fo = obs_of_gidx[fit_end_g] + 1          # 拟合窗OBS终点(含)
    dec_end_g = next((gidx[d] for d in dates_all if d >= y1), N)
    do = max(obs_of_gidx[dec_end_g]+1 if dec_end_g < N else len(OBS), fo+30)
    X_fit = OBS[max(0, fo-750):fo]
    X_dec = OBS[fo:do]
    if len(X_fit) < 300 or len(X_dec) < 20:
        continue
    h, lab = fit_and_map(X_fit)
    st = h.predict(X_dec)
    for j, s_ in enumerate(st):
        wf_label[OBS_DATE[fo+j]] = lab[s_]
    print(f"wf-HMM {y}: fit{len(X_fit)} dec{len(X_dec)} {list(lab.values())}", flush=True)
print(f"滚动HMM完成 ({time.time()-t0:.0f}s)", flush=True)

sw = sqlite3.connect(r"data/swing_labels.db")
full_label = {int(r[0]): r[2] for r in sw.execute("select date,state,label from mkt_state")}
sw.close()
common = [d for d in wf_label if d in full_label]
agree = sum(1 for d in common if wf_label[d] == full_label[d])
print(f"wf vs 全期一致率: {agree/max(len(common),1)*100:.1f}% ({agree}/{len(common)})")

# ②③ 超跌信号 × wf状态 + 敏感性 + 止跌确认
sens_pairs = defaultdict(list)
year_pairs = defaultdict(list)
confirm_rets = []

for ci, (code, s) in enumerate(series.items(), 1):
    c, g = s["c"], s["g"]
    n = len(c)
    if n < 130:
        continue
    r20 = np.full(n, np.nan); r20[20:] = c[20:]/c[:-20] - 1
    ma5 = np.full(n, np.nan); ma5[4:] = np.convolve(c, np.ones(5)/5, mode="valid")
    osig = np.where(r20 <= -0.12)[0]
    for i in osig:
        thr = float(r20[i])
        d_i = dates_all[g[i]]
        lb = wf_label.get(d_i)
        if lb is None:
            continue
        bucket = -0.12 if thr > -0.15 else (-0.15 if thr > -0.18 else (-0.18 if thr > -0.20 else -0.20))
        for hold in (10, 20, 40):
            j = i + 1 + hold
            if j >= n:
                continue
            f = c[j]/c[i+1] - 1
            sens_pairs[(bucket, hold)].append((lb, f))
            if bucket == -0.15 and hold == 20:
                year_pairs[(d_i//10000, lb)].append(f)
    # 止跌确认: 超跌(<=-15%)后5日内首根收复5日线 → 入场持有20
    osig15 = set(int(x) for x in np.where(r20 <= -0.15)[0])
    for i in sorted(osig15):
        if i + 21 >= n:
            continue
        for j in range(i+1, min(i+6, n)):
            if not np.isnan(ma5[j]) and c[j] > ma5[j]:
                fj = c[j+21]/c[j+1] - 1 if j+21 < n else None
                if fj is not None:
                    confirm_rets.append(fj)
                break
    if ci % 1500 == 0:
        print(f"...{ci}/{len(series)}", flush=True)

print(f"信号扫描完成 ({time.time()-t0:.0f}s)", flush=True)

def stat(arr):
    a = np.array(arr)
    if not len(a):
        return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean())*100, 2),
            "win": round(float((a > 0).mean())*100, 1),
            "med": round(float(np.median(a))*100, 2)}

sens = {}
for (thr, hold), pairs in sens_pairs.items():
    by = defaultdict(list)
    for lb, f in pairs:
        by[lb].append(f)
    sens[f"thr{int(-thr*100)}hold{hold}"] = {lb: stat(a) for lb, a in by.items()}
sens["confirm_reclaim5_hold20"] = stat(confirm_rets)

yearly = {}
years = sorted(set(y for (y, lb) in year_pairs))
for y in years:
    yearly[y] = {"up_trend": stat(year_pairs.get((y, "上涨趋势"), [])),
                 "all_states": stat([f for (yy, lb) in year_pairs if yy == y for f in year_pairs[(yy, lb)]])}

# ⑤ 周线突破v2
week_of = np.zeros(N, dtype=int)
for i, d in enumerate(dates_all):
    iso = datetime.date(d//10000, d//100 % 100, d % 100).isocalendar()
    week_of[i] = iso[0]*100 + iso[1]
wk_fail = wk_ok = 0
for ci, (code, s) in enumerate(series.items(), 1):
    c, g = s["c"], s["g"]
    n = len(c)
    if n < 400:
        continue
    wk = week_of[g]
    last_idx = {}
    for i in range(n):
        last_idx[wk[i]] = i
    wlist = sorted(last_idx.items(), key=lambda x: x[1])
    wc = np.array([c[i] for _, i in wlist])
    if len(wc) < 65:
        continue
    for k in range(60, len(wc)-3):
        if wc[k] > wc[k-60:k].max()*1.005:
            nxt = wc[k+1:k+4]
            if len(nxt) == 3:
                if (nxt < wc[k]).any():
                    wk_fail += 1
                else:
                    wk_ok += 1

R = {"built_at": time.strftime("%Y-%m-%d %H:%M"),
     "wf_vs_full_agree_pct": round(agree/max(len(common), 1)*100, 1),
     "yearly_oos": yearly, "sensitivity": sens,
     "weekly_break_v2": {"fail_pct": round(wk_fail/max(wk_fail+wk_ok, 1)*100, 1),
                          "ok_n": wk_ok, "fail_n": wk_fail}}
out = os.path.join(ROOT, "data", "swing_walkforward_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

# wf纯OOS标签落库(幂等UPSERT), 供后续环境分层使用
db = sqlite3.connect(r"data/swing_labels.db")
db.execute("create table if not exists mkt_state_wf(date int primary key, label text)")
db.executemany("insert or replace into mkt_state_wf values(?,?)",
               [(int(d), lb) for d, lb in wf_label.items()])
db.commit(); db.close()
print("wf标签已落库 mkt_state_wf:", len(wf_label), "日")

print("\n=== 分年OOS: 超跌-15% hold20 ===")
for y in years:
    u = yearly[y]["up_trend"]; a = yearly[y]["all_states"]
    print(f"  {y}: 上涨趋势 n={u['n']} {u['mean']}%/胜{u['win']}% | 全状态 {a['mean']}%/胜{a['win']}%")
print("\n=== 敏感性(上涨趋势态) ===")
for k, v in sens.items():
    if isinstance(v, dict) and "上涨趋势" in v:
        s_ = v["上涨趋势"]
        print(f"  {k}: n={s_['n']} {s_['mean']}%/胜{s_['win']}%")
    elif k.startswith("confirm"):
        print(f"  {k}: {v}")
print(f"\n周线突破v2失败率: {R['weekly_break_v2']['fail_pct']}% (日线对照80.1%)")
print(f"落盘: {out} ({time.time()-t0:.0f}s)")
