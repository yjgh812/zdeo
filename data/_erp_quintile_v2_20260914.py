# -*- coding: utf-8 -*-
"""ERP分位×下月全池等权收益 描述统计v2(修复版).
v1不可用原因: erp_daily.erp_pct_5y 仅9/258有值; v2自算滚动60月分位(erp原始列258/258完整).
纯描述, 零判据, 零生产写入. 输出: data/_erp_quintile_v2_20260914.json
"""
import json
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
ERP = r'D:\zcodesj\erp_history.db'
OUT = r'D:\zcodesj\data\_erp_quintile_v2_20260914.json'

ec = sqlite3.connect(f'file:{ERP}?mode=ro', uri=True)
e = pd.read_sql_query("SELECT date, erp FROM erp_daily ORDER BY date", ec)
ec.close()
e['date'] = e['date'].astype(str)
e['erp_pct60m'] = e['erp'].rolling(60, min_periods=60).apply(lambda w: float((w[-1] >= w).mean()) * 100, raw=True)
print(f"[erp] {len(e)}点 {e['date'].iloc[0]}~{e['date'].iloc[-1]}, 分位有效{e['erp_pct60m'].notna().sum()}个(60月滚动)")

con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)


def eq_ret(d0: str, d1: str):
    try:
        v = con.execute(
            "SELECT AVG(close/(SELECT close FROM stock_daily_meta_qfq AS t2 "
            "WHERE t2.code=t1.code AND t2.date=?)-1)*100 FROM stock_daily_meta_qfq AS t1 WHERE date=?",
            (d0, d1)).fetchone()[0]
        return float(v) if v is not None else None
    except Exception:
        return None


anchors = e['date'].tolist()
rows = []
for i in range(len(anchors) - 1):
    d0, d1 = anchors[i], anchors[i + 1]
    pct = e['erp_pct60m'].iloc[i]
    if np.isnan(pct):
        continue
    r = eq_ret(d0, d1)
    if r is None:
        continue
    rows.append({'anchor': d0, 'ret': r, 'pct': float(pct)})
con.close()
print(f"[merge] 有效(分位×下月收益){len(rows)}段")

qs = np.quantile([x['pct'] for x in rows], [0.2, 0.4, 0.6, 0.8])
buckets = defaultdict(list)
for x in rows:
    buckets[f'Q{int(np.searchsorted(qs, x["pct"])) + 1}'].append(x['ret'])
res = {'note': 'ERP=1/PE−十债(erp_history本地口径), 60月滚动分位自算(erp_pct_5y字段已坏仅9/258), '
               '下月收益=qfq全池等权相邻锚点; 纯描述非判据, ERP tilt是否越IC择时黑名单仍待主人裁决',
       'n_valid': len(rows), 'quantile_edges_pct': [round(float(q), 1) for q in qs],
       'quintiles': {k: {'n': len(v), 'mean': round(float(np.mean(v)), 2),
                         'median': round(float(np.median(v)), 2),
                         'win_rate': round(float(np.mean(np.array(v) > 0)), 2)}
                     for k, v in sorted(buckets.items())}}
# 极值描述: 分位>=80 vs <=20
hi = [x['ret'] for x in rows if x['pct'] >= 80]
lo = [x['ret'] for x in rows if x['pct'] <= 20]
res['extremes'] = {'pct>=80': {'n': len(hi), 'mean': round(float(np.mean(hi)), 2)} if hi else None,
                   'pct<=20': {'n': len(lo), 'mean': round(float(np.mean(lo)), 2)} if lo else None}
json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('\n== ERP 60月分位五桶 × 下月全池等权收益 ==')
for k, v in res['quintiles'].items():
    print(f"  {k}: n={v['n']:>3} 均值={v['mean']}% 中位={v['median']}% 胜率={v['win_rate']}")
print('极值:', res['extremes'])
print(f'结果已写: {OUT}')
