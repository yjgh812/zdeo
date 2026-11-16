# -*- coding: utf-8 -*-
"""画像特征全景普查 2016-01-01 至 2026-09-04（纯描述性统计, 不构成选股配方）。
对 panel_v5 全部画像二值信号列统计: n/20日胜率/均值/中位/vs同日全截面超额;
对 PIT 质量连续列(ep/bp/roe)按五分位看 fwd20 单调性。
口径与 combo_c 引擎一致: fwd20_close = t收盘→t+20收盘, 基准=全截面等权。
产出: reports/feature_census_2016_20260906.json"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports')
START = '20160101'

BINARY_COLS = [
    # 缠论族
    'chan_buy1_10', 'chan_buy2_10', 'chan_buy3_10', 'chan_bdiv_10',
    # 15形态
    'island_bottom', 'base_vol_breakout', 'box_breakout', 'w_bottom', 'v_bottom',
    'rounded_bottom', 'ma_bull', 'new_high250', 'vdry_breakout', 'limit_break',
    'dip_rebound', 'hs_bottom', 'gap_up_run', 'volp_up3',
    # K线组合
    'morning_star', 'red_3_soldiers', 'bull_engulf', 'hammer_low', 'duofangpao',
    # MACD结构
    'macd_golden_hi', 'macd_golden_lo', 'macd_hist_shrink3', 'macd_top_div',
    'macd_2golden', 'macd_zero_up',
    # 时序/结构
    'up4of5', 'vol_shrink_pb', 'vol_contract', 'ma_tangle_div', 'higher_low',
    'new_high20', 'nr7', 'inside_break', 'gap_up_hold', 'island_5',
    'limit_break_5', 'gap_up_run_5', 'new_high_3', 'dip_reb_10', 'rounded_20',
    # 排雷/联动/质量二值
    'lhb_buy_5', 'lhb_buy', 'ind_lim_ge3', 'ind_lim_ge5', 'loss', 'macd_div',
]
QCOLS = ['pit_ep', 'pit_bp', 'pit_roe', 'pos250', 'ret60', 'amp20']

def log(s): print(s, flush=True)

def main():
    t0 = time.time()
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    m = m[m['date'] >= START].copy()
    m = m[m['fwd20_close'].notna()]
    day_mean = m.groupby('date')['fwd20_close'].mean()
    m['excess'] = m['fwd20_close'] - m['date'].map(day_mean)
    log(f'2016起 行={len(m)} 日期数={m["date"].nunique()} 载入+超额 {time.time()-t0:.0f}s')

    base_win = float((m['fwd20_close'] > 0).mean() * 100)
    base_mean = float(m['fwd20_close'].mean() * 100)
    res = {'基准(全截面等权)': {'n': len(m), '胜率%': round(base_win, 2), '均值%': round(base_mean, 3)},
           '信号列': [], '分位列': []}

    for c in BINARY_COLS:
        if c not in m.columns:
            res['信号列'].append({'信号': c, '缺失': True})
            continue
        sub = m[m[c].fillna(0) > 0]
        if len(sub) == 0:
            res['信号列'].append({'信号': c, 'n': 0})
            continue
        res['信号列'].append({
            '信号': c, 'n': int(len(sub)),
            '胜率%': round(float((sub['fwd20_close'] > 0).mean() * 100), 2),
            '均值%': round(float(sub['fwd20_close'].mean() * 100), 3),
            '中位%': round(float(sub['fwd20_close'].median() * 100), 3),
            '超额均值%': round(float(sub['excess'].mean() * 100), 3),
            'P90%': round(float(sub['fwd20_close'].quantile(0.9) * 100), 2),
        })

    for c in QCOLS:
        if c not in m.columns:
            continue
        sub = m[m[c].notna()].copy()
        try:
            sub['q'] = pd.qcut(sub[c], 5, labels=False, duplicates='drop')
        except Exception:
            continue
        g = sub.groupby('q')['fwd20_close'].agg(
            n='count', win=lambda s: round(float((s > 0).mean() * 100), 2),
            mean=lambda s: round(float(s.mean() * 100), 3))
        res['分位列'].append({'列': c, '五分位(Q1低→Q5高)': [
            {'Q': int(q), 'n': int(r['n']), '胜率%': r['win'], '均值%': r['mean']}
            for q, r in g.iterrows()]})

    json.dump(res, open(os.path.join(REPORTS, 'feature_census_2016_20260906.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    # 控制台摘要: 按超额均值排序
    sigs = [x for x in res['信号列'] if x.get('n', 0) >= 500]
    sigs.sort(key=lambda x: -x['超额均值%'])
    log(f'\n=== 信号列普查(n>=500, 按超额均值降序, 基准胜率{base_win:.1f}%/均值{base_mean:.2f}%) ===')
    for x in sigs:
        log(f"{x['信号']:<22} n={x['n']:>7} 胜率={x['胜率%']:>6}% 均值={x['均值%']:>6}% "
            f"超额={x['超额均值%']:>6}% P90={x['P90%']:>7}%")
    log(f'落盘 reports/feature_census_2016_20260906.json 总耗时 {time.time()-t0:.0f}s')

if __name__ == '__main__':
    main()
