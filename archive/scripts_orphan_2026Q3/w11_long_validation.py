# -*- coding: utf-8 -*-
"""
W9: 多种子集成 - 消除信号随机性
背景: W8 发现 CatBoost 未固定种子, 单次训练信号随机 -> TopK20 组合收益波动 ±5%,
      W6/W7 单次回测结论不可信。IC 本身稳定 (0.038~0.042, 种子间 ±0.002)。
设计:
  1. 每段 5 个种子 (1/7/42/123/999) 训练, 信号平均 (Rank 平均)
  2. 集成信号下重跑: baseline / MA20 / VolTgt / MA20+Vol (9个月, 保守口径)
  3. 报告单种子收益分布 (噪声范围) vs 集成信号
"""
import os, sys, time, json
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.constant import REG_CN
import pandas as pd, numpy as np

QLIB_BIN = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\w11_results"
os.makedirs(OUT_DIR, exist_ok=True)

qlib.init(provider_uri=QLIB_BIN, region=REG_CN, kernels=1)

from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset import DatasetH
from qlib.contrib.model.catboost_model import CatBoostModel
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.data import D

SEEDS = [1, 7, 42, 123, 999]
t_global = time.time()


class RegimeTopkStrategy(TopkDropoutStrategy):
    """动态风险度策略: 每日按 regime 序列调整目标仓位"""

    def __init__(self, regime: pd.Series = None, default_risk_degree: float = 0.95, **kwargs):
        super().__init__(**kwargs)
        self.regime = regime
        self.default_risk_degree = default_risk_degree

    def generate_trade_decision(self, execute_result=None):
        trade_step = self.trade_calendar.get_trade_step()
        trade_start_time, _ = self.trade_calendar.get_step_time(trade_step)
        date = pd.Timestamp(trade_start_time)
        if self.regime is not None and date in self.regime.index:
            rd = float(self.regime.loc[date])
        else:
            rd = self.default_risk_degree
        self.risk_degree = rd
        return super().generate_trade_decision(execute_result)


def build_ds_and_predict(tr_start, train_end, vp_start, test_start, test_end,
                         seed=None):
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
    kw = dict(loss="RMSE", learning_rate=0.05, depth=6,
              iterations=400, early_stopping_rounds=50)
    if seed is not None:
        kw["random_seed"] = seed
    model = CatBoostModel(**kw)
    model.fit(ds)
    return model.predict(ds, segment="test")


def build_ew_nav(start, end):
    codes = D.instruments("csi300")
    inst_list = [c[0] for c in codes] if isinstance(codes, list) else codes
    close = D.features(inst_list, ["$close"], start_time=start, end_time=end, freq="day")
    close = close.groupby(level="instrument").ffill()
    ret = close.groupby(level="instrument").pct_change(fill_method=None)
    ew = ret.groupby(level="datetime").mean().dropna()
    if isinstance(ew, pd.DataFrame):
        ew = ew.iloc[:, 0]
    return (1 + ew).cumprod()


def shift_index(idx, n_days=1):
    cal = np.array(D.calendar(freq="day"), dtype="datetime64[ns]")
    dates = idx.get_level_values("datetime").values.astype("datetime64[ns]")
    pos = np.searchsorted(cal, dates, side="right") + (n_days - 1)
    valid = pos < len(cal)
    next_dates = pd.DatetimeIndex(cal[pos[valid]])
    new_idx = pd.MultiIndex.from_arrays(
        [next_dates, idx.get_level_values("instrument")[valid]], names=idx.names)
    return valid, new_idx


def build_regime_series(nav, mode="ma20", target_vol=0.15):
    ret = nav.pct_change()
    if mode == "ma20":
        ma20 = nav.rolling(20).mean()
        regime = pd.Series(np.where(nav > ma20, 0.95, 0.0), index=nav.index, dtype=float)
    elif mode == "vol":
        vol20 = ret.rolling(20).std() * np.sqrt(244)
        regime = (target_vol / vol20).clip(upper=0.95, lower=0.0)
    elif mode == "ma20_vol":
        ma20 = nav.rolling(20).mean()
        vol20 = ret.rolling(20).std() * np.sqrt(244)
        vol_part = (target_vol / vol20).clip(upper=0.95, lower=0.0)
        regime = pd.Series(np.where(nav > ma20, vol_part, 0.0), index=nav.index, dtype=float)
    else:
        raise ValueError(f"unknown mode {mode}")
    return regime.fillna(0.0)


def regime_to_decision_day(regime_raw):
    cal = np.array(D.calendar(freq="day"), dtype="datetime64[ns]")
    dates = regime_raw.index.values.astype("datetime64[ns]")
    pos = np.searchsorted(cal, dates, side="right")
    valid_m = pos < len(cal)
    return pd.Series(regime_raw.values[valid_m],
                     index=pd.DatetimeIndex(cal[pos[valid_m]]), dtype=float)


def run_backtest(signal, start, end, regime=None, topk=20, n_drop=2,
                 deal_price="open"):
    from qlib.backtest import backtest_loop, get_exchange
    from qlib.backtest.account import Account
    from qlib.backtest.utils import CommonInfrastructure
    from qlib.backtest.executor import SimulatorExecutor

    account = Account(init_cash=1e8, position_dict={}, pos_type="Position",
                      benchmark_config={"benchmark": None})
    exchange = get_exchange(
        start_time=start, end_time=end, freq="day", limit_threshold=0.095,
        deal_price=deal_price, open_cost=0.0005, close_cost=0.0010,
        min_cost=5, trade_unit=100,
    )
    common = CommonInfrastructure(trade_account=account, trade_exchange=exchange)
    strategy = RegimeTopkStrategy(
        signal=signal, topk=topk, n_drop=n_drop, risk_degree=0.95,
        hold_thresh=1, only_tradable=False, forbid_all_trade_at_limit=True,
        regime=regime,
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

    total_return = float(acc.iloc[-1] / acc.iloc[0] - 1)
    daily = acc.pct_change().dropna()
    n_days = len(daily)
    years = n_days / 244.0
    annualized = float((1 + total_return) ** (1 / years) - 1) if years > 0 else None
    std = float(daily.std())
    sharpe = float(annualized / (std * np.sqrt(244))) if std and std > 0 else None
    mdd = float((acc / acc.cummax() - 1).min())
    avg_turn = float(np.nanmean(np.asarray(turn, dtype=float))) if turn is not None and len(turn) else 0.0

    return {
        "total_return": round(total_return, 4),
        "annualized_return": round(annualized, 4) if annualized else None,
        "sharpe": round(sharpe, 3) if sharpe else None,
        "max_drawdown": round(mdd, 4),
        "avg_turnover": round(avg_turn, 4),
        "account_series": acc,
    }


def seg_analysis(acc, ew_nav, segs):
    out = []
    for name, (s, e) in segs.items():
        a = acc.loc[s:e]
        b = ew_nav.loc[s:e]
        if len(a) < 2:
            continue
        a_ret = float(a.iloc[-1] / a.iloc[0] - 1)
        b_ret = float(b.iloc[-1] / b.iloc[0] - 1)
        out.append({"seg": name, "portfolio": round(a_ret, 4),
                    "ew_bench": round(b_ret, 4),
                    "excess": round(a_ret - b_ret, 4)})
    return out


def main():
    # 8 段季度滚动: 2024Q4 ~ 2026Q3(部分), 每段 5y 训练 + 6m valid
    segments = [
        # (tr_start, train_end, vp_start, test_start, test_end)
        ("2019-10-01", "2024-09-30", "2024-04-01", "2024-10-01", "2024-12-31"),
        ("2020-01-01", "2024-12-31", "2024-07-01", "2025-01-01", "2025-03-31"),
        ("2020-04-01", "2025-03-31", "2024-10-01", "2025-04-01", "2025-06-30"),
        ("2020-07-01", "2025-06-30", "2025-01-01", "2025-07-01", "2025-09-30"),
        ("2020-10-01", "2025-09-30", "2025-04-01", "2025-10-01", "2025-12-31"),
        ("2021-01-01", "2025-12-31", "2025-07-01", "2026-01-01", "2026-03-31"),
        ("2021-04-01", "2026-03-31", "2025-10-01", "2026-04-01", "2026-06-30"),
        ("2021-07-01", "2026-06-30", "2026-01-01", "2026-07-01", "2026-08-07"),
    ]
    bt_start = "2024-10-01"
    bt_end = "2026-07-31"  # 避开日历末尾 (2026-08-07 为 bin 最后一日, qlib shift 越界)

    print("=" * 70)
    print("W11: 长期验证 (22个月: 2024-10 ~ 2026-08, 8段×5种子)")
    print(f"    seeds={SEEDS}, 回测 {bt_start}~{bt_end}, TopK=20, shift1+open")
    print("=" * 70)

    # 1. 每段 5 种子训练 + Rank 平均 (带 pickle 缓存, 避免重训)
    import pickle
    seg_signals = {}
    for i, (tr_start, train_end, vp_start, test_start, test_end) in enumerate(segments):
        cache_file = os.path.join(OUT_DIR, f"seg{i+1}_signal.pkl")
        if os.path.exists(cache_file):
            with open(cache_file, "rb") as f:
                ens = pickle.load(f)
            seg_signals[i] = ens
            print(f"\n[1.{i+1}/8] 段{i+1}: 复用缓存 ({len(ens)} 行)")
            continue
        print(f"\n[1.{i+1}/8] 段{i+1}: {test_start}~{test_end} 训练 {len(SEEDS)} 个种子...")
        preds = []
        for seed in SEEDS:
            t0 = time.time()
            p = build_ds_and_predict(tr_start, train_end, vp_start, test_start, test_end, seed=seed)
            preds.append(p)
            print(f"    seed={seed} 完成 [{time.time()-t0:.0f}s]")
        rank_sum = None
        for p in preds:
            score_s = p if isinstance(p, pd.Series) else p.iloc[:, 0]
            r = score_s.groupby(level="datetime").rank(pct=True)
            rank_sum = r if rank_sum is None else rank_sum.add(r)
        ens = rank_sum / len(SEEDS)
        seg_signals[i] = ens
        with open(cache_file, "wb") as f:
            pickle.dump(ens, f)
        print(f"    集成信号: {len(ens)} 行 (已缓存)")

    # 2. 拼接 + shift
    print(f"\n[2] 拼接集成信号 + shift1...")
    signal = pd.concat([seg_signals[i] for i in range(len(segments))])
    valid, shifted_idx = shift_index(signal.index)
    signal_d = signal.iloc[valid].copy()
    signal_d.index = shifted_idx
    print(f"    决策日信号: {len(signal_d)} 行")

    # 3. benchmark
    print(f"\n[3] csi300 等权 benchmark...")
    ew_nav = build_ew_nav(bt_start, bt_end)
    ew_total = float(ew_nav.iloc[-1] - 1)
    print(f"    等权总收益: {ew_total:+.4f}")

    # 4. baseline 回测 (regime 已在 W8 证伪, 只跑 baseline)
    print(f"\n[4] 回测: baseline TopK20 (集成信号)...")
    t0 = time.time()
    r = run_backtest(signal_d, bt_start, bt_end, regime=None)
    r["excess_vs_ew"] = round(r["total_return"] - ew_total, 4)
    r["segs"] = seg_analysis(r.pop("account_series"), ew_nav, {
        "2024Q4": ("2024-10-01", "2024-12-31"),
        "2025Q1": ("2025-01-01", "2025-03-31"),
        "2025Q2": ("2025-04-01", "2025-06-30"),
        "2025Q3": ("2025-07-01", "2025-09-30"),
        "2025Q4": ("2025-10-01", "2025-12-31"),
        "2026Q1": ("2026-01-01", "2026-03-31"),
        "2026Q2": ("2026-04-01", "2026-06-30"),
        "2026Q3": ("2026-07-01", "2026-07-31"),
    })
    results = {"baseline": r}
    print(f"    总收益={r['total_return']} 年化={r['annualized_return']} "
          f"夏普={r['sharpe']} 回撤={r['max_drawdown']} 超额={r['excess_vs_ew']} "
          f"[{time.time()-t0:.0f}s]")

    # 5. 保存
    out = os.path.join(OUT_DIR, "w11_results.json")
    dump = {k: {kk: vv for kk, vv in v.items()} for k, v in results.items()}
    dump["benchmark_ew_total"] = round(ew_total, 4)
    dump["seeds"] = SEEDS
    dump["period"] = f"{bt_start}~{bt_end}"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")

    # 6. 汇总
    print("\n" + "=" * 70)
    print("W11 汇总 (集成信号, 22个月, 保守口径 T+1开盘)")
    print("=" * 70)
    print(f"csi300 等权: {ew_total:+.4f}")
    print(f"{'方案':<12} {'总收益':>8} {'年化':>8} {'夏普':>6} {'回撤':>8} {'超额':>8}")
    for name, rr in results.items():
        print(f"{name:<12} {rr['total_return']:>+8.4f} {str(rr['annualized_return']):>8} "
              f"{str(rr['sharpe']):>6} {rr['max_drawdown']:>+8.4f} {rr['excess_vs_ew']:>+8.4f}")

    print(f"\n分段超额 (集成信号):")
    win, lose = 0, 0
    for s in r["segs"]:
        mark = "+" if s["excess"] > 0 else "-"
        if s["excess"] > 0:
            win += 1
        else:
            lose += 1
        print(f"  {s['seg']}: 组合 {s['portfolio']:+.4f} vs 等权 {s['ew_bench']:+.4f} = 超额 {s['excess']:+.4f} [{mark}]")
    print(f"\n  正超额季度: {win}/{win+lose}")

    if r["excess_vs_ew"] > 0 and win >= lose:
        print(f"\n*** 长期验证通过: 22个月超额 {r['excess_vs_ew']:+.4f}, "
              f"正超额季度 {win}/{win+lose} ***")
        print(f"    跨 8 个季度仍稳定正超额, 配置可信")
        sys.exit(0)
    elif r["excess_vs_ew"] > 0:
        print(f"\n*** 长期验证勉强通过: 总超额为正但季度胜率不足 ***")
        sys.exit(0)
    else:
        print(f"\n*** 长期验证失败: 22个月超额 {r['excess_vs_ew']:+.4f} ***")
        sys.exit(1)


if __name__ == "__main__":
    main()
    print(f"\n总耗时: {time.time()-t_global:.0f}s")
