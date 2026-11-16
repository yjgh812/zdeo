# -*- coding: utf-8 -*-
"""apool_survivorship_20261018.py — A池格幸存者敞口重跑·合并面板 (2026-10-18, V4遗留项关闭)"""
import json, sqlite3, sys, time, gc
import numpy as np
import pandas as pd
from scipy.ndimage import maximum_filter1d, minimum_filter1d
sys.stdout.reconfigure(encoding="utf-8")
T0 = time.time()
def phase(msg): print(f"[{time.time()-T0:.0f}s] {msg}", flush=True)

def load_panel(db, delisted=False):
    con = sqlite3.connect("file:" + db.replace("\\","/") + "?mode=ro", uri=True)
    if delisted:
        raw = pd.read_sql("SELECT code,date,open,high,low,close,volume,amount FROM stock_daily_delisted_raw WHERE date>='20160101'", con)
        fac = pd.read_sql("SELECT code,date,backAdjustFactor FROM adjust_factor", con)
        con.close()
        raw["code"] = raw["code"].str[-6:]; raw["dt"] = pd.to_datetime(raw["date"])
        fac["dt"] = pd.to_datetime(fac["date"])
        raw = raw.sort_values(["code","dt"]).reset_index(drop=True)
        fac = fac.sort_values(["code","dt"]).reset_index(drop=True)
        m = pd.merge_asof(raw.sort_values("dt"), fac.rename(columns={"dt":"fdt"}).sort_values("fdt"),
                          left_on="dt", right_on="fdt", by="code", direction="backward")
        m["backAdjustFactor"] = m["backAdjustFactor"].fillna(1.0)
        for c in ("open","high","low","close"): m[c] = m[c] * m["backAdjustFactor"]
        m["date"] = m["dt"].dt.strftime("%Y%m%d")
        m = m[(m["volume"]>0)&(m["open"]>0)][["code","date","open","high","low","close","volume","amount"]].copy()
        return m
    df = pd.read_sql("SELECT code,date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq "
                     "WHERE date>='20160101' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
                     "AND substr(code,1,2) NOT IN ('42','83','87','92')", con)
    con.close()
    df = df[(df["volume"]>0)&(df["open"]>0)].copy()
    df["date"] = df["date"].astype(str)
    return df

def extract(df, tag):
    df = df.sort_values(["code","date"]).reset_index(drop=True)
    close = df["close"].to_numpy(float); opn = df["open"].to_numpy(float)
    low_ = df["low"].to_numpy(float); vol = df["volume"].to_numpy(float)
    dates = df["date"].to_numpy(); codes = df["code"].to_numpy()
    N = len(df); del df; gc.collect()
    codes_id, uniq_codes = pd.factorize(codes, sort=True)
    starts = np.searchsorted(codes, uniq_codes)
    counts = np.diff(np.append(starts, N)); tbar = np.arange(N) - starts[codes_id]
    row_of = np.arange(N); n_stocks = len(uniq_codes)
    def shifted(x,k):
        idx = row_of - k; ok = idx >= starts[codes_id]
        out = np.full(N, np.nan); out[ok] = x[idx[ok]]; return out
    def rma(x,w):
        cs = np.concatenate([[0.0], np.cumsum(x)]); idx0 = row_of - w + 1
        ok = (idx0 >= starts[codes_id]) & (row_of+1 <= N)
        out = np.full(N, np.nan); out[ok] = (cs[row_of[ok]+1]-cs[idx0[ok]])/w; return out
    ma5=rma(close,5); ma10=rma(close,10); ma20=rma(close,20); ma60=rma(close,60)
    ma20p5=shifted(ma20,5); prev_c=shifted(close,1)
    vma5=rma(vol,5); vma20=rma(vol,20); vma120=rma(vol,120)
    chg=close/prev_c-1.0; ret20=close/shifted(close,20)-1.0
    big=((vma20>0)&(vol>=2.0*vma20)&(chg>=0.03)).astype(float)
    pos250=np.full(N,np.nan); big60=np.zeros(N,dtype=bool)
    for s in range(n_stocks):
        a,b=starts[s],starts[s]+counts[s]; seg=close[a:b]
        if b-a<250: continue
        lo=minimum_filter1d(seg,250,origin=124,mode="nearest")
        hi=maximum_filter1d(seg,250,origin=124,mode="nearest")
        r=hi-lo; out=np.full(b-a,np.nan)
        okp=(r>0)&(np.arange(b-a)>=249); out[okp]=(seg[okp]-lo[okp])/r[okp]*100
        pos250[a:b]=out
        b60=maximum_filter1d(big[a:b],60,origin=29,mode="nearest"); b60[:59]=0.0
        big60[a:b]=(b60==1.0)
    vr=np.full(N,np.nan); okr=vma120>0; vr[okr]=vma5[okr]/vma120[okr]
    trend=np.where((ma5>ma10)&(ma10>ma20)&(ma20>ma60),1,np.where((ma5<ma10)&(ma10<ma20)&(ma20<ma60),-1,0))
    pull=(~np.isnan(ma10))&(low_<=ma10)&(ma10<=close)&(ma20>ma20p5)
    pop=(pos250<=50)&(close>ma20)&(ma20>ma60)&big60&(close<=30.0)
    shrink=vol<vma5
    day_id,uniq_days=pd.factorize(dates,sort=True); n_days=len(uniq_days)
    m_low=np.zeros(n_days); m_mom=np.zeros(n_days); m_tot=np.zeros(n_days)
    inwin=(tbar>=260)&np.isfinite(pos250)
    np.add.at(m_tot,day_id[inwin],1.0); np.add.at(m_low,day_id[inwin&(pos250<40)],1.0)
    rok=inwin&np.isfinite(ret20)&(ret20>0); np.add.at(m_mom,day_id[rok],1.0)
    valid=m_tot>800
    low_ratio=np.where(valid,m_low/np.maximum(m_tot,1),np.nan)
    mom_ratio=np.where(valid,m_mom/np.maximum(m_tot,1),np.nan)
    cond=((trend==0)|(trend==-1))&(pos250<40)&(vr<1.0)&(close<=10.0)&pull&pop&shrink&(tbar>=260)&(tbar<counts[codes_id]-21)
    cand=np.where(cond)[0]
    kept=[]; last=np.full(n_stocks,-99,dtype=np.int64)
    for r in cand:
        cid=codes_id[r]; tb=tbar[r]
        if tb-last[cid]>=5: kept.append(r); last[cid]=tb
    kept=np.asarray(kept,dtype=np.int64)
    entry=opn[kept+1]; px=close[kept]
    gem_all=np.fromiter((u.startswith("3") for u in uniq_codes),bool,n_stocks)
    gem=gem_all[codes_id[kept]]; lim=px*np.where(gem,1.195,1.095)
    blocked=entry>=lim; ok=~blocked; kt=kept[ok]; k_entry=entry[ok]
    idx_mat=kt[:,None]+np.arange(1,21)[None,:]
    E=close[idx_mat]; brk=E<=(0.92*k_entry)[:,None]; anyb=brk.any(axis=1)
    first_rel=np.where(anyb,brk.argmax(axis=1),19)
    ex=E[np.arange(len(kt)),first_rel]; vt=ex/k_entry-1.0
    ev_days=day_id[kt]; ev_years=np.array([d[:4] for d in dates[kt]])
    ev_codes=uniq_codes[codes_id[kt]]
    phase(f"{tag}: 事件n={len(vt)}")
    return dict(n=len(vt),vt=vt,ev_days=ev_days,ev_years=ev_years,ev_codes=ev_codes,
                low_ratio_ev=low_ratio[ev_days],mom_ratio_ev=mom_ratio[ev_days],ev_dates=dates[kt])

def st(vts):
    if len(vts)==0: return dict(n=0)
    return dict(n=int(len(vts)),med=round(float(np.median(vts))*100,2),
                mean=round(float(np.mean(vts))*100,2),win=round(float((vts>0).mean())*100,1))

WB=r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
DEL=r"D:\zcodesj\data\delisted_segments_baostock.db"
OUT_MD=r"D:\zcodesj\analysis\apool_survivorship_20261018_out.md"

act_df=load_panel(WB,False); phase(f"活股面板 {len(act_df)}行")
del_df=load_panel(DEL,True); phase(f"退市段面板 {len(del_df)}行 {del_df['code'].nunique()}码")

r_act=extract(act_df,"活股")
merged=pd.concat([act_df,del_df],ignore_index=True).drop_duplicates(subset=["code","date"],keep="last").reset_index(drop=True)
r_mrg=extract(merged,"合并")

del_codes=set(del_df["code"].unique())
is_del=np.isin(r_mrg["ev_codes"],list(del_codes))
del_vt=r_mrg["vt"][is_del]
lr=r_mrg["low_ratio_ev"]; mr=r_mrg["mom_ratio_ev"]
q_mask=(lr>=0.70)&(mr<=0.50); q_vt=r_mrg["vt"][q_mask]
q_del=r_mrg["vt"][q_mask&is_del]

act_st=st(r_act["vt"]); del_st=st(del_vt); mrg_st=st(r_mrg["vt"]); q_st=st(q_vt)

lines=[
    "# A池格幸存者敞口重跑·合并面板(2026-10-18, V4遗留项关闭)", "",
    "> 管线=vec逐位复刻 | 面板=活股wb+退市段baostock(段内自洽后复权) | 零交易权", "",
    "## 总对比", "", "| 面板 | n | med20 | mean | win |", "|---|---|---|---|---|",
    f"| 活股源(A) | {act_st['n']} | {act_st['med']:+.2f}% | {act_st['mean']:+.2f}% | {act_st['win']}% |",
    f"| 退市段(D) | {del_st['n']} | {del_st['med']:+.2f}% | {del_st['mean']:+.2f}% | {del_st['win']}% |",
    f"| **合并(A+D)** | **{mrg_st['n']}** | **{mrg_st['med']:+.2f}%** | **{mrg_st['mean']:+.2f}%** | **{mrg_st['win']}%** |",
    "", f"退市段贡献 {del_st['n']} 事件({del_st['n']/max(mrg_st['n'],1)*100:.1f}%), "
    f"涉及 {len(del_codes & set(r_mrg['ev_codes'].tolist()))} 只码。",
    f"幸存者偏差方向: 活股 med {act_st['med']:+.2f} vs 合并 {mrg_st['med']:+.2f} → 差 {act_st['med']-mrg_st['med']:+.2f}pp。", "",
    "## 门A开∧门C关(合并面板)", "",
    f"- n={q_st['n']} med={q_st['med']:+.2f}% win={q_st['win']}%",
    f"- 其中退市码事件 {int(is_del[q_mask].sum())} 个", "",
    "## 退市段A池格事件分年(如有)", "", "| 年 | n | med | win |", "|---|---|---|---|"]
yrs_d=r_mrg["ev_years"][is_del]
if len(yrs_d):
    for y in sorted(set(yrs_d)):
        v=r_mrg["vt"][is_del&(r_mrg["ev_years"]==y)]
        if len(v): lines.append(f"| {y} | {len(v)} | {float(np.median(v))*100:+.2f}% | {float((v>0).mean())*100:.1f}% |")
else:
    lines.append("- 退市段0个A池格事件(退市股不符合A池格条件)")
lines+=["", "## 判读边界", "",
    "- 面板级拼接合规(退市段整段自洽); 禁逐bar跨源拼接;",
    "- 门比率分母在合并面板含退市股, 与活股口径有微差(已注记);",
    "- V4遗留项关闭: A池格+T1族两口径均已量化。"]

import io
io.open(OUT_MD,"w",encoding="utf-8").write("\n".join(lines)+"\n")
json.dump(dict(active=act_st,delisted=del_st,merged=mrg_st,quadrant=q_st),
          open(r"D:\zcodesj\analysis\apool_survivorship_20261018_out.json","w",encoding="utf-8"),
          ensure_ascii=False,indent=1)
print(f"[OK] act={act_st} del={del_st} mrg={mrg_st}")
print(f"[OK] 已直出 {OUT_MD}")
