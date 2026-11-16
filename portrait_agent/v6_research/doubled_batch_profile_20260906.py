# -*- coding: utf-8 -*-
"""2026翻倍股批量画像采集(2026-09-06 主人指令: 用画像特征系统全量采集以上所有股票2026年走势特征).

对象: 137只现价翻倍 + 2只年内曾翻倍(有研硅停牌/立方退退市) + 3只2026新股相对首日翻倍 = 142只.
清单真源: data/_ytd_double_scan_named_20260906.json(本会话上轮qfq扫描产物, 已交叉验证).
数据源: qfq weekly备份(主库并行重建期fail-closed, 禁碰主库), 与上轮扫描同源保证口径一致.
合同: 五族特征与 data/jjmy_features_20260906.json 同构; 15形态复用 morph_profile.compute_patterns 真源.
输出: data/doubled_batch_features_20260906.json(逐只) + portrait_agent/reports/doubled_batch_profile_20260906.json(聚合) + md
"""
import os, sys, json, time, sqlite3, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd
from stock_name_lookup import get_name

ap = argparse.ArgumentParser()
ap.add_argument('--year', choices=['2026', '2025', '2025now'], default='2026',
                help='2025now=2025翻倍股清单, 窗口20250101→20260904(至今)')
args = ap.parse_args()
YEAR = args.year

QFQ_DB = r'D:\zcodesj\backups\dragon_quant_qfq\weekly\dragon_quant_qfq_20260906.db'
SCAN_JSON = {'2026': r'D:\zcodesj\data\_ytd_double_scan_named_20260906.json',
             '2025': r'D:\zcodesj\data\_ytd2025_double_named_20260906.json',
             '2025now': r'D:\zcodesj\data\_ytd2025_double_named_20260906.json'}[YEAR]
OUT_FEATS = {'2026': r'D:\zcodesj\data\doubled_batch_features_20260906.json',
             '2025': r'D:\zcodesj\data\doubled_batch_features_2025.json',
             '2025now': r'D:\zcodesj\data\doubled_batch_features_2025now.json'}[YEAR]
OUT_AGG = {'2026': r'D:\zcodesj\portrait_agent\reports\doubled_batch_profile_20260906.json',
           '2025': r'D:\zcodesj\portrait_agent\reports\doubled_batch_profile_2025.json',
           '2025now': r'D:\zcodesj\portrait_agent\reports\doubled_batch_profile_2025now.json'}[YEAR]
Y0, YEND = {'2026': ('20260101', '20260904'), '2025': ('20250101', '20251231'),
            '2025now': ('20250101', '20260904')}[YEAR]
BASE_D = {'2026': None, '2025': '20241231', '2025now': '20241231'}[YEAR]  # 基期由Y0前最后交易日自动取
T0 = time.time()
def log(m): print(f'[{time.time()-T0:6.1f}s] {m}', flush=True)

# ---------- 清单 ----------
scan = json.load(open(SCAN_JSON, encoding='utf-8'))
universe = []  # (code, name, kind)
if YEAR == '2026':
    for r in scan['dbl_now']:
        universe.append((r['code'], r['name'], 'doubled_now'))
    for r in scan['dbl_peak']:
        nm = get_name(r['code']) or r['name']
        universe.append((r['code'], nm, 'peaked_then_fell'))
    for code in ['688808', '920895', '688813']:
        universe.append((code, get_name(code) or code, 'new_ipo_double'))
else:  # 2025: named json存dbl_now_named/newdbl; dbl_peak在scan json
    scan_raw = json.load(open(r'D:\zcodesj\data\_ytd2025_double_scan_20260906.json', encoding='utf-8'))
    for r in scan['dbl_now_named']:
        universe.append((r['code'], r['name'], 'doubled_now'))
    for r in scan_raw['dbl_peak']:
        nm = get_name(r['code']) or '??'
        universe.append((r['code'], nm, 'peaked_then_fell'))
    for c, g in scan['newdbl']:
        universe.append((c, get_name(c) or c, 'new_ipo_double'))
log(f'清单 {len(universe)} 只: doubled_now={sum(1 for u in universe if u[2]=="doubled_now")}, '
    f'peaked={sum(1 for u in universe if u[2]=="peaked_then_fell")}, new_ipo={sum(1 for u in universe if u[2]=="new_ipo_double")}')
# 名称源健康记录: stock_name_lookup 对已退市股可能返回代码占位(如'sz300344'), 显式留痕不静默
unresolved = [(c, n) for c, n, _ in universe if n.replace('sh', '').replace('sz', '').isdigit()]
if unresolved:
    log(f'⚠名称占位(名称库未收录): {unresolved}')

# ---------- 数据加载(仅清单内code, 2013起供250日窗口) ----------
codes = [u[0] for u in universe]
con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
marks = ','.join('?' * len(codes))
df = pd.read_sql_query(
    f"SELECT code,date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
    f"WHERE code IN ({marks}) AND date>='20130101' AND date<='{YEND}'", con, params=codes)
con.close()
df = df.sort_values(['code', 'date']).reset_index(drop=True)
assert df['code'].nunique() == len(codes), f'数据覆盖不全: {df.code.nunique()}/{len(codes)}'
log(f'rows={len(df)} codes={df.code.nunique()}')

def mkt_of(code):
    p = code[:3]
    if p in ('688', '689'): return '科创板'
    if p in ('300', '301', '302'): return '创业板'
    if code[0] in ('8', '4') or code[:2] == '92': return '北交所'
    return '主板'

def limit_thr(code, name):
    p = code[:3]
    if p in ('300', '301', '302', '688', '689'): return 20.0
    if code[0] in ('8', '4') or code[:2] == '92': return 30.0
    return 5.0 if 'ST' in (name or '') else 10.0

def limit_mask(dates26, chg26, code, name):
    """涨停日判定. 主板ST年内摘/戴帽切换(实测000711/002528均在7月前后5%→10%制):
    按月自适应阈值(月max>5.5%判定为10%制月), 月粒度切换误差如实标注."""
    p = code[:3]
    if p in ('300', '301', '302', '688', '689') or code[0] in ('8', '4') or code[:2] == '92':
        return chg26 >= limit_thr(code, name) * 0.97, ''
    if 'ST' not in (name or ''):
        return chg26 >= 10.0 * 0.97, ''
    mons = pd.Series(dates26.values).str[:6]
    thr_eff = chg26.groupby(mons.values).transform('max') > 5.5
    thr_arr = np.where(thr_eff, 10.0, 5.0)
    mask = chg26.values >= thr_arr * 0.97
    return pd.Series(mask, index=chg26.index), '主板ST月粒度自适应阈值(年内摘/戴帽切换)'

# ---------- 15形态真源复用(morph_profile.compute_patterns) ----------
from morph_profile import compute_patterns

def max_run(mask_dates):
    """连续涨停游程(输入有序bool序列) -> 最长连板"""
    best = cur = 0
    for v in mask_dates:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best

def compress_events(dates):
    """同形态连续日合并为[起,止]"""
    if not len(dates): return []
    out, s, p = [], dates[0], dates[0]
    for d in dates[1:]:
        if d != p:
            out.append([s, p]); s = d
        p = d
    out.append([s, p])
    return out

feats, traj = {}, {}
for code, name, kind in universe:
    g = df[df['code'] == code].reset_index(drop=True)
    g26 = g[g['date'] >= Y0].reset_index(drop=True)
    c = g['close'].astype(float); hi = g['high'].astype(float); lo = g['low'].astype(float)
    amt = g['amount'].astype(float); chg = g['change_pct'].astype(float)
    c26 = g26['close'].astype(float); hi26 = g26['high'].astype(float); lo26 = g26['low'].astype(float)
    amt26 = g26['amount'].astype(float); chg26 = g26['change_pct'].astype(float)
    last_date = g['date'].iloc[-1]
    last = c.iloc[-1]
    thr = limit_thr(code, name)

    # --- 趋势族 ---
    ma5, ma20, ma60 = c.rolling(5).mean(), c.rolling(20).mean(), c.rolling(60).mean()
    ma250h = hi.rolling(250).max(); ma250l = lo.rolling(250).min()
    pos250 = (last - ma250l.iloc[-1]) / (ma250h.iloc[-1] - ma250l.iloc[-1]) * 100 if ma250h.iloc[-1] > ma250l.iloc[-1] else np.nan
    bull = (ma5 > ma20) & (ma20 > ma60)
    bull_days_26 = int(bull[g['date'] >= Y0].sum())
    n26 = len(g26)
    # 月度收益
    m = c26.groupby(g26['date'].str[:6]).agg(['first', 'last'])
    mon_ret = ((m['last'] / m['first'] - 1) * 100).round(1).to_dict()
    # 月末对月末口径修正: 首值用上月末收盘
    closes26 = c26.tolist(); dates26 = g26['date'].tolist()
    mons = sorted(mon_ret)
    # 基准: 上年末收盘; 2026年内上市新股无上年末数据时用上市首日收盘(首月口径=上市以来)
    prev_last = c[g['date'] < Y0].iloc[-1] if (g['date'] < Y0).any() else None
    ipo_note = prev_last is None
    for i, mo in enumerate(mons):
        end_idx = max(j for j, d in enumerate(dates26) if d.startswith(mo))
        if i == 0:
            start_ref = prev_last if prev_last is not None else closes26[0]
        else:
            start_ref = closes26[max(j for j, d in enumerate(dates26) if d.startswith(mons[i-1]))]
        mon_ret[mo] = round((closes26[end_idx] / start_ref - 1) * 100, 1)
    best_mon = max(mon_ret, key=mon_ret.get)

    # --- 动量族 ---
    def ret(n):
        return round((last / c.iloc[-1-n] - 1) * 100, 1) if len(c) > n else None
    up = (chg26 > 0).tolist()
    best_run = cur = 0
    for v in up:
        cur = cur + 1 if v else 0
        best_run = max(best_run, cur)
    cur_run = 0
    for v in reversed(up):
        if v: cur_run += 1
        else: break

    # --- 波动族 ---
    amp26 = ((hi26 - lo26) / c26.shift(1) * 100).dropna()
    amp_all = ((hi - lo) / c.shift(1) * 100).dropna()

    # --- 量能族(volume口径; amount列2026年1-6月约60%行脏已废弃, 见meta.data_incident) ---
    vol = g['volume'].astype(float)
    vol26 = g26['volume'].astype(float)
    v5 = vol26.tail(5).mean(); v20 = vol26.tail(20).mean()
    vol20_prev = vol.shift(1).rolling(20).mean()[g['date'] >= Y0]
    vol_days = int((vol26.values > 2 * vol20_prev.values).sum())
    dry_days = int((vol26.values < 0.5 * vol20_prev.values).sum())
    pk_i = vol26.values.argmax()

    # --- 事件族 ---
    lim, thr_note = limit_mask(g26['date'], chg26, code, name)
    lim_dates = g26.loc[lim.values, 'date'].tolist()
    ph, pl = hi.shift(1), lo.shift(1)
    g26_idx = g['date'] >= Y0
    gap_up_n = int(((lo > ph) & g26_idx).sum()); gap_dn_n = int(((hi < pl) & g26_idx).sum())
    nh20 = int((hi26.values > hi.shift(1).rolling(20).max()[g26_idx].values).sum())
    nh250 = int((hi26.values > hi.shift(1).rolling(250).max()[g26_idx].values).sum())
    # MACD(12,26,9)
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    zc = ((dif > 0) & (dif.shift(1) <= 0)) & g26_idx
    gc = ((dif > dea) & (dif.shift(1) <= dea.shift(1))) & g26_idx
    zc_dates = g.loc[zc, 'date'].tolist(); gc_n = int(gc.sum())
    # MACD底背离(morph主流程同公式: 价格40日新低+DIF不新低, dif同标准12/26/9)
    price_lo40 = c.shift(1).rolling(40).min(); dif_lo40 = dif.shift(1).rolling(40).min()
    md_mask = ((c <= price_lo40 * 1.01) & (dif > dif_lo40)) & g26_idx
    md_dates = g.loc[md_mask, 'date'].tolist()

    # --- 15形态(morph真源) + macd_div(主流程同公式) ---
    pat = compute_patterns(g)
    pat['macd_div'] = md_mask.astype(float)
    pat_days, pat_dates = {}, {}
    for k, s in pat.items():
        s26 = s[g26_idx].fillna(0)
        pat_days[k] = int(s26.sum())
        pat_dates[k] = compress_events(g26.loc[s26.values > 0, 'date'].tolist())

    f = {
        'name': name, 'kind': kind, 'market': mkt_of(code), 'limit_thr': thr,
        'last_date': last_date, 'bars_2026': n26,
        'YTD%': round((last / c[g['date'] < Y0].iloc[-1] - 1) * 100, 2) if (g['date'] < Y0).any() else None,
        '首月基准': '上年末收盘' if not ipo_note else '上市首日收盘(2026年内上市)',
        '现价/ma5%': round((last / ma5.iloc[-1] - 1) * 100, 2) if not np.isnan(ma5.iloc[-1]) else None,
        '现价/ma20%': round((last / ma20.iloc[-1] - 1) * 100, 2) if not np.isnan(ma20.iloc[-1]) else None,
        '现价/ma60%': round((last / ma60.iloc[-1] - 1) * 100, 2) if not np.isnan(ma60.iloc[-1]) else None,
        'pos250%': round(pos250, 2) if not np.isnan(pos250) else None,
        '距一年高点%': round((last / ma250h.iloc[-1] - 1) * 100, 2) if not np.isnan(ma250h.iloc[-1]) else None,
        '均线多头天数/2026': bull_days_26, '多头占比%': round(bull_days_26 / n26 * 100, 1),
        '月度收益%': mon_ret, '主升月': best_mon, '主升月涨幅%': mon_ret[best_mon],
        'ret5%': ret(5), 'ret10%': ret(10), 'ret20%': ret(20), 'ret60%': ret(60),
        '当前连涨天数': cur_run, '2026最长连涨': best_run,
        'amp20均值%': round(amp26.tail(20).mean(), 2) if len(amp26) else None,
        'amp全期均值%': round(amp_all.mean(), 2) if len(amp_all) else None,
        'vol20(日收益std)%': round(chg26.tail(20).std(), 2) if len(chg26) > 5 else None,
        '2026最大单日涨幅%': round(chg26.max(), 2) if n26 else None,
        '2026最大单日跌幅%': round(chg26.min(), 2) if n26 else None,
        '量比(5d/20d)': round(v5 / v20, 2) if v20 > 0 else None,
        '2026峰值成交量M股': round(vol26.max() / 1e6, 1) if n26 else None,
        '峰值成交日': g26.loc[pk_i, 'date'] if n26 else None,
        '放量日数(>2x)': vol_days, '地量日数(<0.5x)': dry_days,
        '涨停次数': int(lim.sum()), '最高连板': max_run(lim.tolist()), '涨停日期': lim_dates,
        '向上跳空次数': gap_up_n, '向下跳空次数': gap_dn_n,
        '创20日新高天数': nh20, '创一年新高天数': nh250,
        'MACD零轴上穿次数': len(zc_dates), 'MACD最近零轴上穿': zc_dates[-1] if zc_dates else None,
        'MACD金叉次数': gc_n, 'MACD底背离天数': len(md_dates),
        '涨停阈值备注': thr_note,
        '形态天数': pat_days, '形态事件': pat_dates,
    }
    feats[code] = f
    traj[code] = {
        '涨停日': lim_dates, 'MACD零轴上穿日': zc_dates,
        '峰值量日': g26.loc[pk_i, 'date'] if n26 else None,
        '年内最高点日': g26.loc[hi26.values.argmax(), 'date'] if n26 else None,
    }

json.dump({'meta': {'year': YEAR, 'asof': YEND, 'source': os.path.basename(QFQ_DB), 'n': len(feats),
                    'data_incident': 'amount列2026年1-6月约60%行脏(vwap偏离close>50%,5022只);量能族已全改volume口径(tdx交叉验证通过),旧amount口径量能数值废弃'}, 'features': feats},
          open(OUT_FEATS, 'w', encoding='utf-8'), ensure_ascii=False)
log(f'逐只特征落盘 {OUT_FEATS}')

# ---------- 横截面聚合 ----------
main = [f for f in feats.values() if f['kind'] == 'doubled_now']
def agg(key, fmt=None):
    vals = [f[key] for f in main if f.get(key) is not None]
    if not vals: return None
    a = np.array(vals, dtype=float)
    return {'中位': round(float(np.median(a)), 2), '均值': round(float(a.mean()), 2),
            'P25': round(float(np.percentile(a, 25)), 2), 'P75': round(float(np.percentile(a, 75)), 2),
            'min': round(float(a.min()), 2), 'max': round(float(a.max()), 2)}
num_keys = ['YTD%', '现价/ma20%', '现价/ma60%', 'pos250%', '距一年高点%', '多头占比%', '主升月涨幅%',
            'ret20%', 'ret60%', '2026最长连涨', 'amp20均值%', 'amp全期均值%', '2026最大单日涨幅%',
            '量比(5d/20d)', '2026峰值成交量M股', '放量日数(>2x)', '地量日数(<0.5x)',
            '涨停次数', '最高连板', '向上跳空次数', '创一年新高天数', 'MACD零轴上穿次数', 'MACD金叉次数']
agg_out = {'样本': len(main), '特征分布(137只现价翻倍)': {k: agg(k) for k in num_keys}}
agg_out['主升月分布'] = pd.Series([f['主升月'] for f in main]).value_counts().to_dict()
agg_out['涨停驱动分类'] = {
    '涨停≥5次(连板驱动)': sum(1 for f in main if f['涨停次数'] >= 5),
    '涨停1-4次(事件驱动)': sum(1 for f in main if 1 <= f['涨停次数'] < 5),
    '零涨停(趋势/跳空驱动)': sum(1 for f in main if f['涨停次数'] == 0),
}
agg_out['当前状态'] = {
    '距高点回撤<10%(仍强势)': sum(1 for f in main if f['距一年高点%'] is not None and f['距一年高点%'] > -10),
    '回撤10-30%': sum(1 for f in main if f['距一年高点%'] is not None and -30 <= f['距一年高点%'] <= -10),
    '回撤>30%': sum(1 for f in main if f['距一年高点%'] is not None and f['距一年高点%'] < -30),
}
agg_out['形态覆盖率(出现该形态的股票数, 137只)'] = {
    k: sum(1 for f in main if f['形态天数'][k] > 0)
    for k in next(iter(main))['形态天数']}
json.dump(agg_out, open(OUT_AGG, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
log(f'聚合报告落盘 {OUT_AGG}')
print(json.dumps(agg_out, ensure_ascii=False, indent=1)[:2500])
