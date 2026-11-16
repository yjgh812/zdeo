#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_ml_walkforward_v2.py — ML 因子管线 v2（不修改原脚本，独立新文件）
================================================================================
在 factor_ml_walkforward.py（v1，只读复用其基础设施）之上的三项改造：

任务1（P0-6）新增 5 个量价/规模特征（日线可算，无前视）:
    rev20     = -(过去20日累计收益)              短期反转（JFE 2022: A股最强预测源）
    vol20     = -std(日收益, 20日)                低波动异象
    illiq20   = +mean(|日收益|/成交额, 20日)      Amihud 非流动性溢价
    size      = -log(流通市值, 缺失回退总市值)     规模效应（daily_basic circ_mv, 当日as-of）
    max_ret20 = -max(日收益, 20日)                博彩偏好 MAX 效应
  方向定义"高分=预期高收益"便于单因子 IC 解读；ML 排序型学习不依赖方向。
  特征集: A=原15因子 | B=15+5新 | C=仅5新

任务2（P0-7）LightGBM 协议化终审（开源证券协议）:
    num_leaves=31 / max_depth=6 / lr=0.05 / n_estimators=500 + 早停50轮
    （验证集=训练窗尾部10%，按时间序切分，不shuffle）/ min_data_in_leaf=200
    （sklearn API: min_child_samples）/ 标签 RobustZScore（(y-median)/(1.4826*MAD), clip±3）
    / 5种子(0-4)预测均值。5种子×全部折全量跑，实测出数。

任务3（P0-8）Embargo + Purged 开关（默认开）:
    purge  : 训练集剔除 Fwd20 标签区间与 test 窗重叠的样本（train 尾部 fwd 天，
             即标签真实使用 test 窗内收益的泄漏通道）
    embargo: 每个 test 窗结束后 embargo_days(21) 个交易日内禁止任何后续折训练
             （测试窗后禁运；本滚动布局下自第3折起实际生效）

数据/清洗（相对 v1 的口径差异均已在报告声明）:
    价格源: dragon_quant_qfq.stock_daily_meta_qfq（前复权）——特征/Fwd20/回测收益
    清洗（参考 microstructure_factors_20260815.py）:
      ① 个股日收益 clip ±21%；② 污染日整日剔除（当日>5%个股|raw_ret|>21%即删该日；
      实测正常日该占比<0.5%、污染日16%~27%，阈值分离干净）
    ≤30元过滤: 用 meta 库原始(未复权)close，与 v1 口径一致；收益用 qfq
    daily_basic: trade_date 混合格式 format='mixed' 解析；2024-01-29 前
      volume_ratio/dv_ratio 为 NULL → 填 0（截面全 0 = z 中性）
    日历对齐 v1: 特征用90自然日预热计算后，日历截回 ≥ start，折结构与 v1 一致

⚠️ 财务前视污染声明（8/15审计）: A 集中 10 个财务因子（ag/droe/acc/sg/roa/pm/ato/
    opa/tbi/bl）按 report_date 期末日前向填充（复用 afd loader），含期末 as-of 前视
    （公告滞后未建模，最多约4个月）——含 A/B 特征集的结果为污染上界，解读必须声明；
    C 特征集纯量价无此问题。

回测口径与 v1 一致: 月度调仓 / Top10% 多头 / ≤30元非ST / 0.1% 单边成本 / next-bar
（信号收盘生成次日计收益）。

用法:
    python factor_ml_walkforward_v2.py --featureset A B C --model ridge          # EP默认开
    python factor_ml_walkforward_v2.py --featureset A B C --model ridge --no-embargo --no-purge
    python factor_ml_walkforward_v2.py --featureset B --model lgbm
    python factor_ml_walkforward_v2.py --make-report                             # 汇总出报告
"""
from __future__ import annotations

import argparse
import glob
import json
import pickle
import sqlite3
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge

warnings.filterwarnings("ignore")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).parent))
import alphalens_factor_diagnosis as afd  # noqa: E402
from alphalens_factor_diagnosis import FACTOR_REGISTRY  # noqa: E402
import factor_combo_walkforward as fcw  # noqa: E402

try:
    import lightgbm as lgb
    HAS_LGBM = True
    _LGBM_VER = lgb.__version__
except ImportError as _e:  # pragma: no cover
    HAS_LGBM = False
    _LGBM_ERR = str(_e)

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")
RESULTS_DIR = REPORT_DIR / "fml_v2"
CACHE_DIR = Path(r"D:\zcodesj\_cache")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)

BASE_FACTORS = ["bp", "ep", "turnover", "volume_ratio", "dv_ratio",
                "ag", "droe", "acc", "sg", "roa", "pm", "ato", "opa", "tbi", "bl"]
DB_FACTORS = ["bp", "ep", "turnover", "volume_ratio", "dv_ratio"]       # 自建loader(混合日期+填0)
FINANCE_FACTORS = ["ag", "droe", "acc", "sg", "roa", "pm", "ato", "opa", "tbi", "bl"]  # ⚠️期末as-of前视
NEW_FACTORS = ["rev20", "vol20", "illiq20", "size", "max_ret20"]
FEATURESETS = {"A": list(BASE_FACTORS), "B": BASE_FACTORS + NEW_FACTORS, "C": list(NEW_FACTORS)}
LGBM_SEEDS = (0, 1, 2, 3, 4)
POLLUTION_THRESHOLD = 0.05


# ═══════════════════════════════════════════════════════
#  数据加载与面板（宽表 + 磁盘缓存）
# ═══════════════════════════════════════════════════════

def load_qfq_pivots(start_int: int, end_int: int):
    """qfq 前复权 close/amount 宽表 + 污染日检测（整日剔除在调用方执行）"""
    conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=120)
    try:
        df = pd.read_sql_query(
            "SELECT code, date, close, amount FROM stock_daily_meta_qfq "
            "WHERE date BETWEEN ? AND ? AND close > 0 ORDER BY date, code",
            conn, params=[start_int, end_int])
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    df = df.drop_duplicates(["code", "date"], keep="last")
    close = df.pivot(index="date", columns="code", values="close").sort_index()
    amount = df.pivot(index="date", columns="code", values="amount").sort_index()
    return close, amount


def load_raw_close_pivot(start_int: int, end_int: int) -> pd.DataFrame:
    """原始(未复权)close 宽表——仅用于 ≤30 元过滤（与 v1 口径一致）"""
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=120)
    try:
        df = pd.read_sql_query(
            "SELECT code, date, close FROM stock_daily_meta "
            "WHERE date BETWEEN ? AND ? AND close > 0 ORDER BY date, code",
            conn, params=[start_int, end_int])
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    piv = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    return piv


def load_daily_basic_full(start_int: int, end_int: int) -> pd.DataFrame:
    """daily_basic 全量（trade_date 混合格式 format='mixed'）"""
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=120)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, turnover_rate, volume_ratio, dv_ratio, "
            "pb, pe_ttm, circ_mv, total_mv FROM daily_basic WHERE trade_date BETWEEN ? AND ?",
            conn, params=[start_int, end_int])
    finally:
        conn.close()
    df["code"] = df["ts_code"].astype(str).str.split(".").str[0]
    df["date"] = pd.to_datetime(df["trade_date"].astype(str), format="mixed")
    df = df.drop_duplicates(["code", "date"], keep="last")
    for c in ["turnover_rate", "volume_ratio", "dv_ratio", "pb", "pe_ttm", "circ_mv", "total_mv"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def detect_polluted_days(ret_raw: pd.DataFrame, ratio_th=POLLUTION_THRESHOLD, min_stocks=100):
    """污染日: 当日 |raw_ret|>21% 个股占比 >5% 且绝对数≥100（正常日<0.5%）"""
    bad = (ret_raw.abs() > 0.21).sum(axis=1)
    cnt = ret_raw.notna().sum(axis=1)
    ratio = (bad / cnt.replace(0, np.nan)).fillna(0.0)
    return ratio[(ratio > ratio_th) & (bad >= min_stocks)].index


def _z_stacked_to_wide(s: pd.Series) -> pd.DataFrame:
    """stacked(date,code) → fcw.cs_rank_z → 宽表 float32（z 语义与 v1 完全一致）"""
    z = fcw.cs_rank_z(s.dropna()).dropna()
    return z.unstack("code").astype(np.float32)


def _z_wide(df: pd.DataFrame) -> pd.DataFrame:
    """宽表截面 rank→z（与 fcw.cs_rank_z 数值一致: (rank-(n+1)/2)/(n/√12)）"""
    r = df.rank(axis=1)
    n = df.notna().sum(axis=1)
    z = (r.sub((n + 1) / 2, axis=0)).div(n / np.sqrt(12), axis=0)
    return z.astype(np.float32)


def build_panels(start: str, end: str, fwd: int, warmup: int = 90, use_cache=True, verbose=True):
    """全 20 因子宽表面板 + Fwd20 + 清洁收益 + 原始close。磁盘缓存。"""
    cache = CACHE_DIR / f"fml_v2_panels_{start}_{end}_fwd{fwd}.pkl"
    if use_cache and cache.exists():
        if verbose:
            print(f"      面板缓存命中: {cache.name}")
        with open(cache, "rb") as f:
            return pickle.load(f)

    t0 = time.time()
    warm_start = (pd.Timestamp(start) - pd.Timedelta(days=warmup)).strftime("%Y%m%d")
    close, amount = load_qfq_pivots(int(warm_start), int(end))
    if verbose:
        print(f"      qfq {close.shape[0]}日 × {close.shape[1]}股 ({warm_start}~{end}) ({time.time()-t0:.0f}s)", flush=True)

    # ── 清洗①: 污染日整日剔除 ──
    raw_ret = close / close.shift(1) - 1.0
    polluted = detect_polluted_days(raw_ret)
    if verbose:
        print(f"      [清洗] 污染日整日剔除 {len(polluted)} 天: {[d.strftime('%Y%m%d') for d in polluted]}")
    close = close.drop(index=polluted)
    amount = amount.drop(index=polluted)
    # ── 清洗②: 个股日收益 clip ±21%（特征/标签/回测统一口径）──
    ret = (close / close.shift(1) - 1.0).clip(-0.21, 0.21)
    logret = np.log1p(ret)

    # Fwd20: 清洁对数收益滚动 fwd 日求和再前移（min_periods 15，标签有界且不含污染日）
    fwd_w = np.expm1(logret.rolling(fwd, min_periods=max(10, int(fwd * 0.75))).sum().shift(-fwd))

    # ── 新 5 特征（量价/规模，无前视；滚动预热由 warmup 提供）──
    raw_feats = {
        "rev20": -np.expm1(logret.rolling(20, min_periods=15).sum()),
        "vol20": -ret.rolling(20, min_periods=15).std(),
        "illiq20": (ret.abs() / amount.where(amount > 0)).rolling(20, min_periods=10).mean(),
        "max_ret20": -ret.rolling(20, min_periods=15).max(),
    }

    # ── daily_basic（自建 loader: format='mixed' + vol/dv NULL→0）──
    db = load_daily_basic_full(int(warm_start), int(end))
    mv = db["circ_mv"].where(db["circ_mv"] > 0, db["total_mv"])
    db = db.assign(size=-np.log(mv.where(mv > 0)))
    dbi = db.set_index(["date", "code"]).sort_index()
    raw_feats["bp"] = dbi["pb"].where(dbi["pb"] > 0).rsub(1.0)
    raw_feats["ep"] = dbi["pe_ttm"].where(dbi["pe_ttm"] > 0).rsub(1.0)
    raw_feats["turnover"] = -dbi["turnover_rate"].where(dbi["turnover_rate"] > 0)
    raw_feats["volume_ratio"] = dbi["volume_ratio"].fillna(0.0)   # 2024-01-29 前 NULL→0（中性）
    raw_feats["dv_ratio"] = dbi["dv_ratio"].fillna(0.0)
    raw_feats["size"] = dbi["size"]

    wides = {}
    for name, v in raw_feats.items():
        if isinstance(v, pd.Series):            # stacked(date,code)
            wides[name] = _z_stacked_to_wide(v)
        else:                                   # 宽表（qfq 滚动特征）
            wides[name] = _z_wide(v)
    if verbose:
        print(f"      量价+daily_basic 10 因子完成 ({time.time()-t0:.0f}s)", flush=True)

    # ── 财务 10 因子（复用 afd.FACTOR_REGISTRY；⚠️期末 as-of 前视）──
    afd.START_DATE = f"{int(start[:4]) - 1}{start[4:]}"
    afd.END_DATE = end
    for name in FINANCE_FACTORS:
        desc, loader = FACTOR_REGISTRY[name]
        s = loader(close.index)
        wides[name] = _z_stacked_to_wide(s)
        if verbose:
            print(f"      {name:<12} ({desc}) n={len(s):,} ({time.time()-t0:.0f}s)", flush=True)

    # ── 日历截回 ≥ start（折结构对齐 v1）──
    cal_full = close.index
    keep = cal_full[cal_full >= pd.Timestamp(start)]
    wides = {k: v.reindex(keep).astype(np.float32) for k, v in wides.items()}
    fwd_w = fwd_w.reindex(keep)
    ret_bt = ret.reindex(keep)
    raw_close = load_raw_close_pivot(int(start), int(end)).reindex(keep)

    panels = {"calendar": keep, "wides": wides, "fwd": fwd_w, "ret_bt": ret_bt,
              "raw_close": raw_close, "polluted": [d.strftime("%Y%m%d") for d in polluted]}
    if use_cache:
        with open(cache, "wb") as f:
            pickle.dump(panels, f, protocol=4)
        if verbose:
            print(f"      面板缓存已存: {cache.name} ({cache.stat().st_size/1e6:.0f}MB, {time.time()-t0:.0f}s)")
    return panels


# ═══════════════════════════════════════════════════════
#  截面快照 + 训练矩阵（宽表向量化，语义与 v1 等价）
# ═══════════════════════════════════════════════════════

class AlignedFeatures:
    """把 features 对齐到公共列（并集），提供快照与训练矩阵的快速构建"""

    def __init__(self, wides: dict, features: list, calendar: pd.DatetimeIndex):
        self.features = list(features)
        self.cal = calendar
        cols = sorted(set().union(*[set(wides[f].columns) for f in features]))
        self.cols = cols
        self.W = {f: wides[f].reindex(columns=cols).astype(np.float32) for f in features}
        self.avail = {f: np.flatnonzero(self.W[f].notna().any(axis=1).values) for f in features}

    def snapshot(self, d_pos: int, max_lookback=250):
        """≤d_pos 最近截面 z（缺失填0中性），返回 (DataFrame[features], stale_max)"""
        rows, stale_max = [], 0
        for f in self.features:
            av = self.avail[f]
            j = av.searchsorted(d_pos, side="right") - 1
            if j < 0 or d_pos - av[j] > max_lookback:
                rows.append(pd.Series(np.nan, index=self.cols, name=f))
                stale_max = 10 ** 9
                continue
            stale_max = max(stale_max, int(d_pos - av[j]))
            rows.append(self.W[f].iloc[av[j]].rename(f))
        snap = pd.concat(rows, axis=1).fillna(0.0)
        return snap[self.features], stale_max

    def train_matrix(self, p0: int, p1: int, fwd_w: pd.DataFrame, banned_pos=None):
        """[p0,p1] 训练矩阵。行=任一特征非NaN的(date,code)（与v1 concat并集语义一致），
        特征缺失填0；y=Fwd20（NaN行剔除）；返回 X(float32), y(float64), 行日期位置"""
        n_win = p1 - p0 + 1
        M = np.zeros((n_win, len(self.cols)), dtype=bool)
        for f in self.features:
            M |= self.W[f].iloc[p0:p1 + 1].notna().values
        flat = M.ravel()
        X = np.empty((int(flat.sum()), len(self.features)), dtype=np.float32)
        for i, f in enumerate(self.features):
            X[:, i] = np.nan_to_num(self.W[f].iloc[p0:p1 + 1].values, nan=0.0).ravel()[flat]
        y = fwd_w.iloc[p0:p1 + 1].values.ravel()[flat].astype(np.float64)
        rowpos = np.repeat(np.arange(p0, p1 + 1), len(self.cols))[flat]
        ok = ~np.isnan(y)
        X, y, rowpos = X[ok], y[ok], rowpos[ok]
        if banned_pos is not None and len(banned_pos):
            keep = ~np.isin(rowpos, banned_pos)
            X, y, rowpos = X[keep], y[keep], rowpos[keep]
        return X, y, rowpos


# ═══════════════════════════════════════════════════════
#  模型
# ═══════════════════════════════════════════════════════

def robust_zscore(y: np.ndarray) -> np.ndarray:
    """RobustZScore: (y-median)/(1.4826*MAD)，clip ±3（开源协议标签处理）"""
    med = np.median(y)
    mad = np.median(np.abs(y - med))
    z = (y - med) / (1.4826 * mad) if mad > 0 else y - med
    return np.clip(z, -3.0, 3.0)


class LgbmEnsemble:
    def __init__(self, models):
        self.models = models

    def predict(self, X):
        return np.mean([m.predict(X) for m in self.models], axis=0)


def fit_models(model_kind, Xtr, ytr, rng, max_train_samples=None, seed=42):
    """Ridge 与 v1 同协议（子采样后 L2）；LGBM 按开源协议（尾部10%时序验证+早停+5种子）"""
    meta = {}
    if model_kind == "ridge":
        if max_train_samples and len(ytr) > max_train_samples:
            idx = rng.choice(len(ytr), max_train_samples, replace=False)
            Xtr, ytr = Xtr[idx], ytr[idx]
        model = Ridge(alpha=1.0, random_state=seed)
        model.fit(Xtr.astype(np.float64), ytr)
        meta["n_fit"] = int(len(ytr))
        return model, meta
    if model_kind == "lgbm":
        n_val = max(1000, int(0.10 * len(ytr)))
        Xv, yv = Xtr[-n_val:], ytr[-n_val:]        # 尾部10%（时序，不shuffle）
        Xf, yf = Xtr[:-n_val], ytr[:-n_val]
        if max_train_samples and len(yf) > max_train_samples:
            idx = rng.choice(len(yf), max_train_samples, replace=False)
            Xf, yf = Xf[idx], yf[idx]
        med = np.median(yf)
        mad = np.median(np.abs(yf - med)) or 1.0
        yf_r = robust_zscore(yf)
        yv_r = np.clip((yv - med) / (1.4826 * mad), -3.0, 3.0)
        models, trees = [], []
        for sd in LGBM_SEEDS:
            m = lgb.LGBMRegressor(
                num_leaves=31, max_depth=6, learning_rate=0.05, n_estimators=500,
                min_child_samples=200, random_state=sd, n_jobs=-1, verbosity=-1)
            m.fit(Xf.astype(np.float64), yf_r, eval_set=[(Xv.astype(np.float64), yv_r)],
                  callbacks=[lgb.early_stopping(50, verbose=False)])
            models.append(m)
            trees.append(int(m.best_iteration_) if m.best_iteration_ else 500)
        meta.update(trees_used=trees, n_fit=int(len(yf)), n_val=int(len(yv)))
        return LgbmEnsemble(models), meta
    raise KeyError(model_kind)


# ═══════════════════════════════════════════════════════
#  Walk-Forward（purge / embargo 开关）
# ═══════════════════════════════════════════════════════

def walk_forward_ml_v2(aligned: AlignedFeatures, fwd_w: pd.DataFrame, model_kind,
                       train_d=500, test_d=60, fwd_days=20,
                       use_purge=True, use_embargo=True, embargo_days=21,
                       max_train_samples=None, seed=42, verbose=True):
    """滚动 train(500d)→test(60d) 冻结预测。
    purge  : 剔除 train 尾部 fwd_days 天（其标签区间 (t, t+20] 与 test 窗重叠）
    embargo: 历次 test 窗结束后 embargo_days 交易日内禁止后续折训练"""
    cal = aligned.cal
    n = len(cal)
    fwd_al = fwd_w.reindex(columns=aligned.cols)   # 对齐特征列并集（多余列NaN）
    rng = np.random.default_rng(seed)
    oos_ic, oos_dates = [], []
    folds, fold_meta = [], []
    imp_acc = {f: 0.0 for f in aligned.features} if model_kind == "lgbm" else None
    n_imp = 0
    trees_all = []
    banned_pos: set[int] = set()
    ridge_coef_acc = None
    t_total = time.time()
    s, fold_id = 0, 0
    while s + train_d + test_d <= n:
        fold_id += 1
        p0, p1 = s, s + train_d - 1
        te0, te1 = s + train_d, s + train_d + test_d - 1

        drop_pos = set()
        n_purge = n_emb = 0
        if use_purge:
            drop_pos.update(range(max(s, te0 - fwd_days), te0))   # 标签重叠 → 剔 train 尾部 fwd 天
            n_purge = len(drop_pos)
        if use_embargo:
            emb = {p for p in banned_pos if p0 <= p <= p1}
            drop_pos |= emb
            n_emb = len(emb)

        Xtr, ytr, _ = aligned.train_matrix(p0, p1, fwd_al, banned_pos=sorted(drop_pos))
        if len(ytr) < 1000:
            s += test_d
            continue
        t0 = time.time()
        predictor, fmeta = fit_models(model_kind, Xtr, ytr, rng,
                                      max_train_samples=max_train_samples, seed=seed)
        dt = time.time() - t0
        if imp_acc is not None:
            for m in predictor.models:
                gain = m.booster_.feature_importance(importance_type="gain")
                tot = gain.sum()
                if tot > 0:
                    for f, g in zip(aligned.features, gain):
                        imp_acc[f] += g / tot
                    n_imp += 1
            trees_all.extend(fmeta.get("trees_used", []))
        if model_kind == "ridge":
            c = np.abs(predictor.coef_)
            ridge_coef_acc = c if ridge_coef_acc is None else ridge_coef_acc + c
        folds.append({"fold": fold_id, "train_end": cal[p1], "predictor": predictor})
        fold_meta.append({"fold": fold_id, "train": [str(cal[p0].date()), str(cal[p1].date())],
                          "test": [str(cal[te0].date()), str(cal[te1].date())],
                          "n_train": int(len(ytr)), "n_purge_days": n_purge,
                          "n_embargo_days": n_emb, "fit_s": round(dt, 1)})

        ic_day = []
        for dp in range(te0, te1 + 1):
            xs, _ = aligned.snapshot(dp)
            if len(xs) < 30:
                continue
            y_day = fwd_al.iloc[dp].dropna()
            common = xs.index.intersection(y_day.index)
            if len(common) < 30:
                continue
            pred = pd.Series(predictor.predict(xs.loc[common].values.astype(np.float64)), index=common)
            ic, _ = spearmanr(pred, y_day.loc[common])
            if not np.isnan(ic):
                ic_day.append((cal[dp], ic))
        if ic_day:
            ics = pd.Series(dict(ic_day))
            oos_ic.extend(ics.values.tolist())
            oos_dates.extend(ics.index.tolist())
            if verbose:
                print(f"      [折{fold_id:>2}] tr {cal[p0].date()}~{cal[p1].date()} → te {cal[te0].date()}~{cal[te1].date()} "
                      f"n={len(ytr):,} purge={n_purge}d emb={n_emb}d fit={dt:.0f}s IC={ics.mean():+.4f}", flush=True)
        if use_embargo:
            banned_pos.update(range(te1 + 1, min(te1 + 1 + embargo_days, n)))
        s += test_d

    arr = np.array(oos_ic)
    if len(arr) == 0:
        summary = {"mean_ic": None, "icir": None, "pos_rate": None, "n_days": 0, "n_folds": fold_id}
    else:
        mu, sd = arr.mean(), arr.std()
        summary = {"mean_ic": round(float(mu), 4), "icir": round(float(mu / sd), 3),
                   "pos_rate": round(float((arr > 0).mean()), 4),
                   "n_days": int(len(arr)), "n_folds": fold_id}
    importance = None
    if model_kind == "lgbm" and n_imp:
        importance = {f: round(v / n_imp, 5) for f, v in
                      sorted(imp_acc.items(), key=lambda kv: -kv[1])}
    if model_kind == "ridge" and ridge_coef_acc is not None:
        importance = {f: round(float(v) / fold_id, 5) for f, v in
                      sorted(zip(aligned.features, ridge_coef_acc), key=lambda kv: -kv[1])}
    if verbose:
        print(f"      WF 完成: {fold_id} 折 {time.time()-t_total:.0f}s | IC={summary['mean_ic']} "
              f"ICIR={summary['icir']} 正率={summary['pos_rate']}", flush=True)
    return {"summary": summary, "folds": folds, "fold_meta": fold_meta, "importance": importance,
            "trees_used_mean": round(float(np.mean(trees_all)), 1) if trees_all else None,
            "oos_dates": oos_dates}


# ═══════════════════════════════════════════════════════
#  月度组合回测（next-bar；qfq收益 + 原始close≤30过滤）
# ═══════════════════════════════════════════════════════

def model_for_date(folds, d_pos, cal):
    best = None
    for f in folds:
        if cal.searchsorted(f["train_end"]) < d_pos:
            best = f["predictor"]
        else:
            break
    return best


def _month_window(idx, rd_):
    next_m = rd_ + pd.offsets.MonthBegin(1)
    return idx[(idx >= rd_) & (idx < next_m)]


def simulate_ml_portfolio(folds, aligned, ret_bt, raw_close, st_codes, reb_pos,
                          top_pct=0.10, cost=0.001, max_stale=60):
    """Top10% 多头月度组合。≤30 用原始 close，收益用清洗后 qfq，next-bar，含换手成本。
    返回 (nav_df, 月收益数组)"""
    cal = aligned.cal
    nav, navs, dates_out = 1.0, [], []
    month_rets = []
    prev_holdings = None
    for rp in reb_pos:
        rd_ = cal[rp]
        month_days = _month_window(cal, rd_)
        if len(month_days) == 0:
            continue
        nav_start = nav
        model = model_for_date(folds, rp, cal)
        if model is None:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        xs, stale = aligned.snapshot(rp)
        if len(xs) < 50 or stale > max_stale:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        day_prices = raw_close.loc[rd_] if rd_ in raw_close.index else None
        if day_prices is None:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        valid = [c for c in xs.index
                 if c not in st_codes and c in day_prices.index
                 and not np.isnan(day_prices[c]) and day_prices[c] <= 30]
        if len(valid) < 50:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        pred = pd.Series(model.predict(xs.loc[valid].values.astype(np.float64)), index=valid)
        n_top = max(5, int(len(pred) * top_pct))
        holdings = set(pred.nlargest(n_top).index)
        if prev_holdings is not None:
            turnover = 1 - len(holdings & prev_holdings) / max(len(holdings), 1)
            nav *= (1 - cost * turnover)
        prev_holdings = holdings
        hold_days = month_days[1:] if len(month_days) > 1 else month_days   # next-bar
        for d in hold_days:
            if d in ret_bt.index:
                r = ret_bt.loc[d, list(holdings)]
                nav *= (1 + r.fillna(0.0).mean())
            navs.append(nav); dates_out.append(d)
        month_rets.append(nav / nav_start - 1.0)
    return pd.DataFrame({"nav": navs}, index=pd.DatetimeIndex(dates_out)), np.array(month_rets)


def simulate_ew(ret_bt, raw_close, st_codes, reb_pos):
    """等权基准（≤30元非ST全域，next-bar）"""
    cal = ret_bt.index
    nav, navs, dates_out = 1.0, [], []
    month_rets = []
    prev_valid = None
    for rp in reb_pos:
        rd_ = cal[rp]
        month_days = _month_window(cal, rd_)
        if len(month_days) == 0:
            continue
        nav_start = nav
        day_prices = raw_close.loc[rd_] if rd_ in raw_close.index else None
        if day_prices is None:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        valid = [c for c in day_prices.index
                 if c not in st_codes and not np.isnan(day_prices[c]) and day_prices[c] <= 30]
        if len(valid) < 50:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        holdings = set(valid)
        hold_days = month_days[1:] if len(month_days) > 1 else month_days
        for d in hold_days:
            if d in ret_bt.index:
                r = ret_bt.loc[d, list(holdings)]
                nav *= (1 + r.fillna(0.0).mean())
            navs.append(nav); dates_out.append(d)
        month_rets.append(nav / nav_start - 1.0)
        prev_valid = valid
    return pd.DataFrame({"nav": navs}, index=pd.DatetimeIndex(dates_out)), np.array(month_rets)


_IC_CACHE: dict[str, pd.Series] = {}


def factor_ic_series(name: str, wides: dict, fwd_w: pd.DataFrame, min_n=30) -> pd.Series:
    """单因子逐日 Spearman IC（宽表 rank→corrwith，全日历一次计算进程内缓存）"""
    if name in _IC_CACHE:
        return _IC_CACHE[name]
    zw = wides[name]
    both = zw.notna() & fwd_w.notna()
    n_ok = both.sum(axis=1)
    rk_z = zw.where(both).rank(axis=1)
    rk_f = fwd_w.where(both).rank(axis=1)
    cov = rk_z.corrwith(rk_f, axis=1)
    cov = cov[n_ok >= min_n].dropna()
    _IC_CACHE[name] = cov
    return cov


# ═══════════════════════════════════════════════════════
#  报告生成（--make-report）
# ═══════════════════════════════════════════════════════

def build_report() -> int:
    jsons = sorted(glob.glob(str(RESULTS_DIR / "*.json")))
    rows = []
    for jf in jsons:
        try:
            with open(jf, encoding="utf-8") as f:
                rows.append(json.load(f))
        except Exception as e:
            print(f"      跳过 {jf}: {e}")
    rows = [r for r in rows if r.get("wf", {}).get("mean_ic") is not None]
    if not rows:
        print(f"      无有效 run JSON（{RESULTS_DIR}）")
        return 1

    def get(fs, md, ep, pu):
        return next((r for r in rows if r["featureset"] == fs and r["model"] == md
                     and bool(r["embargo"]) == ep and bool(r["purge"]) == pu), None)

    today = datetime.now().strftime("%Y%m%d")
    rpt = REPORT_DIR / f"FACTOR_ML_V2_{today}.md"
    p0 = rows[0]
    L = [
        "# 因子 ML 管线 v2 终审报告（P0-6 量价特征 / P0-7 LightGBM 协议 / P0-8 Embargo+Purged）",
        "",
        f"- 生成: {datetime.now().isoformat(timespec='seconds')} | 脚本: factor_ml_walkforward_v2.py（未改原脚本）",
        f"- 区间: {p0['start']} ~ {p0['end']} | train=500d / test=60d / fwd=20d | 折数={p0['wf']['n_folds']}",
        f"- 价格源: dragon_quant_qfq.stock_daily_meta_qfq（前复权，唯一收益源）；≤30元过滤用 meta 原始 close（与v1一致）",
        f"- 清洗: 收益 clip±21% + 污染日整日剔除 {len(p0['cleaning']['polluted_days_dropped'])} 天"
        f"（{', '.join(p0['cleaning']['polluted_days_dropped'])}）",
        f"- 回测: 月度调仓 Top10% 多头 / ≤30元非ST / 0.1%单边 / next-bar；Ridge 子采样600k；"
        f"LGBM 开源协议(num_leaves31/depth6/lr0.05/500树早停50/min_leaf200/RobustZScore/5种子均值)",
        "",
        "## 一、主对照表：特征集 × 模型 × Embargo/Purge 开关（全部样本外）",
        "",
        "EP = embargo(21d)+purge 开（防泄漏，默认）；nEnP = 两者全关（v1 等价口径）。",
        "",
        "| 特征集-模型 [开关] | OOS IC | ICIR | 正率 | 天数 | 组合CAGR | Sharpe | MDD | 总收益 | 多头月均 | 多头年化超额 |",
        "|:--|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|",
    ]
    order = [("A", "ridge"), ("B", "ridge"), ("C", "ridge"), ("A", "lgbm"), ("B", "lgbm"), ("C", "lgbm")]
    for fs, md in order:
        for ep, pu, lab in [(True, True, "EP"), (False, False, "nEnP")]:
            r = get(fs, md, ep, pu)
            if r is None:
                continue
            w, p, t = r["wf"], r["portfolio"], r["top10_monthly"]
            ea = f"{t['excess_ann']:+.1%}" if t.get("excess_ann") is not None else "n/a"
            L.append(f"| {fs}-{md} [{lab}] | {w['mean_ic']:+.4f} | {w['icir']:+.3f} | {w['pos_rate']:.0%} | "
                     f"{w['n_days']} | {p['cagr']:+.1%} | {p['sharpe']:+.3f} | {p['mdd']:+.1%} | {p['total']:+.1%} | "
                     f"{t['mean']:+.2%} | {ea} |")

    # 基准
    L += ["", "### 基准（同区间同口径）", "", "| 基准 | CAGR | Sharpe | MDD | 总收益 |", "|:--|:--:|:--:|:--:|:--:|"]
    for k, st_ in p0["benchmarks"].items():
        L.append(f"| {k} | {st_['cagr']:+.1%} | {st_['sharpe']:+.3f} | {st_['mdd']:+.1%} | {st_['total']:+.1%} |")

    # 二、Embargo/Purge 对照
    L += ["", "## 二、Embargo+Purge 开关对照（泄漏虚高量化）", "",
          "| run | OOS IC(EP) | OOS IC(nEnP) | ΔIC(泄漏虚高) | ICIR(EP) | ICIR(nEnP) | ΔICIR | CAGR(EP) | CAGR(nEnP) |",
          "|:--|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|"]
    for fs, md in order:
        ep, ne = get(fs, md, True, True), get(fs, md, False, False)
        if ep is None or ne is None:
            continue
        d = ne["wf"]["mean_ic"] - ep["wf"]["mean_ic"]
        L.append(f"| {fs}-{md} | {ep['wf']['mean_ic']:+.4f} | {ne['wf']['mean_ic']:+.4f} | {d:+.4f} | "
                 f"{ep['wf']['icir']:+.3f} | {ne['wf']['icir']:+.3f} | {ne['wf']['icir']-ep['wf']['icir']:+.3f} | "
                 f"{ep['portfolio']['cagr']:+.1%} | {ne['portfolio']['cagr']:+.1%} |")

    # 三、单因子同期 OOS（取 B-ridge EP 的测试日）
    bref = get("B", "ridge", True, True) or rows[0]
    if bref.get("single_factor_oos"):
        L += ["", "## 三、单因子同期 OOS（特征集B-ridge[EP] 测试日集合）", "",
              "| 因子 | IC | ICIR | 正率 | 类别 |", "|:--:|:--:|:--:|:--:|:--|"]
        for name, s in sorted(bref["single_factor_oos"].items(), key=lambda kv: -abs(kv[1]["ic"])):
            cat = "量价新" if name in NEW_FACTORS else ("daily_basic" if name in DB_FACTORS else "财务⚠️前视")
            L.append(f"| {name} | {s['ic']:+.4f} | {s['icir']:+.3f} | {s['pos_rate']:.0%} | {cat} |")

    # 四、特征重要性
    for md in ("lgbm", "ridge"):
        cands = [x for x in rows if x["model"] == md and x.get("feature_importance")]
        r = next((x for x in cands if x["featureset"] == "B"), cands[0] if cands else None)
        if r:
            head = "四" if md == "lgbm" else "五"
            L += ["", f"## {head}、特征重要性（{md}，{'gain折均/5种子' if md=='lgbm' else '|coef|折均'}，特征集{r['featureset']}）", "",
                  "| 特征 | 重要性 |", "|:--:|:--:|"]
            for name, v in list(r["feature_importance"].items())[:20]:
                L.append(f"| {name} | {v:.4f} |")
            if md == "lgbm" and r.get("lgbm_trees_used_mean"):
                L.append(f"\n- LGBM 早停后平均树数: {r['lgbm_trees_used_mean']}")

    # 五、结论三问（用真实数字计算）
    def _d(md, field):  # B - A 增量（EP口径）
        b, a = get("B", md, True, True), get("A", md, True, True)
        if b is None or a is None:
            return None
        if field == "ic":
            return b["wf"]["mean_ic"] - a["wf"]["mean_ic"]
        if field == "raw":
            return b, a

    L += ["", "## 六、结论三问（数字全部来自上表实测）", ""]
    inc = _d("ridge", "ic")
    if inc is not None:
        b, a = _d("ridge", "raw")
        c_ridge = get("C", "ridge", True, True)
        c_line = (f"   C vs A（纯量价替代）: IC {c_ridge['wf']['mean_ic']:+.4f}。"
                  if c_ridge else "   C vs A: 未运行。")
        L += [f"**① 量价特征增量（B vs A，Ridge，EP口径）: IC {a['wf']['mean_ic']:+.4f} → {b['wf']['mean_ic']:+.4f} "
              f"（Δ{inc:+.4f}），CAGR {a['portfolio']['cagr']:+.1%} → {b['portfolio']['cagr']:+.1%}"
              f"（Δ{b['portfolio']['cagr']-a['portfolio']['cagr']:+.1%}）**",
              c_line]
    lgb_b, ridge_b = get("B", "lgbm", True, True), get("B", "ridge", True, True)
    if lgb_b and ridge_b:
        verdict = "不能翻案" if lgb_b["wf"]["mean_ic"] <= ridge_b["wf"]["mean_ic"] else "翻案成功"
        L += [f"**② LightGBM 能否翻案（B，EP口径）: LGBM IC {lgb_b['wf']['mean_ic']:+.4f} vs Ridge {ridge_b['wf']['mean_ic']:+.4f}"
              f" → {verdict}**（CAGR {lgb_b['portfolio']['cagr']:+.1%} vs {ridge_b['portfolio']['cagr']:+.1%}）"]
    ep, ne = get("B", "ridge", True, True), get("B", "ridge", False, False)
    if ep and ne:
        d = ne["wf"]["mean_ic"] - ep["wf"]["mean_ic"]
        L += [f"**③ 泄漏虚高幅度（B-Ridge，nEnP − EP）: ΔIC {d:+.4f}"
              f"（{ne['wf']['mean_ic']:+.4f} → {ep['wf']['mean_ic']:+.4f}），ΔICIR {ne['wf']['icir']-ep['wf']['icir']:+.3f}**"]
    L += [
        "",
        "## 局限声明",
        "",
        "- ⚠️ 财务因子前视污染（8/15审计）: ag/droe/acc/sg/roa/pm/ato/opa/tbi/bl 按 report_date 期末日前向填充"
        "（复用 afd loader），公告滞后未建模（最多约4个月 as-of 前视）——A/B 特征集结果为污染上界；C 纯量价无此问题。",
        "- 2024-01-29 前 daily_basic 的 volume_ratio/dv_ratio 为 NULL → 填 0（截面全0=中性）;"
        " 2024-01 前 daily_basic 仅每月1个快照日（2020-2023 每年8-9天），bp/ep/turnover/size 为月频快照因子（v1 同形态）。",
        "- 价格源 qfq 前复权（v1 为未复权，除权失真消除属口径改进）；≤30元过滤用原始 close。",
        "- 污染日剔除改变 2026-06 后的日历（3天），此后折窗边界与 v1 最多偏移3个交易日。",
        "- 停牌以收益0近似；缺失因子截面 z=0（中性）填充；组合等权、next-bar、0.1%单边换手成本。",
        "- LGBM 标签 RobustZScore 为单调变换（clip±3 内），不影响逐日 Spearman 排序。",
    ]
    rpt.write_text("\n".join(L), encoding="utf-8")
    print(f"      报告已生成: {rpt}（{len(rows)} 个 run）")
    return 0


# ═══════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="ML 因子管线 v2: 量价特征+LGBM协议+Embargo/Purge")
    ap.add_argument("--start", default="20200115")
    ap.add_argument("--end", default="20260731")
    ap.add_argument("--featureset", nargs="+", default=["B"], choices=["A", "B", "C"])
    ap.add_argument("--model", nargs="+", default=["ridge"], choices=["ridge", "lgbm"])
    ap.add_argument("--embargo", dest="embargo", action="store_true", default=True,
                    help="测试窗后21交易日禁运（默认开）")
    ap.add_argument("--no-embargo", dest="embargo", action="store_false")
    ap.add_argument("--purge", dest="purge", action="store_true", default=True,
                    help="剔除训练集标签与test窗重叠样本（默认开）")
    ap.add_argument("--no-purge", dest="purge", action="store_false")
    ap.add_argument("--embargo-days", type=int, default=21)
    ap.add_argument("--train", type=int, default=500)
    ap.add_argument("--test", type=int, default=60)
    ap.add_argument("--fwd", type=int, default=20)
    ap.add_argument("--cost", type=float, default=0.001)
    ap.add_argument("--max-train-samples", type=int, default=600_000)
    ap.add_argument("--warmup", type=int, default=90, help="特征rolling预热日历天数")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--make-report", action="store_true")
    a = ap.parse_args()

    if a.make_report:
        return build_report()
    if "lgbm" in a.model and not HAS_LGBM:
        print(f"[错误] lightgbm 不可用: {_LGBM_ERR}")
        return 2

    t0 = time.time()
    print(f"\n{'#' * 78}")
    print(f"  ML 因子管线 v2 | 特征集 {a.featureset} | 模型 {a.model}")
    print(f"  区间 {a.start}~{a.end} | train={a.train}d test={a.test}d fwd={a.fwd}d | "
          f"purge={'ON' if a.purge else 'OFF'} embargo={'ON' if a.embargo else 'OFF'}({a.embargo_days}d) | "
          f"cost={a.cost:.2%} | LGBM版本={_LGBM_VER if HAS_LGBM else 'N/A'}")
    print(f"{'#' * 78}", flush=True)

    # ── 1. 面板（含清洗，磁盘缓存）──
    print("\n[1/5] 构建面板（qfq清洗 + 20因子截面z宽表）...")
    panels = build_panels(a.start, a.end, a.fwd, warmup=a.warmup)
    cal = panels["calendar"]
    wides, fwd_w = panels["wides"], panels["fwd"]
    ret_bt, raw_close = panels["ret_bt"], panels["raw_close"]
    print(f"      清洁日历 {len(cal)} 天（{cal[0].date()} ~ {cal[-1].date()}），污染日已剔除 "
          f"{len(panels['polluted'])} 天")
    st_codes = fcw.load_st_codes()
    print(f"      ST {len(st_codes)} 只（剔除）")

    # ── 2. 基准组合（等权；一次性）──
    print("\n[2/5] 基准组合（等权≤30元，next-bar）...")
    reb_pos = [cal.searchsorted(d) for d in
               fcw.month_starts(cal, from_date=cal[min(a.train, len(cal) - 1)])]
    reb_pos = [p for p in reb_pos if p < len(cal)]
    print(f"      {len(reb_pos)} 个调仓月（自 {cal[reb_pos[0]].date()}）")
    ew_nav, ew_month = simulate_ew(ret_bt, raw_close, st_codes, reb_pos)
    ew_stats = fcw.perf_stats(ew_nav.nav, "等权基准≤30元")
    print(f"      等权: CAGR={ew_stats['cagr']:+.1%} Sharpe={ew_stats['sharpe']:+.3f} MDD={ew_stats['mdd']:+.1%}")

    # ── 3~4. 逐 (特征集×模型) WF + 回测 ──
    all_results = {}
    for fs in a.featureset:
        features = FEATURESETS[fs]
        aligned = AlignedFeatures(wides, features, cal)
        for model_kind in a.model:
            tag = f"{fs}_{model_kind}_{'E' if a.embargo else 'nE'}{'P' if a.purge else 'nP'}_{a.start}_{a.end}"
            print(f"\n[3/5] === 特征集 {fs}（{len(features)}特征）× {model_kind} | "
                  f"purge={'ON' if a.purge else 'OFF'} embargo={'ON' if a.embargo else 'OFF'} ===")
            t1 = time.time()
            wf = walk_forward_ml_v2(aligned, fwd_w, model_kind, train_d=a.train, test_d=a.test,
                                    fwd_days=a.fwd, use_purge=a.purge, use_embargo=a.embargo,
                                    embargo_days=a.embargo_days,
                                    max_train_samples=a.max_train_samples, seed=a.seed)
            wf_runtime = time.time() - t1

            print(f"[4/5] 组合回测 [{tag}] ...", flush=True)
            nav, month_rets = simulate_ml_portfolio(wf["folds"], aligned, ret_bt, raw_close,
                                                    st_codes, reb_pos, cost=a.cost)
            ml_stats = fcw.perf_stats(nav.nav, f"ML_{model_kind}_{fs}")
            k = min(len(month_rets), len(ew_month))
            excess_ann = float((month_rets[:k] - ew_month[:k]).mean() * 12) if k > 6 else None
            top10_monthly = {
                "mean": round(float(np.mean(month_rets)), 4),
                "median": round(float(np.median(month_rets)), 4),
                "pos_rate": round(float((month_rets > 0).mean()), 4),
                "n_month": int(len(month_rets)),
                "excess_ann": round(excess_ann, 4) if excess_ann is not None else None,
            }

            # 单因子同期 OOS（本 run 测试日）
            oos_dates = set(pd.DatetimeIndex(wf["oos_dates"]))
            single_ic = {}
            for name in features:
                ic_series = factor_ic_series(name, wides, fwd_w)
                arr = ic_series[ic_series.index.isin(oos_dates)].dropna().values
                if len(arr):
                    single_ic[name] = {"ic": round(float(arr.mean()), 4),
                                       "icir": round(float(arr.mean() / arr.std()), 3),
                                       "pos_rate": round(float((arr > 0).mean()), 4),
                                       "n": int(len(arr))}

            yr = fcw.yearly_returns(nav.nav)
            res = {
                "tag": tag, "featureset": fs, "model": model_kind,
                "embargo": bool(a.embargo), "purge": bool(a.purge), "embargo_days": a.embargo_days,
                "start": a.start, "end": a.end, "n_features": len(features), "features": features,
                "wf": wf["summary"], "wf_runtime_s": round(wf_runtime, 1),
                "portfolio": {kk: ml_stats[kk] for kk in ("cagr", "ann_vol", "sharpe", "mdd", "total")},
                "top10_monthly": top10_monthly,
                "benchmarks": {"等权基准≤30元": {kk: vv for kk, vv in ew_stats.items() if kk != "label"}},
                "single_factor_oos": single_ic,
                "feature_importance": wf["importance"],
                "lgbm_trees_used_mean": wf["trees_used_mean"],
                "fold_meta": wf["fold_meta"],
                "yearly_returns": {str(k): round(float(v), 4) for k, v in yr.items()},
                "total_runtime_s": round(time.time() - t1, 1),
                "cleaning": {"polluted_days_dropped": panels["polluted"], "ret_clip": 0.21,
                             "pollution_threshold": POLLUTION_THRESHOLD,
                             "price_source": "dragon_quant_qfq.stock_daily_meta_qfq",
                             "filter_price_source": "dragon_quant_meta.stock_daily_meta(raw)"},
                "generated": datetime.now().isoformat(timespec="seconds"),
            }
            all_results[tag] = res
            out_json = RESULTS_DIR / f"{tag}.json"
            out_json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
            pd.to_pickle(nav.nav, RESULTS_DIR / f"{tag}_nav.pkl")

            s_ = wf["summary"]
            print(f"      OOS IC={s_['mean_ic']:+.4f} ICIR={s_['icir']:+.3f} 正率={s_['pos_rate']:.0%} "
                  f"天数={s_['n_days']} 折={s_['n_folds']}")
            print(f"      组合 CAGR={ml_stats['cagr']:+.1%} Sharpe={ml_stats['sharpe']:+.3f} "
                  f"MDD={ml_stats['mdd']:+.1%} 总={ml_stats['total']:+.1%} "
                  f"多头月均={top10_monthly['mean']:+.2%} 年化超额={top10_monthly['excess_ann']:+.1%}"
                  if top10_monthly["excess_ann"] is not None else "")
            print(f"      JSON: {out_json.name} | 本run耗时 {res['total_runtime_s']}s", flush=True)

    print(f"\n[5/5] 汇总（本次进程 {len(all_results)} 个 run）")
    print(f"      {'run':<46} {'OOS IC':>8} {'ICIR':>7} {'正率':>5} {'CAGR':>7} {'Sharpe':>7} {'MDD':>7}")
    print(f"      {'-' * 92}")
    for tag, r in all_results.items():
        w, p = r["wf"], r["portfolio"]
        if w["mean_ic"] is None:
            continue
        print(f"      {tag:<46} {w['mean_ic']:>+8.4f} {w['icir']:>+7.3f} "
              f"{w['pos_rate']:>4.0%} {p['cagr']:>+7.1%} {p['sharpe']:>+7.3f} {p['mdd']:>+7.1%}")
    print(f"\n  总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
