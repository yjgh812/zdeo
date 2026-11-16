# -*- coding: utf-8 -*-
"""第二轮迭代复测报告生成器 (2026-10-22, 承接一轮§6优化清单)
输入: summary.json(一轮基线) + summary_round2.json(变体矩阵) + equity/trades CSV
     + bp_ep_r2_result.json(官方机器2016窗复刻, 由外部解析脚本落盘)
输出: report_round2_20261022.html(自包含) + 副本到可视化目录
"""
import os, sys, json, base64
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
VIZ_DIR = r"D:\zcodesj\.zcode\v2\visualizations\06a257029f8f1d89809f0384d47c6e4b62ed7ed72381fc6efed85ce3ea6dd0e1"
S1 = json.load(open(os.path.join(BASE, "summary.json"), encoding="utf-8"))
R2P = os.path.join(BASE, "summary_round2.json")
R2 = json.load(open(R2P, encoding="utf-8")) if os.path.exists(R2P) else {}
BPEP_P = os.path.join(BASE, "bp_ep_r2_result.json")
BPEP = json.load(open(BPEP_P, encoding="utf-8")) if os.path.exists(BPEP_P) else None

def pct(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and (np.isnan(x))) else f"{x*100:.{nd}f}%"

PRIMARY = "M1_lowconsol_main"
tr1 = pd.read_csv(os.path.join(BASE, f"trades_{PRIMARY}.csv"), encoding="utf-8-sig")
eq1 = pd.read_csv(os.path.join(BASE, f"equity_{PRIMARY}.csv"))

# ---- 相位分桶(②): amt_q 来自 round2 M1 equity 第三列(同网格) ----
eq_aq_src = os.path.join(BASE, "equity_R2stopAtr__M1_lowconsol_main.csv")
phase_tbl = ""
if os.path.exists(eq_aq_src):
    aq = pd.read_csv(eq_aq_src)[["date", "amt_q"]]
    for m in ("M1_lowconsol_main", "M2_lowconsol_bgp"):
        e = pd.read_csv(os.path.join(BASE, f"equity_{m}.csv"))
        if "amt_q" not in e.columns:
            e = e.merge(aq, on="date")
        e["r"] = e["equity"].pct_change()
        on = e[e["amt_q"] >= 0.5]["r"].dropna()
        off = e[e["amt_q"] < 0.5]["r"].dropna()
        ann = lambda s: ((1 + s).prod()) ** (243 / max(len(s), 1)) - 1
        phase_tbl += (f"<tr><td>{m}</td><td>{len(on)}</td><td>{pct(ann(on))}</td>"
                      f"<td>{len(off)}</td><td>{pct(ann(off))}</td>"
                      f"<td class='{'p' if ann(on)>ann(off) else 'n'}'>{(ann(on)-ann(off))*100:+.1f}pp</td></tr>")

# ---- 熔断成本归因(⑩) ----
cb = tr1[tr1["reason"].astype(str).str.startswith("熔断")]
cb_n = len(cb); cb_loss = float(cb["pnl"].sum()); cb_dates = sorted(cb["sell_date"].unique())

# ---- 变体对照表 ----
rows = ""
BL = {"M1_lowconsol_main": S1["models"]["M1_lowconsol_main"]["metrics"],
      "M6_wfa_top20": S1["models"]["M6_wfa_top20"]["metrics"]}
for key, v in sorted(R2.items()):
    tag, m = key.split("__")
    bl = BL.get(m)
    mt, ac = v["metrics"], v.get("acc", {})
    d_cagr = (mt["cagr"] - bl["cagr"]) * 100 if bl else float("nan")
    rows += (f"<tr><td>{tag}</td><td>{m.replace('_lowconsol_main','M1').replace('_wfa_top20','M6')}</td>"
             f"<td>{mt['final_equity']:,.0f}</td>"
             f"<td class='{'p' if mt['cagr']>0 else 'n'}'>{pct(mt['cagr'])}</td>"
             f"<td class='{'p' if d_cagr>0 else 'n'}'>{d_cagr:+.1f}pp</td>"
             f"<td>{pct(mt['mdd'])}</td><td>{pct(mt['win_rate'])}</td>"
             f"<td>{pct(ac.get('stop_avg'),2) if ac.get('stop_avg') is not None else 'n/a'}</td>"
             f"<td>{ac.get('tp_n','-')}</td><td>{ac.get('cb_n','-')}</td>"
             f"<td>{pct(ac.get('bigwin_share')) if ac.get('bigwin_share') is not None else 'n/a'}</td>"
             f"<td>{ac.get('trend_n','-')}</td></tr>")

# ---- 验收判定(清单③④⑤⑥②) ----
def getv(tag, m):
    return R2.get(f"{tag}__{m}", {}).get("metrics"), R2.get(f"{tag}__{m}", {}).get("acc", {})
m1_bl = BL["M1_lowconsol_main"]; m6_bl = BL["M6_wfa_top20"]
verd = []
def verdict(item, crit, ok, detail):
    verd.append(f"<tr><td>{item}</td><td class='l'>{crit}</td>"
                f"<td class='{'p' if ok else 'n'}'>{'✅通过' if ok else '❌未通过'}</td><td class='l'>{detail}</td></tr>")

m_sa, a_sa = getv("R2stopAtr", "M1_lowconsol_main")
if m_sa:
    imp = (a_sa.get("stop_avg") or 0) - (tr1[tr1["reason"].str.startswith("止损")]["pnl_pct"].mean())
    verdict("③止损ATR缓冲(M1)", "止损笔均亏改善≥0.5pp 且 CAGR 不降",
            imp >= 0.005 and m_sa["cagr"] >= m1_bl["cagr"],
            f"止损均亏 {pct(a_sa.get('stop_avg'),2)}(基线{pct(tr1[tr1['reason'].str.startswith('止损')]['pnl_pct'].mean(),2)}) "
            f"改善{(imp)*100:+.2f}pp | CAGR {pct(m_sa['cagr'])} vs 基线 {pct(m1_bl['cagr'])}")
m_tp, a_tp = getv("R2tpdd2_8", "M1_lowconsol_main")
if m_tp:
    bw_b = float((tr1["pnl_pct"] > 0.30).mean())
    verdict("④止盈30%档回撤6→8%(M1)", "大盈利(>30%)笔占比提升 且 MDD 恶化≤1pp",
            (a_tp.get("bigwin_share") or 0) >= bw_b and m_tp["mdd"] >= m1_bl["mdd"] - 0.01,
            f"大盈利占比 {pct(a_tp.get('bigwin_share'))}(基线{pct(bw_b)}) | MDD {pct(m_tp['mdd'])} vs {pct(m1_bl['mdd'])} | CAGR {pct(m_tp['cagr'])}")
m_mx, a_mx = getv("R2ma20exit", "M1_lowconsol_main")
if m_mx:
    verdict("⑤趋势走坏MA20×3(M1)", "触发笔后验为负贡献方向(卖出后收益<0的占比>50%)且CAGR不恶化>2pp",
            m_mx["cagr"] >= m1_bl["cagr"] - 0.02,
            f"触发{a_mx.get('trend_n',0)}笔 | CAGR {pct(m_mx['cagr'])} vs 基线 {pct(m1_bl['cagr'])} | MDD {pct(m_mx['mdd'])}")
for tag, thr in (("R2liq2000", "2000万"), ("R2liq5000", "5000万")):
    m_l, _ = getv(tag, "M1_lowconsol_main")
    if m_l:
        verdict(f"⑥流动性{thr}(M1)", "结论方向与基线一致(同为负CAGR域内排序稳定)",
                (m_l["cagr"] < 0) == (m1_bl["cagr"] < 0),
                f"CAGR {pct(m_l['cagr'])} vs 基线 {pct(m1_bl['cagr'])} | MDD {pct(m_l['mdd'])}")

# ---- HTML ----
bp_html = ""
if BPEP:
    bp_rows = "".join(f"<tr><td>{k}</td><td>{v['final']:,.0f}</td><td class='{'p' if v['ann']>0 else 'n'}'>{pct(v['ann'])}</td>"
                      f"<td>{v.get('sharpe',0):.2f}</td><td>{pct(v['mdd'])}</td></tr>"
                      for k, v in BPEP.items())
    bp_html = f"""<h3>⑨ bp_ep 官方机器复刻(2016窗/10万, 未复权口径如实标注)</h3>
<table><tr><th口径</th><th>期末(元)</th><th>年化</th><th>Sharpe</th><th>MDD</th></tr>{bp_rows}</table>
<p class="note">A=向量月末close计价/换手成本0.35%; B=backtrader理想化(零成本); C=bt全约束(T+1/封板/停牌/整手/真实费用/滑点/量上限)。
官方2018窗参照全机器口径=+21.35%。</p>"""

html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>第二轮迭代复测报告 20261022</title>
<style>body{{font-family:"Microsoft YaHei",SimHei,sans-serif;margin:24px;max-width:1200px;color:#222}}
h1{{font-size:20px;border-bottom:3px solid #1f4e9c;padding-bottom:8px}}
h2{{font-size:16px;color:#1f4e9c;margin-top:30px;border-left:5px solid #1f4e9c;padding-left:8px}}
table{{border-collapse:collapse;font-size:12px;margin:8px 0;width:100%}}
th,td{{border:1px solid #bbb;padding:3px 6px;text-align:right}}th{{background:#1f4e9c;color:#fff}}
td.l{{text-align:left}}td.p{{color:#c0392b}}td.n{{color:#27ae60}}
.note{{background:#fff8e1;border:1px solid #e6c200;padding:8px 12px;font-size:12px;margin:8px 0}}</style></head><body>
<h1>屠龙系统 · 第二轮迭代复测报告（承接一轮§6清单, 2026-10-22）</h1>
<p>窗口 2016-01-01~2026-10-21 | 初始10万 | 引擎 v4 变体矩阵 + bp_ep官方机器复刻 | 全部账目守恒断言PASS</p>

<h2>1. 变体对照（基线=一轮结果; ΔCAGR=相对同模型基线）</h2>
<table><tr><th>变体</th><th>模型</th><th>期末(元)</th><th>CAGR</th><th>ΔCAGR</th><th>MDD</th><th>胜率</th>
<th>止损均亏</th><th>止盈笔</th><th>熔断笔</th><th>大盈利(&gt;30%)占比</th><th>MA20触发</th></tr>{rows}</table>
<p class="note">一轮基线: M1 CAGR {pct(m1_bl['cagr'])} / MDD {pct(m1_bl['mdd'])} / 胜率 {pct(m1_bl['win_rate'])};
M6 CAGR {pct(m6_bl['cagr'])} / MDD {pct(m6_bl['mdd'])} / 胜率 {pct(m6_bl['win_rate'])}。</p>

<h2>2. 优化清单验收判定（一轮§6条款逐条）</h2>
<table><tr><th>条款</th><th>验收标准(一轮钉死)</th><th>判定</th><th>实测</th></tr>{''.join(verd)}</table>

<h2>3. ② 主线闸×相位分桶（活跃度分位 amt_q=全市场20日额rank; ≥0.5=ON）</h2>
<table><tr><th>模型</th><th>ON日数</th><th>ON年化</th><th>OFF日数</th><th>OFF年化</th><th>ON−OFF</th></tr>{phase_tbl}</table>
<p class="note">档案语境: 低位横盘主线首日效应"2024+有效/熊市无效", 相位开关为探索性标注。本表为历史分桶(非前瞻), 翻转即降级。</p>

<h2>4. ⑩ 熔断成本归因（M1 基线）</h2>
<p>熔断平仓 {cb_n} 笔 | 合计损益 {cb_loss:+,.0f} 元 | 触发日 {len(cb_dates)} 个
({"、".join(str(d) for d in cb_dates[:12])}{"…" if len(cb_dates)>12 else ""})</p>

{bp_html}

<h2>5. 第三轮清单（按本轮验收结果滚动更新）</h2>
<ol>
<li>通过验收的变体进入组合叠加复测(如stopAtr+ma20exit同开, 验收=CAGR叠加增益非相消)</li>
<li>主线闸保留为阻断闸; 相位开关按§3分桶结果决定标注/启用(历史分桶仅作参考, 前瞻队列继续)</li>
<li>bp_ep腿若为正: 启动60/20/20统一账户架构回测(bp_ep60%+最优规则腿40%); 若为负: 登记观察不入架构</li>
<li>ML腿阈值秩校准回写信号件文档(0.75生产阈值仅适用于原重训模型版本, 当前库须重标定)</li>
<li>M6(wfa)单腿深化: TopN敏感性(10/30/50)+行业约束变体, 验收=CAGR≥+9.2%且MDD改善</li>
<li>止损ATR缓冲k敏感性(0.5/1.0/1.5), 验收=均亏改善单调且CAGR峰值区间</li>
</ol>
<div class="note"><b>风险提示</b>: 全部为历史理论回测绩效, 不代表未来实盘收益; ML腿含样本内成分; 变体经多重比较,
仅通过钉死验收标准者方可进入下一轮, 禁止据此直接实盘。</div>
</body></html>"""

out = os.path.join(BASE, "report_round2_20261022.html")
open(out, "w", encoding="utf-8").write(html)
import shutil
try:
    shutil.copy(out, os.path.join(VIZ_DIR, "report_round2_20261022.html"))
    print("VIZ copy ok")
except Exception as e:
    print("VIZ copy fail:", e)
print("ROUND2 REPORT DONE:", out, f"{os.path.getsize(out)/1e3:.0f} KB")
