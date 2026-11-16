# -*- coding: utf-8 -*-
"""全景总报告 v3 (2026-10-23): 全域历史多模型回测专项 七轮迭代终版收口
在 v2(五轮收口) 基础上补齐: ①R6 剂量-反应(R30b帽25%→+7.8% vs ≥30%四档逐字节相同) ②R7 R30a同日闸摩擦实测
③PD-024③ R30b 25→30 生产落闸状态 ④基线M1-M6取10/23 01:20复跑CSV现算(数据含20261022) ⑤窗口右端点=20261022如实标注
⑥指令§四全部强制图表(净值/年度柱状/盈亏分布/回撤标记/胜率时序/盈亏散点)按终版数据重绘。
数据真源: 同目录 equity_*/trades_* CSV(10/23 01:20复跑) + summary_round4/5/6/7.json + bp_ep_r2_result.json
         + dragon_quant.db index_daily(上证基准)。本脚本只读上游产物, 生成 HTML + 6 图, 不改任何回测数据。"""
import os, sys, json, base64, re, sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
VIZ_DIR = r"D:\zcodesj\.zcode\v2\visualizations\8c9f798594a169d21f76f28dc0a567237cdc766d4e23e36f0529ea83bba3d8a2"
CH = os.path.join(BASE, "charts")
FLAG = "U100BP_r30a3"   # 旗舰账户: 纯BP满仓 + R30b帽30% + R30a同日闸cap3(=现行生产闸口径)
for f in ("msyh.ttc", "simhei.ttf"):
    fp = os.path.join(r"C:\Windows\Fonts", f)
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

def pct(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))) else f"{x*100:+.{nd}f}%"

def load_eq(name):
    df = pd.read_csv(os.path.join(BASE, f"equity_{name}.csv"))
    s = df.set_index("date")["equity"].astype(float)
    return s

def years_of(idx):
    d0 = pd.Timestamp(str(idx[0])); d1 = pd.Timestamp(str(idx[-1]))
    return (d1 - d0).days / 365.25

def met_of(eq):
    e = eq.to_numpy(); pk = np.maximum.accumulate(e)
    dd = e / pk - 1.0
    i_tr = int(np.argmin(dd)); i_pk = int(np.argmax(e[:i_tr + 1]))
    yrs = years_of(eq.index)
    return dict(final=float(e[-1]), total=float(e[-1] / e[0] - 1),
                cagr=float((e[-1] / e[0]) ** (1 / max(yrs, 1e-9)) - 1),
                mdd=float(dd.min()), dd_peak=str(eq.index[i_pk]), dd_trough=str(eq.index[i_tr]),
                years=yrs)

def load_tr(name):
    return pd.read_csv(os.path.join(BASE, f"trades_{name}.csv"), encoding="utf-8-sig")

def tr_stats(tr):
    w = tr[tr["pnl"] > 0]; l = tr[tr["pnl"] <= 0]
    pf = float(w["pnl"].sum() / abs(l["pnl"].sum())) if len(l) and l["pnl"].sum() != 0 else float("inf")
    big = tr[tr["pnl_pct"] <= -0.05]
    big_share = float(big["pnl"].sum() / l["pnl"].sum()) if len(l) and l["pnl"].sum() < 0 else float("nan")
    stop_n = int(tr["reason"].astype(str).str.contains("止损").sum())
    return dict(n=len(tr), wr=float(len(w) / len(tr)), pf=pf,
                avg_win=float(w["pnl_pct"].mean()) if len(w) else float("nan"),
                avg_loss=float(l["pnl_pct"].mean()) if len(l) else float("nan"),
                med_hold=float(tr["hold_days"].median()), avg_hold=float(tr["hold_days"].mean()),
                big_n=int(len(big)), big_share=big_share, stop_n=stop_n)

def yearly_eq(eq):
    y = eq.groupby(eq.index // 10000)
    return (y.last() / y.first() - 1.0)

# ================= 数据加载(全部只读) =================
MODELS = ["M1_lowconsol_main", "M2_lowconsol_bgp", "M3_v375_top30", "M4_v375_top10",
          "M5_breakout_top30", "M6_wfa_top20"]
mlabel = {"M1_lowconsol_main": "M1 低位横盘主线", "M2_lowconsol_bgp": "M2 低位横盘BGP",
          "M3_v375_top30": "M3 V375 Top30", "M4_v375_top10": "M4 V375 Top10",
          "M5_breakout_top30": "M5 横盘突破Top30", "M6_wfa_top20": "M6 WFA动量Top20"}
eqs = {m: load_eq(m) for m in MODELS}
eqs[FLAG] = load_eq(FLAG)
tr_flag = load_tr(FLAG)
trs = {m: load_tr(m) for m in MODELS}

R4 = json.load(open(os.path.join(BASE, "summary_round4.json"), encoding="utf-8"))
R5 = json.load(open(os.path.join(BASE, "summary_round5.json"), encoding="utf-8"))
R6 = json.load(open(os.path.join(BASE, "summary_round6.json"), encoding="utf-8"))
R7 = json.load(open(os.path.join(BASE, "summary_round7.json"), encoding="utf-8"))
BPM = json.load(open(os.path.join(BASE, "bp_ep_r2_result.json"), encoding="utf-8"))

ci = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro", uri=True)
idx = pd.read_sql_query("SELECT date,close FROM index_daily WHERE index_code='000001' AND date>=20160101 AND date<=20261022", ci)
ci.close()
idx["date"] = idx["date"].astype(int)
sse = idx.drop_duplicates("date").set_index("date")["close"].sort_index()
sse = sse.reindex(eqs[FLAG].index).ffill().bfill()
data_end = int(eqs[FLAG].index[-1])

# ================= 图表(6张, 指令§四.4清单) =================
def dtAxis(idx_):
    return pd.to_datetime(pd.Series(idx_.astype(str)), format="%Y%m%d", errors="coerce")

# c1 净值时序
fig, ax = plt.subplots(figsize=(12, 6))
for lab, key, c in ((f"旗舰统一账户 {FLAG}(现行闸口径)", FLAG, "#c0392b"),
                    ("M6 WFA动量Top20", "M6_wfa_top20", "#1f4e9c"),
                    ("M1 低位横盘主线", "M1_lowconsol_main", "#7f8c8d")):
    s = eqs[key] / 1e5
    ax.plot(dtAxis(s.index), s.to_numpy(), lw=1.7, color=c, label=lab)
sn = sse / sse.iloc[0]
ax.plot(dtAxis(sn.index), sn.to_numpy(), "k--", lw=1.1, label="上证指数(归一)")
ax.set_yscale("log"); ax.set_ylabel("净值(起点1.0, 对数)")
ax.set_title(f"账户净值时序曲线 2016-01-04 ~ {data_end} (初始10万, qfq收益口径)")
ax.legend(fontsize=9); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "v3_c1_nav.png"), dpi=110); plt.close(fig)

# c2 年度收益对比柱状
yr_f = yearly_eq(eqs[FLAG]); yr_m6 = yearly_eq(eqs["M6_wfa_top20"])
yr_s = sse.groupby(sse.index // 10000).last() / sse.groupby(sse.index // 10000).first() - 1.0
yrs = sorted(set(yr_f.index) | set(yr_s.index))
x = np.arange(len(yrs)); w = 0.27
fig, ax = plt.subplots(figsize=(12, 5))
ax.bar(x - w, [yr_f.get(y, np.nan) * 100 for y in yrs], w, label="旗舰统一账户", color="#c0392b")
ax.bar(x, [yr_m6.get(y, np.nan) * 100 for y in yrs], w, label="M6 WFA动量Top20", color="#1f4e9c")
ax.bar(x + w, [yr_s.get(y, np.nan) * 100 for y in yrs], w, label="上证指数", color="#95a5a6")
ax.set_xticks(x); ax.set_xticklabels(yrs); ax.axhline(0, color="k", lw=.8)
ax.set_ylabel("年度收益率(%)"); ax.set_title("年度收益对比(旗舰账户 vs M6 vs 上证)")
ax.legend(fontsize=9); ax.grid(alpha=.3, axis="y")
fig.tight_layout(); fig.savefig(os.path.join(CH, "v3_c2_yearly.png"), dpi=110); plt.close(fig)

# c3 单笔盈亏分布直方图
fig, ax = plt.subplots(figsize=(10, 4.5))
ax.hist(tr_flag["pnl_pct"] * 100, bins=60, color="#c0392b", alpha=.85)
ax.axvline(0, color="k", lw=.8)
ax.set_xlabel("单笔盈亏(%)"); ax.set_ylabel("笔数")
ax.set_title(f"旗舰账户单笔盈亏分布 (n={len(tr_flag)})")
ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "v3_c3_dist.png"), dpi=110); plt.close(fig)

# c4 最大回撤区间标记
m = met_of(eqs[FLAG])
e = eqs[FLAG].to_numpy(); pk = np.maximum.accumulate(e); dd = e / pk - 1
fig, ax = plt.subplots(figsize=(12, 4.5))
ax.plot(dtAxis(eqs[FLAG].index), dd * 100, lw=1.2, color="#1f4e9c")
i_tr = int(np.argmin(dd)); i_pk = int(np.argmax(e[:i_tr + 1]))
ax.axvspan(dtAxis(pd.Series([eqs[FLAG].index[i_pk]])).iloc[0], dtAxis(pd.Series([eqs[FLAG].index[i_tr]])).iloc[0],
           color="#c0392b", alpha=.18)
ax.annotate(f"最大回撤 {dd.min()*100:.1f}%\n{eqs[FLAG].index[i_pk]} → {eqs[FLAG].index[i_tr]}",
            xy=(dtAxis(pd.Series([eqs[FLAG].index[i_tr]])).iloc[0], dd.min() * 100),
            xytext=(10, -10), textcoords="offset points", fontsize=9, color="#c0392b")
ax.set_ylabel("回撤(%)"); ax.set_title("旗舰账户回撤曲线与最大回撤区间标记")
ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "v3_c4_dd.png"), dpi=110); plt.close(fig)

# c5 胜率时序(滚动50笔)
roll = tr_flag["pnl"].gt(0).rolling(50).mean() * 100
fig, ax = plt.subplots(figsize=(12, 4))
ax.plot(pd.to_datetime(pd.Series(tr_flag["sell_date"].astype(str)), format="%Y%m%d", errors="coerce"),
        roll, lw=1.1, color="#8e44ad")
ax.axhline(50, color="k", lw=.8, ls="--")
ax.set_ylabel("滚动50笔胜率(%)"); ax.set_title("旗舰账户胜率时序波动(滚动50笔)")
ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "v3_c5_wr.png"), dpi=110); plt.close(fig)

# c6 盈亏散点(持仓天数×单笔盈亏)
fig, ax = plt.subplots(figsize=(10, 4.5))
cl = np.where(tr_flag["pnl"] > 0, "#c0392b", "#27ae60")
ax.scatter(tr_flag["hold_days"], tr_flag["pnl_pct"] * 100, s=8, c=cl, alpha=.55)
ax.axhline(0, color="k", lw=.8)
ax.set_xlabel("持仓交易日数"); ax.set_ylabel("单笔盈亏(%)")
ax.set_title("盈亏散点分布(红=盈利 绿=亏损)")
ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "v3_c6_scatter.png"), dpi=110); plt.close(fig)

# ================= 深度诊断统计 =================
st = tr_stats(tr_flag)
# 分行情环境(上证120日滚动三分法, 与9/1深度诊断同法)
r120 = sse.pct_change(120)
regime = pd.cut(r120, [-9, -0.10, 0.10, 9], labels=["系统性下跌", "震荡市", "上涨趋势"])
tr_flag["_reg"] = tr_flag["buy_date"].map(regime)
reg_tab = tr_flag.dropna(subset=["_reg"]).groupby("_reg", observed=True).agg(
    n=("pnl", "size"), wr=("pnl", lambda s: (s > 0).mean()), avg=("pnl_pct", "mean"), sum_pnl=("pnl", "sum"))
yr_rows = ""
for y in yrs:
    seg = tr_flag[tr_flag["sell_date"] // 10000 == y]
    w_ = int((seg["pnl"] > 0).sum()); n_ = len(seg)
    yr_rows += (f"<tr><td>{y}</td><td>{n_}</td><td>{w_}</td><td>{n_ - w_}</td>"
                f"<td>{(w_ / n_ * 100 if n_ else float('nan')):.1f}%</td>"
                f"<td>{seg['pnl'].sum():+,.0f}</td><td class='{'p' if yr_f.get(y, 0) > 0 else 'n'}'>{pct(yr_f.get(y, np.nan))}</td>"
                f"<td>{pct(yr_s.get(y, np.nan))}</td></tr>")

# ================= 基线六模型终版表(10/23 01:20复跑CSV现算) =================
mrows = ""
for mk in MODELS:
    mm = met_of(eqs[mk]); ts = tr_stats(trs[mk])
    mrows += (f"<tr><td class='l'>{mlabel[mk]}</td><td>{mm['final']:,.0f}</td>"
              f"<td class='{'p' if mm['total'] > 0 else 'n'}'>{pct(mm['total'])}</td>"
              f"<td class='{'p' if mm['cagr'] > 0 else 'n'}'>{pct(mm['cagr'])}</td><td>{pct(mm['mdd'])}</td>"
              f"<td>{ts['wr']*100:.1f}%</td><td>{ts['pf']:.2f}</td><td>{ts['n']}</td><td>{ts['avg_hold']:.0f}天</td></tr>")
mflag = met_of(eqs[FLAG]); sflag = tr_stats(tr_flag)
mrows += (f"<tr><td class='l'><b>旗舰统一账户 {FLAG}(现行闸口径)</b></td><td>{mflag['final']:,.0f}</td>"
          f"<td class='p'><b>{pct(mflag['total'])}</b></td><td class='p'><b>{pct(mflag['cagr'])}</b></td>"
          f"<td>{pct(mflag['mdd'])}</td><td><b>{sflag['wr']*100:.1f}%</b></td><td><b>{sflag['pf']:.2f}</b></td>"
          f"<td><b>{sflag['n']}</b></td><td><b>{sflag['avg_hold']:.0f}天</b></td></tr>")

def jrow(lab, m, note=""):
    wr = f"{m['win_rate']*100:.1f}%" if m.get("win_rate") is not None else "n/a"
    return (f"<tr><td class='l'>{lab}</td><td>{m['final_equity']:,.0f}</td>"
            f"<td class='{'p' if m['total_return'] > 0 else 'n'}'>{pct(m['total_return'])}</td>"
            f"<td class='{'p' if m['cagr'] > 0 else 'n'}'>{pct(m['cagr'])}</td><td>{pct(m['mdd'])}</td>"
            f"<td>{wr}</td><td class='l'>{note}</td></tr>")

arch = ""
arch += jrow("bp_ep官方机器C腿(qfq, 真实约束)", dict(final_equity=BPM["C-真实约束"]["final"], total_return=BPM["C-真实约束"]["final"] / 1e5 - 1,
           cagr=BPM["C-真实约束"]["ann"], mdd=BPM["C-真实约束"]["mdd"], win_rate=None),
           f"Sharpe {BPM['C-真实约束']['sharpe']:.2f}; 系统内最强真实约束口径(官方multifactor管线)")
arch += jrow("机器分解: A1纯因子", dict(final_equity=BPM["A1-纯因子"]["final"], total_return=BPM["A1-纯因子"]["final"] / 1e5 - 1,
           cagr=BPM["A1-纯因子"]["ann"], mdd=BPM["A1-纯因子"]["mdd"], win_rate=None), "行业约束前基线")
arch += jrow("机器分解: A2 +行业≤4", dict(final_equity=BPM["A2-+行业≤4"]["final"], total_return=BPM["A2-+行业≤4"]["final"] / 1e5 - 1,
           cagr=BPM["A2-+行业≤4"]["ann"], mdd=BPM["A2-+行业≤4"]["mdd"], win_rate=None), "行业约束贡献≈+8.4pp/年=核心增益")
arch += jrow("引擎BP腿·受旧闸帽25%(U100BP)", R4["U100BP"]["metrics"], "闸门摩擦-6.1pp/年(R5豁免A/B实测)")
arch += jrow("引擎BP腿·帽25%豁免(R5U100BPfree)", R5["R5U100BPfree"]["metrics"], "胜率全场最高64.1%")
arch += jrow("引擎BP腿·帽30%(R6cap30, 与35/50/∞逐字节同)", R6["U100BP_R6cap30"]["metrics"], "R30b帽≥30%完全恢复, 剂量-反应因果闭合")
arch += jrow(f"旗舰: 帽30%+R30a闸cap3({FLAG})", R7[FLAG]["metrics"], "=现行生产闸口径, 摩擦仅0.2~0.3pp/年(R7)")
arch += jrow("统一账户60/20/20·豁免(R5U602020free)", R5["R5U602020free"]["metrics"], "BP60+M6Top50·20+M1相位栈·20")
arch += jrow("基准: 上证指数", dict(final_equity=float("nan"), total_return=float(sse.iloc[-1] / sse.iloc[0] - 1),
           cagr=float((sse.iloc[-1] / sse.iloc[0]) ** (1 / years_of(sse.index)) - 1),
           mdd=float((sse / sse.cummax() - 1).min()), win_rate=None), "归一对比")
arch = arch.replace("<td>nan</td>", "<td>—</td>")

reg_rows = ""
for k, r in reg_tab.iterrows():
    reg_rows += f"<tr><td class='l'>{k}</td><td>{int(r['n'])}</td><td>{r['wr']*100:.1f}%</td><td>{r['avg']*100:+.2f}%</td><td>{r['sum_pnl']:+,.0f}</td></tr>"

# ================= 优化清单嵌入(md轻量转换) =================
def md2html(txt):
    out, in_tbl = [], False
    for ln in txt.splitlines():
        s = ln.rstrip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if not in_tbl:
                out.append("<table>"); in_tbl = True
                out.append("<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td class='l'>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_tbl:
            out.append("</table>"); in_tbl = False
        if s.startswith("### "):
            out.append(f"<h3>{s[4:]}</h3>")
        elif s.startswith("## "):
            out.append(f"<h4 style='margin:14px 0 4px'>{s[3:]}</h4>")
        elif s.startswith("# "):
            continue
        elif s.startswith(">"):
            out.append(f"<div class='note'>{s.lstrip('> ')}</div>")
        elif s.startswith("*") and s.endswith("*") and len(s) > 2:
            out.append(f"<i>{s.strip('*')}</i>")
        elif s:
            out.append(f"<p class='l'>{s}</p>")
    if in_tbl:
        out.append("</table>")
    return "\n".join(out)

checklist_md = open(os.path.join(BASE, "优化条目清单_终版_20261022.md"), encoding="utf-8").read()
checklist_html = md2html(checklist_md)

b64 = lambda p: base64.b64encode(open(p, "rb").read()).decode()
html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>全域历史回测专项 · 终版总报告 v3 (20261023)</title>
<style>body{{font-family:"Microsoft YaHei",SimHei,sans-serif;margin:24px;max-width:1240px;color:#222;font-size:13px}}
h1{{font-size:21px;border-bottom:3px solid #1f4e9c;padding-bottom:8px}}h2{{font-size:16px;color:#1f4e9c;border-left:5px solid #1f4e9c;padding-left:8px;margin-top:30px}}
table{{border-collapse:collapse;font-size:12px;margin:8px 0;width:100%}}th,td{{border:1px solid #bbb;padding:3px 6px;text-align:right}}
th{{background:#1f4e9c;color:#fff}}td.p{{color:#c0392b}}td.n{{color:#27ae60}}td.l{{text-align:left}}
.note{{background:#fff8e1;border:1px solid #e6c200;padding:8px 12px;font-size:12px;margin:8px 0}}
.find{{background:#fdecea;border:1px solid #c0392b;padding:10px 14px;font-size:13px;margin:10px 0}}
img{{max-width:100%;margin:6px 0}}</style></head><body>
<h1>屠龙系统 · 全域历史多模型回测专项 · 终版总报告 v3（2016-01-01 ~ 2026-10-22 收盘, 七轮迭代收口）</h1>
<p><b>执行口径</b>: 初始资金 100,000 元 | qfq 唯一收益价格源 + raw 交叉源(30元铁律/涨跌停判定/成交价展示) | next-bar(信号T收盘→成交T+1开盘) | 佣金0.025%(最低5元) + 印花税(0.1%, 2023-08-28后0.05%) + 滑点0.1% | T+1 | 创业板2020-08-24后20cm | 宇宙=沪深主板+创业板, 剔科创/北交/ST快照/上市&lt;61交易日 | 每轮账目守恒断言 PASS。<b>数据右端点=20261022 收盘</b>(20261023 当日数据尚未入本地数据链, 不做前视假设; 指令窗口右端 2026-10-23 于此如实收敛)。</p>
<div class="find"><b>标尺判定(如实, 无美化)</b>: ①胜率标尺 78-82% —— 全场最高 <b>64.1%</b>(引擎BP腿豁免口径; 旗舰现行闸口径 {sflag['wr']*100:.1f}%), <b>差约 14-18pp ❌ 未达</b>。②年化复合 200% 标尺 —— 全场最高 <b>+21.16%</b>(bp_ep官方机器qfq口径), <b>差约 179pp ❌ 未达</b>; 该口径已被主人 2026-08-31 裁决认定为实证不可达并冻结替代(见 §4)。③八轮方向: 六腿五亏 → 旗舰 +{mflag['total']*100:.0f}%/CAGR {pct(mflag['cagr'])}/MDD {pct(mflag['mdd'])}, 收敛方向真实, 但<b>任何腿均未达实盘准入标准, 全部结论禁止直接实盘</b>。</div>

<h2>§0 口径边界与如实声明</h2>
<ul>
<li><b>宇宙实测</b>: 回测网格 2622 交易日 × 4381 标的(指令要求 4500+; 差额=剔除科创/北交/ST快照/次新后的合规宇宙真实规模, 非数据缺失)。</li>
<li><b>已知实现边界(引擎如实标注)</b>: ST 与行业映射为当前快照口径(未做逐日历史 ST 状态回放); "趋势走坏"主观退出未实现(仅规则化近似); R19解禁/R35回补冷却未纳入; 跌停连续&gt;10日以收盘强平近似并留痕。</li>
<li><b>ML 腿诚实缺口</b>: 信号库覆盖自 2020-01, 2016-2019 无 ML 信号; 禁止用当前模型回填早年(构成 lookahead, 唯一合法路径=逐期 PIT 滚动重训)。ML 历史数字一律带"样本内口径"注脚。</li>
<li><b>证伪档案引用</b>: 追突破/追确认族(BT-FAIL-005 + SKILL-SWEEP-STAGE1 免测条款)、低位补涨组合级(MAINLINE-STAGE2-S2B-LOWPOS)均已证伪, 本引擎不含独立追突破腿(M5 为对照复证腿, 结果 -98.2% 再证)。</li>
<li><b>基线数值版本</b>: §1 六模型指标取 10/23 01:20 复跑 equity/trades CSV 现算(含 20261022 交易日); summary.json 为 10/22 03:31 旧构建(数据止 20261021), 两者差异=数据延伸一日, 非规则变更。</li>
</ul>

<h2>§1 基础概览(终版)</h2>
<h3>1.1 六模型基线 + 旗舰统一账户(2016-01-04 ~ {data_end})</h3>
<table><tr><th>模型</th><th>期末(元)</th><th>总收益</th><th>CAGR</th><th>MDD</th><th>胜率</th><th>盈亏比PF</th><th>笔数</th><th>均持仓</th></tr>{mrows}</table>
<p class="l">盈亏比与持仓: 旗舰 PF={sflag['pf']:.2f} (均盈 {pct(sflag['avg_win'],2)} / 均亏 {pct(sflag['avg_loss'],2)}), 中位持仓 {sflag['med_hold']:.0f} 个交易日; M6 PF见上表。</p>
<h3>1.2 关键账户全景(七轮全部实测, 无一虚构)</h3>
<table><tr><th>账户/腿</th><th>期末(元)</th><th>总收益</th><th>CAGR</th><th>MDD</th><th>胜率</th><th>说明</th></tr>{arch}</table>
<img src="data:image/png;base64,{b64(os.path.join(CH, 'v3_c1_nav.png'))}">
<div class="note">解读: 引擎执行层 BP 腿(+{pct(R7[FLAG]['metrics']['cagr'])}/年) 与官方机器 BP(+21.16%/年) 存在 {21.16-R7[FLAG]['metrics']['cagr']*100:.1f}pp/年口径差(执行层摩擦+宇宙过滤+权重体系差异); R30b 帽 25%→30% 是该缺口中唯一已定谳并落闸的成分(-6.0pp/年闸门摩擦)。</div>

<h2>§2 按自然年独立拆分(旗舰统一账户 {FLAG})</h2>
<table><tr><th>年度</th><th>平仓笔数</th><th>盈利笔</th><th>亏损笔</th><th>年度胜率</th><th>年度盈亏(元)</th><th>年度收益率</th><th>上证同期的收益</th></tr>{yr_rows}</table>
<img src="data:image/png;base64,{b64(os.path.join(CH, 'v3_c2_yearly.png'))}">
<p class="l"><b>逐笔明细溯源</b>: 每笔交易永久留痕于 <code>trades_*.csv</code>(12列: code/name/leg/买卖日期/买卖价/pnl/pnl_pct/持仓天数/卖出触发理由/买入决策理由), 旗舰与六模型全量在档; 六模型年度逐笔表另见一轮报告 report_fullhist_20261022.html。卖出触发理由含 月度轮换/止损/止盈/熔断 等结构化标签。</p>

<h2>§3 深度绩效诊断</h2>
<h3>3.1 盈利/亏损共性(旗舰账户实测)</h3>
<ul>
<li>盈利笔均 {pct(sflag['avg_win'],2)}、亏损笔均 {pct(sflag['avg_loss'],2)}, PF {sflag['pf']:.2f}; 胜率 {sflag['wr']*100:.1f}% 依赖"月度轮换长持兑现"结构(与 9/1 深度诊断同构: 盈利共性=长持+调仓兑现)。</li>
<li><b>大额亏损集中</b>: 单笔 ≤-5% 共 {sflag['big_n']} 笔, 贡献亏损金额 {sflag['big_share']*100:.1f}%; 止损触发 {sflag['stop_n']} 笔(-8%硬止损带在跳空+次bar开盘滑损下实际亏损常深于-8%, 与9/1诊断"均值-11.02%"同因)。</li>
<li>亏损高频诱因=价值陷阱缓跌(宽帽月度轮换骑跌)与系统性下跌段低位接刀; 追高族(M5对照腿 -98.2%)与低位横盘裸信号(M1 -67.6%)为独立负选择族, 与证伪档案互证。</li>
</ul>
<h3>3.2 分行情环境适配(上证120日滚动三分法, 按买入日归属)</h3>
<table><tr><th>行情环境</th><th>笔数</th><th>胜率</th><th>单笔均值</th><th>盈亏合计(元)</th></tr>{reg_rows}</table>
<h3>3.3 达标校验与失效时段</h3>
<ul>
<li>胜率/年化两标尺均未达(见顶部判定); <b>绩效不达标为全周期性质而非局部时段</b>, 但相对弱时段明确: 2018(系统性下跌, 旗舰MDD谷底 {mflag['dd_peak']}→{mflag['dd_trough']}), 2026-06 段胜率塌陷(滚动50笔最低至个位数, 与9/1诊断一致)。</li>
<li>风格失效周期: M1 低位横盘族在 2018/2022-2024 深亏(MDD -79.9%基线/-45.5%相位后); M6 动量族 2021 后衰减; bp_ep 价值族 2019-2021 与 2024-2026 为主要贡献年。</li>
</ul>
<img src="data:image/png;base64,{b64(os.path.join(CH, 'v3_c3_dist.png'))}">
<img src="data:image/png;base64,{b64(os.path.join(CH, 'v3_c4_dd.png'))}">
<img src="data:image/png;base64,{b64(os.path.join(CH, 'v3_c5_wr.png'))}">
<img src="data:image/png;base64,{b64(os.path.join(CH, 'v3_c6_scatter.png'))}">

<h2>§4 标尺口径注记(冻结裁决并存呈现)</h2>
<p class="l">主人 2026-08-31 裁决(PROJECT_BOUNDARY §6): 回测验收口径冻结为 <b>"十年总收益 ≥200%(组合50/50口径) + 选股命中率 78-82%(WFA样本外)"</b>, 并明文认定"年化复合200%/交易胜率80%"为实证不可达口径(十年10万→59亿), <b>任何"调参逼近年化200%/胜率80%"的尝试=违反裁决, 拒绝执行</b>。三口径并存呈现:</p>
<table><tr><th>标尺</th><th>阈值</th><th>本轮实测(最优口径)</th><th>判定</th></tr>
<tr><td class="l">指令模板: 交易胜率</td><td>78-82%</td><td>64.1%(引擎BP豁免)</td><td class="n">❌ 差约14pp</td></tr>
<tr><td class="l">指令模板: 年化复合</td><td>200%</td><td>+21.16%(bp_ep机器qfq)</td><td class="n">❌ 差约179pp</td></tr>
<tr><td class="l">冻结验收: 十年总收益(旧引擎组合50/50)</td><td>≥200%</td><td>+202.30%(2026-09-01 drift_check 正式收口达标)</td><td class="p">✅ 已收口(9/1档案)</td></tr>
<tr><td class="l">参考: 本引擎统一账户现行闸口径十年总收益</td><td>—</td><td>{pct(mflag['total'])}(口径不同, 不得与旧引擎组合口径混用)</td><td class="p">✅(仅注脚级参考)</td></tr>
</table>
<p class="l">命中率标尺(78-82% WFA样本外)8/31 已 PASS(bp_ep WFA OOS 年化+8.84%), 与本专项"逐笔交易胜率"为两个不同统计口径, 引用须带口径注脚。</p>

<h2>§5 七轮迭代证据链与落闸状态</h2>
<table><tr><th>轮次</th><th>命题</th><th>结论</th></tr>
<tr><td>R1</td><td class="l">六模型基线全域回测</td><td class="l">五腿深亏(M1 -67.6%/M2 -91.3%/M3·M4 -32.6%/M5 -98.2%), M6 +157.9% 唯一正</td></tr>
<tr><td>R2-R3</td><td class="l">变体矩阵(止损ATR/止盈dd/MA20退出/流动性/相位/TopN)</td><td class="l">域依赖: MA20退出 M1毒(-9.3pp)/M6药(+4.7pp); liq2000双域正; 相位开关 M1 +7.5pp/MDD减半</td></tr>
<tr><td>R4</td><td class="l">统一账户撮合+权重+行业约束+熔断前沿</td><td class="l">行业≤4 为 bp_ep 核心增益(+8.4pp/年); 熔断收紧分域反号</td></tr>
<tr><td>R5</td><td class="l">R30a/b 豁免 A/B</td><td class="l">满仓BP豁免 +6.1pp; 组合权重下不binding; M6域豁免反-1.1pp → 豁免应按源配置</td></tr>
<tr><td>R6</td><td class="l">R30b 剂量-反应 25/30/35/50/∞</td><td class="l">帽25%拦截整月部署(-6.0pp/年); 帽≥30%四档<b>逐字节相同</b>(+302.83%/402,832元)=因果闭合</td></tr>
<tr><td>R7</td><td class="l">R30a 同日闸对篮子源摩擦 cap3/4</td><td class="l">+{pct(R7['U100BP_r30a3']['metrics']['cagr'],2)} vs +{pct(R7['U100BP_r30a4']['metrics']['cagr'],2)} vs 无闸{pct(R5['R5U100BPfree']['metrics']['cagr'],2)} → 摩擦仅0.2~0.3pp/年, <b>维持现状即可</b></td></tr>
</table>
<div class="find"><b>生产落闸状态(PD-024③, 主人「继续」令批准)</b>: R30b 行业簇帽 25%→30% 已于 2026-10-23 落闸至 <code>pre_trade_check_v2.py</code>(R30B_CLUSTER_CAP), 实弹差分验收=簇后26~28%→PASS(旧闸必拒区)/38%→REJECT, 宪法机制回归 ALL PASS; 变更集随本报告一并 git 入库。tulong_system_standard_v1.md 已回写: 阈值-版本绑定注记(C1) + bp_ep 2016窗档案(C2) + 变更记录。</div>

<h2>§6 优化条目清单(终版, 逐条待裁决; 与 优化条目清单_终版_20261022.md 同源)</h2>
{checklist_html}

<h2>§7 风险提示</h2>
<p class="l"><b>回测历史理论绩效不代表未来实盘收益。</b>本报告全部数字为本地历史行情(qfq复权口径)在显式规则与费用假设下的仿真结果, 含样本内成分(ML腿/参数选择的幸存者偏差风险)、快照口径近似(ST/行业映射)、执行近似(跌停强平/滑点常数), 且未含实盘冲击成本。未达实盘准入标准的任何结论(含旗舰账户)禁止直接实盘; 全部优化条目须主人裁决并按验证标准二次回测后方可进入标准规则。</p>
<p class="l" style="color:#888">生成: analysis/build_report_final_v3_20261023.py | 回测框架标准入口: analysis/backtest_fullhist_multimodel_20261022.py --round2/3/4/5/6/7 | 数据真源: D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_qfq.db(qfq唯一真源)</p>
</body></html>"""
out = os.path.join(BASE, "report_final_v3_20261023.html")
open(out, "w", encoding="utf-8").write(html)
import shutil
os.makedirs(VIZ_DIR, exist_ok=True)
try:
    shutil.copy(out, os.path.join(VIZ_DIR, "report_final_v3_20261023.html"))
    print("VIZ copy ok")
except Exception as e:
    print("VIZ copy fail:", e)
print("FINAL V3 REPORT DONE:", out, f"{os.path.getsize(out)/1e3:.0f} KB")
print(f"旗舰 {FLAG}: 期末 {mflag['final']:,.0f} 总收益 {pct(mflag['total'])} CAGR {pct(mflag['cagr'])} MDD {pct(mflag['mdd'])} 胜率 {sflag['wr']*100:.1f}% PF {sflag['pf']:.2f} 笔数 {sflag['n']}")
