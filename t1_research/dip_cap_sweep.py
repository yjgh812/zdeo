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
    """收集单只股票的潜在信号事件（T 日涨停/炸板 + T+1 全部行情）。"""
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
            continue  # T+1 停牌/数据缺失（回测纪律：停牌不成交）
        events.append({
            'code': code, 'date': str(dt.date()), 'kind': 'sealed' if sealed.iloc[i] else 'broken',
            'is_new': int(i < 60), 'one_word': int(o >= lim - 1e-9),
            'close0': float(c), 'open1': float(o1), 'high1': float(h1),
            'low1': float(l1), 'close1': float(c1), 'lim1': float(round(c * (1 + ratio), 2)),
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
    ev['gap'] = ev['open1'] / ev['close0'] - 1
    sig = ev[(ev['gap'] <= -0.03) & (ev['open1'] < ev['lim1'] - 1e-9)].copy()
    sig['buy_ret'] = sig['close1'] / sig['open1'] - 1
    sig['low_ret'] = sig['low1'] / sig['open1'] - 1
    sig = sig[np.isfinite(sig['buy_ret']) & np.isfinite(sig['low_ret']) & (sig['buy_ret'] >= -0.3) & (sig['buy_ret'] <= 0.3)]
    sig = sig.sort_values(['date', 'gap']).reset_index(drop=True)

    # 信号密度（容量证据）：每日低开>=3% 非一字信号数分布
    per_day = sig.groupby('date').size()
    print(f'[容量] 交易日 {len(per_day)} 天，日均信号 {per_day.mean():.1f} 只，'
          f'中位 {per_day.median():.0f}，>=1只 {int((per_day>=1).mean()*100)}% 的天，'
          f'>=3只 {int((per_day>=3).mean()*100)}% 的天，>=5只 {int((per_day>=5).mean()*100)}% 的天')

    sig['rank_in_day'] = sig.groupby('date').cumcount()
    sig = sig[sig['rank_in_day'] < MAX_PER_DAY].copy()

    def is_ld_open(row):
        r = 0.20 if row['code'].startswith(('300', '301')) else 0.10
        ld = round(row['close0'] * (1 - r), 2)
        return abs(row['open1'] - ld) <= 0.005
    sig['is_ld_open'] = sig.apply(is_ld_open, axis=1)

    d = sig[~sig['is_ld_open']].copy()  # D 变体
    d['ret'] = d['buy_ret']
    cost = 0.003
    d['trade_ret'] = d['ret'] - cost
    v = run_backtest_full(d, POS)
    # 月化（复利）折算
    m = (1 + v['ann']/100) ** (1/12) - 1
    print(f'===== D 变体 pos={POS} max_day={MAX_PER_DAY} cost=0.30% =====')
    print(f"{{'pos':{POS}, 'max_day':{MAX_PER_DAY}, 'n':{v['n']}, 'win':{v['win']}%, "
          f"'ann':{v['ann']}%, 'monthly':{m*100:.1f}%, 'cum':{v['cum']}%, 'mdd':{v['mdd']}%}}")
    for y in sorted(v['per_year']):
        py = v['per_year'][y]
        print(f'  {y}: {py["n"]}笔 pnl {py["pnl"]:,.0f}')


def run_backtest_full(d: pd.DataFrame, POS: float):
    if len(d) == 0:
        return {'n': 0, 'win': 0, 'ann': 0, 'cum': 0, 'mdd': 0, 'avg_ret': 0}
    d = d.copy()
    days = sorted(d['date'].unique())
    eq = INIT_CAP; peak = eq; eq_curve, wins, n = [], [], 0
    per_year = {}
    for day in days:
        day_tr = d[d['date'] == day]
        n_day = len(day_tr)
        per = eq * POS / n_day
        day_pnl = 0.0
        for r in day_tr['trade_ret']:
            day_pnl += per * r
            wins.append(1 if r > 0 else 0); n += 1
        eq += day_pnl; peak = max(peak, eq); eq_curve.append(eq)
        py = per_year.setdefault(day[:4], {'n': 0, 'pnl': 0.0})
        py['n'] += n_day; py['pnl'] += day_pnl
    eq_arr = np.array(eq_curve)
    mdd = float((eq_arr / np.maximum.accumulate(eq_arr) - 1).min() * 100)
    cum = (eq / INIT_CAP - 1) * 100
    ny = len(set(d['date'])) / 242
    ann = ((eq / INIT_CAP) ** (1 / ny) - 1) * 100 if ny > 0 else 0
    return {'n': n, 'win': round(float(np.mean(wins) * 100), 1), 'ann': round(float(ann), 1),
            'cum': round(float(cum), 1), 'mdd': round(mdd, 1),
            'avg_ret': round(float(d['trade_ret'].mean() * 100), 3),
            'per_year': {y: {'n': v['n'], 'pnl': round(float(v['pnl']), 0)} for y, v in per_year.items()}}


if __name__ == '__main__':
    main()
