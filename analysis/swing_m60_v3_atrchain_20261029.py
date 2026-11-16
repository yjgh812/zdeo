# -*- coding: utf-8 -*-
"""swing_m60_v3_atrchain_20261029.py — v3: v2信号 + ATR风控链逐笔模拟(主人参数级文档落地)

风控链(主人文档参数):
  初始止损 = 入场价 − 2×ATR14(60分), 与结构位止损取更近者
  移动止损 = 浮盈≥3×ATR后 → 止损上移至入场价+1×ATR; 此后只上移不下移(棘轮)
  结构止损 = 60分放量(≥1.2×5根均量)跌破前一根60分低点 且 3根内不收回 → 立即离场
  时间止损 = 介入后15根60分K(≈3.75日)浮盈<1% → 离场
  到期离场 = 20交易日(≈80根60分K)未触发任一止损 → 到价离场
对照: 同信号固定hold20(v2口径)。输出: 收益/最大回撤/持仓时长分布。
"""
import os, sys, json, sqlite3
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
M60 = os.path.join(ROOT, "data", "m60_samples")
CUT = 20260722

con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
samples = json.load(open("data/m60_sample_picks.json", encoding="utf-8"))
codes = [s[0] for s in samples]
ph = ",".join("?"*len(codes))
drows = con.execute(f"select code,date,close from stock_daily_meta_qfq where code in ({ph}) and date>=20260401 order by code,date",
                    codes).fetchall()
con.close()
daily = defaultdict(list)
for code, d, c in drows:
    daily[code].append((int(d), float(c)))

def fwd20(code, d_signal):
    v = daily.get(code, [])
    ds = [d for d, _ in v]; dd = dict(v)
    if d_signal not in dd:
        prv = [x for x in ds if x <= d_signal]
        if not prv: return None
        d_signal = prv[-1]
    i = ds.index(d_signal)
    return dd[ds[i+20]]/dd[ds[i]] - 1 if i+20 < len(ds) else None

def sma(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a, 0, 0.0)); out[w-1:] = (cs[w:]-cs[:-w])/w
    return out

hold20_rets = []
chain_rets = []
chain_details = []   # 每笔: (code, dt, struct, ret, exit_reason, max_dd_in_trade, hold_bars)
hold_dds = []        # hold20期间最大回撤(逐bar)
for code, sd, ed, ret, days in samples:
    f = os.path.join(M60, f"{code}.json")
    if not os.path.exists(f): continue
    bars = json.load(open(f, encoding="utf-8"))["bars"]
    dt = [b["dt"] for b in bars]
    cut_pos = next((i for i, d in enumerate(dt) if d[:10].replace("-","")==str(CUT)), None)
    if cut_pos is None: continue
    cl = np.array([b["c"] for b in bars]); op = np.array([b["o"] for b in bars])
    lo = np.array([b["l"] for b in bars]); hi = np.array([b["h"] for b in bars])
    vv = np.array([b["v"] for b in bars])
    cut_close = cl[cut_pos]
    ma20, ma60, v5 = sma(cl,20), sma(cl,60), sma(cl,5)
    prev_v = np.full(len(cl), np.nan)
    for i in range(80, len(cl)):
        prev_v[i] = vv[i-80:i-20].mean()
    swing_hi = np.full(len(cl), np.nan)
    for i in range(2, len(cl)-2):
        if hi[i] >= hi[i-2] and hi[i] >= hi[i-1] and hi[i] >= hi[i+1] and hi[i] >= hi[i+2]:
            swing_hi[i] = hi[i]
    last_swing = np.full(len(cl), np.nan); cur = np.nan
    for i in range(len(cl)):
        if not np.isnan(swing_hi[i]): cur = swing_hi[i]
        last_swing[i] = cur
    low20 = np.array([lo[max(0,i-20):i+1].min() for i in range(len(cl))])
    # ATR14 (60分)
    tr = np.full(len(cl), np.nan)
    for i in range(1, len(cl)):
        tr[i] = max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
    atr14 = sma(np.nan_to_num(tr, nan=0.0), 14)

    b = fwd20(code, CUT)
    lo_b, hi_b = max(cut_pos-40, 100), min(cut_pos+120, len(cl)-85)   # 需≥85根余量供风控链
    fired_bars = []
    for i in range(lo_b, hi_b):
        if np.isnan(prev_v[i]) or np.isnan(ma20[i]) or np.isnan(ma60[i]) or np.isnan(atr14[i]) or atr14[i] <= 0:
            continue
        lp = low20[i]
        if lp > 0 and cl[i]/lp - 1 > 0.10: continue
        day_off = (i - cut_pos) / 4.0
        if day_off > 8 or cl[i] > cut_close*1.05:
            continue   # v2位置约束
        in_zone = min(ma20[i], ma60[i])*0.99 <= cl[i] <= max(ma20[i], ma60[i])*1.02
        vol_shrink = vv[i] <= prev_v[i]*0.5
        body_small = 0 < cl[i]-op[i] <= cl[i]*0.008
        lower_sh = (min(op[i],cl[i])-lo[i])/max(cl[i],1e-9) >= 0.004
        vol_break = vv[i] >= 1.5*(v5[i] if not np.isnan(v5[i]) else vv[max(0,i-5):i+1].mean())
        struct = None
        if in_zone and vol_shrink and (lower_sh or body_small):
            struct = "B1"
        elif (not np.isnan(last_swing[i-1])) and cl[i] > last_swing[i-1] and cl[i-1] <= last_swing[i-1] and vol_break:
            struct = "B2"
        touches = sum(1 for kk in range(max(0,i-20), i+1) if lo[kk] <= ma60[kk]*1.01)
        if struct is None and touches >= 2 and lo[i] >= lo[max(0,i-10):i].min()-1e-9 and vv[i] < prev_v[i]*0.6 and cl[i] > op[i]:
            struct = "B3"
        if struct:
            fired_bars.append((i, struct))
    # 去重: 同一票取最早信号(每票一笔, 避免重叠持仓)
    if fired_bars:
        fired_bars = [fired_bars[0]]
    for i, struct in fired_bars:
        entry = cl[i]
        r_hold = fwd20(code, int(dt[i][:10].replace("-","")))
        if r_hold is not None:
            hold20_rets.append(r_hold)
        # ATR风控链逐bar模拟
        atr0 = atr14[i]
        stop0 = entry - 2*atr0
        stop = stop0
        peak_gain = 0.0
        exit_r = None; reason = None; max_dd = 0.0
        run_peak = entry
        prev_low_ref = lo[i]
        breach_bar = None
        j_end = min(i + 80, len(cl)-1)
        for j in range(i+1, j_end+1):
            run_peak = max(run_peak, hi[j])
            gain = cl[j]/entry - 1
            # 移动止损上移
            if gain >= 3*atr0/entry:
                stop = max(stop, entry + 1*atr0)
            # 结构止损: 放量破前一根60分低点 且 3根内不收回
            if lo[j] < lo[j-1] and vv[j] >= 1.2*(v5[j] if not np.isnan(v5[j]) else vv[max(0,j-5):j+1].mean()):
                if breach_bar is None:
                    breach_bar = j
                if j - breach_bar >= 3 and cl[j] < lo[j-1]:
                    exit_r = cl[j]/entry - 1; reason = "结构止损"; break
            else:
                if breach_bar is not None and cl[j] >= lo[j-1]:
                    breach_bar = None
            # 时间止损: 15根后浮盈<1%
            if j - i >= 15 and gain < 0.01:
                exit_r = cl[j]/entry - 1; reason = "时间止损"; break
            # ATR止损(含移动)
            if lo[j] <= stop:
                exit_r = stop/entry - 1; reason = "移动/初始止损"; break
            # 到期
            if j == j_end:
                exit_r = cl[j]/entry - 1; reason = "到期20日"; break
        if exit_r is not None:
            chain_rets.append(exit_r)
            hold_dds.append((run_peak - min(lo[i+1:j+1]))/run_peak if run_peak > 0 else 0)
            chain_details.append({"code": code, "dt": dt[i], "struct": struct,
                                   "ret": round(exit_r*100,2), "reason": reason,
                                   "hold_bars": j - i})

def st(a):
    a = np.array(a)
    if not len(a): return {"n": 0}
    dd = round(float(np.mean(a < -0.02))*100, 1)
    return {"n": len(a), "mean": round(float(a.mean())*100, 2),
            "win": round(float((a>0).mean())*100, 1),
            "worst": round(float(a.min())*100, 2),
            "loss_gt2pct_pct": dd}

R = {"built_at": "20261029",
     "hold20": st(hold20_rets),
     "atr_chain": st(chain_rets),
     "chain_exit_mix": {},
     "n_trades": len(chain_rets)}
for d in chain_details:
    R["chain_exit_mix"][d["reason"]] = R["chain_exit_mix"].get(d["reason"], 0) + 1
R["worst_hold_vs_chain"] = {"hold_worst": st(hold20_rets).get("worst"),
                             "chain_worst": st(chain_rets).get("worst")}
R["details"] = chain_details
out = os.path.join(ROOT, "data", "swing_m60_v3_atrchain_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("=== v2信号: hold20 vs ATR风控链 ===")
print(f"固定hold20 : {R['hold20']}")
print(f"ATR风控链  : {R['atr_chain']}")
print(f"离场构成: {R['chain_exit_mix']}")
print(f"最差单笔: hold={R['worst_hold_vs_chain']['hold_worst']}% vs chain={R['worst_hold_vs_chain']['chain_worst']}%")
print(f"落盘: {out}")
