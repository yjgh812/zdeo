# -*- coding: utf-8 -*-
"""_portrait_fwd_analysis_20260915.py — 画像系统选股线：最近选股后涨跌结果深度分析（只读一次性）。

背景（§10-8 前提）：Y-REALIZABLE-LABEL-VARIANT（9/13）判死画像 v1 特征面"无条件 alpha"，
但档案明文记录环境依赖正能力（8月恢复段+1.5pp/创业板 rankIC+0.077）=regime 依赖。
9/10 前向回访（fwd5_review_20260910.md）因 automation 失败未落盘，本脚本=其增量版：
会话选股（manual_confirmed/mass_scan/full_market）fwd5 全量更新 + 三闸分组（赢家画像：
流通市值≤70亿/mom20≤+2%/换手≤3%）+ 市场位置 regime 轻量切片（上证 pos250）。
扩展口径：+mainline_radar/hotspot_breakout（感知层提名=追高负选择高风险区对照）。
零交易零接线零策略改动；产出=reports/PORTRAIT_FWD_DEEP_ANALYSIS_20260915.md 的数据层。
"""
import os, sys, json, sqlite3, struct, datetime
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ATT_DB = r'D:/zcodesj/attribution/attribution.db'
QFQ_DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FD_DB = r'D:/workbuddyspace/databases/fundamental_data.db'
VIPDOC_SH = r'D:/anzhuang/tdx/vipdoc/sh/lday/sh000001.day'
OUT_JSON = r'D:/zcodesj/attribution/portrait_fwd_analysis_20260915.json'
START = '20260828'
MAIN_SRC = ('manual_confirmed', 'mass_scan', 'full_market')
EXT_SRC = ('mainline_radar', 'hotspot_breakout')
CAP_MAX = 700000.0   # circ_mv 万元 → 70亿
MOM20_MAX = 0.02
TURN_MAX = 3.0

def sh_index_pos250():
    """上证日线(原始价) → (dates, closes)"""
    dates, closes = [], []
    with open(VIPDOC_SH, 'rb') as f:
        data = f.read()
    rec = struct.Struct('<IIIIIfII')
    for i in range(0, len(data) - 31, 32):
        d, o = rec.unpack_from(data, i)[0], rec.unpack_from(data, i)[1]
        if 20150101 <= d <= 20269999:
            dates.append(d); closes.append(o)
    return dates, closes

def main():
    # 1) 会话选股 PASS 记录（(日,码)去重取首条）
    con = sqlite3.connect(ATT_DB, timeout=10)
    recs = con.execute(
        """SELECT trade_date, code, name, source FROM guard_precheck_log
           WHERE trade_date>=? AND verdict LIKE 'PASS%'
           AND source IN (?,?,?,?,?) ORDER BY id""",
        (START,) + MAIN_SRC + EXT_SRC).fetchall()
    con.close()
    seen = {}
    for td, code, name, src in recs:
        key = (td, code)
        if key not in seen:
            seen[key] = {'trade_date': td, 'code': code, 'name': name, 'source': src}
    rows = list(seen.values())
    print(f'PASS记录(去重日,码)={len(rows)}')

    # 2) qfq 价格（涉及 codes）
    codes = sorted({r['code'] for r in rows})
    con = sqlite3.connect(QFQ_DB, timeout=30)
    ph = ','.join('?' * len(codes))
    px = pd.read_sql(
        f"SELECT substr(code,1,6) code6, date, close FROM stock_daily_meta_qfq "
        f"WHERE date>='20250101' AND substr(code,1,6) IN ({ph}) ORDER BY code6, date",
        con, params=codes)
    con.close()
    px['date'] = px['date'].astype(str).str.replace('-', '')
    gmap = {c: g.reset_index(drop=True) for c, g in px.groupby('code6')}

    # 3) daily_basic 三闸快照
    con = sqlite3.connect(FD_DB, timeout=30)
    db = pd.read_sql(
        "SELECT ts_code, trade_date, turnover_rate, circ_mv FROM daily_basic "
        "WHERE trade_date>=? ", con, params=(START,))
    con.close()
    db['code6'] = db['ts_code'].str[:6]
    dbmap = {(r.code6, str(r.trade_date)): (r.turnover_rate, r.circ_mv) for r in db.itertuples(index=False)}

    # 4) 上证 pos250 序列（市场 regime 代理）
    d_idx, c_idx = sh_index_pos250()
    s = pd.Series(c_idx)
    mn = s.rolling(250, min_periods=250).min(); mx = s.rolling(250, min_periods=250).max()
    pos_idx = dict(zip(d_idx, ((s - mn) / (mx - mn)).round(3)))

    # 5) 逐笔 fwd5 + 三闸
    out = []
    for r in rows:
        g = gmap.get(r['code'])
        rec = dict(r)
        td = r['trade_date']
        entry = None
        if g is not None:
            hit = g[g['date'] == td]
            if len(hit):
                i = hit.index[0]
                entry = float(g.loc[i, 'close'])
                j = i + 5
                if j < len(g):
                    rec['fwd5'] = float(g.loc[j, 'close']) / entry - 1.0
                    rec['fwd5_realized'] = True
                else:
                    rec['fwd5_realized'] = False
                # mom20
                if i >= 20:
                    rec['mom20'] = entry / float(g.loc[i - 20, 'close']) - 1.0
        tr_cap = dbmap.get((r['code'], td))
        rec['turnover'] = tr_cap[0] if tr_cap else None
        rec['circ_mv'] = tr_cap[1] if tr_cap else None
        rec['mkt_pos'] = pos_idx.get(int(td))
        # 三闸（缺失=不过，保守；披露缺失）
        g1 = rec.get('circ_mv') is not None and rec['circ_mv'] <= CAP_MAX
        g2 = rec.get('mom20') is not None and rec['mom20'] <= MOM20_MAX
        g3 = rec.get('turnover') is not None and rec['turnover'] <= TURN_MAX
        rec['gate_vvv'] = bool(g1 and g2 and g3)
        rec['gate_missing'] = int(not g1) + int(not g2) + int(not g3) - \
                              sum(1 for x in (rec.get('circ_mv'), rec.get('mom20'), rec.get('turnover')) if x is not None)
        out.append(rec)
    df = pd.DataFrame(out)
    df.to_json(OUT_JSON, orient='records', force_ascii=False, indent=1)

    def stat(sub, label):
        m = sub.dropna(subset=['fwd5'])
        if len(m) == 0:
            print(f'{label}: 无已实现样本')
            return None
        r = {'label': label, 'n_total': int(len(sub)), 'n_realized': int(len(m)),
             'pending': int(len(sub) - len(m)),
             'mean_pp': round(float(m['fwd5'].mean()) * 100, 3),
             'win': round(float((m['fwd5'] > 0).mean()) * 100, 1),
             'median_pp': round(float(m['fwd5'].median()) * 100, 3)}
        print(f"{label}: n={r['n_realized']}(待{r['pending']}) 均值{r['mean_pp']}pp 胜率{r['win']}% 中位{r['median_pp']}pp")
        return r

    res = {'generated': datetime.datetime.now().strftime('%F %H:%M'), 'start': START}
    df['regime'] = np.where(df['mkt_pos'].isna(), 'NA',
                   np.where(df['mkt_pos'] >= 0.5, 'HIGH>=0.5', 'LOW<0.5'))
    main_df = df[df['source'].isin(MAIN_SRC)]
    ext_df = df[df['source'].isin(EXT_SRC)]
    print('\n== 主口径(会话选股 manual/mass/full) ==')
    res['main_all'] = stat(main_df, '全部')
    res['main_vvv'] = stat(main_df[main_df['gate_vvv']], '三闸VVV组')
    res['main_nonvvv'] = stat(main_df[~main_df['gate_vvv']], '非VVV组')
    print('\n== 扩展口径(感知层提名 mainline/hotspot) ==')
    res['ext_all'] = stat(ext_df, '全部')
    res['ext_vvv'] = stat(ext_df[ext_df['gate_vvv']], '三闸VVV组')
    print('\n== regime 轻量切片(买入日上证pos250; 主口径) ==')
    df['regime'] = np.where(df['mkt_pos'].isna(), 'NA',
                   np.where(df['mkt_pos'] >= 0.5, 'HIGH>=0.5', 'LOW<0.5'))
    for rg in ['LOW<0.5', 'HIGH>=0.5']:
        res[f'main_regime_{rg}'] = stat(main_df[main_df['regime'] == rg], f'市场{rg}')
    print('\n== 按周分解(主口径) ==')
    main_df2 = main_df.copy()
    main_df2['week'] = main_df2['trade_date'].str[:6] + 'W' + (pd.to_datetime(main_df2['trade_date']).dt.isocalendar().week.astype(str))
    for w, sub in main_df2.groupby('week'):
        stat(sub, str(w))
    # 缺失披露
    nm = int(main_df['gate_missing'].gt(0).sum())
    print(f'\n三闸数据缺失笔数(主口径): {nm}/{len(main_df)}')
    res['main_gate_missing'] = nm
    json.dump(res, open(OUT_JSON.replace('.json', '_summary.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('saved', OUT_JSON)

if __name__ == '__main__':
    main()
