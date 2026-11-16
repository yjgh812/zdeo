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

DAY_DTYPE = np.dtype([('date', '<u4'), ('open', '<u4'), ('high', '<u4'), ('low', '<u4'),
                      ('close', '<u4'), ('amount', '<f4'), ('volume', '<u4'), ('unused', '<u4')])
MIN_DTYPE = np.dtype([('date', '<u2'), ('time', '<u2'), ('open', '<f4'), ('high', '<f4'),
                      ('low', '<f4'), ('close', '<f4'), ('amount', '<f4'), ('volume', '<u4'),
                      ('unused', '<u4')])

INIT_CAP = 100_000.0
POS = 0.20          # 单笔仓位
MAX_PER_DAY = 5     # 同日最多持仓数


def read_day(path: Path) -> pd.DataFrame:
    raw = path.read_bytes()
    n = len(raw) // 32
    if n == 0:
        return pd.DataFrame()
    d = np.frombuffer(raw[: n * 32], dtype=DAY_DTYPE)
    idx = pd.to_datetime(d['date'].astype(np.int64).astype(str), format='%Y%m%d')
    return pd.DataFrame({
        'open': d['open'] / 100.0, 'high': d['high'] / 100.0,
        'low': d['low'] / 100.0, 'close': d['close'] / 100.0,
        'amount': d['amount'], 'volume': d['volume'].astype(np.float64),
    }, index=idx)


def limit_ratio(code: str) -> float:
    return 0.20 if code.startswith(('300', '301')) else 0.10


def collect(args):
    """收集 T 日封板事件 + 连板数 + T+1 行情。"""
    code, market = args
    day_path = TDX / market / 'lday' / f'{market}{code}.day'
    if not day_path.exists() or not code.startswith(STOCK_PREFIXES):
        return []
    try:
        day = read_day(day_path)
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
    # 连板数：T 日起连续封板天数（含 T 日）
    streak = np.zeros(len(day), dtype=int)
    for i in range(len(day)):
        if sealed.iloc[i]:
            streak[i] = (streak[i-1] + 1) if i > 0 else 1
        else:
            streak[i] = 0

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
            continue
        events.append({
            'code': code, 'date': str(dt.date()), 'kind': 'sealed' if sealed.iloc[i] else 'broken',
            'is_new': int(i < 60), 'one_word': int(o >= lim - 1e-9),
            'streak': int(streak[i]), 'close0': float(c),
            'open1': float(o1), 'high1': float(h1), 'low1': float(l1), 'close1': float(c1),
        })
    return events


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--pos', type=float, default=1.0)
    ap.add_argument('--max_day', type=int, default=5)
    args = ap.parse_args()
    POS, MAX_PER_DAY = args.pos, args.max_day
    codes = []
    for market in ('sh', 'sz'):
        lday = TDX / market / 'lday'
        if lday.exists():
            codes += [(f.stem[2:], market) for f in lday.glob(f'{market}*.day')]
    print(f'[info] 扫描 {len(codes)} 只 pos={POS} max_day={MAX_PER_DAY}', flush=True)

    events = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(collect, c) for c in codes]
        for f in as_completed(futs):
            events.extend(f.result())
    ev = pd.DataFrame(events)
    if len(ev) == 0:
        print('[warn] 无事件'); return
    ev = ev[(ev['is_new'] == 0) & (ev['close0'] >= 2)].copy()
    ev = ev[ev['one_word'] == 0].copy()
    ev = ev[ev['kind'] == 'sealed'].copy()

    # 情绪 regime：T 日全市场封板家数（中位数为界）
    zt_per_day = ev.groupby('date')['code'].count()
    ev['mkt_zt'] = ev['date'].map(zt_per_day)
    med_zt = zt_per_day.median()
    ev['regime'] = np.where(ev['mkt_zt'] >= med_zt, 'high', 'low')
    print(f'[info] 封板非一字 {len(ev)} | 全市场封板家数中位 {med_zt:.0f}')

    tp = 0.03
    hit = ev['high1'] >= ev['open1'] * (1 + tp)
    ev['sell'] = np.where(hit, ev['open1'] * (1 + tp), ev['close1'])
    ev['ret'] = ev['sell'] / ev['close0'] - 1
    ev = ev[np.isfinite(ev['ret']) & (ev['ret'].abs() <= 0.5)].copy()
    ev['rank_in_day'] = ev.groupby('date').cumcount()
    ev = ev[ev['rank_in_day'] < MAX_PER_DAY].copy()

    print(f'===== 分层研究 tp=+3% pos={POS} max_day={MAX_PER_DAY} =====')
    groups = [
        ('全部', np.ones(len(ev), bool)),
        ('连板1', ev['streak'] == 1),
        ('连板2', ev['streak'] == 2),
        ('连板3', ev['streak'] == 3),
        ('连板4+', ev['streak'] >= 4),
        ('情绪高', ev['regime'] == 'high'),
        ('情绪低', ev['regime'] == 'low'),
        ('连板2×情绪高', (ev['streak'] >= 2) & (ev['regime'] == 'high')),
        ('连板1×情绪低', (ev['streak'] == 1) & (ev['regime'] == 'low')),
    ]
    results = {}
    for label, mask in groups:
        d = ev[mask].copy()
        if len(d) == 0:
            print(f'===== {label}: 空 ====='); continue
        for cost in (0.0015, 0.003, 0.0045):
            d2 = d.copy()
            d2['trade_ret'] = d2['ret'] - cost
            v = run_backtest_full(d2, POS)
            m = (1 + v['ann']/100) ** (1/12) - 1
            key = f'{label}_cost{cost*100:.2f}'
            results[key] = v
            if cost == 0.003:
                hr = (d['high1'] >= d['open1'] * (1 + tp)).mean()
                print(f'===== {label} (n={v["n"]}) =====')
                print(f"  win={v['win']}% monthly={m*100:.1f}% ann={v['ann']}% mdd={v['mdd']}% avg={v['avg_ret']}% 触价率={hr*100:.1f}%")
                for y in sorted(v['per_year']):
                    py = v['per_year'][y]
                    print(f'    {y}: {py["n"]}笔 pnl {py["pnl"]:,.0f}')
    OUT = Path(r'D:/zcodesj/t1_research/t1_tp_layers_result.json')
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


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
