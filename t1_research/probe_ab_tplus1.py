#!/usr/bin/env python3
"""
AB候选 T+1 次日表现回测
从 dragon_quant_meta.db 的 stock_daily_meta 读取日K，
统计AB候选（前日涨停+竞价高开4-7%）的当日涨停率 + 次日开盘/收盘表现。
"""
import sqlite3
from collections import defaultdict

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
conn = sqlite3.connect(DB)
cursor = conn.cursor()

# 只取最近2年数据
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

# 过滤条件
def is_excluded(code):
    """排除北交所、科创板"""
    return code.startswith(('688', '8'))

# 判断涨停
def check_zt(pct_chg, code):
    if pct_chg is None:
        return False
    if code.startswith('300'):
        return pct_chg >= 19
    return pct_chg >= 9.5

# 遍历
ab_candidates = []  # 每个元素: {code, date, gap, pct_chg, is_zt, next_open_pct, next_pct_chg}

for code, days in by_code.items():
    if is_excluded(code):
        continue
    if len(days) < 3:  # 需要前日、今日、次日数据
        continue
    for i in range(1, len(days)):
        d = days[i]
        d_prev = days[i-1]
        
        date, open_p, high, low, close, volume, amount, pct_chg = d[1], d[2], d[3], d[4], d[5], d[6], d[7], d[8]
        prev_close, prev_pct = d_prev[5], d_prev[8]
        
        # 前日是否涨停
        if prev_pct is None or not check_zt(prev_pct, code):
            continue
        
        # 今日竞价缺口
        gap = open_p / prev_close - 1
        
        # AB条件：高开4-7%
        if not (0.04 <= gap <= 0.07):
            continue
        
        if pct_chg is None:
            continue
        
        # 今日是否涨停
        is_zt = check_zt(pct_chg, code)
        
        # 查找下一个交易日数据
        next_data = None
        if i + 1 < len(days):
            d_next = days[i+1]
            if d_next[0] == code:  # 确保是同一只股票
                n_open = d_next[2]
                n_close = d_next[5]
                n_pct = d_next[8]
                if n_pct is not None and n_open is not None:
                    # 相对于当日收盘的次日开盘涨跌幅
                    next_open_pct = (n_open / close - 1) * 100
                    next_pct_chg = n_pct
                    next_data = {
                        'next_open_pct': next_open_pct,
                        'next_pct_chg': next_pct_chg,
                        'next_zt': check_zt(n_pct, code)
                    }
        
        ab_candidates.append({
            'code': code,
            'date': date,
            'gap': round(gap, 4),
            'pct_chg': round(pct_chg, 2),
            'is_zt': is_zt,
            'next': next_data
        })

print(f"\nAB候选总数（已过滤科创/北交）: {len(ab_candidates)}")

if not ab_candidates:
    print("无数据！")
    exit()

# ==== 当日统计 ====
zt_count = sum(1 for c in ab_candidates if c['is_zt'])
print(f"\n{'='*60}")
print(f"一、当日涨停率")
print(f"{'='*60}")
print(f"AB候选当日涨停: {zt_count}/{len(ab_candidates)} = {zt_count/len(ab_candidates)*100:.2f}%")

# 按缺口分层
bins = {'4-5%': {'n':0,'zt':0}, '5-6%': {'n':0,'zt':0}, '6-7%': {'n':0,'zt':0}}
for c in ab_candidates:
    gp = c['gap'] * 100
    if 4 <= gp < 5: bins['4-5%']['n'] += 1; bins['4-5%']['zt'] += c['is_zt']
    elif 5 <= gp < 6: bins['5-6%']['n'] += 1; bins['5-6%']['zt'] += c['is_zt']
    elif 6 <= gp <= 7: bins['6-7%']['n'] += 1; bins['6-7%']['zt'] += c['is_zt']

print(f"\n按竞价缺口分层:")
for k, v in bins.items():
    if v['n'] > 0:
        print(f"  {k}: {v['zt']}/{v['n']} = {v['zt']/v['n']*100:.2f}% 涨停")

# 按是否有次日数据切分
with_next = [c for c in ab_candidates if c['next'] is not None]
print(f"\n有次日数据的AB候选: {len(with_next)}/{len(ab_candidates)}")

# ==== 次日统计 ====
print(f"\n{'='*60}")
print(f"二、T+1 次日表现（含次日数据样本）")
print(f"{'='*60}")

if with_next:
    # 次日开盘涨跌幅分布
    open_positive = sum(1 for c in with_next if c['next']['next_open_pct'] > 0)
    open_ge3pct = sum(1 for c in with_next if c['next']['next_open_pct'] >= 3)
    open_ge5pct = sum(1 for c in with_next if c['next']['next_open_pct'] >= 5)
    
    print(f"次日开盘正收益: {open_positive}/{len(with_next)} = {open_positive/len(with_next)*100:.2f}%")
    print(f"次日开盘≥+3%:   {open_ge3pct}/{len(with_next)} = {open_ge3pct/len(with_next)*100:.2f}%")
    print(f"次日开盘≥+5%:   {open_ge5pct}/{len(with_next)} = {open_ge5pct/len(with_next)*100:.2f}%")
    
    # 次日收盘表现
    close_positive = sum(1 for c in with_next if c['next']['next_pct_chg'] > 0)
    close_ge3pct = sum(1 for c in with_next if c['next']['next_pct_chg'] >= 3)
    close_ge5pct = sum(1 for c in with_next if c['next']['next_pct_chg'] >= 5)
    close_zt = sum(1 for c in with_next if c['next']['next_zt'])
    
    print(f"\n次日收盘正收益: {close_positive}/{len(with_next)} = {close_positive/len(with_next)*100:.2f}%")
    print(f"次日收盘≥+3%:   {close_ge3pct}/{len(with_next)} = {close_ge3pct/len(with_next)*100:.2f}%")
    print(f"次日收盘≥+5%:   {close_ge5pct}/{len(with_next)} = {close_ge5pct/len(with_next)*100:.2f}%")
    print(f"次日再涨停:      {close_zt}/{len(with_next)} = {close_zt/len(with_next)*100:.2f}%")
    
    # 当日已涨停的子集次日表现
    zt_subset = [c for c in with_next if c['is_zt']]
    if zt_subset:
        zt_open_pos = sum(1 for c in zt_subset if c['next']['next_open_pct'] > 0)
        zt_open_ge3 = sum(1 for c in zt_subset if c['next']['next_open_pct'] >= 3)
        zt_close_pos = sum(1 for c in zt_subset if c['next']['next_pct_chg'] > 0)
        zt_close_ge3 = sum(1 for c in zt_subset if c['next']['next_pct_chg'] >= 3)
        zt_close_zt = sum(1 for c in zt_subset if c['next']['next_zt'])
        
        print(f"\n--- 当日已涨停的子集 ({len(zt_subset)}条) ---")
        print(f"次日开盘正收益:   {zt_open_pos}/{len(zt_subset)} = {zt_open_pos/len(zt_subset)*100:.2f}%")
        print(f"次日开盘≥+3%:     {zt_open_ge3}/{len(zt_subset)} = {zt_open_ge3/len(zt_subset)*100:.2f}%")
        print(f"次日收盘正收益:   {zt_close_pos}/{len(zt_subset)} = {zt_close_pos/len(zt_subset)*100:.2f}%")
        print(f"次日收盘≥+3%:     {zt_close_ge3}/{len(zt_subset)} = {zt_close_ge3/len(zt_subset)*100:.2f}%")
        print(f"次日再涨停:       {zt_close_zt}/{len(zt_subset)} = {zt_close_zt/len(zt_subset)*100:.2f}%")
    
    # 当日未涨停的子集次日表现（低吸端）
    nonzt_subset = [c for c in with_next if not c['is_zt']]
    if nonzt_subset:
        print(f"\n--- 当日未涨停的子集 ({len(nonzt_subset)}条) ---")
        nz_close_pos = sum(1 for c in nonzt_subset if c['next']['next_pct_chg'] > 0)
        nz_close_ge3 = sum(1 for c in nonzt_subset if c['next']['next_pct_chg'] >= 3)
        print(f"次日收盘正收益: {nz_close_pos}/{len(nonzt_subset)} = {nz_close_pos/len(nonzt_subset)*100:.2f}%")
        print(f"次日收盘≥+3%:   {nz_close_ge3}/{len(nonzt_subset)} = {nz_close_ge3/len(nonzt_subset)*100:.2f}%")

# ==== 按分位统计次日开盘溢价 ====
if with_next:
    next_opens = sorted([c['next']['next_open_pct'] for c in with_next])
    n = len(next_opens)
    print(f"\n{'='*60}")
    print(f"三、次日开盘溢价分位数")
    print(f"{'='*60}")
    for p in [10, 25, 50, 75, 90]:
        idx = int(n * p / 100)
        print(f"  P{p}: {next_opens[idx]:.2f}%")
    print(f"  均值: {sum(next_opens)/n:.2f}%")
    print(f"  中位数: {next_opens[n//2]:.2f}%")

conn.close()