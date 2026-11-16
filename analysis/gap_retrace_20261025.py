# -*- coding: utf-8 -*-
"""向上跳空缺口回踩(不回补)检验 (事件流水线E9, 20261025)。
主臂=跳空≥2%+10日内回踩缺口区但不回补+回踩日次一收盘入场。
只读qfq+st_history, 零交易权。输出: analysis/gap_retrace_20261025_result.json
"""
import hashlib, json, sqlite3, sys, time
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
PREREG = r"D:\zcodesj\data\PREREG_GAP_RETRACE_20261025.json"
OUT = r"D:\zcodesj\analysis\gap_retrace_20261025_result.json"
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

# ---------- 价格底座(含open) ----------
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query("SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq ORDER BY code,date", con); con.close()
df["code"] = df["code"].map(norm_code)
df = df[df["code"].str[0].isin(["6","0","3"]) & ~df["code"].str.startswith(("688","689"))]
df = df[(df["close"]>0)&(df["open"]>0)&(df["volume"]>0)].copy()
df["dl"] = df.groupby("code").cumcount()+1; df = df[df["dl"]>60].reset_index(drop=True)
df["d"] = df["date"].astype("int64")
log(f"qfq 行={len(df)} 股={df['code'].nunique()}")

st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_r = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con); st_con.close()
st_set = set(zip(st_r["code"].map(norm_code), st_r["date"].astype("int64")))

# ---------- 状态机扫描(逐股) ----------
events_main, events_strict, events_noretrace, events_filled = [], [], [], []
last_c = {}
grouped = list(df.groupby("code", sort=False))
log(f"扫描开始 股数={len(grouped)}")

for gi, (code, g) in enumerate(grouped):
    gi_rows = np.sort(g.index.to_numpy())
    n = len(gi_rows)
    if n < 80: continue
    c = g["close"].to_numpy(dtype=float)
    o = g["open"].to_numpy(dtype=float)
    h = g["high"].to_numpy(dtype=float)
    l = g["low"].to_numpy(dtype=float)
    dts = g["d"].to_numpy(dtype="int64")
    i = 30
    while i < n - 12:
        last = last_c.get(code)
        if last is not None and i - last <= 30:
            i += 1; continue
        # 跳空条件: open[i] > close[i-1]*1.02, 阳线, 非一字
        if not (o[i] > c[i-1]*1.02 and c[i] > o[i] and h[i] > l[i]):
            i += 1; continue
        if dts[i] < START or dts[i] > END: i += 1; continue
        if (code, int(dts[i])) in st_set: i += 1; continue
        gap_bottom = c[i-1]  # 缺口下沿=前收
        gap_top = o[i]       # 缺口上沿=开盘
        gap_pct = (o[i]/c[i-1]-1)*100
        d0 = i
        # 扫描D0后[1,10]找回踩日
        retrace = None; filled = False
        for j in range(d0+1, min(d0+11, n)):
            if c[j] < gap_bottom:
                filled = True; break
            if l[j] <= gap_top:  # 回到缺口区
                retrace = j; break
        last_c[code] = d0
        if filled:
            events_filled.append({"code": code, "d": int(dts[d0]), "gap_pct": round(gap_pct,2)})
            i = d0 + 1; continue
        if retrace is None:
            # 无回踩=跳空日次日直接入场(PEAD同型对照)
            if d0+1 < n and dts[d0+1] <= END:
                events_noretrace.append({"code": code, "d": int(dts[d0+1]), "gap_pct": round(gap_pct,2), "d0": int(dts[d0])})
            i = d0 + 1; continue
        # 回踩日=信号日, 入场=次一bar收盘
        entry_i = retrace + 1
        if entry_i >= n or dts[entry_i] > END: i = d0+1; continue
        ev = {"code": code, "d": int(dts[entry_i]), "d0": int(dts[d0]),
              "gap_pct": round(gap_pct,2), "retrace_day": int(dts[retrace])}
        # t20e/t60e
        for tag, (hold, sp) in STOPS.items():
            end = min(entry_i+hold, n-1)
            if end <= entry_i: ev[tag] = None; continue
            entry_px = c[entry_i]
            w = c[entry_i+1:end+1]
            broke = w <= entry_px*sp
            xp = w[int(np.argmax(broke))] if broke.any() else c[end]
            if np.isfinite(xp): ev[tag] = (bool(xp>entry_px), xp/entry_px-1.0)
            else: ev[tag] = None
        events_main.append(ev)
        if gap_pct >= 3.0:
            events_strict.append(ev)
        i = retrace + 1
    if (gi+1) % 800 == 0:
        log(f"  ... {gi+1}/{len(grouped)}")
log(f"主臂={len(events_main)} 强缺口={len(events_strict)} 无回踩={len(events_noretrace)} 回补={len(events_filled)}")

# ---------- 基准 ----------
piv = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
d_sorted = piv.index.to_numpy(dtype="int64")
codes_list = list(piv.columns)
close_by_piv = {c_: piv[c_].to_numpy(dtype=float) for c_ in codes_list}
pos_of = {int(x): i for i,x in enumerate(d_sorted)}

main_df = pd.DataFrame(events_main)
if len(main_df) > 0:
    main_df = main_df[main_df["t20e"].notna()]
need = sorted(set(int(x) for x in main_df["d"])) if len(main_df) else []
# 加对照臂日期
nr_df = pd.DataFrame(events_noretrace)
if len(nr_df): need += [int(x) for x in nr_df["d"]]
need = sorted(set(need))
log(f"基准日期数={len(need)}")

base_by_d = {}
for T in need:
    i = pos_of.get(T)
    if i is None: continue
    ea = piv.iloc[i].to_numpy(dtype=float); w20 = []
    for ci in range(len(codes_list)):
        e_val = ea[ci]
        if not np.isfinite(e_val) or e_val<=0 or e_val>30.0: continue
        cx = codes_list[ci]
        if (cx,T) in st_set: continue
        arr = close_by_piv[cx]; e = i+1
        if e>=len(arr) or not np.isfinite(arr[e]) or arr[e]<=0 or arr[e]>30.0: continue
        ent = arr[e]; end = min(e+20, len(arr)-1)
        if end<=e: continue
        w = arr[e+1:end+1]; br = w<=ent*0.92
        xp = w[int(np.argmax(br))] if br.any() else arr[end]
        if np.isfinite(xp): w20.append(xp>ent)
    if w20: base_by_d[T] = float(np.mean(w20))
log("基准完成")

def stat_ev(ev_df):
    if len(ev_df)==0: return {"n":0}
    bw = ev_df["d"].map(lambda x: base_by_d.get(x))
    s = ev_df[bw.notna()]
    if len(s)==0: return {"n":len(ev_df)}
    t20 = s["t20e"].dropna()
    out = {"n": int(len(s)),
           "win": round(float(t20.apply(lambda x:x[0]).mean()),4) if len(t20)>0 else None,
           "base": round(float(bw[s.index].mean()),4)}
    if out["win"] is not None:
        out["exc_pp"] = round((out["win"]-out["base"])*100,2)
        out["net_pp"] = round((float(t20.apply(lambda x:x[1]).mean())-COST_RT)*100,2)
    s2 = s.assign(year=s["d"]//10000)
    by = {}
    for y,g in s2.groupby("year"):
        if len(g)>=30:
            b = g["d"].map(lambda x: base_by_d.get(x)).mean()
            by[int(y)] = round((float(g["t20e"].dropna().apply(lambda x:x[0]).mean())-float(b))*100,2)
    out["by_year"] = by
    out["n_pos_years"] = int(np.sum([v>0 for v in by.values()]))
    return out

res_main = stat_ev(main_df)
res_strict = stat_ev(pd.DataFrame(events_strict))
# 无回踩对照(跳空日次日=PEAD式)
nr_valid = []
for _, r in nr_df.iterrows():
    if r["code"] not in close_by_piv: continue
    arr = close_by_piv[r["code"]]
    i = int(np.searchsorted(d_sorted, int(r["d"])))
    if i>=len(d_sorted) or (r["code"],int(d_sorted[i])) in st_set: continue
    e = i+1
    if e>=len(arr) or not np.isfinite(arr[e]) or arr[e]<=0 or arr[e]>30: continue
    ent = arr[e]; end = min(e+20, len(arr)-1)
    if end<=e: continue
    w = arr[e+1:end+1]; br = w<=ent*0.92
    xp = w[int(np.argmax(br))] if br.any() else arr[end]
    if np.isfinite(xp):
        nr_valid.append({"code":r["code"],"d":int(d_sorted[i]),"t20e":(bool(xp>ent),xp/ent-1.0)})
nr_df_v = pd.DataFrame(nr_valid)
res_noretrace = stat_ev(nr_df_v) if len(nr_df_v)>0 else {"n":0}

gates = {
    "G1(n>=500)": res_main.get("n",0)>=500,
    "G2_t20e(exc>=+2.0)": res_main.get("exc_pp",-99)>=2.0,
    "G3_net(>=+1.0)": res_main.get("net_pp",-99)>=1.0,
    "G4_years(>=6正)": res_main.get("n_pos_years",0)>=6,
    "G5_t60e(>=+1.5)": res_main.get("t60e_exc",-99) if "t60e_exc" in res_main else -99,
}
# t60e: 需要额外统计
if len(main_df)>0:
    t60_valid = main_df[main_df["t60e"].notna()]
    if len(t60_valid)>0:
        t60_wins = t60_valid["t60e"].apply(lambda x:x[0]).mean()
        gates["G5_t60e(>=+1.5)"] = (t60_wins - res_main.get("base",0.4))*100 >= 1.5
verdict = "PASS_CANDIDATE" if all(v if isinstance(v,bool) else False for v in gates.values()) else "FAIL"

out = {"meta":{"generated_at":"2026-10-25","order":"继续→缺口回踩E9","prereg":PREREG,"fingerprint":fp,"zero_trade_authority":True},
       "main":res_main,"control":{"strict":res_strict,"noretrace":res_noretrace},
       "gates":{k:(bool(v) if isinstance(v,(bool,np.bool_)) else v) for k,v in gates.items()},"verdict":verdict,
       "n_filled":len(events_filled)}
with open(OUT,"w",encoding="utf-8") as f: json.dump(out,f,ensure_ascii=False,indent=1,default=str)
log(f"完成→{OUT}")
print(json.dumps({"verdict":verdict,"gates":out["gates"],
                  "main":res_main,"ctrl_strict":res_strict,"ctrl_noretrace":res_noretrace,
                  "n_filled":len(events_filled)},ensure_ascii=False,indent=1,default=str))
