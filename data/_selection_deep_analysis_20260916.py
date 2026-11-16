# -*- coding: utf-8 -*-
"""_selection_deep_analysis_20260916.py — 8-9月选股胜率全量深度归因（只读一次性）。
范围=8/1-9/16 全部 BUY(241笔/204票)跟踪至 9/16(已卖=实现盈亏,持有=浮动)。
分组: 追高度(个股pos250分位/换手截面分位/市值截面分位)×周×市场regime×来源。
产出=reports/SELECTION_OPTIMIZATION_PLAN_20260916.md 的数据层 json。"""
import sys, json, sqlite3, struct, datetime
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ATT = r'D:/zcodesj/attribution/attribution.db'
QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FD = r'D:/workbuddyspace/databases/fundamental_data.db'
VIPDOC_SH = r'D:/anzhuang/tdx/vipdoc/sh/lday/sh000001.day'
OUT = r'D:/zcodesj/attribution/selection_deep_analysis_20260916.json'
HELD = {'601328':2600,'002379':500,'003006':2100,'600719':1500,'601919':1500,'601975':5100,
'000983':1700,'600470':2400,'300817':2400,'603585':1800,'603230':2100,'002688':5900,
'600694':300,'600717':1100,'600035':1300,'600018':1800,'600020':1500,'002174':100,
'601999':500,'600704':1000,'600827':200,'002011':800,'300296':1400,'301370':1400,
'002481':1700,'002152':700,'002777':500,'002478':1000,'600987':1100,'600626':2100,
'001390':400,'603209':1600,'601022':2800,'000937':5300,'600348':2600,'601866':9900,
'002942':100,'002966':2100,'601077':2900,'603637':1400,'300214':2000,'300657':5600}
PX = {'601328':7.58,'002379':17.57,'003006':19.42,'600719':7.64,'601919':16.38,'601975':4.44,
'000983':6.60,'600470':5.72,'300817':16.06,'603585':10.40,'603230':11.14,'002688':5.13,
'600694':14.31,'600717':4.29,'600035':3.65,'600018':5.46,'600020':3.84,'002174':14.50,
'601999':6.90,'600704':4.91,'600827':7.67,'002011':13.66,'300296':4.88,'301370':8.33,
'002481':4.21,'002152':9.22,'002777':12.50,'002478':6.49,'600987':6.41,'600626':3.57,
'001390':16.49,'603209':14.54,'601022':8.15,'000937':4.45,'600348':9.18,'601866':2.45,
'002942':16.24,'002966':9.05,'601077':6.58,'603637':14.22,'300214':10.20,'300657':36.99}

def sh_pos250_map():
    dates, closes = [], []
    with open(VIPDOC_SH, 'rb') as f:
        data = f.read()
    rec = struct.Struct('<IIIIIfII')
    for i in range(0, len(data)-31, 32):
        d, o = rec.unpack_from(data, i)[0], rec.unpack_from(data, i)[1]
        if 20140101 <= d <= 20269999:
            dates.append(d); closes.append(o)
    s = pd.Series(closes)
    mn = s.rolling(250, min_periods=250).min(); mx = s.rolling(250, min_periods=250).max()
    return dict(zip(dates, ((s-mn)/(mx-mn)).round(3)))

def main():
    t0 = __import__('time').time()
    con = sqlite3.connect(ATT, timeout=10)
    buys = pd.read_sql("SELECT trade_date, code, name, price, volume FROM trade_records "
                       "WHERE trade_date>='20260801' AND action='BUY'", con)
    sells = pd.read_sql("SELECT trade_date, code, name, price, volume FROM trade_records "
                        "WHERE trade_date>='20260801' AND action='SELL'", con)
    con.close()
    for df in (buys, sells):
        df['code6'] = df['code'].astype(str).str[-6:]
        df['trade_date'] = df['trade_date'].astype(str)
    print(f'BUY={len(buys)} SELL={len(sells)} {__import__("time").time()-t0:.0f}s')

    # 逐票 FIFO 结算: 每笔买入 → 卖出匹配(同票按时间 FIFO), 未卖出部分→现价浮动
    codes = sorted(buys['code6'].unique())
    con = sqlite3.connect(QFQ, timeout=60)
    ph = ','.join('?'*len(codes))
    px = pd.read_sql(f"SELECT substr(code,1,6) code6, date, close FROM stock_daily_meta_qfq "
                     f"WHERE date>='20250401' AND substr(code,1,6) IN ({ph}) ORDER BY code6, date",
                     con, params=codes)
    con.close()
    px['date'] = px['date'].astype(str).str.replace('-','')
    gmap = {c: g.reset_index(drop=True) for c, g in px.groupby('code6')}

    # daily_basic 截面分位（换手/市值）
    con = sqlite3.connect(FD, timeout=60)
    db = pd.read_sql("SELECT ts_code, trade_date, turnover_rate, circ_mv FROM daily_basic WHERE trade_date>='20260801'", con)
    con.close()
    db['code6'] = db['ts_code'].str[:6]
    db['turn_pctl'] = db.groupby('trade_date')['turnover_rate'].rank(pct=True) * 100
    db['mv_pctl'] = db.groupby('trade_date')['circ_mv'].rank(pct=True) * 100
    dbmap = {(r.code6, str(r.trade_date)): (r.turn_pctl, r.mv_pctl) for r in db.itertuples(index=False)}
    pos_sh = sh_pos250_map()

    # 个股 pos250（买入日）
    def stock_pos(g, i):
        if i < 249: return None
        w = g['close'].iloc[i-249:i+1]
        mn, mx = w.min(), w.max()
        return round(float((g.loc[i,'close']-mn)/(mx-mn))*100, 1) if mx > mn else None

    lots = []   # (code6, date, qty, price, name)
    for r in buys.itertuples(index=False):
        lots.append([r.code6, r.trade_date, float(r.volume), float(r.price), r.name])
    lots.sort(key=lambda x: (x[0], x[1]))
    from collections import defaultdict
    sell_q = defaultdict(list)
    for r in sells.itertuples(index=False):
        sell_q[r.code6].append((r.trade_date, float(r.volume), float(r.price)))

    trades = []
    for code6 in sorted(set(l[0] for l in lots)):
        clots = [l for l in lots if l[0] == code6]
        sq = sorted(sell_q.get(code6, []))
        g = gmap.get(code6)
        for lot in clots:
            code6_, d, qty, price, name = lot
            remaining = qty
            realized = 0.0; realized_amt = 0.0
            # FIFO 消化卖出
            for si in range(len(sq)):
                sd, sqty, sp = sq[si]
                if sqty <= 0 or sd < d: continue
                take = min(remaining, sqty)
                if take > 0:
                    realized += take * (sp - price)
                    realized_amt += take * sp
                    sq[si] = (sd, sqty - take, sp)
                    remaining -= take
            held_qty = min(remaining, HELD.get(code6, 0))
            float_pnl = 0.0; cur = PX.get(code6)
            if held_qty > 0 and cur:
                float_pnl = held_qty * (cur - price)
            total_pnl = realized + float_pnl
            total_amt = qty * price
            # 追高度
            sPos = sTurn = sMv = None
            if g is not None:
                hit = g[g['date'] == d]
                if len(hit):
                    i = hit.index[0]
                    sPos = stock_pos(g, i)
            tp = dbmap.get((code6, d))
            if tp: sTurn, sMv = round(tp[0],1), round(tp[1],1)
            regime = pos_sh.get(int(d))
            trades.append({'code': code6, 'name': name, 'date': d, 'qty': qty, 'price': price,
                           'amt': round(total_amt, 0), 'pnl': round(total_pnl, 0),
                           'realized': round(realized, 0), 'float_pnl': round(float_pnl, 0),
                           'pos250': sPos, 'turn_pctl': sTurn, 'mv_pctl': sMv,
                           'mkt_pos': regime})
    df = pd.DataFrame(trades)
    df.to_json(OUT, orient='records', force_ascii=False, indent=1)
    print(f'trades={len(df)} 总金额={df["amt"].sum():,.0f} 总pnl={df["pnl"].sum():+,.0f} {__import__("time").time()-t0:.0f}s')

    def grp(sub, label):
        if len(sub) == 0: return
        win = (sub['pnl'] > 0).sum(); n = len(sub)
        print(f"{label}: n={n} 胜率{win/n*100:.1f}% 均值{sub['pnl'].mean():+,.0f} 合计{sub['pnl'].sum():+,.0f}")
        return {'n': n, 'win%': round(win/n*100,1), 'mean': round(float(sub['pnl'].mean()),0), 'sum': round(float(sub['pnl'].sum()),0)}
    R = {}
    R['all'] = grp(df, '全体')
    print('\n== 追高度: 个股pos250分位(买入日) ==')
    for lo, hi, lab in [(0,20,'<20'),(20,40,'20-40'),(40,60,'40-60'),(60,80,'60-80'),(80,101,'>=80')]:
        sub = df[df['pos250'].notna() & (df['pos250']>=lo) & (df['pos250']<hi)]
        R[f'pos_{lab}'] = grp(sub, f'pos250 {lab}')
    print('\n== 换手截面分位 ==')
    for lo, hi, lab in [(0,20,'<20'),(20,40,'20-40'),(40,60,'40-60'),(60,80,'60-80'),(80,101,'>=80')]:
        sub = df[df['turn_pctl'].notna() & (df['turn_pctl']>=lo) & (df['turn_pctl']<hi)]
        R[f'turn_{lab}'] = grp(sub, f'换手 {lab}')
    print('\n== 市值截面分位 ==')
    for lo, hi, lab in [(0,20,'<20'),(20,40,'20-40'),(40,60,'40-60'),(60,80,'60-80'),(80,101,'>=80')]:
        sub = df[df['mv_pctl'].notna() & (df['mv_pctl']>=lo) & (df['mv_pctl']<hi)]
        R[f'mv_{lab}'] = grp(sub, f'市值 {lab}')
    print('\n== 市场regime(上证pos250) ==')
    for rg in ['HIGH', 'LOW']:
        sub = df[df['mkt_pos'].notna()]
        sub = sub[(sub['mkt_pos']>=0.5) if rg=='HIGH' else (sub['mkt_pos']<0.5)]
        R[f'mkt_{rg}'] = grp(sub, f'市场{rg}')
    print('\n== 周 ==')
    df['wk'] = df['date'].str[:4] + 'W' + pd.to_datetime(df['date']).dt.isocalendar().week.astype(str)
    for w, sub in df.groupby('wk'):
        R[f'wk_{w}'] = grp(sub, str(w))
    json.dump(R, open(OUT.replace('.json','_summary.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('saved', OUT)

if __name__ == '__main__':
    main()
