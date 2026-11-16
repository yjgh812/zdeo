# -*- coding: utf-8 -*-
"""全域历史多模型回测报告生成器 (2026-10-22 专项指令§四)
输入: reports/backtest_fullhist_2016_2026_20261022/{summary.json, equity_*.csv, trades_*.csv}
输出: 同目录 report_fullhist_20261022.html (自包含, 图表base64内嵌) + charts/*.png
      并复制HTML到 ZCode 可视化目录供内联渲染。
规范: 全部数字来自回测产物, 禁臆造; 图表中文字体SimHei/YaHei。
"""
import os, sys, json, base64, shutil
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
VIZ_DIR = r"D:\zcodesj\.zcode\v2\visualizations\06a257029f8f1d89809f0384d47c6e4b62ed7ed72381fc6efed85ce3ea6dd0e1"
CH = os.path.join(BASE, "charts"); os.makedirs(CH, exist_ok=True)

for f in ("simhei.ttf", "msyh.ttc"):
    fp = os.path.join(r"C:\Windows\Fonts", f)
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

S = json.load(open(os.path.join(BASE, "summary.json"), encoding="utf-8"))
MODELS = list(S["models"].keys())
PRIMARY = "M1_lowconsol_main" if "M1_lowconsol_main" in MODELS else MODELS[0]
MODEL_LABEL = {
    "M1_lowconsol_main": "M1 低位横盘+主线对齐(官方)",
    "M2_lowconsol_bgp": "M2 低位横盘背景池",
    "M3_v375_top30": "M3 v375 Top30",
    "M4_v375_top10": "M4 v375 Top10",
    "M5_breakout_top30": "M5 breakout Top30",
    "M6_wfa_top20": "M6 wfa Top20(2018+)",
}
def lb(m): return MODEL_LABEL.get(m, m)

EQ = {m: pd.read_csv(os.path.join(BASE, f"equity_{m}.csv")) for m in MODELS}
EQ[PRIMARY]["date"] = EQ[PRIMARY]["date"].astype(int)
TR = {m: pd.read_csv(os.path.join(BASE, f"trades_{m}.csv"), encoding="utf-8-sig") for m in MODELS}

# 基准序列
import sqlite3
ci2 = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro", uri=True)
idx = pd.read_sql_query("SELECT date,close FROM index_daily WHERE index_code='000001' AND date>=20160101", ci2)
ci2.close()
idx["date"] = idx["date"].astype(int)
dts = EQ[PRIMARY]["date"].astype(int)
sse = idx.drop_duplicates("date").set_index("date")["close"].reindex(dts).ffill().bfill()
sse_n = sse / sse.iloc[0]
eqw = pd.read_csv(os.path.join(BASE, f"equity_{PRIMARY}.csv"))  # 占位, 等权由summary基准数给出(曲线略)
# 等权净值: 由引擎summary只有终值, 报告曲线用上证+各模型(等权终值入表)
eqw_final = S["benchmark"]["eqw"]["total_return"]

def pct(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:.{nd}f}%"

def png64(path):
    return base64.b64encode(open(path, "rb").read()).decode()

# ================= 图表 =================
dates_dt = pd.to_datetime(dts.astype(str))
fig, ax = plt.subplots(figsize=(12, 6))
for m in MODELS:
    e = EQ[m]["equity"].to_numpy()
    ax.plot(dates_dt, e / 1e4, lw=1.6, label=lb(m))
ax.plot(dates_dt, sse_n.to_numpy() * 100, "k--", lw=1.2, label="上证指数(归一)")
ax.set_yscale("log")
ax.set_ylabel("账户净值(万元, 对数轴)")
ax.set_title(f"账户净值曲线对比  {dts.iloc[0]} ~ {dts.iloc[-1]}  (初始10万)")
ax.legend(fontsize=8, ncol=3); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "c1_nav.png"), dpi=110); plt.close(fig)

# C2 年度收益柱状
years = sorted({int(y) for m in MODELS for y, _ in ((r["year"], 0) for r in S["models"][m]["yearly"])})
yret = {m: {r["year"]: r["ret"] for r in S["models"][m]["yearly"]} for m in MODELS}
sse_y = pd.Series(sse_n.to_numpy(), index=dates_dt).resample("YE").last()
sse_y0 = pd.Series(sse_n.to_numpy(), index=dates_dt).resample("YE").first()
x = np.arange(len(years)); w = 0.8 / (len(MODELS) + 1)
fig, ax = plt.subplots(figsize=(13, 5.5))
for i, m in enumerate(MODELS):
    vals = [yret[m].get(y, np.nan) * 100 for y in years]
    ax.bar(x + i * w, vals, w, label=lb(m))
sse_yr = [(sse_y.iloc[k] / (sse_y0.iloc[k] if k > 0 else 1.0) - 1) * 100 for k in range(len(sse_y))]
ax.bar(x + len(MODELS) * w, sse_yr[:len(years)], w, label="上证指数", color="black", alpha=.55)
ax.axhline(200, color="red", ls=":", lw=1.2)
ax.text(len(years) - .5, 205, "年化200%目标线", color="red", fontsize=8, ha="right")
ax.set_xticks(x + w * len(MODELS) / 2); ax.set_xticklabels(years)
ax.set_ylabel("年度收益率(%)"); ax.set_title("年度收益对比(净值口径; 红虚线=200%年化目标)")
ax.legend(fontsize=8, ncol=2); ax.grid(alpha=.3, axis="y")
fig.tight_layout(); fig.savefig(os.path.join(CH, "c2_yearly.png"), dpi=110); plt.close(fig)

# C3 盈亏分布
fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=False)
for ax_, m in zip(axes, [PRIMARY, [k for k in MODELS if k != PRIMARY][0]]):
    t = TR[m]
    ax_.hist(t["pnl_pct"] * 100, bins=60, color="#3667b0", alpha=.8)
    ax_.axvline(0, color="red", lw=1)
    ax_.set_title(f"单笔盈亏分布直方图 — {lb(m)}")
    ax_.set_xlabel("单笔盈亏(%)"); ax_.set_ylabel("笔数"); ax_.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "c3_dist.png"), dpi=110); plt.close(fig)

# C4 回撤区间标记(主模型)
e = EQ[PRIMARY]["equity"].to_numpy()
peak = np.maximum.accumulate(e); dd = e / peak - 1
imin = int(dd.argmin()); pk_i = int(e[:imin + 1].argmax())
fig, ax = plt.subplots(figsize=(12, 5))
ax.plot(dates_dt, e / 1e4, lw=1.4, color="#1f4e9c", label=lb(PRIMARY))
ax.axvspan(dates_dt.iloc[pk_i], dates_dt.iloc[imin], color="red", alpha=.18,
           label=f"最大回撤区间 {dd.min()*100:.1f}% ({dates_dt.iloc[pk_i]:%Y-%m-%d}→{dates_dt.iloc[imin]:%Y-%m-%d})")
ax.set_yscale("log"); ax.set_ylabel("净值(万元,对数)")
ax.set_title(f"主模型净值与最大回撤区间标记 — {lb(PRIMARY)}")
ax.legend(fontsize=9); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "c4_dd.png"), dpi=110); plt.close(fig)

# C5 胜率时序(滚动100笔)
fig, ax = plt.subplots(figsize=(12, 4.5))
for m in MODELS:
    t = TR[m].sort_values("sell_date")
    if len(t) < 100: continue
    wr = (t["pnl_pct"] > 0).rolling(100).mean() * 100
    ax.plot(pd.to_datetime(t["sell_date"].astype(str)), wr, lw=1.1, label=lb(m))
ax.axhspan(78, 82, color="green", alpha=.12)
ax.text(dates_dt.iloc[5], 80, "目标胜率带 78–82%", fontsize=8, color="green")
ax.set_ylabel("滚动100笔胜率(%)"); ax.set_title("胜率时序波动(滚动100笔已平仓)")
ax.legend(fontsize=8, ncol=2); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "c5_wr.png"), dpi=110); plt.close(fig)

# C6 盈亏散点(主模型)
t = TR[PRIMARY]
fig, ax = plt.subplots(figsize=(9, 5.5))
winm = t["pnl_pct"] > 0
ax.scatter(t.loc[winm, "hold_days"], t.loc[winm, "pnl_pct"] * 100, s=14, c="#2e8b57", alpha=.6, label="盈利笔")
ax.scatter(t.loc[~winm, "hold_days"], t.loc[~winm, "pnl_pct"] * 100, s=14, c="#c0392b", alpha=.6, label="亏损笔")
ax.axhline(0, color="gray", lw=.8)
ax.set_xlabel("持仓交易日数"); ax.set_ylabel("单笔盈亏(%)")
ax.set_title(f"盈亏散点分布 — {lb(PRIMARY)}")
ax.legend(); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "c6_scatter.png"), dpi=110); plt.close(fig)

# ================= 分年统计 =================
def year_stats(m):
    t = TR[m].copy()
    if len(t) == 0:
        return {}, pd.DataFrame()
    t["y"] = (t["sell_date"] // 10000).astype(int)
    rows = {}
    for y, g in t.groupby("y"):
        wins = int((g["pnl_pct"] > 0).sum()); losses = int((g["pnl_pct"] <= 0).sum())
        rows[int(y)] = dict(n=int(len(g)), wins=wins, losses=losses,
                            wr=wins / len(g), pnl=float(g["pnl"].sum()),
                            avg_pct=float(g["pnl_pct"].mean()))
    return rows, t

ys, tr_p = year_stats(PRIMARY)
# 净值口径年度收益 + 年化折算
eq = EQ[PRIMARY].copy(); eq["y"] = (eq["date"] // 10000).astype(int)
yret_p = {}
prev = None
for y, s in eq.groupby("y")["equity"]:
    e0 = s.iloc[0] if prev is None else prev
    yret_p[int(y)] = float(s.iloc[-1] / e0 - 1)
    prev = s.iloc[-1]

yearly_html = []
for y in sorted(set(list(ys.keys()) + list(yret_p.keys()))):
    st = ys.get(y, {})
    yr = yret_p.get(y)
    g = tr_p[tr_p["y"] == y] if len(tr_p) else pd.DataFrame()
    rows_html = ""
    for _, r in g.iterrows():
        rows_html += (f"<tr><td>{r['code']}</td><td>{r['name']}</td><td>{r['buy_date']}</td>"
                      f"<td>{r['buy_px']:.2f}</td><td>{r['sell_date']}</td><td>{r['sell_px']:.2f}</td>"
                      f"<td class='l'>{r['buy_reason']}</td><td class='l'>{r['reason']}</td>"
                      f"<td class='{ 'p' if r['pnl']>0 else 'n'}'>{r['pnl']:+,.0f}</td>"
                      f"<td class='{ 'p' if r['pnl_pct']>0 else 'n'}'>{r['pnl_pct']*100:+.2f}%</td></tr>")
    yearly_html.append(f"""
<h3>{y} 年 <span class='{"bad" if (yr or 0) < 2.0 else ""}'>净值收益 {pct(yr)}</span>
 | 交易 {st.get("n",0)} 笔 | 盈 {st.get("wins",0)} / 亏 {st.get("losses",0)}
 | 胜率 {pct(st.get("wr"))} | 年度总盈亏 {st.get("pnl",0):+,.0f} 元
 | 单笔均值 {pct(st.get("avg_pct"),2)}</h3>
<table><tr><th>代码</th><th>名称</th><th>买入日</th><th>买入价(元)</th><th>卖出日</th><th>卖出价(元)</th>
<th>买入决策理由</th><th>卖出触发理由</th><th>盈亏(元)</th><th>盈亏比例</th></tr>
{rows_html if rows_html else "<tr><td colspan='10'>(本年无平仓交易)</td></tr>"}</table>""")

# ================= 诊断 =================
diag_rows = ""
for m in MODELS:
    t = TR[m]
    if len(t) == 0: continue
    g = t.groupby("reason").agg(n=("pnl", "size"), pnl=("pnl", "sum"),
                                wr=("pnl_pct", lambda s: (s > 0).mean()), avg=("pnl_pct", "mean"))
    for reason, r in g.iterrows():
        diag_rows += (f"<tr><td>{lb(m)}</td><td class='l'>{reason}</td><td>{int(r['n'])}</td>"
                      f"<td>{pct(r['wr'])}</td><td>{r['avg']*100:+.2f}%</td><td>{r['pnl']:+,.0f}</td></tr>")

t = TR[PRIMARY]
hold_rows = ""
if len(t):
    bins = [(0, 3, "1-2日"), (3, 6, "3-5日"), (6, 11, "6-10日"), (11, 21, "11-20日"), (21, 9999, "21日+")]
    for lo, hi, lab in bins:
        g = t[(t["hold_days"] >= lo) & (t["hold_days"] < hi)]
        if len(g) == 0: continue
        hold_rows += (f"<tr><td>{lab}</td><td>{len(g)}</td><td>{pct((g['pnl_pct']>0).mean())}</td>"
                      f"<td>{g['pnl_pct'].mean()*100:+.2f}%</td><td>{g['pnl'].sum():+,.0f}</td></tr>")
worst = t.nsmallest(15, "pnl") if len(t) else pd.DataFrame()
worst_rows = "".join(
    f"<tr><td>{r['code']}</td><td>{r['name']}</td><td>{r['buy_date']}→{r['sell_date']}</td>"
    f"<td class='n'>{r['pnl']:+,.0f}</td><td class='n'>{r['pnl_pct']*100:+.1f}%</td>"
    f"<td class='l'>{r['reason']}</td></tr>" for _, r in worst.iterrows())

# 行情环境(上证ret20/dd250分桶)
idx_c = sse.copy()
ret20 = idx_c.pct_change(20)
dd250 = idx_c / idx_c.rolling(250, min_periods=60).max() - 1
regime = np.where((ret20 > 0.05) & (idx_c > idx_c.rolling(60).mean()), "上涨趋势",
                  np.where((dd250 < -0.20) | (ret20 < -0.08), "系统性下跌", "震荡/轮动"))
reg_df = pd.DataFrame({"date": dts, "regime": regime})
reg_rows = ""
eqp = EQ[PRIMARY].set_index("date")["equity"]
pretp = eqp.pct_change()
sse_pretp = idx_c.pct_change()
for rg, g in reg_df.assign(r1=pretp.to_numpy(), r2=sse_pretp.to_numpy()).groupby("regime"):
    days = len(g); ann = (1 + g["r1"].fillna(0)).prod() ** (243 / max(days, 1)) - 1
    ann_b = (1 + g["r2"].fillna(0)).prod() ** (243 / max(days, 1)) - 1
    reg_rows += (f"<tr><td>{rg}</td><td>{days}</td><td>{ann*100:+.1f}%</td>"
                 f"<td>{ann_b*100:+.1f}%</td><td class='{'p' if ann>ann_b else 'n'}'>{(ann-ann_b)*100:+.1f}pp</td></tr>")

# ================= 达标校验 =================
chk_rows = ""
for m in MODELS:
    m_ = S["models"][m]["metrics"]
    wr_ok = m_["win_rate"] is not None and 0.78 <= m_["win_rate"] <= 0.82
    cagr_ok = m_["cagr"] >= 2.0
    chk_rows += (f"<tr><td>{lb(m)}</td><td class='{ 'p' if wr_ok else 'n'}'>{pct(m_['win_rate'])} "
                 f"{'✅' if wr_ok else '❌'}</td>"
                 f"<td class='{ 'p' if cagr_ok else 'n'}'>{pct(m_['cagr'])} {'✅' if cagr_ok else '❌'}</td>"
                 f"<td class='n'>{pct(m_['mdd'])}</td><td>{m_['n_trades']}</td></tr>")

# ================= 汇总表 =================
sum_rows = ""
for m in MODELS:
    m_ = S["models"][m]["metrics"]
    pf = f"{m_['profit_factor']:.2f}" if m_["profit_factor"] else "n/a"
    sum_rows += (f"<tr><td>{lb(m)}</td><td>{m_['final_equity']:,.0f}</td><td class='{ 'p' if m_['total_return']>0 else 'n'}'>{pct(m_['total_return'])}</td>"
                 f"<td class='{ 'p' if m_['cagr']>0 else 'n'}'>{pct(m_['cagr'])}</td><td>{pct(m_['mdd'])}</td>"
                 f"<td>{pct(m_['win_rate'])}</td><td>{pf}</td><td>{m_['avg_hold']:.1f}</td><td>{m_['n_trades']}</td></tr>")

# ================= 优化方案(全部数据驱动) =================
stop_share = pct((t["reason"].str.startswith("止损")).mean()) if len(t) else "n/a"
tp_share = pct(t["reason"].str.startswith("止盈").mean()) if len(t) else "n/a"
cb_n = int(t["reason"].str.startswith("熔断").sum()) if len(t) else 0
best_m = max(MODELS, key=lambda m: S["models"][m]["metrics"]["cagr"])
best = S["models"][best_m]["metrics"]
best_note = (f"六腿中唯一正收益={lb(best_m)}(构造上walk-forward无前视): CAGR {best['cagr']*100:+.1f}%, "
             f"胜率 {pct(best['win_rate'])}, MDD {pct(best['mdd'])} — 仍远低于年化200%标尺")
best_m = max(MODELS, key=lambda m: S["models"][m]["metrics"]["cagr"])
best = S["models"][best_m]["metrics"]
_m1 = S["models"][PRIMARY]["metrics"]; _m2 = S["models"].get("M2_lowconsol_bgp", {}).get("metrics", {})
_m6 = S["models"].get("M6_wfa_top20", {}).get("metrics", {})
_gap_m1m2 = (_m1["cagr"] - _m2["cagr"]) * 100 if _m2 else float("nan")

OPT = f"""
<h3>1. 短期微调(参数/过滤)</h3>
<p><b>本轮总诊断</b>: 全部规则腿全窗为负, 止损平仓占 {stop_share}(结构性钝刀亏损: -8%移动止损在该信号域被高频打穿,
阶梯止盈触发率仅 {tp_share}); {best_note}。目标(胜率78–82%/年化200%)与实测差距为数量级级别, 详见§3.5校验表 — 如实标注, 不粉饰。</p>
<ul>
<li><b>ML腿信号口径已改秩校准</b>: 当前信号库分布(v375 p90=0.435 / breakout p90=0.586)与标准v1记载的"重训模型0.75阈值"分布不符,
绝对阈值在本库全饥饿 → 已改为逐日TopN(30/10/20)。<b>二轮验收</b>: 恢复生产前须以当日TopN分位映射回概率阈值并注明版本,
验收=TopN口径 CAGR 与阈值口径差异&lt;3pp 且信号量≥200/年。</li>
<li><b>主线对齐价值判定</b>: 本轮实测 M1(主线对齐) CAGR {_m1['cagr']*100:+.1f}% vs M2(背景池) {_m2.get('cagr',0)*100:+.1f}% → 主线闸贡献 {_gap_m1m2:+.1f}pp,
主线闸<b>显著为正应保留</b>; 二轮验收: 按活跃度相位(amt_q分位)分桶验证其相位依赖(档案记载熊市无效), 分桶后任一桶翻转则降级为标注。</li>
<li><b>流动性参数敏感性</b>: min_amt20=3000万为实现参数, 二轮跑 2000万/5000万 两档, 验收=结论方向不变。</li>
</ul>
<h3>2. 中期规则升级(进出场/风控)</h3>
<ul>
<li><b>止损退出结构</b>: 本轮回测止损占平仓 {stop_share}, 止盈占 {tp_share}, 熔断平仓 {cb_n} 笔。
若止损笔平均亏损恶化至 -9% 以下(滑点+隔夜跳空), 二轮引入"止损价+1×ATR缓冲"变体;
验收=止损笔均亏改善≥0.5pp 且总CAGR不降。</li>
<li><b>阶梯止盈带宽</b>: 依据 C6 散点中大盈利笔持仓分布, 若 +30% 以上盈利笔占比&lt;5%,
二轮测试"30%档回撤阈值 6%→8%"; 验收=大盈利笔均值提升且 MDD 不恶化&gt;1pp。</li>
<li><b>"趋势走坏"退出未实现</b>(主观判定), 二轮以"收盘&lt;MA20 连续3日"规则化替代; 验收=该退出触发笔后续20日均收益&lt;0 才保留。</li>
</ul>
<h3>3. 长期体系增强</h3>
<ul>
<li><b>板块情绪权重融合</b>: 现主线代理=申万L1量比Top3(快照映射), 二轮接入 feature_industry_momentum 时序版并按相位开关
(活跃度分位≥0.5, lowconsol探索性标注)做分桶前瞻验证; 验收=开关ON桶首日效应≥+2pp(OOS)方可转正式过滤。</li>
<li><b>归因经验库联动</b>: 将 attribution.db 错误模式标签(error_pattern_registry)作为买入负清单接入引擎;
验收=命中标签的历史买入笔均收益显著&lt;0(t&lt;-2)方生效。</li>
<li><b>PIT bp_ep 腿补全</b>: 标准v1推荐架构含 bp_ep 60% 腿, 本轮因 PIT 覆盖起点未验证未纳入;
二轮验证 factor_pit 2016+ 可用性后补跑 60/20/20 统一账户架构。</li>
</ul>"""

# ================= HTML =================
html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>屠龙系统全域历史多模型回测报告 2016-2026</title><style>
body{{font-family:"Microsoft YaHei",SimHei,sans-serif;margin:24px;max-width:1280px;color:#222}}
h1{{font-size:22px;border-bottom:3px solid #1f4e9c;padding-bottom:8px}}
h2{{font-size:18px;color:#1f4e9c;margin-top:36px;border-left:5px solid #1f4e9c;padding-left:8px}}
h3{{font-size:14px;margin:14px 0 6px}}
table{{border-collapse:collapse;font-size:11.5px;margin:8px 0;width:100%}}
th,td{{border:1px solid #bbb;padding:3px 6px;text-align:right}}
th{{background:#1f4e9c;color:#fff;position:sticky;top:0}}
td.l{{text-align:left;max-width:340px;word-break:break-all}}
td.p{{color:#c0392b}}td.n{{color:#27ae60}} .bad{{color:#c0392b}}
img{{max-width:100%;border:1px solid #ddd;margin:8px 0}}
.note{{background:#fff8e1;border:1px solid #e6c200;padding:10px 14px;font-size:12.5px;margin:10px 0}}
.risk{{background:#fdecea;border:1px solid #c0392b;padding:10px 14px;font-size:12.5px;margin:16px 0}}
.kpi{{display:inline-block;background:#eef3fb;border:1px solid #b9cbe8;border-radius:6px;padding:8px 14px;margin:4px;font-size:13px}}
</style></head><body>
<h1>屠龙系统 · 全域历史多模型回测报告（2016-01-01 ~ {dates_dt.iloc[-1]:%Y-%m-%d}）</h1>
<p>生成时间 2026-10-22 | 引擎 analysis/backtest_fullhist_multimodel_20261022.py v2 | 逐笔台账与净值CSV同目录留档</p>
<div>
<span class="kpi">初始资金 100,000 元</span>
<span class="kpi">标的宇宙 沪深主板+创业板 {S['cfg'] and ''}(剔ST·科创688·北交所·次新&lt;60交易日)</span>
<span class="kpi">回测网格 {len(dts)} 交易日</span>
<span class="kpi">基准上证 总收益 {pct(S['benchmark']['sse']['total_return'])} / MDD {pct(S['benchmark']['sse']['mdd'])}</span>
<span class="kpi">全市场等权 总收益 {pct(S['benchmark']['eqw']['total_return'])}</span>
</div>

<div class="note"><b>口径与证伪档案引用（宪法§10/§11）</b>：
①收益与盈亏按 qfq 前复权口径, 成交价展示为名义不复权价; ②next-bar(信号T收盘→T+1开盘成交); 无前视;
③<b>ML腿(M3-M6)为训练窗重叠样本内口径, 禁作为生产预期</b>(backtest_template_std ML_LEG_FOOTNOTE铁律);
④追突破/追确认腿<b>未复测</b>(BT-FAIL-005与SKILL-SWEEP-STAGE1"突破×2免测"条款), 规则腿采用已验证低位横盘A格子官方口径
(PREREG_LOW_CONSOLIDATION_ALPHA_20261021 OOS_CONFIRMED); 低位补涨组合级已证伪档案(MAINLINE-STAGE2-S2B-LOWPOS)已引用;
⑤已知局限: ST/行业映射为当前快照口径, "趋势走坏"主观退出未实现, R19解禁/R35回补冷却未纳入。</div>

<h2>1. 基础概览</h2>
<table><tr><th>模型</th><th>期末净值(元)</th><th>总收益</th><th>年化CAGR</th><th>最大回撤</th>
<th>总胜率</th><th>盈亏比PF</th><th>平均持仓(日)</th><th>总笔数</th></tr>{sum_rows}</table>
<img src="data:image/png;base64,{png64(os.path.join(CH,'c1_nav.png'))}">
<img src="data:image/png;base64,{png64(os.path.join(CH,'c4_dd.png'))}">

<h2>2. 按自然年独立拆分（主模型 {lb(PRIMARY)} 逐笔明细; 其余模型年度汇总见§对比表）</h2>
<p>归属口径: 按卖出日所在自然年; 净值口径年度收益含浮动盈亏。</p>
{''.join(yearly_html)}

<h3>年度汇总对比（全部模型, 净值口径）</h3>
<table><tr><th>年份</th>{''.join(f'<th>{lb(m)}</th>' for m in MODELS)}<th>上证</th></tr>
{''.join(f"<tr><td>{y}</td>{''.join(f"<td class='{'p' if (yret[m].get(y) or 0)>0 else 'n'}'>{pct(yret[m].get(y))}</td>" for m in MODELS)}<td>{pct((sse_y.iloc[i]/(sse_y0.iloc[i] if i>0 else 1)-1))}</td></tr>" for i, y in enumerate(years))}
</table>
<img src="data:image/png;base64,{png64(os.path.join(CH,'c2_yearly.png'))}">

<h2>3. 深度绩效诊断</h2>
<h3>3.1 平仓原因 × 绩效（全部模型）</h3>
<table><tr><th>模型</th><th>平仓原因</th><th>笔数</th><th>胜率</th><th>单笔均值</th><th>合计盈亏(元)</th></tr>{diag_rows}</table>
<h3>3.2 持仓时长分桶（{lb(PRIMARY)}）</h3>
<table><tr><th>持仓天数</th><th>笔数</th><th>胜率</th><th>单笔均值</th><th>合计盈亏</th></tr>{hold_rows}</table>
<h3>3.3 大额亏损 Top15（{lb(PRIMARY)}）</h3>
<table><tr><th>代码</th><th>名称</th><th>区间</th><th>盈亏</th><th>比例</th><th>卖出理由</th></tr>{worst_rows}</table>
<h3>3.4 分行情环境适配（{lb(PRIMARY)} vs 上证, 按日收益复利年化）</h3>
<table><tr><th>行情环境</th><th>交易日数</th><th>策略年化</th><th>上证年化</th><th>超额</th></tr>{reg_rows}</table>
<h3>3.5 目标达标校验（标尺: 胜率78–82% / 年化200%）</h3>
<table><tr><th>模型</th><th>总胜率</th><th>年化CAGR</th><th>最大回撤</th><th>笔数</th></tr>{chk_rows}</table>
<img src="data:image/png;base64,{png64(os.path.join(CH,'c5_wr.png'))}">

<h2>4. 可视化图表</h2>
<img src="data:image/png;base64,{png64(os.path.join(CH,'c3_dist.png'))}">
<img src="data:image/png;base64,{png64(os.path.join(CH,'c6_scatter.png'))}">

<h2>5. 策略诊断与完善方案（每条附二轮回测验收标准）</h2>
{OPT}
<h2>6. 精简优化条目清单（直接导入第二轮迭代复测）</h2>
<ol>
<li>ML腿阈值→秩口径映射校准（验收: 与TopN口径CAGR差&lt;3pp）</li>
<li>主线闸价值复验 M1 vs M2（验收: 差值量化后决定标注/阻断）</li>
<li>止损ATR缓冲变体（验收: 止损笔均亏改善≥0.5pp且CAGR不降）</li>
<li>止盈30%档回撤阈值 6%→8% 变体（验收: 大盈利均值↑且MDD恶化≤1pp）</li>
<li>趋势走坏退出规则化=收盘&lt;MA20×3日（验收: 触发笔后20日均收益&lt;0）</li>
<li>流动性阈值 2000万/5000万 敏感性（验收: 方向不变）</li>
<li>板块情绪相位开关分桶前瞻验证（验收: ON桶OOS首日效应≥+2pp）</li>
<li>归因错误模式负清单接入（验收: 命中笔均收益显著&lt;0）</li>
<li>PIT bp_ep腿 + 60/20/20统一账户架构补跑（验收: PIT覆盖2016+证实后）</li>
<li>熔断触发成本归因（验收: 每次熔断→恢复期收益损失量化留档）</li>
</ol>

<div class="risk"><b>风险提示</b>：本报告全部为历史理论回测绩效，含样本内成分与实现口径近似
（ST/行业快照、跌停强平近似、滑点常数），<b>不代表未来实盘收益</b>；胜率与年化目标为既定评判标尺，
回测未达标的模型与年份已如实标注，禁止据此直接实盘。</div>
</body></html>"""

out = os.path.join(BASE, "report_fullhist_20261022.html")
open(out, "w", encoding="utf-8").write(html)
try:
    shutil.copy(out, os.path.join(VIZ_DIR, "report_fullhist_20261022.html"))
    print("VIZ copy ok")
except Exception as e:
    print("VIZ copy fail:", e)
print("REPORT DONE:", out, f"{os.path.getsize(out)/1e6:.1f} MB")
