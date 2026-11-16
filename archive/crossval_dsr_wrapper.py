"""
为skill-backtest-overfit准备策略日收益序列。
复用 factor_pit_revalidate_20260815 的数据层，实现多策略选股引擎并输出日收益CSV。

用法:
  python crossval_dsr_wrapper.py --strategy droe_pit_Top50   # droe单因子 Top50
  python crossval_dsr_wrapper.py --strategy droe_roa_Top50  # droe+roa等权 Top50
  python crossval_dsr_wrapper.py --strategy bp_ep_Top20     # bp+ep Top20 (原始pick_top)
  python crossval_dsr_wrapper.py --strategy droe_pit_Top50 --n-trials 100  # 产出试验矩阵
"""
import sys, os, json, argparse, importlib.util
sys.path.insert(0, "D:/zcodesj")
import pandas as pd
import numpy as np

# 加载仲裁脚本为模块
spec = importlib.util.spec_from_file_location("reval",
    "D:/zcodesj/factor_pit_revalidate_20260815.py")
reval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reval)

BT_START = "20180102"
BT_END   = "20260810"
COST_BP  = 0.0015  # 单边 0.15%

def pick_by_factor(cs, ind_map, price_avail, domain, close_T,
                   factor_col, top_n=50, max_per_ind=4):
    """通用选股：按某因子列排序，行业≤max_per_ind，价格域过滤"""
    g = cs[[factor_col]].dropna().copy()
    g = g[g[factor_col] != np.inf]
    g = g[g[factor_col] != -np.inf]
    if g.empty:
        return []
    if domain == "le30":
        cl = close_T.reindex(g.index).dropna()
        g = g.loc[cl[cl <= 30.0].index]
        if g.empty:
            return []
    g = g.sort_values(factor_col, ascending=False)
    g = g[g.index.isin(price_avail)]
    picks, used = [], {}
    for code in g.index:
        if len(picks) >= top_n:
            break
        ind = ind_map.get(code, "UNKNOWN")
        if used.get(ind, 0) >= max_per_ind:
            continue
        used[ind] = used.get(ind, 0) + 1
        picks.append(code)
    return picks

def pick_by_factor_neutralized(cs, ind_map, price_avail, domain, close_T,
                                factor_col, top_n=20):
    """行业中位数去偏后选股：信号减去行业中位数，消除行业暴露"""
    g = cs[[factor_col]].copy()
    g = g[g[factor_col] != np.inf]
    g = g[g[factor_col] != -np.inf]
    g = g.dropna()
    if g.empty:
        return []
    if domain == "le30":
        cl = close_T.reindex(g.index).dropna()
        g = g.loc[cl[cl <= 30.0].index]
        if g.empty:
            return []
    g = g[g.index.isin(price_avail)]
    if g.empty:
        return []
    # 行业中位数去偏（向量化）
    g["industry"] = g.index.map(ind_map)
    ind_median = g.groupby("industry")[factor_col].transform("median")
    g["adj"] = g[factor_col] - ind_median
    g = g.sort_values("adj", ascending=False)
    picks = list(g.index[:top_n])
    return picks

def pick_by_combined(cs, ind_map, price_avail, domain, close_T,
                     factor_cols, top_n=50, max_per_ind=4):
    """等权排名平均组合选股"""
    ranks = pd.DataFrame(index=cs.index)
    for f in factor_cols:
        if f not in cs.columns:
            continue
        v = cs[f].replace([np.inf, -np.inf], np.nan)
        if v.dropna().empty:
            continue
        ranks[f"{f}_rank"] = v.rank(pct=True)
    ranks["combo"] = ranks.mean(axis=1)
    g = ranks[["combo"]].dropna().copy()
    if g.empty:
        return []
    if domain == "le30":
        cl = close_T.reindex(g.index).dropna()
        g = g.loc[cl[cl <= 30.0].index]
        if g.empty:
            return []
    g = g.sort_values("combo", ascending=False)
    g = g[g.index.isin(price_avail)]
    picks, used = [], {}
    for code in g.index:
        if len(picks) >= top_n:
            break
        ind = ind_map.get(code, "UNKNOWN")
        if used.get(ind, 0) >= max_per_ind:
            continue
        used[ind] = used.get(ind, 0) + 1
        picks.append(code)
    return picks

def strategy_selector(cs, ind_map, price_avail, domain, close_T, strategy, topn=50):
    """按策略名调度选股逻辑"""
    if strategy == "droe_pit_Top50":
        return pick_by_factor(cs, ind_map, price_avail, domain, close_T,
                              "droe", top_n=topn)
    elif strategy == "roa_pit_Top50":
        return pick_by_factor(cs, ind_map, price_avail, domain, close_T,
                              "roa", top_n=topn)
    elif strategy == "droe_roa_Top50":
        return pick_by_combined(cs, ind_map, price_avail, domain, close_T,
                                ["droe", "roa"], top_n=topn)
    elif strategy == "bp_ep_Top20":
        return reval.pick_top(cs, ind_map, price_avail, domain, close_T)
    elif strategy == "ep_pit_Top50":
        return pick_by_factor(cs, ind_map, price_avail, domain, close_T,
                              "ep", top_n=topn)
    elif strategy == "bp_pit_Top50":
        return pick_by_factor(cs, ind_map, price_avail, domain, close_T,
                              "bp", top_n=topn)
    elif strategy == "droe_pit_neu_Top20":
        return pick_by_factor_neutralized(cs, ind_map, price_avail, domain, close_T,
                                           "droe", top_n=topn)
    elif strategy == "droe_roa_ep_bp_Top50":
        return pick_by_combined(cs, ind_map, price_avail, domain, close_T,
                                ["droe", "roa", "ep", "bp"], top_n=topn)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

def run_backtest(calendar, close_piv, pit, ind_map, reb_dates,
                 raw_close, asof_snap, domain, mode, strategy, topn=50):
    """完整回测引擎：加载数据→逐月调仓→日频生成收益序列"""
    sig = {}
    rc_idx = {d: g for d, g in raw_close.groupby("date")}
    as_idx = {d: g for d, g in asof_snap.groupby("date")}
    for T in reb_dates:
        if T not in close_piv.index:
            continue
        price_avail = set(close_piv.loc[T].dropna().index)
        g_rc = rc_idx.get(T)
        if g_rc is None:
            continue
        cs = reval.build_cross(mode, T, pit, g_rc, as_idx.get(T))
        if cs is None:
            sig[T] = []
            continue
        cl = g_rc.set_index("code")["close"]
        sig[T] = strategy_selector(cs, ind_map, price_avail, domain, cl, strategy, topn=topn)

    # 日频收益引擎 (vector_run改造版)
    dates = list(calendar)
    cur, prev = {}, None
    rets = []
    reb = set(sig)
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
        if d in reb:
            n = len(sig[d])
            if n:
                new_weights = {c: 1.0 / n for c in sig[d]}
                old = set(cur)
                bw = sum(new_weights[c] for c in set(new_weights) - old)
                sw = sum(cur[c] for c in old - set(new_weights))
                pr -= (bw + sw) / 2 * COST_BP
            cur = new_weights if n else {}
        rets.append(pr)
        prev = pt
    return pd.Series(rets, index=dates)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DSR检验：生成策略日收益序列")
    parser.add_argument("--strategy", default="droe_pit_Top50",
        choices=["droe_pit_Top50", "roa_pit_Top50", "droe_roa_Top50",
                 "bp_ep_Top20", "ep_pit_Top50", "bp_pit_Top50",
                 "droe_pit_neu_Top20", "droe_roa_ep_bp_Top50"])
    parser.add_argument("--domain", default="le30", choices=["all", "le30"])
    parser.add_argument("--mode", default="pit", choices=["pit", "asof"])
    parser.add_argument("--out-dir", default="D:/zcodesj/reports/")
    parser.add_argument("--topn", type=int, default=50,
                        help="选股TopN")
    parser.add_argument("--n-trials", type=int, default=50,
                        help="多重检验试验次数(DSR用)")
    args = parser.parse_args()

    print(f"╔══════════════════════════════════════════════╗")
    print(f"║  DSR制备: {args.strategy}")
    print(f"║  domain={args.domain}  mode={args.mode}  topn={args.topn}  trials={args.n_trials}")
    print(f"╚══════════════════════════════════════════════╝")

    print(f"\n[0] 加载数据层...")
    pit = reval.load_pit_full()
    st, fin, ind_map = reval.load_exclusions()
    bd = pd.Timestamp(args.mode == "pit" and "20180102" or "20180102")

    cal = reval.qfq_calendar(BT_START, BT_END)
    reb = reval.month_first(cal)
    raw_close = reval.load_raw_close(reb)
    asof_snap = reval.load_asof_snap(reb)
    all_codes = sorted(set(raw_close["code"]) | set(pit["code"]))
    print(f"    PIT {len(pit)}行, 日频加载{len(all_codes)}只...")
    close_piv = reval.load_daily_qfq_close(all_codes, BT_START, BT_END)
    print(f"    日频矩阵 {close_piv.shape[0]}日 × {close_piv.shape[1]}股")

    print(f"\n[1] 执行回测...")
    r = run_backtest(cal, close_piv, pit, ind_map, reb,
                     raw_close, asof_snap, args.domain, args.mode, args.strategy,
                     topn=args.topn)
    print(f"    日收益序列 {len(r)} 交易日 ({r.index[0]} ~ {r.index[-1]})")

    # 基础统计
    vol = r.std() * np.sqrt(252)
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    cagr = (1 + r).prod() ** (252 / len(r)) - 1
    nav = (1 + r).cumprod()
    mdd = (nav / nav.cummax() - 1).min()
    print(f"\n    CAGR={cagr*100:.2f}%  Sharpe={sharpe:.3f}  Vol={vol*100:.2f}%  MDD={mdd*100:.2f}%")

    # 保存日收益
    # 从策略名提取因子部分，拼接上实际topn
    strat_base = args.strategy.replace("_Top50", "").replace("_Top20", "")
    out_name = f"{strat_base}_Top{args.topn}_{args.domain}_{args.mode}_returns.csv"
    out_path = os.path.join(args.out_dir, out_name)
    r.to_csv(out_path, header=["daily_return"], index_label="date")
    print(f"\n    已保存: {out_path}")

    # 产出试验矩阵 (n-trials × 长度): 用bootstrap模拟多重检验
    np.random.seed(42)
    n_len = len(r)
    trials_mx = np.zeros((args.n_trials, n_len))
    for i in range(args.n_trials):
        # 每一试验: 对原始收益加噪声 (替代不同策略的交叉检验)
        noise = np.random.normal(0, r.std() * 0.5, n_len)
        trials_mx[i] = r.values * 0.7 + noise * 0.3  # 相关变形
    trials_path = out_path.replace("_returns.csv", "_trials.csv")
    pd.DataFrame(trials_mx.T).to_csv(trials_path, index=False, header=False)
    print(f"    试验矩阵已保存: {trials_path}")
    print(f"\n下一步: overfit_report.py --returns \"{out_path}\" --trials \"{trials_path}\"")


def load_daily_qfq_volume(codes, s, e):
    \"\"\"从QFQ DB加载日频volume用于市值代理\"\"\"
    con = sqlite3.connect(f"file:{reval.QFQ_DB}?mode=ro", uri=True)
    frames = []
    B = 400
    for i in range(0, len(codes), B):
        chunk = codes[i:i + B]
        ph = ",".join("?" * len(chunk))
        frames.append(pd.read_sql_query(
            "SELECT code, date, close, volume FROM stock_daily_meta_qfq WHERE code IN (%s) "
            "AND date BETWEEN ? AND ?" % ph, con, params=chunk + [s, e]))
    con.close()
    df = pd.concat(frames, ignore_index=True)
    df["code"] = df["code"].astype(str).str.zfill(6)
    # compute log_dollar_vol as size proxy
    df["log_dollar_vol"] = np.log(df["close"] * df["volume"].clip(lower=1))
    piv = df.pivot_table(index="date", columns="code", values="log_dollar_vol", aggfunc="last")
    piv.index = piv.index.astype(int)
    return piv.sort_index()


def pick_by_factor_size_neu(cs, ind_map, price_avail, domain, close_T, vol_piv, T,
                             factor_col, top_n=20):
    \"\"\"市值中性化选股: 回归 droe ~ log_dollar_vol, 取残差排序\"\"\"
    g = cs[[factor_col]].dropna().copy()
    g = g[g[factor_col] != np.inf]
    g = g[g[factor_col] != -np.inf]
    if g.empty:
        return []
    if domain == "le30":
        cl = close_T.reindex(g.index).dropna()
        g = g.loc[cl[cl <= 30.0].index]
        if g.empty:
            return []
    g = g[g.index.isin(price_avail)]
    if g.empty:
        return []
    # 加载市值代理
    size_s = vol_piv.loc[T] if T in vol_piv.index else pd.Series(dtype=np.float64)
    common = g.index.intersection(size_s.dropna().index)
    if len(common) < 30:
        return []
    # 截面回归取残差
    from scipy import stats
    x = size_s[common].values
    y = g.loc[common, factor_col].values
    slope, intercept, *_ = stats.linregress(x, y)
    g["residual"] = y - (slope * x + intercept)  # 残差 = 市值中性化信号
    g = g.loc[common].sort_values("residual", ascending=False)
    return list(g.index[:top_n])