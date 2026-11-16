# -*- coding: utf-8 -*-
"""回购公告事件检验 (事件流水线E7, 20261025)。
主臂=全部回购公告(剔停止/否决); 对照=大额/小额/预案/完成; t20e/t60e; 匹配基准。
只读qfq+st_history+staged csv, 零交易权。输出: analysis/repurchase_20261025_result.json
"""
import hashlib, json, sqlite3, sys, time
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
STAGE = r"D:\zcodesj\data\repurchase_stage_full.csv"
PREREG = r"D:\zcodesj\data\PREREG_REPURCHASE_20261025.json"
OUT = r"D:\zcodesj\analysis\repurchase_20261025_result.json"
START, END = 20160101, 20261023
COST_RT = 0.003
STOPS = {"t20e": (20, 0.92), "t60e": (60, 0.92)}
t0 = time.time()
def log(m): print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)
def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit()); return d[-6:] if len(d) >= 6 else d

pre = json.load(open(PREREG, encoding="utf-8"))
fp = hashlib.md5(json.dumps(pre["rule_frozen"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
pre["fingerprint"] = fp
json.dump(pre, open(PREREG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log(f"指纹={fp}")

# ---------- 回购数据 ----------
rp = pd.read_csv(STAGE, dtype={"股票代码": str})
rp["code"] = rp["股票代码"].map(norm_code)
rp = rp[~rp["code"].str.startswith(("688","689")) & rp["code"].str[0].isin(["6","0","3"])]
rp["d"] = pd.to_datetime(rp["最新公告日期"], format="%Y-%m-%d", errors="coerce").dt.strftime("%Y%m%d")
rp = rp[rp["d"].notna()].copy(); rp["d"] = rp["d"].astype("int64")
rp = rp[(rp["d"] >= START) & (rp["d"] <= END)]
rp["amt_lo"] = pd.to_numeric(rp["计划回购金额区间-下限"], errors="coerce")
# 剔停止/否决
rp = rp[~rp["实施进度"].isin(["停止实施","股东大会否决"])]
# 同股同日去重 → 90日历日冷却
rp = rp.sort_values("amt_lo", ascending=False).drop_duplicates(subset=["code","d"], keep="first")
rp = rp.sort_values(["code","d"]); seen, dedup = {}, []
for _, r in rp.iterrows():
    c = r["code"]
    if c in seen and (int(r["d"]) - seen[c]) < 10000000: continue
    seen[c] = int(r["d"]); dedup.append(r)
rp = pd.DataFrame(dedup)
log(f"回购事件(去重冷却)={len(rp)}")

main = rp  # 全部回购(剔停止/否决)
ctrl_big = rp[rp["amt_lo"] >= 1e8]
ctrl_small = rp[rp["amt_lo"] < 1e8]
ctrl_plan = rp[rp["实施进度"].isin(["董事会预案","股东大会通过"])]
ctrl_done = rp[rp["实施进度"] == "完成实施"]
log(f"主臂={len(main)} 大额={len(ctrl_big)} 小额={len(ctrl_small)} 预案={len(ctrl_plan)} 完成={len(ctrl_done)}")

# ---------- 价格底座 ----------
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query("SELECT code,date,close,volume FROM stock_daily_meta_qfq ORDER BY code,date", con); con.close()
df["code"] = df["code"].map(norm_code)
df = df[df["code"].str[0].isin(["6","0","3"]) & ~df["code"].str.startswith(("688","689"))]
df = df[(df["close"]>0)&(df["volume"]>0)].copy()
df["dl"] = df.groupby("code").cumcount()+1; df = df[df["dl"]>60].reset_index(drop=True)
df["d"] = df["date"].astype("int64")
piv = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
d_sorted = piv.index.to_numpy(dtype="int64"); codes_list = list(piv.columns)
close_by = {c: piv[c].to_numpy(dtype=float) for c in codes_list}
pos_of = {int(x): i for i,x in enumerate(d_sorted)}
st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_r = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con); st_con.close()
st_set = set(zip(st_r["code"].map(norm_code), st_r["date"].astype("int64")))
log(f"qfq 股={len(codes_list)}")

def outcome(code, d_ev):
    if code not in close_by: return None
    arr = close_by[code]; i = int(np.searchsorted(d_sorted, d_ev))
    if i >= len(d_sorted) or (code,int(d_sorted[i])) in st_set: return None
    e = i+1
    if e >= len(arr) or not np.isfinite(arr[e]) or arr[e]<=0 or arr[e]>30.0: return None
    entry = arr[e]; out = {"d": int(d_sorted[i])}
    for tag,(h,sp) in STOPS.items():
        end = min(e+h, len(arr)-1)
        if end <= e: out[tag]=None; continue
        w = arr[e+1:end+1]; broke = w <= entry*sp
        xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
        if np.isfinite(xp): out[tag]=(bool(xp>entry), xp/entry-1.0)
        else: out[tag]=None
    return out

def run_arm(sub_df):
    events = []
    for _, r in sub_df.iterrows():
        o = outcome(r["code"], int(r["d"]))
        if o and o.get("t20e"): events.append({"code": r["code"], **o})
    ev = pd.DataFrame(events)
    if len(ev)==0: return {"n":0}
    out = {"n": int(len(ev))}
    need = sorted(set(int(x) for x in ev["d"]))
    bmap = {}
    for T in need:
        i = pos_of.get(T)
        if i is None: continue
        ea = piv.iloc[i].to_numpy(dtype=float); w20,w60 = [],[]
        for ci in range(len(codes_list)):
            ev_ = ea[ci]
            if not np.isfinite(ev_) or ev_<=0 or ev_>30.0: continue
            cx = codes_list[ci]
            if (cx,T) in st_set: continue
            arr = close_by[cx]; e = i+1
            if e>=len(arr) or not np.isfinite(arr[e]) or arr[e]<=0 or arr[e]>30.0: continue
            ent = arr[e]
            for tag,(h,sp) in STOPS.items():
                end = min(e+h, len(arr)-1)
                if end<=e: continue
                w = arr[e+1:end+1]; br = w<=ent*sp
                xp = w[int(np.argmax(br))] if br.any() else arr[end]
                if not np.isfinite(xp): continue
                if tag=="t20e": w20.append(xp>ent)
                else: w60.append(xp>ent)
        if w20: bmap[T] = {"bw20": float(np.mean(w20)), "bw60": float(np.mean(w60)) if w60 else None}
    bw = ev["d"].map(lambda x: bmap.get(x,{}).get("bw20"))
    s = ev[bw.notna()]; out["n_valid"] = len(s)
    if len(s)==0: return out
    out["t20e"] = {"win": round(float(s["t20e"].apply(lambda x:x[0]).mean()),4),
                   "base": round(float(bw[s.index].mean()),4)}
    out["t20e"]["exc_pp"] = round((out["t20e"]["win"]-out["t20e"]["base"])*100,2)
    out["t20e"]["net_pp"] = round((float(s["t20e"].apply(lambda x:x[1]).mean())-COST_RT)*100,2)
    s60 = s[s["t60e"].notna()]
    if len(s60)>0:
        b60 = s60["d"].map(lambda x: bmap.get(x,{}).get("bw60"))
        m60 = b60.notna()
        if m60.sum()>0:
            out["t60e"] = {"win": round(float(s60[m60]["t60e"].apply(lambda x:x[0]).mean()),4),
                           "base": round(float(b60[m60].mean()),4)}
            out["t60e"]["exc_pp"] = round((out["t60e"]["win"]-out["t60e"]["base"])*100,2)
    s2 = s.assign(year=s["d"]//10000)
    by = {}
    for y,g in s2.groupby("year"):
        if len(g)>=20:
            b = g["d"].map(lambda x: bmap.get(x,{}).get("bw20")).mean()
            by[int(y)] = round((float(g["t20e"].apply(lambda x:x[0]).mean())-float(b))*100,2)
    out["by_year"] = by
    out["n_pos_years"] = int(np.sum([v>0 for v in by.values()]))
    return out

res_main = run_arm(main)
res_ctrl = {k: run_arm(v) for k,v in {"big(>=1亿)":ctrl_big,"small(<1亿)":ctrl_small,"plan":ctrl_plan,"done":ctrl_done}.items()}
gates = {
    "G1(n>=500)": res_main.get("n",0)>=500,
    "G2_t20e(exc>=1.5)": res_main.get("t20e",{}).get("exc_pp",-99)>=1.5,
    "G3_net(>=0.5)": res_main.get("t20e",{}).get("net_pp",-99)>=0.5,
    "G4_years(>=6正)": res_main.get("n_pos_years",0)>=6,
    "G5_t60e(>=1.0)": res_main.get("t60e",{}).get("exc_pp",-99)>=1.0,
}
verdict = "PASS_CANDIDATE" if all(gates.values()) else "FAIL"
out = {"meta":{"generated_at":"2026-10-25","order":"继续→回购E7(复合治理信号族首个)","prereg":PREREG,"fingerprint":fp,"zero_trade_authority":True},
       "main":res_main,"control":res_ctrl,"gates":{k:bool(v) for k,v in gates.items()},"verdict":verdict}
with open(OUT,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=1,default=str)
log(f"完成→{OUT}")
print(json.dumps({"verdict":verdict,"gates":out["gates"],
                  "main_t20e":res_main.get("t20e"),"main_t60e":res_main.get("t60e"),
                  "n":res_main.get("n"),"n_pos_years":res_main.get("n_pos_years"),"by_year":res_main.get("by_year"),
                  "ctrl":{k:{"n":v.get("n"),"t20_exc":v.get("t20e",{}).get("exc_pp"),"t60_exc":v.get("t60e",{}).get("exc_pp")} for k,v in res_ctrl.items()}},
                 ensure_ascii=False,indent=1,default=str))
