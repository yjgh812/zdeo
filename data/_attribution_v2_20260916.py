# -*- coding: utf-8 -*-
"""_attribution_v2_20260916.py — 8-9月选中股票逐票涨跌归因（只读一次性）。
基础=selection_deep_analysis_20260916.json(241笔逐笔pnl+追高度)；本脚本补:
行业(stock_info)/板块β(申万L1线 首买日→9/16)/个股α=票收益−板块β/买入后路径(1/3/5/10日)/
买入日追涨度(当日涨幅)/持有期最大单日跌幅。
产出: attribution/selection_attribution_v2_20260916.json + 控制台分布。"""
import sys, json, sqlite3, datetime
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = r'D:/zcodesj/attribution/selection_deep_analysis_20260916.json'
QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
SW = r'D:/workbuddyspace/databases/sw_industry.db'
DQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant.db'
OUT = r'D:/zcodesj/attribution/selection_attribution_v2_20260916.json'
END = '20260916'

def main():
    t0 = __import__('time').time()
    trades = json.load(open(BASE, encoding='utf-8'))
    df = pd.DataFrame(trades)
    # 票级聚合
    agg = df.groupby('code').agg(
        name=('name', 'first'), first_date=('date', 'min'), last_date=('date', 'max'),
        amt=('amt', 'sum'), pnl=('pnl', 'sum'), n_buys=('amt', 'count'),
        pos250=('pos250', 'first'), turn=('turn_pctl', 'first'), mv=('mv_pctl', 'first'),
        mkt=('mkt_pos', 'first')).reset_index()
    codes = agg['code'].tolist()
    print(f'票数={len(agg)} {__import__("time").time()-t0:.0f}s')

    # 行业(申万L1归一: 通达信111类→31类)
    sys.path.insert(0, r'D:/zcodesj/data')
    from _industry_map_20260916 import MAP
    con = sqlite3.connect(DQ, timeout=10)
    info = pd.read_sql("SELECT code, industry FROM stock_info", con)
    con.close()
    ind = dict(zip(info['code'], info['industry']))
    agg['industry'] = agg['code'].map(ind)
    agg['sw_ind'] = agg['industry'].map(lambda x: MAP.get(x) if x in MAP else x)

    # qfq 路径
    con = sqlite3.connect(QFQ, timeout=60)
    ph = ','.join('?'*len(codes))
    px = pd.read_sql(f"SELECT substr(code,1,6) code6, date, close FROM stock_daily_meta_qfq "
                     f"WHERE date>='20260601' AND substr(code,1,6) IN ({ph}) ORDER BY code6, date",
                     con, params=codes)
    con.close()
    px['date'] = px['date'].astype(str).str.replace('-','')
    gmap = {c: g.reset_index(drop=True) for c, g in px.groupby('code6')}

    # 申万行业线
    con = sqlite3.connect(SW, timeout=30)
    sw = pd.read_sql("SELECT name, trade_date, close FROM industry_kline_sw WHERE level='L1' AND trade_date>='2026-07-01'", con)
    con.close()
    sw['trade_date'] = sw['trade_date'].str.replace('-','')
    swmap = {n_: g.reset_index(drop=True) for n_, g in sw.groupby('name')}
    agg['sw_ind'] = agg['sw_ind'].where(agg['sw_ind'].isin(swmap.keys()), agg['industry'])

    rows = []
    for r in agg.itertuples(index=False):
        rec = {'code': r.code, 'name': r.name, 'industry': r.industry, 'first_date': r.first_date,
               'amt': round(r.amt), 'pnl': round(r.pnl), 'pos250': r.pos250, 'turn': r.turn}
        g = gmap.get(r.code)
        cost = None
        if g is not None:
            hit = g[g['date'] == r.first_date]
            if len(hit):
                i = hit.index[0]
                cost = float(g.loc[i, 'close'])
                cur_row = g[g['date'] == END]
                cur = float(cur_row['close'].iloc[0]) if len(cur_row) else None
                if cost and cur:
                    rec['ret_total'] = round((cur/cost - 1)*100, 2)
                # 路径 1/3/5/10
                for h in (1, 3, 5, 10):
                    j = i + h
                    if j < len(g):
                        rec[f'd{h}'] = round((float(g.loc[j,'close'])/cost - 1)*100, 2)
                # 持有期最大单日跌幅/买入日当日涨幅
                seg = g.iloc[i:min(i+30, len(g))]
                if len(seg) > 1:
                    chg = seg['close'].pct_change()*100
                    rec['max_1d_drop'] = round(float(chg.min()), 2)
                if i > 0:
                    rec['buy_day_chg'] = round(float((cost/float(g.loc[i-1,'close'])-1)*100), 2)
        # 板块β
        ind_name = r.sw_ind if hasattr(r, 'sw_ind') else r.industry
        swg = swmap.get(ind_name)
        if swg is not None and cost:
            h0 = swg[swg['trade_date'] == r.first_date]
            h1 = swg[swg['trade_date'] == END]
            if len(h0) and len(h1):
                b0 = float(h0['close'].iloc[0]); b1 = float(h1['close'].iloc[0])
                rec['sector_beta'] = round((b1/b0 - 1)*100, 2)
        if rec.get('ret_total') is not None and rec.get('sector_beta') is not None:
            rec['alpha'] = round(rec['ret_total'] - rec['sector_beta'], 2)
        rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_json(OUT, orient='records', force_ascii=False, indent=1)
    print(f'rows={len(out)} {__import__("time").time()-t0:.0f}s')

    # 归因分布
    losers = out[out['pnl'] < 0]
    winners = out[out['pnl'] > 0]
    def dist(sub, label):
        n = len(sub)
        if n == 0: return
        a_neg = int(((sub.get('alpha', np.nan) < -2)).sum())
        b_neg = int(((sub.get('sector_beta', np.nan) < -2)).sum())
        both = int(((sub.get('alpha', np.nan) < -2) & (sub.get('sector_beta', np.nan) < -2)).sum())
        print(f"{label}: n={n} | 个股α<-2pp: {a_neg} | 板块β<-2pp: {b_neg} | 双负: {both}")
        hi_pos = int((sub['pos250'] >= 60).sum()); lo_pos = int((sub['pos250'] < 20).sum())
        hi_turn = int((sub['turn'] >= 80).sum())
        print(f"   追高度: pos250≥60: {hi_pos} | pos250<20: {lo_pos} | 换手≥80: {hi_turn}")
        return {'n': n, 'alpha_neg': a_neg, 'beta_neg': b_neg, 'both_neg': both,
                'pos_ge60': hi_pos, 'pos_lt20': lo_pos, 'turn_ge80': hi_turn}
    print('\n== 跌票归因分布 ==')
    L = dist(losers, '亏损票')
    print('\n== 涨票归因分布 ==')
    W = dist(winners, '盈利票')
    # 极端案例
    print('\n== 亏损TOP8 ==')
    for r in losers.nsmallest(8, 'pnl').itertuples(index=False):
        print(f"  {r.code} {r.name}({r.industry}) pnl={r.pnl:+.0f} ret={getattr(r,'ret_total',None)}% 板块β={getattr(r,'sector_beta',None)}% α={getattr(r,'alpha',None)}% pos250={r.pos250} 换手={r.turn}")
    print('\n== 盈利TOP8 ==')
    for r in winners.nlargest(8, 'pnl').itertuples(index=False):
        print(f"  {r.code} {r.name}({r.industry}) pnl={r.pnl:+.0f} ret={getattr(r,'ret_total',None)}% 板块β={getattr(r,'sector_beta',None)}% α={getattr(r,'alpha',None)}% pos250={r.pos250} 换手={r.turn}")
    json.dump({'losers_dist': L, 'winners_dist': W},
              open(OUT.replace('.json', '_dist.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved', OUT)

if __name__ == '__main__':
    main()
