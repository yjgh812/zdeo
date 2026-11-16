# -*- coding: utf-8 -*-
"""画像选股近3年回测-诚实修正版(2026-09-05).

修正点(vs backtest_3yr.py):
1. short 收益改用 fwd5_close(未来5日收盘价收益, 非最高价), 消除最高价偏乐观
   - 原版 fwd5_high=未来5日最高/今收-1, 实际不可在最高价卖出 → 虚高
   - 修正 fwd5_close=未来5日收盘/今收-1, 真实持有5日收益
2. 时间分段: 训练段(前70%) vs 测试段(后30%), 分别回测对比
   - 全量模型在测试段表现 ≈ 训练段 → 泛化能力
   - 测试段远差于训练段 → 前视污染
3. mid 保持 fwd20_close(已是收盘口径)
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
import lightgbm as lgb

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, 'models')
CACHE = os.path.join(HERE, 'cache')
REPORTS = os.path.join(HERE, 'reports')
os.makedirs(REPORTS, exist_ok=True)
from model_v1 import FEATURES

TOPN = 20
MIN_SECTION = 500

def load_panel():
    p = os.path.join(CACHE, 'panel_v1.pkl')
    panel = pd.read_pickle(p)
    panel = panel.sort_values(['code', 'date']).reset_index(drop=True)
    # 补算 fwd5_close: 未来5日收盘/今收-1 (面板已有 fwd20_close, 无 fwd5_close)
    # 按 code 分组, close 的 shift(-5)/close - 1
    panel['fwd5_close'] = panel.groupby('code')['close'].transform(lambda s: s.shift(-5) / s - 1)
    return panel

def backtest_segment(panel, tag='short', hold=5, segment='full', split_ratio=0.7):
    """分段回测. segment: full/train/test. split_ratio: 训练段占比."""
    mp = os.path.join(MODELS, f'v1.0.0_{tag}.txt')
    bst = lgb.Booster(model_file=mp)
    fwd_col = 'fwd5_close' if tag == 'short' else 'fwd20_close'

    dates = sorted(panel['date'].unique())
    n = len(dates)
    i7 = int(n * split_ratio)
    if segment == 'train':
        dates = dates[:i7]
    elif segment == 'test':
        dates = dates[i7:]
    window_starts = dates[::hold]

    rows = []
    nav_p, nav_b = 1.0, 1.0
    for ws in window_starts:
        cs = panel[panel['date'] == ws].copy()
        cs = cs[cs[fwd_col].notna() & (~cs['lim_today'])]
        if len(cs) < MIN_SECTION:
            continue
        cs['prob'] = bst.predict(cs[FEATURES])
        top = cs.nlargest(TOPN, 'prob')
        if len(top) < TOPN:
            continue
        port_ret = float(top[fwd_col].astype(float).mean())
        base_ret = float(cs[fwd_col].astype(float).mean())
        excess = port_ret - base_ret
        nav_p *= (1 + port_ret)
        nav_b *= (1 + base_ret)
        rows.append({
            'date': ws, 'port_ret': round(port_ret*100, 3),
            'base_ret': round(base_ret*100, 3), 'excess': round(excess*100, 3),
            'nav_p': round(nav_p, 4), 'nav_b': round(nav_b, 4),
            'win': int(excess > 0),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return {'error': '无有效窗口', 'segment': segment}
    rets_p = df['port_ret'].values / 100
    rets_b = df['base_ret'].values / 100
    nav_arr = df['nav_p'].values
    years = len(df) * hold / 243
    ann_p = nav_p ** (1/years) - 1 if years > 0 else 0
    ann_b = nav_b ** (1/years) - 1 if years > 0 else 0
    mdd_p = float((np.maximum.accumulate(nav_arr) / nav_arr - 1).max()) if len(nav_arr) else 0
    sharpe = (rets_p.mean() - 0.02/len(df)*years) / rets_p.std() * np.sqrt(len(df)/years) if rets_p.std() > 0 else 0
    return {
        'segment': segment, 'tag': tag, 'hold': hold, 'n_windows': len(df),
        'date_range': f"{df['date'].iloc[0]}~{df['date'].iloc[-1]}",
        'nav_port': round(nav_p, 4), 'nav_base': round(nav_b, 4),
        'total_ret_pct': round((nav_p-1)*100, 2),
        'total_base_pct': round((nav_b-1)*100, 2),
        'excess_total_pct': round((nav_p/nav_b-1)*100, 2),
        'ann_ret_pct': round(ann_p*100, 2),
        'ann_base_pct': round(ann_b*100, 2),
        'mdd_pct': round(mdd_p*100, 2),
        'sharpe': round(float(sharpe), 3),
        'win_rate_pct': round(df['win'].mean()*100, 1),
        'avg_port_ret_pct': round(rets_p.mean()*100, 3),
        'avg_base_ret_pct': round(rets_b.mean()*100, 3),
        'avg_excess_pct': round((rets_p.mean()-rets_b.mean())*100, 3),
    }

def main():
    t0 = time.time()
    print('[honest] 加载面板+补算fwd5_close...', flush=True)
    panel = load_panel()
    print(f'[honest] 面板 {panel.shape} stocks={panel["code"].nunique()} '
          f'({time.time()-t0:.1f}s)', flush=True)

    out = {}
    for tag, hold in [('short', 5), ('mid', 20)]:
        out[tag] = {}
        for seg in ['full', 'train', 'test']:
            r = backtest_segment(panel, tag, hold, seg)
            out[tag][seg] = r
            if 'error' not in r:
                print(f'[{tag}/{seg}] nav={r["nav_port"]} base={r["nav_base"]} '
                      f'ann={r["ann_ret_pct"]}% excess={r["excess_total_pct"]}% '
                      f'mdd={r["mdd_pct"]}% win={r["win_rate_pct"]}% n={r["n_windows"]}', flush=True)

    fp = os.path.join(REPORTS, 'backtest_3yr_honest_20260905.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)

    print('\n' + '='*90)
    print(f'{"模型":<8} {"段":<6} {"窗口":<5} {"净值":<10} {"基准":<10} {"年化%":<8} {"基准年化%":<9} '
          f'{"超额%":<8} {"MDD%":<7} {"夏普":<6} {"胜率%":<6}')
    print('-'*90)
    for tag in ['short', 'mid']:
        for seg in ['full', 'train', 'test']:
            r = out[tag][seg]
            if 'error' in r: continue
            print(f'{tag:<8} {seg:<6} {r["n_windows"]:<5} {r["nav_port"]:<10} {r["nav_base"]:<10} '
                  f'{r["ann_ret_pct"]:<8} {r["ann_base_pct"]:<9} {r["excess_total_pct"]:<8} '
                  f'{r["mdd_pct"]:<7} {r["sharpe"]:<6} {r["win_rate_pct"]:<6}')
    print('='*90)

    print('\n诚实结论:')
    for tag in ['short', 'mid']:
        ft, tt = out[tag]['full'], out[tag]['test']
        if 'error' in ft or 'error' in tt: continue
        print(f'  {tag}: 全量年化{ft["ann_ret_pct"]}% vs 测试段年化{tt["ann_ret_pct"]}% '
              f'→ {"前视污染严重(测试段远差)" if tt["ann_ret_pct"] < ft["ann_ret_pct"]*0.5 else "有一定泛化能力"}')

    print('\n修正说明:')
    print('1. short收益=fwd5_close(未来5日收盘, 非最高价), 消除原版最高价虚高')
    print('2. 时间分段: train=前70%(模型训练段), test=后30%(测试段)')
    print('3. 前视残余: 模型全样本训练含测试段, test段仍非纯净OOS')
    print(f'落盘: {fp}')

if __name__ == '__main__':
    main()
