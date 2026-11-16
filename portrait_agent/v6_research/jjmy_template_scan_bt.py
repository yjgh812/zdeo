# -*- coding: utf-8 -*-
"""金健米业模板→全市场扫描+14年回测(2026-09-06 主人: 采集结果用于选股回测实战).

模板(金健7/31主升前快照实证, 去掉龙头前提=主升后确认):
  T1 地量: amt20 < 0.15 * amt60(金健=0.632/6.011=0.105→用0.15宽松)
  T2 低位: pos250 < 30%(金健19%)
  T3 深跌企稳: ret60 ∈ [-25%, -5%](金健-17.2%, 深跌但未破位)
  T4 贴近均线: ma20乖离|值| < 10%(金健+0.6%)
  T5 价≤20元(池约束)
回测: 14年每个截面满足T1-T5的股票等权组合→20日持有→超额/胜率/t
扫描: 当前截面(20260904)满足模板的股票
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

def main():
    m = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v5_full.pkl'))
    m['lim_today'] = m['lim_today'].fillna(False).astype(bool)
    # panel无amt20原始列(只有amt5_amt20)→从qfq补算amt20/amt60
    import sqlite3 as _sq
    _qfdb = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
    _con = _sq.connect(f'file:{_qfdb}?mode=ro', uri=True)
    _amt = pd.read_sql_query("SELECT code,date,amount FROM stock_daily_meta_qfq WHERE date>='20240101'", _con)
    _con.close()
    _amt = _amt.sort_values(['code', 'date']).reset_index(drop=True)
    _a20 = _amt.groupby('code')['amount'].transform(lambda s: s.rolling(20, min_periods=10).mean())
    _a60 = _amt.groupby('code')['amount'].transform(lambda s: s.rolling(60, min_periods=30).mean())
    _feat = pd.DataFrame({'code': _amt['code'], 'date': _amt['date'],
                          'amt20_raw': _a20, 'amt60_raw': _a60})
    m = m.merge(_feat, on=['code', 'date'], how='left')
    m['ret60_raw'] = m['ret60']
    m['ma20_raw'] = m.groupby('code')['close'].transform(lambda s: s.rolling(20).mean())
    m['bias20_raw'] = m['close']/m['ma20_raw'] - 1

    # ===== 当前截面扫描 =====
    cs = m[m['date'] == m['date'].max()].copy()
    cond = (
        (cs['amt20_raw'] < 0.15 * cs['amt60_raw']) &  # T1 地量
        (cs['pos250'] < 0.30) &                        # T2 低位
        (cs['ret60_raw'] >= -0.25) & (cs['ret60_raw'] <= -0.05) &  # T3
        (cs['bias20_raw'].abs() < 0.10) &              # T4
        (cs['close'] <= 20) &                          # T5
        (~cs['lim_today'])                              # 剔涨停不可买
    )
    hits = cs[cond]
    import sqlite3
    con = sqlite3.connect(r'file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro', uri=True)
    nm = dict(con.execute("SELECT code,name FROM industry_map_ths").fetchall())
    con.close()
    print(f'===== D2 全市场扫描({m["date"].max()}) =====')
    print(f'满足模板条件: {len(hits)}只')
    for _, r in hits.iterrows():
        ratio = r['amt20_raw']/r['amt60_raw'] if r['amt60_raw'] > 0 else 0
        print(f"  {r['code']} {nm.get(r['code'],'?')}: pos250={r['pos250']*100:.0f}% ret60={r['ret60_raw']*100:.1f}% "
              f"bias20={r['bias20_raw']*100:+.1f}% 量比={ratio:.2f} 行业={r['L1']} 收盘{r['close']:.2f}")

    # ===== 14年回测 =====
    print(f'\n===== D3 模板模式14年回测 =====')
    pool = m.dropna(subset=['fwd20_close'])
    day_mean = pool.groupby('date')['fwd20_close'].mean()
    all_dates = sorted(m['date'].unique())
    nav_p = nav_b = 1.0; rows = []
    for ws in all_dates[::20]:
        cs2 = m[m['date'] == ws]
        cond2 = (
            (cs2['amt20_raw'] < 0.15 * cs2['amt60_raw']) &
            (cs2['pos250'] < 0.30) &
            (cs2['ret60_raw'] >= -0.25) & (cs2['ret60_raw'] <= -0.05) &
            (cs2['bias20_raw'].abs() < 0.10) &
            (cs2['close'] <= 20) & (~cs2['lim_today'])
        )
        sel = cs2[cond2 & cs2['fwd20_close'].notna()]
        if len(sel) < 5: continue
        fv = sel['fwd20_close'].astype(float).to_numpy()
        pr = float(fv[:60].mean()); br = float(cs2[cs2['fwd20_close'].notna()]['fwd20_close'].mean())
        nav_p *= (1+pr); nav_b *= (1+br)
        rows.append({'date': ws, 'n': len(sel), 'port': round(pr*100, 3),
                     'base': round(br*100, 3), 'ex': round((pr-br)*100, 3),
                     'win': int(pr > br)})
    rdf = pd.DataFrame(rows)
    ex = rdf['ex'].values/100
    t = float(ex.mean()/(ex.std()/np.sqrt(len(ex)))) if ex.std() > 0 else np.nan
    yrs = len(rdf)*20/243
    ann_p = (nav_p**(1/yrs)-1)*100 if yrs > 0 else 0
    ann_b = (nav_b**(1/yrs)-1)*100 if yrs > 0 else 0
    mdd = float((rdf['port'].cummax()/rdf['port']-1).min())*100 if False else None
    yr = rdf.copy(); yr['year'] = yr['date'].str[:4]
    yearly = yr.groupby('year')['ex'].mean().round(3).to_dict()
    neg = sum(1 for v in yearly.values() if v < 0)
    print(f'窗口数: {len(rdf)} | 组合净值={nav_p:.4f} vs 基准{nav_b:.4f}')
    print(f'年化: {ann_p:.2f}% vs 基准{ann_b:.2f}% | t={t:.2f} | 胜率={rdf["win"].mean()*100:.1f}%')
    print(f'平均超额/窗口: {ex.mean()*100:.3f}% | 年负{neg}/{len(yearly)}')
    print(f'分年: {yearly}')

    # 金健米业对照
    jj = m[(m['code']=='600127') & (m['date']=='20260731')]
    if len(jj):
        r = jj.iloc[0]
        ratio = r['amt20_raw']/r['amt60_raw'] if r['amt60_raw'] > 0 else 0
        print(f'\n金健米业7/31对照: pos250={r["pos250"]*100:.0f}% ret60={r["ret60_raw"]*100:.1f}% '
              f'bias20={r["bias20_raw"]*100:+.1f}% 量比={ratio:.3f} → {"入模板" if ratio < 0.15 else "未入"}')

    json.dump({
        'scan_date': m['date'].max(), 'scan_hits': len(hits),
        'scan_codes': [{'code': r['code'], 'name': nm.get(r['code'],'?'),
                        'pos250%': round(r['pos250']*100), 'ret60%': round(r['ret60_raw']*100,1),
                        '行业': r['L1'], '收盘': round(r['close'],2)} for _, r in hits.iterrows()],
        'backtest': {'n_windows': len(rdf), 'nav': round(nav_p, 4), 'ann': round(ann_p, 2),
                     'ann_b': round(ann_b, 2), 't': round(t, 2), 'win': round(rdf['win'].mean()*100, 1),
                     'yearly': yearly, 'neg_years': neg}
    }, open(os.path.join(HERE, 'reports', 'jjmy_template_backtest_20260906.json'), 'w', encoding='utf-8'),
       ensure_ascii=False, indent=1, default=float)
    print('\n落盘 reports/jjmy_template_backtest_20260906.json')

if __name__ == '__main__':
    main()
