# -*- coding: utf-8 -*-
"""第三轮报告: 60/20/20统一账户架构 + 组合叠加扫描 (2026-10-22)
腿: bp_ep官方机器C腿(60%) + M6 wfa@liq2000(20%) + M1低位横盘相位+liq2000(20%)
合成口径: 按腿分仓各自复利, 组合净值=Σ w×nav(未启动腿=1.0平), 与生产"统一账户撮合"存在
         现金共享差异, 如实标注(二轮近似口径)。
"""
import os, sys, json, base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

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

R3 = json.load(open(os.path.join(BASE, "summary_round3.json"), encoding="utf-8"))
R2 = json.load(open(os.path.join(BASE, "summary_round2.json"), encoding="utf-8"))
S1 = json.load(open(os.path.join(BASE, "summary.json"), encoding="utf-8"))

def pct(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:.{nd}f}%"

def metrics(nav_ser, dates):
    e = nav_ser.to_numpy(dtype=float)
    ret = e[1:] / e[:-1] - 1
    yrs = len(e) / 243.0
    cagr = e[-1] ** (1 / yrs) - 1
    vol = ret.std() * np.sqrt(243)
    dd = e / np.maximum.accumulate(e) - 1
    imin = int(dd.argmin())
    return dict(final=float(e[-1]), cagr=float(cagr), mdd=float(dd.min()),
                sharpe=float(ret.mean() * 243 / vol) if vol > 0 else None,
                mdd_date=int(dates[imin]), n=int(len(e)))

# ---- 腿净值 ----
bp = pd.read_csv(os.path.join(BASE, "bp_ep_r2_nav_C.csv"))
bp["date"] = bp["date"].astype(int)
bpnav = bp.drop_duplicates("date").set_index("date")["nav"]

def leg_nav(tag, model):
    e = pd.read_csv(os.path.join(BASE, f"equity_{tag}__{model}.csv"))
    return e.set_index("date")["equity"] / 100000.0

M6 = "M6_wfa_top20"; M1 = "M1_lowconsol_main"
def best_tag(model, prefix="R3"):
    cands = [(v["metrics"]["cagr"], k.split("__")[0]) for k, v in R3.items()
             if k.endswith("__" + model) and k.startswith(prefix)]
    return max(cands)[1]
TAG_M6 = best_tag(M6)   # R3m6_l_top50
TAG_M1 = best_tag(M1)   # R3m1_pl_sa15
m6nav = leg_nav(TAG_M6, M6)
m1nav = leg_nav(TAG_M1, M1)

cal = sorted(set(bpnav.index) | set(m6nav.index) | set(m1nav.index))
def align(s):
    return s.reindex(cal).ffill().fillna(1.0)
B, X6, X1 = align(bpnav), align(m6nav), align(m1nav)

weights = [(0.6, 0.2, 0.2, "60/20/20(标准架构)"), (0.5, 0.3, 0.2, "50/30/20"),
           (0.4, 0.4, 0.2, "40/40/20"), (0.7, 0.2, 0.1, "70/20/10"),
           (1.0, 0.0, 0.0, "100%bp_ep(对照)")]
rows, curves = "", {}
for wb, w6, w1, lab in weights:
    nav = wb * B + w6 * X6 + w1 * X1
    m = metrics(nav, cal)
    curves[lab] = nav
    rows += (f"<tr><td>{lab}</td><td>{m['final']*100000:,.0f}</td>"
             f"<td class='{'p' if m['cagr']>0 else 'n'}'>{pct(m['cagr'])}</td><td>{pct(m['mdd'])}</td>"
             f"<td>{m['sharpe']:.2f}</td><td>{m['mdd_date']}</td></tr>")

# 年度表(60/20/20)
nav = curves["60/20/20(标准架构)"]
df = pd.DataFrame({"date": cal, "nav": nav.to_numpy()})
df["y"] = (df["date"] // 10000).astype(int)
yrows = ""
prev = None
for y, s in df.groupby("y")["nav"]:
    e0 = s.iloc[0] if prev is None else prev
    yrows += f"<tr><td>{y}</td><td class='{'p' if s.iloc[-1]/e0-1>0 else 'n'}'>{pct(s.iloc[-1]/e0-1)}</td></tr>"
    prev = s.iloc[-1]

# 图
fig, ax = plt.subplots(figsize=(12, 5.5))
for lab, style in [("60/20/20(标准架构)", dict(lw=2.2, color="#c0392b")),
                   ("100%bp_ep(对照)", dict(lw=1.2, color="#1f4e9c", ls="--")),
                   ("50/30/20", dict(lw=1.0, color="#7f8c8d"))]:
    ax.plot(pd.to_datetime(pd.Series(cal).astype(str)), curves[lab], label=lab, **style)
sse = pd.read_sql_query if False else None
ax.set_yscale("log"); ax.set_ylabel("净值(对数, 起点1.0)")
ax.set_title("60/20/20 统一账户(按腿分仓口径) vs bp_ep单腿 2016-2026")
ax.legend(); ax.grid(alpha=.3)
fig.tight_layout(); fig.savefig(os.path.join(CH, "r3_combo.png"), dpi=110); plt.close(fig)

# 变体扫描表(R3)
scan = ""
for key, v in sorted(R3.items()):
    m = v["metrics"]; a = v.get("acc", {})
    scan += (f"<tr><td>{key.split('__')[0]}</td><td>{key.split('__')[1].replace('_lowconsol_main','M1').replace('_wfa_top20','M6')}</td>"
             f"<td>{m['final_equity']:,.0f}</td><td class='{'p' if m['cagr']>0 else 'n'}'>{pct(m['cagr'])}</td>"
             f"<td>{pct(m['mdd'])}</td><td>{pct(m['win_rate'])}</td>"
             f"<td>{a.get('tp_n','-')}</td><td>{a.get('cb_n','-')}</td>"
             f"<td>{a.get('n_signals',0):,}</td><td>{a.get('n',0):,}</td></tr>")

b64 = lambda p: base64.b64encode(open(p, "rb").read()).decode()
html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>第三轮 60/20/20 架构报告 20261022</title>
<style>body{{font-family:"Microsoft YaHei",SimHei,sans-serif;margin:24px;max-width:1150px;color:#222}}
h1{{font-size:20px;border-bottom:3px solid #1f4e9c;padding-bottom:8px}}h2{{font-size:16px;color:#1f4e9c;border-left:5px solid #1f4e9c;padding-left:8px;margin-top:28px}}
table{{border-collapse:collapse;font-size:12px;margin:8px 0;width:100%}}th,td{{border:1px solid #bbb;padding:3px 6px;text-align:right}}
th{{background:#1f4e9c;color:#fff}}td.p{{color:#c0392b}}td.n{{color:#27ae60}}
.note{{background:#fff8e1;border:1px solid #e6c200;padding:8px 12px;font-size:12px;margin:8px 0}}</style></head><body>
<h1>屠龙系统 · 第三轮迭代复测：60/20/20 统一账户架构（2026-10-22）</h1>
<p>腿构成: bp_ep官方机器C腿(真实约束, 未复权口径) 60% | M6 wfa@liq2000(自动选优={TAG_M6}) 20% | M1相位+liq2000+ATR(自动选优={TAG_M1}) 20%</p>
<div class="note"><b>口径标注</b>: 组合净值=按腿分仓各自复利的加权净值(未启动腿=1.0),
与生产文档"统一账户撮合"(共享现金/槽位)存在口径差异, 该差异留待四轮用撮合引擎复刻;
bp_ep腿为stock_daily_meta未复权价比例口径(与官方机器一致)。</div>

<h2>1. 权重结构对比（同三腿）</h2>
<table><tr><th>权重结构</th><th>期末(元)</th><th>CAGR</th><th>MDD</th><th>Sharpe</th><th>MDD谷底日</th></tr>{rows}</table>
<h2>2. 60/20/20 分年净值收益</h2>
<table><tr><th>年份</th><th>年度收益</th></tr>{yrows}</table>
<img src="data:image/png;base64,{b64(os.path.join(CH,'r3_combo.png'))}">

<h2>3. 第三轮扫描（组合叠加/M6 TopN/ATR k）</h2>
<table><tr><th>变体</th><th>模型</th><th>期末(元)</th><th>CAGR</th><th>MDD</th><th>胜率</th><th>止盈</th><th>熔断</th><th>信号数</th><th>交易数</th></tr>{scan}</table>
<p class="note">参照: 一轮基线 M1 -9.9%/-79.9% / M6 +9.2%/-46.5%; 二轮单项 R2phaseON M1 -2.4%/-45.5%, R2liq2000 M6 +14.0%/-45.2%。</p>

<h2>4. 第四轮清单</h2>
<ol>
<li>统一账户撮合引擎复刻(共享现金/50槽位口径), 验证分仓近似与撮合口径差异(验收: 差异CAGR&lt;2pp)</li>
<li>bp_ep腿复权口径升级(qfq重跑A1-A3分解), 验收=与未复权口径结论方向一致</li>
<li>相位开关前瞻队列注册(须主人裁决, 零交易权仅呈报)</li>
<li>60/20/20月度再平衡vs漂移不复平衡对照(验收: 两口径CAGR差量化)</li>
<li>M6行业约束变体(单行业≤4, 对齐bp_ep发现), 验收=CAGR不降且MDD改善</li>
<li>熔断参数敏感性(20/25/30档位), 验收=MDD-CAGR前沿曲线</li>
</ol>
<div class="note"><b>风险提示</b>: 历史理论回测绩效不代表未来实盘收益; bp_ep含未复权口径与样本内成分;
组合架构未经统一账户撮合验证前不得进入实盘配置。</div>
</body></html>"""
out = os.path.join(BASE, "report_round3_20261022.html")
open(out, "w", encoding="utf-8").write(html)
import shutil
try:
    shutil.copy(out, os.path.join(VIZ_DIR, "report_round3_20261022.html"))
    print("VIZ copy ok")
except Exception as e:
    print("VIZ copy fail:", e)
print("ROUND3 REPORT DONE:", out)
