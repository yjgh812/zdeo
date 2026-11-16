# -*- coding: utf-8 -*-
"""PortraitAgent Top20 名单独立复算(只读): 0904/0907/0909/0910 四截面 short+mid 实际涨跌 + 9/4截面影子口径补评 + 0902/0903 history抽查.
口径:
  A 可实现口径: T+1开盘买/T+1收盘买, 持有到20260911收盘 (qfq复权)
  B shadow_gate口径: fwd5_high = max(hi[T+1..T+5])/close[T]-1 (9/4截面5窗口完整→补评; 0902/0903抽查对history独立验证)
  基准: 同截面池等权同口径. 数据源: workbuddyspace qfq 主库(只读) + portrait_*.json/scores CSV 存档(零模型重跑).
"""
import os, sys, json
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import sqlite3
from scipy.stats import spearmanr
from stock_name_lookup import get_name

HERE = r'D:\zcodesj\portrait_agent\reports'
QFQ = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
SECTIONS = ['20260904', '20260907', '20260909', '20260910']
SPOT_CHECK = ['20260902', '20260903']
LAST = '20260911'

def load_qfq(codes, d0='20260901', d1=LAST):
    con = sqlite3.connect(QFQ, uri=True, timeout=30)
    out = []
    for i in range(0, len(codes), 900):
        chunk = codes[i:i+900]
        ph = ','.join('?' * len(chunk))
        df = pd.read_sql_query(
            f"SELECT code,date,open,high,low,close FROM stock_daily_meta_qfq "
            f"WHERE code IN ({ph}) AND date>='{d0}' AND date<='{d1}' ORDER BY code,date",
            con, params=chunk)
        out.append(df)
    con.close()
    df = pd.concat(out, ignore_index=True)
    df['date'] = df['date'].astype(str)
    return df

def fwd5_high(g):
    """对齐 feature_engine 口径: max(hi[T+1..T+5])/close[T]-1"""
    hi = pd.Series(g['high'].values, index=g['date'].values)
    c = pd.Series(g['close'].values, index=g['date'].values)
    rev = hi[::-1].rolling(5).max()[::-1].shift(-1)
    return ((rev / c) - 1).to_dict()

results = {'sections': {}, 'spot_check': {}}
all_codes = set()
jsons, scores = {}, {}
for T in SECTIONS:
    jsons[T] = json.load(open(os.path.join(HERE, f'portrait_{T}.json'), encoding='utf-8'))
    for tag in ['short', 'mid']:
        sc = pd.read_csv(os.path.join(HERE, f'portrait_scores_{T}_{tag}.csv'), dtype={'code': str})
        scores[(T, tag)] = sc
        all_codes.update(sc['code'].tolist())
    # 0902/0903 抽查也需要全池分数
for T in SPOT_CHECK:
    p = os.path.join(HERE, f'portrait_scores_{T}_short.csv')
    if os.path.exists(p):
        sc = pd.read_csv(p, dtype={'code': str})
        scores[(T, 'short')] = sc
        all_codes.update(sc['code'].tolist())

print(f"全池代码 n={len(all_codes)}, 拉取 qfq {LAST} 前推10个交易日...")
qf = load_qfq(sorted(all_codes), d0='20260828')
by_code = {c: g for c, g in qf.groupby('code')}

def realized(c, T, lag_days=1):
    """发布滞后计入: json mtime=截面后第1个交易日15:16生成(15:15任务) → 最早可执行=第2个交易日开盘.
    lag_days=1: T+2开盘买(发布次日). 返回 (ret_open, ret_close, note)"""
    g = by_code.get(c)
    if g is None: return (np.nan, np.nan, '无行情')
    g2 = g[g['date'] > T]
    if len(g2) <= lag_days: return (np.nan, np.nan, '窗口不足')
    e_open, e_close = g2.iloc[lag_days]['open'], g2.iloc[lag_days]['close']
    last = g2.iloc[-1]
    if last['date'] != LAST: return (np.nan, np.nan, f"末bar={last['date']}非{LAST}(停牌?)")
    ro = last['close'] / e_open - 1 if e_open and e_open > 0 else np.nan
    rc = last['close'] / e_close - 1 if e_close and e_close > 0 else np.nan
    return (ro, rc, f"{g2.iloc[lag_days]['date']}买→{LAST}")

def fwd5(c, T):
    g = by_code.get(c)
    if g is None: return np.nan
    return fwd5_high(g).get(T, np.nan)

for T in SECTIONS:
    sec = {'date': T, 'models': {}}
    for tag in ['short', 'mid']:
        top = jsons[T]['models'][tag]['top20']
        rows = []
        for it in top:
            c = it['code']
            ro, rc, note = realized(c, T)
            f5 = fwd5(c, T)
            rows.append({'code': c, 'name': get_name(c) or '?', 'industry': it.get('行业', ''),
                         'close_T': it.get('收盘'), 'prob': round(it['分数'], 4),
                         'ret_open': None if pd.isna(ro) else round(ro * 100, 2),
                         'ret_close': None if pd.isna(rc) else round(rc * 100, 2),
                         'fwd5_high': None if pd.isna(f5) else round(f5 * 100, 2),
                         'note': note})
        df = pd.DataFrame(rows)
        # 池等权基准(同口径, 同截面全池)
        sc = scores[(T, tag)]
        pool_ro, pool_rc, pool_f5 = [], [], []
        for c in sc['code']:
            ro, rc, _ = realized(c, T)
            f5 = fwd5(c, T)
            if not pd.isna(ro): pool_ro.append(ro)
            if not pd.isna(rc): pool_rc.append(rc)
            if not pd.isna(f5): pool_f5.append(f5)
        ic = spearmanr(sc['prob'], [fwd5(c, T) for c in sc['code']]).statistic
        # shadow_gate 判定口径(hit/fwd5均值, 跌侧-10%截断)
        f5_top = np.array([fwd5(c, T) for c in df['code']], dtype=float)
        f5_top = f5_top[~np.isnan(f5_top)]
        hit = float((f5_top > 0).mean() * 100) if len(f5_top) else float('nan')
        port5 = float(np.clip(f5_top, -0.10, None).mean() * 100) if len(f5_top) else float('nan')
        base5 = float(np.clip(np.array(pool_f5), -0.10, None).mean() * 100) if pool_f5 else float('nan')
        # IC 剔除 fwd5 未实现(NaN)样本对后重算(spearmanr 不自动剔 NaN)
        pairs = [(p, f) for p, f in zip(sc['prob'], (fwd5(c, T) for c in sc['code']))
                 if not pd.isna(f) and not pd.isna(p)]
        ic = spearmanr([x[0] for x in pairs], [x[1] for x in pairs]).statistic if len(pairs) > 100 else float('nan')
        sec['models'][tag] = {
            'n_pool_realized': len(pool_ro),
            'pool_ret_open': round(float(np.mean(pool_ro)) * 100, 2),
            'pool_ret_close': round(float(np.mean(pool_rc)) * 100, 2),
            'pool_fwd5_high': round(base5, 2),
            'rankIC': round(float(ic), 4),
            'shadow_hit': round(hit, 1), 'shadow_port5': round(port5, 2),
            'top_ret_open_mean': round(float(df['ret_open'].dropna().mean()), 2),
            'top_ret_close_mean': round(float(df['ret_close'].dropna().mean()), 2),
            'top_excess_close': round(float(df['ret_close'].dropna().mean() - np.mean(pool_rc) * 100), 2),
            'rows': rows,
        }
    results['sections'][T] = sec
    s = sec['models']['short']
    print(f"[{T}] short: Top20收盘口径均值={s['top_ret_close_mean']}% vs 池{s['pool_ret_close']}% "
          f"超额={s['top_excess_close']}pp RankIC={s['rankIC']} hit={s['shadow_hit']}% port5={s['shadow_port5']} base5={s['pool_fwd5_high']}")

# 0902/0903 抽查: 与 shadow_state history 对账
state = json.load(open(r'D:\zcodesj\portrait_agent\models\shadow_state.json', encoding='utf-8'))
hist = {h['date']: h for h in state['history']}
for T in SPOT_CHECK:
    if (T, 'short') not in scores: continue
    sc = scores[(T, 'short')]
    f5s = [fwd5(c, T) for c in sc['code']]
    f5s = np.array([x for x in f5s if not pd.isna(x)])
    top_codes = [h for h in sc.nlargest(20, 'prob')['code']]
    f5_top = np.array([x for x in (fwd5(c, T) for c in top_codes) if not pd.isna(x)])
    ic = spearmanr(sc['prob'], [fwd5(c, T) for c in sc['code']]).statistic
    rec = hist.get(T, {})
    results['spot_check'][T] = {
        'history_port5': rec.get('port5'), 'recomputed_port5': round(float(np.clip(f5_top, -0.10, None).mean() * 100), 2),
        'history_base5': rec.get('base5'), 'recomputed_base5': round(float(np.clip(f5s, -0.10, None).mean() * 100), 2),
        'history_rankIC': rec.get('rankIC'), 'recomputed_rankIC': round(float(ic), 4),
    }
    print(f"[抽查{T}] history port5={rec.get('port5')} base5={rec.get('base5')} IC={rec.get('rankIC')} | 复算 port5={results['spot_check'][T]['recomputed_port5']} base5={results['spot_check'][T]['recomputed_base5']} IC={results['spot_check'][T]['recomputed_rankIC']}")

# 0904 截面面板法对照: 用 shadow_gate 同源 panel_v1.pkl 复算其 shadow 口径(与 qfq 独立法交叉验证数据链一致性)
try:
    panel = pd.read_pickle(r'D:\zcodesj\portrait_agent\cache\panel_v1.pkl')
    T = '20260904'
    cs = panel[panel['date'] == T].copy()
    cs = cs[cs['fwd5_high'].notna() & (~cs['lim_today'])]
    if len(cs) > 500:
        sc = scores[(T, 'short')]
        cs = cs.merge(sc, on='code', how='inner')
        top = cs.nlargest(20, 'prob')
        hit_p = float((top['fwd5_high'] > 0).mean() * 100)
        port5_p = float(top['fwd5_high'].clip(lower=-0.10).mean() * 100)
        base5_p = float(cs['fwd5_high'].clip(lower=-0.10).mean() * 100)
        ic_p = float(spearmanr(cs['prob'], cs['fwd5_high']).statistic)
        results['panel_cross_check'] = {'date': T, 'n': int(len(cs)), 'hit': round(hit_p, 1),
                                        'port5': round(port5_p, 2), 'base5': round(base5_p, 2), 'rankIC': round(ic_p, 4)}
        print(f"[面板法对照{T}] n={len(cs)} hit={hit_p:.1f}% port5={port5_p:.2f} base5={base5_p:.2f} IC={ic_p:.4f}")
except Exception as e:
    results['panel_cross_check'] = {'error': f'{type(e).__name__}: {e}'}
    print(f"[面板法对照] 失败: {e}")

json.dump(results, open(r'D:\zcodesj\data\_portrait_top20_eval_20260913.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=float)
print("已落盘 data/_portrait_top20_eval_20260913.json")
