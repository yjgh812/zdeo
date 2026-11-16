# -*- coding: utf-8 -*-
"""ml臂 收阴顺延执行规则模拟 2026-09-18
动机: ml_signal 买入当日收阴簇胜率38.1% vs 收阳60.5%, 分年7/7全负 → 执行规则候选:
      「ml信号买入改为尾盘执行(收盘价成交), 当日收阴则顺延至下一收阳日, ≤3日仍阴放弃」
设计(同价对照分离择时效应与价格效应):
  对照组A: 全部信号在原买入日以收盘价成交 (与回测开盘价口径的差=价格效应)
  实验组B: 原买入日收阳→当日收盘成交; 收阴→顺延至首个收阳日收盘成交(≤3日); 超时放弃
  两组均以原卖出日卖出价计算收益(卖出侧不动), 加千3来回成本与不加各报。
  对照组A也加同样的"顺延放弃"边界无——A不放弃, 全成交; B放弃笔单列。
判定预声明: B胜率-A胜率 >= +5pp 且分年方向一致>=5/7 为候选PASS, 否则降级。
"""
import csv, json, sqlite3, statistics, sys, os
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = r'D:\zcodesj'
COST = 0.003  # 千3来回

qconn = sqlite3.connect(os.path.join(ROOT, 'data', 'dragon_quant_qfq.db'))
qcur = qconn.cursor()

def px_from(code, date, n=6):
    """date(含)起 n 个交易日 [date, close, change_pct] 升序"""
    r = qcur.execute("SELECT date, close, change_pct FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date LIMIT ?",
                     (code.zfill(6), date, n)).fetchall()
    return [(d, float(c), (float(ch) if ch is not None else None)) for d, c, ch in r]

with open(os.path.join(ROOT, 'reports', 'drift_check_20260918a_0917end', 'ml_signal_trades_full.csv'), encoding='utf-8-sig') as f:
    rd = list(csv.DictReader(f))

A, B = [], []   # (year, ret_pct) ret=卖出/买入-1
giveup = 0; miss = 0
for t in rd:
    d0 = t['买入日期'].replace('-', ''); d1 = t['卖出日期'].replace('-', '')
    code = t['股票代码']; sell = float(t['卖出价格'])
    seg = px_from(code, d0, 6)
    if not seg or seg[0][0] != d0 or seg[0][2] is None:
        miss += 1; continue
    c0 = seg[0][1]
    A.append((d0[:4], (sell/c0 - 1) * 100))
    # B: 找首个收阳日(change_pct>0)
    buy_day = None
    for d, c, ch in seg[:4]:
        if ch is not None and ch > 0:
            buy_day = (d, c); break
    if buy_day is None or buy_day[0] >= d1:
        giveup += 1; continue
    B.append((buy_day[0][:4], (sell/buy_day[1] - 1) * 100))

def stat(rs, cost):
    rets = [(y, r - cost*100) for y, r in rs]
    n = len(rets); wins = sum(1 for _, r in rets if r > 0)
    byy = defaultdict(lambda: [0, 0])
    for y, r in rets:
        byy[y][0] += 1; byy[y][1] += (r > 0)
    return {'n': n, 'win%': round(100*wins/n, 1), 'mean%': round(statistics.mean(r for _, r in rets), 2),
            'median%': round(statistics.median(r for _, r in rets), 2),
            'sum_mean_per_trade': round(statistics.mean(r for _, r in rets), 2),
            '分年win%': {k: round(100*v[1]/v[0], 1) for k, v in sorted(byy.items())}}

OUT = {'原始n': len(rd), '缺行情': miss, 'B放弃笔': giveup,
       'A_原日收盘价成交_含成本': stat(A, COST), 'A_零成本': stat(A, 0),
       'B_收阴顺延≤3日_含成本': stat(B, COST), 'B_零成本': stat(B, 0)}
# 差值与分年方向
wa = OUT['A_原日收盘价成交_含成本']['win%']; wb = OUT['B_收阴顺延≤3日_含成本']['win%']
fa = OUT['A_原日收盘价成交_含成本']['分年win%']; fb = OUT['B_收阴顺延≤3日_含成本']['分年win%']
OUT['胜率差_B-A(pp)'] = round(wb - wa, 1)
OUT['分年方向_B>A年数'] = f"{sum(1 for y in fb if y in fa and fb[y] > fa[y])}/{len(set(fa)&set(fb))}"
qconn.close()

json.dump(OUT, open(os.path.join(ROOT, 'data', 'ml_shade_delay_sim_20260918.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps(OUT, ensure_ascii=False, indent=1))
