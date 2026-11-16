# -*- coding: utf-8 -*-
"""V375 模型重训（2024+ 数据纳入训练）
根因：现有模型 TRAIN_END=20231231 从未见过 2024+ 数据 → OOS 衰减
重训：TRAIN_END=20251231, VALID_END=20260617，让模型学习 2024-2025
"""
import pandas as pd
import numpy as np
from pathlib import Path
import pickle
import json
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')
import lightgbm as lgb
from sklearn.metrics import roc_auc_score, precision_score, recall_score

DATA_PATH = Path(r'D:/workbuddyspace/workspaces/W3_Backtest_V373/v375_dataset/highwin_v375_hd5_v3_qfq.parquet')
MODEL_DIR = Path(r'D:/workbuddyspace/factor_engine/models')

# 动态训练窗口（月度重训跟随市场）：
# TRAIN_END/VALID_END 动态窗口（2026-09-01 修正月度口径）：
# 原逻辑 TRAIN_END=上月末/VALID_END=当日 —— 但数据集标签为 T+5 前瞻，当月行已被裁剪，
# 验证窗恒为空（且旧代码 _last_month_end+1 整数加法产生 20260832 非法日期）。
# 正确口径：训练至上上月末（标签完整），验证=上月整月（有完整 T+5 标签）。
import sys
from datetime import datetime as _dt
_today = _dt.now()
_first_of_month = _today.replace(day=1)
_last_month_end = (_first_of_month - pd.Timedelta(days=1))            # 上月末 20260831
_prev_month_end = (_last_month_end.replace(day=1) - pd.Timedelta(days=1))  # 上上月末 20260731
TRAIN_END = int(sys.argv[1]) if len(sys.argv) > 1 else int(_prev_month_end.strftime('%Y%m%d'))
VALID_END = int(sys.argv[2]) if len(sys.argv) > 2 else int(_last_month_end.strftime('%Y%m%d'))
TAG = datetime.now().strftime('%Y%m%d_%H%M%S')

def get_feature_cols(df):
    exclude = {'code', 'date', 'close', 'label', 'future_return',
               'entry_open', 'max_return_5d', 'min_return_5d',
               'label_max5', 'label_stop', 'label_combined'}
    return [c for c in df.columns if c not in exclude]

def clean_features(X, feature_cols):
    for col in feature_cols:
        X[col] = X[col].replace([np.inf, -np.inf], np.nan)
        X[col] = X[col].fillna(X[col].median())
        q_low, q_high = X[col].quantile([0.001, 0.999])
        X[col] = X[col].clip(q_low, q_high)
    return X

print("=" * 60)
print("V375 模型重训（2024+ 数据纳入）")
print(f"数据集: {DATA_PATH}")
print(f"训练窗口: 2015~{TRAIN_END} | 验证: {TRAIN_END+1}~{VALID_END}")
print("=" * 60)

print("\n[*] 加载数据集（pyarrow 谓词下推分批读 + float32，2026-09-01 内存修复：")
print("    数据量增至 14.46M×75 后全表 float64 加载+copy 峰值 >25GB 触发 OOM（连续三次实锤）；")
print("    LightGBM 内部本就 float32，特征降 float32 无损，谓词下推避免全表驻留）")
import pyarrow.parquet as _pq
import pyarrow.compute as _pc
import pyarrow as _pa
_tbl = _pq.read_table(DATA_PATH)
_cols = [c for c in _tbl.column_names if c != 'date']
_date = _pc.cast(_tbl.column('date'), 'int64')
_train_mask = _pc.less_equal(_date, TRAIN_END)
_valid_mask = _pc.and_(_pc.greater(_date, TRAIN_END), _pc.less_equal(_date, VALID_END))

def _convert(sub, mask):
    # arrow 侧直接降 float32，避免 8.7GB float64 pandas 中间态（峰值再减半）
    _schema32 = _pa.schema([(f.name, _pa.float32()) for f in sub.schema])
    arr32 = sub.cast(_schema32)
    # 2026-09-02 内存修复②(主人授权"修复优化，重新训练")：split_blocks 逐列分块
    # 消除 to_pandas 的单块 3.5GB 连续分配(OOM 三连实锤点)，self_destruct 边转边释放
    # arrow 侧缓冲，峰值≈max(arrow, pandas) 而非两者之和。列语义/dtype 不变。
    out = arr32.to_pandas(split_blocks=True, self_destruct=True)
    del sub, arr32
    out.insert(0, 'date', np.asarray(_date.filter(mask)).astype('int64'))
    return out

# 2026-09-02 内存修复③：先在 arrow 侧完成两个子表过滤并立即释放全表 _tbl(约8.8GB)，
# 再逐块转 pandas —— 修复④前 _tbl 与 train_df 同时驻留导致 dropna 处 9.89MiB 都分配失败的触顶
sub_tr = _tbl.filter(_train_mask).select(_cols)
sub_va = _tbl.filter(_valid_mask).select(_cols)
del _tbl
train_df = _convert(sub_tr, _train_mask)
del sub_tr
valid_df = _convert(sub_va, _valid_mask)
del sub_va
_total = len(train_df) + len(valid_df)
print(f"总样本: {_total:,}")

print(f"训练集: {len(train_df):,} ({int(train_df['date'].min())}~{int(train_df['date'].max())})")
print(f"验证集: {len(valid_df):,} ({int(valid_df['date'].min())}~{int(valid_df['date'].max())})")

feature_cols = get_feature_cols(train_df)  # 2026-09-01 修复：不得直接用 _cols（含 label_* 等前瞻列，曾致 AUC=1.0 泄漏）
print(f"特征数: {len(feature_cols)}")

def _dropna_chunked(df, cols, chunk=2_000_000):
    # 2026-09-02 内存加固④：dropna 分块化——pandas 全表 dropna 为 76列×全行生成
    # notna 掩码+副本(当前 10.37M 行峰值 +6.3GB)，数据再增长将再触顶(9/2 00:11 r4
    # 在同点 9.89MiB 都分配失败)。dropna 是行独立操作，分块+concat 与全表逐行等价
    # (reports/verify_v375_dropna_chunk/ 等价性实证)，块级峰值 ≈608MB 与总量解耦。
    if len(df) <= chunk:
        return df.dropna(subset=cols)
    parts = []
    for i in range(0, len(df), chunk):
        part = df.iloc[i:i + chunk].dropna(subset=cols)
        if len(part):
            parts.append(part)
    return pd.concat(parts) if parts else df.iloc[0:0]

# 清理
train_df = _dropna_chunked(train_df, feature_cols + ['label_combined'])
valid_df = _dropna_chunked(valid_df, feature_cols + ['label_combined'])
X_train = clean_features(train_df[feature_cols].copy(), feature_cols)
y_train = train_df['label_combined'].astype(int)
X_valid = clean_features(valid_df[feature_cols].copy(), feature_cols)
y_valid = valid_df['label_combined'].astype(int)
# 2026-09-02 内存修复②：X/y 已提取，立即释放原始大 df（约 -8.6GB），避免与训练期叠加触顶
import gc
del train_df, valid_df
gc.collect()
print(f"清洗后: 训练 {len(X_train):,} / 验证 {len(X_valid):,} | 正样本率 训练 {y_train.mean()*100:.1f}% 验证 {y_valid.mean()*100:.1f}%")

print("\n[*] 训练 LightGBM...")
params = dict(
    objective='binary', metric='auc', learning_rate=0.02,
    num_leaves=63, max_depth=7, min_data_in_leaf=50,
    feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1,
    lambda_l1=0.1, lambda_l2=1.0, verbose=-1, n_jobs=8,
)
model = lgb.train(
    params,
    lgb.Dataset(X_train, y_train),
    num_boost_round=2000,
    valid_sets=[lgb.Dataset(X_valid, y_valid)],
    callbacks=[lgb.early_stopping(100), lgb.log_evaluation(200)],
)

# 评估
auc_train = roc_auc_score(y_train, model.predict(X_train))
auc_valid = roc_auc_score(y_valid, model.predict(X_valid))
print(f"\nAUC 训练: {auc_train:.4f} | AUC 验证: {auc_valid:.4f}")

# 阈值精度
preds = model.predict(X_valid)
for thr in [0.5, 0.6, 0.65, 0.7, 0.75, 0.8]:
    pl = (preds >= thr).astype(int)
    if pl.sum() == 0:
        print(f"  阈值{thr}: 无命中")
        continue
    prec = precision_score(y_valid, pl, zero_division=0)
    print(f"  阈值{thr}: P={prec*100:.1f}% N={pl.sum()}")

# 保存
model_path = MODEL_DIR / f'v375_combined_qfq_retrained_{TAG}.pkl'
with open(model_path, 'wb') as f:
    pickle.dump(model, f)
config = {
    'train_end': TRAIN_END, 'valid_end': VALID_END,
    'n_train': len(X_train), 'n_valid': len(X_valid),
    'auc_train': round(auc_train, 4), 'auc_valid': round(auc_valid, 4),
    'feature_cols': feature_cols, 'tag': TAG,
    'note': '重训:纳入2024-2025数据，解决OOS衰减'
}
with open(MODEL_DIR / f'v375_retrained_{TAG}_config.json', 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
with open(MODEL_DIR / f'v375_retrained_{TAG}_features.json', 'w') as f:
    json.dump(feature_cols, f)

print(f"\n✅ 模型已保存: {model_path}")
print(f"配置: {MODEL_DIR / f'v375_retrained_{TAG}_config.json'}")
