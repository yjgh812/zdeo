#!/usr/bin/env python3
"""
AB候选涨停率回测探针
从 dragon_quant_meta.db 的 stock_daily_meta 读取日K，
模拟AB规则（前日涨停+今日竞价高开4-7%），统计当日涨停率。
"""
import sqlite3
from collections import defaultdict

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"

conn = sqlite3.connect(DB)
cursor = conn.cursor()

# 只取最近2年数据（2024-08 ~ 2026-08）减少计算量
cursor.execute("""
SELECT code, date, open, high, low, close, volume, amount, change_pct
FROM stock_daily_meta
WHERE date >= 20240801
ORDER BY code, date
""")
rows = cursor.fetchall()
print(f"Rows (2024-08 ~ 2026-08): {len(rows)}")

# 按code分组
by_code = defaultdict(list)
for r in rows:
    by_code[r[0]].append(r)

print(f"Unique stocks: {len(by_code)}")

# 判断涨停（主板10%, 创业板/科创板20%）
def check_zt(close, pre_close, code):
    # 粗略判断：代码688/300开头=20%，否则10%
    if code.startswith('688') or code.startswith('300'):
        threshold = 1.195
    else:
        threshold = 1.095
    return close >= pre_close * threshold

# 遍历
ab_candidates = []  # (code, date, gap, day_pct, is_zt, code_prefix)

for code, days in by_code.items():
    if len(days) < 2:
        continue
    for i in range(1, len(days)):
        d = days[i]
        d_prev = days[i-1]
        
        date, open_p, high, low, close, volume, amount, pct_chg = d[1], d[2], d[3], d[4], d[5], d[6], d[7], d[8]
        prev_close, prev_pct = d_prev[5], d_prev[8]
        
        # 前日是否涨停（用pct_chg判断）
        if prev_pct is None:
            continue
        if code.startswith('688') or code.startswith('300'):
            prev_zt = prev_pct >= 19
        else:
            prev_zt = prev_pct >= 9.5
        
        if not prev_zt:
            continue
        
        # 今日竞价缺口
        gap = open_p / prev_close - 1
        
        # AB条件：高开4-7%
        if not (0.04 <= gap <= 0.07):
            continue
        
        # 今日是否涨停
        if pct_chg is None:
            continue
        if code.startswith('688') or code.startswith('300'):
            is_zt = pct_chg >= 19
        else:
            is_zt = pct_chg >= 9.5
        
        ab_candidates.append({
            'code': code,
            'date': date,
            'gap': round(gap, 4),
            'pct_chg': round(pct_chg, 2),
            'is_zt': is_zt,
            'prefix': code[:3]
        })

print(f"\nAB候选总数（2024-08 ~ 2026-08）: {len(ab_candidates)}")

if not ab_candidates:
    print("无AB候选！检查数据或条件")
    exit()

# 总体统计
zt_count = sum(1 for c in ab_candidates if c['is_zt'])
print(f"\n=== 总体结果 ===")
print(f"AB候选当日涨停: {zt_count}/{len(ab_candidates)} = {zt_count/len(ab_candidates)*100:.2f}%")

# 按竞价缺口分层
bins = {'4-5%': {'n':0,'zt':0}, '5-6%': {'n':0,'zt':0}, '6-7%': {'n':0,'zt':0}}
for c in ab_candidates:
    gp = c['gap'] * 100
    if 4 <= gp < 5: bins['4-5%']['n'] += 1; bins['4-5%']['zt'] += c['is_zt']
    elif 5 <= gp < 6: bins['5-6%']['n'] += 1; bins['5-6%']['zt'] += c['is_zt']
    elif 6 <= gp <= 7: bins['6-7%']['n'] += 1; bins['6-7%']['zt'] += c['is_zt']

print(f"\n=== 按竞价缺口分层 ===")
for k, v in bins.items():
    if v['n'] > 0:
        print(f"  {k}: {v['zt']}/{v['n']} = {v['zt']/v['n']*100:.2f}%")

# 按板块分层（主板 vs 创业板/科创板）
boards = {'主板': {'n':0,'zt':0}, '创业板/科创板': {'n':0,'zt':0}}
for c in ab_candidates:
    if c['prefix'] in ('300','688'):
        boards['创业板/科创板']['n'] += 1
        boards['创业板/科创板']['zt'] += c['is_zt']
    else:
        boards['主板']['n'] += 1
        boards['主板']['zt'] += c['is_zt']

print(f"\n=== 按板块分层 ===")
for k, v in boards.items():
    if v['n'] > 0:
        print(f"  {k}: {v['zt']}/{v['n']} = {v['zt']/v['n']*100:.2f}%")

# 全市场当日涨停率（对比基线）
# 取AB候选出现的日期
ab_dates = set(c['date'] for c in ab_candidates)
print(f"\n=== 全市场对比（AB候选出现日） ===")
# 计算这些日期的全市场涨停率
for target_date in sorted(ab_dates, reverse=True)[:30]:  # 最近30个有AB候选的交易日
    cursor.execute("""
    SELECT code, change_pct FROM stock_daily_meta
    WHERE date = ? AND change_pct IS NOT NULL
    """, (str(target_date),))
    day_rows = cursor.fetchall()
    total = len(day_rows)
    zt = sum(1 for r in day_rows if (r[1] >= 9.5 if not r[0].startswith(('300','688')) else r[1] >= 19))
    
    # 当日AB候选数量及涨停数
    day_ab = [c for c in ab_candidates if c['date'] == target_date]
    ab_n = len(day_ab)
    ab_zt = sum(1 for c in day_ab if c['is_zt'])
    
    if total > 0 and ab_n > 0:
        print(f"  {target_date}: 全市场涨停 {zt}/{total}={zt/total*100:.1f}% | AB候选 {ab_zt}/{ab_n}={ab_zt/ab_n*100:.1f}%")

print(f"\n=== 样本AB候选（前20条） ===")
for c in ab_candidates[:20]:
    print(f"  {c['code']} @ {c['date']} | gap={c['gap']*100:.1f}% | day={c['pct_chg']:.1f}% | ZT={'Y' if c['is_zt'] else 'N'}")

conn.close()