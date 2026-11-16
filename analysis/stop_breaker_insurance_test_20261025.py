# -*- coding: utf-8 -*-
"""ATR止损vs固定8% + 熔断 + 回撤保险 三主张测试 20261025 — 探索性, 零交易权, 只读qfq。
主张1(止损): ATR倍数止损(1.5/2/2.5xATR14, 入场日冻结)优于固定8%(生产口径)?
主张2(熔断): 月度批次连续3负→跳过下批, 能否改善累计?
主张3(保险): 组合回撤15%→下批减半, 20%→降至30%, 新高恢复, 能否改善净值/回撤?
口径: 信号=低位横盘A格(同前); 入场=次日开盘; 窗口20日; 批次=每月抽12只(inv_vol定权, 现行默认);
      ATR14=信号日前移1日; 止损=首日收盘≤entry-k*ATR即出场(全close口径)。
输出: analysis/stop_breaker_insurance_test_20261025_result.json + stdout。
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/stop_breaker_insurance_test_20261025_result.json"
COOLDOWN = 10
PICKS = 12
WIN = 20200101


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
    "WHERE date>=20190101 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
scon = sqlite3.connect(STDB, uri=True)
st = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", scon)
scon.close()
st_set = set(zip(st["code"].map(norm), st["date"].astype("int64")))
print(f"[1/4] qfq行={len(df)} ({time.time()-t0:.0f}s)")

rows = []
for code, gg in df.groupby("code6", sort=False):
    gg = gg.sort_values("d")
    o = gg["open"].to_numpy(float)
    h = gg["high"].to_numpy(float)
    l = gg["low"].to_numpy(float)
    c = gg["close"].to_numpy(float)
    d = gg["d"].to_numpy("int64")
    n = len(c)
    if n < 280:
        continue
    days = np.arange(1, n + 1)
    s = pd.Series(c)
    mn = s.rolling(250, min_periods=120).min().to_numpy()
    mx = s.rolling(250, min_periods=120).max().to_numpy()
    pos250 = np.where(mx > mn, (c - mn) / (mx - mn), np.nan)
    chg5 = s.pct_change(5).to_numpy() * 100
    is_A = (pos250 < 30) & (np.abs(chg5) < 3) & (c <= 30) & (days > 60)
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).rolling(14, min_periods=14).mean().shift(1).to_numpy()
    ret1 = s.pct_change().to_numpy()
    vol20 = pd.Series(ret1).rolling(20, min_periods=20).std().shift(1).to_numpy() * np.sqrt(250)

    last = -99
    for i in np.nonzero(is_A)[0]:
        if i - last <= COOLDOWN:
            continue
        last = i
        e = i + 1
        if e + 19 >= n or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if (code, int(d[i])) in st_set:
            continue
        entry = o[e]
        if entry <= 0 or not np.isfinite(vol20[i]) or vol20[i] <= 0:
            continue
        a = float(atr[i])
        rec = {"ym": int(d[i]) // 100, "vol": float(vol20[i]), "atr_pct": a / entry}
        # 止损变体: 固定8% vs k*ATR (首触收盘出场, 否则20日收盘)
        for tag, dist in (("s8", entry * 0.08), ("a15", 1.5 * a), ("a20", 2.0 * a), ("a25", 2.5 * a)):
            ex, hit = c[e + 19], 0
            for t in range(e, e + 20):
                if c[t] <= entry - dist:
                    ex, hit = c[t], 1
                    break
            rec[tag] = ex / entry - 1.0
            rec[tag + "_hit"] = hit
        rows.append(rec)
print(f"[2/4] 事件={len(rows)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
# ---- 主张1: 止损变体逐仓对比 ----
stop_cmp = {}
for tag, label in (("s8", "固定8%(生产)"), ("a15", "1.5xATR14"), ("a20", "2.0xATR14"), ("a25", "2.5xATR14")):
    x = ev[tag]
    stop_cmp[tag] = {"label": label, "mean": round(float(x.mean()), 5), "median": round(float(x.median()), 5),
                     "win": round(float((x > 0).mean()), 4), "p5": round(float(x.quantile(0.05)), 4),
                     "stop_hit_rate": round(float(ev[tag + "_hit"].mean()), 4)}

# ---- 批次序列(inv_vol定权, 每月12只) ----
batches = []
for ym, sub in ev[ev["ym"] >= WIN // 100].groupby("ym"):
    if len(sub) < 6:
        continue
    sub = sub.sample(n=min(PICKS, len(sub)), random_state=42)
    rets = sub["s8"].to_numpy()
    vols = sub["vol"].to_numpy()
    iv = 1.0 / vols
    w = iv / iv.sum()
    batches.append({"ym": int(ym), "ret": float((rets * w).sum())})
b = pd.DataFrame(batches).sort_values("ym").reset_index(drop=True)
print(f"[3/4] 月度批次={len(b)} ({time.time()-t0:.0f}s)")


def run_series(rets, mode):
    eq, peak, mdd, n_used = 1.0, 1.0, 0.0, 0
    lose_streak = 0
    for r in rets:
        if mode == "breaker" and lose_streak >= 3:
            lose_streak = 0 if r >= 0 else lose_streak  # 跳过本批, 连败计数重置
            continue
        dd = 1.0 - eq / peak
        scale = 1.0
        if mode == "insurance":
            if dd >= 0.20:
                scale = 0.3
            elif dd >= 0.15:
                scale = 0.5
        eq *= (1.0 + scale * r)
        peak = max(peak, eq)
        mdd = max(mdd, 1.0 - eq / peak)
        n_used += 1
        lose_streak = 0 if r >= 0 else lose_streak + 1
    return {"final_eq": round(eq, 4), "mdd": round(mdd, 4), "n_batches_used": n_used}


rets = b["ret"].tolist()
res = {"generated": "2026-10-25", "exploratory": True, "n_events": len(ev), "n_batches": len(b),
       "claim1_stop_variants": stop_cmp,
       "atr_pct_dist": {"p25": round(float(ev["atr_pct"].quantile(0.25)), 4),
                        "p50": round(float(ev["atr_pct"].median()), 4),
                        "p75": round(float(ev["atr_pct"].quantile(0.75)), 4)},
       "claim2_3": {"base_all": run_series(rets, "base"),
                    "breaker_3loss_skip": run_series(rets, "breaker"),
                    "insurance_dd15_20": run_series(rets, "insurance")},
       "batch_stats": {"mean": round(float(b["ret"].mean()), 5), "neg_rate": round(float((b["ret"] < 0).mean()), 4)}}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[4/4] 完成 → {OUT}\n")
print("== 主张1: 止损距离变体(逐仓, 20日窗) ==")
for tag, s in stop_cmp.items():
    print(f"  {s['label']}: 均值={s['mean']:+.2%} 中位={s['median']:+.2%} 胜率={s['win']:.1%} p5={s['p5']:+.1%} 止损触发率={s['stop_hit_rate']:.1%}")
print(f"  ATR占入场价比: p25={res['atr_pct_dist']['p25']:.1%} 中位={res['atr_pct_dist']['p50']:.1%} p75={res['atr_pct_dist']['p75']:.1%} (1x)")
print("== 主张2/3: 批次序列(月, inv_vol) ==")
print(f"  基准全跑: {res['claim2_3']['base_all']}")
print(f"  熔断(连3负跳批): {res['claim2_3']['breaker_3loss_skip']}")
print(f"  保险(DD15%→半仓/20%→30%): {res['claim2_3']['insurance_dd15_20']}")
print(f"  批均={res['batch_stats']['mean']:+.2%} 负批率={res['batch_stats']['neg_rate']:.0%}")
