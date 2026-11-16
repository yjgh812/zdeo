# -*- coding: utf-8 -*-
"""板块补涨轮动·首验跟踪台账(2026-09-06, sector_rotation研究的产品化验收面).

用法:
  python sector_laggard_track.py --register   # 登记9月窗口基线(补涨12只+龙头对照组12只)
  python sector_laggard_track.py --eval       # T+20评估(补涨篮 vs 龙头篮 vs 全池等权)
台账: data/sector_rotation_ledger.json
判定(研究预注册口径): 补涨篮20日累计超额(vs全池)>0 且 补涨篮≥龙头篮 → 轮动规律9月窗口成立
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
LEDGER = r'D:\zcodesj\data\sector_rotation_ledger.json'

def basket_prices(codes, d0, d1):
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    ph = ','.join('?'*len(codes))
    px = pd.read_sql_query(f"SELECT code,date,close FROM stock_daily_meta_qfq "
                           f"WHERE code IN ({ph}) AND date IN (?,?)",
                           con, params=codes + [d0, d1]).set_index(['code', 'date'])
    con.close()
    return px['close'].unstack()

def main():
    if '--register' in sys.argv:
        m = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v5_full.pkl'))
        m['ym'] = m['date'].str[:6]
        m26 = m[m['ym'] >= '202601'].sort_values(['code', 'date'])
        cl = m26.groupby(['code', 'ym'])['close'].agg(['first', 'last']).reset_index()
        cl = cl.sort_values(['code', 'ym'])
        cl['prev_last'] = cl.groupby('code')['last'].shift(1)
        cl['mret'] = np.where(cl['prev_last'].notna(), cl['last']/cl['prev_last']-1, np.nan)
        ind = m26.groupby('code')['L1'].first().reset_index()
        cl = cl.merge(ind, on='code')
        scan = json.load(open(r'D:\zcodesj\data\sector_laggard_scan_20260906.json', encoding='utf-8'))
        laggard = [c['code'] for c in scan['cands'] if c['排雷'] == 0][:12]   # 0雷12只
        # 龙头对照组: 每个8月热门板块的Q5前2只(8月涨幅最高2只)
        leaders = []
        hot_secs = set(c['板块'] for c in scan['cands'])
        for l1 in hot_secs:
            aug = cl[(cl['ym'] == '202608') & (cl['L1'] == l1) & cl['mret'].notna()]
            leaders.extend(aug.nlargest(2, 'mret')['code'].tolist())
        cs = m[m['date'] == m['date'].max()].set_index('code')
        def px_of(code):
            r = cs.loc[code]
            return float(r['close'] if not isinstance(r, pd.DataFrame) else r.iloc[0]['close'])
        reg = {'kind': 'sept_window', 'date': m['date'].max(),
               'laggard12': laggard, 'leader_ctrl': sorted(set(leaders))[:12],
               'baselines': {c: px_of(c) for c in laggard + sorted(set(leaders))[:12] if c in cs.index},
               'note': '补涨篮=0雷滞涨12; 龙头对照=8月板内涨幅Top2×7板块; 判定=补涨篮20日累计>0且≥龙头篮',
               'ts': __import__('time').strftime('%Y-%m-%d %H:%M')}
        json.dump(reg, open(LEDGER, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f"登记: 补涨篮{len(laggard)}只 {laggard}")
        print(f"      龙头对照{len(reg['leader_ctrl'])}只 {reg['leader_ctrl']}")
        print(f"落盘 {LEDGER}")
    elif '--eval' in sys.argv:
        reg = json.load(open(LEDGER, encoding='utf-8'))
        d0 = reg['date']
        con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
        dates = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date", [d0])]
        if len(dates) < 21:
            print(f"未到期({len(dates)-1}/20交易日)"); return
        d1 = dates[20]
        b = reg['baselines']
        def ret(codes):
            vals = []
            for c_ in codes:
                cur = con.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                                  [c_, d1]).fetchone()
                if cur and c_ in b:
                    vals.append(float(cur[0])/b[c_]-1)
            return float(np.mean(vals))*100 if vals else None
        lag = ret(reg['laggard12']); leo = ret(reg['leader_ctrl'])
        pool = con.execute("SELECT AVG(a.close/b.close-1)*100 FROM stock_daily_meta_qfq a "
                           "JOIN stock_daily_meta_qfq b ON a.code=b.code AND b.date=? "
                           "WHERE a.date=? AND b.close<=20", [d0, d1]).fetchone()[0]
        con.close()
        verdict = '成立' if (lag and lag > 0 and lag >= (leo or 0)) else '不成立'
        reg['evaluated'] = True
        reg['result'] = {'eval_date': d1, '补涨篮%': round(lag, 2), '龙头篮%': round(leo, 2) if leo else None,
                         '全池%': round(float(pool), 2), '判定': verdict}
        json.dump(reg, open(LEDGER, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(f"补涨篮{lag:+.2f}% vs 龙头篮{leo:+.2f}% vs 全池{float(pool):+.2f}% → 轮动规律9月窗口{verdict}")
    else:
        reg = json.load(open(LEDGER, encoding='utf-8')) if os.path.exists(LEDGER) else {}
        print(json.dumps(reg, ensure_ascii=False, indent=1, default=str)[:500])

if __name__ == '__main__':
    main()
