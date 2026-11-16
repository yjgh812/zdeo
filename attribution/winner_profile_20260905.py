# -*- coding: utf-8 -*-
"""涨票特征画像 20260904窗口 — 全市场主板+创业板, 1年周期, 双口径涨票 vs 实际买入逐维对比.
数据: qfq真库(stock_daily_meta_qfq) + fundamental_data.db(daily_basic/industry_map_ths) + 买入台账jsonl.
MACD: 复刻 ~/.agents/skills/macd v2.0.4 算法(EMA SMA-seed, DIF=EMA12-EMA26, DEA=EMA(DIF,9), hist=DIF-DEA).
只读研究, 零接线. 输出: attribution/winner_profile_out/ + reports/WINNER_PROFILE_20260904.md(另步生成).
"""
import sqlite3, json, sys, io, os, time, math
from collections import Counter, defaultdict
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
T0 = time.time()
def log(msg): print(f"[{time.time()-T0:7.1f}s] {msg}", flush=True)

QFQ_DB  = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
BUYS_F  = r'D:\zcodesj\attribution\winner_profile_buys_20260905.jsonl'
OUT_DIR = r'D:\zcodesj\attribution\winner_profile_out'
os.makedirs(OUT_DIR, exist_ok=True)

TRAD_PREFIX = ('600','601','603','605','000','001','002','003','300','301','302')
CHINEXT = ('300','301','302')

# ---------- 1. 数据装载 ----------
log('load qfq alive tradable codes')
con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
alive = [r[0] for r in con.execute(
    "SELECT code FROM stock_daily_meta_qfq WHERE date>='20260830' GROUP BY code")]
alive = [c for c in alive if c[:3] in TRAD_PREFIX]
log(f'alive tradable codes={len(alive)}')
ph = ','.join('?'*len(alive))
qfq = pd.read_sql_query(
    f"SELECT code,date,close,high,low,change_pct FROM stock_daily_meta_qfq "
    f"WHERE date>='20240601' AND code IN ({ph})",
    con, params=alive)
log(f'qfq rows={len(qfq)}')

log('load daily_basic')
con2 = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
db = pd.read_sql_query(
    "SELECT ts_code,trade_date,total_mv,circ_mv,turnover_rate,pe_ttm,pb FROM daily_basic "
    "WHERE trade_date>='20250801'", con2)
db['code'] = db['ts_code'].str[:6]
db = db.drop(columns=['ts_code'])
for col in ['total_mv','circ_mv','turnover_rate','pe_ttm','pb']:
    db[col] = pd.to_numeric(db[col], errors='coerce')
log(f'daily_basic rows={len(db)}')

log('load industry/name map')
ind = pd.read_sql_query("SELECT code,name,industry FROM industry_map_ths", con2)
con.close(); con2.close()
ind['L1'] = ind['industry'].fillna('未知').str.split('-').str[0]
ind['is_st'] = ind['name'].fillna('').str.contains('ST') | ind['name'].fillna('').str.contains('退')
name_map = dict(zip(ind['code'], ind['name']))
st_set = set(ind.loc[ind['is_st'], 'code'])
l1_map = dict(zip(ind['code'], ind['L1']))
log(f'name map={len(name_map)} ST={len(st_set)}')

buys = [json.loads(l) for l in open(BUYS_F, encoding='utf-8') if l.strip()]
log(f'buy fills={len(buys)}')

# ---------- 2. 逐票特征面板 ----------
log('build per-stock feature panel')
qfq = qfq.sort_values(['code','date'])

# MACD: skill v2.0.4 逐行复刻 (SMA-seed EMA)
def ema_sma_seed(a, period):
    out = np.empty(len(a)); out[:] = np.nan
    if len(a) < period: return out
    out[period-1] = a[:period].mean()
    m = 2.0/(period+1)
    for i in range(period, len(a)):
        out[i] = a[i]*m + out[i-1]*(1-m)
    return out

def macd_skill(close):
    n = len(close)
    e12 = ema_sma_seed(close, 12); e26 = ema_sma_seed(close, 26)
    dif = np.full(n, np.nan); hist = np.full(n, np.nan); dea = np.full(n, np.nan)
    if n >= 26:
        dif[25:] = e12[25:] - e26[25:]
    d9 = ema_sma_seed(dif[25:], 9) if n >= 26 else None
    if d9 is not None and len(d9) >= 9 and not np.isnan(d9[8]):
        dea[25+8:] = d9[8:]
        hist[25+8:] = dif[25+8:] - dea[25+8:]
    return dif, dea, hist

# zigzag pivot (5% threshold, close-based), full series once
def zigzag_pivots(close, thr=0.05):
    piv = []
    if len(close) < 3: return piv
    direction = 0; last_p = close[0]; last_i = 0
    for i in range(1, len(close)):
        p = close[i]
        if direction >= 0 and p < last_p*(1-thr):
            if direction > 0 or last_i == 0:
                piv.append(last_i)
            direction = -1; last_p = p; last_i = i
        elif direction <= 0 and p > last_p*(1+thr):
            if direction < 0 or last_i == 0:
                piv.append(last_i)
            direction = 1; last_p = p; last_i = i
        else:
            if (direction > 0 and p > last_p) or (direction < 0 and p < last_p):
                last_p = p; last_i = i
    piv.append(last_i)
    return piv

feat_parts = []
q_groups = {c: g for c, g in qfq.groupby('code', sort=False)}
log(f'groups={len(q_groups)}')
for k, (code, g) in enumerate(q_groups.items()):
    if k % 900 == 0: log(f'  stock {k}/{len(q_groups)}')
    g = g.drop_duplicates('date').reset_index(drop=True)
    d = g['date'].values; c = g['close'].values.astype(float)
    hi = g['high'].values.astype(float); lo = g['low'].values.astype(float)
    chg = g['change_pct'].values.astype(float)
    n = len(c)
    if n < 40: continue
    df = pd.DataFrame({'code': code, 'date': d, 'close': c})
    s = pd.Series(c)
    df['ret'] = s.pct_change()
    df['ret20'] = s/s.shift(20)-1
    df['ret60'] = s/s.shift(60)-1
    df['vol60'] = df['ret'].rolling(60).std()
    amp = (hi-lo)/np.roll(c,1); amp[0]=np.nan
    df['amp20'] = pd.Series(amp).rolling(20).mean()
    df['amp_ratio'] = pd.Series(amp).rolling(5).mean()/df['amp20']
    df['hi250'] = s.rolling(250, min_periods=60).max()
    df['lo250'] = s.rolling(250, min_periods=60).min()
    df['pos250'] = (s-df['lo250'])/(df['hi250']-df['lo250'])
    df['dist_hi'] = s/df['hi250']-1
    df['ma5'] = s.rolling(5).mean(); df['ma20'] = s.rolling(20).mean(); df['ma60'] = s.rolling(60).mean()
    df['bias20'] = s/df['ma20']-1
    stk = (df['ma5']>df['ma20']) & (df['ma20']>df['ma60'])
    stk_dn = (df['ma5']<df['ma20']) & (df['ma20']<df['ma60'])
    df['ma_stack'] = np.where(stk, 1, np.where(stk_dn, -1, 0))
    df['nh20_cnt'] = (s.rolling(20).max()==s).rolling(20).sum()
    # 涨停(近似): 主板>=9.8 创业板>=19.8, qfq百分数口径
    lim_thr = 19.8 if code[:3] in CHINEXT else 9.8
    lim = chg >= lim_thr
    df['lim_cnt250'] = pd.Series(lim.astype(float)).rolling(250, min_periods=60).sum()
    df['lim_cnt60'] = pd.Series(lim.astype(float)).rolling(60, min_periods=20).sum()
    # 最大连板streak(250内)
    streak = np.zeros(n, dtype=float); mx = 0.0; cur = 0
    for i in range(n):
        cur = cur+1 if lim[i] else 0
        if cur > mx: mx = cur
        streak[i] = mx
    df['maxstreak'] = streak
    # MACD (skill口径)
    dif, dea, hist = macd_skill(c)
    df['dif'] = dif; df['dea'] = dea; df['hist'] = hist
    pos = dif > dea
    cross_up = (pos & ~np.roll(pos,1)); cross_up[0]=False
    ci = np.where(cross_up, np.arange(n), -1)
    last = np.maximum.accumulate(ci)
    df['bars_golden'] = np.where(last>=0, np.arange(n)-last, np.nan)
    df['dif_pos60'] = pd.Series(np.where(np.isnan(dif), np.nan, (dif>0).astype(float))).rolling(60, min_periods=30).mean()
    # zigzag + 中枢
    piv = zigzag_pivots(c)
    piv_arr = np.array(piv)
    piv_cnt = np.searchsorted(piv_arr, np.arange(n), side='right')
    df['zz_cnt250'] = piv_cnt - np.searchsorted(piv_arr, np.maximum(np.arange(n)-249, 0), side='left')
    med120 = s.rolling(120, min_periods=60).median()
    band = 0.075
    inband = (np.abs(s-med120) <= med120*band).astype(float)
    df['zhongshu60'] = inband.rolling(60, min_periods=30).mean()
    df['pos_zs'] = s/med120-1
    df['fwd20'] = s.shift(-20)/s-1
    df['fwd4'] = s.shift(-4)/s-1
    df['bar_no'] = np.arange(n)
    feat_parts.append(df)

panel = pd.concat(feat_parts, ignore_index=True)
log(f'panel rows={len(panel)}')

# 合并 daily_basic (逐票 reindex+ffill: 缺日回填最近一日,上限7天,记fallback)
db = db.sort_values(['code','trade_date'])
db_by_code = {c: x.set_index('trade_date')[['total_mv','circ_mv','turnover_rate','pe_ttm','pb']]
              for c, x in db.groupby('code')}
merged = []
fallback_cnt = 0
for code, g in panel.groupby('code'):
    b = db_by_code.get(code)
    if b is not None and len(b):
        aligned = b.reindex(g['date'].values).ffill(limit=7)
        merged.append(pd.concat([g.reset_index(drop=True), aligned.reset_index(drop=True)], axis=1))
    else:
        for col in ['total_mv','circ_mv','turnover_rate','pe_ttm','pb']:
            g[col] = np.nan
        merged.append(g)
panel = pd.concat(merged, ignore_index=True)
panel['turnover20'] = panel.groupby('code')['turnover_rate'].transform(lambda x: x.rolling(20, min_periods=10).mean())
panel['log_mv'] = np.log10(panel['total_mv'])       # total_mv万元
panel['log_cmv'] = np.log10(panel['circ_mv'])
panel['loss'] = (panel['pe_ttm'] < 0) | panel['pe_ttm'].isna()
panel['ST'] = panel['code'].isin(st_set)
panel.to_pickle(os.path.join(OUT_DIR, 'panel.pkl'))
log(f'panel merged rows={len(panel)} daily_basic月度fallback行={fallback_cnt}')

# MACD skill 端到端对账: 抽3票 与 pandas ewm 收敛性互检(>120bar 差应<1e-6)
chk_codes = ['600519','000001','300750'] if all(c in q_groups for c in ['600519','000001','300750']) else list(q_groups)[:3]
for cc in chk_codes:
    g = q_groups[cc]; c = g['close'].values.astype(float)
    dif, dea, hist = macd_skill(c)
    ew12 = pd.Series(c).ewm(span=12, adjust=False).mean()
    ew26 = pd.Series(c).ewm(span=26, adjust=False).mean()
    fast_dif = ew12-ew26
    m = np.arange(len(c)) >= 150
    print(f'  MACD对账 {cc}({name_map.get(cc,cc)}): skill_dif末={dif[-1]:.6f} ewm_dif末={fast_dif.iloc[-1]:.6f} 最大差(>150bar)={np.nanmax(np.abs(dif[m]-fast_dif.values[m])):.2e}')

# ---------- 3. 截面: 11个月末完整fwd20 + 20260831补充fwd4 ----------
dates_all = sorted(panel['date'].unique())
def month_end(ds, ym):
    c = [d for d in ds if d[:6] == ym]
    return c[-1] if c else None
sections = []
for ym in ['202509','202510','202511','202512','202601','202602','202603','202604','202605','202606','202607']:
    t = month_end(dates_all, ym)
    if t: sections.append((t, 'fwd20'))
t_aug = month_end(dates_all, '202608')
if t_aug: sections.append((t_aug, 'fwd4'))
log(f'sections={sections}')

def section_frame(t, fwdcol):
    sf = panel[(panel['date']==t) & (~panel['ST'])].copy()
    sf = sf[(sf['bar_no']>=150) & sf['close'].notna() & sf[fwdcol].notna() & sf['total_mv'].notna()]
    # 行业中性化超额: 减所属L1行业内均值(等权均值不减改变截面内排序,无中性化作用)
    sf['L1_'] = sf['code'].map(l1_map).fillna('未知')
    sf['excess'] = sf[fwdcol] - sf.groupby('L1_')[fwdcol].transform('mean')
    q = sf[fwdcol].quantile(0.90); qe = sf['excess'].quantile(0.90)
    sf['win_raw'] = sf[fwdcol] >= q
    sf['win_ex'] = sf['excess'] >= qe
    return sf

DIMS = [('log_mv','总市值log10(万元)',1),('log_cmv','流通市值log10(万元)',1),
        ('turnover20','换手率20日均(%)',1),('ret20','动量ret20(%)',1),('ret60','动量ret60(%)',1),
        ('vol60','波动vol60(日std)',1),('amp20','日均振幅20d(%)',1),
        ('pos250','年区间位置0-1',1),('dist_hi','距250日高点(%)',1),('bias20','MA20乖离(%)',1),
        ('nh20_cnt','20日新高次数(近20日)',1),('lim_cnt250','250日涨停次数',1),('lim_cnt60','60日涨停次数',1),
        ('maxstreak','250日最大连板',1),('zz_cnt250','250日zigzag笔数',1),('zhongshu60','中枢密度60d',1),
        ('pos_zs','现价vs中枢中位(%)',1),('dif','MACD DIF(元)',1),('hist','MACD 柱 DIF-DEA(元)',1),
        ('bars_golden','金叉距今bar数',-1),('dif_pos60','DIF>0时长占比60d',1),
        ('pb','PB(倍)',1),('pe_ttm_pos','PE_TTM盈利子集(倍)',1)]
PRICE_BINS = [(0,5,'<5元'),(5,10,'5-10元'),(10,20,'10-20元'),(20,30,'20-30元'),(30,1e9,'>30元')]

sec_stats = []
pooled_market, pooled_winraw, pooled_winex = [], [], []
for t, fwdcol in sections:
    sf = section_frame(t, fwdcol)
    sf['pe_ttm_pos'] = sf['pe_ttm'].where(~sf['loss'])
    for tag, col in [('market',None),('win_raw','win_raw'),('win_ex','win_ex')]:
        sub = sf if col is None else sf[sf[col]]
        rec = {'date': t, 'fwd': fwdcol, 'grp': tag, 'n': int(len(sub)),
               'loss_share': float(sub['loss'].mean()*100)}
        for dim, _, _ in DIMS:
            rec[dim] = float(sub[dim].median()) if len(sub) and sub[dim].notna().any() else np.nan
        # 价格分桶
        pc = sub['close']
        rec['price_bucket'] = {lab: float(((pc>=a)&(pc<b)).mean()*100) for a,b,lab in PRICE_BINS}
        # 行业L1 top5
        l1 = sub['code'].map(l1_map).fillna('未知')
        rec['top_ind'] = Counter(l1).most_common(5)
        sec_stats.append(rec)
    pooled_market.append(sf)
    pooled_winraw.append(sf[sf['win_raw']]); pooled_winex.append(sf[sf['win_ex']])
    log(f'  section {t}({fwdcol}) n={len(sf)} winraw={int(sf["win_raw"].sum())} fwd20中位={sf[fwdcol].median()*100:.2f}%')

market_pool = pd.concat(pooled_market)
winraw_pool = pd.concat(pooled_winraw)
winex_pool = pd.concat(pooled_winex)

# ---------- 4. 买入组: D-1截面特征 + 当日截面分位 + 入场位置 ----------
log('buy-side features')
buy_rows = []
trade_dates = np.array(dates_all)
for b in buys:
    code, D = b['code'], b['date']
    g = panel[panel['code']==code]
    if g.empty:
        buy_rows.append({**b, 'ok': False, 'reason': 'panel无此票'}); continue
    prev = g[g['date'] < D]
    if prev.empty or len(prev) < 150:
        buy_rows.append({**b, 'ok': False, 'reason': f'历史不足({len(prev)}bar)'}); continue
    row = prev.iloc[-1]
    t_asof = row['date']
    sf = panel[(panel['date']==t_asof) & (~panel['ST']) & (panel['bar_no']>=150) & panel['close'].notna() & panel['total_mv'].notna()]
    pct = {}
    for dim, _, _ in DIMS:
        if dim == 'pe_ttm_pos':
            sub = sf.loc[~sf['loss'], 'pe_ttm'].dropna()
            v = row['pe_ttm'] if not row['loss'] else np.nan
        else:
            sub = sf[dim].dropna()
            v = row[dim]
        pct[dim] = float((sub < v).mean()*100) if (len(sub) and pd.notna(v)) else np.nan
    # 入场位置: 买入日当日涨幅(qfq) + D-1的5日乖离近似(用ret20替代防NaN)+当日价格分位
    day = g[g['date']==D]
    day_chg = float(day['close'].iloc[0]/row['close']-1)*100 if len(day) else np.nan
    bias5 = float(row['close']/g['close'].iloc[max(0,len(prev)-5):].mean()-1) if len(prev)>=5 else np.nan
    buy_rows.append({**b, 'ok': True, 'asof': t_asof, 'name2': name_map.get(code,''),
                     'L1': l1_map.get(code,'未知'),
                     'day_chg': day_chg, 'bias5': bias5,
                     **{f'p_{dim}': pct[dim] for dim, _, _ in DIMS},
                     **{dim: (float(row['pe_ttm']) if not row['loss'] else None) if dim == 'pe_ttm_pos'
                         else (float(row[dim]) if pd.notna(row[dim]) else None) for dim, _, _ in DIMS},
                     'close': float(row['close']), 'loss': bool(row['loss'])})
ok_buys = [r for r in buy_rows if r.get('ok')]
log(f'buys ok={len(ok_buys)} / {len(buy_rows)}; bad={[(r["code"],r.get("reason")) for r in buy_rows if not r.get("ok")]}')

# ---------- 5. 汇总输出 ----------
summary = {}
for dim, label, sgn in DIMS:
    mv = float(market_pool[dim].median()); wv = float(winraw_pool[dim].median()); we = float(winex_pool[dim].median())
    vals = [np.nan if r.get(dim) is None else r[dim] for r in ok_buys]
    bv = np.nanmedian(vals) if any(pd.notna(v) for v in vals) else np.nan
    bp = np.nanmean([r[f'p_{dim}'] for r in ok_buys])
    summary[dim] = {'label': label, 'market_med': mv, 'winraw_med': wv, 'winex_med': we,
                    'buys_med': float(bv), 'buys_mean_pct': float(bp) if pd.notna(bp) else None,
                    'winraw_minus_market': wv-mv}
# 价格分桶对比
def pbucket(frame):
    pc = frame['close']
    return {lab: float(((pc>=a)&(pc<b)).mean()*100) for a,b,lab in PRICE_BINS}
buy_pb = Counter()
for r in ok_buys:
    for a,bb,lab in PRICE_BINS:
        if a <= (r['close'] or 0) < bb: buy_pb[lab]+=1
summary['_price_bucket'] = {'market': pbucket(market_pool), 'win_raw': pbucket(winraw_pool),
    'win_ex': pbucket(winex_pool),
    'buys': {lab: buy_pb[lab]*100/len(ok_buys) for _,_,lab in PRICE_BINS}}
summary['_loss_share'] = {'market': float(market_pool['loss'].mean()*100),
    'win_raw': float(winraw_pool['loss'].mean()*100), 'win_ex': float(winex_pool['loss'].mean()*100),
    'buys': float(np.mean([r['loss'] for r in ok_buys])*100)}
summary['_industry'] = {
    'win_raw_top10': Counter(winraw_pool['code'].map(l1_map).fillna('未知')).most_common(10),
    'win_ex_top10': Counter(winex_pool['code'].map(l1_map).fillna('未知')).most_common(10),
    'buys_top10': Counter([r['L1'] for r in ok_buys]).most_common(10),
    'market_top10': Counter(market_pool['code'].map(l1_map).fillna('未知')).most_common(10)}
# 入场位置
summary['_entry'] = {'day_chg_buys_med': float(np.nanmedian([r['day_chg'] for r in ok_buys])),
    'day_chg_buys_mean': float(np.nanmean([r['day_chg'] for r in ok_buys])),
    'bias5_buys_med': float(np.nanmedian([r['bias5'] for r in ok_buys]))*100,
    'day_chg_market_med': float(market_pool['close'].pct_change().median()*100)}
# 分段: 7月红利期 vs 8月后
jul = [r for r in ok_buys if r['date'] < '20260801']; aug = [r for r in ok_buys if r['date'] >= '20260801']
for tag, grp in [('jul', jul), ('aug', aug)]:
    if grp:
        seg_dims = ['log_mv','turnover20','ret20','vol60','lim_cnt250','pos250']
        summary[f'_seg_{tag}'] = {'n': len(grp), **{
            dim: (lambda v: float(np.nanmedian(v)) if any(pd.notna(x) for x in v) else None)(
                [np.nan if r.get(dim) is None else r[dim] for r in grp]) for dim in seg_dims}}
# 截面级稳定性: 各section win_raw中位 vs market中位的差
stab = defaultdict(list)
for rec in sec_stats:
    if rec['grp']=='win_raw':
        base = [r for r in sec_stats if r['date']==rec['date'] and r['grp']=='market'][0]
        for dim,_,_ in DIMS:
            stab[dim].append(rec[dim]-base[dim])
summary['_stability'] = {dim: {'pos_months': int(sum(1 for x in v if x>0)), 'n': len(v)} for dim,v in stab.items()}

json.dump({'sections': sections, 'sec_stats': sec_stats, 'summary': summary,
           'buys': buy_rows,
           'macd_note': 'skill v2.0.4 SMA-seed算法逐行复刻;预热2024-06起,截面处≥300bar'},
          open(os.path.join(OUT_DIR, 'winner_profile_result.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, default=float, indent=1)
log('result json saved')

# 摘要打印
print('\n==== 逐维: 涨票(raw前10%) vs 市场 vs 我们的买入 ====')
print(f"{'维度':<22}{'市场中位':>12}{'涨票raw':>12}{'涨票excess':>12}{'我们买入':>12}{'买入均分位%':>10}")
for dim, label, sgn in DIMS:
    s = summary[dim]
    f = lambda v: f"{v:12.4f}" if v is not None and pd.notna(v) else f"{'NA':>12}"
    print(f"{label:<22}{f(s['market_med'])}{f(s['winraw_med'])}{f(s['winex_med'])}{f(s['buys_med'])}{s['buys_mean_pct'] or 0:10.1f}")
print('\n价格分桶%:', json.dumps(summary['_price_bucket'], ensure_ascii=False, indent=1))
print('亏损股占比%:', summary['_loss_share'])
print('\n行业top10(涨票raw):', summary['_industry']['win_raw_top10'])
print('行业top10(我们买入):', summary['_industry']['buys_top10'])
print('\n入场: 买入日涨幅中位=%.2f%% 均值=%.2f%% | D-1乖离5日中位=%.2f%%' % (
    summary['_entry']['day_chg_buys_med'], summary['_entry']['day_chg_buys_mean'], summary['_entry']['bias5_buys_med']))
log('DONE')
