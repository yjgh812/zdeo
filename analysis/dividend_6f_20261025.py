# -*- coding: utf-8 -*-
"""红利六因子简化版(5因子)·月度TopN检验 (20261025)。
数据: dividend_period_stage.csv(akshare 37,428行) + factor_pit.db v2。
主臂=composite Top20; 分配/盈利单臂对照; t20e; 匹配基准; bp_ep正交。
零交易权。输出: analysis/dividend_6f_20261025_result.json
"""
import hashlib, json, sqlite3, sys, time
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
PITDB = r"file:D:\zcodesj\factor_pit.db?mode=ro"
DIVDB = r"D:\zcodesj\data\dividend_period_stage.csv"
PREREG = r"D:\zcodesj\data\PREREG_DIVIDEND_6F_20261025.json"
OUT = r"D:\zcodesj\analysis\dividend_6f_20261025_result.json"
START, END = 20160101, 20261023
TOPN, COST_RT = 20, 0.003
t0 = time.time()
def log(m): print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)
def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit()); return d[-6:] if len(d) >= 6 else d

pre = json.load(open(PREREG, encoding="utf-8"))
fp = hashlib.md5(json.dumps(pre["rule_frozen"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
pre["fingerprint"] = fp
json.dump(pre, open(PREREG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log(f"指纹={fp}")

# ---------- 分红数据 ----------
dv = pd.read_csv(DIVDB, dtype={"代码": str})
dv["code"] = dv["代码"].map(norm_code)
dv = dv[~dv["code"].str.startswith(("688","689")) & dv["code"].str[0].isin(["6","0","3"])]
dv["cash_div_10"] = pd.to_numeric(dv["现金分红-现金分红比例"], errors="coerce")
dv["eps"] = pd.to_numeric(dv["每股收益"], errors="coerce")
dv["ex_date"] = pd.to_datetime(dv["除权除息日"], errors="coerce").dt.strftime("%Y%m%d")
dv = dv[dv["cash_div_10"].notna() & (dv["cash_div_10"]>0)]
dv["dps"] = dv["cash_div_10"] / 10.0  # 每股分红
dv["year"] = dv["report_period"].astype(str).str[:4].astype(int)
dv = dv.drop_duplicates(subset=["code","report_period"])
log(f"分红记录={len(dv)} 股数={dv['code'].nunique()}")

# ---------- 价格底座 ----------
con = sqlite3.connect(QFQ, uri=True)
px = pd.read_sql_query("SELECT code,date,close FROM stock_daily_meta_qfq ORDER BY code,date", con); con.close()
px["code"] = px["code"].map(norm_code)
px = px[px["code"].str[0].isin(["6","0","3"]) & ~px["code"].str.startswith(("688","689"))]
px = px[px["close"]>0].copy(); px["d"] = px["date"].astype("int64")
piv = px.pivot_table(index="d", columns="code", values="close", aggfunc="last")
d_sorted = piv.index.to_numpy(dtype="int64"); codes_list = list(piv.columns)
pos_of = {int(x):i for i,x in enumerate(d_sorted)}
log(f"qfq 股={len(codes_list)}")

st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_r = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con); st_con.close()
st_set = set(zip(st_r["code"].map(norm_code), st_r["date"].astype("int64")))

# 月末日期
mdates = sorted(d_sorted[(d_sorted>=START)&(d_sorted<=END)])
ym = pd.Series(mdates).astype(str).str[:6]
me_dates = [int(g.iloc[-1]) for _,g in pd.Series(mdates,index=ym).groupby(level=0,sort=True)]

# ---------- PIT加载 ----------
from build_factor_pit_20260815 import get_pit_factor_v2
pit_con = sqlite3.connect(PITDB, uri=True)

# ---------- 逐月末构建因子+选股 ----------
from scipy.stats import rankdata
results_months = []
baskets_main, baskets_dist, baskets_prof, baskets_bp = {}, {}, {}, {}
for mi, T in enumerate(me_dates[:-1]):
    year = int(str(T)[:4])
    # 分红因子: 近3年(含上一年年报)
    dv_recent = dv[dv["year"].isin([year-1, year-2, year-3])]
    if len(dv_recent) < 100: continue
    # f3: 近3年平均股息率(每股分红/年末close近似用当前close)
    row_T = piv.loc[T] if T in piv.index else None
    if row_T is None: continue
    div_groups = dv_recent.groupby("code").agg(
        dps_avg=("dps","mean"), n_years=("dps","count"), dps_latest=("dps","last"))
    div_groups = div_groups[div_groups["n_years"]>=1]
    # f5: 近5年连续分红年数
    dv_5y = dv[dv["year"].isin([year-1,year-2,year-3,year-4,year-5])]
    cont = dv_5y.groupby("code")["dps"].apply(lambda s: (s>0).sum()).rename("continuity")
    # PIT因子
    snap = get_pit_factor_v2(pit_con, T)
    if snap is None or snap.empty: continue
    snap = snap.assign(close=snap["code"].map(row_T))
    snap = snap[(snap["close"]>0)&(snap["close"]<=30.0)
                & ~snap["code"].str.startswith(("688","689"))
                & snap["code"].str[0].isin(["6","0","3"])]
    snap = snap[~((snap["snap_rule"]=="tail")&(snap["ref_date"]<snap["pit_avail_date"]))]
    snap = snap.dropna(subset=["roe","eps","bps"])
    # 剔ST
    keep = [c for c in snap["code"] if (c,T) not in st_set]
    snap = snap[snap["code"].isin(keep)]
    if len(snap) < 200: continue
    # 合并分红因子
    merged = snap.merge(div_groups, left_on="code", right_index=True, how="left")
    merged = merged.merge(cont, left_on="code", right_index=True, how="left")
    merged["dps_avg"] = merged["dps_avg"].fillna(0)
    merged["continuity"] = merged["continuity"].fillna(0)
    # 要求至少1次分红
    merged = merged[merged["n_years"].notna() & (merged["n_years"]>=1)]
    if len(merged) < 50: continue
    # f1: roe(简化用当前PIT roe替代3年滚动——3年数据需多次PIT查询成本过高)
    merged["f1_roe"] = merged["roe"]
    # f2: eps(简化用当前PIT eps)
    merged["f2_eps"] = merged["eps"]
    # f3: 股息率=近3年均每股分红/当前close
    merged["f3_yield"] = merged["dps_avg"] / merged["close"]
    # f4: 前瞻派息=近3年均每股分红×最近eps/当前close(简化)
    merged["f4_fwd"] = merged["dps_avg"] * merged["eps"].clip(lower=0) / merged["close"]
    # f5: continuity
    merged["f5_cont"] = merged["continuity"]
    # 各因子pct_rank
    for f in ["f1_roe","f2_eps","f3_yield","f4_fwd","f5_cont"]:
        merged[f+"_r"] = merged[f].rank(pct=True)
    # composite: 盈利60%分配40%
    merged["composite"] = (0.30*merged["f1_roe_r"] + 0.30*merged["f2_eps_r"]
                          + 0.15*merged["f3_yield_r"] + 0.15*merged["f4_fwd_r"]
                          + 0.10*merged["f5_cont_r"])
    # 分配维度单臂
    merged["dist_only"] = 0.5*merged["f3_yield_r"] + 0.5*merged["f4_fwd_r"] + 0.3*merged["f5_cont_r"]
    # 盈利维度单臂
    merged["prof_only"] = 0.5*merged["f1_roe_r"] + 0.5*merged["f2_eps_r"]
    # bp_ep参照
    merged["bp_"] = merged["bps"]/merged["close"]
    merged["ep_"] = merged["eps"]
    merged["combo_bp"] = (merged["ep_"].rank(pct=True)+merged["bp_"].rank(pct=True))/2
    # 排序选TopN
    baskets_main[T] = merged.nlargest(TOPN, "composite")["code"].tolist()
    baskets_dist[T] = merged.nlargest(TOPN, "dist_only")["code"].tolist()
    baskets_prof[T] = merged.nlargest(TOPN, "prof_only")["code"].tolist()
    baskets_bp[T] = merged.nlargest(TOPN, "combo_bp")["code"].tolist()
pit_con.close()
log(f"月末篮子数={len(baskets_main)}")

# ---------- 前向收益 ----------
def fwd_from(code, T, hold=20):
    if code not in piv.columns: return None
    arr = piv[code].to_numpy(dtype=float)
    i = pos_of.get(T)
    if i is None: return None
    e = i+1
    if e>=len(arr) or not np.isfinite(arr[e]) or arr[e]<=0 or arr[e]>30: return None
    entry = arr[e]; end = min(e+hold, len(arr)-1)
    if end<=e: return None
    w = arr[e+1:end+1]; broke = w<=entry*0.92
    xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
    if not np.isfinite(xp): return None
    return (bool(xp>entry), xp/entry-1.0)

def run_basket(baskets):
    ev_w, ev_r, monthly = [], [], {}
    for T, codes in baskets.items():
        T2_i = None
        for k,T_ in enumerate(me_dates):
            if T_==T and k+1<len(me_dates): T2_i=k+1; break
        if T2_i is None: continue
        T2 = me_dates[T2_i]
        wins, rets = [], []
        for c in codes:
            r = fwd_from(c, T)
            if r: ev_w.append((T, r[0])); ev_r.append(r[1])
            # 月度收益
            if c in piv.columns:
                arr = piv[c].to_numpy(dtype=float)
                i1 = pos_of.get(T); i2 = pos_of.get(T2)
                if i1 is not None and i2 is not None and i1+1<len(arr) and i2<len(arr):
                    if np.isfinite(arr[i1+1]) and arr[i1+1]>0 and np.isfinite(arr[i2]):
                        wins.append(arr[i2]/arr[i1+1]-1.0)
        if wins: monthly[T] = float(np.mean(wins))
    # 基准月度
    base_monthly = {}
    for T in monthly:
        i = pos_of.get(T)
        if i is None: continue
        T2 = None
        for k,T_ in enumerate(me_dates):
            if T_==T and k+1<len(me_dates): T2=me_dates[k+1]; break
        if T2 is None: continue
        i2 = pos_of.get(T2)
        if i2 is None: continue
        row1, row2 = piv.iloc[i], piv.iloc[i2]
        ok = (row1>0)&(row1<=30)&row2.notna()&(row2>0)
        base_monthly[T] = float((row2[ok]/row1[ok]-1).mean()) if ok.sum()>10 else None
    md = [(T, monthly[T]-COST_RT, base_monthly.get(T)) for T in monthly if base_monthly.get(T) is not None]
    if not md: return {"n_months":0}
    total = np.prod([1+r for _,r,_ in md])-1
    base_total = np.prod([1+b for _,_,b in md])-1
    n_y = len(md)/12
    ann = (1+total)**(1/max(n_y,0.1))-1
    base_ann = (1+base_total)**(1/max(n_y,0.1))-1
    by_year = {}
    for T, r, b in md:
        y = int(str(T)[:4])
        by_year.setdefault(y, []).append(r-b)
    by_year = {y: round(float(np.mean(v))*100,2) for y,v in by_year.items()}
    return {"n_months": len(md), "ann": round(ann,4), "base_ann": round(base_ann,4),
            "ann_exc_pp": round((ann-base_ann)*100,2),
            "n_pos_years": int(np.sum([v>0 for v in by_year.values()])), "by_year": by_year,
            "events": len(ev_w),
            "win_event": round(float(np.mean([w for _,w in ev_w]))*1,4) if ev_w else None}

res_main = run_basket(baskets_main)
res_dist = run_basket(baskets_dist)
res_prof = run_basket(baskets_prof)
res_bp = run_basket(baskets_bp)

# 正交: 重叠
ovs = []
for T in baskets_main:
    if T in baskets_bp:
        ovs.append(len(set(baskets_main[T])&set(baskets_bp[T]))/TOPN)

gates = {
    "G1_portfolio(ann_exc>=+3.0)": res_main.get("ann_exc_pp",-99)>=3.0,
    "G2_by_year(>=7正)": res_main.get("n_pos_years",0)>=7,
    "G3_orthogonality(overlap<=30%或vs_bp>0)": (np.mean(ovs)<=0.30 if ovs else False) or (res_main.get("ann_exc_pp",0) > res_bp.get("ann_exc_pp",-99)),
    "G4_event(win_exc>=+1.5)": False,  # 需匹配基准, 简化跳过
    "G5_dist>prof": res_dist.get("ann_exc_pp",-99) > res_prof.get("ann_exc_pp",-99),
}
verdict = "PASS_CANDIDATE" if all(gates.values()) else "FAIL"

out = {"meta":{"generated_at":"2026-10-25","order":"继续→红利六因子(5因子简化版)","prereg":PREREG,"fingerprint":fp,
               "simplification_note":"f1/f2用当前PIT值替代3年滚动(std/diff因多次PIT查询成本过高简化); 现金流因子缓测; f4简化=近3年均dps*eps/close",
               "zero_trade_authority":True},
       "main":res_main,"dist_arm":res_dist,"prof_arm":res_prof,"bp_ref":res_bp,
       "overlap_bp_mean": round(float(np.mean(ovs)),4) if ovs else None,
       "gates":{k:bool(v) for k,v in gates.items()},"verdict":verdict}
with open(OUT,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=1,default=str)
log(f"完成→{OUT}")
print(json.dumps({"verdict":verdict,"gates":out["gates"],
                  "main":{k:res_main.get(k) for k in ("n_months","ann","base_ann","ann_exc_pp","n_pos_years","events")},
                  "dist":{k:res_dist.get(k) for k in ("ann_exc_pp","n_months")},
                  "prof":{k:res_prof.get(k) for k in ("ann_exc_pp","n_months")},
                  "bp":{k:res_bp.get(k) for k in ("ann_exc_pp","n_months")},
                  "overlap":out["overlap_bp_mean"]},ensure_ascii=False,indent=1,default=str))
