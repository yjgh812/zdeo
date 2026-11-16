# -*- coding: utf-8 -*-
"""画像跟进: 118笔买入的票级实际持有收益 vs 事前特征分位关联(只读复盘).
收益口径=qfq区间收益 close(D_end)/close(D0)-1, D0=首笔买入日, D_end=清仓日或20260904."""
import sys, json, os
import numpy as np
import pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

OUT = r'D:\zcodesj\attribution\winner_profile_out'
panel = pd.read_pickle(os.path.join(OUT, 'panel.pkl'))
res = json.load(open(os.path.join(OUT, 'winner_profile_result.json'), encoding='utf-8'))
buys = [json.loads(l) for l in open(r'D:\zcodesj\attribution\winner_profile_buys_20260905.jsonl', encoding='utf-8')]
sells = [json.loads(l) for l in open(r'D:\zcodesj\attribution\winner_profile_sells_20260905.jsonl', encoding='utf-8')]
feat_by_code = {}  # code -> 首笔买入的特征行
for r in res['buys']:
    if r.get('ok') and r['code'] not in feat_by_code:
        feat_by_code[r['code']] = r

LAST = '20260904'
px = panel.set_index(['code','date'])['close']

rows = []
for code, grp in pd.DataFrame(buys).groupby('code'):
    f = feat_by_code.get(code)
    if not f: continue
    D0 = grp['date'].min()
    bq = grp['qty'].sum()
    sl = [s for s in sells if s['code']==code and s['date'] > D0]
    sq = sum(s['qty'] for s in sl); last_sell = max((s['date'] for s in sl), default=None)
    closed = sq >= bq * 0.95
    D_end = last_sell if (closed and last_sell) else LAST
    try:
        p0 = px.loc[(code, D0)]; p1 = px.loc[(code, D_end)]
    except KeyError:
        continue
    hold_bars = int(((panel['code']==code) & (panel['date']>D0) & (panel['date']<=D_end)).sum())
    rows.append({'code': code, 'name': f.get('name2') or grp['name'].iloc[0], 'L1': f['L1'],
                 'D0': D0, 'D_end': D_end, 'closed': closed, 'hold_bars': hold_bars,
                 'ret': (p1/p0-1)*100,
                 'seg': 'jul' if D0 < '20260801' else ('aug' if D0 < '20260901' else 'sep'),
                 'p_turn': f.get('p_turnover20'), 'p_mv': f.get('p_log_mv'), 'p_pos': f.get('p_pos250'),
                 'p_vol': f.get('p_vol60'), 'p_lim': f.get('p_lim_cnt250'), 'p_pe': f.get('p_pe_ttm_pos'),
                 'p_dif': f.get('p_dif'),
                 'turnover20': f.get('turnover20'), 'lim250': f.get('lim_cnt250'), 'pos250': f.get('pos250')})
df = pd.DataFrame(rows)
df['act_comp'] = df[['p_turn','p_vol','p_lim']].mean(axis=1)  # 活性复合分位
print(f'票级样本 n={len(df)} 清仓={df.closed.sum()} 持有bars中位={df.hold_bars.median():.0f}')

def gstat(g):
    return f"n={len(g):2d} 中位{g.ret.median():+6.2f}% 均值{g.ret.mean():+6.2f}% 胜率{(g.ret>0).mean()*100:4.0f}% 持有{g.hold_bars.median():.0f}bar"

def split_report(sub, col, lab_hi, lab_lo):
    if sub[col].notna().sum() < 8: return
    med = sub[col].median()
    hi, lo = sub[sub[col]>=med], sub[sub[col]<med]
    print(f"  [{col}] 以中位分位{med:.0f}分组: 高({lab_hi}) {gstat(hi)} | 低({lab_lo}) {gstat(lo)} | 差 {hi.ret.median()-lo.ret.median():+.2f}pp")

main = df[df.seg=='aug']  # 主样本: 8月票(持有充分)
print(f'\n== 主样本=8月票 n={len(main)} ==')
print(f"全体: {gstat(main)}")
split_report(main, 'p_turn', '高换手', '低换手')
split_report(main, 'p_pos', '年位置高', '年位置低')
split_report(main, 'act_comp', '高活性', '低活性')
split_report(main, 'p_pe', '高PE', '低PE')
split_report(main, 'p_mv', '大市值', '小市值')
bank = main[main.L1=='银行']; nonbank = main[main.L1!='银行']
if len(bank)>=3: print(f"  [行业] 银行 {gstat(bank)} | 非银行 {gstat(nonbank)}")
print(f"\n== 7月旧红利票 n={len(df[df.seg=='jul'])} =="); print(gstat(df[df.seg=='jul']))
print(f"\n== 9月票(持有短,仅观察) n={len(df[df.seg=='sep'])} =="); print(gstat(df[df.seg=='sep']))

print('\n== 8月票极端组: 收益top5 / bottom5 ==')
for _, r in main.nlargest(5, 'ret').iterrows():
    print(f"  TOP {r['code']} {r['name']}({r.L1}) {r.ret:+.1f}% 持{r.hold_bars}bar 换手分位{r.p_turn:.0f} 位置分位{r.p_pos:.0f} 活性{r.act_comp:.0f} PE分位{r.p_pe or -1:.0f}")
for _, r in main.nsmallest(5, 'ret').iterrows():
    print(f"  BOT {r['code']} {r['name']}({r.L1}) {r.ret:+.1f}% 持{r.hold_bars}bar 换手分位{r.p_turn:.0f} 位置分位{r.p_pos:.0f} 活性{r.act_comp:.0f} PE分位{r.p_pe or -1:.0f}")

df.to_csv(os.path.join(OUT, 'buys_realized_returns.csv'), index=False, encoding='utf-8-sig')
json.dump({'main_aug': {'n': len(main), 'ret_med': float(main.ret.median()), 'win': float((main.ret>0).mean()*100)}},
          open(os.path.join(OUT, 'followup_summary.json'), 'w', encoding='utf-8'))
print('\nsaved buys_realized_returns.csv')
