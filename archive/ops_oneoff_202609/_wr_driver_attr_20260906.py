# -*- coding: utf-8 -*-
"""10只合格名单驱动信号族归因(窗口口径=combo_c_shadow配方): 只读."""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd, numpy as np

CACHE = r'D:\zcodesj\portrait_agent\cache'
codes = ['001390', '002152', '002478', '002481', '002777', '300296', '300773', '301370', '600626', '600987']

m = pd.read_pickle(CACHE + r'\panel_v5_full.pkl')
last = str(m['date'].max())
tdates = sorted({str(d) for d in m['date'].unique()})
win15 = set(tdates[-15:])
win5 = set(tdates[-5:])
print(f'截面{last} win15={tdates[-15:]}..{tdates[-1]}')

chan = pd.read_pickle(CACHE + r'\chan_signals_v2.pkl')
chan['date'] = chan['date'].astype(str)
cols = [c for c in ['island_bottom', 'island_5', 'gap_up_hold'] if c in m.columns]
print('panel岛型列:', cols)
pats = m[m['date'].astype(str).isin(win5)][['code'] + ([c for c in ['island_bottom'] if c in m.columns])]
island_win = set(pats[(pats['island_bottom'] > 0.5)]['code']) if 'island_bottom' in pats.columns else set()

res = {}
for code in codes:
    ch = chan[(chan['code'] == code) & (chan['date'].isin(win15))]
    res[code] = {'chan': sorted(set(ch['type'])), 'island': code in island_win}
    print(code, res[code])
json.dump(res, open(r'D:\zcodesj\data\_wr_driver_attr_20260906.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=str)
