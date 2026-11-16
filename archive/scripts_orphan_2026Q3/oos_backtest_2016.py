"""样本外验证: droe_Top20 在 2016-2017（原回测期前）的表现"""
import sys, sqlite3, time
sys.path.insert(0, "D:/zcodesj")
import numpy as np, pandas as pd
import factor_pit_revalidate_20260815 as reval

OOS_START, OOS_END = "20160104", "20171231"
COST = 0.0015
TOP_N = 20

log = lambda m: print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

log("加载 PIT 因子层...")
pit = reval.load_pit_full()
st, fin, ind_map = reval.load_exclusions()

log(f"加载 OOS 日历 {OOS_START} ~ {OOS_END}...")
cal = reval.qfq_calendar(OOS_START, OOS_END)
reb = reval.month_first(cal)

# 需要该时期的原始close (from MAIN_DB)
raw_close = reval.load_raw_close(reb)
asof_snap = reval.load_asof_snap(reb)
all_codes = sorted(set(raw_close["code"]) | set(pit["code"]))

log(f"加载 QFQ 日线 {len(all_codes)}只...")
con = sqlite3.connect(f"file:{reval.QFQ_DB}?mode=ro", uri=True)
frames = []
B = 400
for i in range(0, len(all_codes), B):
    chunk = all_codes[i:i+B]
    ph = ",".join("?" * len(chunk))
    frames.append(pd.read_sql_query(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN (%s) "
        "AND date BETWEEN ? AND ?" % ph, con, params=chunk + [OOS_START, OOS_END]))
con.close()
df = pd.concat(frames, ignore_index=True)
df["code"] = df["code"].astype(str).str.zfill(6)
close_piv = df.pivot_table(index="date", columns="code", values="close", aggfunc="last")
close_piv = close_piv.sort_index()
close_piv.index = close_piv.index.astype(int)
log(f"  日频矩阵 {close_piv.shape[0]}日 × {close_piv.shape[1]}股")

# ── 回测 ──
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
    g = cs[["droe"]].dropna().copy()
    g = g[g["droe"] != np.inf]
    g = g[g["droe"] != -np.inf]
    clf = cl.reindex(g.index).dropna()
    g = g.loc[clf[clf <= 30.0].index]
    g = g[g.index.isin(price_avail)]
    sig[T] = g.sort_values("droe", ascending=False).index[:TOP_N].tolist() if not g.empty else []

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

print(f"\n{'='*50}")
print(f"  OOS 2016-2017: droe_Top20 (PIT, ≤30元, next-bar)")
print(f"{'='*50}")
print(f"  CAGR={cagr*100:.2f}%  Sharpe={sharpe:.3f}  Vol={vol*100:.1f}%  MDD={mdd*100:.2f}%")
print(f"  Obs={len(r)}  Date={r.index[0]}~{r.index[-1]}")
print(f"{'='*50}")

# 保存
r.index = pd.to_datetime(r.index.astype(str), format="%Y%m%d")
out = "D:/zcodesj/reports/droe_Top20_OOS2016_2017_le30_pit_returns.csv"
r.to_csv(out, header=["daily_return"], index_label="date")
print(f"\n  已保存: {out}")

# 各年
for yr in ["2016", "2017"]:
    yr_r = r.loc[yr]
    yr_vol = yr_r.std() * np.sqrt(252)
    yr_sr = yr_r.mean() / yr_r.std() * np.sqrt(252) if yr_r.std() > 0 else 0
    yr_ret = (1 + yr_r).prod() - 1
    print(f"  {yr}: ret={yr_ret*100:+.1f}%  Sharpe={yr_sr:.3f}  Vol={yr_vol*100:.1f}%")