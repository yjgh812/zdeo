"""
市值中性化回测：DROE 截面回归剥离市值暴露
口径 = factor_pit_revalidate_20260815 同款
"""
import sys, sqlite3, time
sys.path.insert(0, "D:/zcodesj")
import numpy as np, pandas as pd
from scipy import stats

import factor_pit_revalidate_20260815 as reval

BT_START, BT_END = "20180102", "20260810"
COST = 0.0015
TOP_N = 20

log = lambda m: print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

# ── 加载数据 ──
log("加载 PIT 因子层...")
pit = reval.load_pit_full()
log(f"  PIT {len(pit)}行")

log("加载剔除集...")
st, fin, ind_map = reval.load_exclusions()

log("加载日历...")
cal = reval.qfq_calendar(BT_START, BT_END)
reb = reval.month_first(cal)
raw_close = reval.load_raw_close(reb)
asof_snap = reval.load_asof_snap(reb)
all_codes = sorted(set(raw_close["code"]) | set(pit["code"]))
log(f"  {len(all_codes)}只, {len(cal)}交易日")

log("加载 QFQ 日线 + 成交量...")
con = sqlite3.connect(f"file:{reval.QFQ_DB}?mode=ro", uri=True)
frames = []
B = 400
for i in range(0, len(all_codes), B):
    chunk = all_codes[i:i+B]
    ph = ",".join("?" * len(chunk))
    frames.append(pd.read_sql_query(
        "SELECT code, date, close, volume FROM stock_daily_meta_qfq WHERE code IN (%s) "
        "AND date BETWEEN ? AND ?" % ph, con, params=chunk + [BT_START, BT_END]))
con.close()
df = pd.concat(frames, ignore_index=True)
df["code"] = df["code"].astype(str).str.zfill(6)
df["log_dollar_vol"] = np.log((df["close"] * df["volume"].clip(lower=1)))
close_piv = df.pivot_table(index="date", columns="code", values="close", aggfunc="last")
close_piv = close_piv.sort_index()
close_piv.index = close_piv.index.astype(int)
vol_piv = df.pivot_table(index="date", columns="code", values="log_dollar_vol", aggfunc="last")
vol_piv = vol_piv.sort_index()
vol_piv.index = vol_piv.index.astype(int)
log(f"  日频矩阵 {close_piv.shape[0]}日 × {close_piv.shape[1]}股")

# ── 双版回测 ──
results = {}
for label, use_size_neu in [("non_neu", False), ("size_neu", True)]:
    log(f"回测: {label}")
    sig = {}
    rc_idx = {d: g for d, g in raw_close.groupby("date")}
    for T in reb:
        if T not in close_piv.index:
            continue
        price_avail = set(close_piv.loc[T].dropna().index)
        g_rc = rc_idx.get(T)
        if g_rc is None:
            continue
        cs = reval.build_cross("pit", T, pit, g_rc, None)
        if cs is None:
            sig[T] = []
            continue
        cl = g_rc.set_index("code")["close"]
        # droe 排序 + 价格/可用过滤
        g = cs[["droe"]].dropna().copy()
        g = g[g["droe"] != np.inf]
        g = g[g["droe"] != -np.inf]
        clf = cl.reindex(g.index).dropna()
        g = g.loc[clf[clf <= 30.0].index]
        g = g[g.index.isin(price_avail)]
        if g.empty:
            sig[T] = []
            continue
        if use_size_neu:
            # 市值中性化: 回归 droe ~ log_dollar_vol 取残差
            size_s = vol_piv.loc[T] if T in vol_piv.index else pd.Series(dtype=np.float64)
            common = g.index.intersection(size_s.dropna().index)
            if len(common) < 30:
                sig[T] = []
                continue
            x = size_s[common].values
            y = g.loc[common, "droe"].values
            slope, intercept, *_ = stats.linregress(x, y)
            g["signal"] = y - (slope * x + intercept)
            picks = g.loc[common].sort_values("signal", ascending=False).index[:TOP_N].tolist()
        else:
            picks = g.sort_values("droe", ascending=False).index[:TOP_N].tolist()
        sig[T] = picks

    # ── 日频收益引擎 ──
    dates = list(cal)
    cur, prev = {}, None
    rets = []
    reb_set = set(sig)
    empty = pd.Series(dtype=np.float64)
    for d in dates:
        pt = close_piv.loc[d] if d in close_piv.index else empty
        pr = 0.0
        if cur and prev is not None:
            valid = {}
            for c, w in cur.items():
                if c in pt.index and c in prev.index and prev[c] > 0 and pt[c] > 0:
                    pr += w * (pt[c] / prev[c] - 1)
                    valid[c] = w
            if valid:
                drift = {c: w * (pt[c] / prev[c]) for c, w in valid.items()}
                tot = sum(drift.values())
                if tot > 0:
                    orig = sum(cur.values())
                    cur = {c: v / tot * orig for c, v in drift.items()}
        if d in reb_set:
            n = len(sig[d])
            if n:
                new_w = {c: 1.0 / n for c in sig[d]}
                old = set(cur)
                bw = sum(new_w[c] for c in set(new_w) - old)
                sw = sum(cur[c] for c in old - set(new_w))
                pr -= (bw + sw) / 2 * COST
            cur = new_w if n else {}
        rets.append(pr)
        prev = pt

    r = pd.Series(rets, index=dates)
    vol = r.std() * np.sqrt(252)
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    cagr = (1 + r).prod() ** (252 / len(r)) - 1
    nav = (1 + r).cumprod()
    mdd = (nav / nav.cummax() - 1).min()

    # 输出
    out = f"D:/zcodesj/reports/droe_Top20_{label}_le30_pit_returns.csv"
    r.to_csv(out, header=["daily_return"], index_label="date")
    log(f"  CAGR={cagr*100:.2f}%  Sharpe={sharpe:.3f}  Vol={vol*100:.1f}%  MDD={mdd*100:.2f}%")
    results[label] = r

print("\n" + "=" * 55)
print("  对比")
print("=" * 55)
for k, r in results.items():
    vol = r.std() * np.sqrt(252)
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    cagr = (1 + r).prod() ** (252 / len(r)) - 1
    nav = (1 + r).cumprod()
    mdd = (nav / nav.cummax() - 1).min()
    print(f"  {k:12s}: CAGR={cagr*100:5.1f}% Sharpe={sharpe:.3f} Vol={vol*100:4.1f}% MDD={mdd*100:5.1f}%")