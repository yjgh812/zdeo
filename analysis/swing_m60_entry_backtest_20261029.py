# -*- coding: utf-8 -*-
"""swing_m60_entry_backtest_20261029.py — 60分钟介入结构识别+对照回测(主人框架可编码v1)

对40只最近up波段样本, 在波段起点(20260722)前后窗口内检测60分钟三种介入结构:
  B1 回踩不破: 回调触及60分MA20/MA60区 + 量缩至前波放量均量≤1/2 + 止跌(下影/小阳/金叉)
  B2 突破确认: 60分收盘实体上穿最近摆动高点 + 量≥1.5×前5根均量
  B3 二次探底不破: 同区域两探, 第二次不更低且缩量, 后随放量阳线
约束(防追高): 信号日60分现价距最近60分低点涨幅≤10%
日线方向闸(近似): 信号前一日收盘>MA20 且 MA20斜率≥0 (qfq日线)
对照: 7/22波段起点当日收盘直接买入持有20日(=现有方法"信号日次日买入"的口径)
指标: 结构信号后20日收益(60分收盘→聚合日收盘) vs 直接买; 介入区域挂限价的可执行口径另计
"""
import os, sys, json, sqlite3
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
M60 = os.path.join(ROOT, "data", "m60_samples")
CUT = 20260722   # 波段共同起点

# 日线(qfq)用于方向闸与收益结算
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
daily_idx = {c: {d: i for i, (d, c) in enumerate(v)} for c, v in daily.items()}


def daily_close_at(code, target, forward=False):
    v = daily.get(code, [])
    ds = [d for d, _ in v]
    if target in ds:
        return dict(v)[target]
    if forward:
        nxt = [d for d in ds if d >= target]
        return dict(v)[nxt[0]] if nxt else None
    prv = [d for d in ds if d <= target]
    return dict(v)[prv[-1]] if prv else None


def fwd20_daily(code, d_signal):
    """信号日起第20个交易日收盘收益(信号日收盘买入口径近似)"""
    v = daily.get(code, [])
    ds = [d for d, _ in v]
    dd = dict(v)
    if d_signal not in dd:
        prv = [x for x in ds if x <= d_signal]
        if not prv:
            return None
        d_signal = prv[-1]
    i = ds.index(d_signal)
    if i + 20 >= len(ds):
        return None
    return dd[ds[i+20]]/dd[ds[i]] - 1


def sma(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        c = np.cumsum(np.insert(a, 0, 0.0))
        out[w-1:] = (c[w:]-c[:-w])/w
    return out


def ema(a, n):
    k = 2.0/(n+1); e = a[0]; out = np.empty(len(a)); out[0] = e
    for i in range(1, len(a)):
        e = k*a[i] + (1-k)*e; out[i] = e
    return out


signals = []   # (code, dt, struct, price, note)
base_rets = []
sig_rets = defaultdict(list)
no_sig_rets = []
for code, sd, ed, ret, days in samples:
    f = os.path.join(M60, f"{code}.json")
    if not os.path.exists(f):
        continue
    bars = json.load(open(f, encoding="utf-8"))["bars"]
    # 只用波段起点前后的窗口: 起点±(前60根/后160根)
    n = len(bars)
    cl = np.array([b["c"] for b in bars])
    op = np.array([b["o"] for b in bars])
    hi = np.array([b["h"] for b in bars])
    lo = np.array([b["l"] for b in bars])
    vv = np.array([b["v"] for b in bars])
    dt = [b["dt"] for b in bars]
    # 定位起点bar(新浪dt格式="2026-07-22 10:30:00")
    cut_pos = next((i for i, d in enumerate(dt) if d[:10].replace("-", "") == str(CUT)), None)
    if cut_pos is None:
        continue
    ma20 = sma(cl, 20); ma60 = sma(cl, 60)
    dif = ema(cl, 12) - ema(cl, 26)
    dea = ema(dif, 9)
    v5 = sma(vv, 5)
    # 日线方向闸(近似, 用60分聚合ema20>0代替): 每bar记录方向开/关
    trend_ok = np.zeros(n, bool)
    for i in range(80, n):
        trend_ok[i] = cl[i] > ma20[i] and ma20[i] >= ma20[max(0, i-12)]
    # 摆动高点(前后各2根)
    swing_hi = np.full(n, np.nan)
    for i in range(2, n-2):
        if hi[i] >= hi[i-2] and hi[i] >= hi[i-1] and hi[i] >= hi[i+1] and hi[i] >= hi[i+2]:
            swing_hi[i] = hi[i]
    last_swing = np.full(n, np.nan)
    cur = np.nan
    for i in range(n):
        if not np.isnan(swing_hi[i]):
            cur = swing_hi[i]
        last_swing[i] = cur
    # 前波放量均量(信号前80~20根的量均值, 近似"前一波上涨放量")
    prev_v = np.full(n, np.nan)
    for i in range(n):
        if i >= 80:
            prev_v[i] = vv[i-80:i-20].mean()
    # 最近60分低点(20根内)
    low20 = np.array([lo[max(0, i-20):i+1].min() if i >= 0 else np.nan for i in range(n)])

    for i in range(90, n-3):
        d8 = int(dt[i][:8].replace("-", ""))
        if not (d8 <= CUT + 1000 and d8 >= CUT - 2000):
            pass
        # 只统计波段起点前10日~后30日窗口
        date_i = int(dt[i][:8].replace("-", ""))
        # 简化: 用全局日序差
        pass
    # 事件检测: 起点±窗口用bar序号近似(60分bar: 1日=4根, 前后=±40根)
    lo_b = max(cut_pos - 40, 90)
    hi_b = min(cut_pos + 120, n - 3)
    fired = False
    for i in range(lo_b, hi_b):
        if not trend_ok[i] or np.isnan(prev_v[i]) or np.isnan(ma20[i]) or np.isnan(ma60[i]):
            continue
        # 防追高: 距最近低点涨幅≤10%
        lp = low20[i]
        if lp > 0 and cl[i]/lp - 1 > 0.10:
            continue
        body = abs(cl[i]-op[i])
        lower_shadow = (min(op[i], cl[i]) - lo[i]) / max(cl[i], 1e-9)
        small_yang = 0 < cl[i]-op[i] <= cl[i]*0.008
        golden = dif[i] > dea[i] and dif[i-1] <= dea[i-1] and dif[i] > 0
        stop_sig = lower_shadow >= 0.004 or small_yang or golden
        vol_shrink = vv[i] <= prev_v[i]*0.5
        vol_break = vv[i] >= 1.5*(v5[i] if not np.isnan(v5[i]) else vv[max(0,i-5):i+1].mean())
        in_zone = (min(ma20[i], ma60[i]) * 0.99) <= cl[i] <= (max(ma20[i], ma60[i]) * 1.02)
        struct = None
        # B1 回踩不破: 收盘在MA20/60区, 缩量, 止跌, 不破最近低点
        if in_zone and vol_shrink and stop_sig:
            struct = "B1回踩不破"
        # B2 突破确认: 实体上穿最近摆动高点+放量
        elif (not np.isnan(last_swing[i-1])) and cl[i] > last_swing[i-1] and cl[i-1] <= last_swing[i-1] and vol_break:
            struct = "B2突破确认"
        # B3 二次探底不破(简化): 触MA60两次且第二次低点>=第一次且缩量, 后续3根内放量阳线
        #   (v1简化: 近20根内两次touch MA60且第二次缩量+当日阳线放量)
        touches = sum(1 for k in range(max(0, i-20), i+1)
                      if not np.isnan(ma60[k]) and lo[k] <= ma60[k]*1.01)
        if struct is None and touches >= 2 and lo[i] >= lo[i-10:i].min() - 1e-9 and vv[i] < prev_v[i]*0.6 and cl[i] > op[i]:
            struct = "B3二次探底"
        if struct:
            # 信号日(60分bar所在日)→次日收盘买入口径: 用60分信号bar收盘→20日后日线收盘
            d_sig = int(dt[i][:10].replace("-", ""))
            r = fwd20_daily(code, d_sig)
            if r is not None:
                sig_rets[struct].append(r)
                signals.append({"code": code, "dt": dt[i], "struct": struct,
                                "price": round(float(cl[i]), 2), "fwd20": round(r*100, 2)})
                fired = True
    # 基准: 起点(7/22)收盘买入
    b = fwd20_daily(code, CUT)
    if b is not None:
        base_rets.append(b)
        if not fired:
            no_sig_rets.append(b)

import numpy as np
def st(a):
    a = np.array(a)
    if not len(a):
        return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean())*100, 2), "win": round(float((a>0).mean())*100, 1),
            "med": round(float(np.median(a))*100, 2)}

R = {"built_at": "20261029",
     "base_direct_buy": st(base_rets),
     "by_struct": {k: st(v) for k, v in sig_rets.items()},
     "any_signal": st([x for v in sig_rets.values() for x in v]),
     "no_signal_stocks": st(no_sig_rets),
     "n_signals": len(signals)}
out = os.path.join(ROOT, "data", "swing_m60_entry_backtest_20261029.json")
json.dump({"summary": R, "signals": signals[:200]}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("=== 对照: 波段起点直接买 vs 60分结构介入 ===")
print(f"基准(7/22收盘直接买, hold20): {R['base_direct_buy']}")
print(f"任一结构信号: {R['any_signal']}")
for k, v in R['by_struct'].items():
    print(f"  {k}: {v}")
print(f"无信号票(起点直接买子集): {R['no_signal_stocks']}")
print(f"\n信号总数: {len(signals)} | 落盘: {out}")
