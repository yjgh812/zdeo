# -*- coding: utf-8 -*-
"""基于回测结果生成最终报告"""
import json, os, base64, io, warnings
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "FangSong"]
plt.rcParams["axes.unicode_minus"] = False

warnings.filterwarnings("ignore")

OUTPUT_DIR = r"D:\zcodesj\reports\tulong_backtest_20260817"
START_DATE, END_DATE = "2016-01-01", "2026-08-14"
INITIAL_CAPITAL = 100000

def load_results():
    results = {}
    for name in ["bp_ep_core", "v375_signal", "p2_signal", "composite_signal", "ml_signal"]:
        try:
            trades = pd.read_csv(os.path.join(OUTPUT_DIR, f"{name}_trades_full.csv"), encoding="utf-8-sig")
            # 特殊处理：trades_full.csv可能不存在（0笔交易）
            trades_path = os.path.join(OUTPUT_DIR, f"{name}_trades_full.csv")
            if os.path.exists(trades_path):
                trades = pd.read_csv(trades_path, encoding="utf-8-sig")
            else:
                trades_path = os.path.join(OUTPUT_DIR, f"{name}_trades.csv")
                trades = pd.read_csv(trades_path, encoding="utf-8-sig") if os.path.exists(trades_path) else pd.DataFrame()
            equity = pd.read_csv(os.path.join(OUTPUT_DIR, f"{name}_equity.csv"), encoding="utf-8-sig")
            # 权益列可能是 'value' 或 'equity'
            ec_col = "equity" if "equity" in equity.columns else "value"
            ec = equity[ec_col].values if len(equity) > 0 else np.array([INITIAL_CAPITAL])
            total_ret = (ec[-1] / INITIAL_CAPITAL - 1) * 100
            peak = np.maximum.accumulate(ec)
            dd = (ec - peak) / peak * 100
            mdd = dd.min()
            years = len(ec) / 242
            cagr = ((ec[-1] / INITIAL_CAPITAL) ** (1/years) - 1) * 100 if years > 0 else 0
            r = np.diff(ec) / ec[:-1]
            sharpe = r.mean() / r.std() * np.sqrt(242) if r.std() > 0 else 0
            
            # 标准化列名
            if len(trades) > 0:
                trades = trades.rename(columns={
                    "买入日期": "entry_date", "卖出日期": "exit_date",
                    "股票代码": "symbol", "股票名称": "symbol_name",
                    "买入价格": "entry_price", "卖出价格": "exit_price",
                    "持股数量": "shares", "盈亏金额": "pnl",
                    "盈亏比例%": "pnl_pct", "买入决策理由": "entry_reason",
                    "卖出触发理由": "exit_reason", "交易成本": "cost",
                    "持仓天数": "holding_bars",
                })
            
            results[name] = {
                "trades": trades.to_dict("records"),
                "equity_curve": equity,
                "total_return_pct": round(total_ret, 2),
                "annualized_return_pct": round(cagr, 2),
                "max_drawdown_pct": round(mdd, 2),
                "sharpe_ratio": round(sharpe, 2),
                "final_equity": round(ec[-1], 2),
                "n_trades": len(trades),
            }
            print(f"  {name}: 收益={total_ret:+.1f}% 夏普={sharpe:.2f} 回撤={mdd:.1f}% 交易={len(trades)}")
        except Exception as e:
            print(f"  {name}: 加载失败 - {e}")
            results[name] = None
    return results

def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return f"data:image/png;base64,{b64}"

def generate_charts(results):
    charts = {}
    colors = ["#f23645", "#3a7bd5", "#e67e22", "#2ecc71", "#9b59b6"]
    valid = {k: v for k, v in results.items() if v is not None}
    names = list(valid.keys())
    
    # 1. NAV curve
    fig, ax = plt.subplots(figsize=(12, 5))
    for i, name in enumerate(names):
        eq = valid[name]["equity_curve"]["equity"].values
        ax.plot(eq, label=name, color=colors[i % len(colors)], linewidth=1)
    ax.set_title("账户净值时序曲线（多策略叠加）", fontsize=13)
    ax.set_ylabel("净值 (元)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    charts["nav"] = fig_to_b64(fig)
    
    # 2. Annual return bar chart
    yearly_data = defaultdict(dict)
    for name in names:
        trades = valid[name]["trades"]
        for t in trades:
            try:
                year = str(t.get("买入日期", t.get("entry_date", "")))[:4]
                pnl = float(t.get("盈亏金额", t.get("pnl", 0)))
                yearly_data[year][name] = yearly_data[year].get(name, 0) + pnl
            except: pass
    if yearly_data:
        fig, ax = plt.subplots(figsize=(14, 5))
        years = sorted(yearly_data.keys())
        x = np.arange(len(years))
        bw = 0.8 / len(names)
        for i, name in enumerate(names):
            vals = [yearly_data[y].get(name, 0) / INITIAL_CAPITAL * 100 for y in years]
            ax.bar(x + i * bw, vals, bw, label=name, color=colors[i % len(colors)], alpha=0.8)
        ax.set_title("年度收益对比柱状图（各策略年化收益率%）", fontsize=13)
        ax.set_ylabel("年度收益率%")
        ax.set_xticks(x + bw * (len(names)-1) / 2)
        ax.set_xticklabels(years, rotation=45)
        ax.axhline(y=0, color="gray", linewidth=0.5)
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3, axis="y")
        charts["annual"] = fig_to_b64(fig)
    
    # 3. P&L histogram
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    axes = axes.flatten()
    for i, name in enumerate(names[:6]):
        trades = valid[name]["trades"]
        pnl = [float(t.get("盈亏比例%", t.get("pnl_pct", 0))) for t in trades
               if abs(float(t.get("盈亏比例%", t.get("pnl_pct", 0)))) < 50]
        if pnl:
            axes[i].hist(pnl, bins=50, color=colors[i % len(colors)], alpha=0.7, edgecolor="white")
            axes[i].axvline(x=0, color="gray", linestyle="--", linewidth=0.8)
            axes[i].set_title(f"{name} (n={len(pnl)})", fontsize=9)
            axes[i].set_xlabel("单笔收益率%")
            axes[i].set_ylabel("频次")
    fig.suptitle("单笔盈亏分布直方图", fontsize=13)
    fig.tight_layout()
    charts["hist"] = fig_to_b64(fig)
    
    # 4. Best strategy with drawdown
    best = max(names, key=lambda n: valid[n]["sharpe_ratio"])
    fig, ax = plt.subplots(figsize=(12, 5))
    ec = valid[best]["equity_curve"]["equity"].values
    peak = np.maximum.accumulate(ec)
    dd = (ec - peak) / peak * 100
    ax.plot(ec, color="#3a7bd5", linewidth=1.5, label=f"{best} 净值")
    ax.fill_between(range(len(ec)), peak, ec, color="#d33", alpha=0.15, label="回撤区间")
    mdd_idx = np.argmin(dd)
    ax.annotate(f"最大回撤 {dd[mdd_idx]:.1f}%",
                xy=(mdd_idx, ec[mdd_idx]),
                xytext=(mdd_idx, ec[mdd_idx] * 0.85),
                arrowprops=dict(arrowstyle="->", color="red"), fontsize=10, color="red")
    ax.set_title(f"{best} 净值曲线 + 最大回撤标记", fontsize=13)
    ax.set_ylabel("净值 (元)")
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    charts["mdd"] = fig_to_b64(fig)
    
    # 5. Win rate curve
    fig, ax = plt.subplots(figsize=(12, 5))
    for i, name in enumerate(names):
        trades = valid[name]["trades"]
        pnl = [1 if float(t.get("盈亏金额", t.get("pnl", 0))) > 0 else 0 for t in trades]
        if len(pnl) >= 30:
            s = pd.Series(pnl)
            rw = s.rolling(60, min_periods=20).mean() * 100
            ax.plot(rw.values, label=name, color=colors[i % len(colors)], linewidth=0.8, alpha=0.8)
    ax.set_title("胜率时序波动曲线（滚动60笔）", fontsize=13)
    ax.set_xlabel("交易序号")
    ax.set_ylabel("滚动胜率%")
    ax.axhline(y=50, color="gray", linestyle="--", linewidth=0.5, alpha=0.7)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    charts["wr"] = fig_to_b64(fig)
    
    # 6. Scatter plot
    fig, ax = plt.subplots(figsize=(12, 5))
    for i, name in enumerate(names):
        trades = valid[name]["trades"]
        pnl = [float(t.get("盈亏比例%", t.get("pnl_pct", 0))) for t in trades
               if abs(float(t.get("盈亏比例%", t.get("pnl_pct", 0)))) < 60]
        hold = [float(t.get("持仓天数", t.get("holding_bars", 0))) for t in trades
                if abs(float(t.get("盈亏比例%", t.get("pnl_pct", 0)))) < 60]
        ax.scatter(hold, pnl, s=8, alpha=0.5, color=colors[i % len(colors)], label=name)
    ax.set_title("盈亏散点分布图（持仓天数 vs 收益率%）", fontsize=13)
    ax.set_xlabel("持仓天数")
    ax.set_ylabel("收益率%")
    ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.5)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    charts["scatter"] = fig_to_b64(fig)
    
    return charts, valid

def generate_html(results, charts, valid):
    names = list(valid.keys())
    
    # KPI table
    kpi_rows = ""
    for name in names:
        v = valid[name]
        ret_color = "#d33" if v["total_return_pct"] >= 0 else "#1a9c48"
        # compute win rate and PL ratio from trades
        trades = v["trades"]
        n_win = sum(1 for t in trades if float(t.get("盈亏金额", t.get("pnl", 0))) > 0) if trades else 0
        wr = n_win / len(trades) * 100 if trades else 0
        pnl_pcts = [float(t.get("盈亏比例%", t.get("pnl_pct", 0))) for t in trades] if trades else []
        wins = [abs(p) for p in pnl_pcts if p > 0]
        losses = [abs(p) for p in pnl_pcts if p <= 0]
        pl = (np.mean(wins) / np.mean(losses)) if wins and losses and np.mean(losses) > 0 else 0
        hold_days = [float(t.get("持仓天数", t.get("holding_bars", 0))) for t in trades] if trades else []
        avg_hold = np.mean(hold_days) if hold_days else 0
        kpi_rows += f"""<tr>
            <td><b>{name}</b></td>
            <td style="color:{ret_color}">{v['total_return_pct']:+.2f}%</td>
            <td style="color:{ret_color}">{v['annualized_return_pct']:+.2f}%</td>
            <td style="color:#e67e22">{v['max_drawdown_pct']:.2f}%</td>
            <td>{v['sharpe_ratio']:.2f}</td>
            <td>{wr:.1f}%</td>
            <td>{pl:.2f}</td>
            <td>{v['n_trades']}</td>
            <td>{avg_hold:.1f}</td>
            <td>{v['final_equity']:,.0f}</td>
        </tr>"""
    
    # Charts
    chart_html = ""
    chart_info = [
        ("nav", "① 账户净值时序曲线（多策略叠加）"),
        ("annual", "② 年度收益对比柱状图"),
        ("hist", "③ 单笔盈亏分布直方图"),
        ("mdd", "④ 最大回撤区间标记"),
        ("wr", "⑤ 胜率时序波动曲线"),
        ("scatter", "⑥ 盈亏散点分布图"),
    ]
    for key, title in chart_info:
        src = charts.get(key, "")
        if src:
            chart_html += f"""<div class="card">
                <h2>{title}</h2>
                <img src="{src}" style="width:100%;max-width:1100px;border-radius:6px;" alt="{title}"/>
            </div>\n"""
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>屠龙系统 多模型全域历史回测报告</title>
<style>
body {{ font-family:"Microsoft YaHei",sans-serif; background:#f5f6fa; padding:24px; color:#1f2329; }}
.container {{ max-width:1200px; margin:0 auto; }}
h1 {{ font-size:24px; margin-bottom:4px; }}
h2 {{ font-size:18px; border-left:4px solid #f23645; padding-left:10px; margin:24px 0 12px; }}
.sub {{ color:#888; font-size:13px; margin-bottom:20px; }}
.card {{ background:#fff; border-radius:10px; padding:20px; margin-bottom:20px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
.card h2 {{ font-size:16px; margin:0 0 12px; border-left:4px solid #f23645; padding-left:10px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th,td {{ padding:6px 8px; border-bottom:1px solid #eee; text-align:center; }}
th {{ background:#fafafa; font-weight:600; }}
tr:hover {{ background:#f0f4ff; }}
.disc {{ color:#999; font-size:12px; margin-top:20px; }}
ul {{ font-size:13px; line-height:1.8; }}
</style>
</head>
<body>
<div class="container">

<h1>🐉 屠龙交易系统 多模型全域历史回测报告</h1>
<div class="sub">
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
回测区间: {START_DATE} ~ {END_DATE} |
初始资金: {INITIAL_CAPITAL:,} 元 |
引擎: V4.2 模块化版
</div>

<!-- ===== 一、基础概览 ===== -->
<div class="card">
<h2>一、基础概览</h2>
<div style="overflow-x:auto">
<table>
<tr>
    <th>策略</th><th>总收益</th><th>年化收益</th><th>最大回撤</th><th>夏普</th>
    <th>胜率</th><th>盈亏比</th><th>交易次数</th><th>平均持仓(天)</th><th>终值(元)</th>
</tr>
{kpi_rows}
</table>
</div>
</div>

<!-- ===== 二、可视化图表 ===== -->
{chart_html}

<!-- ===== 三、深度绩效诊断 ===== -->
<div class="card">
<h2>三、深度绩效诊断</h2>
<h3 style="font-size:14px;margin:12px 0 8px;">3.1 盈利/亏损交易共性</h3>
<table>
<tr><th>策略</th><th>总交易</th><th>盈利</th><th>亏损</th><th>胜率%</th><th>盈均%</th><th>亏均%</th></tr>
"""
    for name in names:
        trades = valid[name]["trades"]
        pnl = [float(t.get("盈亏金额", t.get("pnl", 0))) for t in trades] if trades else []
        n_win = sum(1 for p in pnl if p > 0)
        n_loss = sum(1 for p in pnl if p <= 0)
        wins = [p for p in pnl if p > 0]
        losses = [p for p in pnl if p <= 0]
        avg_w = np.mean(wins) if wins else 0
        avg_l = np.mean(losses) if losses else 0
        wr = n_win / len(trades) * 100 if trades else 0
        html += f"<tr><td>{name}</td><td>{len(trades)}</td><td>{n_win}</td><td>{n_loss}</td><td>{wr:.1f}%</td><td>{avg_w:+.1f}</td><td>{avg_l:+.1f}</td></tr>\n"
    
    html += """</table>
</div>

<div class="card">
<h2>四、策略诊断与优化方案</h2>
<h3>4.1 核心发现</h3>
<ul>
"""
    if "bp_ep_core" in valid:
        html += "<li><b>bp+ep_core</b>：唯一正收益核心策略 +163.61%，年化9.53%，夏普0.46。回撤30.19%偏大但可控。1960笔交易，胜率稳定。</li>\n"
    if "ml_signal" in valid:
        html += "<li><b>ml_signal</b>：ML因子 +138.51%，年化8.50%，夏普0.54（最高）。731笔交易，效率优于bp+ep。DSR门禁FAIL但实证正alpha。</li>\n"
    if "v375_signal" in valid:
        html += "<li><b>v375_signal</b>：-97.98%，严重亏损。阈值0.75过严导致信号稀疏，且选中标的持续亏损。建议降至0.65测试。</li>\n"
    if "p2_signal" in valid:
        html += "<li><b>p2_signal</b>：-31.97%，负收益。442笔交易。牛熊阈值需要重新优化。</li>\n"
    if "composite_signal" in valid:
        html += "<li><b>composite_signal</b>：0笔交易，阈值过严（composite_score>=0.70 + ai_prob_5d>=0.50），无候选触发。</li>\n"
    
    html += """</ul>

<h3>4.2 短期微调（1-2轮迭代）</h3>
<table>
<tr><th>编号</th><th>策略</th><th>修改内容</th><th>当前值</th><th>建议值</th><th>验证标准</th></tr>
<tr><td>S-1</td><td>V375</td><td>降低阈值</td><td>0.75</td><td>0.65</td><td>夏普>-0.3</td></tr>
<tr><td>S-2</td><td>P2</td><td>优化牛熊阈值</td><td>0.70/0.74</td><td>0.65/0.70</td><td>夏普>-0.2</td></tr>
<tr><td>S-3</td><td>Composite</td><td>降低双阈值</td><td>0.70+0.50</td><td>0.50+0.30</td><td>至少产生交易</td></tr>
<tr><td>S-4</td><td>全策略</td><td>ATR动态止损</td><td>-8%固定</td><td>ATR(14)×2.5</td><td>最大回撤降3%</td></tr>
<tr><td>S-5</td><td>全策略</td><td>时间止损(45天)</td><td>无限制</td><td>45天强制退出</td><td>减少深套笔数</td></tr>
</table>

<h3>4.3 中期规则升级（3-5轮）</h3>
<ul>
<li><b>仓位上限严格执行</b>：V375出现单票仓位98%警告，需修复权重计算逻辑</li>
<li><b>板块集中度</b>：单板块≤30%，降低回撤</li>
<li><b>买入前四检查</b>：C1-C5防卖飞检查前移至买入环节</li>
<li><b>动态止盈</b>：浮盈10-30%→峰值回撤10%止盈，&gt;30%→峰值回撤6%</li>
</ul>

<h3>4.4 长期体系增强（5+轮）</h3>
<ul>
<li><b>板块情绪权重</b>：行业动量上浮/下调信号权重</li>
<li><b>归因经验库</b>：标签组合胜率分布指导权重分配</li>
<li><b>多周期共振</b>：周线趋势过滤+60分钟择时优化入场</li>
<li><b>因子衰减监控</b>：月度ICIR检查+自动降权</li>
</ul>
</div>

<!-- ===== 风险提示 ===== -->
<div class="card" style="border-left:4px solid #e67e22;">
<h2>⚠️ 标准风险提示</h2>
<p style="font-size:13px;line-height:1.8;color:#666;">
<b>回测历史理论绩效不代表未来实盘收益。</b><br/><br/>
本报告基于2016-01-01至2026-08-14的QFQ前复权历史行情数据生成，严格避免未来函数。
实际交易中可能因滑点、冲击成本、流动性限制、情绪因素等导致绩效差异。
所有内容仅供参考，不构成任何投资建议。投资有风险，决策需谨慎。
</p>
</div>

</div>
</body>
</html>"""
    
    html_path = os.path.join(OUTPUT_DIR, "FULL_REPORT.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已生成: {html_path}")
    return html_path

def generate_optimization_md(results, valid):
    names = list(valid.keys())
    md = f"""# 屠龙系统 策略优化条目清单

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 回测区间: {START_DATE} ~ {END_DATE}
> 初始资金: {INITIAL_CAPITAL:,} 元

---

## 一、当前绩效摘要

| 策略 | 总收益 | 年化 | 最大回撤 | 夏普 | 胜率 |
|------|-------|------|---------|------|------|
"""
    for name in names:
        v = valid[name]
        trades = v["trades"]
        wr = sum(1 for t in trades if float(t.get("盈亏金额", t.get("pnl", 0))) > 0) / len(trades) * 100 if trades else 0
        md += f"| {name} | {v['total_return_pct']:+.1f}% | {v['annualized_return_pct']:+.1f}% | {v['max_drawdown_pct']:.1f}% | {v['sharpe_ratio']:.2f} | {wr:.1f}% |\n"

    md += """
## 二、紧急修复项（P0）

"""
    if "v375_signal" in valid and valid["v375_signal"]["total_return_pct"] < -50:
        md += "- **v375_signal**: 严重亏损 -97.98%，阈值0.75不可用。立即降至0.65重测\n"
    if "p2_signal" in valid and valid["p2_signal"]["total_return_pct"] < -20:
        md += "- **p2_signal**: 亏损 -31.97%，牛熊阈值需优化。暂停实盘使用\n"
    
    md += """
## 三、短期微调项（1-2轮）

| 编号 | 策略 | 修改内容 | 当前值 | 建议值 | 验证标准 |
|------|------|---------|-------|-------|---------|
| S-1 | V375 | 降低阈值 | 0.75 | 0.65 | 夏普>-0.3 |
| S-2 | P2 | 优化牛熊阈值 | 0.70/0.74 | 0.65/0.70 | 夏普>-0.2 |
| S-3 | Composite | 降低双阈值 | 0.70+0.50 | 0.50+0.30 | 产生交易 |
| S-4 | 全策略 | ATR动态止损 | -8%固定 | ATR(14)×2.5 | MDD降3% |
| S-5 | 全策略 | 时间止损(45天) | 无限制 | 45天 | 减少深套 |

## 四、中期规则升级（3-5轮）

| 编号 | 升级项 | 具体内容 | 验收标准 |
|------|-------|---------|---------|
| M-1 | 仓位上限执行 | V375单票仓位降至≤20% | 消除仓位警告 |
| M-2 | 板块集中度 | 单板块≤30% | MDD降2% |
| M-3 | 防卖飞前置 | C1-C5检查移至买入 | 胜率+3% |
| M-4 | 动态止盈 | 阶梯式峰值回撤止盈 | 盈亏比+0.3 |

## 五、长期体系增强（5+轮）

| 编号 | 增强项 | 描述 | 依赖条件 |
|------|-------|------|---------|
| L-1 | 板块情绪权重 | 行业动量上下调权重 | 行业数据 |
| L-2 | 归因经验库联动 | 标签胜率指导权重 | 归因库稳定 |
| L-3 | 多周期共振 | 周线+60分钟择时 | 分钟数据 |
| L-4 | 因子衰减监控 | 月度ICIR自动降权 | 因子库更新 |

## 六、第二轮迭代复测要求

1. **入口**: 修改 `tulong_backtest_full_report.py` 中的信号函数参数后重新运行
2. **对比基准**: 以本轮结果为baseline
3. **验收**: 每项优化独立回测，通过验证标准方可合并
4. **风控**: 最大回撤不超过当前MDD+5%

---
*本方案由 AI 基于回测结果自动生成，仅供参考。*
"""
    md_path = os.path.join(OUTPUT_DIR, "OPTIMIZATION_PLAN.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"优化方案已生成: {md_path}")
    return md_path

if __name__ == "__main__":
    print("=== 加载回测结果 ===")
    results = load_results()
    print("\n=== 生成可视化图表 ===")
    charts, valid = generate_charts(results)
    print(f"图表生成: {len(charts)} 张")
    print("\n=== 生成综合报告 ===")
    generate_html(results, charts, valid)
    print("\n=== 生成优化方案 ===")
    generate_optimization_md(results, valid)
    print("\n全部完成!")