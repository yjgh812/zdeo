# -*- coding: utf-8 -*-
"""每日涨票画像观测层(只读,零接线): 全市场特征分位快照 + 持仓落点诊断 vs 冻结涨票基准.
用法: python winner_profile_daily.py [--date YYYYMMDD] [--positions "code,code,..."]
  --date 缺省=qfq最大日期; --positions 缺省=data/positions_latest.json
基准: attribution/winner_profile_out/winner_profile_result.json(2026-09-04冻结, 11截面涨票raw前10%画像)
产物: reports/winner_profile_daily/winner_profile_{T}.csv + .md
宪法口径: 主板+创业板(剔科创/北交/ST/次新<150bar); 纯观测不评分不接线(9/5主人裁决).
"""
import sqlite3, json, os, sys, time, argparse
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

QFQ_DB  = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
BASE    = r'D:\zcodesj\attribution\winner_profile_out\winner_profile_result.json'
OUT_DIR = r'D:\zcodesj\reports\winner_profile_daily'
TRAD_PREFIX = ('600','601','603','605','000','001','002','003','300','301','302')
CHINEXT = ('300','301','302')
os.makedirs(OUT_DIR, exist_ok=True)

ap = argparse.ArgumentParser()
ap.add_argument('--date', default=None)
ap.add_argument('--positions', default=None)
args = ap.parse_args()

# ---------- 数据装载 ----------
con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
mx = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
T = args.date or mx
if T > mx: T = mx  # 幂等: 数据未到则用最近完整日
log(f'T={T} (qfq max={mx})')
warm = (pd.Timestamp(T) - pd.Timedelta(days=520)).strftime('%Y%m%d')
alive = [r[0] for r in con.execute(
    "SELECT code FROM stock_daily_meta_qfq WHERE date>=? GROUP BY code", (T,)
) if False]  # placeholder
alive = [r[0] for r in con.execute(
    f"SELECT code FROM stock_daily_meta_qfq WHERE date>='{T}' GROUP BY code"
)] if False else None
# 存活=T日有bar 的可交易票
alive = [r[0] for r in con.execute(
    "SELECT code FROM stock_daily_meta_qfq WHERE date=? GROUP BY code", (T,)
) if r[0][:3] in TRAD_PREFIX]
ph = ','.join('?'*len(alive))
qfq = pd.read_sql_query(
    f"SELECT code,date,close,high,low,change_pct,amount FROM stock_daily_meta_qfq "
    f"WHERE date>='{warm}' AND date<='{T}' AND code IN ({ph})", con, params=alive)
con.close()
log(f'qfq rows={len(qfq)} alive_tradable={len(alive)}')

con2 = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
db = pd.read_sql_query(
    "SELECT ts_code,trade_date,total_mv,circ_mv,turnover_rate,pe_ttm,pb FROM daily_basic "
    f"WHERE trade_date>='{(pd.Timestamp(T)-pd.Timedelta(days=20)).strftime('%Y%m%d')}' AND trade_date<='{T}'", con2)
ind = pd.read_sql_query("SELECT code,name,industry FROM industry_map_ths", con2)
con2.close()
db['code'] = db['ts_code'].str[:6]
for c in ['total_mv','circ_mv','turnover_rate','pe_ttm','pb']:
    db[c] = pd.to_numeric(db[c], errors='coerce')
ind['L1'] = ind['industry'].fillna('未知').str.split('-').str[0]
ind['is_st'] = ind['name'].fillna('').str.contains('ST') | ind['name'].fillna('').str.contains('退')
st_set = set(ind.loc[ind['is_st'], 'code'])
name_map = dict(zip(ind['code'], ind['name']))
db_by_code = {c: x.set_index('trade_date')[['total_mv','circ_mv','turnover_rate','pe_ttm','pb']]
              for c, x in db.groupby('code')}

# ---------- 特征(与画像主脚本同口径) ----------
def ema_sma_seed(a, p):
    out = np.full(len(a), np.nan)
    if len(a) < p: return out
    out[p-1] = a[:p].mean(); m = 2.0/(p+1)
    for i in range(p, len(a)):
        out[i] = a[i]*m + out[i-1]*(1-m)
    return out
def macd_skill(c):
    n = len(c); e12 = ema_sma_seed(c,12); e26 = ema_sma_seed(c,26)
    dif = np.full(n, np.nan)
    if n >= 26: dif[25:] = e12[25:]-e26[25:]
    d9 = ema_sma_seed(dif[25:],9) if n>=26 else None
    dea = np.full(n, np.nan)
    if d9 is not None and len(d9)>=9 and not np.isnan(d9[8]):
        dea[33:] = d9[8:]
    return dif, dea

qfq = qfq.sort_values(['code','date'])
rows = []
for code, g in qfq.groupby('code', sort=False):
    g = g.drop_duplicates('date').reset_index(drop=True)
    c = g['close'].values.astype(float)
    hi = g['high'].values.astype(float); lo = g['low'].values.astype(float)
    chg = g['change_pct'].values.astype(float)
    amt = g['amount'].values.astype(float)
    if len(c) < 150: continue
    r = g['date'].values
    j = int(np.where(r == T)[0][0]) if T in r else -1
    if j < 0: continue
    s = pd.Series(c)
    def at(x): return float(x.iloc[j]) if pd.notna(x.iloc[j]) else np.nan
    ret = s.pct_change()
    lim_thr = 19.8 if code[:3] in CHINEXT else 9.8
    lim = chg >= lim_thr
    streak = np.zeros(len(c)); best = 0.0; cur = 0
    for i in range(len(c)):
        cur = cur+1 if lim[i] else 0
        if cur > best: best = cur
        streak[i] = best
    dif, dea = macd_skill(c)
    pos = dif > dea
    cross = np.where(pos & ~np.roll(pos,1))[0]
    cross = cross[cross > 0]
    bsg = (j - cross[cross <= j].max()) if len(cross) and (cross <= j).any() else np.nan
    amp = (hi-lo)/np.roll(c,1); amp[0]=np.nan
    med120 = s.rolling(120, min_periods=60).median()
    row = {'code': code, 'name': name_map.get(code, ''), 'close': c[j],
           'log_mv': np.log10(at(pd.Series(np.nan))) if False else None,
           'ret20': (c[j]/c[j-20]-1) if j>=20 else np.nan,
           'ret60': (c[j]/c[j-60]-1) if j>=60 else np.nan,
           'vol60': float(ret.rolling(60).std().iloc[j]),
           'turnover20': None, 'pe_ttm': None, 'pb': None, 'circ_mv': None, 'total_mv': None,
           'amp20': float(pd.Series(amp).rolling(20).mean().iloc[j]),
           'pos250': float(((s-s.rolling(250,min_periods=60).min())/(s.rolling(250,min_periods=60).max()-s.rolling(250,min_periods=60).min())).iloc[j]),
           'dist_hi': float((s/s.rolling(250,min_periods=60).max()-1).iloc[j]),
           'bias20': float((s/s.rolling(20).mean()-1).iloc[j]),
           'nh20_cnt': float((s.rolling(20).max()==s).rolling(20).sum().iloc[j]),
           'lim_cnt250': float(pd.Series(lim.astype(float)).rolling(250,min_periods=60).sum().iloc[j]),
           'lim_cnt60': float(pd.Series(lim.astype(float)).rolling(60,min_periods=20).sum().iloc[j]),
           'dif': dif[j], 'hist': (dif[j]-dea[j]) if not np.isnan(dea[j]) else np.nan,
           'bars_golden': bsg,
           'dif_pos60': float(pd.Series(np.where(np.isnan(dif), np.nan, (dif>0).astype(float))).rolling(60,min_periods=30).mean().iloc[j]),
           'pos_zs': float((s/med120-1).iloc[j]),
           'amt5_amt20': float(pd.Series(amt).rolling(5).mean().iloc[j] / pd.Series(amt).rolling(20).mean().iloc[j]) if j>=20 and pd.Series(amt).rolling(20).mean().iloc[j] > 0 else np.nan,
           'maxstreak': streak[j],
           'bar_no': j}
    b = db_by_code.get(code)
    if b is not None and len(b):
        bb = b[b.index <= T]
        if len(bb):
            v = bb.iloc[-1]
            d_gap = (pd.Timestamp(T) - pd.Timestamp(bb.index[-1])).days
            row['turnover20'] = float(bb['turnover_rate'].tail(20).mean())
            row['pe_ttm'] = float(v['pe_ttm']) if pd.notna(v['pe_ttm']) else np.nan
            row['pb'] = float(v['pb']) if pd.notna(v['pb']) else np.nan
            row['total_mv'] = float(v['total_mv']) if pd.notna(v['total_mv']) else np.nan
            row['circ_mv'] = float(v['circ_mv']) if pd.notna(v['circ_mv']) else np.nan
            row['log_mv'] = np.log10(v['total_mv']) if pd.notna(v['total_mv']) else np.nan
            row['fund_gap_days'] = d_gap
    rows.append(row)
df = pd.DataFrame(rows)
df = df[(~df['code'].isin(st_set)) & (df['bar_no']>=150) & df['close'].notna() & df['total_mv'].notna()].copy()
log(f'section n={len(df)} (ST剔除后, fund覆盖)')

# ---------- 分位(截面内) ----------
DIMS = ['log_mv','turnover20','ret20','ret60','vol60','amp20','pos250','dist_hi','bias20',
        'nh20_cnt','lim_cnt250','lim_cnt60','dif','hist','bars_golden','dif_pos60','pos_zs','pb','pe_ttm',
        'amt5_amt20','maxstreak']
for d in DIMS:
    df[f'p_{d}'] = df[d].rank(pct=True)*100

# ---------- 冻结基准对照 ----------
S = json.load(open(BASE, encoding='utf-8'))['summary']
base_map = {d: (S[d]['winraw_med'], S[d]['market_med']) for d in DIMS if d in S and not str(d).startswith('_')}

# ---------- 持仓落点 ----------
if args.positions == 'NONE':
    hold = []
elif args.positions:
    hold = [c.strip() for c in args.positions.split(',') if c.strip()]
elif os.path.exists(r'D:\zcodesj\data\positions_latest.json'):
    try:
        pj = json.load(open(r'D:\zcodesj\data\positions_latest.json', encoding='utf-8'))
        hold = [str(p.get('code',''))[-6:] for p in (pj if isinstance(pj, list) else pj.get('positions', []))]
    except Exception:
        hold = []
else:
    hold = []
df_idx = df.set_index('code')
lines = []
lines.append(f'# 涨票画像每日观测 {T}（只读·零接线）')
lines.append('')
lines.append(f'> 生成于 {time.strftime("%Y-%m-%d %H:%M")} | 截面n={len(df)}(主板+创业板,剔ST/次新) | 基准=2026-09-04冻结的1年涨票画像(11截面raw前10%中位) | 数据覆盖: qfq到{T}')
lines.append('')
lines.append('## 市场特征面状态(当日截面中位 vs 冻结基准)')
lines.append('')
lines.append('| 维度 | 当日市场中位 | 冻结涨票中位 | 冻结市场中位 | 当日-涨票差 |')
lines.append('|---|---|---|---|---|')
for d in DIMS:
    if d not in base_map: continue
    w, m = base_map[d]
    cur = df[d].median()
    lines.append(f"| {d} | {cur:.3f} | {w:.3f} | {m:.3f} | {cur-w:+.3f} |")
lines.append('')
lines.append(f"亏损股占比: 当日 {((df['pe_ttm']<0)|df['pe_ttm'].isna()).mean()*100:.1f}% (冻结涨票24.7%)")
lines.append('')
if hold:
    lines.append(f'## 持仓落点诊断(n={len(hold)})')
    lines.append('')
    lines.append('| 代码 | 名称 | 换手分位 | 市值分位 | 动量20分位 | 波动分位 | 位置分位 | PE分位 | 涨停250分位 |')
    lines.append('|---|---|---|---|---|---|---|---|---|---|')
    sim_detail = []
    for cd in hold:
        if cd not in df_idx.index:
            lines.append(f"| {cd} | {name_map.get(cd,'')} | 截面无数据 | | | | | | | |"); continue
        r = df_idx.loc[cd]
        f = lambda v: f"{v:.0f}" if pd.notna(v) else "NA"
        lines.append(f"| {cd} | {r['name']} | {f(r['p_turnover20'])} | {f(r['p_log_mv'])} | {f(r['p_ret20'])} | {f(r['p_vol60'])} | {f(r['p_pos250'])} | {f(r['p_pe_ttm'])} | {f(r['p_lim_cnt250'])} |")
    lines.append('')
lines.append('')
lines.append('⚠️ 本产物为纯观测: 画像特征面差异是描述性事实, 不构成买入/卖出依据(9/5主人裁决+§8/§10追热证伪).')
md = '\n'.join(lines)
open(os.path.join(OUT_DIR, f'winner_profile_{T}.md'), 'w', encoding='utf-8').write(md)
df.to_csv(os.path.join(OUT_DIR, f'winner_profile_{T}.csv'), index=False, encoding='utf-8-sig')
log(f'saved {T} md+csv; holdings={len(hold)}')
# 2026-09-24 保留窗(二轮垃圾审计根治): 滚动快照只留最近10个交易日(csv+md各10), 防每日3.3M无轮转累积
try:
    _snaps = sorted(f for f in os.listdir(OUT_DIR) if f.startswith('winner_profile_2'))
    for _f in _snaps[:-20]:
        os.remove(os.path.join(OUT_DIR, _f))
        log(f'保留窗: 清旧快照 {_f}')
except OSError:
    pass
print(f'\n{md[:1500]}')
