# -*- coding: utf-8 -*-
"""apool_tradability_review_vec.py — A池可交易性复核 v4·全速版 (2026-10-11「太慢了全速干」令; 研究件零交易权)
口径与官方引擎gate_c_interact_20261003复刻+四增量 逐位一致(同v2/v3)。v4=全程零逐日循环:
  加载=pd.read_sql(C速度) → 长数组(numpy列); MA类=cumsum技巧O(N)纯向量化(每股边界掩码);
  滚动极值(pos250 min/max + big60)=仅对每股调pandas C实现(5300×3次微调用, 秒级);
  平移(prev/ret20/ma20_p5)=行号回退花式索引(每股边界掩码);
  事件退出=20日窗花式索引矩阵argmax; 5日去重=仅稀疏候选小循环。
面板2021-07起(pos250预热250根+事件2023起; 门比率序列起2022H2对2023+事件日无影响)。
输出: data/apool_tradability_review_20261011_vec.json。断言: n下限/分年覆盖。
"""
import json
import sqlite3
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"  # 唯一真源(2026-10-13 切wb, SOP一百三十二; 首跑数字出自wb重建基线)
OUT = r"D:\zcodesj\data\apool_tradability_review_20261011_vec.json"
START = "20210701"
COST_BPS = (0, 30, 50)
GATEA_GRID = (0.65, 0.70, 0.75)
GATEC_GRID = (0.45, 0.50, 0.55)
T0 = time.time()


def phase(msg):
    print(f"[{time.time()-T0:6.0f}s] {msg}", flush=True)


# ---- 1. 加载(fetchall C速度无排序; ORDER BY=sqlite临时排序540万行病态慢, 改numpy lexsort秒级) ----
con = sqlite3.connect("file:" + QFQ_DB.replace("\\", "/") + "?mode=ro", uri=True)
cur = con.execute(
    "SELECT code, date, open, high, low, close, volume, amount FROM stock_daily_meta_qfq "
    "WHERE date>=? AND (substr(code,1,1) IN ('0','3','6'))",
    (START,))
rows = cur.fetchall()
con.close()
phase(f"fetchall {len(rows)}行 {time.time()-T0:.0f}s")
df = pd.DataFrame(rows, columns=["code", "date", "open", "high", "low", "close", "volume", "amount"])
del rows
codes_u = df["code"].to_numpy()
dates_u = df["date"].to_numpy()
order = np.lexsort((dates_u, codes_u))  # 主键code次键date, 稳定排序秒级
df = df.iloc[order].reset_index(drop=True)
del order
phase(f"lexsort完成 {len(df)}行")
cmask = ~df["code"].str[:3].isin(("688", "689")) & ~df["code"].str[0].isin(("4", "8"))
df = df[cmask].reset_index(drop=True)
phase(f"剔板后 {len(df)}行")

close = df["close"].to_numpy(float)
opn = df["open"].to_numpy(float)
low_ = df["low"].to_numpy(float)
vol = df["volume"].to_numpy(float)
amt = pd.to_numeric(df["amount"], errors="coerce").to_numpy(float)
dates = df["date"].to_numpy()
codes = df["code"].to_numpy()
N = len(df)
del df  # 内存瘦身(2026-10-12): 系统裁剪环境下大DF常驻=页面颠簸根源, 列提取后即弃
import gc
gc.collect()

codes_id, uniq_codes = pd.factorize(codes, sort=True)
n_stocks = len(uniq_codes)
starts = np.searchsorted(codes, uniq_codes)
counts = np.diff(np.append(starts, N))
tbar = np.arange(N) - starts[codes_id]
row_of = np.arange(N)
phase(f"{n_stocks}只 | bar数中位{int(np.median(counts))}")


def shifted(x, k):
    """每股内回退k行(越界=NaN)。"""
    idx = row_of - k
    ok = idx >= starts[codes_id]
    out = np.full(N, np.nan)
    out[ok] = x[idx[ok]]
    return out


def rolling_ma(x, w):
    """cumsum技巧O(N)移动均值, 每股边界掩码。"""
    cs = np.concatenate([[0.0], np.cumsum(x)])
    idx0 = row_of - w + 1
    ok = (idx0 >= starts[codes_id]) & (row_of + 1 <= N)
    out = np.full(N, np.nan)
    out[ok] = (cs[row_of[ok] + 1] - cs[idx0[ok]]) / w
    return out


# ---- 2. 指标 ----
ma5 = rolling_ma(close, 5)
ma10 = rolling_ma(close, 10)
ma20 = rolling_ma(close, 20)
ma60 = rolling_ma(close, 60)
ma20_p5 = shifted(ma20, 5)
prev_c = shifted(close, 1)
vma5 = rolling_ma(vol, 5)
vma20 = rolling_ma(vol, 20)
vma120 = rolling_ma(vol, 120)
ret20 = close / shifted(close, 20) - 1.0
chg = close / prev_c - 1.0
phase("MA/平移完成")

pos250 = np.full(N, np.nan)
big60 = np.zeros(N, dtype=bool)
big = ((vma20 > 0) & (vol >= 2.0 * vma20) & (chg >= 0.03)).astype(float)
# 滚动极值=scipy filter1d (O(n)C实现; pandas逐股rolling开销实测致小时级, 弃用)
# origin自校准fixture: 对首只>=250bar股与pandas rolling逐位对账, 对不上即拒绝运行(防even-size错位)
from scipy.ndimage import maximum_filter1d, minimum_filter1d

a0 = int(starts[0]); b0 = a0 + min(int(counts[0]), 2000)
if b0 - a0 >= 250:
    ref_min = pd.Series(close[a0:b0]).rolling(250).min().to_numpy()[249]
    ref_hi = pd.Series(close[a0:b0]).rolling(250).max().to_numpy()[249]
    ORIGIN250 = None
    for org in (250 // 2 - 1, -(250 // 2), 250 // 2, -(250 // 2) + 1, 0):
        if minimum_filter1d(close[a0:b0], 250, origin=org, mode="nearest")[249] == ref_min \
                and maximum_filter1d(close[a0:b0], 250, origin=org, mode="nearest")[249] == ref_hi:
            ORIGIN250 = org
            break
    assert ORIGIN250 is not None, "scipy origin自校准失败(250与pandas rolling不对齐)"
    ORIGIN60 = None
    for org in (60 // 2 - 1, -(60 // 2), 60 // 2, -(60 // 2) + 1, 0):
        if maximum_filter1d(big[a0:b0], 60, origin=org, mode="nearest")[59] == pd.Series(big[a0:b0]).rolling(60).max().to_numpy()[59]:
            ORIGIN60 = org
            break
    assert ORIGIN60 is not None, "scipy origin自校准失败(60与pandas rolling不对齐)"
    phase(f"scipy origin自校准PASS origin250={ORIGIN250} origin60={ORIGIN60} (与pandas rolling逐位对账)")
for s in range(n_stocks):
    a, b = starts[s], starts[s] + counts[s]
    seg_close = close[a:b]
    lo = minimum_filter1d(seg_close, 250, origin=ORIGIN250, mode="nearest")
    hi = maximum_filter1d(seg_close, 250, origin=ORIGIN250, mode="nearest")
    r = hi - lo
    seg = np.full(counts[s], np.nan)
    okp = (r > 0) & (np.arange(counts[s]) >= 249)  # 预热期置NaN(与rolling语义对齐)
    seg[okp] = (seg_close[okp] - lo[okp]) / r[okp] * 100
    pos250[a:b] = seg
    b60 = maximum_filter1d(big[a:b], 60, origin=ORIGIN60, mode="nearest")
    b60[:59] = 0.0  # rolling(60)前59根NaN→big60=False语义对齐
    big60[a:b] = (b60 == 1.0)
phase("pos250/big60完成(scipy)")

vr = np.full(N, np.nan)
okr = vma120 > 0
vr[okr] = vma5[okr] / vma120[okr]
trend = np.where((ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60), 1,
                 np.where((ma5 < ma10) & (ma10 < ma20) & (ma20 < ma60), -1, 0))
pull = (~np.isnan(ma10)) & (low_ <= ma10) & (ma10 <= close) & (ma20 > ma20_p5)
pop = (pos250 <= 50) & (close > ma20) & (ma20 > ma60) & big60 & (close <= 30.0)
shrink = vol < vma5

# ---- 3. 门日聚合 ----
day_id, uniq_days = pd.factorize(dates, sort=True)
n_days = len(uniq_days)
m_low = np.zeros(n_days); m_mom = np.zeros(n_days); m_tot = np.zeros(n_days)
inwin = (tbar >= 260) & np.isfinite(pos250)
np.add.at(m_tot, day_id[inwin], 1.0)
np.add.at(m_low, day_id[inwin & (pos250 < 40)], 1.0)
rok = inwin & np.isfinite(ret20) & (ret20 > 0)
np.add.at(m_mom, day_id[rok], 1.0)
valid = m_tot > 800
low_ratio = np.where(valid, m_low / np.maximum(m_tot, 1), np.nan)
mom_ratio = np.where(valid, m_mom / np.maximum(m_tot, 1), np.nan)
phase(f"门聚合: 有效日{int(valid.sum())}/{n_days} 门A开{int(((low_ratio >= 0.70) & valid).sum())} 门C开{int(((mom_ratio > 0.50) & valid).sum())}")

# ---- 4. A池事件 ----
cond = ((trend == 0) | (trend == -1)) & (pos250 < 40) & (vr < 1.0) & (close <= 10.0) \
    & pull & pop & shrink & (tbar >= 260) & (tbar < counts[codes_id] - 21)
cand_rows = np.where(cond)[0]
phase(f"候选(去重前)={len(cand_rows)}")

kept_rows = []
last_t = np.full(n_stocks, -99, dtype=np.int64)
for r in cand_rows:  # 稀疏小循环
    cid = codes_id[r]
    tb = tbar[r]
    if tb - last_t[cid] >= 5:
        kept_rows.append(r)
        last_t[cid] = tb
kept = np.asarray(kept_rows, dtype=np.int64)
phase(f"5日去重后={len(kept)}")

entry = opn[kept + 1]
px = close[kept]
gem_all = np.fromiter((u.startswith("3") for u in uniq_codes), bool, n_stocks)
gem = gem_all[codes_id[kept]]
lim = px * np.where(gem, 1.195, 1.095)
blocked = entry >= lim
ok = ~blocked
n_limit_blocked = int(blocked.sum())
kt = kept[ok]
k_entry = entry[ok]

idx_mat = kt[:, None] + np.arange(1, 21)[None, :]
E = close[idx_mat]
brk = E <= (0.92 * k_entry)[:, None]
anyb = brk.any(axis=1)
first_rel = np.where(anyb, brk.argmax(axis=1), 19)
ex = E[np.arange(len(kt)), first_rel]
vt = ex / k_entry - 1.0
ev_days = day_id[kt]
ev_date_str = dates[kt]
ev_amt_low = np.isfinite(amt[kt]) & (amt[kt] < 1e7)
assert len(vt) >= 200, f"A池事件={len(vt)}<200"
# 内存瘦身: 事件已提取, 释放全部面板大数组再进下游(裁剪环境下几百MB工作集不受 trim 伤害)
del close, opn, low_, vol, amt, dates, codes, ma5, ma10, ma20, ma60, ma20_p5, prev_c
del vma5, vma20, vma120, ret20, chg, pos250, big60, vr, trend, pull, pop, shrink, big
del cond, cand_rows, kept, entry, px, lim, blocked, ok, kt, k_entry, idx_mat, E, brk
del anyb, first_rel, ex, lo, hi, r, seg, okp, b60
gc.collect()
phase(f"事件n={len(vt)} 剔涨停不可买={n_limit_blocked} (面板已释放)")


# ---- 5. 下游 ----
def stats(vts, cost_bps=0):
    a = np.asarray(vts, float) - cost_bps / 1e4
    if len(a) == 0:
        return dict(n=0)
    return dict(n=int(len(a)), med=round(float(np.median(a)) * 100, 2),
                mean=round(float(a.mean()) * 100, 2),
                win=round(float((a > 0).mean()) * 100, 1))


def quadrant_at(ga, gc, cost_bps=0):
    ka = low_ratio[ev_days] >= ga
    kc = mom_ratio[ev_days] > gc
    out = {}
    for fa in (True, False):
        for fc in (True, False):
            m = (ka == fa) & (kc == fc)
            if m.any():
                out[f"门A{'开' if fa else '关'}∧门C{'开' if fc else '关'}"] = stats(vt[m], cost_bps)
    return out


by_cost = {f"cost{b}bps": stats(vt, b) for b in COST_BPS}
grid = {}
for ga in GATEA_GRID:
    for gc in GATEC_GRID:
        grid[f"A{ga}_C{gc}"] = {"gross": quadrant_at(ga, gc, 0), "net50": quadrant_at(ga, gc, 50)}
q_base = quadrant_at(0.70, 0.50, 0)

a1c0 = (low_ratio[ev_days] >= 0.70) & (mom_ratio[ev_days] <= 0.50)
day_index = defaultdict(list)
for i in np.where(a1c0)[0]:
    day_index[ev_days[i]].append(vt[i])
days_sorted = sorted(day_index)
if len(days_sorted) >= 10:
    rng = np.random.default_rng(20261011)
    day_arrays = [np.asarray(day_index[g], float) for g in days_sorted]
    n_d = len(day_arrays)
    meds = []
    for _ in range(1000):
        block_starts = rng.integers(0, n_d, size=(n_d + 4) // 5)  # 有放回抽块起点(标准块bootstrap)
        idx = []
        for b0 in block_starts:
            idx.extend(((b0 + np.arange(5)) % n_d).tolist())
        idx = idx[:n_d]
        meds.append(float(np.median(np.concatenate([day_arrays[j] for j in idx]))))
    best = np.concatenate([day_index[g] for g in days_sorted])
    boot = dict(n_days=n_d, med=round(float(np.median(best)) * 100, 2),
                p2_5=round(float(np.percentile(meds, 2.5)) * 100, 2),
                p97_5=round(float(np.percentile(meds, 97.5)) * 100, 2))
else:
    boot = dict(n_days=len(days_sorted), note="样本日不足10")

liq_low = vt[ev_amt_low]
yrs = np.array([d[:4] for d in ev_date_str])
yearly = {y: stats(vt[yrs == y]) for y in sorted(set(yrs))}
assert len(yearly) >= 2, "分年覆盖不足"

result = dict(
    engine="官方gate_c_interact_20261003口径逐位复刻+四增量(v4全速实现: read_sql+cumsumMA+每股C滚动极值+花式索引退出)",
    n_events=int(len(vt)), n_limit_up_blocked=n_limit_blocked,
    limit_block_note=f"剔除占比{n_limit_blocked/(len(vt)+n_limit_blocked)*100:.1f}%(分母=剔除前)",
    cost_sensitivity=by_cost, gate_grid=grid, baseline_quadrant=q_base,
    block_bootstrap=boot,
    liquidity=dict(n_low_amt=int(ev_amt_low.sum()),
                   med=stats(vt[ev_amt_low]).get("med") if ev_amt_low.any() else None),
    yearly=yearly,
    meta=dict(panel_start=START, gate_series_note="门比率序列起2022H2, 对2023+事件日无影响",
              boundary="ST名册不在qfq库→板宽按主板10%/创业板20%(ST 5%剔除遗漏注记, 引pool_a_st_sensitivity_20261008); "
                       "停牌=无bar自然不采样; 上市<60日=tbar>=260更严已覆盖"),
)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print("=" * 96)
print(f"A池事件n={len(vt)} 涨停不可买剔除{n_limit_blocked}件 | 总耗时{time.time()-T0:.0f}s")
print("成本三档(全样本):", json.dumps(by_cost, ensure_ascii=False))
print("基线象限(0.70/0.50):", json.dumps(q_base, ensure_ascii=False))
print("九组合·门A开∧门C关·net50:", json.dumps(
    {k: v["net50"].get("门A开∧门C关") for k, v in grid.items()}, ensure_ascii=False))
print("块bootstrap:", json.dumps(boot, ensure_ascii=False))
print("逐年:", json.dumps(yearly, ensure_ascii=False))
