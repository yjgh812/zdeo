# -*- coding: utf-8 -*-
"""
bp+ep 回调监控器（2026-08-12 知微）
=====================================
监控bp+ep Top20中超买标的的RSI6，
当RSI降到60以下时发出"可买入"信号。

用法: python bp_ep_pullback_monitor.py
定时: 每日15:30收盘后运行
"""
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'

# bp+ep Top20中超买的13只（20260810评估）
WATCH_LIST = [
    ('002463', '沪电股份', '元器件', 122.70, 76.8),
    ('002028', '思源电气', '电气设备', 165.42, 82.1),
    ('600961', '株冶集团', '铅锌', 30.17, 80.5),
    ('001339', '智微智能', 'IT设备', 82.40, 84.1),
    ('603156', '养元饮品', '软饮料', 50.88, 86.4),
    ('002281', '光迅科技', '通信设备', 178.30, 64.5),
    ('002353', '杰瑞股份', '化工机械', 145.85, 47.2),
    ('000688', '国城矿业', '铅锌', 32.16, 86.5),
    ('002975', '博杰股份', '专用机械', 100.99, 88.5),
    ('600988', '赤峰黄金', '黄金', 43.73, 66.0),
    ('600549', '厦门钨业', '小金属', 59.95, 74.7),
    ('603650', '彤程新材', '化工原料', 65.58, 92.5),
    ('601899', '紫金矿业', '铜', 35.45, 62.2),
]

BUY_THRESHOLD = 60.0  # RSI降到60以下=可买入信号


def calc_rsi6(code: str) -> float:
    import talib
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=30)
    df = pd.read_sql(
        'SELECT date, close FROM stock_daily_hsj WHERE code = ? AND date >= "20260601" ORDER BY date',
        conn, params=[code])
    conn.close()
    if len(df) < 10:
        return None
    arr = df['close'].values.astype(float)
    rsi = talib.RSI(arr, timeperiod=6)
    return float(rsi[-1])


def main():
    print('=' * 65)
    print('bp+ep 回调监控器')
    print(f'监控{len(WATCH_LIST)}只超买标的，买入阈值RSI<{BUY_THRESHOLD:.0f}')
    print('=' * 65)

    buy_signals = []
    cooling = []
    still_hot = []

    for code, name, industry, price, prev_rsi in WATCH_LIST:
        rsi = calc_rsi6(code)
        if rsi is None:
            print(f'  {code} {name}: 数据不足')
            continue

        delta_rsi = rsi - prev_rsi
        if rsi <= BUY_THRESHOLD:
            tag = '✅可买入'
            buy_signals.append((code, name, rsi, prev_rsi, price))
        elif rsi <= 65:
            tag = '🟡接近'
            cooling.append((code, name, rsi, prev_rsi, price))
        else:
            tag = '⏳仍超买'
            still_hot.append((code, name, rsi, prev_rsi, price))

        print(f'  {code} {name:8} RSI={rsi:>5.1f} (上次{prev_rsi:.0f} Δ{delta_rsi:+.1f}) {tag}')

    print(f'\n=== 汇总 ===')
    print(f'✅ 可买入（RSI≤{BUY_THRESHOLD:.0f}）: {len(buy_signals)}只')
    for c, n, r, pr, p in buy_signals:
        print(f'   {c} {n} RSI={r:.1f} 价格={p:.2f}元')
    print(f'🟡 接近（RSI 60-65）: {len(cooling)}只')
    for c, n, r, pr, p in cooling:
        print(f'   {c} {n} RSI={r:.1f} 价格={p:.2f}元')
    print(f'⏳ 仍超买（RSI>65）: {len(still_hot)}只')

    if buy_signals:
        print(f'\n⚡ 行动建议: {len(buy_signals)}只标的已回调到可买入区间，可分批建仓bp+ep核心底仓')


if __name__ == '__main__':
    main()
