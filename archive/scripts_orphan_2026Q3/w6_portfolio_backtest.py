# -*- coding: utf-8 -*-
"""
W6: 组合层终审 - qlib TopkDropoutStrategy 回测
目的: 验证 RankIC=0.0412 模型在真实组合层面 (Top-K + 成本 + 涨跌停约束) 是否正期望
设计:
  - 模型: Alpha158 + CatBoost (qlib 官方, W1 最优配置)
  - 回测段: 2025-04-01 ~ 2025-06-30 (与 IC 测试段一致, 样本外)
  - TopK: 10 / 20 / 50, n_drop = max(1, topk//10)
  - 成本: 买入 5bp, 卖出 10bp, 最低 5 元; 一手 100 股
  - 涨跌停: 9.5% 阈值, 涨停禁买跌停禁卖
  - benchmark: csi300 等权组合 (自建)
"""
import os, sys, time, json
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.constant import REG_CN
import pandas as pd, numpy as np

QLIB_BIN = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\w6_results"
os.makedirs(OUT_DIR, exist_ok=True)

qlib.init(provider_uri=QLIB_BIN, region=REG_CN, kernels=1)

from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset import DatasetH
from qlib.contrib.model.catboost_model import CatBoostModel
from qlib.data import D

t_global = time.time()


def build_ds(tr_start, train_end, vp_start, test_start, test_end,
             instruments="csi300"):
    handler = Alpha158(
        instruments=instruments,
        start_time=tr_start, end_time=test_end,
        fit_start_time=tr_start, fit_end_time=train_end,
        label=(["Ref($close,-1)/$close-1"], ["LABEL0"]),
        infer_processors=[
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        learn_processors=[{"class": "DropnaLabel"}],
    )
    return DatasetH(handler=handler, segments={
        "train": (tr_start, train_end),
        "valid": (vp_start, train_end),
        "test":  (test_start, test_end),
    })


def build_ew_benchmark(start, end, instruments="csi300"):
    """csi300 等权组合日收益 (自建 benchmark)"""
    codes = D.instruments(instruments)
    inst_list = [c[0] for c in codes] if isinstance(codes, list) else codes
    close = D.features(inst_list, ["$close"], start_time=start, end_time=end,
                       freq="day")
    close = close.groupby(level="instrument").ffill()
    # 等权日收益: 每日各股 close 变化均值
    ret = close.groupby(level="instrument").pct_change()
    ew = ret.groupby(level="datetime").mean().dropna()
    # 累计净值
    nav = (1 + ew).cumprod()
    return nav


def shift_signal(signal, n_days=1):
    """把 signal 的 datetime 索引后移 n 个交易日 (T日信号 -> T+n 日执行, 排除前视)"""
    from qlib.data import D
    cal = np.array(D.calendar(freq="day"), dtype="datetime64[ns]")
    idx = signal.index
    dates = idx.get_level_values("datetime").values.astype("datetime64[ns]")
    pos = np.searchsorted(cal, dates, side="right") + (n_days - 1)
    # 超出日历末端的日期丢弃
    valid = pos < len(cal)
    next_dates = pd.DatetimeIndex(cal[pos[valid]])
    new_idx = pd.MultiIndex.from_arrays(
        [next_dates, idx.get_level_values("instrument")[valid]],
        names=idx.names,
    )
    return signal.iloc[valid].copy(), new_idx


def run_backtest(signal, start, end, topk, n_drop, deal_price="close",
                 shifted_idx=None):
    """跑一次 TopkDropout 回测 (绕过顶层 backtest, 手动构造以禁用 benchmark)"""
    from qlib.backtest import backtest_loop, get_exchange
    from qlib.backtest.account import Account
    from qlib.backtest.utils import CommonInfrastructure
    from qlib.backtest.executor import SimulatorExecutor
    from qlib.contrib.strategy import TopkDropoutStrategy

    if shifted_idx is not None:
        signal = signal.copy()
        signal.index = shifted_idx

    # 绕过 create_account_instance: 显式 {"benchmark": None} 禁用 benchmark
    account = Account(init_cash=1e8, position_dict={}, pos_type="Position",
                      benchmark_config={"benchmark": None})

    exchange_kwargs = {
        "start_time": start,
        "end_time": end,
        "freq": "day",
        "limit_threshold": 0.095,
        "deal_price": deal_price,
        "open_cost": 0.0005,
        "close_cost": 0.0010,
        "min_cost": 5,
        "trade_unit": 100,
    }
    exchange = get_exchange(**exchange_kwargs)
    common = CommonInfrastructure(trade_account=account, trade_exchange=exchange)

    strategy = TopkDropoutStrategy(
        signal=signal,
        topk=topk,
        n_drop=n_drop,
        risk_degree=0.95,
        hold_thresh=1,
        only_tradable=False,
        forbid_all_trade_at_limit=True,
    )
    strategy.reset_common_infra(common)

    executor = SimulatorExecutor(
        time_per_step="day",
        generate_portfolio_metrics=True,
        common_infra=common,
    )

    portfolio_metric, indicator = backtest_loop(start, end, strategy, executor)

    # qlib 0.9.7: portfolio_metric["1day"] = (report_normal, report_custom)
    pm_1day = portfolio_metric["1day"]
    if isinstance(pm_1day, tuple):
        report_normal, _report_custom = pm_1day
    else:
        report_normal = pm_1day.get("report_normal", {})

    indicator_day = indicator["1day"]
    if isinstance(indicator_day, tuple):
        indicator_day = indicator_day[0]

    # 从 account 序列自算组合指标 (value 序列首项为 0, 不可用)
    value_series = report_normal.get("account")
    ret_series = report_normal.get("return")
    turn_series = report_normal.get("turnover")

    total_return = None
    annualized_return = None
    sharpe = None
    max_drawdown = None
    if value_series is not None and len(value_series) > 1:
        v = value_series.astype(float)
        total_return = float(v.iloc[-1] / v.iloc[0] - 1)
        daily_ret = v.pct_change().dropna()
        n_days = len(daily_ret)
        years = n_days / 244.0
        annualized_return = float((1 + total_return) ** (1 / years) - 1) if years > 0 else None
        std = float(daily_ret.std())
        sharpe = float(annualized_return / (std * np.sqrt(244))) if std and std > 0 else None
        # 最大回撤
        cummax = v.cummax()
        drawdown = (v / cummax - 1).min()
        max_drawdown = float(drawdown)

    # 换手率: 平均每日换手
    avg_turnover = 0.0
    total_turnover = 0.0
    if turn_series is not None and len(turn_series) > 0:
        t_arr = np.asarray(turn_series, dtype=float)
        avg_turnover = float(np.nanmean(t_arr))
        total_turnover = float(np.nansum(t_arr))

    total_cost = report_normal.get("total_cost")
    if isinstance(total_cost, pd.Series) and len(total_cost) > 0:
        total_cost = float(total_cost.iloc[-1])
    elif isinstance(total_cost, pd.Series):
        total_cost = None

    return {
        "topk": topk,
        "n_drop": n_drop,
        "total_return": round(total_return, 4) if total_return is not None else None,
        "annualized_return": round(annualized_return, 4) if annualized_return is not None else None,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "max_drawdown": round(max_drawdown, 4) if max_drawdown is not None else None,
        "avg_turnover": round(avg_turnover, 4),
        "total_turnover": round(total_turnover, 4),
        "total_cost": round(total_cost, 2) if total_cost is not None else None,
        "n_days": len(ret_series) if ret_series is not None else 0,
    }


def main():
    tr_start = "2020-04-01"
    train_end = "2025-03-31"
    vp_start = "2024-10-01"
    test_start = "2025-04-01"
    test_end = "2025-06-30"

    print("=" * 70)
    print("W6: 组合层终审 (TopkDropout 回测)")
    print(f"    train={tr_start}~{train_end} valid={vp_start}~{train_end}")
    print(f"    backtest={test_start}~{test_end}")
    print("=" * 70)

    # 1. 构建 + 训练 + 预测
    t0 = time.time()
    print(f"\n[1] 构建数据集 + 训练 CatBoost...")
    ds = build_ds(tr_start, train_end, vp_start, test_start, test_end)
    model = CatBoostModel(loss="RMSE", learning_rate=0.05, depth=6,
                          iterations=400, early_stopping_rounds=50)
    model.fit(ds)
    pred = model.predict(ds, segment="test")
    print(f"    pred shape: {pred.shape} [{(time.time()-t0):.0f}s]")
    print(f"    pred index sample: {pred.index[:3].tolist()}")

    # 2. IC 复现 (确认信号一致)
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
    print(f"\n[2] 信号 IC 复现: RankIC={daily_ic.mean():.4f} ({len(daily_ic)}天)")

    # 3. csi300 等权 benchmark
    print(f"\n[3] 构建 csi300 等权 benchmark...")
    ew_nav = build_ew_benchmark(test_start, test_end)
    ew_ret = ew_nav.pct_change().dropna()
    ew_total = float(ew_nav.iloc[-1] - 1)
    print(f"    csi300 等权总收益: {ew_total:.4f}")

    # 4. 三种 TopK 回测 (基准: 当日信号当日收盘成交)
    signal = pred  # MultiIndex (datetime, instrument) × score
    results = []
    for topk, n_drop in [(10, 1), (20, 2), (50, 5)]:
        print(f"\n[4] TopK={topk} 回测 (当日信号/当日收盘) ...")
        t0 = time.time()
        try:
            r = run_backtest(signal, test_start, test_end, topk, n_drop,
                             deal_price="close")
            r["label"] = f"TopK{topk}_close"
            r["benchmark_ew_total"] = round(float(ew_total), 4)
            r["excess_vs_ew"] = round(float(r.get("total_return") or 0) - float(ew_total), 4)
            results.append(r)
            print(f"    总收益={r['total_return']} 年化={r['annualized_return']} "
                  f"夏普={r['sharpe']} 回撤={r['max_drawdown']} 换手={r['avg_turnover']} "
                  f"[{(time.time()-t0):.0f}s]")
        except Exception as e:
            print(f"    回测失败: {e}")
            import traceback; traceback.print_exc()
            results.append({"topk": topk, "error": str(e)})

    # 5. 保守验证: 信号后移 1 天 (T日信号 -> T+1 执行), TopK=20
    print(f"\n[5] 保守验证: 信号后移1天, TopK=20...")
    shifted, shifted_idx = shift_signal(signal)
    for deal in ["close", "open"]:
        print(f"    shift1 + {deal} 成交...")
        try:
            r = run_backtest(shifted, test_start, test_end, 20, 2,
                             deal_price=deal, shifted_idx=shifted_idx)
            r["label"] = f"TopK20_shift1_{deal}"
            r["benchmark_ew_total"] = round(float(ew_total), 4)
            r["excess_vs_ew"] = round(float(r.get("total_return") or 0) - float(ew_total), 4)
            results.append(r)
            print(f"    总收益={r['total_return']} 年化={r['annualized_return']} "
                  f"夏普={r['sharpe']} 回撤={r['max_drawdown']} 换手={r['avg_turnover']} "
                  f"超额={r['excess_vs_ew']}")
        except Exception as e:
            print(f"    回测失败: {e}")
            import traceback; traceback.print_exc()
            results.append({"topk": 20, "deal": deal, "error": str(e)})

    # 保存
    out = os.path.join(OUT_DIR, "w6_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"signal_rank_ic": round(float(daily_ic.mean()), 4),
                   "benchmark_ew_total": round(float(ew_total), 4),
                   "backtests": results}, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")

    # 汇总
    print("\n" + "=" * 70)
    print("W6 汇总 (回测段 2025-04-01~2025-06-30)")
    print("=" * 70)
    print(f"csi300 等权 benchmark 总收益: {ew_total:.4f}")
    print(f"{'方案':<20} {'总收益':>9} {'年化':>9} {'夏普':>7} {'回撤':>9} {'换手':>7} {'成本':>8} {'超额vs等权':>10}")
    for r in results:
        if "error" in r:
            print(f"{r.get('label', str(r.get('topk','?'))):<20}  失败: {r['error'][:50]}")
        else:
            f_ = lambda v, w=9: f"{v:>{w}.4f}" if v is not None else f"{'None':>{w}}"
            s_ = lambda v: f"{v:>7.3f}" if v is not None else f"{'None':>7}"
            print(f"{r.get('label','?'):<20} {f_(r['total_return'])} {f_(r['annualized_return'])} "
                  f"{s_(r['sharpe'])} {f_(r['max_drawdown'])} {f_(r['avg_turnover'], 7)} "
                  f"{f_(r['total_cost'], 8)} {f_(r['excess_vs_ew'], 10)}")

    # 判断: 保守版(shift1)是否有正超额
    pos = [r for r in results if "error" not in r and (r.get("excess_vs_ew") or 0) > 0]
    consv = [r for r in pos if "shift1" in r.get("label", "")]
    if consv:
        best = max(consv, key=lambda r: r["excess_vs_ew"])
        print(f"\n*** 保守版正超额: {best['label']} 超额={best['excess_vs_ew']:+.4f} ***")
        print(f"    组合层验证: 排除前视偏差后 IC 0.0412 信号仍可转化为正期望组合")
        sys.exit(0)
    elif pos:
        best = max(pos, key=lambda r: r["excess_vs_ew"])
        print(f"\n*** 仅前视版正超额: {best['label']} 超额={best['excess_vs_ew']:+.4f} ***")
        print(f"    警告: 排除前视偏差后无正超额, 结果不可信")
        sys.exit(1)
    else:
        print(f"\n*** 无正超额方案 ***")
        print(f"    组合层验证: 信号未能转化为正期望, 需进一步诊断")
        sys.exit(1)


if __name__ == "__main__":
    main()
    print(f"\n总耗时: {time.time()-t_global:.0f}s")
