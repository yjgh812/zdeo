# -*- coding: utf-8 -*-
"""第四轮报告: 统一账户撮合/月度再平衡对照/行业约束/熔断前沿/qfq口径 (2026-10-22)"""
import os, sys, json, re
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
VIZ_DIR = r"D:\zcodesj\.zcode\v2\visualizations\06a257029f8f1d89809f0384d47c6e4b62ed7ed72381fc6efed85ce3ea6dd0e1"
R4 = json.load(open(os.path.join(BASE, "summary_round4.json"), encoding="utf-8"))
S1 = json.load(open(os.path.join(BASE, "summary.json"), encoding="utf-8"))

def pct(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:.{nd}f}%"

# CHAIN 修正口径(按日期跨度折年; 引擎summary中该项为月度粒度误折, 此处重算)
ce = pd.read_csv(os.path.join(BASE, "equity_CHAIN602020.csv"))
_yrs = (ce["date"].iloc[-1] - ce["date"].iloc[0]) / 10000.0
_e = ce["equity"].to_numpy()
CHAIN = dict(final_equity=float(_e[-1] * 1e5), cagr=float(_e[-1] ** (1 / _yrs) - 1),
             mdd=float((_e / np.maximum.accumulate(_e) - 1).min()), win_rate=None)

# bp_ep机器 qfq对照(从运行日志尾块解析)
bp_cmp_rows = ""
_log = r"D:\workbuddyspace\temp\zcode-bash-bUYj4O\session_50c20dc045cef44e06bd52ae311798db\call_37b150afeb3d4ab29ad9777d-stdout.log"
try:
    txt = open(_log, encoding="utf-8", errors="replace").read()
    blocks = txt.split("=" * 80)
    tail = blocks[-1] if len(blocks) > 1 else txt
    for m in re.finditer(r"(A\d-[^\s]+|B-理想化|C-真实约束)\s+终值\s+([\d,]+)\s+年化\s+([-\d.]+)%\s+Sharpe\s+([-\d.]+)\s+MDD\s+([-\d.]+)%", tail):
        bp_cmp_rows += (f"<tr><td>qfq</td><td>{m.group(1)}</td><td>{m.group(2).replace(',','')}</td>"
                        f"<td class='p'>{m.group(3)}%</td><td>{m.group(4)}</td><td>{m.group(5)}%</td></tr>")
except Exception as e:
    bp_cmp_rows = f"<tr><td colspan='6'>解析失败: {e}</td></tr>"

rows = ""
for k, v in sorted(R4.items()):
    m = v["metrics"]
    if k == "CHAIN602020":
        m = CHAIN
    a = v.get("acc", {})
    wr = pct(m["win_rate"]) if m.get("win_rate") is not None else "n/a"
    rows += (f"<tr><td>{k}</td><td>{m['final_equity']:,.0f}</td>"
             f"<td class='{'p' if m['cagr']>0 else 'n'}'>{pct(m['cagr'])}</td><td>{pct(m['mdd'])}</td>"
             f"<td>{wr}</td><td>{a.get('tp_n','-')}</td><td>{a.get('cb_n','-')}</td></tr>")

html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>第四轮报告 20261022</title>
<style>body{{font-family:"Microsoft YaHei",SimHei,sans-serif;margin:24px;max-width:1150px;color:#222}}
h1{{font-size:20px;border-bottom:3px solid #1f4e9c;padding-bottom:8px}}h2{{font-size:16px;color:#1f4e9c;border-left:5px solid #1f4e9c;padding-left:8px;margin-top:28px}}
table{{border-collapse:collapse;font-size:12px;margin:8px 0;width:100%}}th,td{{border:1px solid #bbb;padding:3px 6px;text-align:right}}
th{{background:#1f4e9c;color:#fff}}td.p{{color:#c0392b}}td.n{{color:#27ae60}}
.note{{background:#fff8e1;border:1px solid #e6c200;padding:8px 12px;font-size:12px;margin:8px 0}}
.find{{background:#fdecea;border:1px solid #c0392b;padding:10px 14px;font-size:13px;margin:10px 0}}</style></head><body>
<h1>屠龙系统 · 第四轮迭代复测：统一账户撮合与闸门冲突诊断（2026-10-22）</h1>

<div class="find"><b>核心发现（量化）</b>：bp_ep 篮子策略与屠龙组合级行业闸门<b>结构性冲突</b>——
R30a(同日同行业≤3只)+R30b(单行业簇≤25%) 应用于"每行业≤4只"设计的 bp_ep 月度篮子时,
单行业天然占比~25%被闸, 买入大量被拒 → 引擎执行层 BP 腿(U100BP) 年化 +7.8%/MDD -9.8%,
远低于无组合闸的官方机器 C 腿(qfq) +21.2%。缺口 ≈ <b>13pp/年 系闸门摩擦成本</b>。
处理方向(须主人裁决): 对"篮子内部已含行业约束(≤4/行业)"的策略源增加 R30a/b 组合级豁免通道
(生产 pre_trade_check 已有 owner_approve 特批路径, 需固化为策略源级参数而非逐笔特批)。</div>

<h2>1. 第四轮全部运行（初始10万, 2016-2026, 全部账目守恒PASS）</h2>
<table><tr><th>运行</th><th>期末(元)</th><th>CAGR</th><th>MDD</th><th>胜率</th><th>止盈</th><th>熔断</th></tr>{rows}</table>
<p class="note">U602020=统一账户撮合(共享现金/50槽/账户级熔断, BP腿受组合闸); U100BP=统一引擎100%bp_ep(同闸);
CHAIN602020=月度再平衡链(分仓口径, BP腿=U100BP闸口径; 引擎summary中该项年化为月度粒度误折算, 本表已按日期跨度重算);
R4m6_ind4=M6 Top50+行业≤4; R4m1_cb*=M1最优栈熔断档位扫描。</p>

<h2>2. ①统一账户 vs ④月度再平衡（同闸口径对照）</h2>
<p>统一账户 U602020 +9.1%/-19.0% vs 月度再平衡链 +{ (CHAIN['cagr'])*100:.1f}%/{pct(CHAIN['mdd'])}
→ 两口径差异主要来自 BP 腿的欠投资状态(闸门拦截后统一账户现金池被 M6/M1 腿占用),
<b>口径差异验收(&lt;2pp)不通过, 根因=闸门冲突而非撮合机制</b>, 豁免通道落地后须重测。</p>

<h2>3. ② bp_ep 复权口径对照（官方机器, C=真实约束层）</h2>
<table><tr><th>口径</th><th>口径项</th><th>期末(元)</th><th>年化</th><th>Sharpe</th><th>MDD</th></tr>
{bp_cmp_rows}
<tr><td>raw(未复权)</td><td>C-真实约束</td><td>504,831</td><td class='p'>+16.84%</td><td>1.01</td><td>-29.80%</td></tr></table>
<p class="note">qfq 复权后 C 腿 +21.16%(未复权 +16.84%) → 分红送转漏损消除后<b>更强, 方向一致验收✅</b>;
A2(行业≤4) qfq +22.08% 为全场最高。</p>

<h2>4. ⑤ 行业约束迁移 / ⑥ 熔断前沿</h2>
<p><b>⑤</b>: M6 Top50 加行业≤4 → +13.3%(无约束+14.6%, -1.3pp) — bp_ep 的行业约束发现<b>不迁移</b>到动量域(动量篮子行业集中即信息)。<br>
<b>⑥</b>: M1 最优栈熔断前沿: cb15/25 → <b>-0.1%</b>/MDD-50.1% &lt; cb20/25 → -1.2% &lt; cb25/35 → -1.3%
→ 低位横盘域<b>熔断越紧越好</b>(-50%级MDD证明20/30档对该域仍过松), 采纳 15/25 为该域候选(留前瞻)。</p>

<h2>5. 第五轮清单</h2>
<ol>
<li><b>【须主人裁决】</b>策略源级 R30a/b 豁免通道: 对"篮子已含行业约束"的 admitsource(bp_ep/M6类)按源配置豁免组合级行业闸,
保留熔断/止损/涨停闸 → 重测 U602020(验收: 与机器C腿缺口&lt;3pp)</li>
<li>豁免后重跑统一账户 vs 分仓口径(验收: 差异&lt;2pp, 兑现四轮未通过项)</li>
<li>bp_ep qfq 口径为组合基准腿重跑 60/20/20 三权重结构(验收: MDD≤-27%下CAGR最大化)</li>
<li>M1 域熔断 15/25 + 相位开关 前瞻队列合并注册(零交易权呈报, 待主人令)</li>
<li>M6 域熔断扫描(cb15/25对动量域), 验收=MDD-CAGR前沿是否同样左移</li>
<li>全景报告v2: 融合四轮全部结论重出总报告(含逐年明细与六图, 替换一轮报告为当前真源)</li>
</ol>
<div class="note"><b>风险提示</b>: 历史理论回测绩效不代表未来实盘收益; 豁免通道属闸门语义修改(宪法三段式触发条件:
闸门语义修改须走 m1→m2/m4/m6→m7 流水线并经主人批准), 本轮仅量化证据不落闸。</div>
</body></html>"""
out = os.path.join(BASE, "report_round4_20261022.html")
open(out, "w", encoding="utf-8").write(html)
import shutil
try:
    shutil.copy(out, os.path.join(VIZ_DIR, "report_round4_20261022.html"))
    print("VIZ copy ok")
except Exception as e:
    print("VIZ copy fail:", e)
print("ROUND4 REPORT DONE:", out)
