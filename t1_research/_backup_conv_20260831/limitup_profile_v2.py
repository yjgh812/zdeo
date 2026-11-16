# -*- coding: utf-8 -*-
"""
涨停事件画像 v2（2026-08-10，知微）
=================================================
在 v1（封板时间→次日溢价）基础上扩展：
  - 连板数：当日往前连续涨停交易日数（含当日，首板=1）
  - 一字板：开盘价 >= 涨停价
  - 炸板事件：当日 high>=涨停价 但收盘未封住（对照：炸板股次日表现）
  - 交叉：封板时间 × 连板数 → 次日≥3%/涨停/平均
数据：本地通达信 vipdoc（日线 .day + 5分钟 .lc5），口径同 v1
  - 沪深主板+创业板（剔 688/689 科创；北交由目录范围排除）
  - 涨停判定：收盘价==round(昨收×(1+r),2)，主板 r=10%、创业板 r=20%
  - 封板时间：5分钟线首次 high>=涨停价 的 bar 起始时刻
  - 炸板：当日 high>=涨停价 且收盘未封住
输出：D:\\zcodesj\\t1_research\\limitup_profile_v2_YYYYMMDD.json
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

TDX = Path(r'D:\anzhuang\tdx\vipdoc')
START = '20260201'
END = '20260807'
OUT = Path(r'D:\zcodesj\t1_research') / f'limitup_profile_v2_{END}.json'

DAY_DTYPE = np.dtype([('date', '<u4'), ('open', '<u4'), ('high', '<u4'), ('low', '<u4'),
                      ('close', '<u4'), ('amount', '<f4'), ('volume', '<u4'), ('unused', '<u4')])
MIN_DTYPE = np.dtype([('date', '<u2'), ('time', '<u2'), ('open', '<f4'), ('high', '<f4'),
                      ('low', '<f4'), ('close', '<f4'), ('amount', '<f4'), ('volume', '<u4'),
                      ('unused', '<u4')])


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


def read_min5(path: Path) -> pd.DataFrame:
    raw = path.read_bytes()
    n = len(raw) // 32
    if n == 0:
        return pd.DataFrame()
    d = np.frombuffer(raw[: n * 32], dtype=MIN_DTYPE)
    dd, tt = d['date'].astype(np.int64), d['time'].astype(np.int64)
    idx = pd.to_datetime(pd.DataFrame({
        'year': dd // 2048 + 2004, 'month': (dd % 2048) // 100,
        'day': (dd % 2048) % 100, 'hour': tt // 60, 'minute': tt % 60,
    }), errors='coerce')
    return pd.DataFrame({
        'open': d['open'], 'high': d['high'], 'low': d['low'],
        'close': d['close'], 'volume': d['volume'].astype(np.float64),
    }, index=idx)


def limit_ratio(code: str) -> float:
    return 0.20 if code.startswith(('300', '301')) else 0.10


def streak_of(day: pd.DataFrame, i: int, lims: pd.Series) -> int:
    """从 i 日往前数连续涨停天数（含 i 日）。lims 为每日本应有涨停价序列。"""
    s = 0
    j = i
    while j >= 0:
        c, lim = day['close'].iloc[j], lims.iloc[j]
        if pd.notna(lim) and abs(c - lim) <= 0.001:
            s += 1
            j -= 1
        else:
            break
    return s


def profile_stock(args):
    code, market = args
    day_path = TDX / market / 'lday' / f'{market}{code}.day'
    if not day_path.exists() or code.startswith(('688', '689')):
        return []
    try:
        day = read_day(day_path)
    except Exception:
        return []
    if len(day) < 20:
        return []
    day = day[day.index >= '20251201']  # 多取一个月供连板回看
    if len(day) == 0:
        return []

    ratio = limit_ratio(code)
    lims = (day['close'].shift(1) * (1 + ratio)).round(2)
    sealed = ((day['close'] - lims).abs() <= 0.001) & day['close'].notna()
    touched = (day['high'] >= lims - 1e-9) & day['close'].notna()

    m5 = None
    events = []
    for i in range(len(day)):
        dt = day.index[i]
        if dt < pd.Timestamp(START):
            continue
        lim = lims.iloc[i]
        if pd.isna(lim):
            continue
        c, o, h = day['close'].iloc[i], day['open'].iloc[i], day['high'].iloc[i]
        is_sealed = sealed.iloc[i]
        if not touched.iloc[i]:
            continue  # 当日未触及涨停价（既非封板也非炸板）

        # T+1 表现（含可交易性：次日开盘买入 → 收盘）
        ret1, ge3, lu1 = np.nan, 0, 0
        open_ret, buy_ret, buy_ge3 = np.nan, np.nan, 0
        if i + 1 < len(day):
            c0, c1 = c, day['close'].iloc[i + 1]
            o1 = day['open'].iloc[i + 1]
            ret1 = c1 / c0 - 1
            ge3 = 1 if ret1 >= 0.03 else 0
            lim1 = round(c0 * (1 + ratio), 2)
            lu1 = 1 if abs(c1 - lim1) <= 0.001 else 0
            open_ret = o1 / c0 - 1
            if o1 > 0:
                buy_ret = c1 / o1 - 1
                buy_ge3 = 1 if buy_ret >= 0.03 else 0

        # 次新判定：事件日往前历史交易日 < 60
        is_new = int(i < 60)

        # 封板/炸板时间
        seal, brk = None, None
        if m5 is None:
            min_path = TDX / market / 'fzline' / f'{market}{code}.lc5'
            if min_path.exists():
                try:
                    m5 = read_min5(min_path)
                except Exception:
                    m5 = pd.DataFrame()
        if m5 is not None and len(m5) > 0:
            m = m5[m5.index.normalize() == pd.Timestamp(dt).normalize()]
            hit = m[m['high'] >= lim - 1e-9]
            if len(hit) > 0:
                t = hit.index[0].strftime('%H:%M')
                if is_sealed:
                    seal = t
                else:
                    brk = t  # 触及但未封住→炸板事件

        events.append({
            'code': code, 'date': str(dt.date()),
            'kind': 'sealed' if is_sealed else 'broken',
            'seal': seal, 'brk': brk,
            'one_word': int(o >= lim - 1e-9),
            'streak': streak_of(day, i, lims),
            'is_new': is_new,
            'ret1': round(ret1, 6) if ret1 == ret1 else None,
            'ge3': ge3, 'lu1': lu1,
            'open_ret': round(open_ret, 6) if open_ret == open_ret else None,
            'buy_ret': round(buy_ret, 6) if buy_ret == buy_ret else None,
            'buy_ge3': buy_ge3,
        })
    return events


def bucket_of(t: str):
    if not isinstance(t, str):
        return '未知'
    h, m = t.split(':')
    x = int(h) * 60 + int(m)
    if x <= 600:
        return '09:30-10:00'
    if x <= 660:
        return '10:00-11:00'
    if x <= 780:
        return '11:00-13:00'
    if x <= 840:
        return '13:00-14:00'
    if x <= 885:
        return '14:00-14:45'
    return '14:45-15:00'


def report(df: pd.DataFrame, tag: str, use_buy: bool = False):
    """统计。默认收盘对收盘（ret1/ge3/lu1）；use_buy=True 用开盘买入口径（buy_ret/buy_ge3）。"""
    df = df[df['ret1'].notna()].copy()
    n_raw = len(df)
    df = df[(df['ret1'] >= -0.3) & (df['ret1'] <= 0.3)]  # 剔除权/数据异常
    n_adj = len(df)
    if n_adj == 0:
        print(f'[{tag}] 无样本（原始 {n_raw} 全为异常）')
        return {}
    if use_buy:
        dd = df[df['buy_ret'].notna() & (df['buy_ret'] >= -0.3) & (df['buy_ret'] <= 0.3)]
        n = len(dd)
        ge3 = dd['buy_ge3'].mean() * 100
        avg = dd['buy_ret'].mean() * 100
        lu1 = float('nan')  # 开盘买入口径不评收盘涨停
    else:
        n = n_adj
        ge3 = df['ge3'].mean() * 100
        lu1 = df['lu1'].mean() * 100
        avg = df['ret1'].mean() * 100
    extra = f'（剔除异常 {n_raw - n_adj} 条）' if n_raw != n_adj else ''
    print(f'[{tag}] n={n:>5}  次日≥3%={ge3:5.2f}%  涨停={lu1:5.2f}%  平均={avg:6.3f}%{extra}')
    return {'n': n, 'ge3_pct': round(float(ge3), 2),
            'lu1_pct': round(float(lu1), 2) if lu1 == lu1 else None,
            'avg_ret_pct': round(float(avg), 3), 'n_adj_removed': n_raw - n_adj}


def main():
    codes = []
    for market in ('sh', 'sz'):
        lday = TDX / market / 'lday'
        if lday.exists():
            codes += [(f.stem[2:], market) for f in lday.glob(f'{market}*.day')]
    print(f'[info] 扫描 {len(codes)} 只股票（窗口 {START}~{END}）', flush=True)

    events = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(profile_stock, c) for c in codes]
        for f in as_completed(futs):
            events.extend(f.result())

    df = pd.DataFrame(events)
    if len(df) == 0:
        print('[warn] 无事件')
        return
    df['ret1'] = pd.to_numeric(df['ret1'], errors='coerce')
    df['bucket'] = df['seal'].map(bucket_of)

    out = {'window': [START, END], 'n_events': int(len(df)),
           'n_sealed': int((df['kind'] == 'sealed').sum()), 'n_broken': int((df['kind'] == 'broken').sum())}

    print(f"\n===== 涨停画像 v2 {START}~{END}（事件 {len(df)}：封板 {(df['kind']=='sealed').sum()} / 炸板 {(df['kind']=='broken').sum()}）=====")

    sealed = df[df['kind'] == 'sealed'].copy()
    broken = df[df['kind'] == 'broken'].copy()
    seal_ok = sealed[sealed['is_new'] == 0]  # 剔次新（红线）

    print('\n-- 封板股总体 vs 炸板股对照（已剔±30%异常） --')
    out['sealed_overall'] = report(sealed, '封板')
    out['broken_overall'] = report(broken, '炸板')

    print('\n-- 封板股（剔次新后）：按封板时间分桶 --')
    out['by_seal_bucket'] = {}
    for b, g in seal_ok.groupby('bucket'):
        out['by_seal_bucket'][b] = report(g, f'封板{b}')

    print('\n-- 封板股（剔次新后）：按连板数 --')
    out['by_streak'] = {}
    for s, g in seal_ok.groupby('streak'):
        out['by_streak'][int(s)] = report(g, f'{int(s)}板')

    print('\n-- 封板股（剔次新后）：一字 vs 非一字 --')
    out['one_word'] = report(seal_ok[seal_ok['one_word'] == 1], '一字')
    out['not_one_word'] = report(seal_ok[seal_ok['one_word'] == 0], '非一字')

    print('\n-- 可交易性：次日开盘买入（封板股，剔次新） --')
    out['buy_one_word'] = report(seal_ok[seal_ok['one_word'] == 1], '一字-开盘买入', use_buy=True)
    out['buy_not_one_word'] = report(seal_ok[seal_ok['one_word'] == 0], '非一字-开盘买入', use_buy=True)
    out['buy_streak3plus'] = report(seal_ok[seal_ok['streak'] >= 3], '3板+-开盘买入', use_buy=True)

    print('\n-- 次日开盘幅度分层（非一字封板股，开盘买入→收盘，剔次新） --')
    ne = seal_ok[seal_ok['one_word'] == 0].copy()
    ne['gap'] = pd.cut(ne['open_ret'], bins=[-1, -0.03, 0, 0.03, 0.07, 1],
                       labels=['低开<-3%', '-3~0%', '0~3%', '3~7%', '高开>7%'])
    out['by_gap'] = {}
    for g, sub in ne.groupby('gap', observed=True):
        out['by_gap'][str(g)] = report(sub, f'gap {g}-开盘买入', use_buy=True)

    print('\n-- 交叉：封板时间 × 连板数（次日≥3% 命中率，剔次新） --')
    cross = seal_ok.pivot_table(index='streak', columns='bucket', values='ge3',
                                aggfunc='mean').mul(100).round(1)
    cnt = seal_ok.pivot_table(index='streak', columns='bucket', values='ge3', aggfunc='count')
    print(cross.to_string())
    out['cross_ge3'] = {int(i): {str(c): (float(v) if pd.notna(v) else None)
                                 for c, v in row.items() if pd.notna(v)} for i, row in cross.iterrows()}
    out['cross_n'] = {int(i): {str(c): int(v) for c, v in row.items() if pd.notna(v)} for i, row in cnt.iterrows()}

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n[saved] {OUT}')


if __name__ == '__main__':
    main()
