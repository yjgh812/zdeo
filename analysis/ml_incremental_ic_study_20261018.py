# -*- coding: utf-8 -*-
"""P0-3 因子级增量检验（反思文档学习项，只读零交易权）：
ml_prob 在剥掉 bp/ep 后是否仍有增量截面信息（FM 月度回归 + 池内增量强制腿）。
数据合同与引擎同源：
  ml = stock_ml_scores_v22 (dragon_quant.db, engine 同表)
  bp/ep = factor_pit.db stock_factors_fundamental_pit_v2 (asof PIT + 当日收盘价换算, engine 同口径)
  fwd20 = qfq 收盘 (dragon_quant_qfq.db)
检验：
  ①原始 rankIC：spearman(ml_prob, fwd20)、bp、ep
  ②增量 FM 回归：每月截面 fwd20_rank ~ ml_z + bp_z + ep_z，收集 ml 系数序列 → 月均/明确 t
  ③池内增量（强制腿，「IC赢/池内输」三度复现后的必检项）：bp_ep Top50 池内 spearman(ml_prob, fwd20)
    + 池内 ml Top20 子集 fwd20 − 池均差
限制如实声明：ml 含模型选择效应（同引擎 v22 表）；universe=交集成∩close≤30（bp_ep 口径）；
p2 gating/情绪乘数不在信号定义内（测的是 ml_prob 原始信息量）。
输出 = reports/ml_incremental_ic_study_20261018.md（脚本直出）
"""
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
DQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
PIT = r"D:/zcodesj/factor_pit.db"
OUT = r"D:/zcodesj/reports/ml_incremental_ic_study_20261018.md"

L = []


def w(s=""):
    L.append(s)


t0 = datetime.now()

# ---- 交易日历（qfq 全部日期） ----
conn = sqlite3.connect(QFQ)
all_dates = sorted({int(r[0]) for r in conn.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq").fetchall()})
conn.close()
date_idx = {d: i for i, d in enumerate(all_dates)}

# ---- 月末信号日：2020-01 .. 使 fwd20 完整的最后月 ----
ml_start = 202001
last_usable_idx = len(all_dates) - 1 - 20
month_last = {}
for d in all_dates:
    ym = d // 100
    if ym >= ml_start and date_idx[d] <= last_usable_idx:
        month_last[ym] = d
signal_dates = sorted(month_last.values())

needed_dates = set(signal_dates)
for d in signal_dates:
    needed_dates.add(all_dates[date_idx[d] + 20])

# ---- qfq 收盘（仅需要日期） ----
need_int = sorted(needed_dates)
ph = ",".join("?" * len(need_int))
conn = sqlite3.connect(QFQ)
qdf = pd.read_sql_query(
    f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE date IN ({ph})",
    conn, params=need_int,
)
conn.close()
close = qdf.pivot_table(index="date", columns="code", values="close", aggfunc="last")
close.index = close.index.astype(int)
close = close.sort_index()
code_set = set(close.columns)

# ---- ml 信号（信号日截面） ----
conn = sqlite3.connect(DQ)
ph = ",".join("?" * len(signal_dates))
ml = pd.read_sql_query(
    f"SELECT code, date, ml_prob FROM stock_ml_scores_v22 WHERE date IN ({ph})",
    conn, params=signal_dates,
)
ml_min = conn.execute("SELECT MIN(date) FROM stock_ml_scores_v22").fetchone()[0]
conn.close()
ml["code"] = ml["code"].astype(str)
ml_pivot = ml.pivot_table(index="date", columns="code", values="ml_prob", aggfunc="last")
ml_pivot.index = ml_pivot.index.astype(int)

# ---- PIT 因子（增量 asof：逐月并入 pit_avail<=D 的最新记录） ----
conn = sqlite3.connect(f"file:{PIT}?mode=ro", uri=True)
pit = pd.read_sql_query(
    "SELECT code, pit_avail_date, ep, bp, ref_close FROM stock_factors_fundamental_pit_v2",
    conn,
)
conn.close()
pit = pit.dropna(subset=["ep", "bp", "ref_close"]).copy()
pit["code"] = pit["code"].astype(str)
pit["_num"] = pit["pit_avail_date"].map(lambda s: int(str(s).replace("-", "")[:8]) if "-" in str(s) else int(s))
pit = pit.sort_values(["code", "_num"])

month_list = sorted(month_last.keys())
pit_state = {}  # code -> (ep_share, bp_share) 最新披露股均指标
pit_ptr = 0
pit_records = pit.sort_values("_num").reset_index(drop=True)
pit_nums = pit_records["_num"].values

monthly_rows = []
for ym in month_list:
    D = month_last[ym]
    Dn = int(D)
    # 增量并入 pit（pit_avail<=D）
    j = np.searchsorted(pit_nums, Dn, side="right")
    while pit_ptr < j:
        rec = pit_records.iloc[pit_ptr]
        pit_state[rec["code"]] = (rec["ep"], rec["bp"])
        pit_ptr += 1

    closes = close.loc[D]
    fwd_date = all_dates[date_idx[D] + 20]
    fwd_close = close.loc[fwd_date]
    ml_row = ml_pivot.loc[D] if D in ml_pivot.index else None

    codes = [c for c in closes.index if c in code_set and c in pit_state]
    rows = []
    for c in codes:
        c0 = closes[c]
        cf = fwd_close.get(c, np.nan)
        ep_s, bp_s = pit_state[c]
        mlp = ml_row.get(c, np.nan) if ml_row is not None else np.nan
        if not (np.isfinite(c0) and c0 > 0 and np.isfinite(cf)):
            continue
        fwd = cf / c0 - 1
        rows.append((c, c0, fwd, ep_s / c0, bp_s / c0, mlp))
    df = pd.DataFrame(rows, columns=["code", "close", "fwd20", "ep", "bp", "ml_prob"])
    df = df[df["close"] <= 30].dropna(subset=["ep", "bp"])
    if len(df) < 100 or ml_row is None:
        continue
    df = df.dropna(subset=["ml_prob"])
    if len(df) < 100:
        continue

    # 排名
    for col in ("fwd20", "ep", "bp", "ml_prob"):
        df[col + "_r"] = df[col].rank(pct=True)

    ic_ml = spearmanr(df["ml_prob"], df["fwd20"]).correlation
    ic_bp = spearmanr(df["bp"], df["fwd20"]).correlation
    ic_ep = spearmanr(df["ep"], df["fwd20"]).correlation

    # 池内增量（强制腿）：bp_ep Top50 池
    df["score"] = (df["ep_r"] + df["bp_r"]) / 2
    pool = df.nlargest(50, "score")
    ic_pool = spearmanr(pool["ml_prob"], pool["fwd20"]).correlation if len(pool) >= 20 else np.nan
    ml_top20_in_pool = pool.nlargest(20, "ml_prob")
    pool_mean = pool["fwd20"].mean()
    spread = ml_top20_in_pool["fwd20"].mean() - pool_mean

    # FM 增量回归：fwd20_r ~ ml_r + bp_r + ep_r（pct rank 已是 [0,1]，中心化后取系数）
    X = np.column_stack([
        df["ml_prob_r"] - df["ml_prob_r"].mean(),
        df["bp_r"] - df["bp_r"].mean(),
        df["ep_r"] - df["ep_r"].mean(),
    ])
    y = df["fwd20_r"] - df["fwd20_r"].mean()
    coef_ml = float(np.linalg.lstsq(X, y, rcond=None)[0][0])

    monthly_rows.append({
        "ym": ym, "n": len(df), "ic_ml": ic_ml, "ic_bp": ic_bp, "ic_ep": ic_ep,
        "coef_ml": coef_ml, "ic_pool": ic_pool, "spread": spread,
    })

md = pd.DataFrame(monthly_rows)


def tstat(series):
    s = series.dropna()
    if len(s) < 5:
        return np.nan, np.nan, np.nan
    # 月度序列自相关弱,取 NW 简化 = 朴素 t 与 lag3 调整 t 并报
    mu = s.mean()
    se = s.std(ddof=1) / np.sqrt(len(s))
    t_plain = mu / se if se > 0 else np.nan
    e = s - mu
    ac = [float((e.shift(k) * e).mean() / (e.var(ddof=1) if e.var(ddof=1) > 0 else 1)) for k in (1, 2, 3)]
    lrv = se ** 2 * (1 + 2 * sum(a for a in ac if np.isfinite(a)))
    t_nw = mu / np.sqrt(lrv) if lrv > 0 else np.nan
    return mu, t_plain, t_nw


w("# P0-3 因子级增量检验：ml_prob 剥 bp/ep 后的截面信息量（只读预研）")
w()
w(f"> 生成：脚本 analysis/ml_incremental_ic_study_20261018.py 直出（禁人工转抄）｜"
  f"月度截面 n_months={len(md)}（{month_list[0]} ~ {month_list[-1]} 月末信号，fwd20 交易日）｜"
  f"universe=三源交集∩close≤30（bp_ep 口径）｜ml 表=引擎同源 stock_ml_scores_v22（覆盖自 {ml_min}）｜"
  f"日聚簇防护=月度系数序列 NW(lag3) 调整 t")
w()
w("口径声明：①ml_prob 为引擎同表原始分（p2 gating/情绪乘数不在信号定义内，测的是原始信息量）；")
w("②bp/ep=factor_pit v2 asof PIT + 当日收盘价换算（引擎同口径）；③fwd20=信号日收盘→20 交易日后收盘；")
w("④池内增量腿为强制项（「IC 赢/池内输」三度复现后的必检项，B4-TURNOVER-TILT 协议同构：bp_ep Top50 池）。")
w()
if md.empty:
    w("[FAIL-CLOSED] 无有效月度截面")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    sys.exit(2)

w("## 一、原始 rankIC（月均，NW t）")
w()
w("| 指标 | 月均 rankIC | 朴素 t | NW(3) t | 正 IC 月占比 |")
w("|---|---|---|---|---|")
for col, name in (("ic_ml", "ml_prob"), ("ic_bp", "bp"), ("ic_ep", "ep")):
    mu, tp, tn = tstat(md[col])
    pos = (md[col] > 0).mean()
    w(f"| {name} | {mu:+.4f} | {tp:+.2f} | {tn:+.2f} | {pos*100:.0f}% |")
w()

w("## 二、增量 FM 回归（fwd20_rank ~ ml_z + bp_z + ep_z，ml 系数为剥 bp/ep 后的截面增量）")
w()
mu_c, tp_c, tn_c = tstat(md["coef_ml"])
pos_c = (md["coef_ml"] > 0).mean()
w(f"- ml 增量系数月均 **{mu_c:+.5f}**（朴素 t {tp_c:+.2f}｜NW(3) t **{tn_c:+.2f}**）｜正系数月占比 {pos_c*100:.0f}%")
w(f"- 解读基准：ml_z 从 0.5→0.9 分位（+0.4）的 fwd20_rank 预期变动 = 系数×0.4；"
  f"按月均系数折算 ≈ {mu_c*0.4*100:+.2f}pp 的 rank 空间变动（rank 空间读数，非收益pp）")
w()

w("## 三、池内增量（强制腿：bp_ep Top50 池内）")
w()
mu_p, tp_p, tn_p = tstat(md["ic_pool"])
mu_s, tp_s, tn_s = tstat(md["spread"])
pos_p = (md["ic_pool"] > 0).mean()
pos_s = (md["spread"] > 0).mean()
w(f"- 池内 rankIC 月均 {mu_p:+.4f}（NW t {tn_p:+.2f}）｜正月占比 {pos_p*100:.0f}%")
w(f"- 池内 ml-Top20 子集 fwd20 − 池均：月均 {mu_s*100:+.2f}pp（NW t {tn_s:+.2f}）｜正月占比 {pos_s*100:.0f}%")
w()

w("## 四、分年稳定性")
w()
w("| 年 | 月数 | ic_ml 月均 | coef_ml 月均 | ic_pool 月均 |")
w("|---|---|---|---|---|")
md["year"] = (md["ym"] // 100).astype(str)
for yr, g in md.groupby("year"):
    w(f"| {yr} | {len(g)} | {g['ic_ml'].mean():+.4f} | {g['coef_ml'].mean():+.5f} | {g['ic_pool'].mean():+.4f} |")
w()

w("## 五、限制声明（反臆想）")
w()
w("1. ml_prob 含模型选择效应（expanding 全窗重训，引擎同表同窗），读数为相对量级非因果；")
w("2. universe=三源交集∩close≤30，与引擎全口径（ST/次新过滤等在 loader 内）存在差异，如实声明；")
w("3. 月度截面与引擎 22 交易日调仓节奏近似对齐而非逐日全复刻；")
w("4. 判读纪律：因子级 IC 显著 ≠ 池内增量 ≠ 可接线——三者须全过才算数（「IC 赢/池内输」三度复现在档）；")
w("5. 零交易权零接线，本预研不构成任何调参或立项依据。")
w()
w(f"**置信度**：机械事实高（同源数据直读+双检验腿+分年呈现）；对「ml 是否值得加权重」的因果推断不足（限制 1/2）。")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("\n".join(L))
print(f"\n[OK] {OUT}  耗时{(datetime.now()-t0).total_seconds():.0f}s")
