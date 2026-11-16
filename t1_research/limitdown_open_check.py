# -*- coding: utf-8 -*-
"""
跌停开盘样本专项核查（2026-08-10，知微）
=================================================
背景：今日 dip_probe 发现宁夏建材等低开 -10%（=跌停价开盘）。
核查 2018-2026 回测中"低开≥3% 信号"里跌停开盘（gap<=-9.5% 或
open1≈跌停价）样本的占比与表现——若跌停开盘是亏损重灾区，
信号应剔除跌停开盘（跌停封死无法卖出）。

复用 dip_backtest.collect 事件生成器，仅统计不跑组合。
输出：D:\\zcodesj\\t1_research\\limitdown_open_check.json
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r'D:\zcodesj\t1_research')
from dip_backtest import collect, limit_ratio, TDX, STOCK_PREFIXES

START = '20180101'
END = '20260807'
OUT = Path(r'D:\zcodesj\t1_research\limitdown_open_check.json')


def main():
    codes = []
    for market in ('sh', 'sz'):
        lday = TDX / market / 'lday'
        if lday.exists():
            codes += [(f.stem[2:], market) for f in lday.glob(f'{market}*.day')]
    print(f'[info] 扫描 {len(codes)} 只（{START}~{END}）', flush=True)
    events = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(collect, c) for c in codes]
        for f in as_completed(futs):
            events.extend(f.result())
    ev = pd.DataFrame(events)
    if len(ev) == 0:
        print('[warn] 无事件')
        return
    ev = ev[(ev['is_new'] == 0) & (ev['close0'] >= 2)].copy()
    ev['gap'] = ev['open1'] / ev['close0'] - 1
    ev['buy_ret'] = ev['close1'] / ev['open1'] - 1
    ev['lim1'] = ev['lim1']
    sig = ev[(ev['gap'] <= -0.03) & (ev['open1'] < ev['lim1'] - 1e-9)].copy()
    sig['is_ld'] = sig['gap'] <= -0.095  # 跌停开盘近似（gap<=-9.5%）
    sig['is_ld_exact'] = (sig['open1'] - (sig['close0'] * 0.9).round(2)).abs() <= 0.005
    print(f'[info] 低开≥3% 信号总数: {len(sig)}')
    print(f'[info] 其中 gap<=-9.5%: {int(sig["is_ld"].sum())}（{sig["is_ld"].mean()*100:.1f}%）'
          f' | 精确跌停价开盘: {int(sig["is_ld_exact"].sum())}（{sig["is_ld_exact"].mean()*100:.1f}%）')

    def block(df, tag):
        if len(df) == 0:
            print(f'[{tag}] 无样本')
            return None
        r = {'n': int(len(df)), 'ge3_pct': round(float((df['buy_ret'] >= 0.03).mean() * 100), 2),
             'win_pct': round(float((df['buy_ret'] > 0).mean() * 100), 2),
             'avg_ret_pct': round(float(df['buy_ret'].mean() * 100), 3),
             'p05': round(float(df['buy_ret'].quantile(0.05) * 100), 2),
             'min': round(float(df['buy_ret'].min() * 100), 2)}
        print(f'[{tag}] n={r["n"]:>5} ≥3%={r["ge3_pct"]:>5.2f}% 胜率={r["win_pct"]:>5.2f}% '
              f'平均={r["avg_ret_pct"]:>6.3f}% P5={r["p05"]:>6.2f}% 最差={r["min"]:>7.2f}%')
        return r

    out = {
        '跌停开盘(gap<=-9.5%)': block(sig[sig['is_ld']], '跌停开盘'),
        '精确跌停价开盘': block(sig[sig['is_ld_exact']], '精确跌停开盘'),
        '非跌停低开(-9.5%<gap<=-3%)': block(sig[~sig['is_ld']], '非跌停低开'),
        '全低开': block(sig, '全部低开'),
    }
    # 跌停开盘亏损的次日走势：T+2（需拼接次日行情——这里用 ev 的 close1 不够，简化用 buy_ret 分布说明）
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n[saved] {OUT}')


if __name__ == '__main__':
    main()
