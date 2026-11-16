# -*- coding: utf-8 -*-
"""组合C 10只合格名单·画像胜率证据排名(2026-09-06 一次性研究脚本, 只读)."""
import sys, json, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd, numpy as np

m = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v5_full.pkl')
last = m['date'].max()
print('panel截面:', last, '总行数:', len(m))
cs = m[m['date'] == last].set_index('code')
codes = ['001390', '002152', '002478', '002481', '002777', '300296', '300773', '301370', '600626', '600987']

FAM = {'chan_buy2_10': '缠论二买', 'chan_buy1_10': '缠论一买', 'island_5': '岛型反转', 'gap_up_hold': '跳空未回补'}
RISK12 = ['lhb_buy_5', 'limit_break_5', 'new_high_3', 'vwap60_bias', 'vwap20_bias', 'gap_up_run_5',
          'chan_buy3_10', 'new_high20', 'macd_golden_hi', 'rounded_20', 'red_3_soldiers', 'higher_low']

print('\n== 四族正信号历史20日分布(全panel史, 剔fwd缺失) ==')
hstats = {}
for col, name in FAM.items():
    h = m[(m[col] > 0.5) & m['fwd20_close'].notna()]['fwd20_close']
    hstats[col] = dict(n=len(h), win=round((h > 0).mean() * 100, 1), med=round(h.median() * 100, 2),
                       mean=round(h.mean() * 100, 2), p90=round(h.quantile(.9) * 100, 1),
                       crash=round((h <= -0.15).mean() * 100, 1))
    print(f"  {name:8s} n={len(h):6d} 胜率{(h > 0).mean() * 100:5.1f}% 中位{h.median() * 100:+.2f}% "
          f"均值{h.mean() * 100:+.2f}% P90{h.quantile(.9) * 100:+.1f}% 暴跌率(≤-15%){(h <= -0.15).mean() * 100:.1f}%")

con = sqlite3.connect(r'file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro', uri=True)
ind = pd.read_sql_query('SELECT code,industry FROM industry_map_ths', con)
nm = dict(con.execute('SELECT code,name FROM industry_map_ths').fetchall())
con.close()
ind['L1'] = ind['industry'].fillna('未知').str.split('-').str[0]
mm = m.sort_values(['code', 'date']).copy()
mm['chg'] = mm.groupby('code')['close'].pct_change() * 100
o = mm[mm['date'] == last][['code', 'chg']].merge(ind[['code', 'L1']].drop_duplicates('code'), on='code', how='left')
limcnt = o[o['chg'] >= 9.8].groupby('L1').size()
stock_l1 = dict(zip(ind['code'], ind['L1']))

print('\n== 10只合格名单证据表(截面%s) ==' % last)
rows = []
for code in codes:
    r = cs.loc[code]
    if isinstance(r, pd.DataFrame):
        r = r.iloc[0]
    fired = [(FAM[c], hstats[c]) for c in FAM if pd.notna(r.get(c)) and r[c] > 0.5]
    best = max(fired, key=lambda x: x[1]['win']) if fired else None
    risk = int(sum(1 for f in RISK12 if pd.notna(r.get(f)) and r[f] > 0.5))
    l1 = stock_l1.get(code, '未知')
    rows.append(dict(code=code, name=nm.get(code, ''), ind=l1,
                     indlim=int(limcnt.get(l1, 0)),
                     close=round(float(r['close']), 2),
                     pos250=round(float(r.get('pos250', np.nan)) * 100) if pd.notna(r.get('pos250')) else None,
                     roe=round(float(r.get('pit_roe')), 1) if pd.notna(r.get('pit_roe')) else None,
                     fired=[f[0] for f in fired], fam_win=best[1]['win'] if best else None,
                     fam_med=best[1]['med'] if best else None, fam_p90=best[1]['p90'] if best else None,
                     fam_crash=best[1]['crash'] if best else None, fam_n=best[1]['n'] if best else None,
                     risk=risk))
df = pd.DataFrame(rows)
df = df.sort_values(['fam_win', 'indlim', 'risk'], ascending=[False, False, True]).reset_index(drop=True)
df.index += 1
print(df.to_string())
df.to_json(r'D:\zcodesj\data\_wr_rank_combo_c_20260906.json', orient='records', force_ascii=False, indent=1)
print('\n已存 data/_wr_rank_combo_c_20260906.json')
