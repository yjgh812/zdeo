#!/usr/bin/env python3
"""缠论技术面分析: 300201 海伦哲 + 300854 中兰环保"""
import sqlite3
import pandas as pd
import numpy as np

conn = sqlite3.connect('D:/workbuddyspace/factor_engine/data/dragon_quant.db', timeout=60)

def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def get_stock_factors(code):
    """Get technical factors from stock_factors table"""
    cols = [r[1] for r in conn.execute('PRAGMA table_info(stock_factors)').fetchall()]
    row = conn.execute('SELECT * FROM stock_factors WHERE code=? AND date=20260814', (code,)).fetchone()
    if row:
        return dict(zip(cols, row))
    return None

def get_daily_history(code, days=120):
    """Get daily K-line history"""
    return pd.read_sql(
        f"SELECT date, open, high, low, close, vol, amt FROM stock_daily_hsj WHERE code=? ORDER BY date DESC LIMIT {days}",
        conn, params=(code,))

def get_smart_money(code):
    row = conn.execute("SELECT smart_money, rank FROM mv_daily_smart_money WHERE code=? AND date=20260814", (code,)).fetchone()
    return row if row else None

def get_v375_prob(code):
    row = conn.execute("SELECT v375_prob FROM stock_signals_v375 WHERE code=? AND date=20260814", (code,)).fetchone()
    return row[0] if row else None

def chan_analysis(code, name):
    print_header(f"{code} {name} 缠论技术面分析")
    
    # 1. Basic info
    factors = get_stock_factors(code)
    daily = get_daily_history(code, 250)
    daily_long = daily.sort_values('date').reset_index(drop=True)
    
    if factors:
        print(f"\n📊 技术指标 (20260814):")
        print(f"  MA5: {factors['ma5']:.2f}")
        print(f"  MA10: {factors['ma10']:.2f}")
        print(f"  MA20: {factors['ma20']:.2f}")
        print(f"  MA60: {factors['ma60']:.2f}")
        
        # MA alignment
        ma_alignment = "多头排列" if factors['ma5'] > factors['ma10'] > factors['ma20'] else \
                       "空头排列" if factors['ma5'] < factors['ma10'] < factors['ma20'] else \
                       "均线粘合/交叉"
        print(f"  均线状态: {ma_alignment}")
        
        # MACD
        print(f"  MACD: DIF={factors['macd']:.4f}, DEA={factors['macd_signal']:.4f}, 柱={factors['macd_hist']:.4f}")
        macd_signal = "金叉(买入)" if factors['macd'] > factors['macd_signal'] else "死叉(卖出)"
        print(f"  MACD信号: {macd_signal}")
        
        # KDJ
        print(f"  KDJ: K={factors['k']:.2f}, D={factors['d']:.2f}, J={factors['j']:.2f}")
        j_signal = ""
        if factors['j'] > 100: j_signal = "⚠️ J值>100 超买区"
        elif factors['j'] < 0: j_signal = "⚠️ J值<0 超卖区"
        elif factors['k'] > 80: j_signal = "K>80 高位"
        elif factors['k'] < 20: j_signal = "K<20 低位"
        if j_signal: print(f"  KDJ信号: {j_signal}")
        
        # Bollinger
        print(f"  布林带: 上轨={factors['boll_upper']:.2f}, 中轨={factors['boll_mid']:.2f}, 下轨={factors['boll_lower']:.2f}")
        last_close = daily_long['close'].iloc[-1]
        if last_close >= factors['boll_upper']: boll_pos = "触及上轨(超买)"
        elif last_close <= factors['boll_lower']: boll_pos = "触及下轨(超卖)"
        elif last_close >= factors['boll_mid']: boll_pos = "中轨上方(偏多)"
        else: boll_pos = "中轨下方(偏空)"
        print(f"  布林位置: {boll_pos}")
        
        # RSI
        print(f"  RSI6: {factors['rsi6']:.2f}, RSI12: {factors['rsi12']:.2f}")
        rsi_signal = ""
        if factors['rsi6'] > 70: rsi_signal = "⚠️ RSI6>70 超买区"
        elif factors['rsi6'] < 30: rsi_signal = "⚠️ RSI6<30 超卖区"
        print(f"  RSI信号: {rsi_signal or '中性区'}")
        
        # Volume
        print(f"  VOL_MA5: {factors['vol_ma5']:.0f}")
        print(f"  量比: {factors['vol_ratio']:.2f}")
        vol_sig = "放量" if factors['vol_ratio'] > 1.5 else "缩量" if factors['vol_ratio'] < 0.7 else "平量"
        print(f"  成交量状态: {vol_sig}")
        
        # ATR
        print(f"  ATR(14): {factors['atr']:.4f}")
        
        # Returns
        print(f"  一日收益: {factors['ret1']*100:.2f}%")
        print(f"  五日收益: {factors['ret5']*100:.2f}%")
        print(f"  二十日收益: {factors['ret20']*100:.2f}%")
        
        # Momentum
        print(f"  动量: {factors['momentum']:.4f}")
        
        # Overall
        print(f"\n📈 综合判定:")
        bullish_signals = 0
        bearish_signals = 0
        
        # MA alignment
        if factors['ma5'] > factors['ma20']: bullish_signals += 1
        else: bearish_signals += 1
        
        # Price vs MA20
        if daily_long['close'].iloc[-1] > factors['ma20']: bullish_signals += 1
        else: bearish_signals += 1
        
        # MACD
        if factors['macd'] > factors['macd_signal']: bullish_signals += 1
        else: bearish_signals += 1
        
        # KDJ
        if factors['k'] > factors['d']: bullish_signals += 1
        else: bearish_signals += 1
        
        # Bollinger
        if daily_long['close'].iloc[-1] >= factors['boll_mid']: bullish_signals += 1
        else: bearish_signals += 1
        
        # RSI
        if factors['rsi6'] > 50: bullish_signals += 1
        else: bearish_signals += 1
        
        # Volume
        if factors['vol_ratio'] > 1.0: bullish_signals += 1
        else: bearish_signals += 1
        
        # Momentum
        if factors['momentum'] > 0: bullish_signals += 1
        else: bearish_signals += 1
        
        # Smart money
        sm = get_smart_money(code)
        if sm: print(f"  聪明钱排名: #{sm[1]}")
        
        # V375 prob
        prob = get_v375_prob(code)
        if prob: print(f"  V375模型概率: {prob:.4f}")
        
        total = bullish_signals + bearish_signals
        print(f"  多头信号: {bullish_signals}/{total}")
        print(f"  空头信号: {bearish_signals}/{total}")
        verdict = "偏多✅" if bullish_signals > bearish_signals else "偏空🔴" if bearish_signals > bullish_signals else "中性⚖️"
        print(f"  综合倾向: {verdict}")
    
    # 2. Chan Theory Structure (basic)
    print(f"\n📉 缠论结构分析 (基于日线):")
    
    prices = daily_long['close'].values
    highs = daily_long['high'].values
    lows = daily_long['low'].values
    dates = daily_long['date'].values
    
    if len(prices) >= 60:
        # Recent 60-day trend
        recent = daily_long.tail(60)
        p60_start = recent['close'].iloc[0]
        p60_end = recent['close'].iloc[-1]
        p60_high = recent['high'].max()
        p60_low = recent['low'].min()
        p60_range = ((p60_high - p60_low) / p60_low) * 100
        
        trend_60 = "上涨" if p60_end > p60_start * 1.05 else \
                   "下跌" if p60_end < p60_start * 0.95 else \
                   "横盘震荡"
        
        print(f"  近60日: 从{p60_start:.2f}→{p60_end:.2f} ({trend_60})")
        print(f"  60日区间: {p60_low:.2f} ~ {p60_high:.2f} (幅度{p60_range:.1f}%)")
        
        # MA60 position
        if factors:
            if daily_long['close'].iloc[-1] > factors['ma60']:
                print(f"  当前价格在MA60之上 (偏多)")
            else:
                print(f"  当前价格在MA60之下 (偏空)")
    
    # Recent price action
    last_10 = daily_long.tail(10)
    print(f"\n  近10日K线走势:")
    for _, r in last_10.iterrows():
        chg = ((r['close'] - r['open']) / r['open']) * 100
        vol_str = f"量{r['vol']/1000000:.1f}百万"
        arrow = "↑" if chg > 0 else "↓" if chg < 0 else "→"
        marker = "★涨停" if chg >= 9.5 else "◆跌停" if chg <= -9.5 else ""
        print(f"    {r['date']} {arrow}{abs(chg):.2f}% o={r['open']:.2f} h={r['high']:.2f} l={r['low']:.2f} c={r['close']:.2f} {vol_str} {marker}")
    
    # Identify Chan buy/sell points
    print(f"\n  关键缠论信号:")
    
    # Check for底分型 (bottom fractal): low < prev low AND low < next low
    # Simplified: check if recent days show a bottom pattern
    if len(prices) >= 5:
        recent_lows = daily_long['low'].tail(5).values
        if recent_lows[-3] == min(recent_lows):  # middle is lowest
            print(f"    ⚠️ 疑似底分型形成 (5日内中间日最低)")
        if recent_lows[-1] > recent_lows[-2] and recent_lows[-3] < recent_lows[-2]:
            print(f"    ✅ 底分型确认: 最后两日连续上升")
    
    # Check MACD divergence (simplified)
    if factors and len(prices) >= 30:
        # Get MACD histogram values for last 30 days
        # We don't have full MACD history in stock_factors, so use the current value only
        if factors['macd_hist'] > 0:
            print(f"    ✅ MACD红柱: 多头动能持续")
        else:
            print(f"    🔴 MACD绿柱: 空头动能主导")
    
    print()

# Analyze both stocks
chan_analysis('300201', '海伦哲')
chan_analysis('300854', '中兰环保')

conn.close()
print("="*60)
print("  分析完毕")
print("="*60)