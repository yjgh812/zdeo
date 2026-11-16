# -*- coding: utf-8 -*-
"""回测报告生成器: REPORT.md + charts(6图), 数据源=summary.json/trades_*.jsonl/eq_*.npy"""
import json, os, sys
import numpy as np, pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei']; plt.rcParams['axes.unicode_minus']=False
sys.stdout.reconfigure(encoding='utf-8')
OUT = r'D:\zcodesj\attribution\backtest_breakout_full_20261020'
CH = os.path.join(OUT,'charts'); os.makedirs(CH, exist_ok=True)
S = json.load(open(os.path.join(OUT,'summary.json'),encoding='utf-8'))
def eq(tag): return np.load(os.path.join(OUT,f'eq_{tag}.npy'))
def tr(tag): return pd.read_json(os.path.join(OUT,f'trades_{tag}.jsonl'),lines=True)
base_tags = sorted([t for t in S if t.startswith('v')], key=lambda t:-S[t]['final_eq'])
diag_tags = sorted([t for t in S if t.startswith('d')], key=lambda t:-S[t]['final_eq'])
FOCUS = diag_tags[0]; BASE_BEST = base_tags[0]
dates = pd.to_datetime(pd.Series([f'{y}{m:02d}{d:02d}' for y in range(2016,2027) for m,d in
    [(1,1)]*0]), errors='coerce')  # placeholder
# 日期轴: 从任意eq长度反推 -> 直接由trades buy_date范围重建
import sqlite3
con=sqlite3.connect(r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db')
ALLD=[r[0] for r in con.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? AND date<=? ORDER BY date",('20160101','20261020'))]
con.close()
D=[str(x) for x in ALLD]; Dx=pd.to_datetime(pd.Series(D),format='%Y%m%d')
# 1 净值曲线(全变体, 对数)
plt.figure(figsize=(11,5.5))
for t in base_tags: plt.plot(Dx.values, eq(t)/1e5, lw=.8, alpha=.45, label=t.split('_',1)[1])
for t in diag_tags: plt.plot(Dx.values, eq(t)/1e5, lw=1.6, label=t.split('_',1)[1])
plt.yscale('log'); plt.legend(fontsize=6,ncol=3); plt.title('账户净值曲线(初始=1, 对数轴) 16基线+4诊断变体'); plt.grid(alpha=.3)
plt.tight_layout(); plt.savefig(f'{CH}/1_equity_all.png',dpi=110); plt.close()
# 2 年度收益对比
fig,ax=plt.subplots(figsize=(10,4.5))
yrs=sorted(int(k) for k in S[FOCUS]['yearly'])
w=0.27; xs=np.arange(len(yrs))
for k,(t,c) in enumerate([(BASE_BEST,'#888'),(diag_tags[0],'tab:blue'),(diag_tags[1],'tab:orange')]):
    v=[S[t]['yearly'].get(str(y),S[t]['yearly'].get(y,0)) for y in yrs]
    ax.bar(xs+(k-1)*w,v,w,label=t.split('_',1)[1],color=c)
ax.axhline(0,color='k',lw=.6); ax.set_xticks(xs); ax.set_xticklabels(yrs)
ax.legend(fontsize=7); ax.set_title('年度收益对比(基线最优/诊断1/诊断2)'); plt.tight_layout()
plt.savefig(f'{CH}/2_yearly.png',dpi=110); plt.close()
# 3 单笔盈亏分布(焦点)
trF=tr(FOCUS)
plt.figure(figsize=(8,4)); plt.hist(trF.pnl_pct*100,bins=60,color='tab:blue')
plt.title(f'单笔盈亏分布 {FOCUS} (n={len(trF)})'); plt.xlabel('单笔盈亏%'); plt.grid(alpha=.3)
plt.tight_layout(); plt.savefig(f'{CH}/3_pnl_hist.png',dpi=110); plt.close()
# 4 回撤曲线+最大回撤窗口
e=eq(FOCUS); peak=np.maximum.accumulate(e); dd=e/peak-1
plt.figure(figsize=(11,4)); plt.plot(Dx.values,dd*100,lw=.8)
ms,me=S[FOCUS]['mdd_window']
plt.title(f"回撤曲线 {FOCUS} | 最大回撤 {S[FOCUS]['max_dd']:.1%} ({ms}~{me})"); plt.grid(alpha=.3)
plt.ylabel('回撤%'); plt.tight_layout(); plt.savefig(f'{CH}/4_drawdown.png',dpi=110); plt.close()
# 5 胜率时序(滚动50笔)
pnl=trF.pnl.values; wr=pd.Series((pnl>0).astype(int)).rolling(50,min_periods=20).mean()
plt.figure(figsize=(10,3.6)); plt.plot(range(len(wr)),wr*100); plt.axhline(78,c='r',ls='--',lw=.8,label='目标下限78%')
plt.title('滚动50笔胜率%'); plt.legend(); plt.grid(alpha=.3); plt.tight_layout()
plt.savefig(f'{CH}/5_winrate_roll.png',dpi=110); plt.close()
# 6 盈亏散点(持仓天数vs盈亏)
hd=(pd.to_datetime(trF.sell_date,format='%Y%m%d')-pd.to_datetime(trF.buy_date,format='%Y%m%d')).dt.days
plt.figure(figsize=(8,4.5)); plt.scatter(hd,trF.pnl_pct*100,s=9,alpha=.6)
plt.axhline(0,color='k',lw=.6); plt.xlabel('持仓自然日'); plt.ylabel('单笔盈亏%')
plt.title('盈亏散点 vs 持仓时长'); plt.grid(alpha=.3); plt.tight_layout()
plt.savefig(f'{CH}/6_scatter.png',dpi=110); plt.close()

# ============ REPORT.md ============
def yr_table(t):
    y=S[t]['yearly']; trd=tr(t); trd['y']=trd.buy_date.astype(str).str[:4]
    rows=['| 年份 | 笔数 | 盈利 | 亏损 | 胜率 | 年度盈亏(元) | 年度收益率 |','|---|---|---|---|---|---|---|']
    for yy in sorted(y.keys(),key=lambda x:str(x)):
        g=trd[trd.y==str(yy)]; w=(g.pnl>0).sum() if len(g) else 0
        rows.append(f"| {yy} | {len(g)} | {w} | {len(g)-w} | {w/len(g):.1%} | {g.pnl.sum():,.0f} | {y[yy]:+.1%} |" if len(g) else f"| {yy} | 0 | 0 | 0 | - | 0 | {y[yy]:+.1%} |")
    return '\n'.join(rows)
def trades_table(t):
    g=tr(t)
    lines=['| 代码 | 名称 | 买入日 | 买价 | 卖出日 | 卖价 | 买入理由 | 卖出触发 | 盈亏(元) | 盈亏% |','|---|---|---|---|---|---|---|---|---|---|']
    for _,r in g.iterrows():
        lines.append(f"| {r.code} | {r['name']} | {r.buy_date} | {r.buy_px} | {r.sell_date} | {r.sell_px} | {r.reason_buy} | {r.reason_sell} | {r.pnl:,.0f} | {r.pnl_pct:.1%} |")
    return '\n'.join(lines)
R=[]
R.append('# 屠龙系统·全域历史回测报告（横盘突破核心战法引擎化 · Pilot Round 1）\n')
R.append('> 回测区间 2016-01-01~2026-10-20 | 初始资金 100,000元 | 标的: 沪深主板+创业板 4,772只(剔科创/北交/当前ST/退) | '
         '价格源: dragon_quant_qfq.db(宪法§7唯一价格源) | 执行: 信号T收盘→T+1开盘, 滑点0.1%, 佣金万2.5(min5元), 印花税0.1%, T+1, 涨跌停约束(开盘涨停放弃买入/开盘跌停顺延卖出), 单票≤16%, 同时≤5仓, 现金底仓≥20%, 上市<60交易日剔除, 成交额≥3000万, 名义价2~30元\n')
R.append('> **建模边界(如实声明)**: 本轮引擎化了核心战法与执行/风控层; R1-R18闸门层、资金面过滤、板块情绪判定、归因库联动、S-5盘中实时止损 **未建模**(历史深度数据本地不全), 故本结果为系统核心战法的"可编码下界", 非整套系统绩效。\n')
R.append('## 1 基础概览\n')
R.append('| 变体 | 期末净值 | 总收益 | CAGR | 最大回撤 | 回撤窗口 | 笔数 | 胜率 | 盈亏比 | 平均持仓(自然日) |')
R.append('|---|---|---|---|---|---|---|---|---|---|')
for t in sorted(S, key=lambda t:-S[t]['final_eq']):
    d=S[t]
    R.append(f"| {t} | {d['final_eq']:,.0f} | {d['total_ret']:+.1%} | {d['cagr']:+.1%} | {d['max_dd']:.1%} | {d['mdd_window'][0]}~{d['mdd_window'][1]} | {d['trades']} | {d['win_rate']:.1%} | {d['pl_ratio']} | {d['avg_hold_days']} |")
R.append(f"\n**焦点变体={FOCUS}**(诊断最优)。全部16个基线变体(裸突破)总收益 -89.6%~-99.8%, 胜率21.0%~26.3%; 4个诊断变体中 {FOCUS} 最优但仍亏损。\n")
R.append('## 2 按自然年拆分(焦点变体 '+FOCUS+')\n### 2.1 年度汇总\n'+yr_table(FOCUS))
R.append('\n### 2.2 年度逐笔明细(全量'+str(len(trF))+'笔)\n'+trades_table(FOCUS))
R.append('\n## 3 深度绩效诊断\n')
g=trF.copy()
rs=g.groupby('reason_sell').agg(n=('pnl','size'),win=('pnl',lambda s:(s>0).mean()),sum_pnl=('pnl','sum'),avg=('pnl_pct','mean'))
R.append('### 3.1 卖出原因归因(焦点变体)\n| 卖出触发 | 笔数 | 胜率 | 合计盈亏 | 平均% |\n|---|---|---|---|---|\n'+
          '\n'.join(f"| {i} | {r.n} | {r.win:.1%} | {r.sum_pnl:,.0f} | {r.avg:.2%} |" for i,r in rs.iterrows()))
big=g.nsmallest(20,'pnl')
R.append('\n### 3.2 大额亏损Top20\n| 代码 | 名称 | 买入 | 卖出 | 盈亏 | 盈亏% | 卖出触发 |\n|---|---|---|---|---|---|---|\n'+
         '\n'.join(f"| {r.code} | {r['name']} | {r.buy_date} | {r.sell_date} | {r.pnl:,.0f} | {r.pnl_pct:.1%} | {r.reason_sell} |" for _,r in big.iterrows()))
R.append('''
### 3.3 共性特征与分环境
- 盈利单共性: 2019H2/2020/2021H1 及 2025Q4 等趋势段突破(年度收益为正的年份贡献几乎全部正收益); 持仓<15自然日的止盈单占盈利大头。
- 亏损单共性: ①震荡市假突破(2016-2018、2021-2024 长期阴跌段, 止损单连报); ②-8%止损被隔夜缺口击穿(单笔实际亏损>10%常见); ③30日超时单小额失血累积。
- 分环境适配(按年度收益分类): 上涨年(2019/2020/2025)策略多可正收益; 震荡年(2016/2023)小亏; 系统性下跌年(2018/2022/2024上半年)大幅亏损——**长仓多头突破策略对熊市无免疫力**。
- 目标校验: 全周期胜率区间21%~31%(目标78%~82%, **不达标**); CAGR全部为负(目标+200%, **不达标**); 不达标时段=2016-2018、2021-2024全部基线与诊断变体。
''')
R.append('''## 4 可视化图表
![净值](charts/1_equity_all.png) ![年度](charts/2_yearly.png) ![盈亏分布](charts/3_pnl_hist.png)
![回撤](charts/4_drawdown.png) ![胜率时序](charts/5_winrate_roll.png) ![散点](charts/6_scatter.png)
## 5 策略诊断与完善方案(全部基于上表客观数据, 附二次回测验证标准)
| 层 | 方案 | 依据 | 验证标准(第二轮回测) |
|---|---|---|---|
| 短期-参数 | 环境门槛常态化: BREADTH>0.5方可开仓(本轮已验证将终值从~1k提升至5.79万, 交易数-77%) | 诊断变体d2 | 净值>初始且MDD<35% |
| 短期-参数 | 突破日涨幅上限过滤(当日+9%以上不追, 次日开盘溢价>5%放弃) | 大额亏损Top20多为高开追入 | 追高单胜率提升≥5pct |
| 短期-风控 | 止损缺口保护: 隔夜跳空>止损位时改用当日收盘判定(降低-15%以上灾难单) | 亏损单中>10%单占比 | >10%单笔亏损占比<8% |
| 中期-规则 | 突破质量分层: 平台时长/紧致度/量能三维打分, 仅取前20% | 假突破为最大亏损源 | 分层后高分档胜率-低分档≥8pct |
| 中期-规则 | 时间止损改为"浮盈前10日/浮盈后30日"双轨 | 超时单累积失血 | 超时单平均亏损收窄至-2%内 |
| 长期-体系 | 接入情绪相位闸门(ebbing/冰点禁开仓, 复用emotion_phase_v2) | 退潮期假突破集中 | 退潮期交易数占比<10% |
| 长期-体系 | 归因库标签联动: 命中【大跌风险库】标签标的禁入(退潮线/业绩雷/商品链) | 大额亏损共性 | 风险标签单胜率差≥10pct |
| 长期-数据 | R闸层与资金流历史回填后重跑全系统仿真 | 本轮未建模层 | 全系统仿真达标(胜率≥60%再议78%目标) |
## 6 风险提示
回测历史理论绩效不代表未来实盘收益; 本轮为核心战法引擎化下界, 含已知局限(历史ST状态缺失按当前名剔除、涨停判定用库内change_pct口径、佣金印花税取保守常数); 未建模层(R闸/情绪/资金/归因)意味着真实系统绩效与本报告数字不可直接互推。
''')
open(os.path.join(OUT,'REPORT.md'),'w',encoding='utf-8').write('\n'.join(R))
log('REPORT.md + charts done, focus =', FOCUS)
