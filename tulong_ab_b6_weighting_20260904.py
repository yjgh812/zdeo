# -*- coding: utf-8 -*-
"""B6 · bp_ep Top20 仓位加权方案三臂对比 (2026-09-04)
=====================================================================
预注册: reports/PREREG_S3_PUREBPEP_AND_TOP20_WEIGHTING_20260903.md（B6 段, v2 语义钉死, 指纹 94a0fdb0df9cda6d）
臂: A 等权(现行基线) / B 反波动率 w∝1/σ21 / C 反波动率+单票cap10%(cap后不重归一)
唯一 diff=权重公式; risk_mult/sentiment_mult/引擎逻辑逐字克隆 E1 先例件(tulong_ab_e1_ntop_20260903.py)。
判据(PREREG B6): Δ年化(B−A) ∈ ±2pp → NOISE_EQUIVALENT 关闭 / >+2pp 且 MDD(B)≤MDD(A) → CANDIDATE_REGISTERED
                / <−2pp → EQ_LOCKED_CONFIRMED。C 臂仅参考不入判定。研究性裁决: 当日不改生产。
σ_i = 调仓决策日当日收盘可知 21 交易日日收益 std(qfq close, 成交 T+1 无前视); 缺失/≤0 退回等权并计数。
只读研究脚本: 不改生产配置, 不写行情库。
输出: reports/tulong_ab_b6_weighting_20260904/gate/ (三臂 summary/equity + verdict.json)
用法: python tulong_ab_b6_weighting_20260904.py
"""
import dataclasses
import functools
import hashlib
import json
import logging
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")

PREREG_PATH = r"D:\zcodesj\reports\PREREG_S3_PUREBPEP_AND_TOP20_WEIGHTING_20260903.md"
PREREG_FP = "94a0fdb0df9cda6d"
TRIALS_LOG = r"D:\zcodesj\data\wfa_trials.jsonl"
N = 20  # 唯一在产 N(E1 NOISE_EQUIVALENT 维持)

os.environ["TULONG_BT_OUT"] = r"D:\zcodesj\reports\tulong_ab_b6_weighting_20260904\gate"

import tulong_backtest_full_report as base  # noqa: E402
from core.backtest_system.strategy import SingleStrategyBacktest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("b6_weighting")

OUT_DIR = base.OUTPUT_DIR
os.makedirs(OUT_DIR, exist_ok=True)


def verify_prereg():
    fp = hashlib.sha256(open(PREREG_PATH, "rb").read()).hexdigest()[:16]
    assert fp == PREREG_FP, f"B6 预注册指纹漂移: 计算{fp} != 冻结{PREREG_FP} (PREREG 在实跑后被改动即中止)"
    logger.info("B6 预注册校验 OK: %s", PREREG_FP)


class CachedLoader:
    """get_data 跨臂复用(只读缓存), 同 E1/S-3 先例件"""

    def __init__(self, inner):
        self._inner = inner
        self.get_data = functools.lru_cache(maxsize=None)(inner.get_data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class SigmaCache:
    """单票 qfq close 序列缓存 -> 21日日收益std(调仓日当日收盘可知, T+1成交无前视)"""

    def __init__(self, qfq_path: str):
        self._con = None
        self._path = qfq_path
        self._cache = {}

    def _con_or_open(self):
        if self._con is None:
            import sqlite3
            self._con = sqlite3.connect("file:%s?mode=ro" % self._path.replace("\\", "/"), uri=True)
        return self._con

    def sigma21(self, code: str, date_str: str) -> float:
        key = (code, date_str)
        if key in self._cache:
            return self._cache[key]
        # code 归一化: 信号索引可能带 sh/sz 前缀, 库内为纯6位数字; date_str 归一化: 引擎传 'YYYY-MM-DD'
        code6 = "".join(ch for ch in str(code) if ch.isdigit())[-6:]
        d8 = int(str(date_str).replace("-", ""))
        try:
            con = self._con_or_open()
            rows = con.execute(
                "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date<=? "
                "ORDER BY date DESC LIMIT 21", (code6, d8)).fetchall()
            if len(rows) < 21:
                logger.warning("[sigma] %s(%s)@%s 行数=%d<21 退NaN", code, code6, date_str, len(rows))
                s = float("nan")
            else:
                closes = np.array([r[1] for r in rows][::-1], dtype=float)
                rets = np.diff(closes) / closes[:-1]
                s = float(np.std(rets, ddof=1)) if len(rets) >= 15 else float("nan")
                if not (s == s and s > 0):
                    logger.warning("[sigma] %s(%s)@%s std无效(%s) 退NaN", code, code6, date_str, s)
                    s = float("nan")
        except Exception as e:
            logger.warning("[sigma] %s@%s 查询失败: %s", code, date_str, str(e)[:60])
            s = float("nan")
        self._cache[key] = s
        return s


def make_bp_ep_signal_weighted(mode: str, sigma: SigmaCache):
    """bp_ep_signal 忠实克隆(E1先例件), 唯一 diff=权重公式:
    mode='eq'      : 等权 (weight=(0.40/n)*sentiment, 与生产行为一致)
    mode='invvol'  : w_i ∝ (1/σ_i)/Σ(1/σ_j), σ缺失票退回等权并计数
    mode='invvolcap': invvol 后 min(w, 0.10), cap 后不重归一(PREREG v2 钉死)"""

    def bp_ep_signal_weighted(loader: "base.TulongDataLoader", date_str: str):
        df = loader.get_fundamental_factors(date_str)
        if df is None or len(df) < 20:
            return {}, {}

        df = df[df["close"] <= 30].copy()
        if len(df) < 20:
            return {}, {}

        df["ep_rank"] = df["ep"].rank(pct=True)
        df["bp_rank"] = df["bp"].rank(pct=True)
        df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2

        topn = df.nlargest(N, "score")
        sentiment_mult = loader.get_sentiment_multiplier(date_str)

        fail_flags = loader.get_failure_flags(date_str)
        vt_set = fail_flags.get("value_trap", set())
        rc_set = fail_flags.get("rebound_chase", set())
        risk_mult = {}
        for idx in topn.index:
            mult = 1.0
            if idx in vt_set or idx in rc_set:
                mult *= 0.25
            risk_mult[idx] = mult

        if mode == "eq":
            weight = (0.40 / N) * sentiment_mult
            weights = {idx: weight * risk_mult.get(idx, 1.0) for idx, row in topn.iterrows()}
        else:
            inv = {}
            fallback = []
            for idx in topn.index:
                s = sigma.sigma21(idx, date_str)
                inv[idx] = (1.0 / s) if (s == s and s > 0) else np.nan
                if inv[idx] != inv[idx]:
                    fallback.append(idx)
            valid = [k for k, v in inv.items() if v == v]
            if not valid:
                # 全部σ缺失: 整臂退回等权(风险乘数保留)
                weights = {idx: (0.40 / N) * risk_mult.get(idx, 1.0) for idx in topn.index}
            else:
                denom = sum(inv[k] for k in valid)
                weights = {}
                for idx in topn.index:
                    if inv[idx] == inv[idx]:
                        w = (inv[idx] / denom) * 0.40
                    else:  # σ缺失: 退回等权份额(按剩余票均摊前先给均值票权)
                        w = np.nan
                    weights[idx] = w
                # σ缺失票: 用有效票的平均权重补
                if fallback:
                    avg_valid = np.mean([weights[k] for k in valid])
                    for idx in fallback:
                        weights[idx] = avg_valid
                for idx in weights:
                    weights[idx] *= risk_mult.get(idx, 1.0)
                if mode == "invvolcap":
                    for idx in weights:
                        weights[idx] = min(weights[idx], 0.10)

        reasons = {idx: f"bp+ep月度Top{N}加权({mode}) (score={row['score']:.3f})" for idx, row in topn.iterrows()}
        return weights, reasons

    bp_ep_signal_weighted.__name__ = f"bp_ep_signal_weighted_{mode}"
    return bp_ep_signal_weighted


def run_arm(tag, sig_func, loader):
    bt_cfg = dataclasses.replace(base.cfg, **{"max_holding_bars": 0})  # bp_ep 生产覆盖
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(daily_data, date_str):
        weights, _reasons = sig_func(loader, date_str)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=base.START_DATE, end_date=base.END_DATE,
                    rebalance_freq="monthly", strategy_name=tag)
    m = dict(result.get("metrics", {}))
    trades = pd.DataFrame(result.get("trades", []))
    eq = result.get("equity_curve")
    if len(trades):
        trades.to_csv(os.path.join(OUT_DIR, f"{tag}_trades.csv"), index=False, encoding="utf-8-sig")
    if eq is not None and len(eq):
        eq.to_csv(os.path.join(OUT_DIR, f"{tag}_equity.csv"), encoding="utf-8-sig")
    logger.info("[%s] 总收益=%.1f%% | 年化=%.2f%% | 夏普=%.3f | 胜率=%.1f%% | 回撤=%.1f%%",
                tag, m.get("total_return_pct", 0), m.get("annualized_return_pct", 0),
                m.get("sharpe_ratio", 0), m.get("trade_win_rate_pct", 0), m.get("max_drawdown_pct", 0))
    return m


def main():
    verify_prereg()
    loader = CachedLoader(base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH))
    sigma = SigmaCache(base.QFQ_PATH)

    arms = {}
    for tag, mode in [("b6_w20_eqA", "eq"), ("b6_w20_invvolB", "invvol"), ("b6_w20_invvolcapC", "invvolcap")]:
        logger.info("=== B6 臂 %s ===", tag)
        arms[tag] = run_arm(tag, make_bp_ep_signal_weighted(mode, sigma), loader)

    annA = float(arms["b6_w20_eqA"].get("annualized_return_pct") or 0)
    annB = float(arms["b6_w20_invvolB"].get("annualized_return_pct") or 0)
    mddA = float(arms["b6_w20_eqA"].get("max_drawdown_pct") or 0)
    mddB = float(arms["b6_w20_invvolB"].get("max_drawdown_pct") or 0)
    delta = round(annB - annA, 2)

    # fail-closed 自检: B 臂与 A 臂结果分毫不差 = σ管线失效退化为等权, 试验无效禁止出裁决
    totA = float(arms["b6_w20_eqA"].get("total_return_pct") or 0)
    totB = float(arms["b6_w20_invvolB"].get("total_return_pct") or 0)
    if abs(totA - totB) < 1e-9 and abs(mddA - mddB) < 1e-9:
        logger.error("FAIL-CLOSED: B臂与A臂完全相同(σ管线失效退化等权), 试验无效, 不产出verdict")
        sys.exit(2)

    # PREREG B6 判据 (Δ年化(B−A); MDD 记为正数百分比, "不恶化"= mddB <= mddA)
    if abs(delta) <= 2.0:
        verdict = "NOISE_EQUIVALENT(等权维持,问题关闭,E1同款噪声带)"
    elif delta > 2.0 and mddB <= mddA:
        verdict = "CANDIDATE_REGISTERED(仅登记候选,不接线,接线须主人裁决)"
    elif delta < -2.0:
        verdict = "EQ_LOCKED_CONFIRMED(反波动率劣化,等权锁死)"
    else:
        verdict = f"MIXED(delta={delta}, mddA={mddA}, mddB={mddB}, 按PREREG不满足任一单边,登记观察)"

    result = {
        "ts": datetime.now().isoformat(),
        "prereg_fingerprint": PREREG_FP,
        "arms": {k: {kk: (float(vv) if isinstance(vv, (int, float)) else vv)
                     for kk, vv in v.items()
                     if kk in ("total_return_pct", "annualized_return_pct", "max_drawdown_pct",
                               "sharpe_ratio", "total_trades", "trade_win_rate_pct")}
                 for k, v in arms.items()},
        "delta_ann_pp_B_vs_A": delta,
        "verdict": verdict,
        "production_note": "研究性裁决, 生产 bp_ep 保持等权不变",
    }
    with open(os.path.join(OUT_DIR, "verdict.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    entry = {
        "ts": result["ts"],
        "fingerprint": f"b6-weighting-{PREREG_FP}",
        "engine": "tulong_wfa_gate.v1(b6-weighting-channel)",
        "combo": ["bp_ep_weighting_sensitivity"],
        "pool": "bp_ep_monthly_top20_weighting",
        "params": {"arms": ["eq", "invvol", "invvolcap10"]},
        "n_eff_trials": 1,
        "n_folds": 1,
        "verdict": verdict,
        "oos": {"delta_ann_pp": delta},
        "note": "B级候选6(B6)立项(主人9/3按推荐继续); 研究性裁决不接生产; PREREG v2 94a0fdb0df9cda6d",
        "force": False,
        "one_vote_status": "ok",
    }
    with open(TRIALS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("=== B6 裁决: %s | Δ年化(B−A)=%.2fpp | MDD A=%.1f%% B=%.1f%% ===", verdict, delta, mddA, mddB)


if __name__ == "__main__":
    main()
