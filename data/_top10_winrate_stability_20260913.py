# -*- coding: utf-8 -*-
"""Top10 胜率稳定性分级(只读): 逐半年胜率序列 → 区分"稳定高胜率"vs"单段撑起".
规则(主人: 胜率高用谁): S=近4个半年段全部>50%且全程≥55% / A=近2段>50%且全程≥52% / B=其余.
数据: qfq 月末收盘(与此前口径一致)."""
import sys, sqlite3, json
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd

TOP10 = [
    ("603328", "依顿电子"), ("002966", "苏州银行"), ("601328", "交通银行"),
    ("603637", "镇海股份"), ("002478", "常宝股份"), ("603162", "海通发展"),
    ("002942", "新农股份"), ("300214", "日科化学"), ("601077", "渝农商行"),
    ("300154", "瑞凌股份"),
]
con = sqlite3.connect(r'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro', uri=True, timeout=120)
codes = [c for c, _ in TOP10]
ph = ','.join('?' * len(codes))
px = pd.read_sql_query(f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20210101' ORDER BY code,date",
                       con, params=codes)
con.close()
px['date'] = px['date'].astype(str)
px['ym'] = px['date'].str[:6]
me = px.sort_values('date').groupby(['code', 'ym'])['close'].last().reset_index()
me = me.sort_values(['code', 'ym'])
me['up'] = me.groupby('code')['close'].pct_change() > 0
me = me[me['ym'] >= '202109']

# 半年段: 2021H2, 2022H1, 2022H2, ..., 2026H1, 2026H2(进行中)
def half(ym):
    y, m = int(ym[:4]), int(ym[4:6])
    return f"{y}{'H1' if m <= 6 else 'H2'}"

print("=== Top10 逐半年胜率(%) — 稳定性分级 ===")
print(f"{'股票':<12s}" + ''.join(f"{h:>8s}" for h in ['21H2','22H1','22H2','23H1','23H2','24H1','24H2','25H1','25H2','26进行']))
grades = {}
for code, name in TOP10:
    g = me[me['code'] == code].copy()
    g['half'] = g['ym'].apply(half)
    hs = g.groupby('half')['up'].mean() * 100
    line = f"{name:<10s}"
    vals = []
    for h in ['2021H2','2022H1','2022H2','2023H1','2023H2','2024H1','2024H2','2025H1','2025H2','2026H2']:
        v = hs.get(h, np.nan)
        vals.append(v)
        line += f"{v:7.1f}%" if not np.isnan(v) else '     - '
    # 稳定性: 近8个半年段中 >50% 的段数
    recent = [v for v in vals if not np.isnan(v)]
    stable = sum(1 for v in recent if v > 50)
    line += f"  → >50%段数={stable}/{len(recent)}"
    grades[name] = (stable, len(recent))
    print(line)

print("\n=== 分级 ===")
for code, name in TOP10:
    st, tot = grades[name]
    grade = 'S(≥7段)' if st >= 7 else ('A(5-6段)' if st >= 5 else ('B(3-4段)' if st >= 3 else 'C(<3段)'))
    print(f"  {name}: {grade}")
