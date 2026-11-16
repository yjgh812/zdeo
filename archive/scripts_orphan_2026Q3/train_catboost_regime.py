# -*- coding: utf-8 -*-
"""
CatBoost Regime 分类器训练
数据源: dragon_quant.db.index_daily (上证指数 000001)
标签: 未来20日收益 >3%=BULL(2), <-3%=BEAR(0), 其他=SHOCK(1)
"""
import sqlite3
import numpy as np
import pandas as pd
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

INDEX_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
INDEX_CODE = '000001'  # 上证指数
MODEL_PATH = r'D:\workbuddyspace\dragon_v4\models\catboost_regime_v1.cbm'
FWD_DAYS = 20
BULL_THRESH = 0.03
BEAR_THRESH = -0.03


def build_features():
    """构建 regime 分类特征和标签"""
    conn = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(
            "SELECT date, close, volume FROM index_daily WHERE index_code = ? ORDER BY date",
            conn, params=[INDEX_CODE]
        )
    finally:
        conn.close()

    if len(df) < 120:
        logger.error("指数数据不足 120 行")
        return None

    df['close'] = df['close'].astype(float)
    df['volume'] = df['volume'].astype(float)

    # 特征
    df['ret_5d'] = df['close'].pct_change(5)
    df['ret_20d'] = df['close'].pct_change(20)
    df['ret_60d'] = df['close'].pct_change(60)
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma60'] = df['close'].rolling(60).mean()
    df['above_ma20'] = (df['close'] > df['ma20']).astype(float)
    df['above_ma60'] = (df['close'] > df['ma60']).astype(float)
    df['dev_ma20'] = df['close'] / df['ma20'] - 1
    df['dev_ma60'] = df['close'] / df['ma60'] - 1

    rets = df['close'].pct_change()
    df['vol_20d'] = rets.rolling(20).std() * np.sqrt(252)
    df['vol_60d'] = rets.rolling(60).std() * np.sqrt(252)

    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['vol_ma60'] = df['volume'].rolling(60).mean()
    df['vol_ratio'] = df['vol_ma20'] / df['vol_ma60'].replace(0, np.nan)

    # 标签：未来 FWD_DAYS 收益
    df['fwd_ret'] = df['close'].shift(-FWD_DAYS) / df['close'] - 1
    df['label'] = 1  # SHOCK 默认
    df.loc[df['fwd_ret'] > BULL_THRESH, 'label'] = 2  # BULL
    df.loc[df['fwd_ret'] < BEAR_THRESH, 'label'] = 0  # BEAR

    # 特征列
    feature_cols = ['ret_5d', 'ret_20d', 'ret_60d', 'above_ma20', 'above_ma60',
                    'dev_ma20', 'dev_ma60', 'vol_20d', 'vol_60d', 'vol_ratio']

    df = df.dropna(subset=feature_cols + ['fwd_ret'])
    logger.info(f"特征数据: {len(df)} 行, 日期 {df['date'].min()} ~ {df['date'].max()}")
    logger.info(f"标签分布: BEAR={sum(df['label']==0)}, SHOCK={sum(df['label']==1)}, BULL={sum(df['label']==2)}")

    return df, feature_cols


def train():
    """训练 CatBoost regime 分类器"""
    from catboost import CatBoostClassifier
    from sklearn.model_selection import TimeSeriesSplit

    result = build_features()
    if result is None:
        return False
    df, feature_cols = result

    X = df[feature_cols].values
    y = df['label'].values

    # 时序分割：最后 2 年作为验证集
    split_idx = int(len(df) * 0.85)
    X_train, X_val = X[:split_idx], X[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    logger.info(f"训练集: {len(X_train)} | 验证集: {len(X_val)}")

    model = CatBoostClassifier(
        iterations=300,
        learning_rate=0.05,
        depth=5,
        loss_function='MultiClass',
        eval_metric='Accuracy',
        random_seed=42,
        verbose=50,
    )

    model.fit(X_train, y_train, eval_set=(X_val, y_val), early_stopping_rounds=50)

    # 验证集表现
    val_pred = model.predict(X_val)
    val_acc = (val_pred.flatten() == y_val).mean()
    logger.info(f"验证集准确率: {val_acc:.1%}")

    # 分类报告
    from sklearn.metrics import classification_report
    labels_names = ['BEAR(0)', 'SHOCK(1)', 'BULL(2)']
    logger.info(f"\n{classification_report(y_val, val_pred.flatten(), target_names=labels_names)}")

    # 特征重要性
    importance = model.get_feature_importance()
    for name, imp in sorted(zip(feature_cols, importance), key=lambda x: -x[1]):
        logger.info(f"  特征 {name}: {imp:.1f}")

    # 保存模型
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    model.save_model(MODEL_PATH)
    logger.info(f"✅ 模型保存: {MODEL_PATH}")

    # 测试最新预测
    latest_features = X[-1:]
    latest_pred = model.predict(latest_features)[0][0]
    latest_proba = model.predict_proba(latest_features)[0]
    logger.info(f"\n最新 regime 预测: {labels_names[int(latest_pred)]}")
    logger.info(f"概率: BEAR={latest_proba[0]:.1%} SHOCK={latest_proba[1]:.1%} BULL={latest_proba[2]:.1%}")

    return True


if __name__ == "__main__":
    print("=" * 60)
    print("CatBoost Regime 分类器训练 (V5.1)")
    print(f"数据: 上证指数 {INDEX_CODE}, 标签窗口: {FWD_DAYS}日")
    print("=" * 60)
    success = train()
    print(f"\n训练结果: {'✅ 成功' if success else '❌ 失败'}")
