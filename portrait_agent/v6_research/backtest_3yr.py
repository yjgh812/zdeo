# -*- coding: utf-8 -*-
"""画像特征选股近3年回测(2026-09-05 主人指令).

设计:
  - 面板: feature_engine.build() 生成, 已含 fwd5_high(未来5日最高价收益) / fwd20_close(未来20日收盘收益)
  - 模型: v1.0.0_short.txt / v1.0.0_mid.txt (LightGBM, 全样本训练)
  - 选股: 每截面按模型 prob 选 Top20 等权
  - 持有: 非重叠窗口(short=5交易日, mid=20交易日), 避免重叠假设
  - 收益: short 用 fwd5_high 截断-10%(与 shadow_gate 一致, 防最高价偏乐观);
          mid 用 fwd20_close(真实收盘口径)
  - 基准: 全截面等权同窗口同持有期
  - 输出: 累计净值/年化/MDD/胜率/超额/夏普/月度明细

诚实标注:
  1. 前视污染: 模型训练用了全样本(含未来标签), 回测用同一模型打历史分 → 偏乐观
  2. fwd5_high 用最高价(非收盘), 已截断-10%保守化, 仍偏乐观(真实持有5日收盘收益 < 此值)
  3. 无交易成本(实际约0.15%双边, 短期窗口侵蚀较大)
  4. 非重叠窗口调仓频率低, 实际每日画像Top20重叠更高
  结论性质 = 画像特征选股的理论上限参考, 非实盘可预期收益.
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
MIN_SECTION = 500   # 截面最少样本(shadow_gate口径)
FLOOR_SHORT = -0.10  # short收益截断下限(与shadow_gate一致)

def load_panel():
    p = os.path.join(CACHE, 'panel_v1.pkl')
    panel = pd.read_pickle(p)
    panel = panel.sort_values(['code', 'date']).reset_index(drop=True)
    return panel

def backtest(panel, tag='short', hold=5, floor=FLOOR_SHORT):
    """非重叠窗口回测.

    tag: 模型名('short'/'mid')
    hold: 持有交易日数(5/20)
    floor: 收益截断下限(short=-0.10, mid=None)
    返回 dict(净值/指标/明细)
    """
    mp = os.path.join(MODELS, f'v1.0.0_{tag}.txt')
    if not os.path.exists(mp):
        return {'error': f'模型缺失 {mp}'}
    bst = lgb.Booster(model_file=mp)
    fwd_col = 'fwd5_high' if tag == 'short' else 'fwd20_close'

    dates = sorted(panel['date'].unique())
    # 非重叠窗口起始日: 每隔 hold 个交易日
    window_starts = dates[::hold]
    # 截尾: 最后一个窗口如果 fwd 不完整(尾部NaN) 丢弃
    rows = []
    nav_p, nav_b = 1.0, 1.0
    for ws in window_starts:
        cs = panel[panel['date'] == ws].copy()
        cs = cs[cs[fwd_col].notna() & (~cs['lim_today'])]
        if len(cs) < MIN_SECTION:
            continue
        # 截面打分
        cs['prob'] = bst.predict(cs[FEATURES])
        top = cs.nlargest(TOPN, 'prob')
        if len(top) < TOPN:
            continue
        raw_p = top[fwd_col].astype(float).values
        raw_b = cs[fwd_col].astype(float).values
        if floor is not None:
            raw_p = np.clip(raw_p, floor, None)
            raw_b = np.clip(raw_b, floor, None)
        port_ret = float(raw_p.mean())
        base_ret = float(raw_b.mean())
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
        return {'error': '无有效窗口'}
    # 指标
    rets_p = df['port_ret'].values / 100
    rets_b = df['base_ret'].values / 100
    nav_arr = df['nav_p'].values
    years = len(df) * hold / 243   # 交易日年化
    ann_p = nav_p ** (1/years) - 1 if years > 0 else 0
    ann_b = nav_b ** (1/years) - 1 if years > 0 else 0
    mdd_p = float((np.maximum.accumulate(nav_arr) / nav_arr - 1).max()) if len(nav_arr) else 0
    # 夏普(每窗口收益, 无风险利率2%年化)
    if rets_p.std() > 0:
        sharpe = (rets_p.mean() - 0.02/len(df)*years) / rets_p.std() * np.sqrt(len(df)/years)
    else:
        sharpe = 0
    win_rate = df['win'].mean()
    summary = {
        'tag': tag, 'hold_days': hold, 'n_windows': len(df),
        'date_range': f"{df['date'].iloc[0]}~{df['date'].iloc[-1]}",
        'nav_port': round(nav_p, 4), 'nav_base': round(nav_b, 4),
        'total_ret_pct': round((nav_p-1)*100, 2),
        'total_base_pct': round((nav_b-1)*100, 2),
        'excess_total_pct': round((nav_p/nav_b-1)*100, 2),
        'ann_ret_pct': round(ann_p*100, 2),
        'ann_base_pct': round(ann_b*100, 2),
        'mdd_pct': round(mdd_p*100, 2),
        'sharpe': round(float(sharpe), 3),
        'win_rate_pct': round(win_rate*100, 1),
        'avg_port_ret_pct': round(rets_p.mean()*100, 3),
        'avg_base_ret_pct': round(rets_b.mean()*100, 3),
        'avg_excess_pct': round((rets_p.mean()-rets_b.mean())*100, 3),
    }
    return {'summary': summary, 'detail': df.to_dict('records')}

def main():
    t0 = time.time()
    print('[backtest] 加载面板...', flush=True)
    panel = load_panel()
    print(f'[backtest] 面板 {panel.shape} dates={panel["date"].min()}~{panel["date"].max()} '
          f'stocks={panel["code"].nunique()} ({time.time()-t0:.1f}s)', flush=True)

    out = {}
    for tag, hold, floor in [('short', 5, FLOOR_SHORT), ('mid', 20, None)]:
        print(f'[backtest] {tag} 模型 hold={hold} floor={floor} ...', flush=True)
        r = backtest(panel, tag, hold, floor)
        out[tag] = r
        if 'summary' in r:
            print(json.dumps(r['summary'], ensure_ascii=False), flush=True)
        else:
            print(r, flush=True)

    # 落盘
    fp = os.path.join(REPORTS, 'backtest_3yr_20260905.json')
    # detail 转 CSV
    for tag in out:
        if 'detail' in out[tag]:
            df = pd.DataFrame(out[tag]['detail'])
            df.to_csv(os.path.join(REPORTS, f'backtest_3yr_{tag}_20260905.csv'), index=False)
    # JSON(去掉detail防爆)
    out_json = {k: {'summary': v.get('summary'), 'detail_rows': len(v.get('detail', []))} for k, v in out.items()}
    json.dump(out_json, open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'[backtest] 落盘 {fp} ({time.time()-t0:.1f}s)', flush=True)

    # 打印对比表
    if all('summary' in out.get(t, {}) for t in ['short', 'mid']):
        print('\n' + '='*70)
        print(f'{"指标":<24} {"y_short(5日)":<22} {"y_mid(20日)":<22}')
        print('-'*70)
        s, m = out['short']['summary'], out['mid']['summary']
        for k, label in [('n_windows','窗口数'), ('date_range','日期范围'),
                         ('nav_port','组合净值'), ('nav_base','基准净值'),
                         ('total_ret_pct','总收益%'), ('total_base_pct','基准总收益%'),
                         ('excess_total_pct','超额总收益%'), ('ann_ret_pct','年化%'),
                         ('mdd_pct','最大回撤%'), ('sharpe','夏普'),
                         ('win_rate_pct','胜率%'), ('avg_excess_pct','平均超额%')]:
            print(f'{label:<24} {str(s.get(k,"")):<22} {str(m.get(k,"")):<22}')
        print('='*70)
        print('\n诚实标注:')
        print('1. 前视污染: 模型全样本训练, 回测同模型→偏乐观')
        print('2. short用未来5日最高价(截断-10%), 仍高于真实持有5日收盘收益')
        print('3. 无交易成本(双边约0.15%, 短期窗口侵蚀大)')
        print('4. 非重叠窗口调仓频率低, 实际每日Top20重叠更高')
        print('结论性质=画像特征选股理论上限参考, 非实盘可预期收益')

if __name__ == '__main__':
    main()
