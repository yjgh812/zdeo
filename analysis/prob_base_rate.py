# -*- coding: utf-8 -*-
"""prob_base_rate.py — 全市场无条件基准+超额lift标尺件 (R1, 2026-10-11 主人「继续」令; 研究件零交易权)
来源: 外部文档 PORTRAIT_PROB_LEARN_20261011 §二(基准先行/剥β) + §十三阶段一剩余缺口。
口径边界(如实):
  - 主口径 2019-01-01 起——qfq库2012-2017每年仅214-222只(不可用段, uscan_20261011已档), 禁引用外部"2012起"基准表;
  - qfq含退市股(对基准的存续偏差如实注记); 市值/行业分层本库qfq无列→结构性N/A留后续;
  - 窗口=bar数口径(与事件引擎一致, 停牌股日历拉长如实注记);
  - 超额=个股窗收益 − 同日截面等权平均同窗收益(逐日截面基准; 首版用逐日等权再投资组合复利,
    断言逮出+0.24%系统差=再投资溢价+窗内存续组成效应, 已弃用——截面基准下逐日超额均值恒0, 才是干净lift标尺)。
双校准: c2c(收→收, 经典基准) / t1open(次开→收, 事件引擎标尺; 截面基=同日同校准样本均值)。
分层: 分年 / 板别(主板0·6 vs 创业板3) / 门状态(门A0.70×门C0.50, 与官方引擎同参重算)。
实现注记: 三遍全向量化(np.add.at), 禁纯Python逐日循环(gate_c_interact同构循环实测小时级教训)。
断言(标准模板): n下限 / 逐年覆盖 / 总体超额均值≈0(截面基准构造性校验)。
输出: data/prob_base_rate_20261011.json + 控制台表。
"""
import json
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
START = "20190101"
OUT = r"D:\zcodesj\data\prob_base_rate_20261011.json"
WINDOWS = (1, 5, 20, 60)
GATE_A, GATE_C = 0.70, 0.50  # 与官方引擎 gate_c_interact_20261003 同参
YEARS = tuple(range(2019, 2027))
W_MAX = max(WINDOWS)

N_STRAT = 15
QUADS = ("A0C0", "A0C1", "A1C0", "A1C1")
FAM_OFFSET = {"all": 0, "board": 1, "quad": 3, "year": 7}  # board占1-2两槽(MAIN/GEM), quad占3-6, year占7-14
STRAT_NAMES = ["ALL", "MAIN", "GEM", *QUADS, *[f"Y{y}" for y in YEARS]]
acc = np.zeros((len(WINDOWS), 2, N_STRAT, 5))  # n, up, sum, ex_up, ex_sum

con = sqlite3.connect(QFQ_DB, uri=True)
cur = con.execute(
    "SELECT code, date, open, close, volume FROM stock_daily_meta_qfq "
    "WHERE date>=? AND (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date",
    (START,))
panel = {}
while True:
    chunk = cur.fetchmany(400000)
    if not chunk:
        break
    for code, d, o, c, v in chunk:
        if code[:3] in ("688", "689") or code[:1] in ("4", "8"):
            continue
        p = panel.setdefault(code, ([], [], [], []))
        p[0].append(d); p[1].append(o); p[2].append(c); p[3].append(v)
con.close()
del chunk

cal = max((p[0] for p in panel.values()), key=len)
n_cal = len(cal)

# ---- 第一遍: 门状态日聚合(向量化) ----
m_low = np.zeros(n_cal); m_mom = np.zeros(n_cal); m_tot = np.zeros(n_cal)
for code, (dates, opens, closes, vols) in panel.items():
    n = len(dates)
    if n < 260:
        continue
    c = np.asarray(closes, float)
    g = np.searchsorted(cal, dates)
    sw = np.lib.stride_tricks.sliding_window_view(c, 250)
    pos250 = np.concatenate([np.full(249, np.nan),
                             (sw[:, -1] - sw.min(axis=1)) / (sw.max(axis=1) - sw.min(axis=1)) * 100])
    ret20 = np.full(n, np.nan); ret20[20:] = c[20:] / c[:-20] - 1
    g260 = g[259:]
    vok = np.isfinite(pos250[259:])
    np.add.at(m_tot, g260[vok], 1.0)
    np.add.at(m_low, g260[vok & (pos250[259:] < 40)], 1.0)
    rok = vok & np.isfinite(ret20[259:]) & (ret20[259:] > 0)
    np.add.at(m_mom, g260[rok], 1.0)

valid = m_tot > 800
gateA = np.where(valid, m_low / np.maximum(m_tot, 1), np.nan) >= GATE_A
gateC = np.where(valid, m_mom / np.maximum(m_tot, 1), np.nan) > GATE_C
print(f"[门] 有效日={int(valid.sum())}/{n_cal} 门A开={int((gateA & valid).sum())}天 门C开={int((gateC & valid).sum())}天")

# ---- 第二遍: 同日截面基准累计 (per window×caliber: xs_sum/xs_cnt on 全局日) ----
xs_sum = np.zeros((len(WINDOWS), 2, n_cal))
xs_cnt = np.zeros((len(WINDOWS), 2, n_cal))

def win_arrays(c, o, n, w):
    fwd = np.full(n, np.nan); fwd[:n - w] = c[w:] / c[:n - w] - 1.0
    fwd_o = np.full(n, np.nan)
    if n > w:
        fwd_o[:n - w - 1] = c[w:n - 1] / o[1:n - w] - 1.0
    return fwd, fwd_o

for code, (dates, opens, closes, vols) in panel.items():
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
        fwd, fwd_o = win_arrays(c, o, n, w)
        m = (t < n - w) & np.isfinite(fwd)
        if m.any():
            np.add.at(xs_sum[wi, 0], g[m], fwd[m])
            np.add.at(xs_cnt[wi, 0], g[m], 1.0)
        mo = m & np.isfinite(fwd_o)
        if mo.any():
            np.add.at(xs_sum[wi, 1], g[mo], fwd_o[mo])
            np.add.at(xs_cnt[wi, 1], g[mo], 1.0)

xs = np.divide(xs_sum, np.maximum(xs_cnt, 1.0))  # (W, 2, n_cal) 同日截面基准

# ---- 第三遍: 超额+分层累积 ----
med20 = defaultdict(list)
for code, (dates, opens, closes, vols) in panel.items():
    n = len(dates)
    if n < W_MAX + 1:
        continue
    c = np.asarray(closes, float)
    o = np.asarray(opens, float)
    g = np.searchsorted(cal, dates)
    board_id = 1 if code.startswith("3") else 0  # 0=MAIN 1=GEM
    years_u, y_inv = np.unique(np.array([d[:4] for d in dates]), return_inverse=True)
    y_map = np.array([YEARS.index(int(y)) if int(y) in YEARS else -1 for y in years_u])
    year_id = y_map[y_inv]
    t = np.arange(n)
    for wi, w in enumerate(WINDOWS):
        if n <= w:
            continue
        fwd, fwd_o = win_arrays(c, o, n, w)
        base = (t < n - w) & (year_id >= 0)          # 全体族: 无指标预热要求
        quad_ok = (g >= 260) & valid[g]              # 门四格族: pos250/ret20 预热
        if not base.any():
            continue
        ga = gateA[g] & valid[g]
        gc = gateC[g] & valid[g]
        quad_id = np.where(ga, 2, 0) + np.where(gc, 1, 0)
        for ci, fv in enumerate((fwd, fwd_o)):
            m = base & np.isfinite(fv)
            if not m.any():
                continue
            fm = fv[m]
            exm = fm - xs[wi, ci][g[m]]
            fams = (
                np.full(int(m.sum()), FAM_OFFSET["all"]),
                np.full(int(m.sum()), FAM_OFFSET["board"] + board_id),
                FAM_OFFSET["quad"] + quad_id[m],
                FAM_OFFSET["year"] + year_id[m],
            )
            aw = acc[wi, ci]
            upm = (fm > 0).astype(float)
            exupm = (exm > 0).astype(float)
            for ids in fams:
                np.add.at(aw[:, 0], ids, 1.0)
                np.add.at(aw[:, 1], ids, upm)
                np.add.at(aw[:, 2], ids, fm)
                np.add.at(aw[:, 3], ids, exupm)
                np.add.at(aw[:, 4], ids, exm)
            if w == 20:
                for q in range(4):
                    mq = m & quad_ok & (quad_id == q)
                    if mq.any():
                        med20[("c2c", QUADS[q])].append(fv[mq])
                        med20[("t1open", QUADS[q])].append(fwd_o[mq])
                med20[("c2c", "ALL")].append(fm)
                mo = m & np.isfinite(fwd_o)
                if mo.any():
                    med20[("t1open", "ALL")].append(fwd_o[mo])

# ---- 断言(标准模板) ----
i20 = WINDOWS.index(20)
n_all20 = acc[i20, 0, FAM_OFFSET["all"], 0]
assert n_all20 > 1_000_000, f"fwd20样本={n_all20:.0f} < 1e6, 覆盖不足"
yr_cov = [y for y in YEARS if acc[i20, 0, FAM_OFFSET["year"] + YEARS.index(y), 0] > 0]
assert len(yr_cov) >= 7, f"逐年覆盖不足: {yr_cov}"
ex_mean_all = acc[i20, 0, FAM_OFFSET["all"], 4] / n_all20
assert abs(ex_mean_all) < 1e-6, f"总体超额均值{ex_mean_all:.8f}≠0, 截面基准实现有bug"

def seg(wi, ci):
    out = {}
    for si, name in enumerate(STRAT_NAMES):
        n_, up, s_, exn, exs = acc[wi, ci, si]
        if n_ < 1:
            continue
        out[name] = dict(n=int(n_), p_up=round(up / n_, 4), mean=round(s_ / n_ * 100, 3),
                         p_excess_up=round(exn / n_, 4), ex_mean=round(exs / n_ * 100, 3))
    return out

med_out = {f"{c_}|{s_}": dict(n=int(sum(len(x) for x in v)),
                              med=round(float(np.median(np.concatenate(v))) * 100, 3))
           for (c_, s_), v in sorted(med20.items()) if sum(len(x) for x in v) >= 100}

result = {
    "meta": dict(start=START, calendar_days=n_cal, gate_thresholds=(GATE_A, GATE_C),
                 caliber_note="c2c=收→收(经典基准); t1open=次开→收(事件引擎标尺); 窗口=bar口径; "
                              "超额=个股窗收益−同日截面等权平均同窗收益(逐日均值恒0, 干净lift标尺)",
                 boundary="qfq含退市股(存续偏差如实注记); 市值/行业分层库无列N/A; 2012-2017不可用段禁外推; "
                          "停牌股bar窗日历拉长与事件引擎同口径"),
    "gates": dict(valid_days=int(valid.sum()), gateA_open_days=int((gateA & valid).sum()),
                  gateC_open_days=int((gateC & valid).sum())),
    "windows": {f"fwd{w}_{c_}": seg(wi, ci) for wi, w in enumerate(WINDOWS) for ci, c_ in enumerate(("c2c", "t1open"))},
    "fwd20_median": med_out,
}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print(f"→ {OUT}")
for k in ("fwd1_c2c", "fwd5_c2c", "fwd20_c2c", "fwd60_c2c", "fwd20_t1open"):
    print(k, "ALL:", result["windows"][k].get("ALL"))
print("fwd20_c2c 门四格:", {q: result["windows"]["fwd20_c2c"].get(q) for q in QUADS})
print("fwd20_t1open 门四格:", {q: result["windows"]["fwd20_t1open"].get(q) for q in QUADS})
print("分年fwd20_c2c:", {f"Y{y}": result["windows"]["fwd20_c2c"].get(f"Y{y}") for y in YEARS})
