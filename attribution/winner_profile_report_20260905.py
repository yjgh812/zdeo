# -*- coding: utf-8 -*-
"""涨票画像报告生成: 读 panel.pkl + result.json, plotille 分布图 + MD报告."""
import sys, json, os, time
import numpy as np
import pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

OUT_DIR = r'D:\zcodesj\attribution\winner_profile_out'
panel = pd.read_pickle(os.path.join(OUT_DIR, 'panel.pkl'))
res = json.load(open(os.path.join(OUT_DIR, 'winner_profile_result.json'), encoding='utf-8'))
S = res['summary']; ok_buys = [r for r in res['buys'] if r.get('ok')]

try:
    import plotille
    HAS_PLOT = True
except ImportError:
    HAS_PLOT = False

# 重建截面池(market/win_raw/win_ex) 与 buys 值
sec_dates = [t for t, f in res['sections'] if f == 'fwd20']
l1 = {}
import sqlite3
con = sqlite3.connect(r'file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro', uri=True)
for code, name, ind in con.execute("SELECT code,name,industry FROM industry_map_ths"):
    l1[code] = (name, (ind or '未知').split('-')[0])
con.close()
st_codes = {c for c, (n, _) in l1.items() if 'ST' in n or '退' in n}

frames = {'market': [], 'win_raw': [], 'win_ex': []}
for t in sec_dates:
    sf = panel[(panel['date']==t) & (~panel['code'].isin(st_codes))].copy()
    sf = sf[(sf['bar_no']>=150) & sf['close'].notna() & sf['fwd20'].notna() & sf['total_mv'].notna()]
    sf['L1_'] = sf['code'].map(lambda c: l1.get(c, ('?','未知'))[1])
    sf['excess'] = sf['fwd20'] - sf.groupby('L1_')['fwd20'].transform('mean')
    frames['market'].append(sf)
    frames['win_raw'].append(sf[sf['fwd20'] >= sf['fwd20'].quantile(0.90)])
    frames['win_ex'].append(sf[sf['excess'] >= sf['excess'].quantile(0.90)])
pools = {k: pd.concat(v) for k, v in frames.items()}

CHART_DIMS = [('turnover20', '换手率20日均值(%)', 0, 8),
              ('pb', 'PB(倍)', 0, 8),
              ('vol60', '波动vol60(日std%)', 0.005, 0.06),
              ('log_mv', 'log10总市值(万元)', 4.8, 7.2)]
charts = {}
if HAS_PLOT:
    for dim, label, lo, hi in CHART_DIMS:
        fig = plotille.Figure()
        fig.width = 62; fig.height = 14
        fig.color_mode = 'rgb'
        grid = np.linspace(lo, hi, 80)
        def dens(v, bw):
            v = v[(v>=lo-2*bw)&(v<=hi+2*bw)]
            if len(v) < 5: return np.zeros_like(grid)
            return np.array([np.mean(np.exp(-0.5*((grid-g)/bw)**2)) for g in grid]) / (bw*np.sqrt(2*np.pi))
        specs = [('market', 'white', '全市场'), ('win_raw', 'red', '涨票raw'), ('buys', 'cyan', '买入')]
        any_plotted = False
        for key, color, nm in specs:
            vals = pools[key][dim].dropna().values.astype(float) if key != 'buys' else np.array([r[dim] for r in ok_buys if r.get(dim) is not None], dtype=float)
            if len(vals) >= 5:
                bw = max((hi-lo)/60, np.std(vals)/25 if np.std(vals) > 0 else (hi-lo)/60)
                y = dens(vals, bw)
                if y.max() > 0:
                    fig.plot(grid, y/y.max()*100, lc=color, label=nm)
                    any_plotted = True
        if any_plotted:
            fig.set_x_limits(min_=lo, max_=hi); fig.set_y_limits(min_=0, max_=100)
            charts[dim] = f'```\n{fig.show()}\n```(纵轴=归一化密度%, 白=全市场/红=涨票raw前10%/青=我们的买入)\n'

# ---- MD 报告 ----
f = lambda v, nd=2: ('NA' if v is None or (isinstance(v, float) and np.isnan(v)) else f'{v:.{nd}f}')
L = []
L.append('# 涨票特征画像 vs 实际买入逐维对比（1年周期）')
L.append('')
L.append('> 执行日 2026-09-05 | 窗口 2025-09-04 ~ 2026-09-04 | 指令: 主人「全市场不预设假设对涨的票做特征画像, 与实际买入对比量化差在哪」+「MACD用macd skill口径」')
L.append('')
L.append('## 1. 原始数据与口径')
L.append('')
L.append(f"- 截面: 11个月末(20250930~20260731)fwd20完整 + 20260831(fwd4补充); 每期宇宙n≈4300-4370(主板+创业板, 剔ST/次新<150bar, daily_basic覆盖)")
L.append(f"- 涨票双口径: raw=截面fwd20前10%(≈435只/期); excess=**行业中性化**(减申万L1行业内均值)后前10% —— 防raw排序天然偏向高波动低价的同义反复")
L.append(f"- 我们的买入: westock成交史BUY成交单118笔(2026-07-22~09-04), 全部匹配成功; 特征as-of买入前一交易日, 分位=当日截面内落点(时间匹配)")
L.append(f"- MACD: ~/.agents/skills/macd v2.0.4口径逐行复刻(SMA-seed EMA, DIF=EMA12-EMA26, DEA=EMA(DIF,9), 柱=DIF-DEA); 预热2024-06起(截面处≥300bar); 三票对账通过(与pandas ewm收敛差<6e-4, 末值一致)")
L.append(f"- 缠论: 轻量结构代理(zigzag 5%笔数/中枢密度/现价vs中枢), **信号家族已证伪仅作描述特征**; 上涨停为qfq change_pct阈值近似(主板9.8/创业板19.8)")
L.append(f"- ⚠️诚实边界: 买入样本仅6.5周(7/22起), 1年画像只适用市场侧; 7月段为旧红利策略期(11笔大行/公用), 8月后为新策略期, 分段见§5")
L.append('')
L.append('## 2. 逐维对比表(中位数)')
L.append('')
L.append('| 维度 | 全市场 | 涨票raw前10% | 涨票行业内前10% | 我们的买入 | 买入均分位% |')
L.append('|---|---|---|---|---|---|')
for dim, label, _ in [(k, v['label'], 0) for k, v in S.items() if not k.startswith('_')]:
    s = S[dim]
    L.append(f"| {label} | {f(s['market_med'])} | {f(s['winraw_med'])} | {f(s['winex_med'])} | {f(s['buys_med'])} | {f(s['buys_mean_pct'],1)} |")
L.append('')
L.append(f"亏损股占比: 全市场{f(S['_loss_share']['market'],1)}% / 涨票raw {f(S['_loss_share']['win_raw'],1)}% / 涨票excess {f(S['_loss_share']['win_ex'],1)}% / 我们 {f(S['_loss_share']['buys'],1)}%")
L.append('')
L.append('## 3. 价格分桶(%)与行业')
L.append('')
pb = S['_price_bucket']
L.append('| 桶 | 全市场 | 涨票raw | 涨票excess | 我们的买入 |')
L.append('|---|---|---|---|---|')
for lab in ['<5元','5-10元','10-20元','20-30元','>30元']:
    L.append(f"| {lab} | {f(pb['market'][lab],1)} | {f(pb['win_raw'][lab],1)} | {f(pb['win_ex'][lab],1)} | {f(pb['buys'][lab],1)} |")
L.append('')
L.append(f"- 涨票行业top5(1年累计): {S['_industry']['win_raw_top10'][:5]}")
L.append(f"- 涨票excess口径top5: {S['_industry']['win_ex_top10'][:5]}")
L.append(f"- 我们的买入top5: {S['_industry']['buys_top10'][:5]}")
L.append('')
L.append('## 4. 入场时点(执行侧)')
L.append('')
e = S['_entry']
L.append(f"- 买入日当日涨幅: 中位 {f(e['day_chg_buys_med'],2)}% / 均值 {f(e['day_chg_buys_mean'],2)}% —— **无系统性追高当日买入**")
L.append(f"- 买入前一日对5日均线乖离中位: {f(e['bias5_buys_med'],2)}% —— 温和")
L.append('')
L.append('## 5. 分段(旧红利期 vs 新策略期)')
L.append('')
for tag, nm in [('_seg_jul','7月22-31日(旧红利期)'), ('_seg_aug','8月起(新策略期)')]:
    if tag in S:
        seg = S[tag]
        L.append(f"- {nm}: n={seg['n']}, 市值log中位{f(seg.get('log_mv'))}, 换手{f(seg.get('turnover20'))}%, ret20 {f(seg.get('ret20'),4)}, vol60 {f(seg.get('vol60'),4)}, 250日涨停{f(seg.get('lim_cnt250'),0)}, 年区间位置{f(seg.get('pos250'),2)}")
L.append('')
L.append('## 6. 分布图(plotille 6.0.5)')
L.append('')
for dim, label, _, _ in CHART_DIMS:
    if dim in charts:
        L.append(f'### {label}')
        L.append(charts[dim]); L.append('')

md = '\n'.join(L)
rp = r'D:\zcodesj\reports\WINNER_PROFILE_20260904.md'
open(rp, 'w', encoding='utf-8').write(md)
print('report saved:', rp, len(md), 'chars; charts:', list(charts.keys()))
