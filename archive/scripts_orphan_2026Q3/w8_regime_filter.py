# -*- coding: utf-8 -*-
"""
W8: 市场状态防御 - regime filter 对比实验
背景: W7 发现 9 个月正超额微弱 (+1.1%), 2024Q4+2025Q1 下跌市跑输 (-3.8%),
      回撤 -20.1% vs 等权 -12.4%。模型信号有效 (三季 IC 全正), 问题在执行层缺防御。
设计 (复用 W7 三段滚动信号, 保守口径 shift1 + T+1 开盘成交, TopK=20):
  1. Baseline   : risk_degree 固定 0.95 (= W7)
  2. MA20 filter: 沪深300等权净值 < 20日均线 -> 0 仓, 否则 0.95
  3. VolTgt     : 目标年化波动 15%, 仓位 = 0.15/实际波动率 (20日), 封顶 0.95
  regime 只用 T 日收盘信息, 在 T+1 执行 (与信号 shift1 对齐, 无前视)
"""
import os, sys, time, json
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"
import qlib
from qlib.constant import REG_CN
import pandas as pd, numpy as np

QLIB_BIN = r"D:\workbuddyspace\qlib_data\qlib_bin"
OUT_DIR = r"D:\zcodesj\w8_results"
os.makedirs(OUT_DIR, exist_ok=True)

qlib.init(provider_uri=QLIB_BIN, region=REG_CN, kernels=1)

from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset import DatasetH
from qlib.contrib.model.catboost_model import CatBoostModel
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.data import D

t_global = time.time()


class RegimeTopkStrategy(TopkDropoutStrategy):
    """动态风险度策略: 每日按 regime 序列调整目标仓位 (Market timing)"""

    def __init__(self, regime: pd.Series = None, default_risk_degree: float = 0.95, **kwargs):
        super().__init__(**kwargs)
        self.regime = regime          # index=决策日, value=risk_degree (0~1)
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


def build_ds_and_predict(tr_start, train_end, vp_start, test_start, test_end):
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
    return model.predict(ds, segment="test")


def build_ew_nav(start, end):
    """csi300 等权组合净值序列"""
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
    """MultiIndex 的 datetime 层后移 n 个交易日"""
    cal = np.array(D.calendar(freq="day"), dtype="datetime64[ns]")
    dates = idx.get_level_values("datetime").values.astype("datetime64[ns]")
    pos = np.searchsorted(cal, dates, side="right") + (n_days - 1)
    valid = pos < len(cal)
    next_dates = pd.DatetimeIndex(cal[pos[valid]])
    new_idx = pd.MultiIndex.from_arrays(
        [next_dates, idx.get_level_values("instrument")[valid]], names=idx.names)
    return valid, new_idx


def build_regime_series(nav, mode="ma20", target_vol=0.15):
    """由等权净值构建 regime 序列 (index=等权净值日期 = 信号生成日 T)
    使用时需 shift 到决策日 T+1"""
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
    return regime.fillna(0.0)  # 数据不足时保守 0 仓


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
        "account_series": acc,   # 供分段分析
    }


def seg_analysis(acc, ew_nav, segs):
    """分段收益分析: 组合 vs 等权"""
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
    segments = [
        ("2019-10-01", "2024-09-30", "2024-04-01", "2024-10-01", "2024-12-31"),
        ("2020-01-01", "2024-12-31", "2024-07-01", "2025-01-01", "2025-03-31"),
        ("2020-04-01", "2025-03-31", "2024-10-01", "2025-04-01", "2025-06-30"),
    ]
    bt_start = "2024-10-01"
    bt_end = "2025-06-30"

    print("=" * 70)
    print("W8: 市场状态防御 (regime filter 对比)")
    print(f"    回测: {bt_start} ~ {bt_end}, TopK=20, shift1 + T+1开盘")
    print("=" * 70)

    # 1. 滚动训练 (复用 W7 三段; 不缓存 parquet, 避免读回索引错位)
    all_preds = []
    for i, (tr_start, train_end, vp_start, test_start, test_end) in enumerate(segments):
        t0 = time.time()
        print(f"\n[1.{i+1}] 训练: {tr_start}~{train_end}, 预测: {test_start}~{test_end}")
        pred = build_ds_and_predict(tr_start, train_end, vp_start, test_start, test_end)
        all_preds.append(pred)
        print(f"    完成 [{time.time()-t0:.0f}s]")
    signal = pd.concat(all_preds)
    print(f"\n信号总行数: {len(signal)}")

    # 2. shift 信号到决策日
    print(f"\n[2] 信号 shift1 -> 决策日...")
    valid, shifted_idx = shift_index(signal.index)
    signal_d = signal.iloc[valid].copy()
    signal_d.index = shifted_idx
    print(f"    决策日信号: {len(signal_d)} 行")

    # 3. benchmark 净值 + regime 序列
    print(f"\n[3] csi300 等权净值 + regime...")
    ew_nav = build_ew_nav(bt_start, bt_end)
    ew_total = float(ew_nav.iloc[-1] - 1)

    # regime 序列: index=信号生成日(T), 需 shift 到决策日(T+1)
    regime_raw_ma = build_regime_series(ew_nav, "ma20")
    regime_raw_vol = build_regime_series(ew_nav, "vol", target_vol=0.15)
    regime_raw_mv = build_regime_series(ew_nav, "ma20_vol", target_vol=0.15)

    # shift regime 到决策日: regime 的 index (T) 对应决策日 T+1
    # 简化: regime 序列直接按决策日索引重建 (决策日 = 下一交易日)
    def regime_to_decision_day(regime_raw):
        cal = np.array(D.calendar(freq="day"), dtype="datetime64[ns]")
        dates = regime_raw.index.values.astype("datetime64[ns]")
        pos = np.searchsorted(cal, dates, side="right")
        valid_m = pos < len(cal)
        return pd.Series(regime_raw.values[valid_m],
                         index=pd.DatetimeIndex(cal[pos[valid_m]]), dtype=float)

    regime_ma = regime_to_decision_day(regime_raw_ma)
    regime_vol = regime_to_decision_day(regime_raw_vol)
    regime_mv = regime_to_decision_day(regime_raw_mv)
    print(f"    MA20 regime: 弱势日占比 {float((regime_ma==0).mean()):.2%}")
    print(f"    VolTgt regime: 平均仓位 {float(regime_vol.mean()):.2f}")

    # 4. 三个方案回测
    results = {}
    for name, regime in [("baseline", None), ("MA20", regime_ma),
                         ("VolTgt15", regime_vol), ("MA20+Vol", regime_mv)]:
        print(f"\n[4] 回测: {name} ...")
        t0 = time.time()
        r = run_backtest(signal_d, bt_start, bt_end, regime=regime)
        r["excess_vs_ew"] = round(r["total_return"] - ew_total, 4)
        r["segs"] = seg_analysis(r.pop("account_series"), ew_nav, {
            "2024Q4": ("2024-10-01", "2024-12-31"),
            "2025Q1": ("2025-01-01", "2025-03-31"),
            "2025Q2": ("2025-04-01", "2025-06-30"),
        })
        results[name] = r
        print(f"    总收益={r['total_return']} 年化={r['annualized_return']} "
              f"夏普={r['sharpe']} 回撤={r['max_drawdown']} 超额={r['excess_vs_ew']} "
              f"[{time.time()-t0:.0f}s]")

    # 5. 保存
    out = os.path.join(OUT_DIR, "w8_results.json")
    dump = {k: {kk: vv for kk, vv in v.items()} for k, v in results.items()}
    dump["benchmark_ew_total"] = round(ew_total, 4)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")

    # 6. 汇总
    print("\n" + "=" * 70)
    print("W8 汇总 (2024-10 ~ 2025-06, 9个月, 保守口径)")
    print("=" * 70)
    print(f"csi300 等权: {ew_total:+.4f}")
    print(f"{'方案':<12} {'总收益':>8} {'年化':>8} {'夏普':>6} {'回撤':>8} {'超额':>8} {'Q4超额':>7} {'Q1超额':>7} {'Q2超额':>7}")
    for name, r in results.items():
        s = {x["seg"]: x["excess"] for x in r["segs"]}
        print(f"{name:<12} {r['total_return']:>+8.4f} {str(r['annualized_return']):>8} "
              f"{str(r['sharpe']):>6} {r['max_drawdown']:>+8.4f} {r['excess_vs_ew']:>+8.4f} "
              f"{s.get('2024Q4','?'):>+7.4f} {s.get('2025Q1','?'):>+7.4f} {s.get('2025Q2','?'):>+7.4f}")

    # 判定: 防御方案是否优于 baseline
    base = results["baseline"]
    best = max(results.items(), key=lambda kv: kv[1]["excess_vs_ew"])
    print(f"\n*** 最佳防御: {best[0]} 超额={best[1]['excess_vs_ew']:+.4f} "
          f"(baseline {base['excess_vs_ew']:+.4f}) ***")
    if best[0] != "baseline" and best[1]["excess_vs_ew"] > base["excess_vs_ew"]:
        print(f"    防御有效: 超额提升 {best[1]['excess_vs_ew'] - base['excess_vs_ew']:+.4f}")
        sys.exit(0)
    else:
        print(f"    防御无效或未超越 baseline")
        sys.exit(1)


if __name__ == "__main__":
    main()
    print(f"\n总耗时: {time.time()-t_global:.0f}s")
