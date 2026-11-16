# -*- coding: utf-8 -*-
"""
屠龙多模型全域回测 round6 补充深度诊断
=====================================
1) 行情环境分段绩效（上涨趋势/下跌趋势/震荡/题材活跃）
2) 盈利/亏损交易特征归类 + 大额亏损诱因统计
3) 达标校验（胜率78-82%、年化200% 差距 + 失效时段标记）
4) 6类标准可视化图表 PNG（净值+回撤/年度柱状/盈亏直方/胜率时序/盈亏散点/多模型对比）
输出: reports/tulong_backtest_multi_round6_20260819/diagnosis/
"""
import os, json, sqlite3, warnings
from collections import defaultdict
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "FangSong"]
plt.rcParams["axes.unicode_minus"] = False
warnings.filterwarnings("ignore")

ROOT = Path(r"D:\zcodesj\reports\tulong_backtest_multi_round6_20260819")
DIAG = ROOT / "diagnosis"
DIAG.mkdir(parents=True, exist_ok=True)
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
FUND_DB = None  # industry_map 用 a-share-local 的 fund 库路径（见下探测）

MODELS = ["bp_ep_core", "v375_signal", "p2_signal", "composite_signal", "ml_signal"]
TARGET_WIN = (78.0, 82.0)
TARGET_CAGR = 200.0


def load_index():
    conn = sqlite3.connect(DB)
    codes = pd.read_sql("SELECT DISTINCT index_code FROM index_daily", conn)
    idx = "000300" if "000300" in set(codes["index_code"]) else "000001"
    df = pd.read_sql(f"SELECT date, close, volume FROM index_daily WHERE index_code='{idx}' ORDER BY date", conn)
    conn.close()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date")["close"]
    return df, idx


def load_sentiment():
    """题材活跃期：全市场涨停家数密度

    M2修复: feature_market_sentiment 表在 fundamental_data.db，不在 dragon_quant.db。
    原代码连错库致该函数恒返回None，题材活跃标签全程False。
    """
    # 优先真库 fundamental_data.db(workbuddyspace)，回退 backups
    # 2026-08-29 优化专项修复: 旧链首项根目录为错误路径, 实际总落到 8/23 陈旧备份
    import os
    fund_candidates = [
        r"D:\workbuddyspace\databases\fundamental_data.db",
        r"D:\zcodesj\backups\fundamental_data\weekly\fundamental_data_20260823.db",
    ]
    fund_db = None
    for p in fund_candidates:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            fund_db = p
            break
    if fund_db is None:
        return None
    conn = sqlite3.connect(fund_db)
    try:
        df = pd.read_sql("SELECT trade_date, limit_up, total_stocks, limit_up_ratio, sentiment_score FROM feature_market_sentiment ORDER BY trade_date", conn)
    except Exception:
        conn.close()
        return None
    conn.close()
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    return df.dropna(subset=["date"]).set_index("date")


def regime_series(index_close, sentiment=None):
    """行情环境: trend_up / trend_down / range + 题材活跃叠加标签"""
    ma120 = index_close.rolling(120).mean()
    slope = ma120.diff(5)
    reg = pd.Series("range", index=index_close.index)
    up = (index_close > ma120) & (slope > 0)
    down = (index_close < ma120) & (slope < 0)
    reg[up] = "trend_up"
    reg[down] = "trend_down"
    active = pd.Series(False, index=index_close.index)
    if sentiment is not None:
        vals = sentiment["limit_up_ratio"]
        active.loc[vals.index] = vals > 0.012  # 涨停比>1.2% 视为题材活跃
    return reg, active


def load_equity(name):
    p = ROOT / f"{name}_equity.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p)
    df.columns = [c.strip("\ufeff").strip() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def load_trades(name):
    p = ROOT / f"{name}_trades_full.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df.columns = [c.strip("\ufeff").strip() for c in df.columns]
    df["买入日期"] = pd.to_datetime(df["买入日期"])
    df["卖出日期"] = pd.to_datetime(df["卖出日期"])
    return df


def yearly_check(yearly):
    """达标校验"""
    rows = []
    for _, r in yearly.iterrows():
        win_ok = TARGET_WIN[0] <= r["win_rate"] <= TARGET_WIN[1]
        cagr_ok = r["annual_return"] >= TARGET_CAGR
        rows.append({
            "strategy": r["strategy"], "year": r["year"],
            "win_rate": round(r["win_rate"], 2), "win_target": f"{TARGET_WIN[0]}-{TARGET_WIN[1]}",
            "win_gap": round(r["win_rate"] - TARGET_WIN[1], 2),
            "annual_return": round(r["annual_return"], 2), "cagr_target": TARGET_CAGR,
            "year_pnl": round(r["total_pnl"], 2),
            "达标": "胜率达标" if win_ok else ("胜率未达标" if not win_ok and win_ok is False else "胜率未达标"),
            "cagr_达标": "达标" if cagr_ok else "未达标",
            "失效标记": "🔴负收益年" if r["total_pnl"] < 0 else ""
        })
    return pd.DataFrame(rows)


def env_perf(equity, reg, active):
    if equity is None:
        return None
    seg = reg.reindex(equity.index).ffill()
    act = active.reindex(equity.index).fillna(False)
    res = {}
    for env in ["trend_up", "trend_down", "range"]:
        m = seg == env
        if m.sum() == 0:
            continue
        sub = equity[m]
        ret = sub["value"].iloc[-1] / sub["value"].iloc[0] - 1
        ndays = len(sub)
        years = ndays / 244
        cagr = (1 + ret) ** (1 / years) - 1 if years > 0.3 and ret > -1 else np.nan
        res[env] = {"days": ndays, "ret_pct": round(ret * 100, 2), "cagr_pct": round(cagr * 100, 2) if not np.isnan(cagr) else None}
        act_env = act[m]
        if act_env.sum() > 0:
            sub_a = equity[m & act]
            ret_a = sub_a["value"].iloc[-1] / sub_a["value"].iloc[0] - 1 if len(sub_a) > 1 else 0
            res[env]["active_days"] = int(act_env.sum())
            res[env]["active_ret_pct"] = round(ret_a * 100, 2)
    return res


def trade_stats(trades, industry_map=None):
    if trades.empty:
        return None
    win = trades[trades["盈亏金额"] > 0]
    loss = trades[trades["盈亏金额"] <= 0]
    def _agg(df, tag):
        if df.empty:
            return {"tag": tag, "n": 0}
        out = {
            "tag": tag, "n": len(df),
            "mean_hold": round(df["持仓天数"].mean(), 1),
            "median_hold": int(df["持仓天数"].median()),
            "mean_pnl_pct": round(df["盈亏比例%"].mean(), 2),
            "median_pnl_pct": round(df["盈亏比例%"].median(), 2),
            "total_pnl": round(df["盈亏金额"].sum(), 2),
        }
        if industry_map is not None and "股票代码" in df.columns:
            m = df.merge(industry_map, left_on="股票代码", right_on="ts_code", how="left")
            out["top_industry"] = m["industry_name"].value_counts().head(5).to_dict()
        return out
    return {"win": _agg(win, "盈利"), "loss": _agg(loss, "亏损"), "all": _agg(trades, "全部")}


def loss_top(trades, n=20, industry_map=None):
    if trades.empty:
        return pd.DataFrame()
    t = trades.nsmallest(n, "盈亏金额")
    cols = ["股票代码", "股票名称", "买入日期", "卖出日期", "买入价格", "卖出价格",
            "盈亏金额", "盈亏比例%", "卖出触发理由", "持仓天数"]
    t = t[[c for c in cols if c in t.columns]].copy()
    if industry_map is not None and "股票代码" in t.columns:
        t = t.merge(industry_map, left_on="股票代码", right_on="ts_code", how="left")
    return t


# ---------------- 可视化 ----------------
def fig_nav_drawdown(combo_eq, idx_close, name="production_combo_60_40"):
    """图1: 净值曲线 + 最大回撤区间标记"""
    nav = combo_eq["value"] / combo_eq["value"].iloc[0]
    dd = nav / nav.cummax() - 1
    fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1]})
    ax[0].plot(nav.index, nav.values, lw=1.2, color="#c0392b", label=f"{name} 净值(归一化)")
    anchor = dd.idxmin()
    anchor_dd = dd.min() * 100
    ax[0].axvspan(pd.Timestamp("2018-01-24"), pd.Timestamp("2019-01-03"), color="gray", alpha=0.08)
    ax[0].annotate(f"最大回撤 {anchor_dd:.1f}%\n(谷底 {anchor:%Y-%m-%d})",
                   xy=(anchor, nav.loc[anchor]), xytext=(anchor, nav.loc[anchor] * 0.75),
                   arrowprops=dict(arrowstyle="->", color="red"), color="red", fontsize=10)
    ax[0].set_title(f"账户净值曲线（含最大回撤标记） | {name}", fontsize=13)
    ax[0].legend(loc="upper left")
    ax[0].grid(alpha=0.3)
    ax[1].fill_between(dd.index, dd.values * 100, 0, color="#e74c3c", alpha=0.5, label="回撤%")
    ax[1].set_title("回撤(%)", fontsize=10)
    ax[1].grid(alpha=0.3)
    ax[1].legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(DIAG / "fig1_nav_drawdown.png", dpi=130)
    plt.close(fig)


def fig_yearly(yearly):
    """图2: 年度收益柱状图(各模型)"""
    piv = yearly.pivot(index="year", columns="strategy", values="annual_return")
    ax = piv.plot(kind="bar", figsize=(13, 6), width=0.85)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("各模型年度收益对比（按初始本金10万计，%）", fontsize=13)
    ax.set_ylabel("年度收益 %")
    ax.legend(fontsize=8, ncol=3)
    ax.grid(axis="y", alpha=0.3)
    fig = ax.figure
    fig.tight_layout()
    fig.savefig(DIAG / "fig2_yearly_return.png", dpi=130)
    plt.close(fig)


def fig_pnl_hist(trades_all):
    """图3: 单笔盈亏分布直方"""
    pct = trades_all["盈亏比例%"]
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.hist(pct, bins=60, color="#3498db", edgecolor="white", alpha=0.9)
    ax.axvline(0, color="black", lw=1)
    ax.axvline(pct.mean(), color="red", ls="--", lw=1.2, label=f"均值 {pct.mean():.2f}%")
    ax.set_title(f"单笔盈亏分布直方（合并全部模型 {len(trades_all)} 笔，亏损{int((pct<=0).sum())}笔/盈利{int((pct>0).sum())}笔）", fontsize=13)
    ax.set_xlabel("单笔盈亏 %")
    ax.legend()
    fig.tight_layout()
    fig.savefig(DIAG / "fig3_pnl_hist.png", dpi=130)
    plt.close(fig)


def fig_winrate_ts(all_trades_by_model):
    """图4: 滚动胜率时序(50笔窗口)"""
    fig, ax = plt.subplots(figsize=(12, 5.5))
    for name, tr in all_trades_by_model.items():
        if len(tr) < 60:
            continue
        tr = tr.sort_values("卖出日期")
        wr = tr["盈亏金额"].gt(0).rolling(50).mean() * 100
        ax.plot(tr["卖出日期"], wr.values, lw=1.1, label=f"{name} (50笔滚动胜率)")
    ax.axhline(80, color="red", ls="--", lw=1, label="目标中枢 80%")
    ax.axhline(78, color="gray", ls=":", lw=1)
    ax.axhline(82, color="gray", ls=":", lw=1)
    ax.set_ylim(0, 100)
    ax.set_title("胜率时序波动曲线（50笔滚动窗口 vs 目标78-82%）", fontsize=13)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(DIAG / "fig4_winrate_ts.png", dpi=130)
    plt.close(fig)


def fig_scatter(trades_all):
    """图5: 盈亏散点（盈亏金额 vs 持仓天数）"""
    fig, ax = plt.subplots(figsize=(11, 6))
    xs = trades_all["持仓天数"]
    ys = trades_all["盈亏金额"]
    colors = np.where(ys > 0, "#e74c3c", "#2ecc71")
    ax.scatter(xs, ys, c=colors, s=12, alpha=0.45)
    ax.axhline(0, color="black", lw=0.8)
    ax.set_title("单笔盈亏散点分布（X=持仓天数, Y=盈亏金额）", fontsize=13)
    ax.set_xlabel("持仓天数")
    ax.set_ylabel("盈亏金额（元）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(DIAG / "fig5_pnl_scatter.png", dpi=130)
    plt.close(fig)


def fig_multi_compare(equities, idx_close):
    """图6: 多模型净值对比（+上证指数基准）"""
    fig, ax = plt.subplots(figsize=(12, 6))
    for name, eq in equities.items():
        if eq is None or len(eq) < 5:
            continue
        ax.plot(eq.index, eq["value"] / eq["value"].iloc[0], lw=1.1, label=name)
    base = idx_close.reindex(equities["bp_ep_core"].index).ffill() if equities.get("bp_ep_core") is not None else idx_close
    ax.plot(base.index, base / base.iloc[0], lw=1.3, color="black", ls="--", label="上证指数(归一)")
    ax.set_title("多模型净值对比（2016-01-01 = 1.0）", fontsize=13)
    ax.legend(fontsize=8, ncol=2)
    ax.grid(alpha=0.3)
    ax.set_yscale("log")
    fig.tight_layout()
    fig.savefig(DIAG / "fig6_multi_compare.png", dpi=130)
    plt.close(fig)


def main():
    print("== 1. 加载指数与情绪 ==")
    idx_close, idx_name = load_index()
    sentiment = load_sentiment()
    reg, active = regime_series(idx_close, sentiment)

    print("== 2. 行业映射（可选，缺失不影响主流程） ==")
    industry_map = None
    try:
        import glob
        for cand in glob.glob(r"D:\zcodesj\*.db") + glob.glob(r"D:\workbuddyspace\factor_engine\data\*.db"):
            try:
                conn = sqlite3.connect(cand)
                tabs = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "industry_map" in tabs:
                    df = pd.read_sql("SELECT DISTINCT ts_code, industry_name FROM industry_map", conn)
                    conn.close()
                    if len(df) > 1000:
                        industry_map = df
                        print(f"    行业映射来自 {cand} ({len(df)}行)")
                        break
                conn.close()
            except Exception:
                continue
    except Exception:
        pass

    print("== 3. 读取引擎产物 ==")
    yearly = pd.read_csv(ROOT / "yearly_summary_all.csv")
    yearly.columns = [c.strip("\ufeff").strip() for c in yearly.columns]
    equities = {m: load_equity(m) for m in MODELS}
    combo_eq = load_equity("production_combo_60_40")

    print("== 4. 达标校验 ==")
    check = yearly_check(yearly)
    check.to_csv(DIAG / "target_check.csv", index=False, encoding="utf-8-sig")

    print("== 5. 行情环境分段绩效 ==")
    env_rows = []
    for m, eq in equities.items():
        res = env_perf(eq, reg, active)
        if res:
            for env, v in res.items():
                env_rows.append({"strategy": m, "env": env, **v})
    env_df = pd.DataFrame(env_rows)
    env_df.to_csv(DIAG / "regime_perf.csv", index=False, encoding="utf-8-sig")

    print("== 6. 交易特征归类 + 大额亏损 ==")
    trades = {m: load_trades(m) for m in MODELS}
    stats = {m: trade_stats(t, industry_map) for m, t in trades.items()}
    with open(DIAG / "trade_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1, default=str)
    for m in MODELS:
        t = loss_top(trades[m], 20, industry_map)
        if not t.empty:
            t.to_csv(DIAG / f"loss_top20_{m}.csv", index=False, encoding="utf-8-sig")

    print("== 7. 可视化6图 ==")
    fig1_src = combo_eq if (combo_eq is not None and len(combo_eq) > 5) else equities.get("ml_signal")
    fig1_name = "production组合(未生成→ml_signal)" if combo_eq is None else "production_combo_60_40"
    if fig1_src is not None and len(fig1_src) > 5:
        fig_nav_drawdown(fig1_src, idx_close, name=fig1_name)
    fig_yearly(yearly)
    all_trades = pd.concat([trades[m] for m in MODELS if not trades[m].empty], ignore_index=True)
    if not all_trades.empty:
        fig_pnl_hist(all_trades)
        fig_scatter(all_trades)
    fig_winrate_ts({m: trades[m] for m in MODELS})
    fig_multi_compare(equities, idx_close)

    print(f"== 完成: {DIAG} ==")


if __name__ == "__main__":
    main()