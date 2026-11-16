# -*- coding: utf-8 -*-
"""行业板块方向对日线缠论一二买的增量验证（prereg=20260909-sector-filter-chan12-v1, 指纹a4d17df2fc6e40ea）。

假设 H3(跨尺度): 所属行业等权指数周线方向向上(周收盘>周MA_n 且 周MA_n上行, 仅用信号日前已完整周K)
过滤后的 chan buy1+buy2 信号相对同批无过滤信号有显著 fwd20 增量。
与 WEEKLY-FILTER-CHAN12(个股周线, 已证伪)的区别=条件尺度: 行业层条件×个股层买点。
行业指数=成分股 qfq 日收益等权均值 cumprod 合成; 映射=stock_sector_sw1 当前快照(非PIT局限已登记)。
数据源=真主库 workbuddyspace(与 chan_fix_eventstudy 先例同源)。
用法: python sector_filter_chan12_20260909.py [--ma 20|10|30] [--out PATH]
产出: reports/SECTOR_FILTER_CHAN12_20260909_MAIN_ma{n}.json
"""
import os, sys, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
META_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db'
SIGNAL_PKL = os.path.join(HERE, 'cache', 'chan_signals_v2.pkl')
PREREG = r'D:\zcodesj\data\prereg_sector_filter_chan12_20260909.json'
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


def load_sector_map():
    con = sqlite3.connect(f'file:{META_DB}?mode=ro', uri=True)
    rows = con.execute("SELECT code,sector_name FROM stock_sector_sw1").fetchall()
    con.close()
    return {str(c)[-6:]: s for c, s in rows}


def add_fwd(close):
    close = close.copy()
    close['seq'] = close.groupby('code').cumcount()
    for n in HORIZONS:
        close[f'fwd{n}'] = close.groupby('code')['close'].shift(-n) / close['close'] - 1
    return close


def sector_filter_map(close_all, sec_map, ma_win):
    """行业等权日指数合成 + 行业周线方向表 (sector, week_end, pass_filter)。
    指数=成分股日收益等权均值 cumprod; 周点位=resample(W-FRI) 周内最后值; 仅完整周(MA_n 非NaN)。"""
    df = close_all.sort_values(['code', 'date']).copy()
    df['ret'] = df.groupby('code')['close'].pct_change()
    df = df.dropna(subset=['ret'])
    df['sector'] = df['code'].map(sec_map)
    df = df.dropna(subset=['sector'])
    ind = df.groupby(['sector', 'date'])['ret'].mean().reset_index()
    ind = ind.sort_values(['sector', 'date']).reset_index(drop=True)
    ind['idx'] = ind.groupby('sector')['ret'].transform(lambda s: (1.0 + s).cumprod())
    ind['ts'] = pd.to_datetime(ind['date'], format='%Y%m%d')
    wk = (ind.groupby(['sector', pd.Grouper(key='ts', freq='W-FRI')])
             .agg(week_end=('date', 'max'), idx_w=('idx', 'last'))
             .dropna(subset=['idx_w']).reset_index())
    wk = wk.sort_values(['sector', 'week_end']).reset_index(drop=True)
    wk['ma'] = wk.groupby('sector')['idx_w'].transform(lambda s: s.rolling(ma_win).mean())
    wk['ma_prev'] = wk.groupby('sector')['ma'].shift(1)
    wk['pass_filter'] = (wk['idx_w'] > wk['ma']) & (wk['ma'] > wk['ma_prev'])
    out = wk[['sector', 'week_end', 'pass_filter']].dropna(subset=['pass_filter']).copy()
    out['week_end'] = pd.to_datetime(out['week_end'], format='%Y%m%d')
    code_sec = pd.DataFrame([(c[-6:], s) for c, s in sec_map.items()],
                            columns=['code', 'sector']).drop_duplicates('code')
    return out, code_sec


def map_signals(signals, wk_ind, code_sec):
    """merge_asof: 每个信号经其行业配到 week_end<=信号日 的最近完整周状态。"""
    sg = signals.copy()
    sg['ts'] = pd.to_datetime(sg['date'], format='%Y%m%d')
    sg = sg.merge(code_sec, on='code', how='inner')
    w = wk_ind.rename(columns={'week_end': 'ts'})
    m = pd.merge_asof(sg.sort_values('ts'), w.sort_values('ts'),
                      on='ts', by='sector', direction='backward')
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
    ap.add_argument('--ma', type=int, default=20, help='行业指数周MA窗(20=主口径; 10/30=prereg预设敏感性变体)')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    ma = a.ma
    if ma not in (10, 20, 30):
        raise SystemExit(f'--ma={ma} 不在 prereg 预算内(仅10/20/30)')
    start, end = MAIN_START, MAIN_END

    prereg = json.load(open(PREREG, encoding='utf-8'))

    sig_all = pd.read_pickle(SIGNAL_PKL)
    sig = sig_all[sig_all['type'].isin(['buy1', 'buy2'])].copy()
    sig['code'] = sig['code'].astype(str).str.zfill(6)
    sig['date'] = sig['date'].astype(str)
    sig = sig[(sig['date'] >= start) & (sig['date'] <= end)]

    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    first = pd.read_sql("SELECT code,MIN(date) first_date FROM stock_daily_meta_qfq GROUP BY code", con)
    con.close()
    first['code'] = first['code'].astype(str).str.zfill(6)
    sig = sig.merge(first, on='code', how='inner')
    sig = sig[sig['date'] >= sig['first_date']]

    close = load_close('20140101')
    close = add_fwd(close)
    seq_at = close[['code', 'date', 'seq'] + [f'fwd{n}' for n in HORIZONS]]
    sig = sig.merge(seq_at, on=['code', 'date'], how='inner')
    sig = sig[sig['seq'] >= 60].drop(columns=['seq'])

    sec_map = load_sector_map()
    wk_ind, code_sec = sector_filter_map(close[['code', 'date', 'close']], sec_map, ma)
    sigm = map_signals(sig, wk_ind, code_sec)

    base = {}
    for n in HORIZONS:
        base[n] = close.groupby('date')[f'fwd{n}'].mean().to_dict()

    out = {'window': [start, end], 'prereg_id': prereg['id'],
           'prereg_fingerprint': prereg['fingerprint'], 'ma_week': ma,
           'n_sectors': int(wk_ind['sector'].nunique())}
    for n in HORIZONS:
        f = sigm[sigm['pass_filter']]
        g = sigm
        fb, gb = f[f[f'fwd{n}'].notna()], g[g[f'fwd{n}'].notna()]
        inc = fb[f'fwd{n}'].mean() - gb[f'fwd{n}'].mean()
        t_inc = welch_t(fb[f'fwd{n}'].dropna(), gb[f'fwd{n}'].dropna())
        fb_ex = (fb[f'fwd{n}'] - fb['date'].map(base[n])).dropna()
        gb_ex = (gb[f'fwd{n}'] - gb['date'].map(base[n])).dropna()
        blk = {'filtered': group_stats(fb, f'fwd{n}'), 'unfiltered': group_stats(gb, f'fwd{n}'),
               'increment_mean': round(float(inc), 5), 'increment_t': round(float(t_inc), 2),
               'excess_vs_market_filtered_mean': round(float(fb_ex.mean()), 5),
               'excess_vs_market_unfiltered_mean': round(float(gb_ex.mean()), 5),
               'net_increment_after_cost_2bp0': round(float(inc - 0.002), 5)}
        if n == 20:
            yr = fb.assign(y=fb['date'].str[:4]).groupby('y')['fwd20'].mean()
            blk['filtered_by_year'] = {k: round(float(v), 5) for k, v in yr.items()}
            blk['years_nonneg'] = int((yr >= 0).sum())
            blk['years_total'] = int(len(yr))
        out[f'h{n}'] = blk

    h20 = out['h20']
    gate = {'increment_t_ge_2': h20['increment_t'] is not None and h20['increment_t'] >= 2.0,
            'years_nonneg_ge_7': h20.get('years_nonneg', 0) >= 7,
            'market_excess_positive': h20['excess_vs_market_unfiltered_mean'] is not None
                                     and h20['excess_vs_market_unfiltered_mean'] > 0}
    gate['PASS'] = all(gate.values())
    out['gate'] = gate

    of = (a.out if a.out else os.path.join(HERE, 'reports', f'SECTOR_FILTER_CHAN12_20260909_MAIN_ma{ma}.json'))
    os.makedirs(os.path.dirname(of), exist_ok=True)
    json.dump(out, open(of, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f'\n落盘 {of}')


if __name__ == '__main__':
    main()
