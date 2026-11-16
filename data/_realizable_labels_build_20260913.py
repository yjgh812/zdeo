# -*- coding: utf-8 -*-
"""y_realizable 标签生成(只读衍生): 从qfq全史算 ret(T+2开盘→+5日收盘), 存 parquet 供变体训练.
向量化: entry=bars.groupby(code)['open'].shift(-2), exit=groupby['close'].shift(-6), ret=exit/entry-1.
零填充/停牌保护同 ret_at 语义. 含与 ret_at 抽样一致性校验."""
import sys, sqlite3, time
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd

T0 = time.time()
def log(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)

QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
D0, D1 = '20230116', '20260911'

con = sqlite3.connect(QFQ, uri=True, timeout=180)
codes = [r[0] for r in con.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20230116'").fetchall()]
log(f'代码数={len(codes)}')
chunks = []
for i in range(0, len(codes), 700):
    chunk = codes[i:i+700]
    ph = ','.join('?' * len(chunk))
    chunks.append(pd.read_sql_query(
        f"SELECT code,date,open,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='{D0}' AND date<='{D1}' ORDER BY code,date",
        con, params=chunk))
con.close()
bars = pd.concat(chunks, ignore_index=True)
bars['date'] = bars['date'].astype(str)
log(f'bars {len(bars)}行')

g = bars.groupby('code', sort=False)
entry_open = g['open'].shift(-2)
exit_close = g['close'].shift(-6)
ret = exit_close / entry_open - 1
# 零值/停牌保护(与 ret_at 一致: open或close为0/NaN → NaN)
bad = (bars['open'] == 0) | (bars['close'] == 0) | bars['open'].isna() | bars['close'].isna()
ret[bad | ret.isna()] = np.nan
lab = pd.DataFrame({'code': bars['code'], 'date': bars['date'], 'ret_realizable': ret})
lab = lab.dropna(subset=['ret_realizable'])
log(f'标签行 {len(lab)} (覆盖 {lab["code"].nunique()} 只, {lab["date"].min()}~{lab["date"].max()})')
lab.to_parquet(r'D:\zcodesj\data\_realizable_labels_20260913.parquet', index=False)
print(f'分布: 均值={lab["ret_realizable"].mean()*100:.3f}% 中位={lab["ret_realizable"].median()*100:.3f}% ≥3%占比={(lab["ret_realizable"]>=0.03).mean()*100:.1f}%')
print(f'落盘 data/_realizable_labels_20260913.parquet')

# === 抽样一致性校验: 与 ret_at 函数逐行对拍(20只随机) ===
print('\n=== 与 ret_at 语义对拍 ===')
rng = np.random.default_rng(42)
sample_codes = rng.choice(codes, 20, replace=False)
bar_by_code = {c: d.reset_index(drop=True) for c, d in bars[bars['code'].isin(sample_codes)].groupby('code')}
def ret_at_manual(c, T):
    d = bar_by_code.get(c)
    if d is None: return np.nan
    idx = d.index[d['date'] > T]
    if len(idx) < 6: return np.nan
    e, x = d.loc[idx[1]], d.loc[idx[5]]
    if not e['open'] or not x['close']: return np.nan
    return x['close'] / e['open'] - 1
mism = 0; checked = 0
for c in sample_codes:
    d = bar_by_code.get(c)
    if d is None: continue
    lab_c = lab[lab['code'] == c].set_index('date')['ret_realizable']
    for dt in d['date'].iloc[::37]:  # 每只抽约24个日期
        if dt not in lab_c.index: continue
        a = ret_at_manual(c, dt); b = lab_c.loc[dt]
        checked += 1
        if abs(a - b) > 1e-12:
            mism += 1
            if mism <= 3: print(f'  差异: {c} {dt} manual={a:.10f} label={b:.10f}')
print(f'对拍: checked={checked} mism={mism} → {"✅ 一致" if mism == 0 else "❌ 有差异"}')
