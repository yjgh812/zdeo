# -*- coding: utf-8 -*-
"""sold_flyback_study.py — 全历史卖单飞回研究 (2026-09-28, 主人令「检测所有卖飞的股票…提升胜率」).

口径:
  样本=attribution.db trade_records 全部 SELL/PARTIAL_SELL(日期双格式归一YYYYMMDD);
  参考价=qfq 卖出日收盘(无该日bar取下一bar, 避免实际价与qfq复权刻度混比的复权坑);
  前向窗口=T+5/10/20/60 交易日内的 min(low)/max(high)/close;
  分类(按T+20收益): 卖飞>+1% / 卖对<-1% / 平(敏感性±3%另报);
  底距=(参考价-窗口min_low)/参考价; 飞高=(窗口max_high-参考价)/参考价。
只读两库+stdout/JSON, 零写入生产表。今日卖出(无前向窗)单列不计入分布。
用法: python sold_flyback_study.py [--json OUT]
"""
import os, sys, json, sqlite3, argparse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

ATTR_URI = r'file:D:\zcodesj\attribution\attribution.db?mode=ro'
QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
HORIZONS = (5, 10, 20, 60)


def norm_date(s):
    s = str(s).replace('-', '').strip()
    return s[:8] if len(s) >= 8 else ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=None)
    a = ap.parse_args()

    con = sqlite3.connect(ATTR_URI, uri=True)
    sells = pd.read_sql_query(
        "SELECT code,name,trade_date,action,price,subsystems FROM trade_records "
        "WHERE action IN ('SELL','PARTIAL_SELL')", con)
    con.close()
    sells['code'] = sells['code'].astype(str).str.replace(r'^(sh|sz|bj)', '', regex=True).str.zfill(6)
    sells['d'] = sells['trade_date'].map(norm_date)
    sells = sells.dropna(subset=['price'])
    sells = sells[(sells['d'] >= '20260101') & (sells['price'] > 0)]
    # 同码同日去重(保留首条)
    sells = sells.drop_duplicates(subset=['code', 'd'], keep='first')
    print(f'[study] 卖单样本(去重后) {len(sells)} 笔, 涉及 {sells["code"].nunique()} 只')

    qc = sqlite3.connect(QFQ_URI, uri=True, timeout=60)
    all_dates = [r[0] for r in qc.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
    idx = {d: i for i, d in enumerate(all_dates)}

    out = []
    for _, s in sells.iterrows():
        code, d, price = s['code'], s['d'], float(s['price'])
        i0 = idx.get(d)
        if i0 is None:
            # 卖出日无qfq bar(停牌)→取下一bar收盘为参考
            nxt = [x for x in all_dates if x > d][:1]
            if not nxt:
                continue
            i0 = idx[nxt[0]] - 1  # 使T+1从nxt开始
        tds = all_dates[i0 + 1:i0 + 1 + max(HORIZONS)]
        if not tds:
            out.append({'code': code, 'name': s['name'], 'd': d, 'ref': None, 'channel': s['subsystems'],
                        'note': 'no_forward_bars(今日卖出)'})
            continue
        ph = ','.join('?' * len(tds))
        px = pd.read_sql_query(
            f"SELECT date,close,low,high,volume FROM stock_daily_meta_qfq WHERE code=? AND date IN ({ph}) ORDER BY date",
            qc, params=[code] + tds)
        if px.empty:
            continue
        ref_row = pd.read_sql_query(
            "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
            qc, params=[code, all_dates[i0]] if i0 >= 0 and all_dates[i0] >= d else [code, tds[0]])
        ref = float(ref_row['close'].iloc[0]) if not ref_row.empty and ref_row['close'].iloc[0] else float(px['close'].iloc[0])
        rec = {'code': code, 'name': s['name'], 'd': d, 'ref': round(ref, 3),
               'channel': s['subsystems'], 'n_bars': len(px)}
        closes = px['close'].astype(float).reset_index(drop=True)
        lows = px['low'].astype(float).reset_index(drop=True)
        highs = px['high'].astype(float).reset_index(drop=True)
        rec['bottom_dist_pct'] = round((ref - float(lows.min())) / ref * 100, 2)
        for h in HORIZONS:
            seg_c = closes[:h]
            seg_h = highs[:h]
            if len(seg_c) == 0:
                continue
            full = len(seg_c) == min(h, 60) if h != 60 else len(seg_c) == 60
            rec[f'fwd{h}_ret'] = round(float(seg_c.iloc[-1]) / ref - 1, 4)
            rec[f'fwd{h}_maxgain'] = round(float(seg_h.max()) / ref - 1, 4)
            rec[f'fwd{h}_full'] = bool(full and h <= 20)
        out.append(rec)
    qc.close()

    df = pd.DataFrame([o for o in out if o.get('ref')])
    today_rows = [o for o in out if not o.get('ref')]
    print(f'[study] 有前向窗 {len(df)} 笔 + 今日卖出 {len(today_rows)} 笔(不计分布)')

    res = {'n_total': len(sells), 'n_eval': len(df), 'n_today': len(today_rows)}
    # T+20 分类主口径
    d20 = df.dropna(subset=['fwd20_ret'])
    fly = d20[d20['fwd20_ret'] > 0.01]
    right = d20[d20['fwd20_ret'] < -0.01]
    flat = d20[(d20['fwd20_ret'] >= -0.01) & (d20['fwd20_ret'] <= 0.01)]
    res['t20'] = {'n': len(d20), 'fly': len(fly), 'right': len(right), 'flat': len(flat),
                  'fly_rate': round(len(fly) / len(d20) * 100, 1) if len(d20) else None,
                  'fly_median_gain': round(float(fly['fwd20_ret'].median()) * 100, 2) if len(fly) else None,
                  'right_median_avoid': round(float(right['fwd20_ret'].median()) * 100, 2) if len(right) else None,
                  'all_median_fwd20': round(float(d20['fwd20_ret'].median()) * 100, 2) if len(d20) else None,
                  'all_mean_fwd20': round(float(d20['fwd20_ret'].mean()) * 100, 2) if len(d20) else None}
    # 各持有期卖飞率(±1%)
    res['fly_rate_by_horizon'] = {}
    for h in HORIZONS:
        col = f'fwd{h}_ret'
        dd = df.dropna(subset=[col])
        if len(dd):
            res['fly_rate_by_horizon'][h] = {
                'n': len(dd),
                'fly_gt1pct': round(float((dd[col] > 0.01).mean()) * 100, 1),
                'fly_gt3pct': round(float((dd[col] > 0.03).mean()) * 100, 1)}
    # 底距分布(全样本)
    bd = df['bottom_dist_pct'].astype(float)
    res['bottom_dist'] = {'median': round(float(bd.median()), 2), 'p25': round(float(bd.quantile(0.25)), 2),
                          'p75': round(float(bd.quantile(0.75)), 2),
                          'le2pct': round(float((bd <= 2).mean()) * 100, 1),
                          'le5pct': round(float((bd <= 5).mean()) * 100, 1)}
    # 分通道 T+20
    ch = {}
    for c, g in d20.groupby(d20['channel'].fillna('(blank)')):
        ch[str(c)] = {'n': len(g),
                      'fly_rate': round(float((g['fwd20_ret'] > 0.01).mean()) * 100, 1),
                      'median_fwd20': round(float(g['fwd20_ret'].median()) * 100, 2)}
    res['by_channel_t20'] = dict(sorted(ch.items(), key=lambda kv: -kv[1]['n']))
    # 分月 T+20
    mo = {}
    for m, g in d20.groupby(d20['d'].str[:6]):
        mo[m] = {'n': len(g),
                 'fly_rate': round(float((g['fwd20_ret'] > 0.01).mean()) * 100, 1),
                 'median_fwd20': round(float(g['fwd20_ret'].median()) * 100, 2)}
    res['by_month_t20'] = dict(sorted(mo.items()))
    res['rows'] = df.to_dict('records')

    print('\n===== 全历史卖单飞回研究(2026-07-23起) =====')
    t20 = res['t20']
    print(f"T+20 口径(n={t20['n']}): 卖飞 {t20['fly_rate']}% | 卖对 {round(100-t20['fly_rate']-t20['flat']/t20['n']*100,1)}% | "
          f"卖飞组中位踏空 +{t20['fly_median_gain']}% | 卖对组中位避损 {t20['right_median_avoid']}%")
    print(f"全部卖单 T+20 中位 {t20['all_median_fwd20']}% / 均值 {t20['all_mean_fwd20']}% (负=卖出整体避损为正)")
    print(f"卖飞率按持有期: {json.dumps(res['fly_rate_by_horizon'], ensure_ascii=False)}")
    print(f"底距分布: 中位 {res['bottom_dist']['median']}% / P25 {res['bottom_dist']['p25']}% / P75 {res['bottom_dist']['p75']}% | "
          f"≤2% 占 {res['bottom_dist']['le2pct']}% ≤5% 占 {res['bottom_dist']['le5pct']}%")
    print(f"分通道: {json.dumps(res['by_channel_t20'], ensure_ascii=False)}")
    print(f"分月: {json.dumps(res['by_month_t20'], ensure_ascii=False)}")
    if a.json:
        json.dump(res, open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
        print(f'落盘 {a.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
