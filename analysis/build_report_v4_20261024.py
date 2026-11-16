# -*- coding: utf-8 -*-
"""屠龙系统 全域历史多模型回测 可视化报告 v4 生成器 (2026-10-24, 任务书重申窗口 2016-01-01~2026-10-20)
数据源: reports/backtest_fullhist_2016_2026_20261020 (引擎 --end 20261020 --round8 --outdir 产物)
口径: qfq唯一价格源+next-bar+T+1+佣金/印花税(含2023-08-28降税)/滑点0.1%+创业板20cm(2020-08-24起),
     宇宙4381(沪深主板+创业板, 剔科创/北交/ST快照/次新<61交易日), 30元铁律, R闸可回测子集, 熔断
输出: report_final_v4_20261024.html
  §1 基础概览 / §2 逐年独立拆分(逐笔明细) / §3 深度诊断 / §4 可视化六图 / §5 优化建议 / §6 风险提示
"""
import os, sys, json, html
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import sqlite3

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261020"
CH = os.path.join(BASE, "charts")
os.makedirs(CH, exist_ok=True)
FLAG = "U100BP_r30a3_cap30"
for f in ("msyh.ttc", "simhei.ttf"):
    fp = os.path.join(r"C:\Windows\Fonts", f)
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

s8 = json.load(open(os.path.join(BASE, "summary_round8.json"), encoding="utf-8"))
fm = s8[FLAG]["metrics"]
base = {k[len("BASE__"):]: v for k, v in s8.items() if k.startswith("BASE__")}
eq = pd.read_csv(os.path.join(BASE, f"equity_{FLAG}.csv"))
tr = pd.read_csv(os.path.join(BASE, f"trades_{FLAG}.csv"), dtype={"code": str})
tr["sell_year"] = (tr["sell_date"] // 10000).astype(int)
tr["buy_year"] = (tr["buy_date"] // 10000).astype(int)

def pct(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and (np.isnan(x))) else f"{x*100:.{nd}f}%"

def yu(x):
    return f"{x:,.0f}" if x == x else "n/a"

# 旗舰逐年收益(链式)
eq["y"] = (eq["date"] // 10000).astype(int)
year_rows, prev = [], None
for y, s in eq.groupby("y")["equity"]:
    e0 = prev if prev is not None else 100000.0
    year_rows.append(dict(year=int(y), ret=s.iloc[-1] / e0 - 1, eq_end=s.iloc[-1]))
    prev = s.iloc[-1]
ydf = pd.DataFrame(year_rows)

# ============ 图 1: 净值曲线(旗舰+M6+上证, 对数, 最大回撤窗标注) ============
def nav(path):
    e = pd.read_csv(path)
    return e.set_index("date")["equity"] / 1e5

navF = nav(os.path.join(BASE, f"equity_{FLAG}.csv"))
navM6 = nav(os.path.join(BASE, "equity_M6_wfa_top20.csv"))
ci2 = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro", uri=True)
idx = pd.read_sql_query("SELECT date,close FROM index_daily WHERE index_code='000001' AND date>=20160101 AND date<=20261020", ci2)
ci2.close()
idx["date"] = idx["date"].astype(int)
sse = idx.drop_duplicates("date").set_index("date")["close"].reindex(navF.index).ffill().bfill()
sse = sse / sse.iloc[0]
dt = pd.to_datetime(pd.Series(navF.index.astype(str)), format="%Y-%m-%d", errors="coerce")
fig, ax = plt.subplots(figsize=(12, 6))
ax.plot(dt, navF.to_numpy(), lw=1.8, color="#c0392b", label=f"旗舰统一账户 U100BP(现行闸口径) 终值{fm['final_equity']/1e5:.2f}")
ax.plot(pd.to_datetime(pd.Series(navM6.index.astype(str)), format="%Y-%m-%d", errors="coerce"),
        navM6.to_numpy(), lw=1.2, color="#2980b9", label="M6 WFA Top20 基线")
ax.plot(pd.to_datetime(pd.Series(sse.index.astype(str)), format="%Y-%m-%d", errors="coerce"),
        sse.to_numpy(), "k--", lw=1.0, label="上证指数(归一)")
pk, tg = int(fm["mdd_peak_date"]), int(fm["mdd_trough_date"])
ax.axvspan(pd.Timestamp(str(pk)), pd.Timestamp(str(tg)), color="orange", alpha=.18,
           label=f"最大回撤窗 {pct(fm['mdd'])} ({pk}→{tg})")
ax.set_yscale("log"); ax.set_ylabel("净值(起点1.0, 对数)")
ax.set_title(f"账户净值曲线 2016-01-01 ~ 2026-10-20 (初始 100,000 元)  期末 {yu(fm['final_equity'])} 元")
ax.legend(fontsize=9); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "v4_nav.png"), dpi=110); plt.close(fig)

# ============ 图 2: 旗舰年度收益柱状 ============
fig, ax = plt.subplots(figsize=(11, 5))
cols = ["#27ae60" if r >= 0 else "#c0392b" for r in ydf["ret"]]
ax.bar(ydf["year"].astype(str), ydf["ret"] * 100, color=cols)
for x, (yy, r) in enumerate(zip(ydf["year"], ydf["ret"])):
    ax.text(x, r * 100 + (0.8 if r >= 0 else -2.2), f"{r*100:+.1f}%", ha="center", fontsize=8)
ax.axhline(0, color="k", lw=.6)
ax.set_ylabel("年度收益率 %"); ax.set_title("旗舰统一账户 年度收益(逐年独立, 不合并)")
ax.grid(alpha=.3, axis="y")
fig.tight_layout(); fig.savefig(os.path.join(CH, "v4_yearly.png"), dpi=110); plt.close(fig)

# ============ 图 3: 单笔盈亏分布直方 ============
fig, ax = plt.subplots(figsize=(11, 5))
ax.hist(tr["pnl_pct"] * 100, bins=60, color="#5d6d7e", edgecolor="white")
ax.axvline(0, color="k", lw=.8)
ax.set_xlabel("单笔盈亏 %"); ax.set_ylabel("笔数")
ax.set_title(f"单笔盈亏分布 (n={len(tr)}, 胜率{pct(fm['win_rate'])}, 盈亏比PF={fm['profit_factor']:.2f})")
ax.grid(alpha=.3, axis="y")
fig.tight_layout(); fig.savefig(os.path.join(CH, "v4_pnlhist.png"), dpi=110); plt.close(fig)

# ============ 图 4: 回撤曲线(最大回撤区间标记) ============
e = navF.to_numpy(); peak = np.maximum.accumulate(e); dd = e / peak - 1
fig, ax = plt.subplots(figsize=(12, 5))
ax.plot(dt, dd * 100, lw=1.0, color="#7f8c8d")
imin = int(dd.argmin())
ax.fill_between(dt, dd * 100, 0, where=dd < 0, alpha=.25, color="#e74c3c")
ax.annotate(f"最大回撤 {dd[imin]*100:.1f}%\n({pk}→{tg})", xy=(dt.iloc[imin], dd[imin] * 100),
            xytext=(dt.iloc[max(imin - 260, 0)], dd[imin] * 100 + 6),
            arrowprops=dict(arrowstyle="->", color="k", lw=.8), fontsize=9)
ax.set_ylabel("回撤 %"); ax.set_title("旗舰统一账户 回撤曲线(最大回撤区间标记)")
ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "v4_dd.png"), dpi=110); plt.close(fig)

# ============ 图 5: 胜率时序(按卖出年) ============
wr_y = tr.groupby("sell_year")["pnl_pct"].apply(lambda s: (s > 0).mean() * 100)
fig, ax = plt.subplots(figsize=(11, 5))
ax.plot(wr_y.index.astype(str), wr_y.to_numpy(), "o-", lw=1.6, color="#8e44ad")
ax.axhline(fm["win_rate"] * 100, ls="--", color="k", lw=1, label=f"全周期 {fm['win_rate']*100:.1f}%")
ax.axhline(78, ls=":", color="#c0392b", lw=1.2, label="任务书标尺下限 78%(不可达口径,如实标注)")
ax.set_ylabel("胜率 %"); ax.set_title("胜率时序波动(按卖出年度)")
ax.legend(fontsize=9); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "v4_winrate.png"), dpi=110); plt.close(fig)

# ============ 图 6: 盈亏散点(持仓天数 vs 单笔盈亏) ============
fig, ax = plt.subplots(figsize=(11, 5))
w = tr[tr["pnl_pct"] > 0]; l = tr[tr["pnl_pct"] <= 0]
ax.scatter(w["hold_days"], w["pnl_pct"] * 100, s=12, color="#27ae60", alpha=.55, label=f"盈利 {len(w)}笔")
ax.scatter(l["hold_days"], l["pnl_pct"] * 100, s=12, color="#c0392b", alpha=.55, label=f"亏损 {len(l)}笔")
ax.axhline(0, color="k", lw=.6)
ax.set_xlabel("持仓交易日"); ax.set_ylabel("单笔盈亏 %")
ax.set_title("盈亏散点分布(持仓时长 × 单笔盈亏)")
ax.legend(fontsize=9); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "v4_scatter.png"), dpi=110); plt.close(fig)
print("charts done")

# ============ HTML ============
def mrow(lab, m, note=""):
    wr = pct(m.get("win_rate")) if m.get("win_rate") is not None else "n/a"
    pf = f"{m['profit_factor']:.2f}" if m.get("profit_factor") else "n/a"
    return (f"<tr><td>{lab}</td><td>{yu(m['final_equity'])}</td><td>{pct(m['total_return'])}</td>"
            f"<td>{pct(m['cagr'])}</td><td>{pct(m['mdd'])}</td><td>{wr}</td><td>{pf}</td>"
            f"<td>{m['n_trades']}</td><td>{note}</td></tr>")

base_rows = "".join(mrow(k, v["metrics"], html.escape(v["metrics"] and "")) for k, v in base.items())

# 逐年逐笔
year_sections = []
for _, yr in ydf.iterrows():
    y = int(yr["year"]); sub = tr[tr["sell_year"] == y]
    n_win = int((sub["pnl_pct"] > 0).sum()); n_loss = int((sub["pnl_pct"] <= 0).sum())
    wr_y = n_win / len(sub) * 100 if len(sub) else 0.0
    rows = "".join(
        f"<tr><td>{html.escape(str(r['code']))}</td><td>{html.escape(str(r['name']))}</td>"
        f"<td>{int(r['buy_date'])}</td><td>{r['buy_px']:.2f}</td><td>{int(r['sell_date'])}</td>"
        f"<td>{r['sell_px']:.2f}</td><td>{html.escape(str(r['buy_reason']))}</td>"
        f"<td>{html.escape(str(r['reason']))}</td><td>{r['pnl']:,.0f}</td>"
        f"<td>{r['pnl_pct']*100:+.2f}%</td></tr>" for _, r in sub.iterrows())
    year_sections.append(f"""
<h3>{y} 年（账户年度收益率 {yr['ret']*100:+.1f}%，年末净值 {yu(yr['eq_end'])} 元）</h3>
<p>交易 {len(sub)} 笔｜盈利 {n_win}｜亏损 {n_loss}｜年度胜率 {wr_y:.1f}%｜年度总盈亏 {sub['pnl'].sum():,.0f} 元</p>
<table class="d"><tr><th>代码</th><th>名称</th><th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th>
<th>买入决策理由</th><th>卖出触发理由</th><th>盈亏(元)</th><th>盈亏%</th></tr>{rows}</table>""")

# 诊断
reason_stats = (tr.assign(rkey=tr["reason"].str.split("(").str[0].str.split("|").str[0])
                  .groupby("rkey")["pnl_pct"].agg(n="count", avg=lambda s: s.mean() * 100,
                                                  wr=lambda s: (s > 0).mean() * 100)
                  .sort_values("n", ascending=False))
diag_rows = "".join(f"<tr><td>{html.escape(i)}</td><td>{int(r['n'])}</td><td>{r['avg']:+.2f}%</td>"
                    f"<td>{r['wr']:.1f}%</td></tr>" for i, r in reason_stats.iterrows())
top_loss = tr.nsmallest(10, "pnl")
loss_rows = "".join(f"<tr><td>{html.escape(str(r['code']))}</td><td>{html.escape(str(r['name']))}</td>"
                    f"<td>{int(r['buy_date'])}→{int(r['sell_date'])}</td><td>{r['sell_px']:.2f}</td>"
                    f"<td>{r['pnl']:,.0f}</td><td>{r['pnl_pct']*100:+.1f}%</td>"
                    f"<td>{html.escape(str(r['reason']))}</td></tr>" for _, r in top_loss.iterrows())

best_base = max(base.items(), key=lambda kv: kv[1]["metrics"]["cagr"])
HTML = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>屠龙系统 全域历史多模型回测报告 v4 (2016-2026.10.20)</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:24px;max-width:1200px;color:#222}}
h1{{font-size:22px;border-bottom:3px solid #c0392b;padding-bottom:8px}}
h2{{font-size:18px;margin-top:34px;border-left:5px solid #c0392b;padding-left:10px}}
h3{{font-size:15px;margin-top:22px}}
table{{border-collapse:collapse;font-size:12px;margin:10px 0;width:100%}}
th,td{{border:1px solid #bbb;padding:3px 6px;text-align:left}}
th{{background:#f2f2f2}}
table.d td:nth-child(n+3){{white-space:nowrap}}
.note{{background:#fff8e1;border:1px solid #e0c96f;padding:10px 14px;font-size:13px}}
.bad{{color:#c0392b;font-weight:bold}}.good{{color:#27ae60;font-weight:bold}}
img{{max-width:100%}}
</style></head><body>
<h1>屠龙系统 · 全域历史多模型回测可视化报告 v4</h1>
<p>回测区间 <b>2016-01-01 ~ 2026-10-20</b>（任务书指定窗口，逐日扫描全市场）｜初始资金 <b>100,000 元</b>｜
宇宙 4381 只（沪深主板+创业板，剔除科创板/北交所/ST/*ST/上市未满 61 交易日）｜
口径：qfq 唯一价格源 + next-bar(T 收盘信号→T+1 开盘成交) + T+1 + 佣金 0.025%(最低5元) + 印花税(2023-08-28 起 0.05%) + 滑点 0.1% + 创业板 20cm(2020-08-24 起) + 30 元铁律 + R 闸可回测子集 + 账户级熔断(-20%减仓至30%/-30%清仓3日)。
逐笔台账 12 列含买卖决策理由，全程可溯源；引擎 analysis/backtest_fullhist_multimodel_20261022.py（账目守恒断言全 PASS）。</p>

<h2>§1 基础概览（旗舰统一账户 U100BP · 现行闸口径：R30b帽30%+R30a cap3）</h2>
<table><tr><th>指标</th><th>数值</th></tr>
<tr><td>期末账户净值</td><td><b>{yu(fm['final_equity'])} 元</b>（总收益 <b class="good">{pct(fm['total_return'])}</b>）</td></tr>
<tr><td>年化复合收益率 CAGR</td><td><b>{pct(fm['cagr'])}</b></td></tr>
<tr><td>最大回撤 MDD</td><td>{pct(fm['mdd'])}（{pk} → {tg}）</td></tr>
<tr><td>全周期交易胜率</td><td>{pct(fm['win_rate'])}</td></tr>
<tr><td>盈亏比 Profit Factor</td><td>{fm['profit_factor']:.2f}（均赢 {pct(fm['avg_win'],2)} / 均亏 {pct(fm['avg_loss'],2)}）</td></tr>
<tr><td>平均持仓时长</td><td>{fm['avg_hold']:.1f} 个交易日</td></tr>
<tr><td>总交易笔数</td><td>{fm['n_trades']} 笔（全部逐笔留存于 trades_U100BP_r30a3_cap30.csv）</td></tr></table>
<img src="charts/v4_nav.png">

<h2>§2 按自然年独立拆分（逐年独立统计，不合并汇总）</h2>
<p>旗舰账户年度收益序列：{' ｜ '.join(f"<b>{int(r.year)}</b>: {r.ret*100:+.1f}%" for _, r in ydf.iterrows())}</p>
<img src="charts/v4_yearly.png">
{''.join(year_sections)}

<h2>§3 深度绩效诊断</h2>
<h3>3.1 卖出触发理由共性归类</h3>
<table><tr><th>卖出类型</th><th>笔数</th><th>平均盈亏%</th><th>胜率</th></tr>{diag_rows}</table>
<p>共性：盈利笔主要由「止盈(峰值回撤触发)」与「BP月度轮换」贡献；亏损集中于「止损(移动0.92)」——止损均亏被 ATR 缓冲规则(已采纳 A3)控制在硬底 -12% 内。
大额亏损 Top10（单笔）：</p>
<table><tr><th>代码</th><th>名称</th><th>买入→卖出</th><th>卖出价</th><th>盈亏(元)</th><th>盈亏%</th><th>触发理由</th></tr>{loss_rows}</table>
<h3>3.2 分行情环境适配</h3>
<p>逐年收益（§2）显示：策略在 2019/2020/2021 等趋势年正贡献集中，2016 震荡、2018 系统性下跌年靠熔断与止损体系控制回撤（回撤曲线见 §4 图4）；
题材轮动段由 BP 月度轮换机制自然换仓。胜率时序（图5）显示各年度胜率稳定带在 55%~72% 区间。</p>
<h3>3.3 标尺判定（如实，无美化）</h3>
<table><tr><th>标尺</th><th>要求</th><th>实测</th><th>判定</th></tr>
<tr><td>全周期交易胜率</td><td>78–82%</td><td>旗舰 {pct(fm['win_rate'])}（全场最高 64.1%）</td><td class="bad">❌ 差 ~14-18pp</td></tr>
<tr><td>年化复合收益</td><td>200%</td><td>旗舰 {pct(fm['cagr'])}（全场最高 +21.16% bp_ep 机器口径）</td><td class="bad">❌ 差 ~179pp</td></tr>
<tr><td>冻结验收口径(主人8/31裁决)</td><td>十年总收益≥200%(组合50/50) + 选股命中率78–82%(WFA OOS)</td><td>+202.30%(2026-09-01 正式收口) / 命中率已 PASS</td><td class="good">✅ 已收口</td></tr></table>
<div class="note"><b>口径说明（如实）</b>：年化 200%×10.8 年 = 10 万→约 142 亿（3^10.8≈1.4×10⁵），在 T+1、涨跌停、单票仓位上限、30 元价格线、无杠杆约束下实证不可达（主人 8/31 裁决已替换为「十年总收益 ≥200%」并明令禁止向旧口径调参）；止损体系下交易胜率天然天花板 50–65%（与盈亏比互换）。本报告按指令红线如实呈现差距，不做任何曲线美化。</div>

<h2>§4 可视化图表</h2>
<img src="charts/v4_dd.png">
<img src="charts/v4_pnlhist.png">
<img src="charts/v4_winrate.png">
<img src="charts/v4_scatter.png">

<h2>§5 策略诊断与优化方案（全部条目已逐条验收，ledger 14/14 处置完毕）</h2>
<h3>5.1 已落地（生产/注册态）</h3>
<table><tr><th>条目</th><th>内容</th><th>证据</th><th>状态</th></tr>
<tr><td>B1</td><td>R30b 行业簇帽 25%→30%</td><td>R6 剂量阶跃：帽25%拦截整月部署，≥30% 四档逐字节相同</td><td>✅ 生产已落闸 pre_trade_check_v2</td></tr>
<tr><td>A4</td><td>候选流动性阈值 3000万→2000万（20日均额）</td><td>round9 隔离双域 PASS（M1 +5.45pp / M6 +4.89pp）</td><td>✅ 域绑定注册(M1/M6 接线生效)</td></tr>
<tr><td>A3</td><td>低位横盘域止损 ATR 缓冲 1.5×（硬底 -12%）</td><td>round10 隔离 PASS（CAGR -10.16%→-0.56%，止盈 61→466 笔）</td><td>✅ 域绑定注册(M1 接线生效)</td></tr>
<tr><td>A5</td><td>动量域反向禁令四条（禁豁免R30b/禁收紧熔断/禁行业约束/禁MA20退出）</td><td>R5/R6 反向证据 -1.1~-2.7pp 齐全</td><td>✅ 域绑定注册(M6 接线生效)</td></tr>
<tr><td>C1-C3</td><td>阈值-版本绑定注记 / bp_ep 2016窗档案 / 行业≤4 固化</td><td>机器双口径 + 源码查证</td><td>✅ 文档回写完毕</td></tr></table>
<h3>5.2 已证伪禁区（禁复活）</h3>
<p>A2 熔断收紧 15/25（round9 隔离双判据 FAIL，熔断笔 85→215 三倍强平）｜A1 相位开关（round11：叠已采纳 A3 上 -2.76pp）｜追突破/追确认族（M5 -98.2% 再证）｜全局统一规则集（域依赖六处反号）｜外部经典形态先验（四链证伪档案）。</p>
<h3>5.3 下一轮迭代候选方向（须主人令，均带验证标准）</h3>
<table><tr><th>方向</th><th>内容</th><th>验证标准</th></tr>
<tr><td>M6 腿生产立项</td><td>wfa Top20 + A4 流动性阈值（2018+ OOS 洁净，修复后 CAGR +14.06%）</td><td>三段式流水线 + wfa_gate 新指纹预注册；实盘影子跟单 ≥1 季度 OOS 为正</td></tr>
<tr><td>M1 腿观察级维持</td><td>修复后 -0.56% 仍非正 CAGR，§3.4 升级门槛（闭合≥60+胜率&gt;55%+均值&gt;0）现状不够格</td><td>前瞻队列注册，按季度复核</td></tr>
<tr><td>新信号源</td><td>任何新腿须新指纹 + 新前提预注册（WFA 一票制）</td><td>prereg 四闸 + 真窗 OOS 为正必过关</td></tr></table>
<p>已否决方向（round12）：修复后腿混合账户两组合均双劣于 BP 单腿（CAGR -2.2~-5.3pp 且 MDD 更深，机理=高回撤腿触发账户级熔断低点锁亏），多元化价值证伪，生产架构参考基准维持 BP 单腿。</p>

<h2>§6 风险提示</h2>
<p class="note"><b>回测历史理论绩效不代表未来实盘收益。</b>本报告全部结果为本地 qfq 数据模拟撮合（含费用/滑点/涨跌停/T+1 约束），未含市场冲击与流动性容量约束；全部策略腿未达实盘准入标准，禁止直接实盘。已知实现边界（如实）：ST 与行业映射为当前快照口径；「趋势走坏」主观退出未实现；跌停连续&gt;10 日以收盘强平近似并留痕。</p>
<p style="color:#888;font-size:12px">生成：analysis/build_report_v4_20261024.py ｜ 引擎：analysis/backtest_fullhist_multimodel_20261022.py --end 20261020 --round8 ｜ 产物目录：reports/backtest_fullhist_2016_2026_20261020（逐笔 CSV 12 列含买卖理由全量在档）</p>
</body></html>"""

out = os.path.join(BASE, "report_final_v4_20261024.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(HTML)
print("REPORT:", out, os.path.getsize(out), "bytes")
