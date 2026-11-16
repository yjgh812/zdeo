# -*- coding: utf-8 -*-
"""BAYA-METHOD-REPLAY 2006-2026: 选中百亚的完整方法链 20 年真实数据回放(只读研究件, 20260915)

冻结口径(运行前锁定):
  域 = 每月首个交易日T0, PIT库ep(EPS, pit_avail_date<=T0最新一期)全市场降序Top20(盈利质量域)
  R  = pos250<=45 ∧ 距年高<=-30% ∧ ret60<=0 ∧ ret20>=-12 ∧ vol20<=4 (250bar窗口, T0收盘)
  地量 = amt_ratio = 20日均额/250bar内20日均额峰值 < 0.35
  门 = 上证指数 pos250@T0 < 67
  宇宙 = qfq全池, 剔688/4/8/92开头, T0收盘<=30元
  评估 = 域全组/R组/F组(域∧R∧地量∧门开)/全市场等权, fwd20/40/60交易日, T0收盘入场
已知局限(如实): ①域为ep单因子近似, 非生产bp+ep合成(bp语义争议另报); ②无历史ST状态过滤;
  ③入场=T0收盘非次日开盘; ④PIT是法定披露滞后口径, 无提前泄漏。
输出: data/_baya_method_replay_2006_result.json + 控制台分年表
"""
import sys, os, csv, sqlite3, json, statistics
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
PIT = r'D:\zcodesj\factor_pit.db'
IDX = r'D:\zcodesj\data\_sh_index_daily.csv'
OUT = r'D:\zcodesj\data\_baya_method_replay_2006_result.json'

print('加载qfq(2003起)...', flush=True)
qfq = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
df = pd.read_sql_query("SELECT code, date, close, high, amount FROM stock_daily_meta_qfq "
                       "WHERE date>='20030101' ORDER BY code, date", qfq)
qfq.close()
df['code'] = df['code'].astype(str)
df = df[~df['code'].str.startswith(('688', '4', '8', '92'))]
print(f'行数 {len(df)}, 股票 {df.code.nunique()}', flush=True)

g = df.groupby('code', sort=False)
c = g['close']; h = g['high']; a = g['amount']
df['ret'] = g['close'].pct_change()
df['pos250'] = (df['close'] - c.transform(lambda s: s.rolling(250).min())) / \
               (h.transform(lambda s: s.rolling(250).max()) - c.transform(lambda s: s.rolling(250).min())) * 100
df['dist_hi'] = (df['close'] / h.transform(lambda s: s.rolling(250).max()) - 1) * 100
df['ret20'] = df['close'] / g['close'].shift(20) - 1
df['ret60'] = df['close'] / g['close'].shift(60) - 1
df['vol20'] = g['ret'].transform(lambda s: s.rolling(20).std(ddof=1)) * 100
amt20 = a.transform(lambda s: s.rolling(20).mean())
df['amt_ratio'] = amt20 / amt20.groupby(df['code']).transform(lambda s: s.rolling(250).max())
df['R'] = ((df['pos250'] <= 45) & (df['dist_hi'] <= -30) & (df['ret60'] <= 0) &
           (df['ret20'] >= -12) & (df['vol20'] <= 4))
df['desert'] = df['amt_ratio'] < 0.35
print('滚动特征完成', flush=True)

# 月度T0 = 每月首个交易日
df['ym'] = df['date'].str[:6]
t0s = df.groupby('ym')['date'].min()
t0s = t0s[t0s.index >= '200601']
print(f'月度T0: {len(t0s)} 期 ({t0s.index[0]}~{t0s.index[-1]})', flush=True)

# 市场门: 上证 pos250@T0
idx = [(r[0].replace('-', ''), float(r[1])) for r in list(csv.reader(open(IDX, encoding='utf-8')))[1:] if r and r[0] != 'date']
idx_d = [x[0] for x in idx]; idx_c = [x[1] for x in idx]
def mkt_pos(d):
    i = idx_d.index(d) if d in idx_d else -1
    if i < 249: return None
    seg = idx_c[i-249:i+1]
    lo, hi = min(seg), max(seg)
    return (seg[-1]-lo)/(hi-lo)*100 if hi > lo else None
gate = {d: mkt_pos(d) for d in t0s.values}

# PIT ep as-of
print('加载PIT...', flush=True)
pc = sqlite3.connect(f'file:{PIT}?mode=ro', uri=True)
pit = pd.read_sql_query('SELECT code, pit_avail_date, ep, report_date FROM stock_factors_fundamental_pit_v2 '
                        'WHERE ep IS NOT NULL', pc)
pc.close()
pit['code'] = pit['code'].astype(str)
pit = pit.sort_values(['code', 'pit_avail_date', 'report_date']).drop_duplicates(['code', 'pit_avail_date'], keep='last')
pit = pit[['code', 'pit_avail_date', 'ep']].rename(columns={'pit_avail_date': 'avail'})
pit['avail'] = pit['avail'].astype(int)

panel = df[df['date'].isin(set(t0s.values))][['code', 'date', 'close', 'pos250', 'dist_hi',
                                              'ret20', 'ret60', 'vol20', 'amt_ratio', 'R', 'desert']].copy()
panel = panel[(panel['close'] <= 30) & panel['pos250'].notna()]
panel = panel.rename(columns={'date': 't0'})
panel['t0i'] = panel['t0'].astype(int)
panel = panel.sort_values('t0i')
pit_s = pit.sort_values('avail')
merged = pd.merge_asof(panel, pit_s, left_on='t0i', right_on='avail', by='code')
merged = merged[merged['ep'].notna()]
print(f'panel: {len(merged)} 行 (T0×股票)', flush=True)

# 域: 每月ep降序Top20
merged['ep_rank'] = merged.groupby('t0')['ep'].rank(ascending=False, method='first')
merged['in_domain'] = merged['ep_rank'] <= 20
# 市场门
merged['gate_open'] = merged['t0'].map(lambda d: gate.get(d) is not None and gate[d] < 67)
merged['F'] = merged['in_domain'] & merged['R'] & merged['desert'] & merged['gate_open']
merged['Rm'] = merged['in_domain'] & merged['R']

# 前向收益: 全局交易日索引
alld = sorted(df['date'].unique())
dpos = {d: i for i, d in enumerate(alld)}
close_map = df.set_index(['code', 'date'])['close']
for nd in (20, 40, 60):
    def fwd(row):
        i = dpos.get(row['t0'])
        if i is None or i + nd >= len(alld):
            return None
        return close_map.get((row['code'], alld[i+nd]))
    merged[f'fwd{nd}'] = (merged['close'].values / merged.apply(fwd, axis=1).values - 1) * 100
print('前向收益完成', flush=True)

# 全市场等权基准 per t0
mkt_base = merged.groupby('t0')[[f'fwd{nd}' for nd in (20, 40, 60)]].mean()

def agg(sub, label, nd):
    v = sub[f'fwd{nd}'].dropna()
    if len(v) == 0:
        return None
    return dict(n=int(len(v)), mean=round(float(v.mean()), 2), median=round(float(v.median()), 2),
                win=round(float((v > 0).mean()*100), 1))

result = {'spec': 'BAYA-METHOD-REPLAY 2006-2026 (ep域Top20 × R∧地量∧门控)', 'overall': {}, 'yearly': []}
for lab, mask in [('domain_all', merged['in_domain']), ('R_group', merged['Rm']),
                  ('F_group', merged['F']), ('market', pd.Series(True, index=merged.index))]:
    sub = merged[mask]
    result['overall'][lab] = {f'fwd{nd}': agg(sub, lab, nd) for nd in (20, 40, 60)}
    print(f"\n[{lab}] n={len(sub)}: " + ' | '.join(
        f"fwd{nd} {result['overall'][lab][f'fwd{nd}']['mean']:+.2f}%/中位{result['overall'][lab][f'fwd{nd}']['median']:+.2f}%/胜率{result['overall'][lab][f'fwd{nd}']['win']:.0f}%"
        if result['overall'][lab][f'fwd{nd}'] else f"fwd{nd} 无" for nd in (20, 40, 60)))

merged['year'] = merged['t0'].str[:4]
for y, gy in merged.groupby('year'):
    row = {'year': y}
    for lab, col in [('domain_all', 'in_domain'), ('R_group', 'Rm'), ('F_group', 'F')]:
        sub = gy[gy[col]]
        v = sub['fwd20'].dropna()
        row[lab] = dict(n=int(len(v)), mean=round(float(v.mean()), 2)) if len(v) >= 3 else None
    vm = gy['fwd20'].dropna()
    row['market'] = dict(n=int(len(vm)), mean=round(float(vm.mean()), 2))
    result['yearly'].append(row)

json.dump(result, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'\n落盘 {OUT}', flush=True)
print('\n分年 fwd20 (域/R组/F组/全市场):')
for row in result['yearly']:
    def fmt(d):
        return f"{d['mean']:+6.2f}%({d['n']:>3d})" if d else '   无   '
    print(f"  {row['year']}: 域{fmt(row['domain_all'])} R{fmt(row['R_group'])} F{fmt(row['F_group'])} 市场{fmt(row['market'])}")
