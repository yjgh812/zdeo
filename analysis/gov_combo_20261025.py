# -*- coding: utf-8 -*-
"""复合治理信号(回购+增持共振)·60日窗检验 (事件流水线E8, 20261025)。
主臂=同股90日内同时出现回购+增持, 取较晚日为信号日; t60e主口径。
只读qfq+st_history+两staged csv, 零交易权。输出: analysis/gov_combo_20261025_result.json
"""
import hashlib, json, sqlite3, sys, time
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
RP_STAGE = r"D:\zcodesj\data\repurchase_stage_full.csv"
GG_STAGE = r"D:\zcodesj\data\ggcg_stage_full.csv"
PREREG = r"D:\zcodesj\data\PREREG_GOV_COMBO_20261025.json"
OUT = r"D:\zcodesj\analysis\gov_combo_20261025_result.json"
START, END = 20160101, 20261023
COST_RT = 0.003
t0 = time.time()
def log(m): print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)
def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit()); return d[-6:] if len(d) >= 6 else d

pre = json.load(open(PREREG, encoding="utf-8"))
fp = hashlib.md5(json.dumps(pre["rule_frozen"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
pre["fingerprint"] = fp
json.dump(pre, open(PREREG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
log(f"指纹={fp}")

# ---------- 事件加载 ----------
rp = pd.read_csv(RP_STAGE, dtype={"股票代码": str})
rp["code"] = rp["股票代码"].map(norm_code)
rp = rp[~rp["code"].str.startswith(("688","689")) & rp["code"].str[0].isin(["6","0","3"])]
rp["d"] = pd.to_datetime(rp["最新公告日期"], format="%Y-%m-%d", errors="coerce").dt.strftime("%Y%m%d")
rp = rp[rp["d"].notna()].copy(); rp["d"] = rp["d"].astype("int64")
rp = rp[(rp["d"]>=START)&(rp["d"]<=END) & ~rp["实施进度"].isin(["停止实施","股东大会否决"])]
rp_events = set(zip(rp["code"], rp["d"]))

gg = pd.read_csv(GG_STAGE, dtype={"代码": str})
gg["code"] = gg["代码"].map(norm_code)
gg = gg[~gg["code"].str.startswith(("688","689")) & gg["code"].str[0].isin(["6","0","3"])]
gg["d"] = pd.to_datetime(gg["公告日"], format="%Y-%m-%d", errors="coerce").dt.strftime("%Y%m%d")
gg = gg[gg["d"].notna()].copy(); gg["d"] = gg["d"].astype("int64")
gg = gg[(gg["d"]>=START)&(gg["d"]<=END)&(gg["持股变动信息-增减"]=="增持")]
gg_events = set(zip(gg["code"], gg["d"]))

rp_codes = {}; gg_codes = {}
for c,d in rp_events: rp_codes.setdefault(c,[]).append(d)
for c,d in gg_events: gg_codes.setdefault(c,[]).append(d)
log(f"回购事件={len(rp_events)} 增持事件={len(gg_events)}")

# ---------- 共振检测 ----------
combo_events = []
overlap_codes = set(rp_codes) & set(gg_codes)
for c in overlap_codes:
    rp_ds = sorted(rp_codes[c]); gg_ds = sorted(gg_codes[c])
    for rd in rp_ds:
        for gd in gg_ds:
            if abs(rd - gd) < 10000000:  # 90日历日内
                combo_events.append((c, max(rd, gd)))
                break  # 每个回购事件只配一次
# 去重+冷却
combo_events.sort(key=lambda x:(x[0],x[1]))
seen, dedup = {}, []
for c,d in combo_events:
    if c in seen and d - seen[c] < 10000000: continue
    seen[c] = d; dedup.append((c,d))
combo_events = dedup
log(f"共振事件(去重)={len(combo_events)}")

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

def outcome60(code, d_ev):
    if code not in close_by: return None
    arr = close_by[code]; i = int(np.searchsorted(d_sorted, d_ev))
    if i >= len(d_sorted) or (code,int(d_sorted[i])) in st_set: return None
    e = i+1
    if e >= len(arr) or not np.isfinite(arr[e]) or arr[e]<=0 or arr[e]>30.0: return None
    entry = arr[e]
    end = min(e+60, len(arr)-1)
    if end <= e: return None
    w = arr[e+1:end+1]; broke = w <= entry*0.92
    xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
    if not np.isfinite(xp): return None
    return {"d": int(d_sorted[i]), "win": bool(xp>entry), "ret": xp/entry-1.0}

# 主臂: 共振事件
combo_results = []
for c, d in combo_events:
    r = outcome60(c, d)
    if r: combo_results.append({"code": c, **r})
combo_df = pd.DataFrame(combo_results)
log(f"主臂有效事件={len(combo_df)}")

# 对照臂: 仅回购(无共振) / 仅增持(无共振)
combo_codes = set(combo_df["code"]) if len(combo_df) else set()
rp_only = [(c,d) for c,d in rp_events if c not in overlap_codes or not any(abs(d-gd)<10000000 for gd in gg_codes.get(c,[]))]
gg_only = [(c,d) for c,d in gg_events if c not in overlap_codes or not any(abs(d-rd)<10000000 for rd in rp_codes.get(c,[]))]

def run_events(events, label):
    results = []
    seen_c = {}
    for c, d in sorted(events, key=lambda x:(x[0],x[1])):
        if c in seen_c and d - seen_c[c] < 10000000: continue
        seen_c[c] = d
        r = outcome60(c, d)
        if r: results.append({"code": c, **r})
    ev = pd.DataFrame(results)
    if len(ev)==0: return {"n":0}, ev
    return {"n": int(len(ev))}, ev

ctrl_rp, ctrl_rp_df = run_events(rp_only, "rp_only")
ctrl_gg, ctrl_gg_df = run_events(gg_only, "gg_only")

# ---------- 基准 ----------
need = sorted(set(int(x) for x in combo_df["d"])) if len(combo_df) else []
# 加对照的日期
if len(ctrl_rp_df): need += [int(x) for x in ctrl_rp_df["d"]]
if len(ctrl_gg_df): need += [int(x) for x in ctrl_gg_df["d"]]
need = sorted(set(need))
log(f"基准日期数={len(need)}")
base_by_d = {}
for T in need:
    i = pos_of.get(T)
    if i is None: continue
    ea = piv.iloc[i].to_numpy(dtype=float); wins = []
    for ci in range(len(codes_list)):
        e_val = ea[ci]
        if not np.isfinite(e_val) or e_val<=0 or e_val>30.0: continue
        cx = codes_list[ci]
        if (cx,T) in st_set: continue
        arr = close_by[cx]; e = i+1
        if e>=len(arr) or not np.isfinite(arr[e]) or arr[e]<=0 or arr[e]>30.0: continue
        ent = arr[e]; end = min(e+60, len(arr)-1)
        if end<=e: continue
        w = arr[e+1:end+1]; br = w<=ent*0.92
        xp = w[int(np.argmax(br))] if br.any() else arr[end]
        if np.isfinite(xp): wins.append(xp>ent)
    if wins: base_by_d[T] = float(np.mean(wins))
log("基准完成")

def stat_ev(ev):
    if len(ev)==0: return {"n":0}
    bw = ev["d"].map(lambda x: base_by_d.get(x))
    s = ev[bw.notna()]
    if len(s)==0: return {"n":len(ev)}
    out = {"n": int(len(s)),
           "win": round(float(s["win"].mean()),4),
           "base": round(float(bw[s.index].mean()),4),
           "exc_pp": round((float(s["win"].mean())-float(bw[s.index].mean()))*100,2),
           "net_pp": round((float(s["ret"].mean())-COST_RT)*100,2)}
    s2 = s.assign(year=s["d"]//10000)
    by = {}
    for y,g in s2.groupby("year"):
        if len(g)>=5:
            b = g["d"].map(lambda x: base_by_d.get(x)).mean()
            by[int(y)] = round((float(g["win"].mean())-float(b))*100,2)
    out["by_year"] = by
    out["n_pos_years"] = int(np.sum([v>0 for v in by.values()]))
    return out

main = stat_ev(combo_df)
ctrl = {"rp_only": stat_ev(ctrl_rp_df), "gg_only": stat_ev(ctrl_gg_df)}
log(f"主臂: n={main['n']} exc={main.get('exc_pp')}pp")

# ---------- 门禁 ----------
gates = {
    "G1(n>=100)": main.get("n",0)>=100,
    "G2_t60e(exc>=+2.0)": main.get("exc_pp",-99)>=2.0,
    "G3_net(>=+1.0)": main.get("net_pp",-99)>=1.0,
    "G4_vs_single(>min(1.06,3.09)=1.06)": main.get("exc_pp",-99)>1.06,
    "G5_years(>=4正)": main.get("n_pos_years",0)>=4,
}
verdict = "PASS_CANDIDATE" if all(gates.values()) else "FAIL"
out = {"meta":{"generated_at":"2026-10-25","order":"继续→复合治理共振E8","prereg":PREREG,"fingerprint":fp,"zero_trade_authority":True},
       "main":main,"control":ctrl,"gates":{k:bool(v) for k,v in gates.items()},"verdict":verdict}
with open(OUT,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=1,default=str)
log(f"完成→{OUT}")
print(json.dumps({"verdict":verdict,"gates":out["gates"],
                  "main":main,
                  "ctrl_summary":{k:{"n":v.get("n"),"exc":v.get("exc_pp"),"net":v.get("net_pp")} for k,v in ctrl.items()}},
                 ensure_ascii=False,indent=1,default=str))
