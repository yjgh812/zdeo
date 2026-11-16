# -*- coding: utf-8 -*-
"""_pead_prereg_eval_20260917.py — PEAD 预增漂移 prereg 评估（六问裁决口径，零调参）。
规则以 reports/PREREG_PEAD_20260917.md 为准：
事件=预增(p_change_min≥30, 2020-2026, 剔ST/主板创业板)；
主口径=T+1开盘入(开盘涨停剔除)+持有期low≤cost*0.92当日收盘止损+T+20收盘退(未止损时)；
集中度=同日同申万L1行业按p_change_min降序取前3；对照臂=预减/首亏同流程；
切分=2020-2023验证(三闸)/2024-2026确认；成本0.2%。"""
import os, sys, json, hashlib, time, sqlite3, datetime
import numpy as np, pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:/zcodesj/data')

PREREG = r'D:/zcodesj/reports/PREREG_PEAD_20260917.md'
FD = r'D:/workbuddyspace/databases/fundamental_data.db'
QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
DQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant.db'
SW = r'D:/workbuddyspace/databases/sw_industry.db'
OUT = r'D:/zcodesj/attribution/pead_prereg_result_20260917.json'
COST = 0.002
STOP = 0.92
HOLD = 20
TOPN_IND = 3

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(65536), b''):
            h.update(c)
    return h.hexdigest()

def main():
    t0 = time.time()
    pre_hash = sha256(PREREG)
    from _industry_map_20260916 import MAP

    # 1) 预告事件
    con = sqlite3.connect(FD, timeout=30)
    fc = pd.read_sql("SELECT ts_code, ann_date, end_date, type, p_change_min FROM forecast "
                     "WHERE ann_date>='20200101' AND ann_date<='20260916'", con)
    con.close()
    fc['code6'] = fc['ts_code'].str[:6]
    fc['ann_date'] = fc['ann_date'].astype(str).str.replace('-','')
    fc['type'] = fc['type'].astype(str)
    fc['pchg'] = pd.to_numeric(fc['p_change_min'], errors='coerce')
    fc = fc.dropna(subset=['pchg'])
    ok3 = ('600','601','603','605','000','001','002','003','300')
    fc = fc[fc['code6'].str[:3].isin(ok3)]
    # ST 剔除(当前名单近似)
    con = sqlite3.connect(DQ, timeout=10)
    st = set(r[0] for r in con.execute("SELECT code FROM stock_info WHERE is_st<>'0'"))
    con.close()
    fc = fc[~fc['code6'].isin(st)]
    # 事件臂
    pos = fc[fc['type'].str.contains('增') & (fc['pchg'] >= 30)].copy()
    neg = fc[fc['type'].str.contains('减|首亏')].copy()
    for d_ in (pos, neg):
        d_.sort_values(['code6','ann_date'], inplace=True)
        d_.drop_duplicates(subset=['code6','end_date'], inplace=True)
        # 同票60日内只取首次
        keep, last = [], {}
        for r in d_.itertuples(index=False):
            if r.code6 in last and (datetime.datetime.strptime(r.ann_date,'%Y%m%d') - last[r.code6]).days < 60:
                continue
            last[r.code6] = datetime.datetime.strptime(r.ann_date,'%Y%m%d')
            keep.append(r)
        d_ = pd.DataFrame(keep)
    pos['arm'] = 'pos'; neg['arm'] = 'neg'
    ev = pd.concat([pos, neg], ignore_index=True)
    print(f'预增事件={len(pos)} 预减/首亏事件={len(neg)} {time.time()-t0:.0f}s', flush=True)

    # 2) 行业(申万L1归一)
    con = sqlite3.connect(DQ, timeout=10)
    info = pd.read_sql("SELECT code, industry FROM stock_info", con)
    con.close()
    info['code6'] = info['code'].astype(str).str[-6:]
    ind = dict(zip(info['code6'], info['industry'].map(lambda x: MAP.get(x, x))))
    ev['sw'] = ev['code6'].map(ind)

    # 3) qfq 价格(OHLC)
    codes = sorted(ev['code6'].unique())
    con = sqlite3.connect(QFQ, timeout=120)
    ph = ','.join('?'*len(codes))
    px = pd.read_sql(f"SELECT substr(code,1,6) code6, date, open, high, low, close FROM stock_daily_meta_qfq "
                     f"WHERE date>='20191201' AND substr(code,1,6) IN ({ph}) ORDER BY code6, date",
                     con, params=codes)
    con.close()
    px['date'] = px['date'].astype(str).str.replace('-','')
    for c in ('open','high','low','close'):
        px[c] = px[c].astype(float)
    gmap = {c: g.reset_index(drop=True) for c, g in px.groupby('code6')}

    # 4) 事件流程(含集中度)
    ev = ev.sort_values(['ann_date','sw','pchg'], ascending=[True, True, False])
    taken = set()   # (入场日, 行业) 计数
    res = []
    for r in ev.itertuples(index=False):
        g = gmap.get(r.code6)
        if g is None: continue
        d = g[g['date'] >= r.ann_date]
        if len(d) < 2: continue
        i = d.index[0]
        opn = float(g.loc[i, 'open'])
        prev_c = float(g.loc[i-1, 'close']) if i > 0 else None
        if prev_c is None or prev_c <= 0: continue
        lim = 1.19 if r.code6[:3] == '300' else 1.095
        if opn >= prev_c*lim: continue   # 开盘涨停不可成交
        key = (d['date'].iloc[0], r.sw)
        cnt = sum(1 for k in taken if k == key)
        if cnt >= TOPN_IND: continue
        taken.add(key)
        entry = opn
        stopp = entry*STOP
        exit_p = None; exit_tag = 'T20'
        seg = g.loc[i: i+HOLD]
        for _, row in seg.iterrows():
            if float(row['low']) <= stopp:
                exit_p = float(row['close']); exit_tag = 'STOP'
                break
        if exit_p is None:
            j = min(i+HOLD, len(g)-1)
            exit_p = float(g.loc[j, 'close'])
        ret = exit_p/entry - 1 - COST
        res.append({'code': r.code6, 'ann': r.ann_date, 'arm': r.arm, 'pchg': float(r.pchg),
                    'ret': ret, 'exit_tag': exit_tag, 'seg': 'confirm' if r.ann_date >= '20240101' else 'valid',
                    'year': r.ann_date[:4], 'wk': r.ann_date[:6]+'W'+str(pd.to_datetime(r.ann_date).isocalendar().week),
                    'sw': r.sw})
    df = pd.DataFrame(res)
    print(f'evaluated={len(df)} {time.time()-t0:.0f}s', flush=True)

    def stats(sub, label):
        if len(sub) < 30:
            print(f'{label}: n={len(sub)} 样本不足'); return None
        g = sub.groupby('wk')['ret'].mean()
        t = float(g.mean()/(g.std(ddof=1)/np.sqrt(len(g)))) if len(g) > 2 and g.std(ddof=1) > 0 else float('nan')
        r_ = {'n': int(len(sub)), 'mean_pp': round(float(sub['ret'].mean())*100, 3),
              'median_pp': round(float(sub['ret'].median())*100, 3),
              'win%': round(float((sub['ret'] > 0).mean())*100, 1),
              't_week': round(t, 2), 'stop_rate%': round(float((sub['exit_tag']=='STOP').mean())*100, 1)}
        print(f"{label}: n={r_['n']} 均值{r_['mean_pp']}pp 中位{r_['median_pp']}pp 胜率{r_['win%']}% t_w={r_['t_week']} 止损率{r_['stop_rate%']}%")
        return r_

    R = {'prereg_sha256': pre_hash, 'cost': COST, 'stop': STOP, 'hold': HOLD, 'topn_ind': TOPN_IND}
    main_ = df[(df['arm'] == 'pos') & (df['seg'] == 'valid')]
    conf = df[(df['arm'] == 'pos') & (df['seg'] == 'confirm')]
    negv = df[df['arm'] == 'neg']
    print('\n== G1/G2 验证段(2020-2023, 主口径) ==')
    R['valid'] = stats(main_, '验证段')
    if main_.len if hasattr(main_, 'len') else None: pass
    mono = []
    for lo, hi, lab in [(30,60,'30-60'),(60,100,'60-100'),(100,999,'>=100')]:
        sub = main_[(main_['pchg'] >= lo) & (main_['pchg'] < hi)]
        if len(sub) >= 30:
            mono.append(float(sub['ret'].mean()))
            print(f'  预增{lab}%: n={len(sub)} 均值={float(sub["ret"].mean())*100:+.3f}pp')
    g2_mono = all(mono[i] <= mono[i+1] for i in range(len(mono)-1)) if len(mono) == 3 else False
    g1 = (R['valid'] and R['valid']['mean_pp'] > 0 and R['valid']['t_week'] is not None and R['valid']['t_week'] >= 2)
    g2 = (R['valid'] and R['valid']['win%'] > 50 and g2_mono)
    print('\n== G3 确认段(2024-2026) ==')
    R['confirm'] = stats(conf, '确认段')
    g3 = (R['confirm'] and R['confirm']['mean_pp'] > 0)
    print('\n== 安慰剂臂(预减/首亏) ==')
    R['placebo'] = stats(negv, '预减/首亏臂')
    plc_ok = (R['placebo'] and R['placebo']['mean_pp'] < 0)
    y2024 = conf[conf['year'] == '2024']
    R['confirm_2024'] = stats(y2024, '确认段2024单列')
    R['gates'] = {'G1_expect>0&t>=2': bool(g1), 'G2_win>50&mono': bool(g2),
                  'G3_confirm>0': bool(g3), 'placebo_neg_ok': bool(plc_ok),
                  'verdict': 'PASS_CANDIDATE' if (g1 and g2 and g3 and plc_ok) else 'FAIL'}
    print('GATES:', json.dumps(R['gates'], ensure_ascii=False))
    json.dump(R, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    df.to_json(OUT.replace('.json','_trades.json'), orient='records', force_ascii=False, indent=1)
    print('saved', OUT, f'total {time.time()-t0:.0f}s')

if __name__ == '__main__':
    sys.exit(main())
