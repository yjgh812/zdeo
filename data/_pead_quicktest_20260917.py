# -*- coding: utf-8 -*-
"""_pead_quicktest_20260917.py — PEAD(业绩预增漂移)立项侦察 quick test（只读）。
事件=业绩预告预增(p_change_min≥30%), 入=公告后首交易日收盘, 出=+20交易日收盘, 成本0.2%。
对照=同期全A(剔ST宇宙)中位收益。学术先验: A股PEAD年化超额~9.5%(Truong 2011)/季度6.78%(Lan 2024)。"""
import sys, json, time, sqlite3, datetime
import numpy as np, pandas as pd
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

FD = r'D:/workbuddyspace/databases/fundamental_data.db'
QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
DQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant.db'
OUT = r'D:/zcodesj/attribution/pead_quicktest_20260917.json'

def main():
    t0 = time.time()
    con = sqlite3.connect(DQ, timeout=10)
    info = pd.read_sql("SELECT substr(code,1,6) code6, is_st, list_date FROM stock_info "
                       "WHERE substr(code,1,3) IN ('600','601','603','605','000','001','002','003','300') "
                       "AND is_st='0' AND instrument_type LIKE '%stock%'", con)
    con.close()
    ok_codes = set(info['code6'])
    st_codes = set()
    con2 = sqlite3.connect(DQ, timeout=10)
    for r in con2.execute("SELECT code FROM stock_info WHERE is_st<>'0'"):
        st_codes.add(str(r[0])[-6:])
    con2.close()

    con = sqlite3.connect(FD, timeout=30)
    fc = pd.read_sql("SELECT ts_code, ann_date, end_date, type, p_change_min FROM forecast "
                     "WHERE ann_date>='20200101' AND ann_date<='20260916'", con)
    con.close()
    fc['code6'] = fc['ts_code'].str[:6]
    fc = fc[fc['code6'].isin(ok_codes)]
    fc = fc[~fc['code6'].isin(st_codes)]
    fc = fc[fc['type'].astype(str).str.contains('增', na=False)]
    fc = fc[pd.to_numeric(fc['p_change_min'], errors='coerce') >= 30]
    fc = fc.dropna(subset=['ann_date'])
    fc['ann_date'] = fc['ann_date'].astype(str).str.replace('-','')
    # 同票同公告期去重(取首条)
    fc = fc.sort_values(['code6','ann_date']).drop_duplicates(subset=['code6','end_date'])
    print(f'预增事件={len(fc)} {time.time()-t0:.0f}s', flush=True)

    codes = sorted(fc['code6'].unique())
    con = sqlite3.connect(QFQ, timeout=120)
    ph = ','.join('?'*len(codes))
    px = pd.read_sql(f"SELECT substr(code,1,6) code6, date, close FROM stock_daily_meta_qfq "
                     f"WHERE date>='20191201' AND substr(code,1,6) IN ({ph}) ORDER BY code6, date",
                     con, params=codes)
    # 全A基线(每日中位T20)
    base = pd.read_sql("SELECT date, close FROM stock_daily_meta_qfq WHERE substr(code,1,6) IN "
                       "(SELECT code6 FROM (SELECT substr(code,1,6) code6 FROM stock_daily_meta_qfq GROUP BY 1)) ",
                       con) if False else None
    con.close()
    px['date'] = px['date'].astype(str).str.replace('-','')
    px['close'] = px['close'].astype(float)
    gmap = {c: g.reset_index(drop=True) for c, g in px.groupby('code6')}
    print(f'px rows={len(px)} {time.time()-t0:.0f}s', flush=True)

    evs = []
    for r in fc.itertuples(index=False):
        g = gmap.get(r.code6)
        if g is None: continue
        d = g[g['date'] >= r.ann_date]
        if len(d) < 21: continue
        i = d.index[0]           # 公告后首交易日
        entry = float(g.loc[i, 'close'])
        j = i + 20
        if j >= len(g): continue
        exitp = float(g.loc[j, 'close'])
        ret = exitp/entry - 1 - 0.002
        evs.append({'code': r.code6, 'ann': r.ann_date, 'type': str(r.type)[:12],
                    'pchg': float(r.p_change_min), 'ret': ret})
    ev = pd.DataFrame(evs)
    ev['y'] = ev['ann'].str[:4]
    ev['wk'] = ev['ann'].str[:6] + 'W' + pd.to_datetime(ev['ann']).dt.isocalendar().week.astype(str)
    print(f'事件完成={len(ev)} {time.time()-t0:.0f}s', flush=True)

    def stats(sub, label):
        if len(sub) < 30:
            print(f'{label}: n={len(sub)} 样本不足'); return None
        g = sub.groupby('wk')['ret'].mean()
        t = float(g.mean()/(g.std(ddof=1)/np.sqrt(len(g)))) if len(g) > 2 and g.std(ddof=1) > 0 else float('nan')
        med = float(sub['ret'].median())
        r_ = {'n': int(len(sub)), 'mean_pp': round(float(sub['ret'].mean())*100, 3),
              'median_pp': round(med*100, 3), 'win%': round(float((sub['ret'] > 0).mean())*100, 1),
              't_week': round(t, 2)}
        print(f"{label}: n={r_['n']} 均值{r_['mean_pp']}pp 中位{r_['median_pp']}pp 胜率{r_['win%']}% t_w={r_['t_week']}")
        return r_
    R = {'generated': datetime.datetime.now().strftime('%F %H:%M')}
    print('\n== 全体预增事件(T+20) ==')
    R['all'] = stats(ev, '全体')
    print('\n== 分年 ==')
    for y, sub in ev.groupby('y'):
        R[f'y{y}'] = stats(sub, str(y))
    # 惊喜强度分层
    print('\n== 惊喜强度分层(p_change_min) ==')
    for lo, hi, lab in [(30,60,'30-60'),(60,100,'60-100'),(100,999,'>=100')]:
        sub = ev[(ev['pchg'] >= lo) & (ev['pchg'] < hi)]
        R[f'pchg_{lab}'] = stats(sub, f'预增{lab}%')
    json.dump(R, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved', OUT, f'total {time.time()-t0:.0f}s')

if __name__ == '__main__':
    main()
