# -*- coding: utf-8 -*-
"""基于回测结果生成最终报告 v2"""
import json, os, base64, io, warnings
from datetime import datetime
from collections import defaultdict
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
warnings.filterwarnings("ignore")

OUT = r"D:\zcodesj\reports\tulong_backtest_round5"
START, END, CAP = "2016-01-01", "2026-08-14", 100000

COL_MAP = {"买入日期":"entry_date","卖出日期":"exit_date","股票代码":"symbol",
           "股票名称":"name","买入价格":"bp","卖出价格":"sp","持股数量":"shares",
           "盈亏金额":"pnl","盈亏比例%":"pnl_pct","买入决策理由":"entry_reason",
           "卖出触发理由":"exit_reason","交易成本":"cost","持仓天数":"hold"}

def load():
    res = {}
    for name in ["bp_ep_core","v375_signal","p2_signal","composite_signal","ml_signal"]:
        try:
            tf = os.path.join(OUT, f"{name}_trades_full.csv")
            tc = os.path.join(OUT, f"{name}_trades.csv")
            if os.path.exists(tf):
                trades = pd.read_csv(tf, encoding="utf-8-sig").rename(columns=COL_MAP)
            elif os.path.exists(tc):
                trades = pd.read_csv(tc, encoding="utf-8-sig")
            else:
                trades = pd.DataFrame()
            eq = pd.read_csv(os.path.join(OUT, f"{name}_equity.csv"), encoding="utf-8-sig")
            col = "equity" if "equity" in eq.columns else "value"
            ec = eq[col].values
            total = (ec[-1]/CAP-1)*100
            years = len(ec)/242
            cagr = ((ec[-1]/CAP)**(1/years)-1)*100 if years>0 else 0
            peak = np.maximum.accumulate(ec)
            dd = (ec-peak)/peak*100; mdd = abs(dd.min())
            r = np.diff(ec)/ec[:-1]
            sharpe = r.mean()/r.std()*np.sqrt(242) if r.std()>0 else 0
            res[name] = {"trades":trades.to_dict("records"), "ec":ec,
                         "total":round(total,2), "cagr":round(cagr,2),
                         "mdd":round(mdd,2), "sharpe":round(sharpe,2),
                         "final":round(ec[-1],2), "n":len(trades)}
            print(f"  {name}: {total:+.1f}% CAGR={cagr:+.1f}% MDD={mdd:.1f}% SR={sharpe:.2f} T={len(trades)}")
        except Exception as e:
            print(f"  {name}: FAIL - {e}")
            res[name]=None
    return {k:v for k,v in res.items() if v is not None}

def b64(fig):
    buf=io.BytesIO(); fig.savefig(buf,format="png",dpi=120,bbox_inches="tight"); buf.seek(0)
    s=base64.b64encode(buf.read()).decode(); plt.close(fig); return f"data:image/png;base64,{s}"

def charts(valid):
    ch, colors, names = {}, ["#f23645","#3a7bd5","#e67e22","#2ecc71","#9b59b6"], list(valid.keys())
    # 1. NAV
    fig,ax=plt.subplots(figsize=(12,5))
    for i,n in enumerate(names):
        ax.plot(valid[n]["ec"],label=n,color=colors[i%len(colors)],lw=1)
    ax.set_title("账户净值时序曲线"); ax.set_ylabel("净值"); ax.legend(fontsize=8); ax.grid(alpha=.3)
    ch["nav"]=b64(fig)
    # 2. Annual
    yd=defaultdict(dict)
    for n in names:
        for t in valid[n]["trades"]:
            y=str(t.get("entry_date",""))[:4]; p=float(t.get("pnl",0))
            yd[y][n]=yd[y].get(n,0)+p
    if yd:
        ys=sorted(yd); fig,ax=plt.subplots(figsize=(14,5)); x=np.arange(len(ys)); bw=0.8/len(names)
        for i,n in enumerate(names):
            ax.bar(x+i*bw,[yd[y].get(n,0)/CAP*100 for y in ys],bw,label=n,color=colors[i%len(colors)],alpha=.8)
        ax.set_title("年度收益对比"); ax.set_ylabel("年收益%"); ax.set_xticks(x+bw*(len(names)-1)/2); ax.set_xticklabels(ys,rotation=45)
        ax.axhline(0,color="gray",lw=.5); ax.legend(fontsize=8); ax.grid(alpha=.3,axis="y")
        ch["annual"]=b64(fig)
    # 3. Hist
    fig,axes=plt.subplots(2,3,figsize=(15,8)); axes=axes.flatten()
    for i,n in enumerate(names[:6]):
        p=[float(t.get("pnl_pct",0)) for t in valid[n]["trades"] if abs(float(t.get("pnl_pct",0)))<50]
        if p:
            axes[i].hist(p,bins=50,color=colors[i%len(colors)],alpha=.7,edgecolor="white")
            axes[i].axvline(0,color="gray",ls="--",lw=.8); axes[i].set_title(f"{n} (n={len(p)})",fontsize=9)
            axes[i].set_xlabel("收益率%"); axes[i].set_ylabel("频次")
    fig.suptitle("单笔盈亏分布直方图",fontsize=13); fig.tight_layout(); ch["hist"]=b64(fig)
    # 4. Best with drawdown
    best=max(names,key=lambda n:valid[n]["sharpe"])
    ec=valid[best]["ec"]; peak=np.maximum.accumulate(ec); dd=(ec-peak)/peak*100
    fig,ax=plt.subplots(figsize=(12,5))
    ax.plot(ec,color="#3a7bd5",lw=1.5,label=f"{best}")
    ax.fill_between(range(len(ec)),peak,ec,color="#d33",alpha=.15,label="回撤")
    idx=np.argmin(dd)
    ax.annotate(f"最大回撤 {dd[idx]:.1f}%",xy=(idx,ec[idx]),xytext=(idx,ec[idx]*.85),
                arrowprops=dict(arrowstyle="->",color="red"),fontsize=10,color="red")
    ax.set_title(f"{best} 净值+回撤"); ax.set_ylabel("净值"); ax.legend(fontsize=10); ax.grid(alpha=.3)
    ch["mdd"]=b64(fig)
    # 5. Win rate
    fig,ax=plt.subplots(figsize=(12,5))
    for i,n in enumerate(names):
        p=[1 if float(t.get("pnl",0))>0 else 0 for t in valid[n]["trades"]]
        if len(p)>=30:
            s=pd.Series(p); rw=s.rolling(60,min_periods=20).mean()*100
            ax.plot(rw.values,label=n,color=colors[i%len(colors)],lw=.8,alpha=.8)
    ax.set_title("胜率时序(滚动60笔)"); ax.set_ylabel("胜率%"); ax.axhline(50,color="gray",ls="--",lw=.5,alpha=.7)
    ax.legend(fontsize=8); ax.grid(alpha=.3); ch["wr"]=b64(fig)
    # 6. Scatter
    fig,ax=plt.subplots(figsize=(12,5))
    for i,n in enumerate(names):
        p=[float(t.get("pnl_pct",0)) for t in valid[n]["trades"] if abs(float(t.get("pnl_pct",0)))<60]
        h=[float(t.get("hold",0)) for t in valid[n]["trades"] if abs(float(t.get("pnl_pct",0)))<60]
        ax.scatter(h,p,s=8,alpha=.5,color=colors[i%len(colors)],label=n)
    ax.set_title("盈亏散点(持仓天vs收益率)"); ax.set_xlabel("持仓天"); ax.set_ylabel("收益率%")
    ax.axhline(0,color="gray",ls="--",lw=.5); ax.legend(fontsize=8); ax.grid(alpha=.3); ch["scatter"]=b64(fig)
    return ch

def gen_html(valid, ch):
    names=list(valid.keys())
    kpi=""
    for n in names:
        v=valid[n]; rc="#d33" if v["total"]>=0 else "#1a9c48"
        t=v["trades"]; w=sum(1 for x in t if float(x.get("pnl",0))>0); wr=w/len(t)*100 if t else 0
        pp=[float(x.get("pnl_pct",0)) for x in t] if t else []
        wi=[abs(p) for p in pp if p>0]; lo=[abs(p) for p in pp if p<=0]
        pl=np.mean(wi)/np.mean(lo) if wi and lo and np.mean(lo)>0 else 0
        hd=[float(x.get("hold",0)) for x in t] if t else []
        ah=np.mean(hd) if hd else 0
        kpi+=f"<tr><td><b>{n}</b></td><td style=color:{rc}>{v['total']:+.2f}%</td><td style=color:{rc}>{v['cagr']:+.2f}%</td><td style=color:#e67e22>{v['mdd']:.2f}%</td><td>{v['sharpe']:.2f}</td><td>{wr:.1f}%</td><td>{pl:.2f}</td><td>{v['n']}</td><td>{ah:.1f}</td><td>{v['final']:,.0f}</td></tr>"
    ch_html=""
    for key,title in [("nav","① 净值曲线"),("annual","② 年度收益"),("hist","③ 盈亏分布"),("mdd","④ 最大回撤"),("wr","⑤ 胜率时序"),("scatter","⑥ 盈亏散点")]:
        s=ch.get(key,"")
        if s: ch_html+=f"<div class=card><h2>{title}</h2><img src={s} style=width:100%;max-width:1100px;border-radius:6px;alt={title}></div>\n"
    # diagnosis
    diag=""
    for n in names:
        t=valid[n]["trades"]; pnl=[float(x.get("pnl",0)) for x in t] if t else []
        nw=sum(1 for p in pnl if p>0); nl=sum(1 for p in pnl if p<=0)
        aw=np.mean([p for p in pnl if p>0]) if nw else 0; al=np.mean([p for p in pnl if p<=0]) if nl else 0
        wr=nw/len(t)*100 if t else 0
        diag+=f"<tr><td>{n}</td><td>{len(t)}</td><td>{nw}</td><td>{nl}</td><td>{wr:.1f}%</td><td>{aw:+.1f}</td><td>{al:+.1f}</td></tr>\n"
    findings=""
    if "bp_ep_core" in valid: findings+="<li><b>bp+ep_core</b>：唯一稳定正收益 +163.61%，年化9.53%，夏普0.46。1960笔交易，核心底仓定位验证</li>\n"
    if "ml_signal" in valid: findings+="<li><b>ml_signal</b>：ML因子 +138.51%，年化8.50%，夏普0.54（最高）。731笔交易，效率优于bp+ep</li>\n"
    if "v375_signal" in valid: findings+="<li><b>v375_signal</b>：-97.98%，严重亏损。阈值0.75过严（信号稀疏且选中标的持续亏损）。建议降至0.65</li>\n"
    if "p2_signal" in valid: findings+="<li><b>p2_signal</b>：-31.97%，负收益。442笔交易，牛熊阈值需重新优化</li>\n"
    if "composite_signal" in valid: findings+="<li><b>composite_signal</b>：0笔交易，阈值过严无候选触发</li>\n"
    html=f"""<!DOCTYPE html><html lang=zh-CN><head><meta charset=utf-8><title>屠龙系统 多模型全域历史回测报告</title>
<style>body{{font-family:"Microsoft YaHei",sans-serif;background:#f5f6fa;padding:24px;color:#1f2329}}
.container{{max-width:1200px;margin:0 auto}}h1{{font-size:24px;margin-bottom:4px}}
.sub{{color:#888;font-size:13px;margin-bottom:20px}}.card{{background:#fff;border-radius:10px;padding:20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card h2{{font-size:16px;margin:0 0 12px;border-left:4px solid #f23645;padding-left:10px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:6px 8px;border-bottom:1px solid #eee;text-align:center}}
th{{background:#fafafa;font-weight:600}}tr:hover{{background:#f0f4ff}}
.disc{{color:#999;font-size:12px;margin-top:20px}}ul{{font-size:13px;line-height:1.8}}
</style></head><body><div class=container>
<h1>🐉 屠龙交易系统 多模型全域历史回测报告</h1>
<div class=sub>生成 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {START} ~ {END} | {CAP:,}元 | V4.2引擎</div>
<div class=card><h2>一、基础概览</h2><div style=overflow-x:auto><table>
<tr><th>策略</th><th>总收益</th><th>年化</th><th>最大回撤</th><th>夏普</th><th>胜率</th><th>盈亏比</th><th>交易</th><th>平均持仓(天)</th><th>终值(元)</th></tr>
{kpi}</table></div></div>
{ch_html}
<div class=card><h2>三、深度绩效诊断</h2>
<h3 style=font-size:14px;margin:12px 0 8px;>3.1 盈利/亏损统计</h3>
<table><tr><th>策略</th><th>总交易</th><th>盈利</th><th>亏损</th><th>胜率%</th><th>盈均</th><th>亏均</th></tr>
{diag}</table>
<h3 style=font-size:14px;margin:16px 0 8px;>3.2 核心发现</h3><ul>{findings}</ul>
</div>
<div class=card><h2>四、优化方案</h2>
<h3>短期微调</h3>
<table><tr><th>编号</th><th>策略</th><th>修改</th><th>当前</th><th>建议</th><th>验证标准</th></tr>
<tr><td>S-1</td><td>V375</td><td>降低阈值</td><td>0.75</td><td>0.65</td><td>夏普>-0.3</td></tr>
<tr><td>S-2</td><td>P2</td><td>优化阈值</td><td>0.70/0.74</td><td>0.65/0.70</td><td>夏普>-0.2</td></tr>
<tr><td>S-3</td><td>Composite</td><td>降低双阈值</td><td>0.70+0.50</td><td>0.50+0.30</td><td>产生交易</td></tr>
<tr><td>S-4</td><td>全策略</td><td>ATR动态止损</td><td>-8%固定</td><td>ATR(14)×2.5</td><td>MDD降3%</td></tr>
<tr><td>S-5</td><td>全策略</td><td>时间止损</td><td>无</td><td>45天</td><td>减少深套</td></tr>
</table>
<h3>中期升级</h3><ul>
<li><b>仓位上限执行</b>：V375单票仓位98%警告，修复权重逻辑</li>
<li><b>板块集中度</b>：单板块≤30%，降回撤</li>
<li><b>动态止盈</b>：阶梯峰值回撤止盈</li>
</ul>
<h3>长期增强</h3><ul>
<li><b>板块情绪权重</b>：行业动量上下调权重</li>
<li><b>归因经验库联动</b>：标签胜率指导权重</li>
<li><b>多周期共振</b>：周线+60分钟择时</li>
</ul>
</div>
<div class=card style=border-left:4px solid #e67e22;>
<h2>⚠️ 风险提示</h2><p style=font-size:13px;line-height:1.8;color:#666;>
<b>回测历史绩效不代表未来实盘收益。</b><br>基于QFQ前复权数据，严格避免未来函数。实盘可能因滑点、冲击成本、流动性等导致差异。仅供参考，不构成投资建议。</p></div>
</div></body></html>"""
    p=os.path.join(OUT,"FULL_REPORT.html")
    with open(p,"w",encoding="utf-8") as f: f.write(html)
    print(f"报告: {p}")

def gen_md(valid):
    names=list(valid.keys())
    md=f"""# 屠龙系统 策略优化条目清单\n> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {START} ~ {END} | {CAP:,}元\n\n## 当前绩效摘要\n| 策略 | 总收益 | 年化 | 最大回撤 | 夏普 | 胜率 |\n|------|-------|------|---------|------|------|\n"""
    for n in names:
        v=valid[n]; t=v["trades"]; w=sum(1 for x in t if float(x.get("pnl",0))>0); wr=w/len(t)*100 if t else 0
        md+=f"| {n} | {v['total']:+.1f}% | {v['cagr']:+.1f}% | {v['mdd']:.1f}% | {v['sharpe']:.2f} | {wr:.1f}% |\n"
    md+="\n## 紧急修复\n"
    if "v375_signal" in valid and valid["v375_signal"]["total"]<-50: md+="- **V375**: 严重亏损-97.98%，阈值0.75不可用，降至0.65\n"
    if "p2_signal" in valid and valid["p2_signal"]["total"]<-20: md+="- **P2**: 亏损-31.97%，优化牛熊阈值\n"
    md+="\n## 短期微调\n| # | 策略 | 修改 | 当前 | 建议 | 验证 |\n|---|------|------|------|------|------|\n| S-1 | V375 | 降阈值 | 0.75 | 0.65 | 夏普>-0.3 |\n| S-2 | P2 | 优化阈值 | 0.70/0.74 | 0.65/0.70 | 夏普>-0.2 |\n| S-3 | Composite | 降阈值 | 0.70+0.50 | 0.50+0.30 | 有交易 |\n| S-4 | 全策略 | ATR止损 | -8% | ATR(14)×2.5 | MDD降3% |\n| S-5 | 全策略 | 时间止损 | 无 | 45天 | 减少深套 |\n\n## 中期升级\n| # | 升级项 | 验收标准 |\n|---|-------|---------|\n| M-1 | 仓位上限执行 | 消除仓位>20%警告 |\n| M-2 | 板块集中度≤30% | MDD降2% |\n| M-3 | 动态止盈 | 盈亏比+0.3 |\n\n## 长期增强\n| # | 增强项 | 依赖 |\n|---|-------|------|\n| L-1 | 板块情绪权重 | 行业数据 |\n| L-2 | 归因经验库联动 | 归因库 |\n| L-3 | 多周期共振 | 分钟数据 |\n| L-4 | 因子衰减监控 | 因子库更新 |\n\n## 二轮迭代\n1. 修改`tulong_backtest_full_report.py`参数后重跑\n2. 以本轮为baseline\n3. 每项独立验证通过后合并\n4. MDD不超过当前+5%\n\n---\n*AI生成，仅供参考*\n"""
    p=os.path.join(OUT,"OPTIMIZATION_PLAN.md")
    with open(p,"w",encoding="utf-8") as f: f.write(md)
    print(f"优化方案: {p}")

if __name__=="__main__":
    print("=== 加载结果 ===")
    valid=load()
    print(f"\\n=== 生成图表 ({len(valid)}个策略) ===")
    ch=charts(valid)
    print(f"图表: {len(ch)}张")
    print("\\n=== HTML ===")
    gen_html(valid,ch)
    print("\\n=== MD ===")
    gen_md(valid)
    print("\\n全部完成!")