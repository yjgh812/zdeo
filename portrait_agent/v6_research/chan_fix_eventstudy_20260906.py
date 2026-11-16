# -*- coding: utf-8 -*-
"""P2 修复验证 · 缠论买点事件研究 v1(中心日/带前视) vs v2(确认日/无前视)。
判例口径: SKILL-SWEEP2 右移1bar 崩塌测试同题。产出: reports/P2_CHAN_FIX_EVENTSTUDY_20260906.json/md"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
N = 20  # 20交易日持有(对齐组合C)

def load(pkl):
    df = pd.read_pickle(pkl)
    return df[df['type'].isin(['buy1', 'buy2'])].copy()

def fwd20(df, close):
    df = df.sort_values(['code', 'date']).reset_index(drop=True)
    close = close.sort_values(['code', 'date']).reset_index(drop=True)
    close['seq'] = close.groupby('code').cumcount()
    seq_map = {(c, d): q for c, d, q in zip(close['code'], close['date'], close['seq'])}
    df['seq'] = [seq_map.get((c, d), np.nan) for c, d in zip(df['code'], df['date'])]
    df = df.dropna(subset=['seq'])
    df['seq'] = df['seq'].astype(int)
    px = {(c, q): x for c, q, x in zip(close['code'], close['seq'], close['close'])}
    out = []
    for r in df.itertuples():
        a = px.get((r.code, r.seq + N))
        b = px.get((r.code, r.seq))
        if a is not None and b is not None:
            out.append((r.type, a / b - 1))
    return pd.DataFrame(out, columns=['type', 'fwd'])

def stats(df):
    res = {}
    for t, g in df.groupby('type'):
        s = g['fwd'].dropna()
        t_stat = s.mean() / (s.std(ddof=1) / len(s) ** 0.5) if len(s) > 2 else np.nan
        res[t] = {'n': len(s), 'mean': round(float(s.mean()), 5),
                  't': round(float(t_stat), 2), 'hit': round(float((s > 0).mean()), 3)}
    return res

con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
close = pd.read_sql("SELECT code,date,close FROM stock_daily_meta_qfq WHERE substr(code,1,3) IN "
                    "('600','601','603','605','000','001','002','003','300','301','302','688','689')", con)
con.close()
close['date'] = close['date'].astype(str)

v1 = load(os.path.join(HERE, 'cache', 'chan_signals_v1.pkl'))
v2 = load(os.path.join(HERE, 'cache', 'chan_signals_v2.pkl'))
print(f"事件数: v1={len(v1)} v2={len(v2)}")
r1 = fwd20(v1, close)
r2 = fwd20(v2, close)
s1, s2 = stats(r1), stats(r2)
out = {'N': N, 'v1_center': {'events': len(v1), **s1},
       'v2_confirm': {'events': len(v2), **s2},
       'note': 'v1=分型中心日(带前视,已废弃) v2=确认日(第3根merged K); fwd20=close[D+20]/close[D]-1'}
print(json.dumps(out, ensure_ascii=False, indent=1))
json.dump(out, open(os.path.join(HERE, 'reports', 'P2_CHAN_FIX_EVENTSTUDY_20260906.json'), 'w',
                    encoding='utf-8'), ensure_ascii=False, indent=1)
md = f"""# P2 修复验证 · 缠论买点事件研究（v1 中心日 vs v2 确认日，N={N}）

| 口径 | 类型 | 事件数 | fwd均值 | t | 命中率 |
|---|---|---|---|---|---|
| v1 中心日(带前视) | buy1 | {s1.get('buy1', {}).get('n')} | {s1.get('buy1', {}).get('mean')} | {s1.get('buy1', {}).get('t')} | {s1.get('buy1', {}).get('hit')} |
| v1 中心日(带前视) | buy2 | {s1.get('buy2', {}).get('n')} | {s1.get('buy2', {}).get('mean')} | {s1.get('buy2', {}).get('t')} | {s1.get('buy2', {}).get('hit')} |
| **v2 确认日(无前视)** | buy1 | {s2.get('buy1', {}).get('n')} | **{s2.get('buy1', {}).get('mean')}** | **{s2.get('buy1', {}).get('t')}** | {s2.get('buy1', {}).get('hit')} |
| **v2 确认日(无前视)** | buy2 | {s2.get('buy2', {}).get('n')} | **{s2.get('buy2', {}).get('mean')}** | **{s2.get('buy2', {}).get('t')}** | {s2.get('buy2', {}).get('hit')} |

- 判读：v2 相对 v1 的衰减幅度=前视贡献占比；v2 若仍 |t|≥2 → 缠论成分修复后可回组合C（新指纹重登记）；崩塌 → chan 成分判死，组合C 仅存 island/gap_up+负过滤族另行评估。
"""
open(os.path.join(HERE, 'reports', 'P2_CHAN_FIX_EVENTSTUDY_20260906.md'), 'w', encoding='utf-8').write(md)
print('落盘 reports/P2_CHAN_FIX_EVENTSTUDY_20260906.md/.json')
