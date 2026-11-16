# -*- coding: utf-8 -*-
"""高位突破衰竭风险提示器——影子扫描（只读，零生产改动，禁自动交易）。
口径=#5 ENGINE-B-HIGHPOS-BREAKOUT 已实证的高位衰竭组合：pos250>=0.70 × 近5日
{①机械突破(c>前60日最高收盘+量≥1.2×20日均量) ②炸板近似(high触涨停价且收盘未封) ③跳空高开近似(open>昨收3%且收阳)}。
数据截止 qfq 库最新（20260910 收盘）。名称源=westock 持仓接口（2026-09-11 实拉）。"""
import sqlite3, json, time, sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
OUT_JSON = r'D:/zcodesj/attribution/risk_alert_shadow_result_20260911.json'

# (code6, name, 持仓备注语义) —— code/name 来自 westock 持仓接口 20260911
HOLDINGS = [
    ('002637', '赞宇科技', ''), ('002379', '宏桥控股', ''), ('003006', '百亚股份', ''),
    ('600719', '大连热电', ''), ('601919', '中远海控', ''), ('601975', '招商南油', ''),
    ('000983', '山西焦煤', ''), ('600470', '六国化工', ''), ('300817', '双飞集团', ''),
    ('603585', '苏利股份', ''), ('603230', '内蒙新华', ''), ('002688', '金河生物', ''),
    ('603819', '神力股份', ''), ('300025', '华星创业', ''), ('600694', '大商股份', ''),
    ('600717', '天津港', ''), ('600035', '楚天高速', ''), ('600018', '上港集团', ''),
    ('600020', '中原高速', ''), ('002174', '游族网络', ''), ('601999', '出版传媒', ''),
    ('600704', '物产中大', ''), ('600827', '百联股份', ''), ('002011', '盾安环境', ''),
    ('300296', '利亚德', '抄底仓(9/7主人裁决)'), ('301370', '国科恒泰', '抄底仓(9/7主人裁决)'),
    ('002481', '双塔食品', ''), ('002152', '广电运通', ''), ('002777', '久远银海', ''),
    ('002478', '常宝股份', ''), ('600987', '航民股份', ''), ('600626', '申达股份', ''),
    ('001390', '古麒绒材', '抄底仓(9/10主人归类)'), ('600737', '中粮糖业', ''),
    ('601118', '海南橡胶', ''), ('000911', '*ST广糖', ''), ('603209', '兴通股份', ''),
    ('601022', '宁波远洋', ''), ('000937', '冀中能源', ''), ('600348', '华阳股份', ''),
]

def main():
    t0 = time.time()
    codes = [c for c, _, _ in HOLDINGS]
    con = sqlite3.connect(DB)
    cols = [r[1] for r in con.execute('PRAGMA table_info(stock_daily_meta_qfq)')]
    has_high = 'high' in cols
    mx = con.execute('SELECT MAX(date) FROM stock_daily_meta_qfq').fetchone()[0]
    ph = ','.join('?' * len(codes))
    df = pd.read_sql(
        f"SELECT code,date,open,high,close,volume FROM stock_daily_meta_qfq "
        f"WHERE date>='20150101' AND substr(code,1,6) IN ({ph}) ORDER BY code,date",
        con, params=codes)
    con.close()
    print(f'asof={mx} rows={len(df)} has_high={has_high} {time.time()-t0:.0f}s', flush=True)
    df['code6'] = df['code'].str.extract(r'(\d{6})')[0]
    df['date'] = df['date'].astype(np.int64)
    asof = int(mx)

    out = {'asof': asof, 'has_high_col': has_high, 'rows': []}
    for code6, name, tag in HOLDINGS:
        g = df[df['code6'] == code6].reset_index(drop=True)
        if len(g) < 261:
            out['rows'].append({'code': code6, 'name': name, 'note': '数据不足'})
            continue
        c = g['close'].to_numpy(); o = g['open'].to_numpy(); v = g['volume'].to_numpy()
        h = g['high'].to_numpy() if has_high else None
        d = g['date'].to_numpy()
        s = pd.Series(c)
        mn250 = s.rolling(250, min_periods=250).min().to_numpy()
        mx250 = s.rolling(250, min_periods=250).max().to_numpy()
        prev = s.shift(1)
        box_top = prev.rolling(60, min_periods=60).max().to_numpy()
        vol_ma20 = pd.Series(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
        i = len(g) - 1   # asof 日
        pos = (c[i] - mn250[i]) / (mx250[i] - mn250[i]) if mx250[i] > mn250[i] else np.nan
        evs = []
        last_hit_i = None
        for k in range(max(1, i - 4), i + 1):
            if d[k] > asof:
                continue
            b1 = bool(c[k] > box_top[k] and v[k] >= 1.2 * vol_ma20[k])
            b2 = False
            if has_high and np.isfinite(prev.iloc[k]) and prev.iloc[k] > 0:
                lim = round(prev.iloc[k] * (1.2 if code6[:3] == '300' else 1.1), 2)
                b2 = bool(h[k] >= lim and c[k] < lim)
            b3 = bool(prev.iloc[k] > 0 and o[k] > prev.iloc[k] * 1.03 and c[k] > o[k])
            hits = [t for t, b in (('突破', b1), ('炸板近似', b2), ('跳空近似', b3)) if b]
            if hits:
                evs.append({'date': int(d[k]), 'types': hits})
                last_hit_i = k
        fwd = None
        if last_hit_i is not None:
            j = last_hit_i + 1
            k20 = last_hit_i + 21
            if k20 < len(g) and j < len(g):
                fwd = round(float(c[k20] / o[j] - 1.0) * 100, 2)
            else:
                fwd = 'N/A未满'
        band = 'high' if pos >= 0.70 else ('mid' if pos >= 0.50 else 'low')
        level = ('RISK_ALERT' if (band == 'high' and evs) else
                 'WATCH' if (band == 'mid' and evs) else 'NONE')
        out['rows'].append({'code': code6, 'name': name, 'tag': tag,
                            'close': float(c[i]), 'pos250': round(float(pos), 3),
                            'band': band, 'events_5d': evs,
                            'last_hit_fwd20_pct': fwd, 'level': level})
    json.dump(out, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    alerts = [r for r in out['rows'] if r.get('level') == 'RISK_ALERT']
    watch = [r for r in out['rows'] if r.get('level') == 'WATCH']
    print(f'== 影子扫描完成 asof={asof} | RISK_ALERT {len(alerts)} | WATCH {len(watch)} ==')
    for r_ in alerts:
        print('RISK_ALERT', r_['code'], r_['name'], r_['tag'], 'pos250=', r_['pos250'],
              '事件=', json.dumps(r_['events_5d'], ensure_ascii=False), '上次命中fwd20=', r_['last_hit_fwd20_pct'])
    for r_ in watch:
        print('WATCH    ', r_['code'], r_['name'], r_['tag'], 'pos250=', r_['pos250'],
              '事件=', json.dumps(r_['events_5d'], ensure_ascii=False), '上次命中fwd20=', r_['last_hit_fwd20_pct'])
    print('saved', OUT_JSON, f'total {time.time()-t0:.0f}s')

if __name__ == '__main__':
    sys.exit(main())
