# -*- coding: utf-8 -*-
"""百亚股份(003006) 2025/1/1至今 波段划分+日线图可视化
波段=Zigzag 12%(缠论笔对齐口径); 上涨段绿色连线/下跌段红色连线+每段标注涨跌幅/历时;
标注买入日(2026-09-07)竖线。输出 reports/baiya_waves_20250101_20260918.png
"""
import sqlite3, sys, os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"

def f3(x):
    try: return float(x)
    except Exception: return None

def zigzag(closes, pct=0.12):
    if len(closes) < 10: return [0, len(closes)-1]
    piv=[0]; d=0; ext=0
    for i in range(1, len(closes)):
        if d >= 0:
            if closes[i] > closes[ext]: ext=i
            elif closes[i]/closes[ext]-1 <= -pct: piv.append(ext); d=-1; ext=i
        else:
            if closes[i] < closes[ext]: ext=i
            elif closes[i]/closes[ext]-1 >= pct: piv.append(ext); d=1; ext=i
    piv.append(ext if ext > piv[-1] else len(closes)-1)
    return piv

db = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
rows = db.execute("SELECT date, close FROM stock_daily_meta_qfq WHERE code='003006' AND date>='20250101' ORDER BY date ASC").fetchall()
db.close()
data = [(d, f3(c)) for d, c in rows if f3(c) is not None]
# ===== 修正: qfq前复权价 -> 不复权实际价(主人9/19纠错: 百亚2025/4/23除息0.55元+2026/5/20除息0.40元, qfq历史价偏低) =====
# qfq因子还原: 不复权价 = qfq价 / 复权因子; 因子用 baostock 不复权收盘/qfq收盘 逐段推算(除息日前差一个分红额比例)
import subprocess, csv, io
r = subprocess.run([sys.executable, '-c', '''
import baostock as bs
import pandas as pd
bs.login()
df = bs.query_history_k_data_plus("sz.003006", "date,close", start_date="2025-01-01", end_date="2026-09-18", frequency="d", adjustflag="3").get_data()
bs.logout()
print("DATE,CLOSE")
for _, row in df.iterrows():
    print(row["date"].replace("-", ""), ",", row["close"])
'''], capture_output=True, text=True, timeout=120, encoding='utf-8', errors='replace')
raw_close = {}
for line in (r.stdout or '').splitlines()[1:]:
    parts = [p.strip() for p in line.split(',')]
    if len(parts) == 2 and parts[1].replace('.', '').isdigit():
        raw_close[parts[0]] = float(parts[1])
if len(raw_close) > 100:
    data = [(d, raw_close[d]) for d, _ in data if d in raw_close]
    print(f'[修正] 已切换不复权实际价 {len(data)} 日')
dates = [datetime.strptime(r[0], '%Y%m%d') for r in data]
closes = [r[1] for r in data]
n = len(closes)

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False
fig, ax = plt.subplots(figsize=(16, 9), dpi=110)

# 日线
ax.plot(dates, closes, color='#888888', linewidth=0.8, alpha=0.7, label='日线收盘价(不复权实际价)')

# 波段连线
pv = zigzag(closes, 0.12)
for k in range(len(pv)-1):
    a, b = pv[k], pv[k+1]
    chg = (closes[b]/closes[a]-1)*100
    col = '#d62728' if chg < 0 else '#2ca02c'   # 跌=红 涨=绿(A股习惯)
    ax.plot([dates[a], dates[b]], [closes[a], closes[b]], color=col, linewidth=2.5,
            marker='o', markersize=5, zorder=5)
    # 段标注
    mx, my = dates[(a+b)//2], (closes[a]+closes[b])/2
    ax.annotate(f'{chg:+.1f}%\n{b-a}日', xy=(mx, my), fontsize=8, ha='center',
                color=col, fontweight='bold',
                xytext=(0, 12 if chg < 0 else -18), textcoords='offset points')

# 买入日竖线
try:
    bi = next(i for i, d in enumerate(data) if d[0] == '20260907')
    ax.axvline(x=dates[bi], color='#1f77b4', linestyle='--', linewidth=1.5, alpha=0.8)
    ax.annotate('买入日 2026-09-07\n@14.93 (现+42%)', xy=(dates[bi], closes[bi]),
                xytext=(-120, 30), textcoords='offset points', fontsize=9, color='#1f77b4',
                arrowprops=dict(arrowstyle='->', color='#1f77b4'))
except StopIteration:
    pass

# 低点标注
lo_i = closes.index(min(closes))
ax.annotate(f'低点 {data[lo_i][0][:4]}-{data[lo_i][0][4:6]}-{data[lo_i][0][6:]}\n@{closes[lo_i]:.2f}',
            xy=(dates[lo_i], closes[lo_i]), xytext=(15, -25), textcoords='offset points',
            fontsize=9, color='#d62728', arrowprops=dict(arrowstyle='->', color='#d62728'))

# ===== 所有拐点标注: 时间+价位(交替上下偏移防重叠) =====
for pi, p in enumerate(pv):
    dstr = data[p][0]
    dlabel = f"{dstr[2:4]}/{dstr[4:6]}/{dstr[6:]}"
    plabel = f"{dlabel} {closes[p]:.2f}"
    is_peak = pi > 0 and closes[p] > closes[pv[pi-1]] if pi < len(pv) else True
    # 峰=上方标注, 谷=下方标注; 同向再交错一层
    if pi == 0:
        dy, col = 18, '#555555'
    elif closes[p] >= closes[pv[pi-1]]:
        dy, col = 18 + (pi % 2) * 14, '#2ca02c'
    else:
        dy, col = -30 - (pi % 2) * 14, '#d62728'
    ax.annotate(plabel, xy=(dates[p], closes[p]), xytext=(0, dy), textcoords='offset points',
                fontsize=7.5, color=col, ha='center', fontweight='bold', zorder=6,
                bbox=dict(boxstyle='round,pad=0.15', fc='white', ec=col, alpha=0.75, lw=0.5))

ax.set_title('百亚股份(003006) 上涨/下跌波段划分  2025-01-01 至 2026-09-18\n波段=12%转折阈值｜绿线=上涨段 红线=下跌段｜拐点标注=日期+价位', fontsize=13)
ax.set_ylim(min(closes)*0.92, max(closes)*1.12)
ax.xaxis.set_major_formatter(mdates.DateFormatter('%y-%m'))
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
ax.grid(alpha=0.25)
ax.legend(loc='upper right')
plt.tight_layout()
out = os.path.join(ROOT, 'reports', 'baiya_waves_20250101_20260918.png')
plt.savefig(out, bbox_inches='tight')
print(f'[PLOT-OK] {out}')
print(f'波段数: {len(pv)-1} 段, 拐点 {len(pv)} 个')
for k in range(len(pv)-1):
    a, b = pv[k], pv[k+1]
    chg = (closes[b]/closes[a]-1)*100
    tag = '上涨' if chg > 0 else '下跌'
    print(f'  {tag} {data[a][0]}→{data[b][0]} {closes[a]:.2f}→{closes[b]:.2f} {chg:+.1f}% {b-a}日')
