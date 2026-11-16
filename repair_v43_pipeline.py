#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
repair_v43_pipeline.py — 重建 stock_signals_v43 表
====================================================
背景：stock_signals_v43 于 2026-07-30 被 drop_legacy_tables_20260730.py
      误判为 legacy 并 purge（真删，无 _dropped 备份）。
      但 tulong_live_v5v7.py 依赖该表生成 V5/V7 实盘候选。

修复：用已训练的 v43_signal_model.lgb（LightGBM, 78特征, 列位置训练）
      对 feature_daily_v2 重新推理，重建 stock_signals_v43。
      保持 V5(>=0.60) / V7(>=0.70) 阈值语义不变（原回测校准值）。

模式：
  默认（无参）: 重建，推理 date>=20260701 全部特征行（覆盖 regime_window 7/1 起）
  --date YYYYMMDD : 仅刷新指定单日（增量补推）
  --from YYYYMMDD : 自定义重建起始日

数据：D:\\workbuddyspace\\factor_engine\\data\\dragon_quant.db
模型：D:\\workbuddyspace\\models\\v43_signal_model.lgb
用法: python repair_v43_pipeline.py [--date 20260811 | --from 20260701]
"""
import sqlite3
import sys
import time
import argparse
import numpy as np
import pandas as pd
import lightgbm as lgb
from pathlib import Path

DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db")
MODEL = Path(r"D:\workbuddyspace\models\v43_signal_model.lgb")
TABLE = "stock_signals_v43"
EXCLUDE = {"code", "date", "future_return_5d", "future_win_5d"}
DEFAULT_FROM = 20260701  # 覆盖 regime_window 需要的起始日


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get_feature_cols(conn):
    """按 PRAGMA 列序取特征列（与 train_v43_signal_model.py 训练时位置对齐）。"""
    all_cols = [r[1] for r in conn.execute("PRAGMA table_info(feature_daily_v2)").fetchall()]
    feat = [c for c in all_cols if c not in EXCLUDE]
    return feat, all_cols


def main():
    ap = argparse.ArgumentParser(description="重建/刷新 stock_signals_v43")
    ap.add_argument("--date", type=int, default=None, help="仅刷新指定单日 YYYYMMDD")
    ap.add_argument("--from", dest="from_date", type=int, default=DEFAULT_FROM,
                    help=f"重建起始日，默认 {DEFAULT_FROM}")
    args = ap.parse_args()

    if not MODEL.exists():
        log(f"[致命] 模型文件不存在: {MODEL}")
        sys.exit(1)
    if not DB.exists():
        log(f"[致命] 数据库不存在: {DB}")
        sys.exit(1)

    log("[1/6] 加载 v43 LightGBM 模型")
    booster = lgb.Booster(model_file=str(MODEL))
    n_model_feat = len(booster.feature_name())
    log(f"      模型特征数: {n_model_feat} (列位置训练 Column_0..{n_model_feat-1})")

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    log("[2/6] 读取 feature_daily_v2 列序")
    feat_cols, all_cols = get_feature_cols(conn)
    log(f"      特征列数: {len(feat_cols)} (全列 {len(all_cols)})")
    if len(feat_cols) != n_model_feat:
        log(f"[致命] 特征列数 {len(feat_cols)} != 模型期望 {n_model_feat}，中止")
        conn.close()
        sys.exit(2)

    # 确定目标日期范围
    if args.date:
        dates_cond = f"date = {args.date}"
        mode = f"单日刷新 {args.date}"
    else:
        dates_cond = f"date >= {args.from_date}"
        mode = f"重建 date>={args.from_date}"

    log(f"[3/6] 读取特征数据（{mode}）")
    q = f"SELECT code, date, {', '.join(feat_cols)} FROM feature_daily_v2 WHERE {dates_cond}"
    df = pd.read_sql(q, conn)
    df["code"] = df["code"].astype(str).str.strip()
    df["date"] = df["date"].astype(int)
    n = len(df)
    if n == 0:
        log(f"[警告] {dates_cond} 无特征行，退出")
        conn.close()
        return
    n_days = df["date"].nunique()
    log(f"      命中 {n} 行，覆盖 {n_days} 个交易日")

    log("[4/6] LightGBM 推理")
    t0 = time.time()
    X = df[feat_cols].values.astype(np.float32)  # 严格按 PRAGMA 顺序，位置对齐
    probs = booster.predict(X)
    # 加固：概率范围校验（LightGBM 二分类输出应 ∈ [0,1]，容差 0.01 防浮点）
    pmin, pmax = float(probs.min()), float(probs.max())
    if pmin < -0.01 or pmax > 1.01:
        log(f"[警告] 概率越界 min={pmin:.4f} max={pmax:.4f}，已 clip 到 [0,1]")
    probs = np.clip(probs, 0.0, 1.0)
    df["v43_prob"] = np.round(probs, 6)
    log(f"      推理完成 {n} 行 ({time.time()-t0:.1f}s) | prob∈[{pmin:.4f},{pmax:.4f}]")

    log("[5/6] 写入 stock_signals_v43")
    out = df[["code", "date", "v43_prob"]].copy()
    if args.date:
        # 增量：先删该日再写
        cur.execute(f"DELETE FROM {TABLE} WHERE date = {args.date}")
        out.to_sql(TABLE, conn, if_exists="append", index=False)
    else:
        # 重建：replace 全表
        out.to_sql(TABLE, conn, if_exists="replace", index=False)
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_code_date ON {TABLE}(code, date)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_date ON {TABLE}(date)")
    conn.commit()

    log("[6/6] 验证摘要")
    mx_date, total = cur.execute(
        f"SELECT MAX(date), COUNT(*) FROM {TABLE}"
    ).fetchone()
    log(f"      {TABLE}: 共 {total} 行，最新 date={mx_date}")

    # 最新日的分布
    latest = mx_date
    rows = cur.execute(
        f"SELECT v43_prob FROM {TABLE} WHERE date={latest} AND v43_prob IS NOT NULL"
    ).fetchall()
    if rows:
        ps = np.array([r[0] for r in rows])
        ge60 = int((ps >= 0.60).sum())
        ge70 = int((ps >= 0.70).sum())
        log(f"      最新日 {latest}: n={len(ps)}, min={ps.min():.4f}, "
            f"max={ps.max():.4f}, mean={ps.mean():.4f}, >=0.60={ge60}, >=0.70={ge70}")

    # 近5日窗口
    log("      近5日信号窗口:")
    win = cur.execute(f"""
        SELECT date, COUNT(*) n,
               SUM(CASE WHEN v43_prob>=0.60 THEN 1 ELSE 0 END) ge60,
               SUM(CASE WHEN v43_prob>=0.70 THEN 1 ELSE 0 END) ge70
        FROM {TABLE} WHERE date >= (SELECT MAX(date)-10 FROM {TABLE})
        GROUP BY date ORDER BY date DESC LIMIT 5
    """).fetchall()
    for w in win:
        log(f"        {w[0]}: n={w[1]:5d}  >=0.60={w[2]:4d}  >=0.70={w[3]:4d}")

    conn.close()
    log("完成。stock_signals_v43 已就绪，tulong_live_v5v7.py 可运行。")


if __name__ == "__main__":
    main()
