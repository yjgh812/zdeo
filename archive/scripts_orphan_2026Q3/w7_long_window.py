# -*- coding: utf-8 -*-
"""
W7: 长窗口复验 - 9个月滚动训练 + 保守口径回测
目的: 验证 W6 的 TopK20 正超额不是 2025Q2 单一行情运气
设计:
  - 季度滚动训练 3 段 (各 5y 训练 + 6m valid + 3m 预测):
      段1: 预测 2024-10-01~2024-12-31 (train_end=2024-09-30)
      段2: 预测 2025-01-01~2025-03-31 (train_end=2024-12-31)
      段3: 预测 2025-04-01~2025-06-30 (train_end=2025-03-31, = W6)
  - 回测: TopK=20, 保守口径 (信号 shift 1 天 + T+1 开盘价成交)
  - benchmark: csi300 等权 (同期 9 个月)
"""
import os, sys, time, json
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.constant import REG_CN
import pandas as pd, numpy as np

QLIB_BIN = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\w7_results"
os.makedirs(OUT_DIR, exist_ok=True)

qlib.init(provider_uri=QLIB_BIN, region=REG_CN, kernels=1)

from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset import DatasetH
from qlib.contrib.model.catboost_model import CatBoostModel
from qlib.data import D

t_global = time.time()


def build_ds_and_predict(tr_start, train_end, vp_start, test_start, test_end):
    """构建 DatasetH + 训练 + 预测, 返回 pred (MultiIndex) 和 IC"""
    handler = Alpha158(
        instruments="csi300",
        start_time=tr_start, end_time=test_end,
        fit_start_time=tr_start, fit_end_time=train_end,
        label=(["Ref($close,-1)/$close-1"], ["LABEL0"]),
        infer_processors=[
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        learn_processors=[{"class": "DropnaLabel"}],
    )
    ds = DatasetH(handler=handler, segments={
        "train": (tr_start, train_end),
        "valid": (vp_start, train_end),
        "test":  (test_start, test_end),
    })
    model = CatBoostModel(loss="RMSE", learning_rate=0.05, depth=6,
                          iterations=400, early_stopping_rounds=50)
    model.fit(ds)
    pred = model.predict(ds, segment="test")

    # IC
    label_df = ds.prepare("test", col_set="label")
    if isinstance(label_df, pd.DataFrame):
        label_s = label_df.iloc[:, 0]
    else:
        label_s = label_df
    score_s = pred if isinstance(pred, pd.Series) else pred.iloc[:, 0]
    df_ic = pd.DataFrame({"score": score_s, "label": label_s}).dropna()
    daily_ic = df_ic.groupby(level="datetime").apply(
        lambda g: g["score"].corr(g["label"], method="spearman") if len(g) > 5 else np.nan
    ).dropna()
    return pred, daily_ic.mean()


def build_ew_benchmark(start, end):
    """csi300 等权组合日收益"""
    codes = D.instruments("csi300")
    inst_list = [c[0] for c in codes] if isinstance(codes, list) else codes
    close = D.features(inst_list, ["$close"], start_time=start, end_time=end, freq="day")
    close = close.groupby(level="instrument").ffill()
    ret = close.groupby(level="instrument").pct_change()
    ew = ret.groupby(level="datetime").mean().dropna()
    return (1 + ew).cumprod()


def shift_signal(signal, n_days=1):
    cal = np.array(D.calendar(freq="day"), dtype="datetime64[ns]")
    idx = signal.index
    dates = idx.get_level_values("datetime").values.astype("datetime64[ns]")
    pos = np.searchsorted(cal, dates, side="right") + (n_days - 1)
    valid = pos < len(cal)
    next_dates = pd.DatetimeIndex(cal[pos[valid]])
    new_idx = pd.MultiIndex.from_arrays(
        [next_dates, idx.get_level_values("instrument")[valid]], names=idx.names)
    return signal.iloc[valid].copy(), new_idx


def run_backtest_loop(signal, start, end, topk=20, n_drop=2, deal_price="open"):
    """手动构造回测 (无 benchmark, 保守口径)"""
    from qlib.backtest import backtest_loop, get_exchange
    from qlib.backtest.account import Account
    from qlib.backtest.utils import CommonInfrastructure
    from qlib.backtest.executor import SimulatorExecutor
    from qlib.contrib.strategy import TopkDropoutStrategy

    account = Account(init_cash=1e8, position_dict={}, pos_type="Position",
                      benchmark_config={"benchmark": None})
    exchange = get_exchange(
        start_time=start, end_time=end, freq="day", limit_threshold=0.095,
        deal_price=deal_price, open_cost=0.0005, close_cost=0.0010,
        min_cost=5, trade_unit=100,
    )
    common = CommonInfrastructure(trade_account=account, trade_exchange=exchange)
    strategy = TopkDropoutStrategy(
        signal=signal, topk=topk, n_drop=n_drop, risk_degree=0.95,
        hold_thresh=1, only_tradable=False, forbid_all_trade_at_limit=True,
    )
    strategy.reset_common_infra(common)
    executor = SimulatorExecutor(time_per_step="day",
                                 generate_portfolio_metrics=True,
                                 common_infra=common)
    pm, ind = backtest_loop(start, end, strategy, executor)

    rn = pm["1day"]
    if isinstance(rn, tuple):
        rn = rn[0]
    acc = rn.get("account")
    turn = rn.get("turnover")
    cost = rn.get("total_cost")

    total_return = float(acc.iloc[-1] / acc.iloc[0] - 1)
    daily = acc.pct_change().dropna()
    n_days = len(daily)
    years = n_days / 244.0
    annualized = float((1 + total_return) ** (1 / years) - 1) if years > 0 else None
    std = float(daily.std())
    sharpe = float(annualized / (std * np.sqrt(244))) if std and std > 0 else None
    mdd = float((acc / acc.cummax() - 1).min())
    avg_turn = float(np.nanmean(np.asarray(turn, dtype=float))) if turn is not None and len(turn) else 0.0
    total_cost = float(np.asarray(cost, dtype=float)[-1]) if cost is not None and len(cost) else 0.0

    return {
        "total_return": round(total_return, 4),
        "annualized_return": round(annualized, 4) if annualized else None,
        "sharpe": round(sharpe, 3) if sharpe else None,
        "max_drawdown": round(mdd, 4),
        "avg_turnover": round(avg_turn, 4),
        "total_cost": round(total_cost, 0),
        "n_days": int(n_days),
    }


def main():
    # 三段滚动窗口 (每段 5y 训练, 起点前移)
    segments = [
        # (tr_start, train_end, vp_start, test_start, test_end)
        ("2019-10-01", "2024-09-30", "2024-04-01", "2024-10-01", "2024-12-31"),
        ("2020-01-01", "2024-12-31", "2024-07-01", "2025-01-01", "2025-03-31"),
        ("2020-04-01", "2025-03-31", "2024-10-01", "2025-04-01", "2025-06-30"),
    ]
    bt_start = "2024-10-01"
    bt_end = "2025-06-30"

    print("=" * 70)
    print("W7: 长窗口复验 (9个月滚动训练 + 保守口径 T+1开盘)")
    print(f"    回测区间: {bt_start} ~ {bt_end}")
    print("=" * 70)

    # 1. 滚动训练预测
    all_preds = []
    seg_ics = []
    for i, (tr_start, train_end, vp_start, test_start, test_end) in enumerate(segments):
        t0 = time.time()
        print(f"\n[段{i+1}/3] 训练: {tr_start}~{train_end}, 预测: {test_start}~{test_end}")
        pred, ic = build_ds_and_predict(tr_start, train_end, vp_start, test_start, test_end)
        seg_ics.append({"segment": i + 1, "test": f"{test_start}~{test_end}",
                        "rank_ic": round(float(ic), 4), "elapsed": round(time.time() - t0)})
        all_preds.append(pred)
        print(f"    RankIC={ic:.4f} [{time.time()-t0:.0f}s]")

    signal = pd.concat(all_preds)
    print(f"\n信号总行数: {len(signal)}")

    # 2. 保守口径: shift 1 天 + open 成交
    print(f"\n[2] 保守回测: TopK=20, shift1 + open 成交...")
    shifted, shifted_idx = shift_signal(signal)
    t0 = time.time()
    result = run_backtest_loop(shifted, bt_start, bt_end, topk=20, n_drop=2,
                               deal_price="open")
    result["elapsed"] = round(time.time() - t0)
    print(f"    总收益={result['total_return']} 年化={result['annualized_return']} "
          f"夏普={result['sharpe']} 回撤={result['max_drawdown']} "
          f"换手={result['avg_turnover']} [{result['elapsed']}s]")

    # 3. benchmark
    print(f"\n[3] csi300 等权 benchmark (同期 9 个月)...")
    ew_nav = build_ew_benchmark(bt_start, bt_end)
    ew_total = float(ew_nav.iloc[-1] - 1)
    ew_daily = ew_nav.pct_change().dropna()
    ew_years = len(ew_daily) / 244.0
    ew_ann = float((1 + ew_total) ** (1 / ew_years) - 1)
    ew_mdd = float((ew_nav / ew_nav.cummax() - 1).min())
    print(f"    等权总收益={ew_total:.4f} 年化={ew_ann:.4f} 回撤={ew_mdd:.4f}")

    # 4. 保存 + 汇总
    result["excess_vs_ew"] = round(result["total_return"] - ew_total, 4)
    out = os.path.join(OUT_DIR, "w7_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"segments": seg_ics, "benchmark_ew": {
                       "total_return": round(ew_total, 4),
                       "annualized": round(ew_ann, 4), "mdd": round(ew_mdd, 4)},
                   "backtest": result}, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")

    print("\n" + "=" * 70)
    print("W7 汇总 (2024-10-01 ~ 2025-06-30, 9个月)")
    print("=" * 70)
    print(f"{'指标':<16} {'TopK20 保守版':>14} {'csi300等权':>12}")
    print(f"{'总收益':<16} {result['total_return']:>14.4f} {ew_total:>12.4f}")
    print(f"{'年化':<16} {str(result['annualized_return']):>14} {ew_ann:>12.4f}")
    print(f"{'夏普':<16} {str(result['sharpe']):>14} {'--':>12}")
    print(f"{'最大回撤':<16} {result['max_drawdown']:>14.4f} {ew_mdd:>12.4f}")
    print(f"{'超额vs等权':<16} {result['excess_vs_ew']:>14.4f} {'--':>12}")
    print(f"\n各段 RankIC: " + ", ".join(f"段{s['segment']}={s['rank_ic']}" for s in seg_ics))

    if result["excess_vs_ew"] > 0:
        print(f"\n*** 长窗口复验通过: 9个月正超额 {result['excess_vs_ew']:+.4f} ***")
        print(f"    TopK20 + 保守口径在 9 个月跨行情区间稳定正超额")
        sys.exit(0)
    else:
        print(f"\n*** 长窗口复验失败: 9个月超额 {result['excess_vs_ew']:+.4f} ***")
        print(f"    W6 的正超额可能是 2025Q2 单一行情运气")
        sys.exit(1)


if __name__ == "__main__":
    main()
    print(f"\n总耗时: {time.time()-t_global:.0f}s")
