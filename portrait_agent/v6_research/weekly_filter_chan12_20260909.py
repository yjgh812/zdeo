# -*- coding: utf-8 -*-
"""周线方向过滤对日线缠论一二买的增量验证（prereg=20260909-weekly-filter-chan12-v1, 指纹75f5775b6d71d8c5）。

假设 H1: 周线趋势代理（周收盘>周MA20 且 周MA20上行，仅用信号日前已完整周K）过滤后的
chan buy1+buy2 信号相对同批无过滤信号有显著 fwd20 增量。
口径锁定见 data/prereg_weekly_filter_chan12_20260909.json；样本外 2026 段须 --oos 且仅限主检验过闸后跑一次。
用法: python weekly_filter_chan12_20260909.py [--oos] [--out PATH]
产出: reports/WEEKLY_FILTER_CHAN12_20260909.{json,md}
"""
import os, sys, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
SIGNAL_PKL = os.path.join(HERE, 'cache', 'chan_signals_v2.pkl')
PREREG = r'D:\zcodesj\data\prereg_weekly_filter_chan12_20260909.json'
MAIN_START, MAIN_END = '20160101', '20251231'
OOS_START, OOS_END = '20260101', '20260831'
HORIZONS = [5, 10, 20]


def welch_t(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or len(b) < 3:
        return np.nan
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    return (a.mean() - b.mean()) / np.sqrt(va + vb)


def load_close(start):
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    df = pd.read_sql(
        "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>=? AND substr(code,1,3) IN "
        "('600','601','603','605','000','001','002','003','300','301','302')", con, params=(start,))
    con.close()
    df['date'] = df['date'].astype(str)
    return df.sort_values(['code', 'date']).reset_index(drop=True)


def eligible_codes(df):
    return df  # 前缀过滤已在 SQL 完成（剔688/689与北交前缀）


def add_fwd(close):
    close = close.copy()
    close['seq'] = close.groupby('code').cumcount()
    for n in HORIZONS:
        close[f'fwd{n}'] = close.groupby('code')['close'].shift(-n) / close['close'] - 1
    return close


def weekly_filter_map(close_sig, ma_win):
    """对信号涉及 code 构建周线过滤布尔表 (code, week_end, pass_filter)。
    周K=日线 resample W-FRI；cond = close_w>MA_n 且 MA_n 上行；仅完整周（MA_n 非NaN）。"""
    cs = close_sig.copy()
    cs['ts'] = pd.to_datetime(cs['date'], format='%Y%m%d')
    wk = (cs.groupby(['code', pd.Grouper(key='ts', freq='W-FRI')])
            .agg(week_end=('date', 'max'), close_w=('close', 'last'))
            .dropna(subset=['close_w']).reset_index())
    wk = wk.sort_values(['code', 'week_end']).reset_index(drop=True)
    wk['ma'] = wk.groupby('code')['close_w'].transform(lambda s: s.rolling(ma_win).mean())
    wk['ma_prev'] = wk.groupby('code')['ma'].shift(1)
    wk['pass_filter'] = (wk['close_w'] > wk['ma']) & (wk['ma'] > wk['ma_prev'])
    out = wk[['code', 'week_end', 'pass_filter']].dropna(subset=['pass_filter']).copy()
    out['week_end'] = pd.to_datetime(out['week_end'], format='%Y%m%d')
    return out


def map_signals(signals, wk):
    """merge_asof: 每个信号配到 week_end<=信号日 的最近周（D 恰为周收盘日时含当周）。"""
    sg = signals.copy()
    sg['ts'] = pd.to_datetime(sg['date'], format='%Y%m%d')
    w = wk.copy()
    w = w.rename(columns={'week_end': 'ts'})
    sg = sg.sort_values('ts')
    w = w.sort_values('ts')
    m = pd.merge_asof(sg, w, on='ts', by='code', direction='backward')
    m['pass_filter'] = m['pass_filter'].fillna(False).astype(bool)
    return m


def group_stats(df, col):
    s = df[col].dropna()
    if len(s) < 3:
        return {'n': int(len(s)), 'mean': None, 't': None, 'hit': None}
    t_stat = s.mean() / (s.std(ddof=1) / len(s) ** 0.5)
    return {'n': int(len(s)), 'mean': round(float(s.mean()), 5),
            't': round(float(t_stat), 2), 'hit': round(float((s > 0).mean()), 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--oos', action='store_true', help='样本外段(须主检验过闸后仅跑一次)')
    ap.add_argument('--ma', type=int, default=20, help='周线MA窗(20=主口径; 10/30=prereg预设敏感性变体)')
    ap.add_argument('--types', default='buy1,buy2', help='缠论买点类型(逗号分隔)')
    ap.add_argument('--prereg', default=PREREG, help='prereg登记文件路径')
    ap.add_argument('--subperiod', action='store_true', help='闸门改用两段方向一致(小样本buy3口径)')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    ma = a.ma
    types = tuple(t.strip() for t in a.types.split(',') if t.strip())
    if not a.oos and ma not in (10, 20, 30):
        raise SystemExit(f'--ma={ma} 不在 prereg 预算内(仅10/20/30)')
    start, end = (OOS_START, OOS_END) if a.oos else (MAIN_START, MAIN_END)
    tag = 'OOS' if a.oos else 'MAIN'

    prereg = json.load(open(a.prereg, encoding='utf-8'))

    sig_all = pd.read_pickle(SIGNAL_PKL)
    sig = sig_all[sig_all['type'].isin(types)].copy()
    sig['code'] = sig['code'].astype(str).str.zfill(6)
    sig['date'] = sig['date'].astype(str)
    sig = sig[(sig['date'] >= start) & (sig['date'] <= end)]

    # 次新<60交易日剔除: 用全库 min(date) 判定（不受读窗截断影响）
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    first = pd.read_sql("SELECT code,MIN(date) first_date FROM stock_daily_meta_qfq GROUP BY code", con)
    con.close()
    first['code'] = first['code'].astype(str).str.zfill(6)
    sig = sig.merge(first, on='code', how='inner')
    sig = sig[sig['date'] >= sig['first_date']]

    close = eligible_codes(load_close('20151001'))
    close = add_fwd(close)
    # 次新: 信号须在 code 第60个交易日之后（按读窗内交易日序列）; 顺带从 close 表取 fwd 列
    seq_at = close[['code', 'date', 'seq'] + [f'fwd{n}' for n in HORIZONS]]
    sig = sig.merge(seq_at, on=['code', 'date'], how='inner')
    sig = sig[sig['seq'] >= 60].drop(columns=['seq'])

    # 周线过滤（仅信号涉及 code 的日线参与 resample）
    sig_codes = set(sig['code'])
    close_sig = close[close['code'].isin(sig_codes)][['code', 'date', 'close']]
    wk = weekly_filter_map(close_sig, ma)
    sigm = map_signals(sig, wk)

    # 市场基准（同资格层宇宙，逐日等权 fwdN）
    base = {}
    for n in HORIZONS:
        base[n] = close.groupby('date')[f'fwd{n}'].mean().to_dict()

    out = {'tag': tag, 'window': [start, end], 'prereg_id': prereg['id'],
           'prereg_fingerprint': prereg['fingerprint'], 'ma_week': ma, 'types': list(types)}
    for n in HORIZONS:
        f = sigm[sigm['pass_filter']]
        g = sigm
        fb, gb = f[f[f'fwd{n}'].notna()], g[g[f'fwd{n}'].notna()]
        inc = fb[f'fwd{n}'].mean() - gb[f'fwd{n}'].mean()
        t_inc = welch_t(fb[f'fwd{n}'].dropna(), gb[f'fwd{n}'].dropna())
        # 市场超额
        fb_ex = (fb[f'fwd{n}'] - fb['date'].map(base[n])).dropna()
        gb_ex = (gb[f'fwd{n}'] - gb['date'].map(base[n])).dropna()
        blk = {'filtered': group_stats(fb, f'fwd{n}'), 'unfiltered': group_stats(gb, f'fwd{n}'),
               'increment_mean': round(float(inc), 5), 'increment_t': round(float(t_inc), 2),
               'excess_vs_market_filtered_mean': round(float(fb_ex.mean()), 5),
               'excess_vs_market_unfiltered_mean': round(float(gb_ex.mean()), 5),
               'net_increment_after_cost_2bp0': round(float(inc - 0.002), 5)}
        if n == 20 and not a.oos:
            if a.subperiod:
                seg = fb.assign(seg=np.where(fb['date'] < '20210101', '2016-2020', '2021-2025'))
                seg_m = seg.groupby('seg')['fwd20'].mean()
                blk['segment_means'] = {k: round(float(v), 5) for k, v in seg_m.items()}
                blk['subperiod_consistent'] = bool((seg_m > 0).all()) and len(seg_m) == 2
            else:
                yr = fb.assign(y=fb['date'].str[:4]).groupby('y')['fwd20'].mean()
                blk['filtered_by_year'] = {k: round(float(v), 5) for k, v in yr.items()}
                blk['years_nonneg'] = int((yr >= 0).sum())
                blk['years_total'] = int(len(yr))
        out[f'h{n}'] = blk

    h20 = out['h20']
    if a.subperiod:
        gate = {'increment_t_ge_2': h20['increment_t'] is not None and h20['increment_t'] >= 2.0,
                'subperiod_consistent': h20.get('subperiod_consistent', False),
                'market_excess_positive': h20['excess_vs_market_unfiltered_mean'] is not None
                                         and h20['excess_vs_market_unfiltered_mean'] > 0}
    else:
        gate = {'increment_t_ge_2': h20['increment_t'] is not None and h20['increment_t'] >= 2.0,
                'years_nonneg_ge_7': h20.get('years_nonneg', 0) >= 7,
                'market_excess_positive': h20['excess_vs_market_unfiltered_mean'] is not None
                                         and h20['excess_vs_market_unfiltered_mean'] > 0}
    gate['PASS'] = all(gate.values())
    out['gate'] = gate

    of = (a.out if a.out else os.path.join(HERE, 'reports', f'WEEKLY_FILTER_CHAN12_20260909_{tag}_ma{ma}.json'))
    os.makedirs(os.path.dirname(of), exist_ok=True)
    json.dump(out, open(of, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f'\n落盘 {of}')


if __name__ == '__main__':
    main()
