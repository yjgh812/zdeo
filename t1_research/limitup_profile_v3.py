# -*- coding: utf-8 -*-
"""
涨停画像 v3：低开承接扩窗验证（2026-08-10，知微）
=================================================
目标：验证 v2 发现"非一字封板股次日低开<-3% 开盘买入正期望"的跨年稳定性。
窗口：2025-01-01 ~ 2026-08-07（20 个月，v2 仅 6 个月）。
新增：
  - 按年分组（2025 vs 2026）稳定性
  - 止损敏感性（-3/-5/-7%，日线 low 近似）
  - 交叉增强：低开×早封板（09:30-10:00）、低开×连板≥2
数据：TDX vipdoc 日线+5分钟线，口径同 v2（剔科创、次新、±30% 异常）
输出：D:\\zcodesj\\t1_research\\limitup_profile_v3_YYYYMMDD.json
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

TDX = Path(r'D:\anzhuang\tdx\vipdoc')
START = '20250101'
END = '20260807'
OUT = Path(r'D:\zcodesj\t1_research') / f'limitup_profile_v3_{END}.json'

# A 股白名单（剔逆回购/基金/B股/债券/科创/北交，2026-08-10 修复）
STOCK_PREFIXES = ('600', '601', '603', '605', '000', '001', '002', '003', '300', '301')

import tdx_common as tc  # 2026-08-31 收敛: 解析统一走 tdx_common(委托 tdx_reader)




def limit_ratio(code: str) -> float:
    return 0.20 if code.startswith(('300', '301')) else 0.10


def streak_of(day: pd.DataFrame, i: int, lims: pd.Series) -> int:
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
    if not day_path.exists() or not code.startswith(STOCK_PREFIXES):
        return []
    try:
        day = tc.read_day(day_path)
    except Exception:
        return []
    if len(day) < 20:
        return []
    day = day[day.index >= '20241001']  # 回看 3 个月供连板/次新判定
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
        if not touched.iloc[i]:
            continue

        # T+1 表现（次日 open/high/low/close 全取，供 gap 分层与止损模拟）
        rec = {'code': code, 'date': str(dt.date()), 'kind': 'sealed' if sealed.iloc[i] else 'broken',
               'streak': streak_of(day, i, lims), 'is_new': int(i < 60),
               'one_word': int(o >= lim - 1e-9)}
        if i + 1 < len(day):
            c1, o1, h1, l1 = (day['close'].iloc[i + 1], day['open'].iloc[i + 1],
                              day['high'].iloc[i + 1], day['low'].iloc[i + 1])
            rec['ret1'] = round(c1 / c - 1, 6)
            rec['ge3'] = int(rec['ret1'] >= 0.03)
            lim1 = round(c * (1 + ratio), 2)
            rec['lu1'] = int(abs(c1 - lim1) <= 0.001)
            rec['open_ret'] = round(o1 / c - 1, 6)
            rec['buy_ret'] = round(c1 / o1 - 1, 6) if o1 > 0 else None
            rec['buy_ge3'] = int(rec['buy_ret'] >= 0.03) if rec['buy_ret'] is not None else 0
            rec['low_ret'] = round(l1 / o1 - 1, 6) if o1 > 0 else None
        else:
            rec.update({'ret1': None, 'ge3': 0, 'lu1': 0, 'open_ret': None,
                        'buy_ret': None, 'buy_ge3': 0, 'low_ret': None})
        # 封板时间（仅封板股需要）
        if sealed.iloc[i]:
            if m5 is None:
                min_path = TDX / market / 'fzline' / f'{market}{code}.lc5'
                if min_path.exists():
                    try:
                        m5 = tc.read_min5(min_path)
                    except Exception:
                        m5 = pd.DataFrame()
            if m5 is not None and len(m5) > 0:
                m = m5[m5.index.normalize() == pd.Timestamp(dt).normalize()]
                hit = m[m['high'] >= lim - 1e-9]
                if len(hit) > 0:
                    rec['seal'] = hit.index[0].strftime('%H:%M')
        events.append(rec)
    return events


def bucket_of(t):
    if not isinstance(t, str):
        return '未知'
    h, m = t.split(':')
    x = int(h) * 60 + int(m)
    if x <= 600:
        return '早封板09:30-10:00'
    if x <= 660:
        return '10:00-11:00'
    if x <= 780:
        return '11:00-13:00'
    if x <= 840:
        return '13:00-14:00'
    if x <= 885:
        return '14:00-14:45'
    return '14:45-15:00'


def report(df: pd.DataFrame, tag: str, stop_list=(0.0,)):
    """开盘买入口径统计 + 止损敏感性。stop=0 表示无止损。"""
    dd = df[df['buy_ret'].notna() & (df['ret1'] >= -0.3) & (df['ret1'] <= 0.3)].copy()
    dd = dd[(dd['buy_ret'] >= -0.3) & (dd['buy_ret'] <= 0.3)]
    if len(dd) == 0:
        print(f'[{tag}] 无样本')
        return None
    row = {'n': int(len(dd))}
    for s in stop_list:
        if s == 0:
            r = dd['buy_ret']
        else:
            r = np.where(dd['low_ret'] <= -s, -s, dd['buy_ret'])
            r = pd.Series(r, index=dd.index)
        row[f'ge3_s{int(s*100):02d}' if s else 'ge3'] = round(float((r >= 0.03).mean() * 100), 2)
        row[f'avg_s{int(s*100):02d}' if s else 'avg'] = round(float(r.mean() * 100), 3)
        row[f'med_s{int(s*100):02d}' if s else 'med'] = round(float(r.median() * 100), 3)
        row[f'maxdd_s{int(s*100):02d}' if s else 'maxdd'] = round(float(r.min() * 100), 2)
    print(f"[{tag}] n={row['n']:>5}  " + '  '.join(
        f"≥3%={row[k]:>5.2f}% 平均={row[v]:>6.3f}%" for k, v in
        [('ge3', 'avg')] + [(f'ge3_s{s}', f'avg_s{s}') for s in (3, 5, 7) if f'ge3_s{s}' in row]))
    return row


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
    df['open_ret'] = pd.to_numeric(df['open_ret'], errors='coerce')
    df['buy_ret'] = pd.to_numeric(df['buy_ret'], errors='coerce')
    df['low_ret'] = pd.to_numeric(df['low_ret'], errors='coerce')
    df['year'] = df['date'].str[:4]
    df['bucket'] = df['seal'].map(bucket_of)

    out = {'window': [START, END], 'n_events': int(len(df)),
           'n_sealed': int((df['kind'] == 'sealed').sum()), 'n_broken': int((df['kind'] == 'broken').sum())}

    seal_ok = df[(df['kind'] == 'sealed') & (df['is_new'] == 0)].copy()
    ne = seal_ok[seal_ok['one_word'] == 0].copy()
    ne['gap'] = pd.cut(ne['open_ret'], bins=[-1, -0.03, 0, 0.03, 0.07, 1],
                       labels=['低开<-3%', '-3~0%', '0~3%', '3~7%', '高开>7%'])

    print(f"\n===== 涨停画像 v3 {START}~{END}（封板 {out['n_sealed']} / 炸板 {out['n_broken']}，剔次新后封板 {len(seal_ok)}）=====")

    print('\n-- 非一字封板股：次日开盘幅度分层（2025+2026 合并） --')
    out['by_gap'] = {}
    for g, sub in ne.groupby('gap', observed=True):
        out['by_gap'][str(g)] = report(sub, f'gap {g}', (0.0, 0.03, 0.05, 0.07))

    print('\n-- 低开<-3% 承接：按年分组（跨年稳定性） --')
    dip = ne[ne['gap'] == '低开<-3%']
    out['by_year'] = {}
    for y, g in dip.groupby('year'):
        out['by_year'][str(y)] = report(g, f'{y} 低开<-3%', (0.0, 0.05))
    out['by_year']['all'] = report(dip, '全部 低开<-3%', (0.0, 0.05))

    print('\n-- 交叉增强：低开<-3% × 早封板 / 连板≥2 --')
    early = dip[dip['bucket'] == '早封板09:30-10:00']
    st2 = dip[dip['streak'] >= 2]
    out['cross_early'] = report(early, '低开×早封板', (0.0, 0.05))
    out['cross_streak2'] = report(st2, '低开×连板≥2', (0.0, 0.05))
    out['cross_early_streak2'] = report(early[early['streak'] >= 2], '低开×早封板×连板≥2', (0.0, 0.05))

    print('\n-- 对照组：一字板 / 炸板 低开<-3% --')
    ow_dip = seal_ok[(seal_ok['one_word'] == 1) & (seal_ok['open_ret'] <= -0.03)]
    br_dip = df[(df['kind'] == 'broken') & (df['is_new'] == 0) & (df['open_ret'] <= -0.03)]
    out['ctrl_oneword_dip'] = report(ow_dip, '一字板低开(罕见)', (0.0,))
    out['ctrl_broken_dip'] = report(br_dip, '炸板股低开<-3%', (0.0, 0.05))

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n[saved] {OUT}')


if __name__ == '__main__':
    main()
