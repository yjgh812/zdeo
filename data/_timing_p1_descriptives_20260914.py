# -*- coding: utf-8 -*-
"""P1积木一次性描述统计(纯描述,零判据,零生产写入):
A. 月度等权收益本地复算(qfq全池, 覆盖段) → 月度胜率/均值表
B. ERP五年分位(erp_history.db自带erp_pct_5y) × 下月全池等权收益 五分位描述表
输出: data/_timing_p1_descriptives_20260914.json + stdout
"""
import json
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.stdout.reconfigure(encoding='utf-8')

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
ERP = r'D:\zcodesj\erp_history.db'
OUT = r'D:\zcodesj\data\_timing_p1_descriptives_20260914.json'

con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
qmin, qmax = con.execute("SELECT MIN(date),MAX(date) FROM stock_daily_meta_qfq").fetchone()
print(f'[qfq] 覆盖 {qmin}~{qmax}')

ec = sqlite3.connect(f'file:{ERP}?mode=ro', uri=True)
anchors = [str(r[0]) for r in ec.execute("SELECT date FROM erp_daily WHERE date>=? ORDER BY date", (qmin,)).fetchall()]
pcts = {str(k): v for k, v in ec.execute("SELECT date,erp_pct_5y FROM erp_daily WHERE date>=? ORDER BY date", (qmin,)).fetchall()}
ec.close()
print(f'[erp] 锚点{len(anchors)}个 {anchors[0]}~{anchors[-1]} (月度稀疏口径,erp_pct_5y现成)')


def eq_ret(d0: str, d1: str):
    """全池等权 d0→d1 收益%(同 engineA_shadow_eval 口径), 失败返None."""
    try:
        v = con.execute(
            "SELECT AVG(close/(SELECT close FROM stock_daily_meta_qfq AS t2 "
            "WHERE t2.code=t1.code AND t2.date=?)-1)*100 FROM stock_daily_meta_qfq AS t1 WHERE date=?",
            (d0, d1)).fetchone()[0]
        return float(v) if v is not None else None
    except Exception:
        return None


# ---- 月度收益序列(相邻锚点) ----
monthly = []  # (anchor_date, next_date, ret%, erp_pct)
for i in range(len(anchors) - 1):
    d0, d1 = anchors[i], anchors[i + 1]
    r = eq_ret(d0, d1)
    if r is None:
        continue
    monthly.append({'anchor': d0, 'next': d1, 'ret': round(r, 3),
                    'erp_pct': pcts.get(d0)})
print(f'[monthly] 有效月度收益{len(monthly)}段')

# A. 自然月胜率表(锚点=月末 → 锚点月m的收益归属m+1月)
by_m = defaultdict(list)
for m in monthly:
    m_anchor = int(m['anchor'][4:6])
    by_m[m_anchor % 12 + 1].append(m['ret'])
cal = {}
for m in range(1, 13):
    xs = by_m.get(m, [])
    cal[f'{m:02d}月'] = {'n': len(xs), 'win_rate': round(float(np.mean(np.array(xs) > 0)), 3) if xs else None,
                         'mean': round(float(np.mean(xs)), 3) if xs else None}
res_a = {'note': 'qfq全池等权月度收益(锚点=erp_daily月末锚,收益归属次月), 纯描述统计, 禁直接仓位联动; 涉持仓时间型退出抵触9/7裁决方向',
         'months': cal}

# B. ERP分位×下月收益 五分位描述
valid = [m for m in monthly if m['erp_pct'] is not None]
qs = np.quantile([m['erp_pct'] for m in valid], [0.2, 0.4, 0.6, 0.8])
buckets = defaultdict(list)
for m in valid:
    b = int(np.searchsorted(qs, m['erp_pct']))
    buckets[f'Q{b + 1}'].append(m['ret'])
res_b = {'note': 'erp_pct_5y五分位 × 当月全池等权收益(相邻锚点段), 纯描述非判据; ERP tilt升级仍待IC择时黑名单边界裁决',
         'quantile_edges': [round(float(q), 3) for q in qs],
         'buckets': {k: {'n': len(v), 'mean': round(float(np.mean(v)), 3),
                         'win_rate': round(float(np.mean(np.array(v) > 0)), 3)}
                     for k, v in sorted(buckets.items())}}
con.close()

out = {'A_monthly_calendar': res_a, 'B_erp_quintile': res_b,
       'n_monthly': len(monthly), 'window': [anchors[0], anchors[-1]]}
json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\n== A 月度胜率(全池等权) ==')
for k, v in cal.items():
    print(f"  {k}: n={v['n']:>3} 胜率={v['win_rate']} 均值={v['mean']}%")
print('== B ERP五年分位五桶 × 当月收益 ==')
for k, v in res_b['buckets'].items():
    print(f"  {k}: n={v['n']:>3} 均值={v['mean']}% 胜率={v['win_rate']}")
print(f'结果已写: {OUT}')
