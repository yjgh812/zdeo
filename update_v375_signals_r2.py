#!/usr/bin/env python3
"""
使用 V3 QFQ 前复权模型生成 V375 信号，更新 stock_signals_v375 表。
与 v3 版逻辑一致，但改为从 dragon_quant_qfq.db 实时读取 K 线并计算特征，
确保信号表始终覆盖到最新交易日（而不是被训练集标签窗口截断）。
"""
import os
import sys
import json
import sys, pickle
sys.path.insert(0, r"D:/workbuddyspace/workspaces/W3_Backtest_V373")
import sqlite3
import warnings
import time
import gc
from pathlib import Path
from multiprocessing import Pool, cpu_count

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# 自动发现最新的 qfq combined 模型
MODEL_DIR = Path(r'D:\workbuddyspace\factor_engine\models')
qfq_models = sorted(MODEL_DIR.glob('v375_combined_qfq_retrained_*.pkl'), reverse=True)
if not qfq_models:
    print("错误: 未找到 v375_combined_v3_qfq_*.pkl 模型文件")
    sys.exit(1)
MODEL_PATH = qfq_models[0]
import glob as _g
FEATURES_PATH = Path(_g.glob(str(MODEL_DIR / 'v375_retrained_*_features.json'))[-1]) if _g.glob(str(MODEL_DIR / 'v375_retrained_*_features.json')) else MODEL_DIR / 'v375_combined_v3_qfq_features.json'
QFQ_DB_PATH = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
PROD_DB_PATH = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
PARQUET_PATH = r'D:\workbuddyspace\workspaces\W3_Backtest_V373\v375_dataset\highwin_v375_hd5_v3_qfq.parquet'

START_DATE = 20150101
# 多进程 worker 数（与训练集构建保持一致）
WORKERS = max(1, min(cpu_count() - 2, 14))
BATCH_SIZE = 100000


def is_valid_code(code):
    code = str(code).strip()
    return len(code) == 6 and code.isdigit() and code.startswith(('00', '30', '31', '60'))


def _worker_compute(args):
    """多进程 worker：接收 (code, df) 返回特征 DataFrame"""
    from v375_feature_engine_qfq import compute_features_for_stock
    code, g = args
    try:
        feat = compute_features_for_stock(g)
        return (code, feat, None)
    except Exception as e:
        return (code, None, str(e))


def compute_features_from_db(df_prices):
    """从 qfq 数据库的价格 DataFrame 多进程计算特征"""
    if df_prices.empty:
        return pd.DataFrame()

    groups = list(df_prices.groupby('code', sort=False))
    total_groups = len(groups)
    all_frames = []
    skipped = 0
    print(f"  总股票数: {total_groups} | worker 数: {WORKERS}")

    with Pool(processes=WORKERS) as pool:
        for i, result in enumerate(pool.imap_unordered(_worker_compute, groups, chunksize=1)):
            code, feat, err = result
            if err:
                print(f"  [ERROR] 股票 {code} 处理失败: {err}")
                skipped += 1
                continue
            if feat is None:
                skipped += 1
                continue
            all_frames.append(feat)
            if (i + 1) % 100 == 0:
                print(f"  进度: {i + 1} / {total_groups} 只股票")

    if not all_frames:
        return pd.DataFrame()
    df = pd.concat(all_frames, ignore_index=True)
    del all_frames, groups
    gc.collect()
    print(f"  完成特征计算: {len(df):,} 条 (跳过 {skipped} 只数据不足)")
    return df


def predict_and_write(df, model, feature_cols, db_path, del_since=None):
    """对包含特征的 DataFrame 预测概率并写入生产信号表"""
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        print(f"  错误: 缺少特征列: {missing[:10]}...")
        sys.exit(1)

    # 过滤有效标的池
    df['valid'] = df['code'].apply(is_valid_code)
    df = df[df['valid']].copy()
    print(f"  过滤标的池后: {len(df):,} 条")

    print("\n[3/5] 批量预测概率...")
    t0 = time.time()
    n = len(df)
    probs = []
    for i in range(0, n, BATCH_SIZE):
        end = min(i + BATCH_SIZE, n)
        batch = df.iloc[i:end]
        X = batch[feature_cols].values
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        prob = model.predict(X, num_iteration=model.best_iteration)
        probs.extend(prob.tolist())
        if (i // BATCH_SIZE) % 5 == 0 or end == n:
            print(f"  进度: {end:,}/{n:,} ({end/n*100:.1f}%)")

    df['v375_prob'] = probs
    print(f"  预测完成: {len(probs):,} 条, 耗时 {time.time()-t0:.1f}s")
    print(f"  概率范围: {df['v375_prob'].min():.4f} ~ {df['v375_prob'].max():.4f}")
    print(f"  概率均值: {df['v375_prob'].mean():.4f}")

    print("\n[4/5] 写入数据库...")
    t0 = time.time()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock_signals_v375 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            date INTEGER NOT NULL,
            v375_prob REAL NOT NULL,
            UNIQUE(code, date)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_v375_code_date ON stock_signals_v375(code, date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_v375_date ON stock_signals_v375(date)')

    if del_since:
        cursor.execute('DELETE FROM stock_signals_v375 WHERE date > ?', (del_since,))

    records = df[['code', 'date', 'v375_prob']].values.tolist()
    cursor.executemany(
        'INSERT OR REPLACE INTO stock_signals_v375 (code, date, v375_prob) VALUES (?, ?, ?)',
        records
    )

    conn.commit()
    conn.close()
    print(f"  写入: {len(records):,} 条, 耗时 {time.time()-t0:.1f}s")

    print("\n[5/5] 信号统计...")
    print(f"  总信号: {len(records):,}")
    print(f"  交易日: {df['date'].nunique()}")
    print(f"  股票数: {df['code'].nunique()}")
    print(f"  概率>0.5: {(df['v375_prob'] > 0.5).sum():,} ({(df['v375_prob'] > 0.5).mean()*100:.1f}%)")
    print(f"  概率>0.6: {(df['v375_prob'] > 0.6).sum():,} ({(df['v375_prob'] > 0.6).mean()*100:.1f}%)")
    print(f"  概率>0.7: {(df['v375_prob'] > 0.7).sum():,} ({(df['v375_prob'] > 0.7).mean()*100:.1f}%)")
    print(f"  日期范围: {df['date'].min()} ~ {df['date'].max()}")


def main():
    print("=" * 70)
    print("V375 Combined v3 QFQ 模型信号生成 —— 实时特征版")
    print(f"模型: {MODEL_PATH}")
    print(f"数据源: {QFQ_DB_PATH}")
    print(f"写入: {PROD_DB_PATH}")
    print("=" * 70)

    print("\n[1/5] 加载模型...")
    with open(MODEL_PATH, 'rb') as f:
        model = pickle.load(f)
    print(f"  模型: {type(model).__name__}")

    if not os.path.exists(FEATURES_PATH):
        print(f"  错误: 特征文件不存在 {FEATURES_PATH}")
        sys.exit(1)
    with open(FEATURES_PATH) as f:
        feature_cols = json.load(f)
    print(f"  特征数: {len(feature_cols)}")

    print("\n[2/5] 加载前复权K线并计算特征...")
    t0 = time.time()

    # 增量模式(20260818): 历史特征已全量落生产表, 只读 parquet date 列定增量起点,
    # 避免每天全量加载 7.7GB parquet + 全量K线(峰值30GB+, 8/18实测 commit 不足两次失败)
    historical_df = None
    parquet_max_date = None
    if os.path.exists(PARQUET_PATH):
        import pyarrow.parquet as pq
        print(f"  发现历史数据集: {PARQUET_PATH}")
        parquet_max_date = int(pq.read_table(PARQUET_PATH, columns=['date'])
                               .column('date').to_pandas().max())
        print(f"  历史数据集仅读 date 列, max={parquet_max_date} (增量起点)")

    # 只读增量起点前推150个自然日的K线(滚动特征窗口), 算完特征后仅保留增量行
    from datetime import datetime as _dt, timedelta as _td
    _win_start = START_DATE
    if parquet_max_date:
        _win_start = int((_dt.strptime(str(parquet_max_date), "%Y%m%d") - _td(days=150))
                         .strftime("%Y%m%d"))
    conn = sqlite3.connect(QFQ_DB_PATH)
    print(f"  从数据库读取 K 线 (>= {_win_start}, 滚动窗口150天)...")
    all_prices = pd.read_sql(f"""
        SELECT code, date, open, high, low, close, volume
        FROM stock_daily_meta_qfq
        WHERE date >= {_win_start}
        ORDER BY code, date
    """, conn)
    conn.close()

    if not all_prices.empty:
        all_prices['date'] = all_prices['date'].astype(int)
        print(f"  全量 K 线: {len(all_prices):,} 条, 股票数 {all_prices['code'].nunique():,}, 日期 {all_prices['date'].min()} ~ {all_prices['date'].max()}")
        all_features_df = compute_features_from_db(all_prices)
        # 只保留 parquet 中不存在的最新日期
        if parquet_max_date:
            incremental_df = all_features_df[all_features_df['date'] > parquet_max_date].copy()
            print(f"  增量特征: {len(incremental_df):,} 条, 日期 {incremental_df['date'].min()} ~ {incremental_df['date'].max()}")
        else:
            incremental_df = all_features_df.copy()
    else:
        print("  错误: 数据库无 K 线数据")
        incremental_df = pd.DataFrame()

    # 增量模式: 历史特征已在生产表内, 本次仅处理 date > parquet_max_date 的增量行
    if incremental_df.empty:
        print("  无增量数据, 无需更新")
        sys.exit(0)
    df = incremental_df.copy()

    print(f"\n  本次增量特征: {len(df):,} 条, 日期范围 {df['date'].min()} ~ {df['date'].max()}")
    print(f"  耗时: {time.time()-t0:.1f}s")

    predict_and_write(df, model, feature_cols, PROD_DB_PATH, del_since=parquet_max_date)

    print("\n" + "=" * 70)
    print("完成！")
    print("=" * 70)


if __name__ == '__main__':
    main()
