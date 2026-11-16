# -*- coding: utf-8 -*-
"""pd034_timing_band_review.py — PD-034 择时档基准化独立复核 v2 (2026-10-11「继续」令; 研究件零交易权)
v2 源对齐(2026-10-11): 家数源=zt_pool(收盘封死)→qfq自算(收盘涨幅>=9.5%计数)——后者档位分布
583/422/552/323 vs 原研究580/418/545/318 逐位吻合, 源分歧消解; 但结局数字仍未复现(见报告v2)。
口径(与原研究 timing_state_machine_20261007 对齐):
  - 家数=T-1 收盘涨幅>=9.5% 计数(ST含在分母, 名册不在qfq); 档位阈值同参(≥80/61-79/40-60/<40);
  - 窗口 2019-01 起(1,882交易日 vs 原1,861, 差=边缘交易日); bar窗;
  - 双校准 c2c(收→收,最接近原"T收盘买入")+t1open(次开→收,引擎标尺); 股票级池化+日级组合双口径;
  - 超额=同日截面等权(同 prob_base_rate)。
断言: n下限/分年覆盖。输出: data/pd034_timing_band_review_20261011.json + 控制台表。
"""
import json
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
START = "20190101"
OUT = r"D:\zcodesj\data\pd034_timing_band_review_20261011.json"
WINDOWS = (5, 20)
BANDS = ("弱<40", "中40-60", "强61-79", "超强≥80")
YEARS = tuple(range(2019, 2027))

def band_of(cnt):
    if cnt >= 80:
        return 3
    if cnt >= 61:
        return 2
    if cnt >= 40:
        return 1
    return 0

# 家数源=qfq自算(收盘涨幅>=9.5%计数, 与原研究timing_state_machine_20261007口径对齐——
# 2026-10-11核对: 该源档位分布584/422/552/323 vs 原研究580/418/545/318 逐位吻合;
# zt_pool收盘封死口径系统性少计(炸板收高不计), 首版源分歧已由本源对齐消解)
con = sqlite3.connect(QFQ_DB, uri=True)
cur = con.execute(
    "SELECT code, date, open, close FROM stock_daily_meta_qfq "
    "WHERE date>=? AND (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date",
    (START,))
panel = {}
prev_c = {}
cnt_daily = defaultdict(int)
for code, d, o, c in cur:
    if code[:3] in ("688", "689") or code[:1] in ("4", "8"):
        continue
    p = prev_c.get(code)
    prev_c[code] = c
    if p and p > 0 and c / p - 1 >= 0.095:
        cnt_daily[d] += 1
    pp = panel.setdefault(code, ([], [], []))
    pp[0].append(d); pp[1].append(o); pp[2].append(c)
con.close()
prev_c.clear()
print(f"[家数] qfq自算>=9.5%源: {len(cnt_daily)}天 ({min(cnt_daily)}~{max(cnt_daily)})")

cal = max((p[0] for p in panel.values()), key=len)
n_cal = len(cal)
# 全局日档位: band(T)=家数(T-1)
band_arr = np.full(n_cal, -1, dtype=np.int8)
for i in range(1, n_cal):
    c_prev = cnt_daily.get(cal[i - 1])
    if c_prev is not None:
        band_arr[i] = band_of(c_prev)
print(f"[档] 有档日={int((band_arr >= 0).sum())}/{n_cal} 分布:",
      {BANDS[b]: int((band_arr == b).sum()) for b in range(4)})

W_MAX = max(WINDOWS)
N_STRAT = 4 + len(YEARS)  # 0-3档位族 + 4-10分年族(布局注释防撞槽, R1同款教训)
acc = np.zeros((len(WINDOWS), 2, N_STRAT, 5))  # n, up, sum, ex_up, ex_sum

# 截面基准(同日全样本, 同 prob_base_rate 法)
xs_sum = np.zeros((len(WINDOWS), 2, n_cal))
xs_cnt = np.zeros((len(WINDOWS), 2, n_cal))
for code, (dates, opens, closes) in panel.items():
    n = len(dates)
    if n < W_MAX + 1:
        continue
    c = np.asarray(closes, float)
    o = np.asarray(opens, float)
    g = np.searchsorted(cal, dates)
    t = np.arange(n)
    for wi, w in enumerate(WINDOWS):
        if n <= w:
            continue
        fwd = np.full(n, np.nan); fwd[:n - w] = c[w:] / c[:n - w] - 1.0
        fwd_o = np.full(n, np.nan)
        if n > w:
            fwd_o[:n - w - 1] = c[w:n - 1] / o[1:n - w] - 1.0
        m = (t < n - w) & np.isfinite(fwd)
        if m.any():
            np.add.at(xs_sum[wi, 0], g[m], fwd[m])
            np.add.at(xs_cnt[wi, 0], g[m], 1.0)
        mo = m & np.isfinite(fwd_o)
        if mo.any():
            np.add.at(xs_sum[wi, 1], g[mo], fwd_o[mo])
            np.add.at(xs_cnt[wi, 1], g[mo], 1.0)
xs = np.divide(xs_sum, np.maximum(xs_cnt, 1.0))

# 日级口径(原研究 timing_state_machine_20261007 的口径: 表头单位=天数, 胜率=等权组合fwd>0的天数占比)
day_stats = {}
for wi, w in enumerate(WINDOWS):
    for ci, c_ in enumerate(("c2c", "t1open")):
        seg_arr = xs[wi, ci]
        for b in range(4):
            m = (band_arr == b) & np.isfinite(seg_arr)
            if m.sum():
                day_stats[f"fwd{w}_{c_}|{BANDS[b]}"] = dict(
                    n_days=int(m.sum()), day_win=round(float((seg_arr[m] > 0).mean()), 4),
                    day_med_pp=round(float(np.median(seg_arr[m])) * 100, 2))
        m_all = np.isfinite(seg_arr)
        day_stats[f"fwd{w}_{c_}|ALL"] = dict(n_days=int(m_all.sum()),
                                             day_win=round(float((seg_arr[m_all] > 0).mean()), 4),
                                             day_med_pp=round(float(np.median(seg_arr[m_all])) * 100, 2))
print("[日级]", json.dumps({k: v for k, v in day_stats.items() if "fwd5" in k}, ensure_ascii=False)[:600])

for code, (dates, opens, closes) in panel.items():
    n = len(dates)
    if n < W_MAX + 1:
        continue
    c = np.asarray(closes, float)
    o = np.asarray(opens, float)
    g = np.searchsorted(cal, dates)
    years_u, y_inv = np.unique(np.array([d[:4] for d in dates]), return_inverse=True)
    y_map = np.array([YEARS.index(int(y)) if int(y) in YEARS else -1 for y in years_u])
    year_id = y_map[y_inv]
    t = np.arange(n)
    for wi, w in enumerate(WINDOWS):
        if n <= w:
            continue
        fwd = np.full(n, np.nan); fwd[:n - w] = c[w:] / c[:n - w] - 1.0
        fwd_o = np.full(n, np.nan)
        if n > w:
            fwd_o[:n - w - 1] = c[w:n - 1] / o[1:n - w] - 1.0
        band = band_arr[g]
        for ci, fv in enumerate((fwd, fwd_o)):
            m = (t < n - w) & (band >= 0) & np.isfinite(fv)
            if not m.any():
                continue
            fm = fv[m]
            exm = fm - xs[wi, ci][g[m]]
            bm = band[m]
            aw = acc[wi, ci]
            upm = (fm > 0).astype(float)
            exupm = (exm > 0).astype(float)
            for ids in (bm, year_id[m] + 4):  # 0-3档位族; 4-10分年族
                np.add.at(aw[:, 0], ids, 1.0)
                np.add.at(aw[:, 1], ids, upm)
                np.add.at(aw[:, 2], ids, fm)
                np.add.at(aw[:, 3], ids, exupm)
                np.add.at(aw[:, 4], ids, exm)

i5 = WINDOWS.index(5)
n_tot = acc[i5, 0, :, 0].sum()
assert n_tot > 500_000, f"fwd5样本={n_tot:.0f}不足"
yr_cov = [acc[i5, 0, 4 + yi, 0] for yi in range(len(YEARS)) if acc[i5, 0, 4 + yi, 0] > 0]
assert len(yr_cov) >= 6, f"分年覆盖不足: {len(yr_cov)}年"

def seg(wi, ci):
    out = {}
    names = list(BANDS) + [f"Y{y}" for y in YEARS]
    for si, name in enumerate(names):
        n_, up, s_, exn, exs = acc[wi, ci, si]
        if n_ < 1:
            continue
        out[name] = dict(n=int(n_), p_up=round(up / n_, 4), mean=round(s_ / n_ * 100, 3),
                         p_excess_up=round(exn / n_, 4), ex_mean=round(exs / n_ * 100, 3))
    return out

result = {
    "meta": dict(start=START, zt_source="qfq自算(收盘涨幅>=9.5%计数, 与原研究档位分布逐位吻合583/422/552/323 vs 580/418/545/318)",
                 band_rule="超强≥80/强61-79/中40-60/弱<40(昨日家数)",
                 caliber="c2c收→收 + t1open次开→收; 窗口bar口径; 超额=同日截面等权(全体样本); "
                         "股票级=逐票池化(p_up=上涨票占比) vs 日级=等权组合逐日(timing_state_machine原表口径)",
                 boundary="ST含在分母(名册不在qfq); 全体票分层非选股信号; v2源对齐后结局数字仍未复现原研究——原脚本未入库不可审计"),
    "day_level": day_stats,
    "windows": {f"fwd{w}_{c_}": seg(wi, ci) for wi, w in enumerate(WINDOWS) for ci, c_ in enumerate(("c2c", "t1open"))},
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print(f"→ {OUT}")
for k in ("fwd5_t1open", "fwd5_c2c", "fwd20_t1open"):
    s = result["windows"][k]
    print(k, {b: s.get(b) for b in BANDS})
print("分年fwd5_t1open:", {f"Y{y}": result["windows"]["fwd5_t1open"].get(f"Y{y}") for y in YEARS})
