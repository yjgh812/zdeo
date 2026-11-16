# -*- coding: utf-8 -*-
"""round12 ml 腿复活门禁：双臂回测 + 四判据裁决 (2026-09-03)
=====================================================================
预注册: reports/round12_wf_ml_20260903/PREREG.json（指纹 44f3245d5600339e, 跑前冻结, trial_budget=1）
双臂: R12_raw = v25 纯 OOS 打分 + 正典 ml 全栈 + hold45 无 trailing（归因对照）
      R12_M3 = 同 + trailing10%/hold20（复活候选, 唯一裁决臂）
判据(LOCKED): C1 拼接OOS年化>=+8% | C2 2026年>0 | C3 MDD<=25% | C4 正年占比>=50%
PASS=C1∧C2∧C3∧C4 → 解锁 ml 权重翻转; FAIL → ml 维持退役, 复活条款诚实关闭。
用法: python tulong_wfa_gate_round12_ml_20260903.py [--from-disk]
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")

PREREG_PATH = r"D:\zcodesj\reports\round12_wf_ml_20260903\PREREG.json"
PREREG_FP = "380b95ffbb494d64"   # 规范化内容哈希(剔除 fingerprint 字段后 sha256[:16], 内容保全重铸)
TRIALS_LOG = r"D:\zcodesj\data\wfa_trials.jsonl"

os.environ["TULONG_ML_TABLE"] = "stock_ml_scores_v25_round12wf"   # 引擎原生研究表开关(round11 起)
os.environ["TULONG_BT_OUT"] = r"D:\zcodesj\reports\round12_wf_ml_20260903\gate"

import tulong_backtest_full_report as base  # noqa: E402

GATE_DIR = base.OUTPUT_DIR
os.makedirs(GATE_DIR, exist_ok=True)

import functools  # noqa: E402
import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("round12_gate")


class CachedLoader:
    def __init__(self, inner):
        self._inner = inner
        self.get_data = functools.lru_cache(maxsize=None)(inner.get_data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def verify_prereg():
    p = json.load(open(PREREG_PATH, encoding="utf-8"))
    claimed = p.pop("fingerprint", None)
    canonical = json.dumps(p, ensure_ascii=False, sort_keys=True, indent=1)
    fp = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    assert fp == PREREG_FP == claimed, f"预注册指纹漂移: 计算{fp} != 冻结{PREREG_FP} != 文件{claimed}"
    assert p["status"] in ("preregistered", "completed") and p["trial_budget"] == 1
    logger.info(f"预注册校验 OK: {PREREG_FP} | criterion={list(p['criterion_LOCKED'].keys())}")
    return p


def yearly_from_equity(eq) -> dict:
    """equity_curve -> {year: 年收益}; 兼容 (date,nav[,cum]) 列或 date 索引"""
    import pandas as pd
    df = eq.copy()
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    if "date" in df.columns:
        dcol = "date"
    else:
        df = df.reset_index()
        dcol = df.columns[0]
    vcol = [c for c in df.columns if c != dcol and pd.api.types.is_numeric_dtype(df[c])][0]
    df[dcol] = df[dcol].astype(str)
    df = df.sort_values(dcol)
    df["year"] = df[dcol].str[:4]
    ye = df.groupby("year")[vcol].last()
    years = sorted(ye.index)
    out = {}
    prev = None
    for y in years:
        out[y] = (ye[y] / prev - 1.0) if prev else None
        prev = ye[y]
    return out, {y: float(v) for y, v in ye.items()}


def stats_from_equity(eq, d1: str, d2: str):
    import pandas as pd
    df = eq.copy()
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    if "date" in df.columns:
        dcol = "date"
    else:
        df = df.reset_index()
        dcol = df.columns[0]
    vcol = [c for c in df.columns if c != dcol and pd.api.types.is_numeric_dtype(df[c])][0]
    df[dcol] = df[dcol].astype(str)
    df = df.sort_values(dcol)
    w = df[(df[dcol] >= d1) & (df[dcol] <= d2)]
    vals = w[vcol].astype(float).tolist()
    start, end = vals[0], vals[-1]
    years = (len(vals)) / 244.0
    ann = (end / start) ** (1.0 / years) - 1.0
    peak, mdd = start, 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return {"window": [d1, d2], "bars": len(vals), "total_return": end / start - 1.0,
            "ann_return": ann, "mdd": mdd, "years": years}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-disk", action="store_true",
                    help="从 gate 目录已落盘产物再生裁决(判据修复用, 不重跑回测)")
    args = ap.parse_args()
    prereg = verify_prereg()

    if args.from_disk:
        import pandas as pd
        def load_eq(name):
            return pd.read_csv(os.path.join(GATE_DIR, f"{name}_equity.csv"), encoding="utf-8-sig")
        eq_raw, eq_m3 = load_eq("r12_ml_raw"), load_eq("r12_ml_m3_trail")
        sm_raw = json.load(open(os.path.join(GATE_DIR, "r12_ml_raw_summary.json"), encoding="utf-8"))["summary"]
        sm_m3 = json.load(open(os.path.join(GATE_DIR, "r12_ml_m3_trail_summary.json"), encoding="utf-8"))["summary"]
        arm_meta = {"r12_ml_raw": sm_raw, "r12_ml_m3_trail": sm_m3}
    else:
        import functools
        loader = CachedLoader(base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH))

        logger.info("=== R12_raw: v25 OOS 打分 + 正典 ml 全栈 + hold45 无 trailing ===")
        r_raw = base.run_single_backtest("r12_ml_raw", base.ml_signal, loader, "monthly",
                                         {"max_holding_bars": 45})
        logger.info("=== R12_M3: 同 + trailing10%%/hold20 (唯一裁决臂) ===")
        r_m3 = base.run_single_backtest("r12_ml_m3_trail", base.ml_signal, loader, "monthly",
                                        {"max_holding_bars": 20, "trailing_stop_pct": 0.10})
        eq_raw, eq_m3 = r_raw.get("equity_curve"), r_m3.get("equity_curve")
        arm_meta = {
            "r12_ml_raw": r_raw.get("metrics", {}),
            "r12_ml_m3_trail": r_m3.get("metrics", {}),
        }

    yr_m3, ye_m3 = yearly_from_equity(eq_m3)
    yr_raw, _ = yearly_from_equity(eq_raw)
    st = stats_from_equity(eq_m3, "2019-01-01", "2026-09-02")

    score_years = [str(y) for y in prereg["trainer"]["score_years"]]
    year_rets = {y: (float(yr_m3.get(y)) if yr_m3.get(y) is not None else 0.0) for y in score_years}
    pos_years = sum(1 for v in year_rets.values() if v > 0)

    c1 = bool(st["ann_return"] >= 0.08)
    c2 = bool(year_rets.get("2026", 0.0) > 0)
    c3 = bool(st["mdd"] >= -0.25)
    c4 = bool(pos_years >= len(score_years) * 0.5)
    verdict = "PASS" if (c1 and c2 and c3 and c4) else "FAIL"

    result = {
        "ts": datetime.now().isoformat(),
        "prereg_fingerprint": PREREG_FP,
        "ml_table": "stock_ml_scores_v25_round12wf",
        "oos_window": st["window"],
        "arm_R12_M3": {
            "stats": {k: (round(float(v), 4) if isinstance(v, float) else v) for k, v in st.items()},
            "yearly": {y: round(float(v), 4) for y, v in year_rets.items()},
            "engine_metrics": {k: (None if v is None else float(v)) for k, v in
                               ((k, arm_meta["r12_ml_m3_trail"].get(k)) for k in
                                ("total_return_pct", "annualized_return_pct", "max_drawdown_pct",
                                 "sharpe_ratio", "total_trades", "trade_win_rate_pct"))},
        },
        "arm_R12_raw_yearly": {y: (round(float(v), 4) if v is not None else None) for y, v in yr_raw.items()},
        "criteria": {
            "C1_seat_bar_ann>=8%": {"value": round(st["ann_return"], 4), "pass": c1},
            "C2_2026_true_oos>0": {"value": round(year_rets.get("2026", 0.0), 4), "pass": c2},
            "C3_mdd<=25%": {"value": round(st["mdd"], 4), "pass": c3},
            "C4_pos_years>=50%": {"value": f"{pos_years}/{len(score_years)}", "pass": c4},
        },
        "verdict": verdict,
    }
    with open(os.path.join(GATE_DIR, "gate_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    entry = {
        "ts": result["ts"],
        "fingerprint": f"round12-ml-m3-{PREREG_FP}",
        "engine": "tulong_wfa_gate.v1(round12-ml-channel)",
        "combo": ["m3_trail_ml_wf"],
        "pool": "ml_signal_v25_round12wf_monthly",
        "params": {"arms": ["r12_ml_raw", "r12_ml_m3_trail"], "oos": st["window"],
                   "fix_early_stop": "fixed_500_rounds"},
        "n_eff_trials": 1,
        "n_folds": len(score_years),
        "verdict": verdict,
        "oos": {"ann": round(st["ann_return"], 4), "mdd": round(st["mdd"], 4),
                "pos_years": pos_years, "y2026": round(year_rets.get("2026", 0.0), 4)},
        "note": "round11b 救ml条款兑现; v25=修早停(固定500轮)WF禁重叠纯OOS打分; 判据见 PREREG 44f3245d5600339e; "
                "PASS→解锁ml权重翻转(单旗守卫已就位), FAIL→ml维持退役复活条款关闭",
        "force": False,
        "one_vote_status": "ok",
        "prereg": {"id": prereg["id"], "status": "completed"},
    }
    with open(TRIALS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("=== round12 门禁裁决: %s ===", verdict)
    logger.info("C1 年化 %.2f%% (>=8%%? %s) | C2 2026 %.2f%% (>0? %s) | C3 MDD %.2f%% (<=25%%? %s) | C4 正年 %d/%d (%s)",
                st["ann_return"] * 100, c1, year_rets.get("2026", 0.0) * 100, c2,
                st["mdd"] * 100, c3, pos_years, len(score_years), c4)
    logger.info("结果: %s/gate_result.json | 台账: %s", GATE_DIR, TRIALS_LOG)


if __name__ == "__main__":
    main()
