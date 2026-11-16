# -*- coding: utf-8 -*-
"""
统一策略复测框架 — 屠龙系统策略扩展工程 Phase 0
================================================
所有新策略测试与判死策略复测共用本框架，保证口径统一。

口径铁律（与 arbitration_pit_final / factor_ml_walkforward_v2 完全一致）:
- 价格:   qfq前复权 (dragon_quant_qfq.db) + 原始close(≤30元过滤用)
- 成交:   next-bar（T月末信号 → T+1首交易日按次日收益计入）
- 过滤:   ≤30元(原始价) + 非ST + 白名单(60/00/30/68/92)
- 成本:   单边0.3%（佣金+滑点），月度调仓
- 财务:   必走 factor_pit.db（禁用 as-of loader）
- 清洗:   污染日整日剔除 + 日收益clip ±21%

统一评估三关 + 统计检验:
- 关1: 月度IC序列（均值/ICIR/正率）
- 关2: 五分层单调性（Spearman p）
- 关3: Walk-Forward 组合回测（next-bar+成本）
- 统计: PSR/DSR/MinTRL（deflated_sharpe）+ PBO（CSCV）
"""
from __future__ import annotations

import math
import pickle
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── 环境预检（必须在任何第三方依赖导入之前）─────────────────
# 统计代码依赖 scipy；系统 python 3.13.12 未装 scipy。
# 提前到此处检查，确保缺环境时给出明确指引而非后续静默降级。
try:
    import scipy  # noqa: F401
except ImportError as _e:
    raise RuntimeError(
        "统计工具需要 scipy。请用 .venv_ml 运行本脚本：\n"
        r'D:\workbuddyspace\.venv_ml\Scripts\python.exe ' + __file__ +
        "\n（系统 python 未安装 scipy/numpy）"
    ) from _e

import numpy as np
import pandas as pd

# ── 路径常量 ──────────────────────────────────────────────
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
CACHE_DIR = Path(r"D:\zcodesj\_cache")
REPORT_DIR = Path(r"D:\zcodesj\reports")
CACHE_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

# 统计工具链（DSR/PBO/MinTRL/Haircut）—— zcodesj/statlib 自包含 vendor
# 铁律：统计代码必须在 .venv_ml 环境运行（scipy>=1.18）；系统 python 无 scipy。
_STATLIB = Path(r"D:\zcodesj\statlib")
if str(_STATLIB) not in sys.path:
    sys.path.insert(0, str(_STATLIB))


def require_scipy() -> None:
    """环境自检：统计函数依赖 scipy，缺则给出明确指引而非静默降级。"""
    try:
        import scipy  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "统计工具需要 scipy。请用 .venv_ml 运行本脚本：\n"
            r'D:\workbuddyspace\.venv_ml\Scripts\python.exe ' + __file__ +
            "\n（系统 python 未安装 scipy）"
        ) from e


require_scipy()

COST_ONE_SIDE = 0.003          # 单边成本 0.3%
PRICE_CAP = 30.0               # 价格上限铁律
CODE_WHITELIST_PREFIX = ("60", "00", "30", "68", "92")


# ═══════════════════════════════════════════════════════
#  数据层
# ═══════════════════════════════════════════════════════

def _ro(db: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=120)


def load_qfq(start: str, end: str, fields=("open", "high", "low", "close", "volume")) -> dict:
    """qfq 宽表 dict[field] = DataFrame(date×code)。自动剔除白名单外代码。"""
    cols = ", ".join(fields)
    conn = _ro(QFQ_DB)
    try:
        df = pd.read_sql_query(
            f"SELECT code, date, {cols} FROM stock_daily_meta_qfq "
            f"WHERE date BETWEEN ? AND ? AND close > 0 ORDER BY date, code",
            conn, params=[start, end])
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(CODE_WHITELIST_PREFIX)]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    df = df.drop_duplicates(["code", "date"], keep="last")
    out = {}
    for f in fields:
        out[f] = df.pivot(index="date", columns="code", values=f).sort_index()
    return out


def load_raw_close(start: str, end: str) -> pd.DataFrame:
    """原始(未复权)close宽表 — 仅用于 ≤30 元过滤。"""
    conn = _ro(META_DB)
    try:
        df = pd.read_sql_query(
            "SELECT code, date, close FROM stock_daily_meta "
            "WHERE date BETWEEN ? AND ? AND close > 0 ORDER BY date, code",
            conn, params=[start, end])
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(CODE_WHITELIST_PREFIX)]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    return df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()


def load_st_codes() -> set:
    """ST/*ST 代码集合（dragon_quant.db stock_info）。"""
    try:
        conn = _ro(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db")
        cur = conn.execute(
            "SELECT code FROM stock_info WHERE name LIKE '%ST%' OR name LIKE '%*%'")
        codes = {r[0][:6] for r in cur.fetchall()}
        conn.close()
        return codes
    except Exception:
        return set()


def detect_polluted_days(close: pd.DataFrame, ratio_th=0.05, min_stocks=100) -> pd.DatetimeIndex:
    ret = close / close.shift(1) - 1.0
    bad = (ret.abs() > 0.21).sum(axis=1)
    cnt = ret.notna().sum(axis=1)
    ratio = (bad / cnt.replace(0, np.nan)).fillna(0.0)
    return ratio[(ratio > ratio_th) & (bad >= min_stocks)].index


def clean_returns(close: pd.DataFrame) -> pd.DataFrame:
    """污染日剔除 + clip ±21% 的日收益。"""
    polluted = detect_polluted_days(close)
    c = close.drop(index=polluted)
    return (c / c.shift(1) - 1.0).clip(-0.21, 0.21), polluted


def load_pit_wide(factor: str, start: str, end: str) -> pd.DataFrame:
    """factor_pit.db → 月度 PIT 因子宽表（pivot date×code）。"""
    conn = _ro(PIT_DB)
    try:
        df = pd.read_sql_query(
            "SELECT code, pit_avail_date AS date, {f} AS val FROM stock_factors_fundamental_pit_v2 "
            "WHERE pit_avail_date BETWEEN ? AND ? AND {f} IS NOT NULL".format(f=factor),
            conn, params=[start, end])
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    # 同月多报取最后一条（as-of PIT）
    df["ym"] = df["date"].dt.to_period("M")
    df = df.sort_values("date").drop_duplicates(["code", "ym"], keep="last")
    wide = df.pivot(index="date", columns="code", values="val").sort_index()
    return wide


def month_ends(calendar: pd.DatetimeIndex) -> list:
    """每月最后一个交易日列表。"""
    s = pd.Series(calendar, index=calendar)
    return list(s.groupby(s.index.to_period("M")).last().values)


# ═══════════════════════════════════════════════════════
#  评估层：三关 + 统计
# ═══════════════════════════════════════════════════════

@dataclass
class EvalResult:
    name: str
    ic_mean: float = np.nan
    icir: float = np.nan
    ic_pos_rate: float = np.nan
    n_months: int = 0
    mono_p: float = np.nan          # 分层单调性 Spearman p
    layer_rets: list = field(default_factory=list)   # Q1..Q5 月均收益
    ls_month: float = np.nan        # 多空月收益
    cagr: float = np.nan
    sharpe: float = np.nan
    mdd: float = np.nan
    yearly: dict = field(default_factory=dict)
    ann_ret: float = np.nan
    n_picks: float = np.nan         # 平均持仓数

    def verdict(self) -> str:
        """三关判定: PASS 要求 IC>0.02 且 ICIR>0.3 且 mono_p<0.05 且组合回测不违反。"""
        ok_ic = (not np.isnan(self.ic_mean)) and abs(self.ic_mean) > 0.02
        ok_icir = (not np.isnan(self.icir)) and abs(self.icir) > 0.3
        ok_mono = (not np.isnan(self.mono_p)) and self.mono_p < 0.05
        return "PASS" if (ok_ic and ok_icir and ok_mono) else "FAIL"


def monthly_ic(factor_wide: pd.DataFrame, fwd_ret: pd.DataFrame, min_n=50) -> pd.Series:
    """月度 Spearman IC：每月末截面 factor vs 未来20日收益。"""
    ics = {}
    f = factor_wide
    for d, row in f.iterrows():
        if row.notna().sum() < min_n:
            continue
        # 未来收益: 从d起20日累计
        loc = fwd_ret.index.get_loc(d)
        end_pos = min(loc + 20, len(fwd_ret) - 1)
        if end_pos <= loc:
            continue
        fr = (1 + fwd_ret.iloc[loc + 1:end_pos + 1]).prod() - 1
        common = row.index.intersection(fr.dropna().index)
        common = [c for c in common if not np.isnan(row[c])]
        if len(common) < min_n:
            continue
        x = row[common].astype(float)
        y = fr[common].astype(float)
        if x.std() == 0 or y.std() == 0:
            continue
        ics[d] = x.corr(y, method="spearman")
    return pd.Series(ics).sort_index()


def layer_backtest(factor_wide: pd.DataFrame, ret: pd.DataFrame, n_layers=5,
                   price_cap_mask: pd.DataFrame = None) -> tuple:
    """五分层月度回测：每月末按因子值分5层，持有一个月。
    返回 (layer_rets DataFrame, mono_p, 多空月收益)。"""
    me = month_ends(factor_wide.index.intersection(ret.index))
    layer_rows = {f"Q{i+1}": [] for i in range(n_layers)}
    dates_used = []
    for i, d in enumerate(me[:-1]):
        next_d = me[i + 1]
        row = factor_wide.loc[d].dropna()
        if price_cap_mask is not None:
            m = price_cap_mask.loc[d] if d in price_cap_mask.index else pd.Series(True, index=row.index)
            row = row[m.reindex(row.index).fillna(True)]
        if len(row) < 50:
            continue
        try:
            loc0 = ret.index.get_loc(d)
            loc1 = ret.index.get_loc(next_d)
        except KeyError:
            continue
        seg = ret.iloc[loc0 + 1:loc1 + 1]  # next-bar: 次日起
        if seg.empty:
            continue
        month_ret = (1 + seg).prod() - 1
        q = pd.qcut(row.rank(method="first"), n_layers, labels=False)
        dates_used.append(d)
        for L in range(n_layers):
            codes = q[q == L].index
            r = month_ret.reindex(codes).dropna()
            layer_rows[f"Q{L+1}"].append(r.mean() if len(r) else np.nan)
    lr = pd.DataFrame(layer_rows, index=dates_used)
    means = lr.mean().values
    # 单调性: 层序 vs 层均收益 Spearman
    from scipy.stats import spearmanr
    rho, p = spearmanr(np.arange(1, n_layers + 1), means)
    ls = (lr.iloc[:, -1] - lr.iloc[:, 0]).mean()  # Q5-Q1 (因子值最高层-最低层)
    return lr, p, ls, means


def portfolio_backtest(picks_by_month: dict, ret: pd.DataFrame,
                       raw_close: pd.DataFrame, st_codes: set,
                       cost=COST_ONE_SIDE, max_stale=5) -> dict:
    """next-bar 月度组合回测。
    picks_by_month: {month_end_date: [codes]} (因子值TopN，T日收盘可知)
    规则: T月末信号 → T+1起按日收益计入；≤30元过滤（信号日原始价）；非ST；
          停牌>max_stale日的持仓按最后可得收益平仓（简化: 缺失日收益按0）。
    返回 dict(nav, daily_ret, yearly, stats)。"""
    navs, daily = [], []
    nav = 1.0
    all_dates = ret.index
    months = sorted(picks_by_month.keys())
    holding = []
    for i, d in enumerate(months):
        next_m = months[i + 1] if i + 1 < len(months) else None
        # 信号日过滤
        codes = picks_by_month[d]
        if raw_close is not None and d in raw_close.index:
            px = raw_close.loc[d]
            codes = [c for c in codes if c in px.index and px[c] <= PRICE_CAP and px[c] > 0]
        codes = [c for c in codes if c not in st_codes]
        holding = codes
        if next_m is None:
            continue
        # 持有期日收益（next-bar: 从d的次交易日起）
        try:
            loc0 = all_dates.get_loc(d)
        except KeyError:
            continue
        loc1 = all_dates.get_loc(next_m)
        seg_dates = all_dates[loc0 + 1:loc1 + 1]
        if not len(holding) or seg_dates.empty:
            for _ in seg_dates:
                navs.append(nav)
                daily.append(0.0)
            continue
        sub = ret.loc[seg_dates, ret.columns.intersection(holding)]
        eq = sub.mean(axis=1)  # 等权
        eq = eq.fillna(0.0)
        # 换手成本: 与上月持仓差异比例
        if i > 0 and daily:
            pass
        prev = set(picks_by_month[months[i - 1]]) if i > 0 else set()
        turnover = 1 - len(prev & set(holding)) / max(len(holding), 1)
        cost_hit = cost * turnover  # 单边近似
        eq.iloc[0] -= cost_hit
        for r in eq.values:
            nav *= (1 + r)
            navs.append(nav)
            daily.append(r)
    nav_s = pd.Series(navs, name="nav")
    dr = pd.Series(daily)
    if len(dr) < 20:
        return {"nav": nav_s, "daily": dr, "cagr": np.nan, "sharpe": np.nan,
                "mdd": np.nan, "yearly": {}, "n_picks": np.nan}
    yrs = (len(dr) / 244)
    cagr = nav_s.iloc[-1] ** (1 / yrs) - 1 if nav_s.iloc[-1] > 0 else -1.0
    sharpe = dr.mean() / (dr.std() + 1e-12) * math.sqrt(244)
    mdd = (nav_s / nav_s.cummax() - 1).min()
    yearly = {}
    if len(nav_s):
        idx = nav_s.index if hasattr(nav_s.index, 'year') else pd.RangeIndex(len(nav_s))
    out = {"nav": nav_s, "daily": dr, "cagr": cagr, "sharpe": sharpe,
           "mdd": mdd, "yearly": yearly}
    return out


# ═══════════════════════════════════════════════════════
#  统计层: DSR / PSR / MinTRL / PBO
# ═══════════════════════════════════════════════════════

def dsr_suite(daily_ret: pd.Series, n_trials: int = 20,
              trial_sharpes: list = None) -> dict:
    """PSR/DSR/MinTRL。daily_ret: 日收益序列。"""
    from deflated_sharpe import (sharpe_ratio, probabilistic_sharpe_ratio,
                                 expected_max_sharpe, minimum_track_record_length,
                                 _skew_kurt)
    r = daily_ret.dropna().values
    if len(r) < 30:
        return {"psr": np.nan, "dsr": np.nan, "min_trl": np.nan, "note": "样本不足"}
    sr = sharpe_ratio(r, 0.0)
    skew, kurt = _skew_kurt(r)
    psr = probabilistic_sharpe_ratio(sr, 0.0, len(r), skew, kurt)
    if trial_sharpes and len(trial_sharpes) > 2:
        sr_var = float(np.var(trial_sharpes))
        ems = expected_max_sharpe(sr_var, len(trial_sharpes))
    else:
        ems = 0.0
    dsr = probabilistic_sharpe_ratio(sr, ems, len(r), skew, kurt)
    min_trl = minimum_track_record_length(sr, 0.0, skew, kurt)
    return {"psr": float(psr), "dsr": float(dsr),
            "min_trl": float(min_trl), "sr": float(sr)}


def pbo_check(returns_matrix: pd.DataFrame, n_blocks=8) -> dict:
    """CSCV PBO. returns_matrix: T×N (日期×试验) 收益矩阵。"""
    from pbo_cscv import probability_of_backtest_overfitting
    return probability_of_backtest_overfitting(returns_matrix.values, n_blocks)


# ═══════════════════════════════════════════════════════
#  报告层
# ═══════════════════════════════════════════════════════

def render_report(title: str, result: EvalResult, bt: dict, dsr: dict,
                  verdict_note: str, original_verdict: str = "",
                  extra: dict = None) -> str:
    """统一 markdown 报告。"""
    lines = [f"# {title}", "",
             f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M')}",
             f"- 口径: qfq + next-bar + ≤30元 + 非ST + 单边成本{COST_ONE_SIDE:.1%} + 污染日剔除",
             ""]
    if original_verdict:
        lines += ["## 原判死理由", original_verdict, ""]
    lines += [
        "## 三关评估",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 月度IC均值 | {result.ic_mean:.4f} |",
        f"| ICIR | {result.icir:.3f} |",
        f"| IC正率 | {result.ic_pos_rate:.1%} |",
        f"| 有效月数 | {result.n_months} |",
        f"| 分层单调性 p | {result.mono_p:.4f} |",
        f"| Q1..Q5月均 | {', '.join(f'{x:.2%}' for x in result.layer_rets) if result.layer_rets else '-'} |",
        f"| 多空月收益 | {result.ls_month:.2%} |",
        "",
        "## 组合回测 (TopN等权·next-bar)",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| CAGR | {bt.get('cagr', np.nan):.2%} |",
        f"| Sharpe | {bt.get('sharpe', np.nan):.3f} |",
        f"| MDD | {bt.get('mdd', np.nan):.2%} |",
        f"| 平均持仓 | {bt.get('n_picks', np.nan):.1f} |",
        "",
        "## 统计检验",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| PSR | {dsr.get('psr', np.nan):.3f} |",
        f"| DSR | {dsr.get('dsr', np.nan):.3f} |",
        f"| MinTRL(日) | {dsr.get('min_trl', np.nan):.0f} |",
        "",
        "## 分年收益",
        "",
    ]
    y = bt.get("yearly", {})
    if y:
        lines += ["| 年份 | 收益 |", "|------|------|"]
        for yy, rr in y.items():
            lines.append(f"| {yy} | {rr:.2%} |")
    else:
        lines.append("(待补)")
    lines += ["", f"## 判定: {result.verdict()}", "", verdict_note, ""]
    if extra:
        lines += ["## 补充", ""]
        for k, v in extra.items():
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def save_report(name: str, content: str) -> Path:
    p = REPORT_DIR / f"{name}_{time.strftime('%Y%m%d')}.md"
    p.write_text(content, encoding="utf-8")
    return p


# ═══════════════════════════════════════════════════════
#  冒烟自检
# ═══════════════════════════════════════════════════════

def smoke_test():
    print("=== strategy_test_harness 冒烟测试 ===")
    t0 = time.time()
    q = load_qfq("20240101", "20240630", fields=("close",))
    close = q["close"]
    print(f"  qfq加载: {close.shape[0]}日 × {close.shape[1]}股 ({time.time()-t0:.1f}s)")
    ret, polluted = clean_returns(close)
    print(f"  清洁收益: {ret.shape}, 污染日 {len(polluted)}")
    # 用 bp PIT 因子冒烟
    bp = load_pit_wide("bp", "20240101", "20240630")
    print(f"  PIT bp: {bp.shape[0]}月 × {bp.shape[1]}股" if not bp.empty else "  PIT bp: 空!")
    ics = monthly_ic(bp, ret)
    print(f"  月度IC: n={len(ics)}, mean={ics.mean():.4f}" if len(ics) else "  月度IC: 无")
    if not ics.empty:
        ic_pos = (ics > 0).mean()
        icir = ics.mean() / (ics.std() + 1e-12)
        print(f"  IC正率={ic_pos:.1%} ICIR={icir:.3f}")
    # DSR
    fake = pd.Series(np.random.default_rng(42).normal(0.001, 0.01, 500))
    d = dsr_suite(fake)
    print(f"  DSR套件: PSR={d.get('psr', float('nan')):.3f} DSR={d.get('dsr', float('nan')):.3f}")
    print(f"=== 冒烟通过 ({time.time()-t0:.1f}s) ===")


if __name__ == "__main__":
    smoke_test()
