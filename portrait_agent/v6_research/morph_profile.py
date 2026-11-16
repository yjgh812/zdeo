# -*- coding: utf-8 -*-
"""上涨形态画像研究(2026-09-05 主人指令: 对股票上涨的所有类型做画像研究).

背景: 当前23画像特征全是统计量(收益率/波动/换手/MACD), 无K线形态结构特征.
主人点名: 岛型反转、底部放量突破等上涨形态未研究未捕捉.

研究框架(三维度):
  D1 画像(lift): 未来20日top10%上涨股 vs 非上涨股, 各形态出现率对比 → 哪些形态是上涨的前兆
  D2 预测力: 每形态信号日的截面超额收益(fwd5/fwd20收盘口径, 截面内对比) + t值
  D3 稳定性+共振: 分年超额稳定性; 高lift形态AND组合

形态库(15类, 全部无前视, t日收盘决策):
  岛型反转/底部放量突破/平台突破/W底/V型反转/圆弧底/均线多头/创一年新高/
  缩量后放量启动/MACD底背离/涨停突破平台/深跌企稳/头肩底/连续跳空强势/量价齐升3日
"""
import os, sys, json, time
import sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
import pandas as pd

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
FUND_DB = r'D:\workbuddyspace\databases\fundamental_data.db'
TRAD_PREFIX = ('600','601','603','605','000','001','002','003','300','301','302','688','689')
CHINEXT = ('300','301','302'); STAR = ('688','689')
PRICE_CAP = 20.0; MIN_AMT20 = 500e4; MIN_BARS = 120
HERE = os.path.dirname(os.path.abspath(__file__))

T0 = time.time()
def log(m): print(f'[{time.time()-T0:7.1f}s] {m}', flush=True)

PATTERNS = ['island_bottom','base_vol_breakout','box_breakout','w_bottom','v_bottom',
            'rounded_bottom','ma_bull','new_high250','vdry_breakout','macd_div',
            'limit_break','dip_rebound','hs_bottom','gap_up_run','volp_up3']

def load_pool():
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    end_sql = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    df = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>='20130101' AND date<=?", con, params=[end_sql])
    con.close()
    log(f'qfq rows={len(df)}')
    df = df[df['code'].str[:3].isin(TRAD_PREFIX)].copy()
    # ST过滤(与大面板同口径)
    con2 = sqlite3.connect(f'file:{FUND_DB}?mode=ro', uri=True)
    ind = pd.read_sql_query("SELECT code,name FROM industry_map_ths", con2)
    con2.close()
    st = ind[ind['name'].fillna('').str.contains('ST|退', regex=True)]['code']
    df = df[~df['code'].isin(set(st))].copy()
    df = df.sort_values(['code','date']).reset_index(drop=True)
    return df

def compute_patterns(g):
    """单code组内计算全部形态(输入按date升序). 返回dict of Series."""
    c = g['close'].astype(float); hi = g['high'].astype(float); lo = g['low'].astype(float)
    amt = g['amount'].astype(float); chg = g['change_pct'].astype(float)
    idx = g.index
    out = {}
    ph, pl = hi.shift(1), lo.shift(1)
    gap_down = (hi < pl).astype(float)
    gap_up = (lo > ph).astype(float)
    # 1 岛型反转: 近10日有向下跳空 + 当日向上跳空
    out['island_bottom'] = ((gap_down.rolling(10).max() > 0) & (gap_up > 0)).astype(float)
    # 2 底部放量突破: 60日窄幅(<30%) + 收盘破60日高 + 量>2x20日均量
    hi60p = hi.shift(1).rolling(60).max(); lo60p = lo.shift(1).rolling(60).min()
    amt20 = amt.shift(1).rolling(20).mean()
    breakout = c > hi60p
    out['base_vol_breakout'] = (((hi60p/lo60p - 1) < 0.30) & breakout & (amt > 2*amt20)).astype(float)
    # 3 平台突破(不限位置): 60日收盘cv<6% + 破高 + 量>1.5x
    cv60 = c.shift(1).rolling(60).std() / c.shift(1).rolling(60).mean()
    out['box_breakout'] = ((cv60 < 0.06) & breakout & (amt > 1.5*amt20)).astype(float)
    # 4 W底: 左段低/右段低差<4% + 破中段峰
    l1 = lo.shift(35).rolling(25).min(); l2 = lo.shift(5).rolling(25).min()
    mid_peak = hi.shift(25).rolling(20).max()
    out['w_bottom'] = (((l1-l2).abs()/l1 < 0.04) & (c > mid_peak) & (mid_peak > l1*1.05)).astype(float)
    # 5 V型反转: 20日低点至今涨>=8% 且 该低点较前高跌>=15%
    lo20 = lo.shift(1).rolling(20).min()
    hi40 = hi.shift(20).rolling(20).max()
    out['v_bottom'] = (((c/lo20 - 1) >= 0.08) & (lo20/hi40 - 1 <= -0.15)).astype(float)
    # 6 圆弧底: 低点抬升(近15日低>前15日低*1.03) + 仍处60日低位 + 当日上攻
    lof = lo.shift(25).rolling(15).min(); lol = lo.shift(1).rolling(15).min()
    hi60 = hi.shift(1).rolling(60).max()
    out['rounded_bottom'] = (((lol > lof*1.03) & (c < hi60*0.85) & (c > lol*1.02)) &
                             (c > c.shift(1))).astype(float)
    # 7 均线多头: ma5>ma10>ma20>ma60 & c>ma5
    ma5, ma10 = c.rolling(5).mean(), c.rolling(10).mean()
    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    out['ma_bull'] = ((ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60) & (c > ma5)).astype(float)
    # 8 创一年新高(近似250日)
    hi250 = hi.shift(1).rolling(250, min_periods=120).max()
    out['new_high250'] = (c >= hi250*0.998).astype(float)
    # 9 缩量后放量启动: 近5日有缩量(5日均量<0.65*20日均量) + 当日量>1.8x + 涨>2%
    dry = (amt.rolling(5).mean() < 0.65*amt.rolling(20).mean())
    dry_recent = dry.shift(1).rolling(5).max() > 0
    out['vdry_breakout'] = (dry_recent & (amt > 1.8*amt20) & (chg > 2)).astype(float)
    # 10 MACD底背离: 价40日新低附近 + dif未新低 (dif从大面板join)
    # 11 涨停突破: 涨停 + 破60日高 (阈值按板)
    lim_thr = 19.8 if g['code'].iloc[0][:3] in CHINEXT or g['code'].iloc[0][:3] in STAR else 9.8
    out['limit_break'] = ((chg >= lim_thr) & breakout).astype(float)
    # 12 深跌企稳: 60日跌>=25% + 近3日反弹
    drop60 = c/c.shift(60) - 1
    out['dip_rebound'] = ((drop60 <= -0.25) & (c > c.shift(3)) &
                          (chg.rolling(3).mean() > 0.5)).astype(float)
    # 13 头肩底: 中低<左右低*0.98 + 左右差<5% + 破肩线
    lsh = lo.shift(35).rolling(15).min(); hsh = lo.shift(15).rolling(15).min()
    rsh = lo.shift(1).rolling(15).min()
    out['hs_bottom'] = ((hsh < lsh*0.98) & (hsh < rsh*0.98) &
                        ((lsh-rsh).abs()/lsh < 0.05) & (c > (lsh+rsh)/2*1.05)).astype(float)
    # 14 连续跳空强势: 近5日>=2次向上跳空 + c>ma20
    out['gap_up_run'] = ((gap_up.rolling(5).sum() >= 2) & (c > ma20)).astype(float)
    # 15 量价齐升3日
    vp = (c > c.shift(1)) & (amt > amt.shift(1))
    out['volp_up3'] = (vp & vp.shift(1) & vp.shift(2)).astype(float)
    return pd.DataFrame(out, index=idx)

def main():
    log('加载行情...')
    df = load_pool()
    # join大面板的dif(MACD底背离用)
    panel = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v2_big.pkl'))
    dif_df = panel[['code', 'date', 'dif']].dropna(subset=['dif'])
    df = df.merge(dif_df, on=['code', 'date'], how='left')
    log(f'合并dif后 {len(df)} stocks={df["code"].nunique()}')
    # 前向收益
    df = df.sort_values(['code', 'date']).reset_index(drop=True)
    log('计算fwd5/fwd20...')
    df['fwd5_close'] = df.groupby('code')['close'].transform(lambda s: s.shift(-5)/s - 1)
    df['fwd20_close'] = df.groupby('code')['close'].transform(lambda s: s.shift(-20)/s - 1)
    # MACD底背离(需dif)
    c = df['close']
    df['price_lo40'] = df.groupby('code')['close'].transform(lambda s: s.shift(1).rolling(40).min())
    df['dif_lo40'] = df.groupby('code')['dif'].transform(lambda s: s.shift(1).rolling(40).min())
    df['macd_div'] = ((df['close'] <= df['price_lo40']*1.01) &
                      (df['dif'] > df['dif_lo40'])).astype(float)
    # 池过滤(与大面板一致): close<=20 + amt20>=500万
    df['amt20_'] = df.groupby('code')['amount'].transform(lambda s: s.rolling(20, min_periods=10).mean())
    df = df[(df['close'] <= PRICE_CAP) & (df['amt20_'] >= MIN_AMT20)].copy()
    # 窗口过滤: 每code至少120bar(rolling 60预热)
    bar_cnt = df.groupby('code')['code'].transform('size')
    df = df[bar_cnt >= MIN_BARS].copy()
    log(f'池过滤后 {len(df)} stocks={df["code"].nunique()}')

    # 形态计算(分块per code)
    log('计算15形态...')
    parts = []
    for k, (code, g) in enumerate(df.groupby('code', sort=False)):
        if k % 800 == 0: log(f'  stock {k}')
        pats = compute_patterns(g)
        pats['code'] = code; pats['date'] = g['date'].values
        parts.append(pats)
    pats_all = pd.concat(parts, ignore_index=True)
    log(f'形态面板 {pats_all.shape}')
    # 主面板只留必要列合并
    base = df[['code','date','close','fwd5_close','fwd20_close']].reset_index(drop=True)
    m = base.merge(pats_all, on=['code','date'], how='inner')
    m['macd_div'] = m['macd_div'] if 'macd_div' in m else 0
    # macd_div 在df上算的, 合并
    md = df[['code','date','macd_div']]
    m = m.drop(columns=['macd_div'], errors='ignore').merge(md, on=['code','date'], how='left')
    m['macd_div'] = m['macd_div'].fillna(0)
    log(f'研究面板 {m.shape}')

    # ==== D1 画像: 上涨股(fwd20截面top10%)的形态出现率 lift ====
    log('D1 形态画像(lift)...')
    m['up20'] = m.groupby('date')['fwd20_close'].rank(pct=True)
    m['is_up'] = (m['up20'] >= 0.90).astype(float)
    m.loc[m['fwd20_close'].isna(), 'is_up'] = np.nan
    valid = m.dropna(subset=['is_up'])
    up, dn = valid[valid['is_up'] == 1], valid[valid['is_up'] == 0]
    profile = []
    for p in PATTERNS:
        p_up = up[p].mean(); p_dn = dn[p].mean()
        lift = p_up / p_dn if p_dn > 0 else np.nan
        profile.append({'pattern': p, 'rate_up': round(p_up*100, 3), 'rate_dn': round(p_dn*100, 3),
                        'lift': round(lift, 3) if not np.isnan(lift) else None,
                        'n_signals': int(m[p].sum())})
    profile = pd.DataFrame(profile).sort_values('lift', ascending=False)
    log('\n形态画像(fwd20 top10%上涨股 vs 其余, 出现率%/lift/14年信号总数):\n' +
        profile.to_string(index=False))

    # ==== D2 预测力: 信号日截面超额(fwd5/fwd20) ====
    log('D2 形态预测力(截面超额+t值)...')
    results = []
    for p in PATTERNS:
        sub = m[m[p] > 0].dropna(subset=['fwd20_close'])
        # 每日截面超额: 信号股均值 - 当日全池均值
        day_ex20 = sub.groupby('date')['fwd20_close'].mean() - m.dropna(subset=['fwd20_close']).groupby('date')['fwd20_close'].mean()
        day_ex20 = day_ex20.dropna()
        sub5 = m[m[p] > 0].dropna(subset=['fwd5_close'])
        day_ex5 = sub5.groupby('date')['fwd5_close'].mean() - m.dropna(subset=['fwd5_close']).groupby('date')['fwd5_close'].mean()
        day_ex5 = day_ex5.dropna()
        def _t(s):
            return float(s.mean() / (s.std()/np.sqrt(len(s)))) if len(s) > 5 and s.std() > 0 else np.nan
        results.append({
            'pattern': p, 'n_sig': int(m[p].sum()), 'n_days20': len(day_ex20),
            'ex20_mean_pct': round(day_ex20.mean()*100, 3) if len(day_ex20) else None,
            'ex20_t': round(_t(day_ex20), 2) if len(day_ex20) else None,
            'win20_pct': round((day_ex20 > 0).mean()*100, 1) if len(day_ex20) else None,
            'ex5_mean_pct': round(day_ex5.mean()*100, 3) if len(day_ex5) else None,
            'ex5_t': round(_t(day_ex5), 2) if len(day_ex5) else None,
            'win5_pct': round((day_ex5 > 0).mean()*100, 1) if len(day_ex5) else None,
        })
    res = pd.DataFrame(results).sort_values('ex20_mean_pct', ascending=False)
    log('\n形态预测力(信号日截面超额, fwd20/fwd5 收盘口径):\n' + res.to_string(index=False))

    # ==== D3 分年稳定性(top5形态) + 共振 ====
    log('D3 分年稳定性...')
    day_mean_all = m.dropna(subset=['fwd20_close']).groupby('date')['fwd20_close'].mean()
    top5 = res.head(5)['pattern'].tolist()
    yearly = {}
    for p in top5:
        sub = m[(m[p] > 0)].dropna(subset=['fwd20_close']).copy()
        sub['year'] = sub['date'].str[:4]
        sub['ex'] = sub['fwd20_close'] - sub['date'].map(day_mean_all)   # 全池日均值对照(原版子集内均值恒≈0的bug修复)
        yr = sub.groupby('year')['ex'].agg(['size', 'mean', lambda s: (s > 0).mean()*100])
        yr.columns = ['n', 'avg_ex_pct', 'win_pct']
        yr['avg_ex_pct'] = (yr['avg_ex_pct']*100).round(3); yr['win_pct'] = yr['win_pct'].round(1)
        yearly[p] = yr.to_dict('index')
    for p, yr in yearly.items():
        neg = sum(1 for v in yr.values() if v['avg_ex_pct'] < 0)
        log(f'  {p}: {len(yr)}年里{neg}年负超额')
    # 共振: top3 lift形态 AND
    top3p = profile.head(3)['pattern'].tolist()
    m['combo3'] = m[top3p].max(axis=1)  # 先占位
    m['combo3'] = m[top3p].min(axis=1)   # AND=全1才1
    sub = m[m['combo3'] > 0].dropna(subset=['fwd20_close'])
    if len(sub) > 50:
        day_ex = sub.groupby('date')['fwd20_close'].mean() - m.dropna(subset=['fwd20_close']).groupby('date')['fwd20_close'].mean()
        day_ex = day_ex.dropna()
        t = float(day_ex.mean()/(day_ex.std()/np.sqrt(len(day_ex)))) if day_ex.std() > 0 else np.nan
        combo = {'patterns': top3p, 'n': len(sub), 'ex20_pct': round(day_ex.mean()*100, 3),
                 't': round(t, 2), 'win_pct': round((day_ex > 0).mean()*100, 1)}
        log(f'共振组合: {json.dumps(combo, ensure_ascii=False)}')
    else:
        combo = {'patterns': top3p, 'n': len(sub), 'note': '样本不足50'}
        log(f'共振组合样本不足: n={len(sub)}')

    # 落盘
    fp = os.path.join(HERE, 'reports', 'morph_profile_20260905.json')
    json.dump({'profile_lift': profile.to_dict('records'),
               'predictive': res.to_dict('records'),
               'yearly': yearly, 'combo3': combo,
               'meta': {'rows': len(m), 'stocks': int(m['code'].nunique()),
                        'dates': f"{m['date'].min()}~{m['date'].max()}"}},
              open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    log(f'落盘 {fp}')

if __name__ == '__main__':
    main()
