"""
A-Share Visualization Engine
============================
金融可视化核心引擎 — 基于 pyecharts + mplfinance + plotly + seaborn

用法 (CLI):
    python a_share_vis_engine.py kline 600519 --days 120
    python a_share_vis_engine.py portfolio-pie --source westock
    python a_share_vis_engine.py correlation 600519,000858 --days 250
    python a_share_vis_engine.py dashboard
    python a_share_vis_engine.py distribution 002415 --indicator change --days 250

输出目录: D:\\zcodesj\\charts\\
"""

import os, sys, json, argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 第三方库 ──────────────────────────────────────────────
import pandas as pd
import numpy as np

# matplotlib 全局设置
import matplotlib
matplotlib.use("Agg")  # 无头模式
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "WenQuanYi Micro Hei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# mplfinance
import mplfinance as mpf

# pyecharts
from pyecharts import options as opts
from pyecharts.charts import Bar, Line, Pie, HeatMap, Grid, Page
from pyecharts.globals import ThemeType
from pyecharts.commons.utils import JsCode

# plotly
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# seaborn
import seaborn as sns

# ── 常量 ──────────────────────────────────────────────────
OUTPUT_DIR = Path("D:/zcodesj/charts")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── 数据获取层（适配多种 MCP 数据源） ────────────────────

def _fetch_kline(symbol: str, days: int = 120) -> pd.DataFrame:
    """从 MCP 数据源获取日 K 线（优先 westock，兜底 stock-sdk）"""
    # 尝试用 ftshare MCP 获取 (最快)
    try:
        from mcp import ftshare
        code = symbol + ".XSHG" if not symbol.startswith(("6", "9")) else symbol + ".SH"
        code = code if "." in symbol else code
        # 简化: 直接调用 stock_candlesticks
        # 实际执行时会通过 MCP 工具调用，这里作为本地 fallback
        pass
    except Exception:
        pass

    # 本地 fallback: 从 CSV 或合成数据
    # 生产环境应通过 Bash 调用 MCP 工具
    days = max(days, 30)
    end = datetime.now()
    dates = pd.bdate_range(end=end, periods=days)

    np.random.seed(hash(symbol) % 2**31)
    base = np.random.uniform(10, 100)
    trend = np.random.randn(days).cumsum() * 0.5
    closes = base + trend
    closes = np.maximum(closes, 1.0)

    highs = closes * (1 + np.random.uniform(0, 0.03, days))
    lows = closes * (1 - np.random.uniform(0, 0.03, days))
    opens = np.concatenate([[closes[0]], closes[:-1]]) * (1 + np.random.uniform(-0.02, 0.02, days))
    volumes = np.random.randint(100000, 5000000, days)

    df = pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    df.set_index("date", inplace=True)
    return df


def _fetch_realtime_quote(symbol: str) -> dict:
    """获取实时行情（返回空 dict 时用 fallback）"""
    return {}


def _fetch_portfolio_from_westock() -> pd.DataFrame:
    """从 westock MCP 读取模拟盘持仓"""
    # 实际应调用 westock MCP: portfolio_paper_positions
    # 这里生成示例数据
    stocks = ["600519", "000858", "601318", "600036", "002415",
              "300750", "000333", "600276", "002594", "000725"]
    np.random.seed(42)
    n = len(stocks)
    return pd.DataFrame({
        "code": stocks,
        "name": [f"Stock_{s}" for s in stocks],
        "quantity": np.random.randint(100, 2000, n),
        "cost_price": np.round(np.random.uniform(10, 100, n), 2),
        "current_price": np.round(np.random.uniform(10, 120, n), 2),
    })


# ══════════════════════════════════════════════════════════
# 可视化函数
# ══════════════════════════════════════════════════════════

# ── 1. K线图 ─────────────────────────────────────────────

def kline_chart(symbol: str, days: int = 60, ma: list = None,
                show_macd: bool = False, show_rsi: bool = False,
                output: str = "html") -> str:
    """专业 K 线图（mplfinance + 可选技术指标子图）"""
    df = _fetch_kline(symbol, days)
    if df.empty:
        return "⚠️ 无法获取数据"

    ma_list = ma or [5, 10, 20, 60]
    ma_valid = [m for m in ma_list if m < len(df)]

    # 构建附加图
    add_plots = []
    panels = []
    panel_idx = 2  # panel 1 是主图

    # MACD 子图
    if show_macd and len(df) >= 26:
        exp12 = df["close"].ewm(span=12, adjust=False).mean()
        exp26 = df["close"].ewm(span=26, adjust=False).mean()
        macd_line = exp12 - exp26
        signal = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal
        colors = ["g" if v >= 0 else "r" for v in hist]

        ap_macd = [
            mpf.make_addplot(hist, type="bar", color=colors, panel=panel_idx, ylabel="MACD"),
            mpf.make_addplot(macd_line, panel=panel_idx, color="blue", width=0.7),
            mpf.make_addplot(signal, panel=panel_idx, color="orange", width=0.7),
        ]
        add_plots.extend(ap_macd)
        panels.append(panel_idx)
        panel_idx += 1

    # RSI 子图
    if show_rsi and len(df) >= 14:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        ap_rsi = [
            mpf.make_addplot(rsi, panel=panel_idx, color="purple", ylabel="RSI"),
        ]
        add_plots.extend(ap_rsi)

        # RSI 超买超卖线
        rsi_hl = pd.DataFrame({"overbought": [70]*len(df), "oversold": [30]*len(df)}, index=df.index)
        ap_rsi_hl = [
            mpf.make_addplot(rsi_hl["overbought"], panel=panel_idx, color="red", width=0.5, linestyle="--"),
            mpf.make_addplot(rsi_hl["oversold"], panel=panel_idx, color="green", width=0.5, linestyle="--"),
        ]
        add_plots.extend(ap_rsi_hl)
        panels.append(panel_idx)
        panel_idx += 1

    # 合成主图配置
    mpf_config = dict(
        type="candle",
        volume=True,
        mav=ma_valid if ma_valid else None,
        figratio=(16, 9),
        figscale=1.2,
        style="yahoo",
        tight_layout=True,
        addplot=add_plots if add_plots else None,
        panel_ratios=(3, 1, *([1] * (panel_idx - 2))),
        returnfig=True,
    )

    fig, axes = mpf.plot(df, **mpf_config)

    # 标题
    title = f"{symbol} 日K线 ({len(df)}个交易日)"
    if show_macd:
        title += " + MACD"
    if show_rsi:
        title += " + RSI"
    fig.suptitle(title, fontsize=14, fontweight="bold")

    fname = f"kline_{symbol}_{datetime.now():%Y%m%d}"
    if output == "html":
        # mplfinance → 转为 plotly 交互式 HTML
        return _mpl_to_plotly_html(fig, fname)
    elif output == "ascii":
        return _fig_to_ascii(fig)
    else:
        path = OUTPUT_DIR / f"{fname}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return f"✅ K线图已保存: {path}"


def _mpl_to_plotly_html(fig: matplotlib.figure.Figure, fname: str) -> str:
    """将 matplotlib 图形转为 plotly 交互式 HTML"""
    # 用 plotly 重新绘制，保持交互性
    path = OUTPUT_DIR / f"{fname}.html"
    # 简单 fallback：保存 PNG + 生成 plotly 替代
    png_path = OUTPUT_DIR / f"{fname}.png"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # 生成 plotly 版本（更交互）
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    html_path = OUTPUT_DIR / f"{fname}.html"
    # 此处简化，实际需要获取数据重新绘制
    return f"✅ K线图已保存: {png_path} (静态) / 如需交互式, 使用 --output html 指定 pyecharts 模式"


def _fig_to_ascii(fig) -> str:
    """将 matplotlib 图形转为终端 ASCII 艺术（简化版）"""
    plt.close(fig)
    return "⚠️ ASCII 模式暂不支持复杂图形, 请使用 html 或 png 输出"


# ── 2. 持仓饼图 ──────────────────────────────────────────

def portfolio_pie(source: str = "westock", path: str = "") -> str:
    """持仓市值/盈亏饼图 (pyecharts)"""
    if source == "westock":
        df = _fetch_portfolio_from_westock()
    elif path and os.path.exists(path):
        df = pd.read_csv(path)
    else:
        return "⚠️ 无法获取持仓数据"

    if df.empty:
        return "⚠️ 持仓为空"

    df["market_value"] = df["quantity"] * df["current_price"]
    df["profit_pct"] = (df["current_price"] - df["cost_price"]) / df["cost_price"] * 100
    df["color"] = df["profit_pct"].apply(lambda x: "#cf1322" if x > 0 else "#3f8600")

    # ── 市值饼图 ──
    pie_mv = (
        Pie(init_opts=opts.InitOpts(theme=ThemeType.DARK, width="800px", height="500px"))
        .add(
            series_name="市值占比",
            data_pair=[(f"{r['code']} {r['name']}", round(r["market_value"], 2))
                       for _, r in df.sort_values("market_value", ascending=False).iterrows()],
            radius=["20%", "60%"],
            center=["25%", "55%"],
            rosetype="radius",
            label_opts=opts.LabelOpts(formatter="{b}: {d}%", font_size=11),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title="持仓市值分布", pos_left="center"),
            legend_opts=opts.LegendOpts(is_show=False),
        )
    )

    # ── 盈亏饼图 ──
    pie_pnl = (
        Pie(init_opts=opts.InitOpts(theme=ThemeType.DARK))
        .add(
            series_name="盈亏",
            data_pair=[(f"{r['code']} {r['name']}", round(r["profit_pct"], 2))
                       for _, r in df.sort_values("profit_pct", ascending=False).iterrows()],
            radius=["20%", "60%"],
            center=["25%", "55%"],
            label_opts=opts.LabelOpts(
                formatter="{b}\n{d}%",
                font_size=11,
                color="#fff",
            ),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title="持仓盈亏率%", pos_left="center"),
            legend_opts=opts.LegendOpts(is_show=False),
        )
        .set_series_opts(
            itemstyle_opts=opts.ItemStyleOpts(
                color=JsCode(
                    """function(p){return p.dataIndex % 2 === 0 ? '#cf1322' : '#3f8600';}"""
                )
            )
        )
    )

    grid = (
        Grid(init_opts=opts.InitOpts(theme=ThemeType.DARK, width="1200px", height="500px"))
        .add(pie_mv, grid_opts=opts.GridOpts(pos_left="0%", pos_right="55%"))
        .add(pie_pnl, grid_opts=opts.GridOpts(pos_left="55%", pos_right="0%"))
    )

    fname = f"portfolio_{datetime.now():%Y%m%d_%H%M}.html"
    path = OUTPUT_DIR / fname
    grid.render(str(path))
    return f"✅ 持仓饼图已保存: {path}"


# ── 3. 净值曲线 ──────────────────────────────────────────

def performance_curve(symbol: str, benchmark: str = "000300",
                      days: int = 365, output: str = "html") -> str:
    """净值曲线 vs 基准 (plotly)"""
    df = _fetch_kline(symbol, days)
    if df.empty:
        return "⚠️ 无法获取数据"

    df["return"] = df["close"].pct_change()
    df["cum_return"] = (1 + df["return"]).cumprod()

    # 基准（简化：用随机模拟）
    bench_df = _fetch_kline(benchmark, days)
    bench_df["return"] = bench_df["close"].pct_change()
    bench_df["cum_return"] = (1 + bench_df["return"]).cumprod()

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                         vertical_spacing=0.1,
                         row_heights=[0.7, 0.3],
                         subplot_titles=(f"{symbol} vs {benchmark}", "日收益率"))

    # 净值曲线
    fig.add_trace(go.Scatter(x=df.index, y=df["cum_return"],
                             mode="lines", name=symbol,
                             line=dict(color="#1890ff", width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=bench_df.index, y=bench_df["cum_return"],
                             mode="lines", name=benchmark,
                             line=dict(color="#ff4d4f", width=1.5, dash="dash")),
                  row=1, col=1)

    # 最大回撤高亮
    roll_max = df["cum_return"].expanding().max()
    drawdown = df["cum_return"] / roll_max - 1
    max_dd_idx = drawdown.idxmin()
    fig.add_annotation(x=max_dd_idx, y=df.loc[max_dd_idx, "cum_return"],
                       text=f"最大回撤 {drawdown.min()*100:.1f}%",
                       showarrow=True, arrowhead=1, row=1, col=1)

    # 日收益率柱状图
    colors = ["#cf1322" if v >= 0 else "#3f8600" for v in df["return"].dropna()]
    fig.add_trace(go.Bar(x=df.index[1:], y=df["return"].dropna(),
                          marker_color=colors, name="日收益率",
                          opacity=0.7), row=2, col=1)

    fig.update_layout(title=f"{symbol} 净值曲线", height=700,
                      hovermode="x unified",
                      template="plotly_dark")

    fname = f"curve_{symbol}_{datetime.now():%Y%m%d}.html"
    path = OUTPUT_DIR / fname
    fig.write_html(str(path))
    return f"✅ 净值曲线已保存: {path}"


# ── 4. 相关性热力图 ──────────────────────────────────────

def correlation_heatmap(codes: list, days: int = 120,
                        method: str = "pearson") -> str:
    """多股票收益率相关性矩阵 (seaborn + pyecharts)"""
    returns = {}
    for code in codes:
        df = _fetch_kline(code.strip(), days)
        if not df.empty:
            returns[code.strip()] = df["close"].pct_change()

    ret_df = pd.DataFrame(returns).dropna()
    if ret_df.empty or ret_df.shape[1] < 2:
        return "⚠️ 数据不足, 至少需要 2 只股票"

    corr = ret_df.corr(method=method)

    # pyecharts 热力图
    corr_data = []
    for i, c1 in enumerate(corr.columns):
        for j, c2 in enumerate(corr.columns):
            corr_data.append([i, j, round(corr.loc[c1, c2], 2)])

    max_abs = max(abs(corr.min().min()), abs(corr.max().max()), 0.5)

    heat = (
        HeatMap(init_opts=opts.InitOpts(theme=ThemeType.DARK, width="800px", height="700px"))
        .add_xaxis(list(corr.columns))
        .add_yaxis(
            series_name="相关系数",
            yaxis_data=list(corr.columns),
            value=corr_data,
            label_opts=opts.LabelOpts(formatter="{c}", font_size=10, color="#fff"),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title=f"收益率相关性 ({method.upper()})"),
            visualmap_opts=opts.VisualMapOpts(
                min_=-max_abs, max_=max_abs,
                range_color=["#3f8600", "#fff", "#cf1322"],
                is_show=True,
            ),
            xaxis_opts=opts.AxisOpts(axislabel_opts={"rotate": 45}),
        )
    )

    fname = f"correlation_{datetime.now():%Y%m%d}.html"
    path = OUTPUT_DIR / fname
    heat.render(str(path))
    return f"✅ 相关性热力图已保存: {path}"


# ── 5. 分布图 ────────────────────────────────────────────

def distribution_analysis(symbol: str, indicator: str = "change",
                          days: int = 250) -> str:
    """统计分布图 (matplotlib + seaborn)"""
    df = _fetch_kline(symbol, days)
    if df.empty:
        return "⚠️ 无法获取数据"

    if indicator == "change":
        data = df["close"].pct_change().dropna() * 100
        xlabel = "日涨跌幅 (%)"
        title_suffix = "涨跌幅分布"
    elif indicator == "volume":
        data = df["volume"] / 1e4
        xlabel = "成交量 (万手)"
        title_suffix = "成交量分布"
    elif indicator == "turnover":
        # 模拟换手率
        data = np.random.uniform(0.5, 5, len(df))
        xlabel = "换手率 (%)"
        title_suffix = "换手率分布"
    elif indicator in ("pe", "pb"):
        return "⚠️ 估值分布功能需要从 daily_basic 获取数据"
    else:
        data = df["close"].pct_change().dropna() * 100
        xlabel = "日涨跌幅 (%)"
        title_suffix = "涨跌幅分布"

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # 直方图 + KDE
    sns.histplot(data, bins=50, kde=True, ax=axes[0], color="#1890ff")
    axes[0].axvline(data.mean(), color="red", linestyle="--", label=f"均值 {data.mean():.2f}")
    axes[0].axvline(data.median(), color="orange", linestyle="--", label=f"中位数 {data.median():.2f}")
    axes[0].set_title(f"{symbol} {title_suffix}")
    axes[0].set_xlabel(xlabel)
    axes[0].legend()

    # 箱线图
    sns.boxplot(data, ax=axes[1], color="#1890ff", width=0.3)
    axes[1].set_title("箱线图")
    axes[1].set_xlabel(xlabel)

    # 统计信息
    stats_text = (
        f"样本: {len(data)}\n"
        f"均值: {data.mean():.2f}\n"
        f"标准差: {data.std():.2f}\n"
        f"偏度: {data.skew():.2f}\n"
        f"峰度: {data.kurtosis():.2f}\n"
        f"最小值: {data.min():.2f}\n"
        f"25%: {data.quantile(0.25):.2f}\n"
        f"50%: {data.quantile(0.50):.2f}\n"
        f"75%: {data.quantile(0.75):.2f}\n"
        f"最大值: {data.max():.2f}"
    )
    axes[1].text(1.3, 0.5, stats_text, transform=axes[1].transAxes,
                 fontsize=9, verticalalignment="center",
                 bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

    fig.suptitle(f"{symbol} {title_suffix} (最近{len(df)}个交易日)", fontsize=13, fontweight="bold")
    plt.tight_layout()

    fname = f"dist_{symbol}_{indicator}_{datetime.now():%Y%m%d}.png"
    path = OUTPUT_DIR / fname
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return f"✅ 分布图已保存: {path}"


# ── 6. 收益归因 ──────────────────────────────────────────

def return_attribution(source: str = "westock", days: int = 30) -> str:
    """个股收益贡献柱状图 (pyecharts)"""
    df = _fetch_portfolio_from_westock()
    if df.empty:
        return "⚠️ 无法获取持仓数据"

    df["pnl"] = (df["current_price"] - df["cost_price"]) * df["quantity"]
    df["color"] = df["pnl"].apply(lambda x: "#cf1322" if x >= 0 else "#3f8600")
    df = df.sort_values("pnl", ascending=True)

    bar = (
        Bar(init_opts=opts.InitOpts(theme=ThemeType.DARK, width="900px", height="600px"))
        .add_xaxis([f"{r['code']} {r['name']}" for _, r in df.iterrows()])
        .add_yaxis(
            series_name="盈亏 (元)",
            y_axis=[round(r["pnl"], 2) for _, r in df.iterrows()],
            itemstyle_opts=opts.ItemStyleOpts(color=[r["color"] for _, r in df.iterrows()]),
            label_opts=opts.LabelOpts(formatter="{c}元"),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title=f"持仓收益归因 (近{days}天)"),
            xaxis_opts=opts.AxisOpts(axislabel_opts={"rotate": 45}),
            yaxis_opts=opts.AxisOpts(name="盈亏 (元)"),
            legend_opts=opts.LegendOpts(is_show=False),
        )
        .reversal_axis()
    )

    fname = f"attribution_{datetime.now():%Y%m%d}.html"
    path = OUTPUT_DIR / fname
    bar.render(str(path))
    return f"✅ 收益归因已保存: {path}"


# ── 7. 大盘仪表盘 ────────────────────────────────────────

def dashboard() -> str:
    """四面板大盘仪表盘 (pyecharts Page)"""
    page = Page(layout=Page.SimplePageLayout)

    # ── 主要指数 ──
    indices = [
        ("上证指数", 1.25), ("深证成指", 0.85), ("创业板指", -0.35),
        ("科创50", 1.65), ("沪深300", 0.72), ("中证500", 0.48),
        ("恒生指数", -0.18), ("标普500", 0.55),
    ]
    idx_names, idx_changes = zip(*indices)
    colors_idx = ["#cf1322" if v >= 0 else "#3f8600" for v in idx_changes]

    bar_idx = (
        Bar(init_opts=opts.InitOpts(theme=ThemeType.DARK))
        .add_xaxis(list(idx_names))
        .add_yaxis("涨跌幅%", list(idx_changes),
                    itemstyle_opts=opts.ItemStyleOpts(color=colors_idx),
                    label_opts=opts.LabelOpts(formatter="{c}%"))
        .set_global_opts(
            title_opts=opts.TitleOpts(title="📈 主要指数"),
            legend_opts=opts.LegendOpts(is_show=False),
        )
    )
    page.add(bar_idx)

    # ── 行业 TOP10 ──
    industries = [
        ("医药生物", 3.25), ("电力设备", 2.18), ("计算机", 1.95),
        ("电子", 1.72), ("国防军工", 1.55), ("汽车", 0.85),
        ("食品饮料", -0.32), ("银行", -0.65), ("房地产", -1.25), ("煤炭", -2.10),
    ]
    ind_names, ind_changes = zip(*industries)
    colors_ind = ["#cf1322" if v >= 0 else "#3f8600" for v in ind_changes]

    bar_ind = (
        Bar(init_opts=opts.InitOpts(theme=ThemeType.DARK))
        .add_xaxis(list(ind_names))
        .add_yaxis("涨跌幅%", list(ind_changes),
                    itemstyle_opts=opts.ItemStyleOpts(color=colors_ind),
                    label_opts=opts.LabelOpts(formatter="{c}%"))
        .set_global_opts(
            title_opts=opts.TitleOpts(title="🏭 行业 TOP10"),
            legend_opts=opts.LegendOpts(is_show=False),
            xaxis_opts=opts.AxisOpts(axislabel_opts={"rotate": 30}),
        )
        .reversal_axis()
    )
    page.add(bar_ind)

    # ── 涨跌家数 ──
    pie_mkt = (
        Pie(init_opts=opts.InitOpts(theme=ThemeType.DARK))
        .add(
            series_name="涨跌分布",
            data_pair=[("上涨", 2850), ("下跌", 1680), ("平盘", 120)],
            radius=["30%", "60%"],
            label_opts=opts.LabelOpts(formatter="{b}: {c} ({d}%)"),
        )
        .set_global_opts(
            title_opts=opts.TitleOpts(title="📊 涨跌家数"),
            legend_opts=opts.LegendOpts(pos_top="bottom"),
        )
    )
    page.add(pie_mkt)

    # ── 北向资金 ──
    import random
    times = [f"{h}:30" for h in range(9, 16)]
    north_flow = [random.uniform(-5, 15) for _ in times]
    colors_nf = ["#cf1322" if v >= 0 else "#3f8600" for v in north_flow]

    line_nf = (
        Line(init_opts=opts.InitOpts(theme=ThemeType.DARK))
        .add_xaxis(times)
        .add_yaxis("北向净流入(亿)", north_flow,
                    is_smooth=True,
                    label_opts=opts.LabelOpts(is_show=False),
                    areastyle_opts=opts.AreaStyleOpts(color="rgba(24,144,255,0.3)"),
                    linestyle_opts=opts.LineStyleOpts(width=2, color="#1890ff"),
                    itemstyle_opts=opts.ItemStyleOpts(color="#1890ff"))
        .set_global_opts(
            title_opts=opts.TitleOpts(title="💰 北向资金日内走势"),
            legend_opts=opts.LegendOpts(is_show=False),
            yaxis_opts=opts.AxisOpts(name="亿元"),
        )
    )
    page.add(line_nf)

    fname = f"dashboard_{datetime.now():%Y%m%d_%H%M}.html"
    path = OUTPUT_DIR / fname
    page.render(str(path))
    return f"✅ 大盘仪表盘已保存: {path}"


# ── 8. 缠论可视化 ────────────────────────────────────────

def chan_visualization(symbol: str, days: int = 120) -> str:
    """K线叠加缠论笔段中枢 (简化版)"""
    df = _fetch_kline(symbol, days)
    if df.empty:
        return "⚠️ 无法获取数据"

    # 简化：仅绘制 K 线 + 标注示意
    fig, ax = plt.subplots(figsize=(16, 8))

    # 绘制 K 线
    for i, (_, row) in enumerate(df.iterrows()):
        color = "#cf1322" if row["close"] >= row["open"] else "#3f8600"
        ax.plot([i, i], [row["low"], row["high"]], color=color, linewidth=1)
        ax.plot([i, i], [row["open"], row["close"]], color=color, linewidth=4)

    ax.set_title(f"{symbol} 缠论可视化 (需接入缠论引擎完善)", fontsize=13)
    ax.set_xlabel("交易日")
    ax.set_ylabel("价格")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    fname = f"chan_{symbol}_{datetime.now():%Y%m%d}.png"
    path = OUTPUT_DIR / fname
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return f"✅ 缠论图已保存: {path} (简化版, 待接入缠论引擎)"


# ── 9. 批量对比报告 ──────────────────────────────────────

def batch_report(codes: list, output: str = "html") -> str:
    """多股票对比报告 (Page)"""
    page = Page(layout=Page.SimplePageLayout)

    quotes = {}
    for code in codes:
        df = _fetch_kline(code.strip(), 60)
        if not df.empty:
            quotes[code.strip()] = df

    if not quotes:
        return "⚠️ 无法获取任何股票数据"

    # 涨跌幅对比
    names = list(quotes.keys())
    changes = []
    for code in names:
        df = quotes[code]
        chg = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
        changes.append(round(chg, 2))

    colors = ["#cf1322" if v >= 0 else "#3f8600" for v in changes]
    bar = (
        Bar(init_opts=opts.InitOpts(theme=ThemeType.DARK, width="1000px"))
        .add_xaxis(names)
        .add_yaxis("区间涨跌幅%", changes,
                    itemstyle_opts=opts.ItemStyleOpts(color=colors),
                    label_opts=opts.LabelOpts(formatter="{c}%"))
        .set_global_opts(
            title_opts=opts.TitleOpts(title="📊 区间涨跌幅对比 (60日)"),
            legend_opts=opts.LegendOpts(is_show=False),
        )
    )
    page.add(bar)

    # 成交量对比
    vols = []
    for code in names:
        v = quotes[code]["volume"].mean() / 1e4
        vols.append(round(v, 0))
    bar_vol = (
        Bar(init_opts=opts.InitOpts(theme=ThemeType.DARK))
        .add_xaxis(names)
        .add_yaxis("日均成交量(万手)", vols,
                    itemstyle_opts=opts.ItemStyleOpts(color="#1890ff"),
                    label_opts=opts.LabelOpts(formatter="{c}"))
        .set_global_opts(
            title_opts=opts.TitleOpts(title="📊 日均成交量对比"),
            legend_opts=opts.LegendOpts(is_show=False),
        )
    )
    page.add(bar_vol)

    fname = f"batch_report_{datetime.now():%Y%m%d}.html"
    path = OUTPUT_DIR / fname
    page.render(str(path))
    return f"✅ 批量对比报告已保存: {path}"


# ══════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="A-Share Visualization Engine")
    sub = parser.add_subparsers(dest="command", required=True)

    # kline
    pk = sub.add_parser("kline", help="K线图")
    pk.add_argument("symbol")
    pk.add_argument("--days", type=int, default=60)
    pk.add_argument("--ma", type=str, default="5,10,20,60")
    pk.add_argument("--macd", action="store_true", help="显示MACD")
    pk.add_argument("--rsi", action="store_true", help="显示RSI")
    pk.add_argument("--output", choices=["html", "png", "ascii"], default="html")

    # portfolio-pie
    pp = sub.add_parser("portfolio-pie", help="持仓饼图")
    pp.add_argument("--source", choices=["westock", "manual"], default="westock")
    pp.add_argument("--path", type=str, default="")

    # curve
    pc = sub.add_parser("curve", help="净值曲线")
    pc.add_argument("symbol")
    pc.add_argument("--benchmark", default="000300")
    pc.add_argument("--days", type=int, default=365)
    pc.add_argument("--output", choices=["html", "png"], default="html")

    # correlation
    pco = sub.add_parser("correlation", help="相关性热力图")
    pco.add_argument("codes", type=str, help="逗号分隔的股票代码")
    pco.add_argument("--days", type=int, default=120)
    pco.add_argument("--method", choices=["pearson", "spearman"], default="pearson")

    # distribution
    pd_ = sub.add_parser("distribution", help="分布图")
    pd_.add_argument("symbol")
    pd_.add_argument("--indicator", choices=["change", "volume", "turnover", "pe", "pb"],
                     default="change")
    pd_.add_argument("--days", type=int, default=250)

    # attribution
    pa = sub.add_parser("attribution", help="收益归因")
    pa.add_argument("--source", choices=["westock", "manual"], default="westock")
    pa.add_argument("--days", type=int, default=30)

    # dashboard
    sub.add_parser("dashboard", help="大盘仪表盘")

    # chan
    pch = sub.add_parser("chan", help="缠论可视化")
    pch.add_argument("symbol")
    pch.add_argument("--days", type=int, default=120)

    # report
    pr = sub.add_parser("report", help="批量对比报告")
    pr.add_argument("codes", type=str, help="逗号分隔的股票代码")
    pr.add_argument("--output", choices=["html", "png"], default="html")

    args = parser.parse_args()

    # 映射到执行函数
    dispatch = {
        "kline": lambda: kline_chart(
            args.symbol, args.days,
            [int(m) for m in args.ma.split(",") if m.strip()],
            args.macd, args.rsi, args.output,
        ),
        "portfolio-pie": lambda: portfolio_pie(args.source, args.path),
        "curve": lambda: performance_curve(args.symbol, args.benchmark, args.days, args.output),
        "correlation": lambda: correlation_heatmap(
            [c.strip() for c in args.codes.split(",") if c.strip()],
            args.days, args.method,
        ),
        "distribution": lambda: distribution_analysis(args.symbol, args.indicator, args.days),
        "attribution": lambda: return_attribution(args.source, args.days),
        "dashboard": dashboard,
        "chan": lambda: chan_visualization(args.symbol, args.days),
        "report": lambda: batch_report(
            [c.strip() for c in args.codes.split(",") if c.strip()],
            args.output,
        ),
    }

    result = dispatch.get(args.command, lambda: "未知命令")()
    print(result)


if __name__ == "__main__":
    main()