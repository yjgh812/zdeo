# -*- coding: utf-8 -*-
"""E1 n 值敏感性三臂回测 (2026-09-03)
=====================================================================
预注册: reports/tulong_ab_e1_ntop_20260903/PREREG.json（规范化指纹 1b5d52a801a2eab4, trial_budget=1）
臂: bp_ep TopN ∈ {20(in-run 基线), 30, 50} | 判据: R1 噪声(±2pp 内) / R2 候选登记(>+2pp) / R3 劣化(<-2pp)
研究性裁决: 无论结果当日不改生产(改 R3 需宪法修订, V2 计划原文)。
TopN15 不入本试验(S-3 已证伪)。装置克隆 tulong_ab_s3_topn_sweep_20260902.py 先例件。
只读研究脚本: 不改生产配置, 不写行情库。
输出: reports/tulong_ab_e1_ntop_20260903/gate/ (三臂标准三件套 + verdict.json)
用法: python tulong_ab_e1_ntop_20260903.py [--from-disk]
"""
import argparse
import dataclasses
import functools
import hashlib
import json
import logging
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")

PREREG_PATH = r"D:\zcodesj\reports\tulong_ab_e1_ntop_20260903\PREREG.json"
PREREG_FP = "1b5d52a801a2eab4"
TRIALS_LOG = r"D:\zcodesj\data\wfa_trials.jsonl"

os.environ["TULONG_BT_OUT"] = r"D:\zcodesj\reports\tulong_ab_e1_ntop_20260903\gate"

import tulong_backtest_full_report as base  # noqa: E402
from core.backtest_system.strategy import SingleStrategyBacktest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("e1_ntop")

OUT_DIR = base.OUTPUT_DIR
os.makedirs(OUT_DIR, exist_ok=True)


def verify_prereg():
    p = json.load(open(PREREG_PATH, encoding="utf-8"))
    claimed = p.pop("fingerprint", None)
    canonical = json.dumps(p, ensure_ascii=False, sort_keys=True, indent=1)
    fp = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    assert fp == PREREG_FP == claimed, f"E1 预注册指纹漂移: 计算{fp} != 冻结{PREREG_FP} != 文件{claimed}"
    assert p["status"] in ("preregistered", "completed") and p["trial_budget"] == 1
    logger.info("E1 预注册校验 OK: %s", PREREG_FP)
    return p


class CachedLoader:
    """get_data 跨臂复用(只读缓存), 同 S-3 先例件"""

    def __init__(self, inner):
        self._inner = inner
        self.get_data = functools.lru_cache(maxsize=None)(inner.get_data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def make_bp_ep_signal_topn(n: int):
    """bp_ep_signal 忠实参数化副本, 唯一 diff=选取数 20→n（逐字克隆 S-3 sweep 先例件）"""

    def bp_ep_signal_topn(loader: "base.TulongDataLoader", date_str: str):
        df = loader.get_fundamental_factors(date_str)
        if df is None or len(df) < 20:
            return {}, {}

        df = df[df["close"] <= 30].copy()
        if len(df) < 20:
            return {}, {}

        df["ep_rank"] = df["ep"].rank(pct=True)
        df["bp_rank"] = df["bp"].rank(pct=True)
        df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2

        _sec_cap = float(os.environ.get("TULONG_BPEP_SECTOR_CAP", "0") or 0)
        _sec_mom = int(os.environ.get("TULONG_BPEP_SECTOR_MOM", "0") or 0)
        if _sec_mom > 0:
            topn = base._sector_mom_topn(df, n, _sec_mom, date_str)
        elif _sec_cap > 0:
            topn = base._capped_topn(df, n, _sec_cap)
        else:
            topn = df.nlargest(n, "score")  # 唯一核心 diff
        sentiment_mult = loader.get_sentiment_multiplier(date_str)

        fail_flags = loader.get_failure_flags(date_str)
        vt_set = fail_flags.get("value_trap", set())
        rc_set = fail_flags.get("rebound_chase", set())
        l3_blocked = 0
        risk_mult = {}
        for idx in topn.index:
            mult = 1.0
            if idx in vt_set or idx in rc_set:
                mult *= 0.25
                l3_blocked += 1
            risk_mult[idx] = mult

        weight = (0.40 / n) * sentiment_mult
        weights = {idx: weight * risk_mult.get(idx, 1.0) for idx, row in topn.iterrows()}
        reasons = {idx: f"bp+ep月度调仓Top{n} (score={row['score']:.3f})" for idx, row in topn.iterrows()}
        return weights, reasons

    bp_ep_signal_topn.__name__ = f"bp_ep_signal_topn{n}"
    return bp_ep_signal_topn


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-disk", action="store_true", help="从 gate 目录产物再生裁决(判据修复用)")
    args = ap.parse_args()
    prereg = verify_prereg()

    if args.from_disk:
        arms = {}
        for n in (20, 30, 50):
            sm = json.load(open(os.path.join(OUT_DIR, f"bp_ep_topn{n}_summary.json"), encoding="utf-8"))["summary"]
            arms[f"bp_ep_topn{n}"] = sm
    else:
        loader = CachedLoader(base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH))
        arms = {}
        for n in (20, 30, 50):
            tag = f"bp_ep_topn{n}"
            logger.info("=== E1 臂 %s ===", tag)
            arms[tag] = run_arm(tag, make_bp_ep_signal_topn(n), loader)

    ann20 = float(arms["bp_ep_topn20"].get("annualized_return_pct") or 0)
    deltas = {}
    for n in (30, 50):
        ann_n = float(arms[f"bp_ep_topn{n}"].get("annualized_return_pct") or 0)
        deltas[f"topn{n}"] = round(ann_n - ann20, 2)

    r1 = bool(all(abs(d) <= 2.0 for d in deltas.values()))
    r2 = bool(any(d > 2.0 for d in deltas.values()))
    r3 = bool(any(d < -2.0 for d in deltas.values()))
    if r1:
        verdict = "NOISE_EQUIVALENT(Top20 维持,问题关闭)"
    elif r2:
        verdict = "CANDIDATE_REGISTERED(仅 R3 宪法修订讨论候选,不接线)"
    else:
        verdict = "TOP20_INFERIOR_CONFIRMED(方向关闭)"

    result = {
        "ts": datetime.now().isoformat(),
        "prereg_fingerprint": PREREG_FP,
        "arms": {k: {kk: (float(vv) if isinstance(vv, (int, float)) else vv)
                     for kk, vv in v.items()
                     if kk in ("total_return_pct", "annualized_return_pct", "max_drawdown_pct",
                               "sharpe_ratio", "total_trades", "trade_win_rate_pct")}
                 for k, v in arms.items()},
        "deltas_ann_pp_vs_top20": deltas,
        "criteria": {"R1_noise_±2pp": r1, "R2_candidate_>2pp": r2, "R3_inferior_<-2pp": r3},
        "verdict": verdict,
        "production_note": "研究性裁决, 生产 bp_ep 保持 Top20 不变",
    }
    with open(os.path.join(OUT_DIR, "verdict.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    entry = {
        "ts": result["ts"],
        "fingerprint": f"e1-ntop-{PREREG_FP}",
        "engine": "tulong_wfa_gate.v1(e1-ntop-channel)",
        "combo": ["bp_ep_ntop_sensitivity"],
        "pool": "bp_ep_monthly_topn",
        "params": {"arms": [20, 30, 50], "excluded": "topn15(S-3 falsified)"},
        "n_eff_trials": 1,
        "n_folds": 1,
        "verdict": verdict,
        "oos": {"deltas_ann_pp": deltas},
        "note": "V2计划§4 E1 立项(主人9/3『其他继续』); 研究性裁决不接生产; 判据见 PREREG 1b5d52a801a2eab4",
        "force": False,
        "one_vote_status": "ok",
        "prereg": {"id": prereg["id"], "status": "completed"},
    }
    with open(TRIALS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("=== E1 裁决: %s | Δ年化(pp) vs Top20: %s ===", verdict, deltas)


if __name__ == "__main__":
    main()
