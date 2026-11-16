#!/usr/bin/env python3
"""实战分析演示 — 使用新术语标准（自适应数据库schema）"""
import sqlite3

FUND = r'D:\workbuddyspace\databases\fundamental_data.db'
MAIN = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
SWDB = r'D:\workbuddyspace\databases\sw_industry.db'

print("=" * 70)
print("《屠龙系统量化术语标准手册》实战分析演示")
print("日期: 2026-08-15 | 数据基准日: 20260814")
print("=" * 70)

# ==== 1. 情绪周期判定 [SC-001] ====
print("\n[SC-001] 1. 情绪周期判定")
with sqlite3.connect(f'file:{FUND}?mode=ro', uri=True) as c:
    rows = c.execute('''SELECT trade_date, limit_up, limit_down
        FROM feature_market_sentiment
        ORDER BY trade_date DESC LIMIT 5''').fetchall()
print("近5日涨停/跌停趋势:")
for d, lu, ld in rows:
    phase = "冰点" if lu < 20 else ("高潮" if lu > 80 else "发酵/主升")
    print(f"  {d}: 涨停{lu} 跌停{ld} → {phase}")

latest = rows[0]
lu, ld = latest[1], latest[2]
if lu < 20:
    verdict = "冰点/启动 — 低吸试仓，禁重仓"
elif lu > 80:
    verdict = "高潮/分歧 — 逐步减仓，禁新开高位"
elif 40 <= lu <= 80 and ld < 10:
    verdict = "发酵/主升 — 可满仓做主线 ✅"
else:
    verdict = "温和/过渡 — 正常操作，注意仓位"

print(f"\n判定: 涨停{lu} + 跌停{ld} → {verdict}")

# ==== 2. 主线判定 [TH-001] ====
print("\n[TH-001] 2. 主线判定（行业K线涨幅TOP10）")
with sqlite3.connect(f'file:{SWDB}?mode=ro', uri=True) as c:
    ind = c.execute('''SELECT i1.industry_name, i1.close,
        (i1.close - i2.close) / i2.close * 100 as pct_chg
        FROM industry_kline_ths i1
        JOIN industry_kline_ths i2
          ON i1.industry_name = i2.industry_name
          AND i2.trade_date = (SELECT MAX(trade_date) FROM industry_kline_ths WHERE trade_date < i1.trade_date)
        WHERE i1.trade_date = '2026-08-14'
        ORDER BY pct_chg DESC LIMIT 10''').fetchall()
if ind:
    print("板块涨幅前十:")
    for name, close, pct in ind:
        marker = " ✅" if pct > 3 else ""
        print(f"  {name}: {pct:+.2f}%{marker}")

# ==== 3. 涨停板数据分析 ====
print("\n[LU] 3. 涨停板数据（≥9.9%视为主板涨停）")
with sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True) as c:
    lu_today = c.execute('''
        SELECT t.code, t.close,
        ROUND((t.close - y.close) / y.close * 100, 1) as pct
        FROM stock_daily_hsj t
        JOIN stock_daily_hsj y ON t.code = y.code AND y.date = 20260813
        WHERE t.date = 20260814
        AND (t.close - y.close) / y.close * 100 >= 9.9
        ORDER BY pct DESC''').fetchall()
print(f"今日涨停: {len(lu_today)} 只")
for row in lu_today[:10]:
    print(f"  {row[0]}: +{row[2]:.1f}%")

# ==== 4. 跌停板 ====
with sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True) as c:
    ld_today = c.execute('''
        SELECT t.code, t.close,
        ROUND((t.close - y.close) / y.close * 100, 1) as pct
        FROM stock_daily_hsj t
        JOIN stock_daily_hsj y ON t.code = y.code AND y.date = 20260813
        WHERE t.date = 20260814
        AND (t.close - y.close) / y.close * 100 <= -9.9
        ORDER BY pct ASC''').fetchall()
print(f"\n今日跌停: {len(ld_today)} 只")
for row in ld_today[:5]:
    print(f"  {row[0]}: {row[2]:.1f}%")

# ==== 5. 综合市场画像 ====
print("\n" + "=" * 70)
print("📋 综合市场画像（量化标准格式）")
print("=" * 70)
print(f"[SC-001] 涨停{lu}/跌停{ld} → {verdict}")
print(f"[LU-006] 今日涨停{len(lu_today)}只, 跌停{len(ld_today)}只")
if ind:
    top3 = ", ".join([f"{r[0]}({r[2]:+.1f}%)" for r in ind[:3]])
    print(f"[TH-001] 板块涨幅前3: {top3}")
print(f"[T-002] ADX需运行 trend_checker 获取")
print(f"\n执行规范: 严格使用《屠龙系统量化术语标准手册》v1.0")
print(f"✅ 无模糊词 ✅ 无YTD判趋势 ✅ 无PE为负当卖出理由")