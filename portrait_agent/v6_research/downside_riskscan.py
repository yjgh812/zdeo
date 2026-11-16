# -*- coding: utf-8 -*-
"""持仓排雷扫描工具(2026-09-06 固化, prereg_downside_riskscore_obs_v1 的日常执行面).

用法:
  python downside_riskscan.py --holdings            # 从 data/positions_latest.json 读持仓快照
  python downside_riskscan.py --codes 600519,000001 # 任意代码列表
输出: 排雷分+触发明细(12特征), ≥3雷组标记(仅提示不拦截, prereg boundary_LOCKED)
依赖: cache/panel_v5_full.pkl(截面特征); 池外股票标注N/A
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
# 2026-09-25 迁入v6_research后, cache仍在父目录 portrait_agent/cache
CACHE = os.path.join(os.path.dirname(HERE), 'cache')
RISK12 = ['lhb_buy_5', 'limit_break_5', 'new_high_3', 'vwap60_bias', 'vwap20_bias',
          'gap_up_run_5', 'chan_buy3_10', 'new_high20', 'macd_golden_hi', 'rounded_20',
          'red_3_soldiers', 'higher_low']
POS_FILE = r'D:\zcodesj\data\positions_latest.json'

def get_names(codes):
    import sqlite3
    con = sqlite3.connect(r'file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro', uri=True)
    ph = ','.join('?'*len(codes))
    d = dict(con.execute(f'SELECT code,name FROM industry_map_ths WHERE code IN ({ph})', codes).fetchall())
    con.close()
    return d

def main():
    if '--track' in sys.argv:
        track(); return
    if '--holdings' in sys.argv:
        if not os.path.exists(POS_FILE):
            print(f'FAIL 持仓快照缺失 {POS_FILE}; 先跑持仓同步'); sys.exit(2)
        pos = json.load(open(POS_FILE, encoding='utf-8'))
        # positions_latest.json 结构兼容: [{code/name/...}] 或 {positions:[...]}
        items = pos.get('positions', pos) if isinstance(pos, dict) else pos
        codes = []
        names = {}
        for it in items:
            c = str(it.get('code', '')).replace('sh', '').replace('sz', '').replace('bj', '')
            if c:
                codes.append(c)
                names[c] = it.get('name', '')
    elif '--codes' in sys.argv:
        codes = [c.strip() for c in sys.argv[sys.argv.index('--codes')+1].split(',') if c.strip()]
        names = get_names(codes)
    else:
        print(__doc__); sys.exit(1)
    if not codes:
        print('FAIL 无有效代码'); sys.exit(2)

    m = pd.read_pickle(os.path.join(CACHE, 'panel_v5_full.pkl'))
    m['vwap60_bias_b'] = (m['vwap60_bias'] > 0.5).astype(float)
    m['vwap20_bias_b'] = (m['vwap20_bias'] > 0.5).astype(float)
    # 兼容列名(扫描用_b后缀)
    m['vwap60_bias'] = m['vwap60_bias_b']; m['vwap20_bias'] = m['vwap20_bias_b']
    last = m['date'].max()
    cs = m[m['date'] == last].set_index('code')

    # FRESHNESS: panel截面 vs qfq最新, 滞后>3交易日WARN(重建=跑retrain_v4_full.py+data_enrich_v5.py约15分钟)
    import sqlite3 as _sq, datetime as _dt
    try:
        _c = _sq.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True)
        _qmax = _c.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]; _c.close()
        _gap = (_dt.datetime.strptime(_qmax, '%Y%m%d') - _dt.datetime.strptime(str(last), '%Y%m%d')).days
        if _gap > 5:
            print(f'WARN panel截面{last}落后qfq{_qmax}约{_gap}天, 特征基于旧截面; 重建: python portrait_agent/v6_research/retrain_v4_full.py && python portrait_agent/data_enrich_v5.py')
    except Exception:
        pass
    rows = []
    for code in codes:
        nm = names.get(code, '')
        if not nm:
            nm = get_names([code]).get(code, code)
        if code not in cs.index:
            rows.append({'code': code, 'name': nm, 'risk_score': None,
                         'risk_grp': '池外', 'triggers': '价>20元或流动性池外'})
            continue
        r = cs.loc[code]
        if isinstance(r, pd.DataFrame): r = r.iloc[0]
        trg = [f for f in RISK12 if pd.notna(r.get(f)) and r[f] > 0.5]
        score = len(trg)
        grp = '3+雷' if score >= 3 else ('2雷' if score == 2 else ('1雷' if score == 1 else '0无雷'))
        rows.append({'code': code, 'name': nm, 'risk_score': score, 'risk_grp': grp,
                     'triggers': ','.join(trg) if trg else '-',
                     'close': round(float(r['close']), 3) if pd.notna(r['close']) else None})
    df = pd.DataFrame(rows).sort_values('risk_score', ascending=False, na_position='last')
    print(f'排雷扫描(截面{last}, 12特征, P跌15参考: 0雷5.1%/1雷6.1%/2雷8.8%/3+雷15.2%):')
    print(df.to_string(index=False))
    hi = df[df['risk_score'] >= 3]
    if len(hi):
        print(f'\n⚠️ ≥3雷 {len(hi)}只(20日暴跌概率组, 仅提示不拦截——prereg观察口径): '
              + '; '.join(f"{r['name']}({int(r['risk_score'])}雷)" for _, r in hi.iterrows()))
    out_fp = os.path.join(r'D:\zcodesj\data',
                          f'downside_riskscan_{last}.json')
    json.dump({'date': last, 'kind': 'riskscan_baseline',
               'rows': df.astype({'risk_score': 'float'}).to_dict('records')},
              open(out_fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    print(f'落盘 {out_fp}')

def track():
    """--track: 最近riskscan基线 T+20 复评(3+雷 vs 0雷对照, prereg observation执行面)."""
    import glob as _g
    bases = sorted(_g.glob(os.path.join(r'D:\zcodesj\data', 'downside_riskscan_*.json')))
    if not bases:
        print('FAIL 无基线登记'); sys.exit(2)
    for bp in bases:
        d = json.load(open(bp, encoding='utf-8'))
        if d.get('evaluated') or not d.get('rows'): continue
        t0 = str(d['date'])
        import sqlite3 as _sq
        con = _sq.connect(r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro', uri=True)
        dates = [r[0] for r in con.execute(
            'SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date', [t0])]
        if len(dates) < 21:
            print(f'基线{t0}: 未到期({len(dates)-1}/20交易日)'); con.close(); continue
        t1 = dates[20]
        results = []
        for r in d['rows']:
            if not r.get('close'): continue
            cur = con.execute(
                'SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?',
                [r['code'], t1]).fetchone()
            if cur:
                results.append({**r, 'close_t20': round(float(cur[0]), 3),
                                'ret20_pct': round((float(cur[0])/r['close']-1)*100, 2)})
        con.close()
        hi = [x for x in results if (x['risk_score'] or 0) >= 3]
        lo = [x for x in results if (x['risk_score'] or 0) == 0]
        def _s(g):
            if not g: return 'n=0'
            rets = [x['ret20_pct'] for x in g]
            dn = sum(1 for x in g if x['ret20_pct'] <= -15)
            return (f'n={len(g)} 均值{sum(rets)/len(rets):+.2f}% '
                    f'暴跌(<=-15%)率{dn/len(g)*100:.1f}%({dn}例)')
        print(f'排雷追踪复评 基线{t0}→T+20({t1}):')
        print(f'  3+雷组: {_s(hi)}')
        print(f'  0无雷组: {_s(lo)}')
        for x in hi:
            print(f'    {x["code"]} {x["name"]}: {x["ret20_pct"]:+.2f}% '
                  f'({"暴跌" if x["ret20_pct"] <= -15 else "-"}) 触发={x["triggers"]}')
        d['evaluated'] = True; d['track_t20'] = t1; d['track_results'] = results
        json.dump(d, open(bp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print(f'回写 {bp}')
        return
    print('无到期基线(全部未到期或已评估)')

if __name__ == '__main__':
    main()
