#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSCV / PBO 回测过拟合自检工具
================================================================
方法论: Bailey, Borwein, Lopez de Prado & Zhu (2014)
        "The Probability of Backtest Overfitting"
        (华泰金工系列22《回测过拟合的概率》引入中国的 CSCV 框架)

核心思想:
  把整个回测期收益矩阵(时间x轨道)按时间顺序切成 S 份子矩阵,
  对称地取其中 S/2 份拼成训练集, 其余 S/2 份为测试集, 遍历全部
  对称组合(共 C(S,S/2)/2 个)。每次组合里:
    1) 训练集上选出收益能力最优的轨道 n*
    2) 看 n* 在测试集上收益排名的相对位置 omega (0=最差, 1=最好)
    3) lambda = ln(omega / (1-omega)), omega<0.5 即测试期落入下半区
  PBO = lambda<0 的组合占比 = "训练期最优轨道在测试期落入收益
  排名下半区的频率"。纯随机轨道的 PBO 理论值 = 0.5。
  PBO < 0.25 较稳健; 0.25~0.5 存疑; > 0.5 大概率过拟合。

用法:
  # 1) 纯随机轨道自测(工具正确性校验, PBO应约=0.5)
  python backtest_pbo_check.py --demo

  # 2) 自测: 真信号轨道(共同alpha+小噪声, PBO应显著低)
  python backtest_pbo_check.py --demo --demo-mode signal

  # 3) 参数扫描结果矩阵: CSV(首列可为日期列, 其余每列一条轨道)
  python backtest_pbo_check.py --input sweep_returns.csv --splits 16

  # 4) npz 矩阵 (shape=[T,N], 每列一条轨道)
  python backtest_pbo_check.py --input sweep_returns.npz --splits 16

  # 5) 单轨道退化用法: 真实策略收益序列 vs 匹配均值/方差的人工噪声
  python backtest_pbo_check.py --input bp_ep_equity.csv --single-vs-noise

输入格式:
  - CSV: 每列一条轨道(日/月收益或净值)。净值列会自动转收益。
         首列若为日期(非数值)自动跳过。BOM 自动处理。
  - NPZ: 单个二维数组, 键名任意(取第一个)。

已知限制:
  - 轨道数 N<5 时 PBO 分辨率粗(排名下半区粒度=1/N), 结果仅供参考;
  - --single-vs-noise 检验的是"策略是否显著优于随机噪声"(退化的
    过拟合/有效性检验), 不等价于标准 CSCV-PBO;
  - 假定各轨道收益矩阵行(时间)已对齐, 工具只按行序切分。
"""
import argparse
import itertools
import sys

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------
# 数据加载
# ----------------------------------------------------------------------
def load_matrix(path: str) -> pd.DataFrame:
    """读入 CSV/NPZ, 返回 DataFrame(index=时间序号或日期, 每列一条轨道, 值=收益)."""
    if path.lower().endswith(".npz"):
        z = np.load(path, allow_pickle=False)
        arr = z[list(z.keys())[0]]
        arr = np.asarray(arr, dtype=float)
        if arr.ndim != 2:
            raise ValueError(f"npz 数组须为二维 [T,N], got {arr.shape}")
        df = pd.DataFrame(arr, columns=[f"track{i}" for i in range(arr.shape[1])])
    else:
        df = pd.read_csv(path, encoding="utf-8-sig")
        # 首列日期处理: 列名为日期类关键字, 或非数值列
        first = df.columns[0]
        if first.strip().lower() in ("date", "trade_date", "datetime", "day") \
                or not np.issubdtype(df[first].dtype, np.number):
            df = df.set_index(first)
        # 全数值列
        df = df.select_dtypes(include=[np.number]).astype(float)
    df = df.dropna(how="all")
    return to_returns(df)


def load_matrix_dir(path: str) -> pd.DataFrame:
    """目录模式: 目录下每个 CSV = 一条轨道(取其首个数值列为净值/收益),
    按日期(若有)对齐合并成多轨道矩阵。适用于每参数一个结果文件的扫描输出。"""
    import os
    import glob
    files = sorted(glob.glob(os.path.join(path, "*.csv")))
    if not files:
        raise FileNotFoundError(f"{path} 下无 CSV")
    cols = {}
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        df = pd.read_csv(f, encoding="utf-8-sig")
        first = df.columns[0]
        idx = None
        if not np.issubdtype(df[first].dtype, np.number):
            idx = df[first]
            val_col = df.columns[1] if df.shape[1] > 1 else first
        else:
            val_col = first
        s = pd.to_numeric(df[val_col], errors="coerce").reset_index(drop=True)
        if idx is not None:
            s.index = pd.Index(idx)
        cols[name] = s
    merged = pd.DataFrame(cols)
    merged = merged.dropna(how="all").sort_index()
    # 日期索引则用outer对齐已有, 缺口前向填充无意义 -> 保留NaN由to_returns处理
    return to_returns(merged.reset_index(drop=True))


def to_returns(df: pd.DataFrame) -> pd.DataFrame:
    """若是净值(全正且可复利还原)则转为简单收益, 否则原样返回."""
    vals = df.to_numpy()
    if vals.size and np.nanmin(vals) > 0 and np.all(np.isfinite(vals)):
        rets = df.pct_change(fill_method=None).iloc[1:]
        if np.allclose((1.0 + rets).prod(), df.iloc[-1] / df.iloc[0], rtol=0.05):
            return rets.reset_index(drop=True)
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------
# CSCV 核心
# ----------------------------------------------------------------------
def perf_metric(returns: np.ndarray, metric: str = "sharpe") -> np.ndarray:
    """按列计算绩效, 返回长度 N 的得分(越大越好)."""
    if metric == "sharpe":
        mu = np.nanmean(returns, axis=0)
        sd = np.nanstd(returns, axis=0, ddof=1)
        sd = np.where(sd < 1e-15, np.nan, sd)
        return mu / sd
    if metric == "total_return":
        return np.nanprod(1.0 + np.nan_to_num(returns, nan=0.0), axis=0) - 1.0
    raise ValueError(f"未知 metric: {metric}")


def cscv_pbo(returns: pd.DataFrame, splits: int = 16, metric: str = "sharpe",
             verbose: bool = True):
    """
    返回 dict: pbo, lambda 分位数, 每轨道训练/测试一致性摘要。
    """
    M = returns.to_numpy(dtype=float)
    T, N = M.shape
    if splits < 4 or splits % 2 != 0:
        raise ValueError("--splits 须为>=4 的偶数")
    if T < splits * 4:
        raise ValueError(f"时间长度 T={T} 不足以切 {splits} 份(建议 T>=4*S)")
    if N < 2:
        raise ValueError("轨道数 N<2, 无法计算标准 PBO (请用 --single-vs-noise)")

    # 切 S 份(按行连续切)
    edges = np.linspace(0, T, splits + 1).astype(int)
    blocks = [M[edges[i]:edges[i + 1]] for i in range(splits)]
    # 向量化预计算: 每块每轨道的收益和/平方和, 便于拼任意训练/测试集的均值方差
    b_sum = np.array([np.nansum(b, axis=0) for b in blocks])          # [S,N]
    b_sumsq = np.array([np.nansum(b * b, axis=0) for b in blocks])    # [S,N]
    b_sumlog = None
    if metric == "total_return":
        # log1p 仅对小数收益有效; percent 单位输入会出现 1+r<0 -> NaN 警告
        with np.errstate(invalid="ignore"):
            b_sumlog = np.array([np.nansum(np.log1p(np.nan_to_num(b, nan=0.0)),
                                           axis=0) for b in blocks])  # [S,N]
    b_cnt = np.array([b.shape[0] for b in blocks], dtype=float)       # [S]

    half = splits // 2
    combos = list(itertools.combinations(range(splits), half))
    # 对称去重: 只保留 组合 < 补集 的那一半
    combos = [c for c in combos if c < tuple(sorted(set(range(splits)) - set(c)))]

    def _metric_from(idx, metric):
        n = b_cnt[list(idx)].sum()
        s = b_sum[list(idx)].sum(axis=0)
        mu = s / n
        if metric == "sharpe":
            ss = b_sumsq[list(idx)].sum(axis=0)
            var = np.maximum(ss / n - mu * mu, 0.0)
            sd = np.sqrt(var)
            sd = np.where(sd < 1e-15, np.nan, sd)
            return mu / sd
        return b_sumlog[list(idx)].sum(axis=0)  # 总收益的log, 单调等价于prod(1+r)-1

    lambdas = []
    n_best_counts = np.zeros(N)          # 各轨道被选为训练期最优的次数
    test_relrank_when_best = [[] for _ in range(N)]  # 被选最优时测试期相对排名
    test_relrank_all = [[] for _ in range(N)]        # 所有组合下测试期相对排名

    all_idx = frozenset(range(splits))
    for c in combos:
        comp = tuple(sorted(all_idx - set(c)))

        tr_score = _metric_from(c, metric)
        te_score = _metric_from(comp, metric)

        n_star = int(np.nanargmax(tr_score))
        n_best_counts[n_star] += 1

        # 测试期相对排名 omega (0=最差, 1=最好; 与Bailey约定一致)
        order = pd.Series(te_score).rank(ascending=False, method="average").to_numpy()
        omega = (N - order[n_star] + 1.0) / N  # rank1 -> omega=1
        # omega 取 (1/N, 1); 拉姆达 = ln(omega/(1-omega)); omega=0.5 -> 0
        omega = min(max(omega, 1e-9), 1 - 1e-9)
        lambdas.append(np.log(omega / (1.0 - omega)))
        test_relrank_when_best[n_star].append(omega)
        for i in range(N):
            test_relrank_all[i].append((N - order[i] + 1.0) / N)

    lambdas = np.asarray(lambdas)
    pbo = float(np.mean(lambdas < 0))

    consistency = pd.DataFrame({
        "track": returns.columns,
        "train_best_freq": n_best_counts / len(combos),
        "test_relrank_when_best": [float(np.mean(x)) if x else np.nan
                                   for x in test_relrank_when_best],
        "test_relrank_mean": [float(np.mean(x)) for x in test_relrank_all],
        "test_relrank_gt50_freq": [float(np.mean(np.asarray(x) > 0.5))
                                   for x in test_relrank_all],
        "test_relrank_p05": [float(np.percentile(x, 5)) if x else np.nan
                             for x in test_relrank_all],
        "full_period_score": perf_metric(M, metric),
    }).sort_values("train_best_freq", ascending=False)

    if verbose:
        print(f"\n=== CSCV/PBO 结果 ===")
        print(f"轨道数 N={N}, 时间长度 T={T}, 切分 S={splits}, "
              f"组合数 C={len(combos)}, 绩效指标={metric}")
        print(f"PBO = {pbo:.4f}")
        q = np.percentile(lambdas, [5, 25, 50, 75, 95])
        print(f"lambda 分位数 p5/p25/p50/p75/p95 = "
              f"{q[0]:.3f}/{q[1]:.3f}/{q[2]:.3f}/{q[3]:.3f}/{q[4]:.3f}")
        if pbo < 0.25:
            verdict = "PBO<0.25: 参数选择大概率可泛化, 结果较稳健"
        elif pbo <= 0.5:
            verdict = "0.25<=PBO<=0.5: 存在过拟合嫌疑, 谨慎解读训练期最优参数"
        else:
            verdict = "PBO>0.5: 大概率过拟合, 训练期最优≈测试期偏差(比随机还差)"
        print(f"判读: {verdict}")
        with pd.option_context("display.width", 160, "display.float_format",
                               lambda v: f"{v:.4f}"):
            print("\n--- 各轨道训练/测试一致性摘要(按被选最优频率降序) ---")
            print(consistency.to_string(index=False))
    return {"pbo": pbo, "lambdas": lambdas, "consistency": consistency,
            "n_combos": len(combos)}


# ----------------------------------------------------------------------
# 单轨道退化用法: 真实轨道 vs 匹配均值/方差的噪声轨道
# ----------------------------------------------------------------------
def single_vs_noise(returns: pd.DataFrame, n_noise: int = 49,
                    seed: int = 42, splits: int = 16, verbose: bool = True):
    """
    真实轨道 + n_noise 条 iid正态噪声(匹配真实轨道的均值/标准差)。
    报告: (a) PBO_ensemble(真实轨道若在训练期领先, 测试期是否跌入下半区);
          (b) 真实轨道测试期相对排名>0.5 的频率(退化PBO定义)。
    严格说这检验的是"策略是否显著优于随机噪声", 不是标准CSCV-PBO。
    """
    rng = np.random.default_rng(seed)
    r = returns.to_numpy(dtype=float).squeeze()
    mu, sd = np.nanmean(r), np.nanstd(r, ddof=1)
    noise = rng.normal(mu, sd, size=(len(r), n_noise))
    ens = pd.DataFrame(np.column_stack([r, noise]),
                       columns=["REAL"] + [f"noise{i}" for i in range(n_noise)])
    res = cscv_pbo(ens, splits=splits, verbose=False)
    row = res["consistency"].set_index("track").loc["REAL"]
    if verbose:
        print("\n=== 单轨道退化检验: 真实轨道 vs 匹配均值/方差噪声轨道 ===")
        print(f"(真实轨道日/期收益: mean={mu:.6f}, sd={sd:.6f}, "
              f"噪声轨道数={n_noise}, seed={seed})")
        print(f"真实轨道被选为训练期最优的频率 = {row['train_best_freq']:.4f}")
        print(f"被选最优时测试期平均相对排名 = {row['test_relrank_when_best']:.4f}")
        print(f"全组合测试期相对排名>0.5的频率 = {row['test_relrank_gt50_freq']:.4f}")
        print(f"ensemble PBO(真实vs噪声) = {res['pbo']:.4f}")
        print("解读: PBO明显<0.5 且真实轨道训练期高频领先=>策略大概率含真实信号;")
        print("      PBO≈0.5 => 与随机噪声不可区分; PBO>0.5 => 风险极高。")
    return res


# ----------------------------------------------------------------------
# demo: 已知答案的自测
# ----------------------------------------------------------------------
def make_demo(mode: str = "random", n_tracks: int = 50, n_days: int = 1200,
              seed: int = 7) -> pd.DataFrame:
    """random: 纯随机轨道(理论PBO=0.5); signal: 共同alpha+异质噪声(PBO应低)."""
    rng = np.random.default_rng(seed)
    if mode == "random":
        R = rng.normal(0.0, 0.01, size=(n_days, n_tracks))
    elif mode == "signal":
        alpha = rng.normal(0.0006, 0.0002, size=(n_days, 1))  # 共同弱alpha
        R = alpha + rng.normal(0.0, 0.01, size=(n_days, n_tracks))
    else:
        raise ValueError(f"未知 demo-mode: {mode}")
    return pd.DataFrame(R, columns=[f"t{i}" for i in range(n_tracks)])


# ----------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="CSCV/PBO 回测过拟合自检 (Bailey et al. 2014)")
    ap.add_argument("--input", help="CSV/NPZ 收益矩阵文件(列=轨道)")
    ap.add_argument("--input-dir", help="目录模式: 每个CSV=一条轨道, 自动合并")
    ap.add_argument("--splits", type=int, default=16, help="时间切分份数 S(默认16, 偶数)")
    ap.add_argument("--metric", default="sharpe", choices=["sharpe", "total_return"])
    ap.add_argument("--demo", action="store_true", help="内置demo自测(随机轨道)")
    ap.add_argument("--demo-mode", default="random", choices=["random", "signal"])
    ap.add_argument("--demo-tracks", type=int, default=50)
    ap.add_argument("--demo-days", type=int, default=1200)
    ap.add_argument("--single-vs-noise", action="store_true",
                    help="单轨道退化用法: 真实轨道 vs 噪声轨道")
    ap.add_argument("--n-noise", type=int, default=49)
    args = ap.parse_args(argv)

    if args.demo:
        print(f"[demo] mode={args.demo_mode}, tracks={args.demo_tracks}, "
              f"days={args.demo_days}"
              + (" (纯随机轨道 PBO 理论值≈0.48, N=50时下半区概率=24/50)"
                 if args.demo_mode == "random" else
                 " (含共同alpha+异质噪声, PBO 应明显低于随机基线)"))
        R = make_demo(args.demo_mode, args.demo_tracks, args.demo_days)
        res = cscv_pbo(R, splits=args.splits, metric=args.metric)
        pbo = res["pbo"]
        if args.demo_mode == "random":
            # 零假设下单矩阵 PBO 有显著抽样波动: IS-best 高度集中于同一条
            # "幸运"轨道, lambda 间强相关, 有效样本量远小于组合数(多种子
            # 标准差≈0.09)。因此自测容差取 [-0.20, +0.20] 而非组合数标准误。
            ok = 0.30 <= pbo <= 0.70
            print(f"[自测] 随机轨道 PBO={pbo:.4f}, 容差[0.30,0.70] -> "
                  f"{'PASS' if ok else 'FAIL'}")
            print("       注: 单矩阵 PBO 波动大属正常(见上); 多次独立生成轨道"
                  "后均值应收敛到≈0.48-0.5。")
        else:
            ok = pbo < 0.35
            verdict = "PASS" if ok else "FAIL"
            print(f"[自测] signal 模式 PBO={pbo:.4f} "
                  f"(<0.35 为 PASS, 应显著低于随机基线) -> {verdict}")
        return 0 if ok else 1

    if not args.input and not args.input_dir:
        ap.error("须提供 --input / --input-dir / --demo 之一")
    if args.input_dir:
        R = load_matrix_dir(args.input_dir)
    else:
        R = load_matrix(args.input)
    print(f"[load] {args.input}: {R.shape[0]} 期 x {R.shape[1]} 轨道")
    if R.shape[1] == 1 or args.single_vs_noise:
        if R.shape[1] != 1 and args.single_vs_noise:
            print("[warn] --single-vs-noise 只使用第一列作为真实轨道")
        single_vs_noise(R.iloc[:, [0]], n_noise=args.n_noise, splits=args.splits)
    else:
        cscv_pbo(R, splits=args.splits, metric=args.metric)
    return 0


if __name__ == "__main__":
    sys.exit(main())
