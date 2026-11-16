# -*- coding: utf-8 -*-
"""校准台账+排序证据层 每日生成器 (呈报层·零交易权·直跑制定案, 不挂cron不接executor)
立项: 主人2026-11-09「继续」令批准; 证据=VP1-VP8登记(data/falsified_rules.json) + commit 67c85c6c
输出: data/calibration_ledger_latest.txt (盘前计划可引用段落)
用法: python analysis/calibration_ledger_daily.py [--codes 600519,000001] [--since 20260701]
fail-closed: 数据不足返回N/A, 禁伪值(反臆想框架)"""
import sqlite3, json, os, math, argparse
import numpy as np, pandas as pd
from scipy import stats

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
ST_DB = r'D:\zcodesj\data\st_history.db'
OUT_TXT = r'D:\zcodesj\data\calibration_ledger_latest.txt'

ap = argparse.ArgumentParser()
ap.add_argument('--codes', default='', help='候选代码逗号分隔(6位), 可选; 给出则输出排序overlay')
ap.add_argument('--since', default='20260701', help='数据窗口起点(默认近4个月, 覆盖60日回看)')
args = ap.parse_args()

def q(sql, db, **kw):
    con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    df = pd.read_sql(sql, con, **kw)
    con.close()
    return df

print('[1] 加载窗口数据...', flush=True)
px = q("SELECT code,date,close,volume,amount FROM stock_daily_meta_qfq WHERE date>='%s'" % args.since, QFQ_DB)
px = px[(px['volume'] > 0) & (px['close'] > 0)].copy()
px['date'] = px['date'].astype(str)
px = px[~px['code'].str[0].isin(['4', '8', '9'])]
px = px[~px['code'].str[:3].isin(['688', '689'])]
con = sqlite3.connect('file:' + FUND_DB + '?mode=ro', uri=True)
db1 = pd.read_sql("SELECT ts_code,trade_date,turnover_rate FROM daily_basic WHERE trade_date>='%s'" % args.since, con)
con.close()
db1['date'] = db1['trade_date'].astype(str)
db1['code'] = db1['ts_code'].str[:6]
db1['tr'] = pd.to_numeric(db1['turnover_rate'], errors='coerce')
db1 = db1[['code', 'date', 'tr']].dropna()
st = q("SELECT code,date FROM st_daily WHERE is_st=1", ST_DB).drop_duplicates()
st['date'] = st['date'].astype(str)
st['flag'] = 1
px = px.merge(st, on=['code', 'date'], how='left')
px = px[px['flag'].isna()].drop(columns=['flag'])
m = px.merge(db1, on=['code', 'date'], how='inner').sort_values(['code', 'date']).reset_index(drop=True)
con2 = sqlite3.connect('file:' + FUND_DB + '?mode=ro', uri=True)
imap = pd.read_sql("SELECT ts_code, industry_name FROM industry_map WHERE level='L1'", con2)
con2.close()
imap['code'] = imap['ts_code'].str[:6]
imap = imap[['code', 'industry_name']].drop_duplicates('code')
m = m.merge(imap, on='code', how='left')
m['amount'] = pd.to_numeric(m['amount'], errors='coerce')
print('  rows', len(m), 'codes', m['code'].nunique(), 'range', m['date'].min(), '~', m['date'].max(), flush=True)

g = m.groupby('code', observed=True)
m['dr'] = g['close'].transform(lambda s: s / s.shift(1) - 1.0)
lo60 = g['close'].transform(lambda s: s.rolling(60).min())
hi60 = g['close'].transform(lambda s: s.rolling(60).max())
m['pos'] = (m['close'] - lo60) / (hi60 - lo60 + 1e-12)
m['tr5'] = g['tr'].transform(lambda s: s.rolling(5).mean())
m['tr60'] = g['tr'].transform(lambda s: s.rolling(60).mean())
m['vr'] = m['tr5'] / (m['tr60'] + 1e-12)
m['hot'] = (m['tr'] > 1.5 * m['tr60']).astype(int)
m['uphot'] = ((m['dr'] > 0) & (m['hot'] == 1)).astype(int)
m['volup20'] = g['uphot'].transform(lambda s: s.rolling(20).sum())
cm = g['close'].transform(lambda s: s.rolling(20).mean())
cs = g['close'].transform(lambda s: s.rolling(20).std())
tm = g['tr'].transform(lambda s: s.rolling(20).mean())
ts_ = g['tr'].transform(lambda s: s.rolling(20).std())
prod = (m['close'] - cm) * (m['tr'] - tm)
m['ctc'] = prod.groupby(m['code']).transform(lambda s: s.rolling(20).mean()) / (cs * ts_ + 1e-12)
pc = m['tr'] * m['close']
wsum = pc.groupby(m['code']).transform(lambda s: s.rolling(60).sum())
tsum = g['tr'].transform(lambda s: s.rolling(60).sum())
m['profit'] = m['close'] / (wsum / (tsum + 1e-12)) - 1.0
T0 = m['date'].max()
last = m[m['date'] == T0].copy()
print('[2] 特征完成, 最新交易日', T0, flush=True)

print('[2b] 环境状态机输入(系统性风险观察层, 呈报零拦截)...', flush=True)
m['ma20'] = g['close'].transform(lambda s: s.rolling(20).mean())
m['above20'] = (m['close'] > m['ma20']).astype(int)
m['amount'] = pd.to_numeric(m['amount'], errors='coerce').fillna(0.0)
m['isdl'] = (((m['dr'] <= -0.095) & (~m['code'].str.startswith('3'))) |
             ((m['dr'] <= -0.19) & (m['code'].str.startswith('3')))).astype(int)
daily = m.groupby('date').agg(upshare=('dr', lambda s: (s > 0).mean()),
                              limit_down=('isdl', 'sum'), amt=('amount', 'sum')).reset_index()
tot_d = m.groupby('date')['amount'].sum().rename('tot')
top_d = m[m.groupby('date')['amount'].rank(ascending=False) <=
          (m.groupby('date')['code'].transform('size') * 0.10)].groupby('date')['amount'].sum().rename('top')
conc = (top_d / tot_d).reset_index(name='conc10')
ind = m.dropna(subset=['industry_name', 'ma20']).groupby(['date', 'industry_name'])['above20'].mean().reset_index(name='shr')
indw = ind.groupby('date')['shr'].mean().reset_index(name='industry_width')
daily = daily.merge(conc, on='date').merge(indw, on='date').sort_values('date').reset_index(drop=True)

print('[3] 环境温度...', flush=True)
daily_up = m.groupby('date')['dr'].apply(lambda s: (s > 0).mean())
b20 = round(float(daily_up.sort_index().tail(20).mean()) * 100, 1)
mm = m.copy()
mm['ym'] = mm['date'].str[:6]
mm['fwd10'] = mm.groupby('code')['close'].transform(lambda s: s.shift(-11) / s.shift(-1) - 1.0)
mm['win'] = (mm['fwd10'] > 0).astype(int)
mo = mm.dropna(subset=['fwd10']).groupby('ym').agg(base=('win', 'mean'), days=('date', 'nunique'))
mo_ok = mo[mo['days'] >= 15]
last_month = mo_ok.index[-1] if mo_ok is not None and len(mo_ok) else 'N/A'
last_base = float(mo_ok.iloc[-1]['base']) if mo_ok is not None and len(mo_ok) else None

L = []
L.append('# 校准台账 (呈报层·零交易权) | 生成 %s | 数据截至 %s' % (pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'), T0))
L.append('')
L.append('## 一、环境温度')
L.append('- 近20日实现广度(日均上涨股占比): **%.1f%%**' % b20)
if last_base is not None:
    L.append('- 最近完整月前瞻基础率(全市场10日上涨占比): **%.1f%%** (%s月)  ——低于50%%时个股看多证据整体折价' % (last_base * 100, last_month))
else:
    L.append('- 最近完整月前瞻基础率: N/A(窗口内无完整月)')
L.append('')
L.append('## 二、T1级知识 (样本外CI存活, 登记键VP8)')
L.append('- **否决端铁律**: 高位/巨量8格CI全负(t-2.4~-7.6, 2025-2026样本外)——候选触「高位+换手>7%」即降权, 「>15%」直接否决')
L.append('- **唯一CI全正格**: 低位×换手1-3% (OOS +0.59%, t=3.0)——温和关注档')
L.append('- **衰减警示**: H_t01等8格OOS跨零轴已降级; 排序证据每季重验, CI跌破零轴即降级')
L.append('')
L.append('## 三、排序证据 (全样本过闸, OOS-CI补测挂账⑪)')
L.append('- CTC(20日价量相关性)低优先: +0.47pp t6.2 [VP2-CTC-RANKING]')
L.append('- 浮盈低优先: 高浮盈组-1.10pp t7.8 [VP7-FLOAT-PROFIT-REVERSE]')
L.append('- 非频繁放量上涨优先: 频繁组-0.90pp t6.4 [VP7-VOLUP-FREQ-REVERSE]')
L.append('- 涨停结构: 缩量涨停(换手<3%)=持仓安心结构; 巨量涨停(>50%)=次日兑现警惕 [VP5/VP6]')
L.append('')
if args.codes.strip():
    L.append('## 四、候选排序 overlay (仅排序, 不产生买单)')
    codes = [c.strip() for c in args.codes.split(',') if c.strip()]
    rows = []
    for c in codes:
        sub = last[last['code'] == c]
        if len(sub) == 0 or sub[['ctc', 'profit', 'volup20', 'pos', 'vr', 'tr']].isna().any(axis=1).iloc[0]:
            rows.append({'code': c, 'ctc': None, 'profit': None, 'volup20': None, 'pos': None, 'vr': None, 'tr': None, 'rank_score': None})
            continue
        rows.append({'code': c, 'ctc': float(sub['ctc'].iloc[0]), 'profit': float(sub['profit'].iloc[0]),
                     'volup20': float(sub['volup20'].iloc[0]), 'pos': float(sub['pos'].iloc[0]), 'vr': float(sub['vr'].iloc[0]),
                     'tr': float(sub['tr'].iloc[0]),
                     'rank_score': None})
    df = pd.DataFrame(rows)
    ok = df.dropna(subset=['ctc', 'profit', 'volup20'])
    if len(ok):
        for colx, asc in [('ctc', True), ('profit', True), ('volup20', True)]:
            df.loc[ok.index, colx + '_r'] = ok[colx].rank(ascending=asc)
        df['rank_score'] = df[['ctc_r', 'profit_r', 'volup20_r']].mean(axis=1)
    df = df.sort_values('rank_score', na_position='last')
    L.append('| 候选 | CTC(低优) | 浮盈(低优) | 放量上涨天数/20(低优) | 位置 | 绝对换手%(否决判据) | 换手倍数 | 综合秩 |')
    L.append('|---|---|---|---|---|---|---|---|')
    for _, r in df.iterrows():
        fmt = lambda v, p=3: ('%.3f' % v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else 'N/A'
        veto = ''
        if r['tr'] is not None and not math.isnan(r['tr']) and r['pos'] is not None and not math.isnan(r['pos']):
            if r['tr'] > 15 and r['pos'] > 0.66:
                veto = ' ⛔否决端'
            elif r['tr'] > 7 and r['pos'] > 0.66:
                veto = ' ⚠️降权'
        L.append('| %s | %s | %s | %s | %s | %.2f | %s | %s |%s' % (
            r['code'], fmt(r['ctc']), fmt(r['profit']), fmt(r['volup20'], 1),
            ('%.2f' % r['pos']) if r['pos'] is not None and not math.isnan(r['pos']) else 'N/A',
            r['tr'] if r['tr'] is not None and not math.isnan(r['tr']) else float('nan'),
            ('%.2f' % r['vr']) if r['vr'] is not None and not math.isnan(r['vr']) else 'N/A',
            ('%.2f' % r['rank_score']) if r['rank_score'] is not None and not math.isnan(r['rank_score']) else 'N/A',
            veto))
    L.append('')
    L.append('注: 排序不新增交易, 不覆盖闸门; N/A=窗口数据不足(反臆想: 不给伪值)')
L.append('')
print('[2c] 环境状态机四档判定...', flush=True)
dd = daily.tail(5).reset_index(drop=True)
up_t = float(dd.iloc[-1]['upshare']) * 100
dl_t = int(dd.iloc[-1]['limit_down'])
conc_t = float(dd.iloc[-1]['conc10']) * 100
indw_t = float(dd.iloc[-1]['industry_width']) * 100
ups = dd['upshare'].tolist()
narrow3 = len(ups) >= 4 and all(ups[i] < ups[i - 1] for i in range(len(ups) - 3, len(ups)))
state, why = None, []
if up_t < 20:
    state, why = '极端/停手', ['上涨占比%.0f%%<20%%(一九分化)' % up_t]
elif dl_t > 50 and conc_t > 60:
    state, why = '极端/停手', ['跌停%d家>50且前10%%成交占比%.0f%%高位' % (dl_t, conc_t)]
elif dl_t <= 2 and up_t > 50 and indw_t > 50:
    state, why = '风险真空', ['跌停仅%d家且宽度健康' % dl_t]
elif narrow3 or (30 <= indw_t <= 50) or (10 < dl_t <= 50) or conc_t > 55:
    state, why = '警戒/收缩', []
    if narrow3:
        why.append('上涨占比连续3日缩窄')
    if 30 <= indw_t <= 50:
        why.append('行业宽度%.0f%%处30-50%%带' % indw_t)
    if 10 < dl_t <= 50:
        why.append('跌停%d家' % dl_t)
    if conc_t > 55:
        why.append('前10%%成交占比%.0f%%>55%%' % conc_t)
else:
    state, why = '正常/进攻', ['上涨占比%.0f%%/行业宽度%.0f%%/跌停%d家/集中度%.0f%%' % (up_t, indw_t, dl_t, conc_t)]
L.append('')
L.append('## 五、环境状态机 (系统性风险观察层, **呈报零拦截**, 依据主人框架四档定义)')
L.append('- **当前状态: %s**' % state)
for w in why:
    L.append('- 触发/依据: %s' % w)
L.append('- 输入实测: 上涨占比%.1f%% | 行业宽度(申万一级成员站上MA20占比均值)%.1f%% | 跌停家数%d | 前10%%成交额占比%.1f%% | 近5日上涨占比序列[%s]' % (
    up_t, indw_t, dl_t, conc_t, ','.join('%.0f' % (u * 100) for u in ups)))
L.append('- 边界声明: 本段只报告不拦截, 与已废除的DD_GATE/PD-037闸门无涉(台账在案); 交易决定权在主人与既有闸门')
L.append('')
L.append('—— 本段为呈报层输出, 依据 VP1-VP8 登记(data/falsified_rules.json)与 commit 67c85c6c 档案; 零接线零交易权')
txt = '\n'.join(L)
open(OUT_TXT, 'w', encoding='utf-8').write(txt)
print(txt)
print('\n已写入', OUT_TXT)