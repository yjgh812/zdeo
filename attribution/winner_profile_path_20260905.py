# -*- coding: utf-8 -*-
"""线索②持有期错配反事实: 每笔买入的持有路径 ret_k (k=1..20bar) vs 同期全市场等权路径.
分组: 活性复合分位/换手分位; 批次分层(线索①). 只读, 描述性, 路径右端受9/4截断."""
import sys, json, os
import numpy as np
import pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

OUT = r'D:\zcodesj\attribution\winner_profile_out'
panel = pd.read_pickle(os.path.join(OUT, 'panel.pkl'))
res = json.load(open(os.path.join(OUT, 'winner_profile_result.json'), encoding='utf-8'))
buys = [json.loads(l) for l in open(r'D:\zcodesj\attribution\winner_profile_buys_20260905.jsonl', encoding='utf-8')]
feat_by_code = {}
for r in res['buys']:
    if r.get('ok') and r['code'] not in feat_by_code:
        feat_by_code[r['code']] = r

# 每票收盘序列(数组化, 供路径索引)
close_by_code = {}
bar_dates = {}
for code, g in panel.sort_values(['code','date']).groupby('code'):
    close_by_code[code] = g['close'].values
    bar_dates[code] = g['date'].values

# D0集合上的全市场等权路径(regime基准): 用全部可交易票(剔ST近似: 用截面全部, ST占比4%影响小, 标注)
all_dates = np.array(sorted(panel['date'].unique()))
date_idx = {d: i for i, d in enumerate(all_dates)}
close_mat_cache = {}
def mkt_path(D0, kmax):
    """全市场等权: 从D0(含)起k+1个收盘日的截面均值累计收益路径."""
    i0 = date_idx[D0]
    idxs = all_dates[i0:i0+kmax+1]
    if len(idxs) < 2: return None
    key = (D0, len(idxs))
    if key in close_mat_cache: return close_mat_cache[key]
    sub = panel[panel['date'].isin(idxs)][['code','date','close']]
    piv = sub.pivot_table(index='date', columns='code', values='close')
    base = piv.iloc[0]
    path = (piv.div(base, axis=1).mean(axis=1) - 1).values * 100  # 等权累计%
    close_mat_cache[key] = path
    return path

KS = [1, 2, 3, 5, 8, 10, 13]
rows = []
for code, grp in pd.DataFrame(buys).groupby('code'):
    f = feat_by_code.get(code)
    if not f: continue
    D0 = grp['date'].min()
    c = close_by_code.get(code); dt = bar_dates.get(code)
    if c is None: continue
    j0 = int(np.where(dt == D0)[0][0])
    mp = mkt_path(D0, max(KS))
    rec = {'code': code, 'name': f.get('name2') or grp['name'].iloc[0], 'D0': D0,
           'seg': 'jul' if D0 < '20260801' else ('aug' if D0 < '20260901' else 'sep'),
           'batch': 'B0817_19' if '20260817' <= D0 <= '20260819' else ('other_aug' if D0 < '20260901' else 'sep'),
           'act': np.nanmean([f.get('p_turnover20'), f.get('p_vol60'), f.get('p_lim_cnt250')]),
           'p_turn': f.get('p_turnover20'), 'p_pos': f.get('p_pos250')}
    for k in KS:
        if j0 + k < len(c):
            r = (c[j0+k]/c[j0]-1)*100
            m = mp[k] if (mp is not None and k < len(mp)) else np.nan
            rec[f'ret_{k}'] = r; rec[f'ex_{k}'] = r - m
        else:
            rec[f'ret_{k}'] = np.nan; rec[f'ex_{k}'] = np.nan
    rows.append(rec)
df = pd.DataFrame(rows)
print(f'票级 n={len(df)}; k=2可得={df.ret_2.notna().sum()} k=5={df.ret_5.notna().sum()} k=10={df.ret_10.notna().sum()} k=13={df.ret_13.notna().sum()}')

def line(g, tag):
    out = f"{tag:<26}"
    for k in KS:
        v = g[f'ex_{k}']
        out += f" | k={k}:{v.median():+6.2f}%" if v.notna().sum() >= 5 else f" | k={k}:  n<5"
    return out

main = df[df.seg == 'aug']
print('\n== 8月票: 超额路径中位(相对同期全市场等权, pp) ==')
print(line(main, '全体8月票'))
med_act = main.act.median(); med_t = main.p_turn.median()
print(line(main[main.act >= med_act], f'高活性(>={med_act:.0f}分位)'))
print(line(main[main.act < med_act], f'低活性(<{med_act:.0f}分位)'))
print(line(main[main.p_turn >= med_t], f'高换手(>={med_t:.0f}分位)'))
print(line(main[main.p_turn < med_t], f'低换手(<{med_t:.0f}分位)'))
print('\n== 批次分层(线索①) ==')
print(line(df[df.batch == 'B0817_19'], '8/17-19高位批次'))
print(line(df[df.batch == 'other_aug'], '其他8月批次'))
print(line(df[df.seg == 'jul'], '7月红利票(参照)'))
print(line(df[df.seg == 'sep'], '9月票(参照,截断)'))

# 右尾检验: 8月高活性组 k=2 vs k=8 的分布(超额)
print('\n== 高活性8月票: 持有2bar vs 8bar 超额分布(右尾是否被截断) ==')
hi = main[main.act >= med_act]
for k in [2, 5, 8]:
    v = hi[f'ex_{k}'].dropna()
    if len(v) >= 5:
        print(f"  k={k}: n={len(v)} 中位{v.median():+.2f}% 均值{v.mean():+.2f}% P25{v.quantile(.25):+.2f}% P75{v.quantile(.75):+.2f}% >0占比{(v>0).mean()*100:.0f}%")
lo = main[main.act < med_act]
print('  --对照 低活性8月票:')
for k in [2, 5, 8]:
    v = lo[f'ex_{k}'].dropna()
    if len(v) >= 5:
        print(f"  k={k}: n={len(v)} 中位{v.median():+.2f}% 均值{v.mean():+.2f}% P75{v.quantile(.75):+.2f}% >0占比{(v>0).mean()*100:.0f}%")

df.to_csv(os.path.join(OUT, 'buys_hold_paths.csv'), index=False, encoding='utf-8-sig')
print('\nsaved buys_hold_paths.csv')
