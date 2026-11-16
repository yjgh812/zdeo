# -*- coding: utf-8 -*-
"""B6: momvol 月度收益序列构建 + 与双引擎相关性 (只读, 2026-08-17)

复用 bt_momvol_enhancer_final.py 的信号生成逻辑 (向量级, 用于相关性分析;
全约束复核数值来自 bt 三轨回测输出)。
"""
import sys
import sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
BPEP = r"D:\zcodesj\reports\bp_ep_Top20_le30_pit_returns.csv"
WDOWN = r"D:\zcodesj\reports\droe_roa_Top50_le30_pit_returns.csv"

# ── 复用 bt_momvol 的信号构建 (与原脚本同口径) ──
log = lambda m: print("[%s] %s" % (__import__("time").strftime("%H:%M:%S"), m), flush=True)
START, END, PRE, TOPN = 20180101, 20260731, 20170701, 20

log("加载数据...")
con = sqlite3.connect(META_DB)
dates = [r[0] for r in con.execute(
    "SELECT DISTINCT date FROM stock_daily_meta WHERE date BETWEEN ? AND ? ORDER BY date",
    (PRE, END))]
df = pd.DataFrame({"d": dates})
df["ym"] = df["d"].astype(str).str[:6]
me_dates = [int(x) for x in df.groupby("ym")["d"].max().tolist()]
me_dates = [d for d in me_dates if d >= 20180131]
keep = set(me_dates)
raw_amt = pd.read_sql_query(
    "SELECT code, date, close, amount FROM stock_daily_meta "
    "WHERE date BETWEEN ? AND ? AND close > 0", con, params=(PRE, END))
con.close()
con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
qfq = pd.read_sql_query(
    "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
    "WHERE date BETWEEN ? AND ? AND close > 0", con, params=(PRE, END))
con.close()
raw_amt["date"] = raw_amt["date"].astype(int)
qfq["date"] = qfq["date"].astype(int)

log("构建因子(qfq清洁只杀上行)...")
close_p = qfq.pivot(index="date", columns="code", values="close").sort_index()
med = close_p.rolling(21, min_periods=5).median()
bad = (close_p / med > 3) | (close_p.pct_change() > 0.21)
close_p = close_p.mask(bad).ffill()
mom20 = close_p.pct_change(20)
vol20 = close_p.pct_change().rolling(20).std()
amt20 = raw_amt.pivot(index="date", columns="code", values="amount").sort_index().rolling(20).mean()
raw_close_me = raw_amt[raw_amt["date"].isin(keep)].pivot(
    index="date", columns="code", values="close")
raw_close_me.index = raw_close_me.index.astype(int)
pxs_qfq_clean = close_p.stack()
pxs_qfq_clean.index.names = ["date", "code"]

combo = (mom20.rank(axis=1, pct=True) - vol20.rank(axis=1, pct=True)) / 2
signals = {}
for d in me_dates:
    if d not in combo.index or d not in raw_close_me.index or d not in amt20.index:
        continue
    f = combo.loc[d].dropna()
    day_px = raw_close_me.loc[d]
    liq = amt20.loc[d]
    f = f[[c for c in f.index if c in day_px.index
           and day_px[c] == day_px[c] and 0 < day_px[c] <= 30
           and c in liq.index and liq[c] == liq[c] and liq[c] > 2e7]]
    if len(f) < TOPN * 3:
        continue
    signals[int(d)] = f.nlargest(TOPN).index.tolist()
log("信号月=%d" % len(signals))

# ── 向量级月度收益 (0.4%/次) ──
me = sorted(signals)
rets = []
for d0, d1 in zip(me[:-1], me[1:]):
    rs = []
    for c in signals[d0]:
        try:
            p0, p1 = pxs_qfq_clean.loc[(d0, c)], pxs_qfq_clean.loc[(d1, c)]
            if p0 > 0 and p1 > 0:
                rs.append(p1 / p0 - 1)
        except KeyError:
            pass
    if rs:
        rets.append((pd.Timestamp(str(d1)), np.mean(rs) - 0.004))
mret = pd.Series({d: r for d, r in rets}).sort_index()
mret.name = "momvol"
log("momvol 月度收益: %d 个月" % len(mret))

# ── 与双引擎相关性 ──
bpep = pd.read_csv(BPEP, parse_dates=[0], index_col=0).iloc[:, 0]
wdown = pd.read_csv(WDOWN, parse_dates=[0], index_col=0).iloc[:, 0]
mb = (1 + bpep).resample("ME").prod() - 1
mw = (1 + wdown).resample("ME").prod() - 1

# 沪深300 (剥离贝塔)
con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
hs = pd.read_sql_query(
    "SELECT date, close FROM index_daily_meta WHERE code='000300' ORDER BY date", con)
con.close()
hs["date"] = pd.to_datetime(hs["date"].astype(str), format="%Y%m%d")
hs = hs.set_index("date")["close"]
mkt_m = (1 + hs.pct_change().dropna()).resample("ME").prod() - 1

print("\n=== momvol 与双引擎相关性 ===")
for name, s in (("bp_ep", mb), ("W_DOWN", mw)):
    cc = s.dropna().index.intersection(mret.dropna().index)
    r_pearson = mret.loc[cc].corr(s.loc[cc])
    r_spear = mret.loc[cc].corr(s.loc[cc], method="spearman")
    # 剥离贝塔
    cm = mkt_m.dropna().index.intersection(cc)
    if len(cm) > 20:
        rm_, rs_, rmk = mret.loc[cm], s.loc[cm], mkt_m.loc[cm]
        bm = rm_.cov(rmk) / rmk.var()
        bs = rs_.cov(rmk) / rmk.var()
        em = rm_ - bm * rmk
        es = rs_ - bs * rmk
        r_alpha = em.corr(es)
    else:
        r_alpha = np.nan
    print(f"{name:<10} Pearson月={r_pearson:.4f}  Spearman月={r_spear:.4f}  alpha月={r_alpha:.4f}  (n={len(cc)})")

# momvol 自身统计
nav = (1 + mret).cumprod()
n_y = len(mret) / 12
cagr = nav.iloc[-1] ** (1 / n_y) - 1
sharpe = mret.mean() / (mret.std() + 1e-12) * np.sqrt(12)
mdd = (nav / nav.cummax() - 1).min()
print(f"\nmomvol 向量级: CAGR={cagr*100:.2f}% Sharpe={sharpe:.2f} MDD={mdd*100:.1f}% (n={len(mret)}月, 0.4%/次)")
print("(全约束数值见 bt_momvol_enhancer_final.py 三轨输出)")
