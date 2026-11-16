# -*- coding: utf-8 -*-
"""panel_v6 增量更新管线 v2 (2026-09-09 重写: 修 patch 叠加导致的基座/池过滤/chan重量缺陷).

基座: panel_v6_full.pkl 优先(动态), 不存在时回落 panel_v5_full.pkl.
新增日: qfq 日期 > 基座 max (基座即最新已入panel口径).
chan/lhb: 窗口查询(信号表 dict+交易日序号), 不再全史 rolling(原100分钟->秒级).
池过滤: 逐日池口径(<=20元/amt20>=500万/非ST/上市>=120bar), amt20 自算自洽.
空跑: 无新增交易日直接退出.
用法: python panel_v6_update.py
验收: 行数=基座+新增, 旧行抽样与基座一致, 日期覆盖至最新.
"""
import os, sys, time, sqlite3, bisect, gc
import numpy as np
import pandas as pd
sys.path.insert(0, r'D:\zcodesj\portrait_agent')
sys.path.insert(0, r'D:\zcodesj\portrait_agent\v6_research')  # 2026-09-19补: morph_profile/retrain_v4_full 9/16移入v6_research(迁移漏改致ModuleNotFoundError)
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
from morph_profile import compute_patterns
from retrain_v4_full import compute_new_patterns

HERE = r'D:\zcodesj\portrait_agent'
CACHE = os.path.join(HERE, 'cache')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
SPOT = r'D:\zcodesj\eastmoney_quant\stock\stock_data.db'
V5 = os.path.join(CACHE, 'panel_v5_full.pkl')
V6 = os.path.join(CACHE, 'panel_v6_full.pkl')
MORF_CACHE = os.path.join(CACHE, '_v6_morf_new.pkl')
T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

log('加载基座...')
base_path = V6 if (os.path.exists(V6) and os.path.getmtime(V6) > os.path.getmtime(V5)) else V5
base = pd.read_pickle(base_path)
base['date'] = base['date'].astype(str)
base_cols = list(base.columns)
base_max = base['date'].max()
log(f'基座 {os.path.basename(base_path)} -> {base.shape}, date_max={base_max}')

con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
# 2026-09-23 内存窗口化: 全史 q(~5209只x3300日, ~4GB)是OOM主源之一; 在用指标窗口
# 均<=252日(rolling/ewm 400日后衰减归零/ndays次新语义经 [120,400] 分档验证无损),
# 取倒数第401个交易日起载入, 内存降至~0.6GB。
q = pd.read_sql_query(
    "SELECT code,date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
    "WHERE date >= (SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date DESC LIMIT 1 OFFSET 400) "
    "ORDER BY code,date", con)
con.close()
for c in ['open','high','low','close','volume','amount','change_pct']:
    q[c] = pd.to_numeric(q[c], errors='coerce')
q = q.dropna(subset=['close'])
q = q[q['code'].str[:2].isin(('60','00','30','68'))].sort_values(['code','date']).reset_index(drop=True)
new_dates = sorted(d for d in set(q.date.unique()) - set(base.date.unique()) if d > base_max)
if not new_dates:
    log('无新增交易日, 已是最新, 退出'); sys.exit(0)
log(f'qfq {q.code.nunique()}只; 新增交易日 {new_dates}')

# ===== 1) 新增行量价/MACD/P0 (全史算好切新增行) =====
g = q.groupby('code', sort=False)
q['pc'] = g['close'].shift(1)
q['r'] = q['close']/q['pc'] - 1
q['ret5'] = q['close']/g['close'].shift(5) - 1
q['ret20'] = q['close']/g['close'].shift(20) - 1
q['ret60'] = q['close']/g['close'].shift(60) - 1
q['vol20'] = g['r'].transform(lambda s: s.rolling(20).std())
q['vol60'] = g['r'].transform(lambda s: s.rolling(60).std())
q['amp20'] = ((q['high']-q['low'])/q['pc']).groupby(q['code']).transform(lambda s: s.rolling(20).mean())
hi250 = g['high'].transform(lambda s: s.shift(1).rolling(250, min_periods=120).max())
lo250 = g['low'].transform(lambda s: s.shift(1).rolling(250, min_periods=120).min())
q['pos250'] = ((q['close']-lo250)/(hi250-lo250)).clip(0,1)
q['dist_hi'] = q['close']/hi250 - 1
q['bias20'] = q['close']/g['close'].transform(lambda s: s.rolling(20).mean()) - 1
q['nh20_cnt'] = g['close'].transform(lambda s: (s >= s.shift(1).rolling(20).max()*0.998).astype(float).rolling(20).sum())
q['lim_flag'] = q['change_pct'] >= np.where(q['code'].str[:3].isin(('300','301','688')), 19.88, 9.88)
q['lim_cnt250'] = g['lim_flag'].transform(lambda s: s.shift(1).rolling(250, min_periods=60).sum())
q['lb_seq'] = g['lim_flag'].transform(lambda s: s.groupby((~s).cumsum()).cumsum())
q['on'] = q['open']/q['pc'] - 1
q['intr'] = (1+q['change_pct'].fillna(0)/100)/(1+q['on']) - 1
q['cpos'] = np.where(q['high']>q['low'], (q['close']-q['low'])/(q['high']-q['low']), np.nan)
q['cpos20'] = g['cpos'].transform(lambda s: s.rolling(20, min_periods=15).mean())
q['oneword'] = (q['high'] <= q['low']*1.001)
q['first_board'] = (q['lim_flag'] & (g['lim_flag'].transform(lambda s: s.shift(1).rolling(60, min_periods=20).sum()).fillna(99)==0) & (~q['oneword'])).astype(float)
q['limit_touch'] = ((q['high'] >= q['pc']*(1+np.where(q['code'].str[:3].isin(('300','301','688')), 19.88, 9.88)/100)) & (~q['lim_flag'])).astype(float)
mkt = q.groupby('date')['r'].mean()
q['mkt_r'] = q['date'].map(mkt)
def _beta(s):
    mm = q.loc[s.index, 'mkt_r']
    return s.rolling(252, min_periods=150).cov(mm) / mm.rolling(252, min_periods=150).var()
q['beta252'] = g['r'].transform(_beta)
q['vol20_ann'] = g['r'].transform(lambda s: s.rolling(20).std())*np.sqrt(252)*100
q['brk_after_lb2'] = ((g['lb_seq'].shift(1) >= 2) & (~q['lim_flag'])).astype(float)
q['amt20'] = g['amount'].transform(lambda s: s.rolling(20).mean())
q['ndays'] = g.cumcount()+1
ema12 = g['close'].transform(lambda s: s.ewm(span=12, adjust=False).mean())
ema26 = g['close'].transform(lambda s: s.ewm(span=26, adjust=False).mean())
q['dif'] = ema12 - ema26
q['hist'] = q['dif'].groupby(q['code']).transform(lambda s: s.ewm(span=9, adjust=False).mean())
q['dif_pos60'] = (q['dif'] > 0).groupby(q['code']).transform(lambda s: s.rolling(60).mean())
q['bars_golden'] = g['hist'].transform(lambda s: s.groupby((np.sign(s) == np.sign(s.shift(1))).cumsum()).cumcount()+1)
newq = q[q['date'].isin(new_dates)].copy()
log(f'新增行 {len(newq)}')

# ===== 2) 池过滤(逐日池) =====
con2 = sqlite3.connect(f'file:{SPOT}?mode=ro', uri=True)
nm8 = dict(con2.execute("SELECT symbol,name FROM stock_spot WHERE updated_date=(SELECT MAX(updated_date) FROM stock_spot)").fetchall())
con2.close()
snap8 = q[q['date']==max(new_dates)].set_index('code')
pool8 = {c for c, r in snap8.iterrows()
         if c[:2] in ('60','00','30','68') and c in nm8 and 'ST' not in str(nm8[c]).upper()
         and r['close'] <= 20 and (r['amt20'] or 0) >= 5e6 and r['ndays'] >= 120}
n_bef = len(newq)
newq = newq[newq['code'].isin(pool8)]
log(f'池过滤: {n_bef} -> {len(newq)} (剔{n_bef-len(newq)}池外行, 池{len(pool8)}只)')

# ===== 3) 形态列(全史重算口径; 缓存按覆盖判断) =====
morf = None
if os.path.exists(MORF_CACHE):
    morf_c = pd.read_pickle(MORF_CACHE)
    covered = set(morf_c['date'].astype(str).unique())
    if set(new_dates).issubset(covered):
        morf = morf_c[morf_c['date'].astype(str).isin(new_dates)]
        log(f'形态缓存复用 {len(morf)} 行')
if morf is None:
    log('形态全史重算...')
    q2 = q[['code','date','open','high','low','close','volume','amount','change_pct','dif','hist']].copy()
    frames = []
    for k, (code, gg) in enumerate(q2.groupby('code', sort=False)):
        if k % 1000 == 0: log(f'  形态 {k}/{q2.code.nunique()}')
        gg = gg.reset_index(drop=True)
        try:
            a = compute_patterns(gg)
            b = compute_new_patterns(gg, gg['dif'], gg['hist'])
            mf = pd.concat([a, b], axis=1)
            mf['code'] = code; mf['date'] = gg['date']
            frames.append(mf[mf['date'].isin(new_dates)])
        except Exception:
            continue
    morf = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    morf.to_pickle(MORF_CACHE)
    log(f'形态重算完成 {len(morf)} 行(缓存更新)')

# ===== 4) chan/lhb 窗口查询(轻量) =====
con_t = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
tds = [r[0] for r in con_t.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
con_t.close()
td_idx = {d: i for i, d in enumerate(tds)}
base_keys = list(newq[['code','date']].itertuples(index=False, name=None))

def win_hit(sig_set, win_days):
    out = []
    sig_by = {}
    for c, d in sig_set:
        sig_by.setdefault(c, []).append(td_idx.get(d, 10**9))
    for c, d in base_keys:
        i = td_idx.get(d)
        lst = sig_by.get(c, [])
        j = bisect.bisect_right(lst, i)
        hit = j > 0 and lst[j-1] >= i - win_days
        out.append((c, d, 1.0 if hit else 0.0))
    return out

cs = pd.read_pickle(os.path.join(CACHE, 'chan_signals_v2.pkl'))
cs['date'] = cs['date'].astype(str)
h1 = win_hit(set(zip(cs[cs['type']=='buy1']['code'], cs[cs['type']=='buy1']['date'])), 9)
h2 = win_hit(set(zip(cs[cs['type']=='buy2']['code'], cs[cs['type']=='buy2']['date'])), 9)
cdf = pd.DataFrame(h1, columns=['code','date','chan_buy1_10']).merge(
      pd.DataFrame(h2, columns=['code','date','chan_buy2_10']), on=['code','date'], how='outer')
log('chan 窗口查询完成')

con3 = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
# 2026-09-10 D1: 迁移 dragon_tiger(8/10 冻结, 无写入方) → dragon_tiger_new(日更,
# lhb_update 四表唯一写源); 列名映射 net_buy→net_amount
lhb = pd.read_sql_query(
    "SELECT trade_date,ts_code,net_amount AS net_buy FROM dragon_tiger_new", con3)
con3.close()
lhb['date'] = lhb['trade_date'].astype(str).str.replace('-','')
lhb['net_buy'] = pd.to_numeric(lhb['net_buy'], errors='coerce')
hl = win_hit(set(zip(lhb[lhb['net_buy'] > 0]['ts_code'], lhb[lhb['net_buy'] > 0]['date'])), 4)
ldf = pd.DataFrame(hl, columns=['code','date','lhb_buy_5'])
log('lhb 窗口查询完成')

# 2026-09-23 内存减负: q 全表(~4GB, 5209只全史+40派生列)在新增行切片后仅形态全史
# 分支使用, 合成段不再需要; 不清场则 concat 峰值叠加致 6.15GiB 单次分配 OOM。
del q
try:
    del q2
except NameError:
    pass
gc.collect()
log('内存减负完成(q 全表已释放)')

# ===== 5) 合成 =====
P0_COLS = ['on','intr','cpos','cpos20','oneword','lb_seq','first_board','limit_touch','beta252','vol20_ann','brk_after_lb2']
keep = ['code','date','open','high','low','close','volume','amount','change_pct',
        'ret5','ret20','ret60','vol20','vol60','amp20','pos250','dist_hi','bias20',
        'nh20_cnt','lim_cnt250','maxstreak250','dif','hist','dif_pos60','bars_golden',
        'lim_today','on','intr','cpos','cpos20','oneword','lb_seq','first_board',
        'limit_touch','beta252','vol20_ann','brk_after_lb2']
new_rows = newq[[c for c in keep if c in newq.columns]].copy()
if 'chg' not in new_rows.columns:
    new_rows['chg'] = new_rows.get('change_pct')
new_rows = new_rows.merge(morf, on=['code','date'], how='left')
raw_cols = ['island_bottom','dip_rebound','rounded_bottom','limit_break','gap_up_run','new_high250']
comb_raw = pd.concat([base[['code','date']+raw_cols], new_rows[['code','date']+raw_cols]]).sort_values(['code','date'])
gc = comb_raw.groupby('code', sort=False)
der = pd.DataFrame({'code': comb_raw['code'].values, 'date': comb_raw['date'].values})
der['island_5'] = gc['island_bottom'].transform(lambda s: s.rolling(5).max()).values
der['dip_reb_10'] = gc['dip_rebound'].transform(lambda s: s.rolling(10).max()).values
der['rounded_20'] = gc['rounded_bottom'].transform(lambda s: s.rolling(20).max()).values
der['limit_break_5'] = gc['limit_break'].transform(lambda s: s.rolling(5).max()).values
der['gap_up_run_5'] = gc['gap_up_run'].transform(lambda s: s.rolling(5).max()).values
der['new_high_3'] = gc['new_high250'].transform(lambda s: s.rolling(3).max()).values
new_rows = new_rows.merge(der[der['date'].isin(new_dates)], on=['code','date'], how='left')
new_rows = new_rows.merge(cdf, on=['code','date'], how='left')
new_rows = new_rows.merge(ldf, on=['code','date'], how='left')
# enrich 派生列(行业/市场截面, 口径=data_enrich_v5: 池内聚合)
if 'lianban' not in new_rows.columns:
    new_rows['lianban'] = new_rows.get('lb_seq', 0)
# 2026-09-12 修增量首跑 KeyError: 增量路径 newq 无 lim_today 生成(首建走 feature_engine 有),
# 按同口径就地派生(feature_engine.py:89: 主板9.8/科创创业19.8)
if 'lim_today' not in new_rows.columns:
    _chg = pd.to_numeric(new_rows['change_pct'], errors='coerce')
    _is20 = new_rows['code'].astype(str).str[:3].isin(['300', '301', '688', '689'])
    new_rows['lim_today'] = _chg >= np.where(_is20, 19.8, 9.8)
new_rows['lim_b'] = new_rows['lim_today'].fillna(False).astype(int)
ind_map = base.groupby('code')[['L1']].last()
nr = new_rows.drop(columns=[c for c in ['L1'] if c in new_rows.columns]).merge(ind_map, on='code', how='left')
day_ind = nr.groupby(['date','L1']).agg(ind_limit_cnt=('lim_b','sum'), ind_mean_chg=('chg','mean'),
                                        ind_up_pct=('chg', lambda s: (s > 0).mean())).reset_index()
day_mkt = nr.groupby('date').agg(mkt_limit_cnt=('lim_b','sum'), mkt_lianban_max=('lianban','max')).reset_index()
day_ind['ind_ret_rank'] = day_ind.groupby('date')['ind_mean_chg'].rank(pct=True)
nr = nr.merge(day_ind, on=['date','L1'], how='left').merge(day_mkt, on='date', how='left')
nr['rel_ind_ret'] = nr['chg'] - nr['ind_mean_chg']
nr['ind_lim_ge3'] = (nr['ind_limit_cnt'] >= 3).astype(float)
nr['ind_lim_ge5'] = (nr['ind_limit_cnt'] >= 5).astype(float)
nr['mkt_hot'] = (nr['mkt_limit_cnt'] >= 100).astype(float)
new_rows = nr
# 2026-09-12 修增量路径 pit_last 未定义(半截引用, 首建脚本才有构造段):
# 按base同口径从 factor_pit.db v2 asof取每code截面日前已生效最新报告期
# (ep/bp/roe/lev, pit_avail_date门控; 量纲=百分数, 与base抽样一致)
con_pit = sqlite3.connect(r'file:D:\zcodesj\factor_pit.db?mode=ro', uri=True)
_pit = pd.read_sql_query(
    "SELECT code, pit_avail_date, ep, bp, roe, lev FROM stock_factors_fundamental_pit_v2 "
    "WHERE pit_avail_date <= ?", con_pit, params=[max(new_dates)])
con_pit.close()
_pit = _pit.sort_values(['code', 'pit_avail_date']).groupby('code').tail(1)
pit_last = _pit.rename(columns={'ep': 'pit_ep', 'bp': 'pit_bp', 'roe': 'pit_roe', 'lev': 'pit_lev'})[
    ['code', 'pit_ep', 'pit_bp', 'pit_roe', 'pit_lev']]
log(f'pit asof {len(pit_last)} code (avail<={max(new_dates)})')
new_rows = new_rows.merge(pit_last, on='code', how='left')
meta = base.groupby('code')[[c for c in ['board','L1'] if c in base.columns]].last()
new_rows = new_rows.merge(meta, on='code', how='left')
for c in base_cols:
    if c not in new_rows.columns: new_rows[c] = np.nan
new_rows = new_rows[base_cols]
# 2026-09-23 修 OOM(9/23凌晨两次复现: 801万行x103列 全量 sort 的 consolidate 需要
# 单次连续分配 6.15GiB, 63GB 物理机也炸): base 由本管线落盘前恒按 code,date 全序,
# 且 new_rows 日期恒 > base_max, 故 new_rows 按 code 排序后 concat 即天然全局有序,
# 全量 argsort 免除; 保留 base 有序性断言, 断言不过才回退旧路径(宁可慢不可错序)。
nr_sorted = new_rows.sort_values(['code','date'])
_base_ordered = bool((base['code'].values[1:] >= base['code'].values[:-1]).all())
if _base_ordered:
    v6 = pd.concat([base, nr_sorted], ignore_index=True)
else:
    log('WARN: base 有序性断言不过, 回退全量排序(内存峰值高)')
    v6 = pd.concat([base, nr_sorted], ignore_index=True).sort_values(['code','date']).reset_index(drop=True)
del nr_sorted, _base_ordered
log(f'合成 v6 {v6.shape}, date_max={v6.date.max()}')

# ===== 6) 验收 =====
ok_rows = len(v6) == len(base) + len(new_rows)
ok_date = v6['date'].max() == max(new_dates)
chk = base.sample(min(800, len(base)), random_state=3)[['code','date','close','pos250','chan_buy1_10','island_5']]
cc = chk.merge(v6[chk.columns], on=['code','date'], suffixes=('_b','_n'))
ok_cons = all((cc[f'{c}_b'].fillna(-999) == cc[f'{c}_n'].fillna(-999)).all() for c in ['close','pos250','chan_buy1_10','island_5'])
print('==== 验收 ====')
print(f'行数 {len(base)}+{len(new_rows)}={len(v6)} {"OK" if ok_rows else "FAIL"} | 日期至 {v6.date.max()} {"OK" if ok_date else "FAIL"} | 旧行一致性 {"OK" if ok_cons else "FAIL"}')
del base  # 2026-09-23: 验收已毕, 释放6.3GB再序列化, 削to_pickle峰值
v6.to_pickle(V6)
log(f'落盘 {V6}')
