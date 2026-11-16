# -*- coding: utf-8 -*-
"""从轻量面板重算相似分回测聚合(面板已落盘, 秒级) + 修NW与groupby两bug."""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np

panel = pd.read_pickle(r'D:\zcodesj\data\_lowrev_lite_panel_20260909.pkl')
print('面板', panel.shape, '日期范围', panel['date'].min(), '~', panel['date'].max())

# 事件覆盖修复: chan月key太严(仅当月有事件的股票入池, 每截面池均规模偏小)
# 补口径: chan事件=触发后60个日历日有效 -> (code, YYYYMM±1月)均算覆盖
cs = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\chan_signals_v2.pkl')
cs['date'] = cs['date'].astype(str)
ym = cs['date'].str[:6]
def ym_shift(s, k):
    y, m = int(s[:4]), int(s[4:])
    m += k
    if m > 12: y, m = y + 1, m - 12
    if m < 1: y, m = y - 1, m + 12
    return f'{y}{m:02d}'
keyset = set()
for s, c in zip(ym, cs['code']):
    keyset.add(f'{c}_{s}')
    keyset.add(f'{c}_{ym_shift(s, 1)}')
    keyset.add(f'{c}_{ym_shift(s, -1)}')
panel['chan_w'] = [f'{c}_{d[:6]}' in keyset for c, d in zip(panel['code'], panel['date'])]
print('chan宽口径覆盖行:', int(panel['chan_w'].sum()))

for ev_col, tag in [('chan', '严口径(当月)'), ('chan_w', '宽口径(±1月)')]:
    res_rows = []
    quint_rows = []
    for t in sorted(panel['date'].unique()):
        snap = panel[panel['date'] == t]
        pool = snap[(snap['pos250'] <= 0.40) & (snap[ev_col] == 1) & (snap['ep'] > 0)].dropna(subset=['fwd'])
        if len(pool) < 30:
            continue
        pool = pool.copy()
        pool['s_low'] = (1 - pool['pos250'] / 0.40).clip(0, 1)
        pool['s_far'] = ((10 - pool['ret60']) / 25).clip(0, 1)
        pool['s_epi'] = (np.log1p(pool['roe'].clip(lower=0)) / np.log1p(8)).clip(0, 1)
        pool['s_vr'] = ((1.2 - pool['vr']) / 0.8).clip(0, 1)
        pool['s_calm'] = ((5 - pool['ret20'].abs()) / 5).clip(0, 1)
        pool['score'] = (pool['s_low']*30 + pool['s_far']*20 + pool['s_epi']*20 +
                         pool['s_vr'].fillna(0.5)*20 + pool['s_calm']*10)
        res_rows.append({'date': t, 'n': len(pool), 'pool': pool['fwd'].mean(),
                         'top': pool.nlargest(20, 'score')['fwd'].mean(),
                         'bot': pool.nsmallest(20, 'score')['fwd'].mean()})
        for comp in ['s_low', 's_far', 's_epi', 's_vr', 's_calm']:
            try:
                qs = pd.qcut(pool[comp].rank(method='first'), 5, labels=False)
                quint_rows.append({'date': t, 'comp': comp,
                                   **{f'q{i+1}': float(pool.loc[qs == i, 'fwd'].mean()) for i in range(5)}})
            except Exception:
                pass
    r = pd.DataFrame(res_rows)
    r['excess'] = r['top'] - r['pool']
    r['bot_ex'] = r['bot'] - r['pool']

    def nwt(x, lag=3):
        x = np.asarray(pd.Series(x).dropna(), dtype=float)
        n = len(x)
        if n < 5 or np.allclose(x, x[0]):
            return round(float(x.mean()), 3), np.nan
        mu = x.mean(); g = x - mu
        nw = np.dot(g, g) / n
        for k in range(1, lag + 1):
            if len(g) > k:
                nw += 2 * np.dot(g[:-k], g[k:]) / n
        se = np.sqrt(max(nw, 1e-12) / n)
        return round(float(mu), 3), round(float(mu / se), 2)

    m_e, t_e = nwt(r['excess'])
    m_b, t_b = nwt(r['bot_ex'])
    agg = {'截面数': len(r), '池均规模': int(r['n'].mean()),
           '池等权均值%': round(r['pool'].mean(), 2), 'Top20均值%': round(r['top'].mean(), 2),
           'Bot20均值%': round(r['bot'].mean(), 2),
           'Top超额均值%': m_e, 'Top超额NW_t': t_e, 'Bot超额均值%': m_b, 'Bot超额NW_t': t_b,
           '超额正占比%': round((r['excess'] > 0).mean() * 100, 1)}
    print(f'\n===== [{tag}] 全期聚合 =====')
    print(json.dumps(agg, ensure_ascii=False))
    yr = {}
    for y in sorted({d[:4] for d in r['date']}):
        sub = r[r['date'].str.startswith(y)]
        m_y, t_y = nwt(sub['excess'])
        yr[y] = {'n': len(sub), 'Top%': round(sub['top'].mean(), 2), '池%': round(sub['pool'].mean(), 2),
                 '超额%': m_y, 't': t_y}
    print('分年:', json.dumps(yr, ensure_ascii=False))

    q = pd.DataFrame(quint_rows)
    quint = q.groupby('comp').mean(numeric_only=True).round(3)
    quint = quint[[c for c in ['q1', 'q2', 'q3', 'q4', 'q5'] if c in quint.columns]]
    print(f'===== [{tag}] 分量五分位 fwd20 (Q1=最低→Q5=最高) =====')
    print(quint.to_string())
    if ev_col == 'chan_w':
        json.dump({'聚合_宽口径': agg, '分年_宽口径': yr, '分量五分位_宽口径': json.loads(quint.to_json()),
                   '逐截面': r.assign(**{'pool': r['pool'].round(3), 'top': r['top'].round(3),
                                          'bot': r['bot'].round(3), 'excess': r['excess'].round(3)}).to_dict('records')},
                  open(r'D:\zcodesj\data\lowrev_similar_score_backtest_20260909.json', 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1, default=str)
        print('\n落盘 data/lowrev_similar_score_backtest_20260909.json')
