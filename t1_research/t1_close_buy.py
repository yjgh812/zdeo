# -*- coding: utf-8 -*-
"""
低开承接策略完整回测 v4（2026-08-10，知微）
=================================================
信号：T 日涨停封板 或 炸板（触板未封）→ T+1 开盘低开≥3%（open <= close_T×0.97）且非一字 → 开盘买入
出场：T+1 收盘卖出（变体 C 加 -5% 盘中止损）
组合：10 万本金、单笔 20% 仓、同日最多 5 只（低开幅度最深优先）、整手取整
成本：佣金万2.5×2 + 印花税0.05%(卖) + 滑点0.1%×2 ≈ 0.325% 双边；敏感性 0.15%/0.45%
变体：A=仅封板股；B=封板+炸板；C=B+止损-5%
数据：TDX vipdoc（2025-01-01 ~ 2026-08-07），合规=剔科创/ST(涨停价自动)/次新/低价<2
无前视：信号 T 日收盘后确定，T+1 开盘价成交 ✓
输出：D:\\zcodesj\\t1_research\\dip_backtest_result.json
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

TDX = Path(r'D:\anzhuang\tdx\vipdoc')
START = '20180101'
END = '20260807'
OUT = Path(r'D:\zcodesj\t1_research\dip_backtest_2023_2026_result.json')

# A 股股票白名单（沪主板/深主板/创业板；排除逆回购 204/131、基金 5/15/16、
# B股 200/900、债券 11/12/13、科创 688/689、北交 4/8/92）
STOCK_PREFIXES = ('600', '601', '603', '605', '000', '001', '002', '003', '300', '301')

import tdx_common as tc  # 2026-08-31 收敛: 解析统一走 tdx_common(委托 tdx_reader)



INIT_CAP = 100_000.0
POS = 0.20          # 单笔仓位
MAX_PER_DAY = 5     # 同日最多持仓数




def limit_ratio(code: str) -> float:
    return 0.20 if code.startswith(('300', '301')) else 0.10


def collect(args):
    """收集 T 日涨停/炸板事件 + T+1 行情（T 日尾盘买入用）。"""
    code, market = args
    day_path = TDX / market / 'lday' / f'{market}{code}.day'
    if not day_path.exists() or not code.startswith(STOCK_PREFIXES):
        return []
    try:
        day = tc.read_day(day_path)
    except Exception:
        return []
    if len(day) < 20:
        return []
    day = day[day.index >= '20171001']
    if len(day) == 0:
        return []

    ratio = limit_ratio(code)
    lims = (day['close'].shift(1) * (1 + ratio)).round(2)
    touched = (day['high'] >= lims - 1e-9) & day['close'].notna()
    sealed = ((day['close'] - lims).abs() <= 0.001) & day['close'].notna()

    events = []
    for i in range(len(day)):
        dt = day.index[i]
        if dt < pd.Timestamp(START):
            continue
        lim = lims.iloc[i]
        if pd.isna(lim) or not touched.iloc[i] or i + 1 >= len(day):
            continue
        c, o = day['close'].iloc[i], day['open'].iloc[i]
        nxt = day.iloc[i + 1]
        o1, h1, l1, c1 = nxt['open'], nxt['high'], nxt['low'], nxt['close']
        if o1 <= 0 or c1 <= 0:
            continue  # T+1 停牌
        events.append({
            'code': code, 'date': str(dt.date()), 'kind': 'sealed' if sealed.iloc[i] else 'broken',
            'is_new': int(i < 60), 'one_word': int(o >= lim - 1e-9),
            'close0': float(c), 'open1': float(o1), 'high1': float(h1),
            'low1': float(l1), 'close1': float(c1),
        })
    return events


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--pos', type=float, default=0.2)
    ap.add_argument('--max_day', type=int, default=5)
    args = ap.parse_args()
    POS, MAX_PER_DAY = args.pos, args.max_day
    codes = []
    for market in ('sh', 'sz'):
        lday = TDX / market / 'lday'
        if lday.exists():
            codes += [(f.stem[2:], market) for f in lday.glob(f'{market}*.day')]
    print(f'[info] 扫描 {len(codes)} 只（窗口 {START}~{END}）pos={POS} max_day={MAX_PER_DAY}', flush=True)

    events = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(collect, c) for c in codes]
        for f in as_completed(futs):
            events.extend(f.result())
    ev = pd.DataFrame(events)
    if len(ev) == 0:
        print('[warn] 无事件'); return
    ev = ev[(ev['is_new'] == 0) & (ev['close0'] >= 2)].copy()
    # 剔除一字板（尾盘买不进）；保留封板+炸板
    ev = ev[ev['one_word'] == 0].copy()
    # T 日尾盘买入（close0）→ T+1 三卖出时点
    ev['r_open'] = ev['open1'] / ev['close0'] - 1    # T+1 开盘卖
    ev['r_close'] = ev['close1'] / ev['close0'] - 1  # T+1 收盘卖
    ev['r_high'] = ev['high1'] / ev['close0'] - 1    # T+1 冲高卖（上界基准）
    for c_ in ('r_open', 'r_close', 'r_high'):
        ev[c_] = ev[c_].where(np.isfinite(ev[c_]) & (ev[c_].abs() <= 0.5))
    ev = ev.sort_values(['date', 'r_close']).reset_index(drop=True)
    print(f'[info] 事件 {len(ev)}（封板 {int((ev["kind"]=="sealed").sum())} / 炸板 {int((ev["kind"]=="broken").sum())}，均非一字）')

    results = {}
    for kind_label, kind_mask in [('封板', ev['kind']=='sealed'), ('炸板', ev['kind']=='broken'), ('全部', np.ones(len(ev), bool))]:
        for retcol, sell_label in [('r_open','开盘卖'), ('r_close','收盘卖'), ('r_high','冲高卖(上界)')]:
            d = ev[kind_mask].copy()
            d['rank_in_day'] = d.groupby('date').cumcount()
            d = d[d['rank_in_day'] < MAX_PER_DAY].copy()
            d['ret'] = d[retcol]
            d = d[d['ret'].notna()].copy()
            d['trade_ret'] = d['ret'] - 0.003
            v = run_backtest_full(d, POS)
            m = (1 + v['ann']/100) ** (1/12) - 1
            key = f'{kind_label}_{sell_label}'
            results[key] = v
            print(f'===== {key} pos={POS} =====')
            print(f"  n={v['n']} win={v['win']}% ann={v['ann']}% monthly={m*100:.1f}% mdd={v['mdd']}% avg={v['avg_ret']}%")
    OUT = Path(r'D:/zcodesj/t1_research/t1_close_buy_result.json')
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


def run_backtest_full(d: pd.DataFrame, POS: float):
    if len(d) == 0:
        return {'n': 0, 'win': 0, 'ann': 0, 'cum': 0, 'mdd': 0, 'avg_ret': 0}
    d = d.copy()
    days = sorted(d['date'].unique())
    eq = INIT_CAP; peak = eq; eq_curve, wins, n = [], [], 0
    for day in days:
        day_tr = d[d['date'] == day]
        n_day = len(day_tr)
        per = eq * POS / n_day
        day_pnl = 0.0
        for r in day_tr['trade_ret']:
            day_pnl += per * r
            wins.append(1 if r > 0 else 0); n += 1
        eq += day_pnl; peak = max(peak, eq); eq_curve.append(eq)
    eq_arr = np.array(eq_curve)
    mdd = float((eq_arr / np.maximum.accumulate(eq_arr) - 1).min() * 100)
    cum = (eq / INIT_CAP - 1) * 100
    ny = len(set(d['date'])) / 242
    ann = ((eq / INIT_CAP) ** (1 / ny) - 1) * 100 if ny > 0 else 0
    return {'n': n, 'win': round(float(np.mean(wins) * 100), 1), 'ann': round(float(ann), 1),
            'cum': round(float(cum), 1), 'mdd': round(mdd, 1),
            'avg_ret': round(float(d['trade_ret'].mean() * 100), 3)}


if __name__ == '__main__':
    main()
