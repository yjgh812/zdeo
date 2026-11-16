# -*- coding: utf-8 -*-
"""
WIRE-1 C1 臂: ml 信号 walk-forward PIT 重训·回归目标变体（2026-09-04）
=====================================================================
溯源: ml_walkforward_pit_retrain.py 逐字复制(round12 修早停版, 产 v25_round12wf 基线),
仅单点换目标函数: objective binary→regression, 目标=holding_ret 按时间序 train 部分
(不含尾 10% val)1%/99% 分位 winsorize; 指标 AUC→RankIC。其余逐字一致(diff 可审计)。
协议授权: reports/rankloss_wire1_20260904/PREREG.json(指纹 a81c24fafcc3f11f, trial_budget=1)。
输出: dragon_quant.db 新表 stock_ml_scores_v26_c1reg(v25 基线表不动)。
用法:
  python ml_walkforward_pit_retrain_c1_20260904.py --smoke        # 端到端冒烟, 不写库
  python ml_walkforward_pit_retrain_c1_20260904.py                # 全量 2018-2026（可中断重跑, 幂等）
  python ml_walkforward_pit_retrain_c1_20260904.py --years 2018,2019
  python ml_walkforward_pit_retrain_c1_20260904.py --rescore 2018 # 强制重打某年分
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

# ---------------------------------------------------------------- 路径/常量（全部来自预注册）
DQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
MODEL_DIR = r"D:\zcodesj\models\round12_c1"
REPORT_DIR = r"D:\zcodesj\reports\rankloss_wire1_20260904"
OUT_TABLE = "stock_ml_scores_v26_c1reg"
FEATURE_SOURCE = "pit"  # pit=round11 生产研究口径; leaky=round11b 归因对照(禁入生产, 见 ROUND11B_PREREG)
LEAKY_TABLE = "stock_ml_scores_v24_leakwf"
LEAKY_MODEL_DIR = r"D:\zcodesj\models\round11b"
LEAKY_REPORT_DIR = r"D:\zcodesj\reports\round11b_leakctl_20260829"
SCORE_START_YEAR, SCORE_END_YEAR = 2018, 2026
FEATURE_START = 20160701  # feature_daily_v2 实际起点 2016-06-30, 取整日起
PURGE_TRADE_DAYS = 7      # 训练尾 purge（预注册 2.2）
VAL_TAIL_FRAC = 0.10      # early-stop 验证集 = 训练窗按时间尾 10%
SAMPLES_PER_YEAR = 300000
SMOKE_SAMPLE_CAP = 60000
PRICE_CAP = 200.0
EXCLUDE_PREFIX = ("688", "689", "8", "920", "430", "4")

# 88 维特征（逐字复制自 v22 训练脚本 archive/20260818_audit_cleanup/
# snapshots/2026-07-03-21-47-27/exp_tulong_v22_train.py, 勿改动顺序）
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
    "objective": "regression", "metric": "l2",
    "num_leaves": 63, "learning_rate": 0.05,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 5,
    "min_data_in_leaf": 200, "lambda_l1": 0.5, "lambda_l2": 0.5,
    "max_depth": 8, "verbose": -1, "random_state": 42, "n_jobs": -1,
}

logger = logging.getLogger("round11")


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


# ---------------------------------------------------------------- PIT 事件表加载
def load_pit_tables(dq_conn: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame]:
    """fund: factor_pit.db v2（tail 排除, 镜像生产 IC 链口径）; fin: stock_financial（pit_avail 对齐）"""
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
    # 同日同码多 report: keep last（与生产 ffill 语义一致）
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
    """按 code 分组、pit_avail_date <= date 的 backward as-of join（G1 由构造保证, 事后断言）"""
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


# ---------------------------------------------------------------- 特征/标签装配
def build_year_features(dq: sqlite3.Connection, year: int, fund: pd.DataFrame,
                        fin: pd.DataFrame, audit: dict) -> pd.DataFrame:
    d1, d2 = year * 10000 + 101, year * 10000 + 1231
    if FEATURE_SOURCE == "leaky":
        # round11b 对照臂: v22 precompute SQL 逐字复刻(含已知前视, 仅归因用)。
        # 年窗全宽 fetchall 会触发 sqlite disk I/O error, 按季度切片(与 v22 precompute 同粒度)
        fv2_sel = ", ".join([f"f.{c}" for c in FV2_COLS])
        sql_t = f"""SELECT f.code, f.date, {fv2_sel},
            fu.ep, fu.bp, fu.sp, fu.roe, fu.lev, fu.ato, fu.pm,
            sf.smart_money, sf.momentum, sf.ma_bull, sf.boll_upper, sf.boll_mid, sf.boll_lower,
            fi.gross_margin, fi.net_margin, fi.revenue_growth, fi.profit_growth,
            fi.debt_ratio, fi.current_ratio, fi.basic_eps, fi.bps
            FROM feature_daily_v2 f
            LEFT JOIN stock_factors_fundamental fu ON f.code=fu.code AND f.date=fu.date
            LEFT JOIN stock_factors sf ON f.code=sf.code AND f.date=sf.date
            LEFT JOIN stock_financial fi ON f.code=fi.code AND fu.report_date=fi.report_date
            WHERE f.date >= {{d1}} AND f.date <= {{d2}}"""
        qs = [(year * 10000 + 101, year * 10000 + 331), (year * 10000 + 401, year * 10000 + 630),
              (year * 10000 + 701, year * 10000 + 930), (year * 10000 + 1001, d2)]
        frames = [pd.read_sql(sql_t.format(d1=q1, d2=q2), dq) for q1, q2 in qs]
        df = pd.concat(frames, ignore_index=True)
        del frames
        df["code"] = _zfill(df["code"])
        return df[df["code"].apply(valid_code)].copy()
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
    """T+1 open -> T+5 close 标签（v22 逐字复刻）; d2 加自然日 margin 让 LEAD 窗口闭合"""
    dm = d2 + margin_days if d2 % 10000 <= 1211 else d2 + 10000 - 1231 + 31  # 简化: 跨年兜底
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
    boundary = tds[-PURGE_TRADE_DAYS]  # 保留 date < boundary（预注册 2.2 purge）
    cap = SMOKE_SAMPLE_CAP if smoke else SAMPLES_PER_YEAR
    if smoke:
        train_years = [year - 1]  # 冒烟只装 1 年
    else:
        train_years = list(range(FEATURE_START // 10000, year))

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
    # C1(WIRE-1): 回归目标=holding_ret, winsorize 分位仅按 train 部分(不含尾10% val)计算防泄漏
    y_raw = data["holding_ret"].values.astype(np.float64)
    n_val = max(int(len(data) * VAL_TAIL_FRAC), 1000)
    _q_lo, _q_hi = np.nanquantile(y_raw[:-n_val], [0.01, 0.99])
    y_all = np.clip(y_raw, _q_lo, _q_hi)
    X_tr, y_tr = X_all[:-n_val], y_all[:-n_val]
    X_va, y_va = X_all[-n_val:], y_all[-n_val:]
    logger.info(f"M{year}: train={len(X_tr):,} val(尾{VAL_TAIL_FRAC:.0%})={len(X_va):,} "
                f"(val 特征日 {data['date'].iloc[-n_val]}~{data['date'].iloc[-1]}) "
                f"目标winsor=[{_q_lo:.4f},{_q_hi:.4f}]")
    del data, X_all, y_all, y_raw
    gc.collect()

    import lightgbm as lgb
    dtrain = lgb.Dataset(X_tr, label=y_tr, feature_name=FEATURE_COLS)
    dval = lgb.Dataset(X_va, label=y_va, feature_name=FEATURE_COLS, reference=dtrain)
    model = lgb.train(LGB_PARAMS, dtrain, valid_sets=[dtrain, dval],
                      valid_names=["train", "val"], num_boost_round=500,
                      callbacks=[lgb.early_stopping(30), lgb.log_evaluation(0)])
    from scipy.stats import spearmanr
    rank_tr = float(spearmanr(model.predict(X_tr, num_iteration=model.best_iteration), y_tr).statistic)
    rank_va = float(spearmanr(model.predict(X_va, num_iteration=model.best_iteration), y_va).statistic)
    blob = {
        "model": model, "feature_cols": FEATURE_COLS, "medians": medians,
        "best_iteration": int(model.best_iteration),
        "train_max_date": int(audit["train_max_date"][str(year)]),
        "train_rankic": rank_tr, "val_rankic": rank_va,
        "target_winsor": [float(_q_lo), float(_q_hi)],
        "train_size": int(len(X_tr)), "val_size": int(len(X_va)),
        "boundary": int(boundary),
    }
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    with open(model_path, "wb") as f:
        pickle.dump(blob, f)
    meta = {k: v for k, v in blob.items() if k not in ("model",)}
    io.open(model_path.replace(".pkl", ".json"), "w", encoding="utf-8").write(
        json.dumps(meta, ensure_ascii=False, indent=1))
    logger.info(f"M{year}: 训练完成 best_iter={blob['best_iteration']} "
                f"RankIC train={rank_tr:.4f} val={rank_va:.4f} 耗时 {time.time()-t0:.0f}s")
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
    """打分结果写库(或冒烟只打印), 两特征路径共享"""
    if smoke:
        logger.info(f"M{year} smoke 打分 {d1}-{d2}: {len(df):,} 行 "
                    f"score[min/med/max]={np.min(probs):.3f}/{np.median(probs):.3f}/{np.max(probs):.3f}")
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
    model, fc, med = blob["model"], blob["feature_cols"], blob["medians"]
    quarters = [(year * 10000 + 101, year * 10000 + 331), (year * 10000 + 401, year * 10000 + 630),
                (year * 10000 + 701, year * 10000 + 930), (year * 10000 + 1001, year * 10000 + 1231)]
    if smoke:
        quarters = quarters[:1]
    total = 0
    for d1, d2 in quarters:
        t0 = time.time()
        if FEATURE_SOURCE == "leaky":
            fv2_sel = ", ".join([f"f.{c}" for c in FV2_COLS])
            sql = f"""SELECT f.code, f.date, {fv2_sel},
                fu.ep, fu.bp, fu.sp, fu.roe, fu.lev, fu.ato, fu.pm,
                sf.smart_money, sf.momentum, sf.ma_bull, sf.boll_upper, sf.boll_mid, sf.boll_lower,
                fi.gross_margin, fi.net_margin, fi.revenue_growth, fi.profit_growth,
                fi.debt_ratio, fi.current_ratio, fi.basic_eps, fi.bps
                FROM feature_daily_v2 f
                LEFT JOIN stock_factors_fundamental fu ON f.code=fu.code AND f.date=fu.date
                LEFT JOIN stock_factors sf ON f.code=sf.code AND f.date=sf.date
                LEFT JOIN stock_financial fi ON f.code=fi.code AND fu.report_date=fi.report_date
                WHERE f.date >= {d1} AND f.date <= {d2}"""
            df = pd.read_sql(sql, dq)
        else:
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
        if FEATURE_SOURCE == "leaky":
            for c in fc:
                df[c] = pd.to_numeric(df[c], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0) \
                    if c in df.columns else 0
            X = df[fc].values.astype(np.float64)
            probs = model.predict(X, num_iteration=blob["best_iteration"])
            total += _write_or_smoke(df, probs, year, d1, d2, dq, smoke, t0)
            del df, X, probs
            gc.collect()
            continue
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
    global FEATURE_SOURCE, OUT_TABLE, MODEL_DIR, REPORT_DIR
    ap = argparse.ArgumentParser(description="WIRE-1 C1 回归目标 WF 重训 (prereg a81c24fafcc3f11f; 基线=round12 v25_round12wf)")
    ap.add_argument("--smoke", action="store_true", help="端到端冒烟: 1年小样本训练+1季打分, 不写库")
    ap.add_argument("--years", default="", help="逗号分隔打分年, 默认 2018-2026")
    ap.add_argument("--rescore", default="", help="强制重打分的年（逗号分隔）")
    ap.add_argument("--feature-source", choices=["pit", "leaky"], default="pit",
                    help="pit=round11 生产研究口径; leaky=round11b 归因对照臂(禁入生产)")
    args = ap.parse_args()
    FEATURE_SOURCE = args.feature_source
    if FEATURE_SOURCE == "leaky":
        OUT_TABLE = LEAKY_TABLE
        MODEL_DIR = LEAKY_MODEL_DIR
        REPORT_DIR = LEAKY_REPORT_DIR
    tag = ("smoke" if args.smoke else "full") + "_" + FEATURE_SOURCE
    setup_log(tag)
    logger.info(f"walk-forward 重训启动 mode={'SMOKE' if args.smoke else 'FULL'} "
                f"feature_source={FEATURE_SOURCE} objective=regression "
                f"prereg=a81c24fafcc3f11f")

    years = ([int(y) for y in args.years.split(",")] if args.years
             else list(range(SCORE_START_YEAR, SCORE_END_YEAR + 1)))
    rescore = {int(y) for y in args.rescore.split(",")} if args.rescore else set()

    dq = sqlite3.connect(DQ_DB, timeout=120)
    audit = {"train_max_date": {}, "asof_rows": 0, "asof_violations": 0}
    fund, fin = (load_pit_tables(dq) if FEATURE_SOURCE == "pit" else (None, None))

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
            results[y] = {"rows": n, "best_iteration": blob["best_iteration"],
                          "val_rankic": blob.get("val_rankic"), "train_max_date": blob["train_max_date"]}
        except Exception as e:
            logger.error(f"M{y} 失败: {e}", exc_info=True)
            results[y] = {"error": str(e)}
            if args.smoke:
                dq.close()
                return 2
    dq.close()

    audit_path = os.path.join(REPORT_DIR, f"audit_{'smoke' if args.smoke else 'full'}.json")
    io.open(audit_path, "w", encoding="utf-8").write(json.dumps(
        {"audit": audit, "results": results, "ts": datetime.now().isoformat()}, ensure_ascii=False, indent=1))
    g1 = audit["asof_violations"] == 0
    logger.info(f"完成: {len(results)} 年 | G1 asof断言 violations={audit['asof_violations']} "
                f"({'PASS' if g1 else 'FAIL'}) | 明细 {audit_path}")
    return 0 if g1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
