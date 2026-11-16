# -*- coding: utf-8 -*-
"""2026翻倍股画像扩展采集v2(2026-09-06 主人指令"继续完善").

在v1(五族+15形态)之上补齐金健二、三轮维度并新增主升启动截面:
  A K线组合  B 收益分布  C 逐月矩阵  D 日内微观  E 极端日档案  F 资金代理
  G 筹码成本乖离(vwap)  H MACD结构(金叉/死叉/零轴/顶背离)  I 缠论v2复盘(cache真源)
  J 相对强弱(对全市场等权)  K 主升启动截面(T-1月末快照+全市场分位=金健模板142案例版)
口径锚验收: 600127金健加入运行, 对照 jjmy_profile_report_20260906.json 已知值.
数据源: qfq weekly备份(主库重建期) + cache/chan_signals_v2.pkl(确认日口径真源).
"""
import os, sys, json, time, sqlite3, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument('--year', choices=['2026', '2025'], default='2026')
yargs = ap.parse_args()
YEAR = yargs.year

QFQ_DB = r'D:\zcodesj\backups\dragon_quant_qfq\weekly\dragon_quant_qfq_20260906.db'
V1_JSON = {'2026': r'D:\zcodesj\data\doubled_batch_features_20260906.json',
           '2025': r'D:\zcodesj\data\doubled_batch_features_2025.json'}[YEAR]
CHAN_PKL = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'chan_signals_v2.pkl')
OUT_FEATS = {'2026': r'D:\zcodesj\data\doubled_batch_features_v2_20260906.json',
             '2025': r'D:\zcodesj\data\doubled_batch_features_v2_2025.json'}[YEAR]
OUT_AGG = {'2026': r'D:\zcodesj\portrait_agent\reports\doubled_batch_profile_v2_20260906.json',
           '2025': r'D:\zcodesj\portrait_agent\reports\doubled_batch_profile_v2_2025.json'}[YEAR]
Y0, YEND = {'2026': ('20260101', '20260904'), '2025': ('20250101', '20251231')}[YEAR]
DATA_FROM = {'2026': '20241101', '2025': '20231001'}[YEAR]  # pos250需250日预热
ME = {'2026': ['20251231', '20260130', '20260227', '20260331', '20260430', '20260529', '20260630', '20260731', '20260829'],
      '2025': ['20241231', '20250127', '20250228', '20250331', '20250430', '20250530', '20250630', '20250731', '20250829',
               '20250930', '20251031', '20251128', '20251231']}[YEAR]
T0 = time.time()
def log(m): print(f'[{time.time()-T0:6.1f}s] {m}', flush=True)

ANCHOR = None if YEAR == '2025' else '600127'  # 金健米业口径锚(2026专属); 2025用上纬新材YTD轻锚

v1 = json.load(open(V1_JSON, encoding='utf-8'))['features']
universe = [(c, f['name'], f['kind'], f['主升月']) for c, f in v1.items()]
if ANCHOR:
    universe.append((ANCHOR, '金健米业', 'anchor', '202608'))
codes = [u[0] for u in universe]
log(f'清单 {len(universe)} 只' + ('(含锚600127)' if ANCHOR else '(2025版, 锚=上纬新材YTD轻锚)'))

con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>=? AND date<=?", con, params=[DATA_FROM, YEND])
con.close()
df = df.sort_values(['code', 'date']).reset_index(drop=True)
log(f'全市场rows={len(df)} codes={df.code.nunique()}')

# ---------- 全市场月末截面(pivot向量化, K族分位对照) ----------
# 🔴数据事故(20260906普查): amount列2026年1-6月60%行vwap偏离close>50%(5022只受污染),
#    量能族一律改用volume口径(已tdx交叉验证: 600127 8/18=54.8M股/8/20=278.6M股分毫不差)
cp = df.pivot(index='date', columns='code', values='close')
vp = df.pivot(index='date', columns='code', values='volume')
rng = pd.Series(cp.index)
pos250_m = (cp - cp.rolling(250, min_periods=60).min()) / (cp.rolling(250, min_periods=60).max() - cp.rolling(250, min_periods=60).min()) * 100
bias20_m = (cp / cp.rolling(20).mean() - 1) * 100
ret60_m = (cp / cp.shift(60) - 1) * 100
vr_m = vp.rolling(5).mean() / vp.rolling(20).mean()
dry_m = (vp < 0.5 * vp.rolling(20).mean())
dry_cnt = dry_m.groupby(rng.str[:6].values).transform('sum')
mkt_sec = {}
for me in ME:
    me_key = me if me in cp.index else [d for d in cp.index if d < me][-1]
    mkt_sec[me] = {'实际月末': me_key,
                   'pos250中位': round(float(pos250_m.loc[me_key].median()), 1),
                   'bias20中位%': round(float(bias20_m.loc[me_key].median()), 1),
                   'ret60中位%': round(float(ret60_m.loc[me_key].median()), 1),
                   '量比中位': round(float(vr_m.loc[me_key].median()), 2),
                   '月内地量中位': round(float(dry_cnt.loc[me_key].median()), 0)}
log(f'全市场月末截面 {len(mkt_sec)} 期就绪')

def sec_pctl(kind, me_key, val):
    s = {'pos250': pos250_m, 'bias20': bias20_m, 'ret60': ret60_m, 'vr': vr_m, 'dry': dry_cnt}[kind].loc[me_key].dropna()
    return round(float((s < val).mean() * 100), 1)

chan = pd.read_pickle(CHAN_PKL)
chan26 = chan[chan['date'] >= Y0]
log(f'缠论v2信号(2026) {len(chan26)} 条')

mkt_ret = df.groupby('date')['change_pct'].mean()

def limit_thr(code, name):
    p = code[:3]
    if p in ('300', '301', '302', '688', '689'): return 20.0
    if code[0] in ('8', '4') or code[:2] == '92': return 30.0
    return 5.0 if 'ST' in (name or '') else 10.0

def limit_mask(dates26, chg26, code, name):
    p = code[:3]
    if p in ('300', '301', '302', '688', '689') or code[0] in ('8', '4') or code[:2] == '92':
        return chg26 >= limit_thr(code, name) * 0.97
    if 'ST' not in (name or ''):
        return chg26 >= 9.7
    mons = pd.Series(dates26.values).str[:6]
    thr_eff = chg26.groupby(mons.values).transform('max') > 5.5
    return pd.Series(chg26.values >= np.where(thr_eff, 9.7, 4.85), index=chg26.index)

def med_of(pairs):
    vals = [v for v in pairs if v is not None and not (isinstance(v, float) and np.isnan(v))]
    return round(float(np.median(vals)), 2) if vals else None

out, anchor = {}, {}
for code, name, kind, best_mon in universe:
    g = df[df['code'] == code].reset_index(drop=True)
    g26 = g[g['date'] >= Y0].reset_index(drop=True)
    c = g['close'].astype(float); o = g['open'].astype(float)
    hi = g['high'].astype(float); lo = g['low'].astype(float)
    amt = g['amount'].astype(float); chg = g['change_pct'].astype(float)
    vol = g['volume'].astype(float)
    c26 = g26['close'].astype(float); o26 = g26['open'].astype(float)
    hi26 = g26['high'].astype(float); lo26 = g26['low'].astype(float)
    vol26 = g26['volume'].astype(float); chg26 = g26['change_pct'].astype(float)
    d26 = g26['date']
    lim = limit_mask(d26, chg26, code, name)

    # A K线组合
    body = c26 - o26
    red3 = int(((body > 0) & (body.shift(1) > 0) & (body.shift(2) > 0)).sum())
    engulf = int(((body > 0) & (body.shift(1) < 0) & (c26 >= o26.shift(1)) & (o26 <= c26.shift(1))).sum())
    # B 收益分布
    r = chg26.dropna()
    up, dn = r[r > 0], r[r < 0]
    plr = float(up.mean() / abs(dn.mean())) if len(up) and len(dn) and dn.mean() != 0 else None
    # C 逐月矩阵(首月基准=上年末收盘; 2026年内上市=上市首日收盘)
    months = sorted(set(d26.str[:6]))
    prev_close = c[g['date'] < Y0].iloc[-1] if (g['date'] < Y0).any() else c26.iloc[0]
    mat, mon_end = [], {}
    for mo in months:
        msk = (d26.str[:6] == mo).values
        end = float(c26[msk].iloc[-1])
        ret = round((end / prev_close - 1) * 100, 1)
        prev_close = end
        mon_end[mo] = end
        mat.append({'月': mo[4:], '收益%': ret,
                    '振幅%': round(float(hi26[msk].max() / lo26[msk].min() - 1) * 100, 1),
                    '日均量M股': round(float(vol26[msk].mean() / 1e6), 1),
                    '涨停': int(lim.values[msk].sum()),
                    '波动%': round(float(chg26[msk].std()), 2)})
    mi = months.index(best_mon[:6]) if best_mon[:6] in months else None
    surge = round(mat[mi]['日均量M股'] / mat[mi - 1]['日均量M股'], 1) if mi is not None and mi > 0 and mat[mi - 1]['日均量M股'] > 0 else None
    # D 日内微观
    gapdir = o26.values - c26.shift(1).values
    clo_pos = ((c26 - lo26) / (hi26 - lo26) * 100).replace([np.inf, -np.inf], np.nan)
    # E 极端日
    ext = [{'date': d, 'chg%': round(float(cc), 2), '量M股': round(float(v) / 1e6, 1)}
           for d, cc, v in zip(d26, chg26, vol26) if abs(cc) > 5]
    # F 资金代理(volume口径)
    vp_up = int(((chg26 > 0) & (vol26 > vol26.shift(1))).sum())
    vp_dn = int(((chg26 < 0) & (vol26 > vol26.shift(1))).sum())
    # G 筹码乖离(close滚动均价口径; vwap口径因amount脏数据废弃)
    bias_chip = {}
    for n in (20, 60, 120):
        ma = c.rolling(n).mean()
        if not np.isnan(ma.iloc[-1]) and ma.iloc[-1] > 0:
            bias_chip[f'{n}日'] = round(float((c.iloc[-1] / ma.iloc[-1] - 1) * 100), 1)
    # H MACD结构
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    m26 = (g['date'] >= Y0).values
    def dlist(mask):
        return g.loc[mask & m26, 'date'].str[4:].tolist()
    gc_d = dlist((dif > dea) & (dif.shift(1) <= dea.shift(1)))
    dc_d = dlist((dif < dea) & (dif.shift(1) >= dea.shift(1)))
    zc_d = dlist((dif > 0) & (dif.shift(1) <= 0))
    hi40 = hi.shift(1).rolling(40).max(); difhi40 = dif.shift(1).rolling(40).max()
    td_d = dlist((c >= hi40 * 0.99) & (dif < difhi40))
    # I 缠论v2
    ch = chan26[chan26['code'] == code]
    chan_cnt = {t: int(n) for t, n in ch['type'].value_counts().items()}
    chan_dates = {t: ch[ch['type'] == t]['date'].str[4:].tolist() for t in chan_cnt}
    # J 相对强弱(个股N日累计-全市场等权N日累计)
    def ex(n):
        if len(chg26) < n: return None
        s = float((1 + chg26.tail(n) / 100).prod() - 1) * 100
        mm = mkt_ret.reindex(d26.tail(n)).astype(float)
        m = float((1 + mm / 100).prod() - 1) * 100
        return round(s - m, 1)
    # K 主升启动截面
    launch = None
    match = [m for m in ME if m.startswith(best_mon[:6])]
    if match:
        idx = ME.index(match[0])
        if idx > 0:
            prev_me = ME[idx - 1]
            me_key = prev_me if prev_me in cp.index else [d for d in cp.index if d < prev_me][-1]
            raw = {'pos250': float(pos250_m.at[me_key, code]), '量比': float(vr_m.at[me_key, code]),
                   'ma20乖离%': float(bias20_m.at[me_key, code]), 'ret60%': float(ret60_m.at[me_key, code]),
                   '前月地量天': float(dry_cnt.at[me_key, code])}
            snap = {k: (round(v, 2) if not np.isnan(v) else None) for k, v in raw.items()}
            pmap = [('pos250', 'pos250', raw['pos250']), ('量比', 'vr', raw['量比']),
                    ('ma20乖离%', 'bias20', raw['ma20乖离%']), ('ret60%', 'ret60', raw['ret60%']),
                    ('前月地量天', 'dry', raw['前月地量天'])]
            pct = {lbl: sec_pctl(pk, me_key, v) for lbl, pk, v in pmap if not np.isnan(v)}
            launch = {'基准月末': me_key, '快照': snap, '全市场分位': pct, '全市场当期中位': mkt_sec[prev_me]}

    out[code] = {
        'name': name, 'kind': kind,
        'K线组合': {'红三兵天': red3, '看涨吞没天': engulf},
        '收益分布': {'偏度': round(float(r.skew()), 2), '峰度': round(float(r.kurt()), 2),
                  '正收益占比%': round(float((r > 0).mean() * 100), 1),
                  '平均涨%': round(float(up.mean()), 2) if len(up) else None,
                  '平均跌%': round(float(dn.mean()), 2) if len(dn) else None,
                  '盈亏比': round(plr, 2) if plr else None},
        '逐月矩阵': mat, '主升月放量倍数': surge,
        '日内微观': {'高开天': int((gapdir > 0).sum()), '低开天': int((gapdir < 0).sum()),
                  '收盘位置全期%': round(float(clo_pos.mean()), 0),
                  '收盘位置近20日%': round(float(clo_pos.tail(20).mean()), 0),
                  '近20日上影%': round(float((hi26 - c26).tail(20).mean() / c26.iloc[-1] * 100), 2),
                  '近20日下影%': round(float((c26 - lo26).tail(20).mean() / c26.iloc[-1] * 100), 2)},
        '极端日档案': ext,
        '资金代理': {'量价齐升天': vp_up, '放量下跌天': vp_dn},
        '筹码乖离': bias_chip,
        'MACD结构': {'金叉': gc_d, '死叉': dc_d, '零轴上穿': zc_d, '顶背离': td_d},
        '缠论v2': {'买点统计': chan_cnt, '日期': chan_dates},
        '相对强弱': {'20日对全市场超额%': ex(20), '60日对全市场超额%': ex(60)},
        '主升启动截面': launch,
    }
    if kind == 'anchor':
        anchor = out[code]

# ---------- 锚验收(2026=600127金健全套; 2025=上纬新材YTD轻锚) ----------
# 口径说明: 金健报告(jjmy_profile_report)的量能/筹码字段基于脏amount库, 仅作方向参照不作硬判据;
#           MACD以panel dif真源对齐(实测panel与本脚本ewm零轴上穿完全一致).
if ANCHOR:
    bd, mi_, ch_ = anchor['收益分布'], anchor['日内微观'], anchor['筹码乖离']
    checks = [
        ('偏度~1.42', abs(bd['偏度'] - 1.42) < 0.15),
        ('峰度~3.03', abs(bd['峰度'] - 3.03) < 0.6),
        ('正收益占比~49.7', abs(bd['正收益占比%'] - 49.7) < 1.5),
        ('盈亏比~1.41', abs(bd['盈亏比'] - 1.41) < 0.1),
        ('高开~47/低开~95', abs(mi_['高开天'] - 47) <= 3 and abs(mi_['低开天'] - 95) <= 3),
        ('MACD零轴上穿=panel口径0126/0303/0331/0806', anchor['MACD结构']['零轴上穿'] == ['0126', '0303', '0331', '0806']),
        ('缠论2026无买点', sum(anchor['缠论v2']['买点统计'].values()) == 0),
        ('主升月放量倍数(vol口径)10~20', anchor['主升月放量倍数'] is not None and 10 <= anchor['主升月放量倍数'] <= 20),
    ]
    ls = anchor['主升启动截面']
    if ls:
        checks += [
            ('启动快照pos250~16.4', ls['快照']['pos250'] is not None and abs(ls['快照']['pos250'] - 16.4) < 5),
            ('启动快照ret60~-17.2', ls['快照']['ret60%'] is not None and abs(ls['快照']['ret60%'] + 17.2) < 5),
        ]
else:
    a = out.get('688585')
    v1f = json.load(open(V1_JSON, encoding='utf-8'))['features'].get('688585')
    checks = []
    if a and v1f:
        checks = [('上纬新材v1锚YTD=1820.55', abs(v1f['YTD%'] - 1820.55) < 0.01),
                  ('上纬新材收益分布可算', a['收益分布']['正收益占比%'] is not None),
                  ('上纬新材月度矩阵完整', len(a['逐月矩阵']) >= 11)]
n_pass = sum(1 for _, ok in checks if ok)
log(f'锚验收: {n_pass}/{len(checks)} PASS')
for n, ok in checks:
    log(f'  {n}: {"PASS" if ok else "FAIL"}')
if ANCHOR:
    log(f'  [对照] 20日筹码乖离(close口径)={ch_.get("20日")}% (金健报告+19.2%系脏amount的vwap口径,不可比)')
    log(f'  [对照] 启动快照量比(vol口径)={ls["快照"]["量比"] if ls and ls["快照"]["量比"] is not None else None} (金健模板0.109系脏amount产物,不可比)')
    log(f'  [对照] 主升月放量倍数(vol口径)={anchor["主升月放量倍数"]} (金健报告14.7系脏amount口径)')
    log(f'  [对照] MACD金叉={anchor["MACD结构"]["金叉"]} (金健报告4次系旧口径)')

json.dump({'meta': {'year': YEAR, 'asof': YEND, 'n': len(out), 'anchor_pass': f'{n_pass}/{len(checks)}',
                    'data_incident': 'amount列2026年1-6月约60%行脏(vwap偏离close>50%,5022只,142只中140只受染);量能族已全改volume口径(tdx交叉验证:600127 8/18=54.8M股,8/20=278.6M股分毫不差);v1的amount口径量能字段(量比/峰值额/放量/地量/日均额)废弃'},
           'features': out},
          open(OUT_FEATS, 'w', encoding='utf-8'), ensure_ascii=False)
log(f'v2逐只落盘 {OUT_FEATS}')

# ---------- 聚合 ----------
main = {c: f for c, f in out.items() if f['kind'] == 'doubled_now'}
agg = {
    '样本': len(main),
    '收益分布中位': {k: med_of([f['收益分布'].get(k) for f in main.values()]) for k in ['偏度', '峰度', '正收益占比%', '盈亏比']},
    'K线组合中位': {k: med_of([f['K线组合'].get(k) for f in main.values()]) for k in ['红三兵天', '看涨吞没天']},
    '日内微观中位': {k: med_of([f['日内微观'].get(k) for f in main.values()]) for k in ['高开天', '低开天', '收盘位置全期%', '收盘位置近20日%']},
    '筹码乖离中位': {k: med_of([f['筹码乖离'].get(k) for f in main.values()]) for k in ['20日', '60日', '120日']},
    '相对强弱中位': {k: med_of([f['相对强弱'].get(k) for f in main.values()]) for k in ['20日对全市场超额%', '60日对全市场超额%']},
    '主升月放量倍数中位': med_of([f['主升月放量倍数'] for f in main.values()]),
    '主升前月放量≥2倍占比%': round(sum(1 for f in main.values() if f['主升月放量倍数'] and f['主升月放量倍数'] >= 2) / len(main) * 100, 1),
    '缠论2026有买点只数': sum(1 for f in main.values() if sum(f['缠论v2']['买点统计'].values()) > 0),
    '缠论买点类型分布': {t: int(n) for t, n in pd.Series([t for f in main.values() for t in f['缠论v2']['买点统计']]).value_counts().items()},
}
ls_all = [f['主升启动截面'] for f in main.values() if f['主升启动截面']]
snap, pct, base_cnt = {}, {}, {}
for k in ['pos250', '量比', 'ma20乖离%', 'ret60%', '前月地量天']:
    vals = [l['快照'][k] for l in ls_all if l['快照'].get(k) is not None]
    snap[k] = {'中位': round(float(np.median(vals)), 2), 'P25': round(float(np.percentile(vals, 25)), 2),
               'P75': round(float(np.percentile(vals, 75)), 2)} if vals else None
    pv = [l['全市场分位'][k] for l in ls_all if l['全市场分位'].get(k) is not None]
    pct[k] = {'分位中位': round(float(np.median(pv)), 1), '低于市场中位占比%': round(sum(1 for v in pv if v < 50) / len(pv) * 100, 1)} if pv else None
for l in ls_all:
    base_cnt[l['基准月末']] = base_cnt.get(l['基准月末'], 0) + 1
agg['主升启动截面(137案例版)'] = {'样本': len(ls_all), '快照分布': snap, '全市场分位': pct, '基准月末分布': base_cnt}
json.dump(agg, open(OUT_AGG, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
log(f'v2聚合落盘 {OUT_AGG}')
print(json.dumps(agg, ensure_ascii=False, indent=1)[:3200])
