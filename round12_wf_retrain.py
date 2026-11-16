# -*- coding: utf-8 -*-
"""
round12: ml 腿复活门禁 walk-forward 重训（预注册指纹 380b95ffbb494d64）
=====================================================================
目的: round11b『救 ml』条款正式兑现——修早停(固定500轮)后的 WF 禁重叠重训,
      产出纯 OOS 打分 v25, 供 M-3 trailing 叠加门禁裁决(R12_M3 臂)。
协议: reports/round12_wf_ml_20260903/PREREG.json（规范化内容哈希 380b95ffbb494d64, 勿改;
      初版冻结自引用哈希缺陷已内容保全重铸, 合同实质字段自训练前冻结起零变更）
与 round11 训练器唯一实质差异:
  ① 早停机制修复——废除尾10%验证集早停(round11 实证 1/3 年份机械失灵), 固定 500 轮;
     尾10% 仅保留作 AUC 诊断, 不参与停止
  ② 打分年 2019-2026(2018 按 prereg exclusion_declared 排除: 18个月 burn-in 边界)
输出: dragon_quant.db 新表 stock_ml_scores_v25_round12wf（v22/v23/v24 不动）
用法:
  python round12_wf_retrain.py --smoke            # 端到端冒烟, 不写库
  python round12_wf_retrain.py                    # 全量 2019-2026（可中断重跑, 幂等）
  python round12_wf_retrain.py --years 2019,2020
  python round12_wf_retrain.py --rescore 2019     # 强制重打某年分
"""
import argparse
import gc
import io
import json
import logging
import os
import pickle
import sqlite3
import sys
import time
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------- 路径/常量（全部来自预注册 44f3245d5600339e）
DQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
MODEL_DIR = r"D:\zcodesj\models\round12"
REPORT_DIR = r"D:\zcodesj\reports\round12_wf_ml_20260903"
OUT_TABLE = "stock_ml_scores_v25_round12wf"
SCORE_START_YEAR, SCORE_END_YEAR = 2019, 2026   # prereg score_years; 2018 排除已声明
FEATURE_START = 20160701
PURGE_TRADE_DAYS = 7      # 训练尾 purge（同 round11 预注册 2.2）
VAL_TAIL_FRAC = 0.10      # 仅 AUC 诊断用, 不参与早停（本轮修复语义）
SAMPLES_PER_YEAR = 300000
SMOKE_SAMPLE_CAP = 60000
PRICE_CAP = 200.0
EXCLUDE_PREFIX = ("688", "689", "8", "920", "430", "4")
NUM_BOOST_ROUNDS = 500    # 修早停: 固定轮数, 无 early_stopping callback

# 88 维特征（逐字复制自 v22/round11 训练器, 勿改动顺序）
FUND_COLS = ["ep", "bp", "sp", "roe", "lev", "ato", "pm"]
SF_COLS = ["smart_money", "momentum", "ma_bull", "boll_upper", "boll_mid", "boll_lower"]
FIN_COLS = ["gross_margin", "net_margin", "revenue_growth", "profit_growth",
            "debt_ratio", "current_ratio", "basic_eps", "bps"]
FV2_COLS = [
    "ret_1d", "open_gap_pct", "close_to_open", "high_low_pct",
    "ret_5d", "log_ret_5d", "max_ret_5d", "min_ret_5d",
    "ret_10d", "log_ret_10d", "max_ret_10d", "min_ret_10d",
    "ret_20d", "log_ret_20d", "max_ret_20d", "min_ret_20d",
    "ret_60d", "log_ret_60d", "max_ret_60d", "min_ret_60d",
    "close_to_ma5", "close_to_ma10", "close_to_ma20", "close_to_ma60", "close_to_ma120",
    "ma5_to_ma20", "ma20_to_ma60",
    "std_5d", "std_10d", "std_20d", "std_60d",
    "atr_14", "atr_pct_14", "skew_20d", "kurt_20d",
    "vol_ratio_5", "amt_ratio_5", "vol_ratio_10", "amt_ratio_10", "vol_ratio_20", "amt_ratio_20",
    "ret_1d_lag1", "vol_ratio_5_lag1", "amt_ratio_5_lag1",
    "ret_1d_lag2", "vol_ratio_5_lag2", "amt_ratio_5_lag2",
    "ret_1d_lag3", "vol_ratio_5_lag3", "amt_ratio_5_lag3",
    "ret_1d_lag5", "vol_ratio_5_lag5", "amt_ratio_5_lag5",
    "rsi14", "macd", "macd_signal", "kdj_k", "kdj_d", "kdj_j",
    "ret_1d_rank", "ret_5d_rank", "ret_20d_rank",
    "vol_ratio_5_rank", "vol_ratio_20_rank", "std_20d_rank", "close_rank", "amt_rank",
]
FEATURE_COLS = FV2_COLS + FUND_COLS + SF_COLS + FIN_COLS
assert len(FEATURE_COLS) == 88, f"特征数 {len(FEATURE_COLS)} != 88"

LGB_PARAMS = {
    "objective": "binary", "metric": ["auc", "binary_logloss"],
    "num_leaves": 63, "learning_rate": 0.05,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
    "min_data_in_leaf": 200, "lambda_l1": 0.5, "lambda_l2": 0.5,
    "max_depth": 8, "verbose": -1, "random_state": 42, "n_jobs": -1,
}

logger = logging.getLogger("round12")


def setup_log(tag: str) -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(MODEL_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(os.path.join(REPORT_DIR, f"retrain_{tag}.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)


def valid_code(code) -> bool:
    c = str(code).strip()
    return len(c) == 6 and not c.startswith(EXCLUDE_PREFIX)


def _zfill(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.zfill(6)


# ---------------------------------------------------------------- PIT 事件表加载（同 round11 逐字）
def load_pit_tables(dq_conn: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame]:
    pit = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True)
    fund = pd.read_sql_query(
        "SELECT code, pit_avail_date, ep, bp, sp, roe, lev, ato, pm "
        "FROM stock_factors_fundamental_pit_v2 "
        "WHERE pit_avail_date IS NOT NULL "
        "AND NOT (snap_rule='tail' AND ref_date < pit_avail_date)", pit)
    pit.close()
    fund["code"] = _zfill(fund["code"])
    fund["pit_avail_date"] = fund["pit_avail_date"].astype("int64")
    fund = fund.sort_values("pit_avail_date").reset_index(drop=True)
    fund = fund.drop_duplicates(subset=["code", "pit_avail_date"], keep="last")

    fin = pd.read_sql_query(
        "SELECT code, pit_avail_date, " + ", ".join(FIN_COLS) + " FROM stock_financial "
        "WHERE pit_avail_date IS NOT NULL", dq_conn)
    fin["code"] = _zfill(fin["code"])
    fin["pit_avail_date"] = fin["pit_avail_date"].astype("int64")
    fin = fin.sort_values("pit_avail_date").reset_index(drop=True)
    fin = fin.drop_duplicates(subset=["code", "pit_avail_date"], keep="last")
    logger.info(f"PIT 事件表: fund={len(fund):,} fin={len(fin):,}")
    return fund, fin


def pit_asof(df: pd.DataFrame, ev: pd.DataFrame, cols: list[str], audit: dict) -> pd.DataFrame:
    left = df[["code", "date"]].copy()
    left = left.sort_values("date").reset_index(drop=True)
    joined = pd.merge_asof(
        left, ev, left_on="date", right_on="pit_avail_date",
        by="code", direction="backward")
    got = joined[["pit_avail_date"] + cols].reset_index(drop=True)
    out = pd.concat([df.reset_index(drop=True), got], axis=1)
    ok = out["pit_avail_date"].notna()
    if ok.any():
        assert (out.loc[ok, "pit_avail_date"] <= out.loc[ok, "date"]).all(), "G1 断言失败: pit_avail_date > date"
    audit["asof_rows"] = audit.get("asof_rows", 0) + int(ok.sum())
    audit["asof_violations"] = audit.get("asof_violations", 0) + int(
        (out.loc[ok, "pit_avail_date"] > out.loc[ok, "date"]).sum())
    return out


# ---------------------------------------------------------------- 特征/标签装配（同 round11 逐字, 仅去 leaky 支路）
def build_year_features(dq: sqlite3.Connection, year: int, fund: pd.DataFrame,
                        fin: pd.DataFrame, audit: dict) -> pd.DataFrame:
    d1, d2 = year * 10000 + 101, year * 10000 + 1231
    fv2 = pd.read_sql(
        f"SELECT code, date, {', '.join(FV2_COLS)} FROM feature_daily_v2 "
        f"WHERE date >= {d1} AND date <= {d2}", dq)
    sf = pd.read_sql(
        f"SELECT code, date, {', '.join(SF_COLS)} FROM stock_factors "
        f"WHERE date >= {d1} AND date <= {d2}", dq)
    df = fv2.merge(sf, on=["code", "date"], how="left")
    del fv2, sf
    df["code"] = _zfill(df["code"])
    df = df[df["code"].apply(valid_code)].copy()
    df = pit_asof(df, fund, FUND_COLS, audit)
    df = df.drop(columns=["pit_avail_date"])
    df = pit_asof(df, fin, FIN_COLS, audit)
    df = df.drop(columns=["pit_avail_date"])
    return df


def load_labels(dq: sqlite3.Connection, d1: int, d2: int, margin_days: int = 20) -> pd.DataFrame:
    """T+1 open -> T+5 close 标签（v22/round11 逐字复刻）; d2 加自然日 margin 让 LEAD 窗口闭合"""
    dm = d2 + margin_days if d2 % 10000 <= 1211 else d2 + 10000 - 1231 + 31
    sql = f"""
    SELECT code, date, label, holding_ret FROM (
        SELECT code, date,
               CASE WHEN next_open > 0 AND close_5d > 0 AND close_5d > next_open * 1.001 THEN 1.0 ELSE 0.0 END as label,
               (close_5d - next_open) / next_open as holding_ret
        FROM (
            SELECT code, date,
                   LEAD(open, 1) OVER (PARTITION BY code ORDER BY date) as next_open,
                   LEAD(close, 5) OVER (PARTITION BY code ORDER BY date) as close_5d
            FROM stock_daily_hsj
            WHERE date >= {d1} AND date <= {dm} AND close > 0 AND close <= {PRICE_CAP}
        ) WHERE next_open IS NOT NULL AND close_5d IS NOT NULL
    ) WHERE date <= {d2}
    """
    df = pd.read_sql(sql, dq)
    df["code"] = _zfill(df["code"])
    return df[["code", "date", "label", "holding_ret"]]


def trade_dates_of_year(dq: sqlite3.Connection, year: int) -> list[int]:
    rows = dq.execute(
        "SELECT DISTINCT date FROM stock_daily_hsj WHERE date >= ? AND date <= ? ORDER BY date",
        (year * 10000 + 101, year * 10000 + 1231)).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------- 训练 / 打分
def train_model_for_year(year: int, dq, fund, fin, audit: dict, smoke: bool) -> dict:
    model_path = os.path.join(MODEL_DIR, "smoke" if smoke else "", f"M{year}.pkl")
    if os.path.exists(model_path):
        with open(model_path, "rb") as f:
            blob = pickle.load(f)
        logger.info(f"M{year}: 模型已存在, 跳过训练 ({blob['best_iteration']} 轮)")
        audit["train_max_date"][str(year)] = blob["train_max_date"]
        assert blob["train_max_date"] < blob["boundary"], "G1 断言失败(重载): 训练行越过 purge 边界"
        return blob

    t0 = time.time()
    tds = trade_dates_of_year(dq, year - 1)
    if len(tds) < PURGE_TRADE_DAYS + 1:
        raise RuntimeError(f"{year-1} 交易日不足: {len(tds)}")
    boundary = tds[-PURGE_TRADE_DAYS]
    cap = SMOKE_SAMPLE_CAP if smoke else SAMPLES_PER_YEAR
    train_years = [year - 1] if smoke else list(range(FEATURE_START // 10000, year))

    frames = []
    for ty in train_years:
        feat = build_year_features(dq, ty, fund, fin, audit)
        if len(feat) == 0:
            logger.info(f"M{year} 训练年 {ty}: 无特征行")
            continue
        d1 = FEATURE_START if ty == FEATURE_START // 10000 else ty * 10000 + 101
        d2 = ty * 10000 + 1231
        lab = load_labels(dq, d1, d2)
        df = feat.merge(lab, on=["code", "date"], how="inner")
        del feat
        if ty == year - 1:
            df = df[df["date"] < boundary]
        if len(df) > cap:
            df = df.sample(n=cap, random_state=42)
        frames.append(df)
        logger.info(f"M{year} 训练年 {ty}: 采样后 {len(df):,} 行 (cap={cap})")
        del df
        gc.collect()

    data = pd.concat(frames, ignore_index=True)
    del frames
    gc.collect()
    logger.info(f"M{year}: 训练样本合计 {len(data):,}, 正样本 {data['label'].mean():.2%}, "
                f"特征日范围 {data['date'].min()}~{data['date'].max()} (purge边界 {boundary})")
    audit["train_max_date"][str(year)] = int(data["date"].max())
    assert data["date"].max() < boundary, "G1 断言失败: 训练行越过 purge 边界"

    medians = {}
    for c in FEATURE_COLS:
        v = pd.to_numeric(data[c], errors="coerce").replace([np.inf, -np.inf], np.nan)
        med = v.median()
        medians[c] = float(med) if not pd.isna(med) else 0.0
        data[c] = v.fillna(medians[c])

    data = data.sort_values("date").reset_index(drop=True)
    X_all = data[FEATURE_COLS].values.astype(np.float64)
    y_all = data["label"].values
    n_val = max(int(len(data) * VAL_TAIL_FRAC), 1000)
    X_tr, y_tr = X_all[:-n_val], y_all[:-n_val]
    X_va, y_va = X_all[-n_val:], y_all[-n_val:]
    logger.info(f"M{year}: train={len(X_tr):,} val(尾{VAL_TAIL_FRAC:.0%},仅诊断)={len(X_va):,} "
                f"(val 特征日 {data['date'].iloc[-n_val]}~{data['date'].iloc[-1]})")
    del data, X_all, y_all
    gc.collect()

    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_COLS)
    dval = lgb.Dataset(X_va, label=y_va, feature_name=FEATURE_COLS, reference=dtrain)
    # 修早停(prereg 唯一实质变更): 固定 500 轮, 无 early_stopping; val 仅作 AUC 诊断
    model = lgb.train(LGB_PARAMS, dtrain, valid_sets=[dtrain, dval],
                      valid_names=["train", "val"], num_boost_round=NUM_BOOST_ROUNDS,
                      callbacks=[lgb.log_evaluation(0)])
    auc_tr = roc_auc_score(y_tr, model.predict(X_tr, num_iteration=NUM_BOOST_ROUNDS))
    auc_va = roc_auc_score(y_va, model.predict(X_va, num_iteration=NUM_BOOST_ROUNDS))
    blob = {
        "model": model, "feature_cols": FEATURE_COLS, "medians": medians,
        "best_iteration": NUM_BOOST_ROUNDS,
        "train_max_date": int(audit["train_max_date"][str(year)]),
        "train_auc": float(auc_tr), "val_auc": float(auc_va),
        "train_size": int(len(X_tr)), "val_size": int(len(X_va)),
        "boundary": int(boundary),
    }
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    with open(model_path, "wb") as f:
        pickle.dump(blob, f)
    meta = {k: v for k, v in blob.items() if k != "model"}
    io.open(model_path.replace(".pkl", ".json"), "w", encoding="utf-8").write(
        json.dumps(meta, ensure_ascii=False, indent=1))
    logger.info(f"M{year}: 训练完成 fixed_rounds={NUM_BOOST_ROUNDS} "
                f"AUC train={auc_tr:.4f} val={auc_va:.4f} 耗时 {time.time()-t0:.0f}s")
    return blob


def scored_rows(dq: sqlite3.Connection, year: int) -> int:
    try:
        return dq.execute(
            f"SELECT COUNT(*) FROM {OUT_TABLE} WHERE date >= ? AND date <= ?",
            (year * 10000 + 101, year * 10000 + 1231)).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _write_or_smoke(df: pd.DataFrame, probs, year: int, d1: int, d2: int,
                    dq, smoke: bool, t0: float) -> int:
    if smoke:
        logger.info(f"M{year} smoke 打分 {d1}-{d2}: {len(df):,} 行 "
                    f"prob[min/med/max]={np.min(probs):.3f}/{np.median(probs):.3f}/{np.max(probs):.3f}")
        return len(df)
    rows = [(str(df.iloc[j].code), int(df.iloc[j].date), float(probs[j])) for j in range(len(df))]
    dq.executemany(f"INSERT OR REPLACE INTO {OUT_TABLE} VALUES(?,?,?)", rows)
    dq.commit()
    logger.info(f"M{year} 打分 {d1}-{d2}: {len(rows):,} 行入库 ({time.time()-t0:.0f}s)")
    return len(rows)


def score_year(year: int, dq, fund, fin, blob: dict, audit: dict, smoke: bool) -> int:
    if not smoke:
        n = scored_rows(dq, year)
        if n > 0:
            logger.info(f"M{year} 打分: 已有 {n:,} 行, 跳过（--rescore 强制）")
            return n
        dq.execute(f"CREATE TABLE IF NOT EXISTS {OUT_TABLE} "
                   "(code TEXT, date INT, ml_prob REAL, PRIMARY KEY(code, date))")
        dq.commit()
    model, fc = blob["model"], blob["feature_cols"]
    quarters = [(year * 10000 + 101, year * 10000 + 331), (year * 10000 + 401, year * 10000 + 630),
                (year * 10000 + 701, year * 10000 + 930), (year * 10000 + 1001, year * 10000 + 1231)]
    if smoke:
        quarters = quarters[:1]
    total = 0
    for d1, d2 in quarters:
        t0 = time.time()
        fv2 = pd.read_sql(
            f"SELECT code, date, {', '.join(FV2_COLS)} FROM feature_daily_v2 "
            f"WHERE date >= {d1} AND date <= {d2}", dq)
        sf = pd.read_sql(
            f"SELECT code, date, {', '.join(SF_COLS)} FROM stock_factors "
            f"WHERE date >= {d1} AND date <= {d2}", dq)
        df = fv2.merge(sf, on=["code", "date"], how="left")
        del fv2, sf
        if len(df) == 0 or "code" not in df.columns:
            logger.info(f"M{year} 打分 {d1}-{d2}: 无数据行(未来季度), 跳过")
            continue
        df["code"] = _zfill(df["code"])
        df = df[df["code"].apply(valid_code)].copy()
        df = pit_asof(df, fund, FUND_COLS, audit)
        df = df.drop(columns=["pit_avail_date"])
        df = pit_asof(df, fin, FIN_COLS, audit)
        df = df.drop(columns=["pit_avail_date"])
        for c in fc:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0)
            else:
                df[c] = 0
        X = df[fc].values.astype(np.float64)
        probs = model.predict(X, num_iteration=blob["best_iteration"])
        total += _write_or_smoke(df, probs, year, d1, d2, dq, smoke, t0)
        del df, X, probs
        gc.collect()
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="round12 WF retrain (prereg 44f3245d5600339e)")
    ap.add_argument("--smoke", action="store_true", help="端到端冒烟: 1年小样本训练+1季打分, 不写库")
    ap.add_argument("--years", default="", help="逗号分隔打分年, 默认 2019-2026")
    ap.add_argument("--rescore", default="", help="强制重打分的年（逗号分隔）")
    args = ap.parse_args()
    tag = "smoke" if args.smoke else "full"
    setup_log(tag)
    logger.info(f"round12 walk-forward 重训启动 mode={'SMOKE' if args.smoke else 'FULL'} "
                f"prereg=44f3245d5600339e 修早停=固定{NUM_BOOST_ROUNDS}轮 打分年={SCORE_START_YEAR}-{SCORE_END_YEAR}")

    years = ([int(y) for y in args.years.split(",")] if args.years
             else list(range(SCORE_START_YEAR, SCORE_END_YEAR + 1)))
    rescore = {int(y) for y in args.rescore.split(",")} if args.rescore else set()

    dq = sqlite3.connect(DQ_DB, timeout=120)
    audit = {"train_max_date": {}, "asof_rows": 0, "asof_violations": 0}
    fund, fin = load_pit_tables(dq)

    results = {}
    for y in years:
        try:
            blob = train_model_for_year(y, dq, fund, fin, audit, args.smoke)
            if y in rescore and not args.smoke:
                dq.execute(f"DELETE FROM {OUT_TABLE} WHERE date >= ? AND date <= ?",
                           (y * 10000 + 101, y * 10000 + 1231))
                dq.commit()
                logger.info(f"M{y} --rescore: 清除旧打分")
            n = score_year(y, dq, fund, fin, blob, audit, args.smoke)
            results[y] = {"rows": n, "rounds": blob["best_iteration"],
                          "val_auc": blob.get("val_auc"), "train_max_date": blob["train_max_date"]}
        except Exception as e:
            logger.error(f"M{y} 失败: {e}", exc_info=True)
            results[y] = {"error": str(e)}
            if args.smoke:
                dq.close()
                return 2
    dq.close()

    audit_path = os.path.join(REPORT_DIR, f"audit_{tag}.json")
    io.open(audit_path, "w", encoding="utf-8").write(json.dumps(
        {"audit": audit, "results": results, "ts": datetime.now().isoformat()}, ensure_ascii=False, indent=1))
    g1 = audit["asof_violations"] == 0
    logger.info(f"完成: {len(results)} 年 | G1 asof断言 violations={audit['asof_violations']} "
                f"({'PASS' if g1 else 'FAIL'}) | 明细 {audit_path}")
    return 0 if g1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
