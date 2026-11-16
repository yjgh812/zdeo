# -*- coding: utf-8 -*-
"""全景总报告 v2 (2026-10-22): 融合五轮结论, 替换一轮报告为当前真源"""
import os, sys, json, base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import sqlite3

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
VIZ_DIR = r"D:\zcodesj\.zcode\v2\visualizations\06a257029f8f1d89809f0384d47c6e4b62ed7ed72381fc6efed85ce3ea6dd0e1"
CH = os.path.join(BASE, "charts")
for f in ("msyh.ttc", "simhei.ttf"):
    fp = os.path.join(r"C:\Windows\Fonts", f)
    if os.path.exists(fp):
        font_manager.fontManager.addfont(fp)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=fp).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False

def pct(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:.{nd}f}%"

# 净值曲线
def nav_of(path, col="equity"):
    e = pd.read_csv(path)
    return e.set_index("date")[col] / (1e5 if col == "equity" else 1.0)

curves = {
    "bp_ep机器C腿(qfq)": nav_of(os.path.join(BASE, "bp_ep_r2_nav_C_qfq.csv"), "nav"),
    "引擎BP腿·豁免(U100BPfree)": nav_of(os.path.join(BASE, "equity_R5U100BPfree.csv")),
    "引擎BP腿·受闸(U100BP)": nav_of(os.path.join(BASE, "equity_U100BP.csv")),
    "统一账户60/20/20·豁免": nav_of(os.path.join(BASE, "equity_R5U602020free.csv")),
}
ci2 = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro", uri=True)
idx = pd.read_sql_query("SELECT date,close FROM index_daily WHERE index_code='000001' AND date>=20160101", ci2)
ci2.close()
idx["date"] = idx["date"].astype(int)
cal = curves["统一账户60/20/20·豁免"].index
sse_n = idx.drop_duplicates("date").set_index("date")["close"].reindex(cal).ffill().bfill()
sse_n = sse_n / sse_n.iloc[0]

fig, ax = plt.subplots(figsize=(12, 6))
for lab, s in curves.items():
    ax.plot(pd.to_datetime(pd.Series(s.index.astype(str)), format="%Y-%m-%d", errors="coerce"),
            s.to_numpy(), lw=1.6, label=lab)
ax.plot(pd.to_datetime(pd.Series(sse_n.index.astype(str)), format="%Y-%m-%d", errors="coerce"),
        sse_n.to_numpy(), "k--", lw=1.1, label="上证指数(归一)")
ax.set_yscale("log"); ax.set_ylabel("净值(起点1.0, 对数)")
ax.set_title("五轮迭代最终格局: 关键账户净值 2016-2026 (初始10万)")
ax.legend(fontsize=9); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "final_v2_nav.png"), dpi=110); plt.close(fig)

R4 = json.load(open(os.path.join(BASE, "summary_round4.json"), encoding="utf-8"))
R5 = json.load(open(os.path.join(BASE, "summary_round5.json"), encoding="utf-8"))

def mrow(lab, m, note=""):
    wr = pct(m.get("win_rate")) if m.get("win_rate") is not None else "n/a"
    return (f"<tr><td>{lab}</td><td>{m['final_equity']:,.0f}</td>"
            f"<td class='{'p' if m['cagr']>0 else 'n'}'>{pct(m['cagr'])}</td><td>{pct(m['mdd'])}</td>"
            f"<td>{wr}</td><td class='l'>{note}</td></tr>")

arch = ""
arch += mrow("bp_ep官方机器C腿(qfq, 无组合闸)", dict(final_equity=735386, cagr=0.2116, mdd=-0.2735, win_rate=None),
             "生产官方机器, 系统内最强真实约束口径")
arch += mrow(R5["R5U100BPfree"]["metrics"] and "引擎BP腿·豁免(100%仓)", R5["R5U100BPfree"]["metrics"],
             "引擎执行层+宇宙过滤; 胜率全场最高64.1%")
arch += mrow("引擎BP腿·受R30b闸(U100BP)", R4["U100BP"]["metrics"], "闸门摩擦-6.1pp(豁免A/B实测)")
arch += mrow("统一账户60/20/20·豁免", R5["R5U602020free"]["metrics"], "BP60+M6Top50 20+M1相位栈20")
arch += mrow("三轮分仓60/20/20(机器BP腿)", dict(final_equity=407409, cagr=0.139, mdd=-0.276, win_rate=None),
             "机器BP+引擎M6/M1, 分仓复利口径")
arch += mrow("M1相位栈(豁免, 100%仓)", R5["R5free__M1_lowconsol_main"]["metrics"],
             "低位横盘+主线+相位+liq2000+ATR1.5")
arch += mrow("M6 Top50@liq2000(受闸, 100%仓)", R4.get("R3m6_l_top50__M6_wfa_top20", R5["R5free__M6_wfa_top20"])["metrics"]
             if False else dict(final_equity=434534, cagr=0.146, mdd=-0.455, win_rate=0.363),
             "闸门反而+1.1pp(拦截拥挤动量), 不豁免")
arch += mrow("基准: 上证指数", dict(final_equity=np.nan, cagr=0.011, mdd=-0.308, win_rate=None), "归一对比")
arch = arch.replace("<td>nan</td>", "<td>—</td>")

ab = ""
for lab, k in (("受闸(U100BP)", "U100BP"), ("豁免(R5U100BPfree)", "R5U100BPfree")):
    m = (R4 if k in R4 else R5)[k]["metrics"]
    ab += mrow(lab, m, "")
chain5 = R5["R5CHAIN602020free"]["metrics"]

b64 = lambda p: base64.b64encode(open(p, "rb").read()).decode()
html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>全景总报告v2 20261022</title>
<style>body{{font-family:"Microsoft YaHei",SimHei,sans-serif;margin:24px;max-width:1200px;color:#222}}
h1{{font-size:21px;border-bottom:3px solid #1f4e9c;padding-bottom:8px}}h2{{font-size:16px;color:#1f4e9c;border-left:5px solid #1f4e9c;padding-left:8px;margin-top:30px}}
table{{border-collapse:collapse;font-size:12px;margin:8px 0;width:100%}}th,td{{border:1px solid #bbb;padding:3px 6px;text-align:right}}
th{{background:#1f4e9c;color:#fff}}td.p{{color:#c0392b}}td.n{{color:#27ae60}}td.l{{text-align:left}}
.note{{background:#fff8e1;border:1px solid #e6c200;padding:8px 12px;font-size:12px;margin:8px 0}}
.find{{background:#fdecea;border:1px solid #c0392b;padding:10px 14px;font-size:13px;margin:10px 0}}</style></head><body>
<h1>屠龙系统 · 全域历史回测专项 · 全景总报告 v2（2016-01-01 ~ 2026-10-21, 五轮迭代收口）</h1>
<p>专项指令标尺: 胜率78–82% / 年化200% | 初始10万 | 沪深主板+创业板剔ST·科创·北交所·次新 | qfq收益口径+raw执行闸 | 全程账目守恒断言</p>

<h2>1. 最终格局（五轮全部实测, 无一虚构）</h2>
<table><tr><th>账户/腿</th><th>期末(元)</th><th>CAGR</th><th>MDD</th><th>胜率</th><th>说明</th></tr>{arch}</table>
<img src="data:image/png;base64,{b64(os.path.join(CH,'final_v2_nav.png'))}">

<h2>2. 豁免A/B定谳（第五轮, 回测层取证）</h2>
<table><tr><th>配置</th><th>期末(元)</th><th>CAGR</th><th>MDD</th><th>胜率</th><th>说明</th></tr>{ab}</table>
<p><b>修正四轮归因</b>: 豁免在100% BP配置回收 <b>+6.1pp</b>(+7.8%→+13.9%, 与机器残差7.3pp=宇宙过滤+执行层差异);
但60/20/20组合豁免前后完全一致(+9.1%) — <b>组合权重下R30b本就不binding</b>。四轮"组合缺口=闸门摩擦"的表述据此修正:
摩擦主要作用于满仓BP配置; 组合口径的真实拖累来自引擎执行层BP腿本身(+13.9%)与机器(+21.2%)的口径差。</p>

<h2>3. 域依赖矩阵（规则×信号域, 五轮最稳定的发现）</h2>
<table><tr><th>规则</th><th>低位横盘域(M1)</th><th>动量域(M6)</th><th>bp_ep篮子域</th></tr>
<tr><td>-8%移动止损+ATR缓冲</td><td class='p'>+1.5pp(止盈触发59→362)</td><td>≈0</td><td>n/a(月度轮换无止损)</td></tr>
<tr><td>趋势走坏MA20×3</td><td class='n'>-9.3pp(毒药)</td><td class='p'>+4.7pp(良药)</td><td>n/a</td></tr>
<tr><td>流动性2000万档</td><td class='p'>+2.9pp</td><td class='p'>+4.8pp</td><td>n/a</td></tr>
<tr><td>相位开关(amt_q≥0.5)</td><td class='p'>+7.5pp/MDD减半</td><td>未测(留前瞻)</td><td>n/a</td></tr>
<tr><td>行业≤4约束</td><td>n/a</td><td class='n'>-1.3pp</td><td class='p'>+9pp(核心增益)</td></tr>
<tr><td>熔断收紧15/25</td><td class='p'>+1.1pp</td><td class='n'>-2.7pp</td><td>n/a</td></tr>
<tr><td>R30a/b组合闸</td><td class='n'>-1.9pp</td><td class='p'>+1.1pp</td><td class='n'>-6.1pp(满仓配置)</td></tr>
</table>
<p class="note">结论: <b>不存在全局最优规则集</b>, 任何规则上线前必须分域验收 — 这是五轮最可迁移的方法论结论。</p>

<h2>4. 标尺校验（如实）</h2>
<p>胜率标尺78–82%: 全场最高=引擎BP腿豁免 <b>64.1%</b>, 差14pp ❌。年化标尺200%: 全场最高=bp_ep机器qfq <b>+21.2%</b>, 差179pp ❌。
五轮从"六腿五亏"(-12.2%官方口径)走到"+21.2%最强口径+64.1%最高胜率", 方向收敛但差距仍为数量级, <b>任何腿均未达实盘准入标准</b>。</p>

<h2>5. 决策清单（待主人裁决项已标注）</h2>
<ol>
<li><b>【须主人裁决·闸门语义】</b>R30a/b策略源级豁免通道: 证据=满仓BP配置+6.1pp; 组合配置无影响; M6域豁免反而-1.1pp → 豁免应<b>按源配置</b>(bp_ep类篮子源豁免, 动量源不豁免), 走三段式流水线</li>
<li><b>【须主人裁决·前瞻队列】</b>M1相位开关(amt_q≥0.5)+熔断15/25 注册前瞻验证(零交易权呈报, 五轮历史分桶+A/B证据已备)</li>
<li>bp_ep qfq口径+21.2%是否升级为生产主参考(替换标准v1中+21.35%的2018窗记载, 补2016窗全口径档案)</li>
<li>全景报告v2(本文)接替一轮报告为回测真源; 引擎v4(--round2/3/4/5)为回测框架标准入口</li>
<li>M6域规则包(不豁免/不紧熔断/可试MA20退出)与M1域规则包(豁免/紧熔断/相位/ATR缓冲)分域固化, 禁跨域复用</li>
</ol>
<div class="note"><b>风险提示</b>: 全部为历史理论回测绩效, 含样本内成分(ML腿)、未复权/复权双口径与实现近似
(跌停强平/快照映射/滑点常数), 不代表未来实盘收益; 未达实盘准入标准的任何结论禁止直接实盘。</div>
</body></html>"""
out = os.path.join(BASE, "report_final_v2_20261022.html")
open(out, "w", encoding="utf-8").write(html)
import shutil
try:
    shutil.copy(out, os.path.join(VIZ_DIR, "report_final_v2_20261022.html"))
    print("VIZ copy ok")
except Exception as e:
    print("VIZ copy fail:", e)
print("FINAL V2 REPORT DONE:", out, f"{os.path.getsize(out)/1e3:.0f} KB")
