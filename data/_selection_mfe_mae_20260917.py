# -*- coding: utf-8 -*-
"""_selection_mfe_mae_20260917.py — 选股胜率第二轮深度诊断（只读）。
新维度: ①MFE/MAE(持有期最大浮盈/最大浮亏, 区分「选股错」vs「退出错」)
       ②fwd阶梯(T+1/3/5/10/20胜率期限结构) ③止损组与盈利组的路径对照 ④退出效率。
基础=selection_deep_analysis逐笔(241笔)。qfq含high/low。"""
import sys, json, time, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = r'D:/zcodesj/attribution/selection_deep_analysis_20260916.json'
QFQ = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
HELD = {'601328':2600,'002379':500,'003006':2100,'600719':1500,'601919':1500,'601975':5100,
'000983':1700,'600470':2400,'300817':2400,'603585':1800,'603230':2100,'002688':5900,
'600694':300,'600717':1100,'600035':1300,'600018':1800,'600020':1500,'002174':100,
'601999':500,'600704':1000,'600827':200,'002011':800,'300296':1400,'301370':1400,
'002481':1700,'002152':700,'002777':500,'002478':1000,'600987':1100,'600626':2100,
'001390':400,'603209':1600,'601022':2800,'000937':5300,'600348':2600,'601866':9900,
'002942':100,'002966':2100,'601077':2900,'603637':1400,'300214':2000,'300657':5600}
END = '20260916'

def main():
    t0 = time.time()
    trades = json.load(open(BASE, encoding='utf-8'))
    df = pd.DataFrame(trades)
    codes = sorted(df['code'].unique())
    con = sqlite3.connect(QFQ, timeout=60)
    ph = ','.join('?'*len(codes))
    px = pd.read_sql(f"SELECT substr(code,1,6) code6, date, open, high, low, close FROM stock_daily_meta_qfq "
                     f"WHERE date>='20260720' AND substr(code,1,6) IN ({ph}) ORDER BY code6, date",
                     con, params=codes)
    con.close()
    px['date'] = px['date'].astype(str).str.replace('-','')
    gmap = {c: g.reset_index(drop=True) for c, g in px.groupby('code6')}

    rows = []
    for r in df.itertuples(index=False):
        g = gmap.get(r.code)
        if g is None: continue
        hit = g[g['date'] == r.date]
        if not len(hit): continue
        i = hit.index[0]
        cost = float(r.price)
        seg = g.iloc[i:]
        if len(seg) < 2: continue
        held_qty = min(float(r.qty), HELD.get(r.code, 0))
        # 持有窗口终点: 若该票当前持股<买入量, 找卖出日(简化: 用成交量匹配不了则到END)
        hi_after = seg.iloc[1:]['high'].max() if len(seg) > 1 else np.nan
        lo_after = seg.iloc[1:]['low'].min() if len(seg) > 1 else np.nan
        mfe = (hi_after/cost - 1)*100 if np.isfinite(hi_after) else np.nan
        mae = (lo_after/cost - 1)*100 if np.isfinite(lo_after) else np.nan
        d1 = (seg.iloc[1]['close']/cost - 1)*100 if len(seg) > 1 else np.nan
        d5 = (seg.iloc[5]['close']/cost - 1)*100 if len(seg) > 5 else np.nan
        d10 = (seg.iloc[10]['close']/cost - 1)*100 if len(seg) > 10 else np.nan
        day0_chg = (cost/float(g.loc[i-1,'close']) - 1)*100 if i > 0 else np.nan
        rows.append({'code': r.code, 'name': r.name, 'date': r.date, 'amt': r.amt, 'pnl': r.pnl,
                     'mfe': round(mfe,2) if mfe==mfe else None, 'mae': round(mae,2) if mae==mae else None,
                     'd1': round(d1,2) if d1==d1 else None, 'd5': round(d5,2) if d5==d5 else None,
                     'd10': round(d10,2) if d10==d10 else None,
                     'buy_day_chg': round(day0_chg,2) if day0_chg==day0_chg else None,
                     'pos250': r.pos250, 'turn': r.turn_pctl})
    out = pd.DataFrame(rows)
    out.to_json(OUT := r'D:/zcodesj/attribution/selection_mfe_mae_20260917.json',
                orient='records', force_ascii=False, indent=1)
    print(f'rows={len(out)} {time.time()-t0:.0f}s')

    d = out.dropna(subset=['mfe','mae']).copy()
    d['pnl_pos'] = d['pnl'] > 0
    print(f'\n== 全体 MFE/MAE (n={len(d)}) ==')
    print(f"MFE中位={d['mfe'].median():.2f}%  MAE中位={d['mae'].median():.2f}%")
    print(f"MFE≥5%的比例: {(d['mfe']>=5).mean()*100:.1f}%   MFE≥3%: {(d['mfe']>=3).mean()*100:.1f}%")
    print(f"买入当日即跌(d1<0): {(d['d1']<0).mean()*100:.1f}%")
    # 分组: 亏损票的 MFE 形态
    lose = d[d['pnl'] <= 0]; win = d[d['pnl'] > 0]
    print(f'\n== 亏损票(n={len(lose)}) ==')
    print(f"MFE中位={lose['mfe'].median():.2f}%  MFE≥5%占比={(lose['mfe']>=5).mean()*100:.1f}%  MAE中位={lose['mae'].median():.2f}%")
    print(f"买入当日即跌(d1<0)占比: {(lose['d1']<0).mean()*100:.1f}%")
    print('\n== 盈利票(n=%d) ==' % len(win))
    print(f"MFE中位={win['mfe'].median():.2f}%  MAE中位={win['mae'].median():.2f}%")
    # MFE 五档 × 平均 pnl
    print('\n== MFE 分档 × 平均盈亏(元) ==')
    for lo, hi, lab in [(0,1,'<1'),(1,3,'1-3'),(3,5,'3-5'),(5,10,'5-10'),(10,999,'>=10')]:
        sub = d[(d['mfe']>=lo)&(d['mfe']<hi)]
        if len(sub): print(f"MFE {lab}%: n={len(sub)} 平均pnl={sub['pnl'].mean():+,.0f} 合计={sub['pnl'].sum():+,.0f}")
    # fwd 阶梯(全体)
    print('\n== fwd 阶梯(全体, 平均%) ==')
    for c in ('d1','d5','d10'):
        v = out[c].dropna()
        print(f"{c}: n={len(v)} 均值{v.mean():+.2f}% 胜率{(v>0).mean()*100:.1f}%")
    # 买入日追涨度 × 结果
    print('\n== 买入日当日涨幅(追涨度)分组 ==')
    for lo, hi, lab in [(-99,0,'当日跌/平'),(0,3,'当日涨0-3'),(3,6,'当日涨3-6'),(6,999,'当日涨≥6')]:
        sub = out[out['buy_day_chg'].notna() & (out['buy_day_chg']>=lo) & (out['buy_day_chg']<hi)]
        if len(sub): print(f"{lab}%: n={len(sub)} 胜率{(sub['pnl']>0).mean()*100:.1f}% 平均pnl={sub['pnl'].mean():+,.0f} 合计={sub['pnl'].sum():+,.0f}")
    json.dump({'mfe_median_all': float(d['mfe'].median()), 'mae_median_all': float(d['mae'].median())},
              open(OUT.replace('.json','_summary.json'),'w',encoding='utf-8'))
    print('saved', OUT)

if __name__ == '__main__':
    main()
