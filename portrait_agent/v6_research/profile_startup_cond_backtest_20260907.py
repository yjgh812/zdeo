# -*- coding: utf-8 -*-
"""画像启动条件 PIT 化回测(2026-09-07, 预注册 PREREG_PROFILE_STARTUP_COND_20260907 的执行器).

假设H1(跑前冻结): T日收盘满足 pos250>=0.80 且 ret60当日截面分位>=0.75 且 量比(vol5d/vol20d)∈[0.8,1.5]
→ T+1收盘买入持有20日(T+21收盘卖出), 超额=个股fwd20 - 同日universe等权fwd20, 均值为正.
样本窗: 信号日2016-2024(9年); holdout 2025-2026(画像来源窗, 仅报告不判定).
universe: 60/00/30前缀, 剔名含ST/退(静态近似), 剔上市<60交易日, 剔停牌.
统计: 按信号日聚合日序列, NW(5) t, 分年; 敏感性9变体全量呈报不挑选.
"""
import os, sys, json, time, sqlite3, hashlib
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
PREREG = r'D:\zcodesj\data\PREREG_PROFILE_STARTUP_COND_20260907.json'
OUT = r'D:\zcodesj\portrait_agent\reports\profile_startup_cond_backtest_20260907.json'
T0 = time.time()
def log(m): print(f'[{time.time()-T0:6.1f}s] {m}', flush=True)

CONF = json.load(open(PREREG, encoding='utf-8'))
fp = hashlib.sha256(json.dumps(CONF, sort_keys=True).encode()).hexdigest()[:16]
log(f'预注册指纹 {fp}')

# ── universe: 剔ST/退(当前名静态) ──
con = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
ind = pd.read_sql_query('select code, name from industry_map_ths', con)
con.close()
st_codes = set(ind[ind['name'].fillna('').str.contains('ST|退', regex=True)]['code'])
log(f'ST/退剔除(静态): {len(st_codes)}只')

# ── 数据: 2014-07起(pos250预热250日+缓冲), 终点20260904(holdout含) ──
con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
df = pd.read_sql_query(
    "SELECT code,date,high,low,close,volume FROM stock_daily_meta_qfq "
    "WHERE date>='20140701' AND date<='20260904'", con)
con.close()
df = df[df['code'].str[:3].isin(('600', '601', '603', '605', '000', '001', '002', '003', '300', '301'))]
df = df[~df['code'].isin(st_codes)]
df = df.sort_values(['code', 'date']).reset_index(drop=True)
log(f'rows={len(df)} stocks={df.code.nunique()}')

# ── 信号条件(向量化, T日收盘可得) ──
df['lo250'] = df.groupby('code')['low'].transform(lambda s: s.rolling(250, min_periods=200).min())
df['hi250'] = df.groupby('code')['high'].transform(lambda s: s.rolling(250, min_periods=200).max())
df['pos250'] = (df['close'] - df['lo250']) / (df['hi250'] - df['lo250'])
df['ret60'] = df.groupby('code')['close'].transform(lambda s: s / s.shift(60) - 1)
df['vr'] = df.groupby('code')['volume'].transform(lambda s: s.rolling(5).mean() / s.rolling(20).mean())
df['bars_since_ipo'] = df.groupby('code').cumcount() + 1
# ret60 当日截面分位
df['ret60_rank'] = df.groupby('date')['ret60'].rank(pct=True)
# next-bar fwd20
df['fwd20'] = df.groupby('code')['close'].transform(lambda s: s.shift(-21) / s.shift(-1) - 1)
log('特征计算完成')

# 市场等权基准(逐日, universe全体可交易行)
mkt_fwd = df[df['volume'] > 0].groupby('date')['fwd20'].mean()

# ── 回测函数(参数化, 敏感性复用) ──
def run(pos_thr, vr_lo, vr_hi, sig_lo='20160101', sig_hi='20241231', label=''):
    m = ((df['pos250'] >= pos_thr) & (df['ret60_rank'] >= 0.75) &
         (df['vr'] >= vr_lo) & (df['vr'] <= vr_hi) &
         (df['volume'] > 0) & (df['bars_since_ipo'] > 60) &
         (df['date'] >= sig_lo) & (df['date'] <= sig_hi) &
         df['fwd20'].notna() & df['pos250'].notna())
    sig = df[m].copy()
    if sig.empty:
        return {'label': label, 'n_sig': 0}
    sig['excess'] = sig['fwd20'] - sig['date'].map(mkt_fwd)
    daily = sig.groupby('date')['excess'].mean()  # 信号日序列
    x = daily.values
    # NW(5) t
    n = len(x); xb = x.mean()
    e = x - xb
    l = 5
    g0 = (e * e).sum() / n
    nw = g0
    for k in range(1, l + 1):
        gk = (e[k:] * e[:-k]).sum() / n
        nw += 2 * (1 - k / (l + 1)) * gk
    se = np.sqrt(nw / n)
    t = xb / se if se > 0 else 0
    yr = pd.Series(daily.index).str[:4].values
    ydf = pd.DataFrame({'yr': yr, 'ex': x}).groupby('yr')['ex'].agg(['mean', 'count'])
    pos_years = int((ydf['mean'] > 0).sum())
    return {'label': label, 'n_sig': len(sig), 'n_days': n, 'ex_mean_pct': round(xb * 100, 3),
            't_nw5': round(float(t), 2), 'win_days_pct': round(float((x > 0).mean() * 100), 1),
            'pos_years': pos_years, 'years': {i: round(r['mean'] * 100, 2) for i, r in ydf.iterrows()},
            'yearly_span': f"{ydf.index.min()}~{ydf.index.max()}"}

# ── 主口径(跑前锁定参数) ──
main = run(0.80, 0.8, 1.5, label='MAIN pos0.80 vr[0.8,1.5]')
log(f'主口径: {json.dumps({k: v for k, v in main.items() if k != "years"}, ensure_ascii=False)}')

# ── holdout(2025-2026画像来源窗, 仅报告) ──
hold = run(0.80, 0.8, 1.5, sig_lo='20250101', sig_hi='20260904', label='HOLDOUT 2025-2026')
log(f'holdout: n={hold.get("n_sig")} mean={hold.get("ex_mean_pct")}% t={hold.get("t_nw5")}')

# ── 敏感性9变体(全量呈报不挑选) ──
sens = []
for p in (0.75, 0.80, 0.85):
    for vlo, vhi in ((0.7, 1.4), (0.8, 1.5), (0.9, 1.6)):
        sens.append(run(p, vlo, vhi, label=f'pos{p} vr[{vlo},{vhi}]'))
robust_pos = sum(1 for s in sens if s.get('t_nw5', -9) >= 2)
robust_posy = sum(1 for s in sens if s.get('pos_years', 0) >= 7)
log(f'敏感性: t>=2 的变体 {robust_pos}/9; 正超额年>=7 的变体 {robust_posy}/9')

# ── 判据(跑前锁定) ──
t_main = main.get('t_nw5', -9); py_main = main.get('pos_years', 0)
if t_main >= 2 and py_main >= 7:
    verdict = 'PASS_CANDIDATE'
elif t_main < 2 or py_main < 5:
    verdict = 'FAIL'
else:
    verdict = 'OBSERVATION'
log(f'判定: {verdict} (t={t_main}, 正超额年={py_main}/9)')

result = {'prereg_id': CONF['prereg_id'], 'fingerprint': fp, 'verdict': verdict,
          'main': main, 'holdout': {k: hold.get(k) for k in ('n_sig', 'ex_mean_pct', 't_nw5', 'pos_years')},
          'sensitivity': [{k: s.get(k) for k in ('label', 'n_sig', 'ex_mean_pct', 't_nw5', 'pos_years')} for s in sens],
          'sensitivity_robust': {'t_ge2': f'{robust_pos}/9', 'posyears_ge7': f'{robust_posy}/9'}}
json.dump(result, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
log(f'落盘 {OUT}')
