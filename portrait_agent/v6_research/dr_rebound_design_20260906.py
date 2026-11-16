# -*- coding: utf-8 -*-
"""dip_rebound(深跌企稳) 配方立项探索 —— 危机alpha定位, 组合C同构流程第1步。
变体(按先验逻辑, 不穷举):
  E1 事件式: dip_rebound 原始(引用census) / +组合C排雷壳(NEG8+rel_ind_hi) 行级胜率/均值/分年
  E2 事件式共振: dip_rebound ∩ chan_buy1_10 / ∩ chan_buy2_10 / ∩ (buy1∪buy2) [v2确认日口径]
  E3 窗口式基线: dip_reb_5(5日有效) + 排雷壳, 20bar窗 head60, 组合C引擎同构(成本千三/基准全池)
产出: reports/dr_rebound_design_20260906.json"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

from combo_c_2016_backtest_20260906 import rebuild_chan, NEG8

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports')
START = '20160101'
COST_RT = 0.003

def log(s): print(s, flush=True)

def event_stats(sub_all, mask, tag, day_mean):
    sub = sub_all[mask]
    if len(sub) == 0:
        return {'tag': tag, 'n': 0}
    ex = sub['fwd20_close'] - sub['date'].map(day_mean)
    yr = {}
    for y, g in sub.groupby(sub['date'].str[:4]):
        exy = g['fwd20_close'] - g['date'].map(day_mean)
        yr[y] = {'n': int(len(g)), 'win%': round(float((g['fwd20_close'] > 0).mean() * 100), 1),
                 'mean%': round(float(g['fwd20_close'].mean() * 100), 2), 'ex%': round(float(exy.mean() * 100), 2)}
    return {'tag': tag, 'n': int(len(sub)),
            'win%': round(float((sub['fwd20_close'] > 0).mean() * 100), 2),
            'mean%': round(float(sub['fwd20_close'].mean() * 100), 3),
            'ex%': round(float(ex.mean() * 100), 3),
            'P90%': round(float(sub['fwd20_close'].quantile(0.9) * 100), 2),
            '分年': yr}

def main():
    t0 = time.time()
    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    m = m[m['date'] >= START].copy()
    m['lim_today'] = m['lim_today'].astype('boolean').fillna(False).astype(bool)
    m['rel_ind_hi'] = m.groupby('date')['rel_ind_ret'].rank(pct=True) >= 0.90
    m[['chan_buy1_10_v2', 'chan_buy2_10_v2']] = rebuild_chan(m, os.path.join(CACHE, 'chan_signals_v2.pkl'))
    m = m[m['fwd20_close'].notna()].copy()
    # dip_reb_5: 5日有效扩展(与 island_5/limit_break_5 同构)
    m = m.sort_values(['code', 'date']).reset_index(drop=True)
    m['dip_reb_5'] = m.groupby('code')['dip_rebound'].transform(lambda s: s.rolling(5).max())
    day_mean = m.groupby('date')['fwd20_close'].mean()
    negmask = pd.Series(False, index=m.index)
    for c in NEG8:
        negmask |= (m[c].fillna(0) > 0.5)
    negmask |= m['rel_ind_hi'].fillna(False)
    log(f'载入+重建 {time.time()-t0:.0f}s 行={len(m)}')

    res = {}
    # E1 排雷壳效果(事件式, 行等权)
    base = m['dip_rebound'] > 0
    res['E1_事件式'] = [
        event_stats(m, base, 'dip_rebound原始', day_mean),
        event_stats(m, base & ~negmask, 'dip_rebound+排雷壳(NEG8+rel_ind_hi)', day_mean),
    ]
    # E2 缠论共振(事件式)
    b1 = m['chan_buy1_10_v2'] > 0; b2 = m['chan_buy2_10_v2'] > 0
    res['E2_共振'] = [
        event_stats(m, base & b1, 'dip_rebound∩chan_buy1_10(v2)', day_mean),
        event_stats(m, base & b2, 'dip_rebound∩chan_buy2_10(v2)', day_mean),
        event_stats(m, base & (b1 | b2), 'dip_rebound∩(buy1∪buy2)(v2)', day_mean),
        event_stats(m, base & (b1 | b2) & ~negmask, 'dip_rebound∩(buy1∪buy2)+排雷壳', day_mean),
    ]
    # E3 窗口式基线: dip_reb_5+排雷壳, 组合C引擎同构
    groups = {d: g for d, g in m.groupby('date')}
    all_dates = sorted(m['date'].unique())
    nav_pc = nav_b = 1.0
    rows, trades = [], []
    for ws in all_dates[::20]:
        cs = groups.get(ws)
        if cs is None:
            continue
        cs2 = cs[cs['fwd20_close'].notna() & (~cs['lim_today'])]
        if len(cs2) < 500:
            continue
        sig = (cs2['dip_reb_5'].fillna(0) > 0).to_numpy() & (~negmask.loc[cs2.index]).to_numpy()
        sel_df = cs2[sig]
        rows.append({'date': ws, 'n_sig': len(sel_df)})
        if len(sel_df) < 5:
            continue  # 空仓窗, 如实计入
        sel60 = sel_df.head(60)
        fv = sel60['fwd20_close'].astype(float).to_numpy()
        fv_all = cs2['fwd20_close'].astype(float).to_numpy()
        pr = float(fv.mean()); br = float(fv_all.mean())
        nav_pc *= (1 + pr - COST_RT); nav_b *= (1 + br)
        trades.extend([(ws, c, float(v)) for c, v in zip(sel60['code'], fv)])
    wdf = pd.DataFrame(rows)
    tr = pd.DataFrame(trades, columns=['date', 'code', 'ret20'])
    cov = wdf[wdf['n_sig'] >= 5]
    exs = []
    tr_by_ws = tr.groupby('date')['ret20'].mean()
    for ws, pr_c in tr_by_ws.items():
        pass
    # 窗口超额: 入选均值-当日全池均值
    for ws in tr_by_ws.index:
        cs2 = groups.get(ws)
        if cs2 is None: continue
        br = float(cs2['fwd20_close'].astype(float).mean())
        exs.append(tr_by_ws[ws] - br)
    exs = np.array(exs)
    years = len(tr_by_ws) * 20 / 243
    res['E3_窗口式基线'] = {
        '配方': 'dip_reb_5(5日有效)+NEG8+rel_ind_hi; 20bar窗起点选股 head60等权 持有20bar 成本千三',
        '总窗口数': int(len(wdf)), '有信号窗口数': int(len(cov)),
        '窗口覆盖率%': round(len(cov) / len(wdf) * 100, 1),
        '每窗平均信号数': round(float(wdf['n_sig'].mean()), 1),
        'n_trades': int(len(tr)),
        '单笔胜率%': round(float((tr['ret20'] > 0).mean() * 100), 2),
        '单笔均值%': round(float(tr['ret20'].mean() * 100), 3),
        '成本后年化%(按有信号窗复利,空仓窗不计息)': round(((1 + tr_by_ws - COST_RT).prod() ** (1 / years) - 1) * 100, 2),
        '基准年化%(同窗口序列)': round((nav_b ** (1 / years) - 1) * 100, 2),
        '超额t(有信号窗)': round(float(exs.mean() / (exs.std() / np.sqrt(len(exs)))), 2) if len(exs) > 5 else None,
        '窗口超额胜率%': round(float((exs > 0).mean() * 100), 1) if len(exs) else None,
    }

    log(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    json.dump(res, open(os.path.join(REPORTS, 'dr_rebound_design_20260906.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    tr.to_csv(os.path.join(REPORTS, 'dr_rebound_design_trades_20260906.csv'), index=False)
    log(f'落盘 reports/dr_rebound_design_20260906.json 总耗时 {time.time()-t0:.0f}s')

if __name__ == '__main__':
    main()
