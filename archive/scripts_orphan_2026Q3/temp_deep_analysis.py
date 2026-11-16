#!/usr/bin/env python3
"""Deep market analysis for 20260814."""
import sqlite3
from collections import Counter

main = sqlite3.connect('D:/workbuddyspace/factor_engine/data/dragon_quant.db', timeout=60)

print("=" * 70)
print("📊 2026-08-14 深度市场分析报告")
print("=" * 70)

# === 1. 指数环境 ===
print("\n【一、指数环境】")
# 上证指数 - use index_daily (last available is 20260813)
sh = main.execute("SELECT date, close FROM index_daily WHERE index_code='000001' ORDER BY date DESC LIMIT 3").fetchall()
print("  上证指数:")
for r in sh:
    print(f"    {r[0]}: {r[1]:.2f}")

# Other indexes from 20260813
others = main.execute("SELECT index_code, close FROM index_daily WHERE date=20260813 AND index_code IN ('399001','399006','000688','000300','000016')").fetchall()
name_map = {'399001':'深证成指','399006':'创业板指','000688':'科创50','000300':'沪深300','000016':'上证50'}
for r in others:
    print(f"  {name_map.get(r[0],r[0])}: {r[1]:.2f}")

# === 2. 市场广度 ===
print("\n【二、市场广度】")
rows = main.execute("""
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN (close-open)/NULLIF(open,0)*100 > 0 THEN 1 ELSE 0 END) as up,
        SUM(CASE WHEN (close-open)/NULLIF(open,0)*100 < 0 THEN 1 ELSE 0 END) as down,
        SUM(CASE WHEN (close-open)/NULLIF(open,0)*100 >= 9.5 AND close > open THEN 1 ELSE 0 END) as zt,
        SUM(CASE WHEN (open-close)/NULLIF(open,0)*100 >= 9.5 AND close < open THEN 1 ELSE 0 END) as dt,
        ROUND(AVG((close-open)/NULLIF(open,0)*100), 2) as avg,
        ROUND(SUM(amt)/100000000.0, 0) as total_amt_yi
    FROM stock_daily_hsj 
    WHERE date=20260814 AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')
""").fetchone()
print(f"  总样本: {rows[0]} | 上涨: {rows[1]} | 下跌: {rows[2]} | 平盘: {rows[0]-rows[1]-rows[2]}")
print(f"  涨停(>=9.5%): {rows[3]} | 跌停(<=-9.5%): {rows[4]}")
print(f"  平均涨跌幅: {rows[5]:+.2f}%")
print(f"  总成交额: {rows[6]:.0f}亿")
print(f"  涨跌比: {rows[1]/max(rows[2],1):.2f}:1")

# === 3. 涨跌结构 ===
print("\n【三、涨跌结构分布】")
buckets = main.execute("""
    SELECT 
        CASE 
            WHEN (close-open)/NULLIF(open,0)*100 >= 9.5 THEN '涨停'
            WHEN (close-open)/NULLIF(open,0)*100 >= 7 THEN '大涨7%+'
            WHEN (close-open)/NULLIF(open,0)*100 >= 4 THEN '大涨4%+'
            WHEN (close-open)/NULLIF(open,0)*100 >= 2 THEN '中涨2%+'
            WHEN (close-open)/NULLIF(open,0)*100 > 0 THEN '微涨'
            WHEN (close-open)/NULLIF(open,0)*100 = 0 THEN '平盘'
            WHEN (close-open)/NULLIF(open,0)*100 > -2 THEN '微跌'
            WHEN (close-open)/NULLIF(open,0)*100 > -4 THEN '中跌4%-'
            WHEN (close-open)/NULLIF(open,0)*100 > -7 THEN '大跌7%-'
            WHEN (close-open)/NULLIF(open,0)*100 > -9.5 THEN '大跌'
            ELSE '跌停'
        END as bucket,
        COUNT(*) as cnt
    FROM stock_daily_hsj 
    WHERE date=20260814 AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')
    GROUP BY bucket ORDER BY 
        CASE bucket
            WHEN '涨停' THEN 1 WHEN '大涨7%+' THEN 2 WHEN '大涨4%+' THEN 3
            WHEN '中涨2%+' THEN 4 WHEN '微涨' THEN 5 WHEN '平盘' THEN 6
            WHEN '微跌' THEN 7 WHEN '中跌4%-' THEN 8 WHEN '大跌7%-' THEN 9
            WHEN '大跌' THEN 10 ELSE 11
        END
""").fetchall()
maxc = max(b[1] for b in buckets)
for b in buckets:
    bar = '█' * int(b[1] / maxc * 30)
    print(f"  {b[0]:>8}: {b[1]:>5} {bar}")

# === 4. 板块跟踪 (from stock_factors sector info) ===
print("\n【四、成交额与活跃板块】")
# Top 10 by volume
top_amt = main.execute("""
    SELECT code, ROUND(amt/100000000.0, 1) as amt_yi, 
           ROUND((close-open)/NULLIF(open,0)*100, 2) as chg_pct
    FROM stock_daily_hsj 
    WHERE date=20260814 AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')
    ORDER BY amt DESC LIMIT 15
""").fetchall()
print("  成交额TOP15:")
for r in top_amt:
    arrow = '↑' if r[2] > 0 else '↓' if r[2] < 0 else '→'
    print(f"    {r[0]}: ¥{r[1]:>6.1f}亿 {arrow}{abs(r[2]):.2f}%")

# === 5. 聪明钱因子 ===
print("\n【五、聪明钱因子排行】")
sm_top = main.execute("""
    SELECT a.code, b.close, a.smart_money, a.rank,
           ROUND((b.close-b.open)/NULLIF(b.open,0)*100, 2) as chg
    FROM mv_daily_smart_money a
    JOIN stock_daily_hsj b ON a.code=b.code AND a.date=b.date
    WHERE a.date=20260814 AND b.close < 200
    ORDER BY a.smart_money DESC LIMIT 15
""").fetchall()
print("  聪明钱TOP15:")
for r in sm_top:
    print(f"  #{r[3]:>3} {r[0]}: SM={r[2]:.1f}, close={r[1]:.2f}, chg={r[4]:+.2f}%")

# === 6. V375信号高概率候选 ===
print("\n【六、V375高概率候选 (prob > 0.75, price <= 30)】")
v375_candidates = main.execute("""
    SELECT s.code, s.v375_prob, d.close,
           ROUND((d.close-d.open)/NULLIF(d.open,0)*100, 2) as chg
    FROM stock_signals_v375 s
    JOIN stock_daily_hsj d ON s.code=d.code AND s.date=d.date
    WHERE s.date=20260814 AND s.v375_prob >= 0.75
          AND d.close <= 30 AND d.close >= 3
          AND (d.code LIKE '00%' OR d.code LIKE '30%' OR d.code LIKE '60%')
    ORDER BY s.v375_prob DESC LIMIT 20
""").fetchall()
print(f"  共 {len(v375_candidates)} 只候选")
for r in v375_candidates:
    print(f"  {r[0]}: prob={r[1]:.4f}, close=¥{r[2]:.2f}, chg={r[3]:+.2f}%")

# === 7. 情绪指标 ===
print("\n【七、情绪与连板梯队】")
# Count zt_pool
zt = main.execute("SELECT COUNT(*) FROM zt_pool WHERE date=20260814").fetchone()[0]
zt_prev = main.execute("SELECT COUNT(*) FROM zt_pool_prev WHERE date=20260814").fetchone()[0]
print(f"  涨停数: {zt}")
print(f"  昨日涨停今表现: {zt_prev} 只跟踪")

# Check if zt_pool_prev has prev_close data
zt_prev_chg = main.execute("""
    SELECT code, name, ROUND(prev_close,2), ROUND(open_pct,2) 
    FROM zt_pool_prev WHERE date=20260814 LIMIT 10
""").fetchall()
if zt_prev_chg:
    pos = sum(1 for r in zt_prev_chg if r[3] and r[3] > 0)
    print(f"  昨日涨停今日高开: {pos}/{len(zt_prev_chg)}")
    for r in zt_prev_chg[:5]:
        print(f"    {r[0]} {r[1] or ''}: 昨收={r[2]:.2f}, 今开%={r[3]:+.2f}")

# === 8. 牛熊判定 ===
print("\n【八、牛熊判定与仓位取向】")
sh_idx = main.execute("SELECT close FROM index_daily WHERE index_code='000001' ORDER BY date DESC LIMIT 250").fetchall()
vals = [r[0] for r in sh_idx]
last_v = vals[0] if vals else None
ma250 = sum(vals)/len(vals) if len(vals)>=250 else None
if last_v and ma250:
    regime = "🐻 熊市" if last_v < ma250 else "🐂 牛市"
    print(f"  上证最新: {last_v} (20260813)")
    print(f"  MA250: {ma250:.2f}")
    print(f"  判定: {regime}")
    print(f"  仓位取向: {'中等偏高' if last_v > ma250 else '保守/低仓'}")
else:
    print("  数据不足判定")

main.close()