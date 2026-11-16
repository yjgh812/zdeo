#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
combo_b_list_generator.py — B组试验组合建仓清单生成器（droe+roa WF-icir）

A/B 试验设计（2026-08-15 主人批准）:
  A组(影子): bp+ep Top20 影子组合（不占资金，仅对比）
  B组(实盘): droe+roa WF-icir 组合 Top20（卫星仓 50%）
  周期: 月度换仓，一个月后对比收益/回撤/胜率

清单生成规则:
  1. droe/roa 最新截面 rank-z 标准化
  2. WF-icir 权重: 用截至最近可算 IC 的 500 日历史（fwd 20D，防未来）
  3. 过滤: 价格≤30 / 非ST / 非已持仓（避免A/B重复）/ 剔除688/920（行业映射外+流动性）
  4. Top20 等权
  5. 名称经 stock_info 机械校验（铁律: 代码必带真实名称）

用法:
    python combo_b_list_generator.py                # B组 Top20
    python combo_b_list_generator.py --top 15       # 数量调整
    python combo_b_list_generator.py --both         # 同时生成A组影子清单
    python combo_b_list_generator.py --pit          # droe/roa 改读 factor_pit.db v2（PIT无前视）
"""
# --pit 说明（2026-08-15 准备性工程，主人未确认切源前默认关闭）:
#   默认路径: FACTOR_REGISTRY → finance_daily 按 report_date 前向填充（含前视）
#   --pit路径: factor_pit.db.stock_factors_fundamental_pit_v2，as-of 语义 =
#     pit_avail_date <= T 的最新 report_date（同 code 同 avail 日取更晚报告期），
#     陈旧度守卫 (T - report_date) <= 400 自然日（与 get_pit_factor_v2/W1影子轨一致）。
#   行为零变化: 不加 --pit 时与旧版逐字节等价。
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import alphalens_factor_diagnosis as afd  # noqa: E402
from alphalens_factor_diagnosis import FACTOR_REGISTRY  # noqa: E402

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
ATT_DB = r"D:\zcodesj\attribution\attribution.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
PIT_SHADOW_DB = r"D:\zcodesj\pit_shadow_track.db"
PIT_TABLE = "stock_factors_fundamental_pit_v2"   # droe 用 v2 更准（W1影子轨结论）
PIT_STALE_DAYS = 400                              # 报告期距T最大自然日陈旧度
REPORT_DIR = Path(r"D:\zcodesj\reports")
REPORT_DIR.mkdir(exist_ok=True)

MAX_PRICE = 30.0
FWD = 20
TRAIN = 500


def cs_rank_z(col: pd.Series) -> pd.Series:
    ranked = col.groupby(level="date").rank()
    n = col.groupby(level="date").transform("count")
    return (ranked - (n + 1) / 2) / (n / np.sqrt(12))


def load_name_map() -> dict[str, str]:
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=30)
    try:
        rows = conn.execute("SELECT code, name FROM stock_info").fetchall()
    finally:
        conn.close()
    return {r[0][:6]: r[1] for r in rows}


def load_current_holdings() -> set[str]:
    """账本推导当前持仓（westock 授权恢复后核实）"""
    conn = sqlite3.connect(ATT_DB)
    try:
        rows = conn.execute(
            "SELECT code, action, volume FROM trade_records WHERE trade_date >= '20260810'"
        ).fetchall()
    finally:
        conn.close()
    hold = {}
    for code, action, vol in rows:
        c = code[-6:]
        hold[c] = hold.get(c, 0) + (vol if action == "BUY" else -vol)
    return {c for c, v in hold.items() if v > 0}


def icir_weights(ic_hist: pd.DataFrame) -> dict[str, float]:
    mu = ic_hist.mean()
    sd = ic_hist.std()
    icir = mu / sd.replace(0, np.nan)
    w = icir / icir.abs().sum()
    return {k: round(float(v), 4) for k, v in w.items()}


# ═══════════════════════════════════════════════════════
#  PIT 因子加载（--pit 开启时替代 droe/roa 的 registry loader）
# ═══════════════════════════════════════════════════════

PIT_FACTORS = ("droe", "roa")
_PIT_CACHE: pd.DataFrame | None = None


def _load_pit_snapshots() -> pd.DataFrame:
    """加载 factor_pit.db v2 全量快照（单次加载，droe/roa 共享）"""
    global _PIT_CACHE
    if _PIT_CACHE is not None:
        return _PIT_CACHE.copy()
    conn = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            f"SELECT code, report_date, pit_avail_date, droe, roa FROM {PIT_TABLE} "
            f"WHERE pit_avail_date IS NOT NULL", conn)
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["avail"] = pd.to_datetime(df["pit_avail_date"].astype(str), format="%Y%m%d")
    df["rd"] = pd.to_datetime(df["report_date"].astype(str), format="%Y%m%d")
    _PIT_CACHE = df
    return df.copy()


def load_pit_factor(factor: str, date_slice: pd.DatetimeIndex) -> pd.Series:
    """PIT as-of 截面序列: T 日可用 = pit_avail_date<=T 的最新 report_date 快照。

    语义与 build_factor_pit_20260815.get_pit_factor_v2 一致（同 code 同
    pit_avail_date 并列时取更晚 report_date，即年报与Q1同日披露取Q1），
    另加陈旧度守卫 (T - report_date) <= PIT_STALE_DAYS 自然日。
    返回 MultiIndex(date, code) Series，与 registry loader 同构。
    """
    df = _load_pit_snapshots()
    df = df.dropna(subset=[factor])
    df = (df.sort_values(["code", "avail", "rd"])
            .drop_duplicates(subset=["code", "avail"], keep="last"))
    piv_f = df.pivot_table(index="code", columns="avail", values=factor,
                           aggfunc="first").sort_index(axis=1)
    piv_rd = df.pivot_table(index="code", columns="avail", values="rd",
                            aggfunc="first").sort_index(axis=1)
    piv_f = piv_f.reindex(columns=date_slice, method="ffill")
    piv_rd = piv_rd.reindex(columns=date_slice, method="ffill")
    # 陈旧度守卫: (T - report_date) 自然日 <= PIT_STALE_DAYS
    age = (date_slice.values[None, :].astype("datetime64[ns]")
           - piv_rd.values.astype("datetime64[ns]")).astype("timedelta64[D]")
    piv_f = piv_f.where(age.astype(int) <= PIT_STALE_DAYS)
    result = piv_f.stack()
    result.index.names = ["code", "date"]
    result = result.reorder_levels(["date", "code"]).sort_index()
    result.name = "factor"
    return result


def build_list(factor_names: list[str], top_n: int, exclude: set[str],
               name_map: dict, st_codes: set[str], prices: pd.DataFrame,
               pit: bool = False) -> tuple[pd.DataFrame, dict]:
    """生成组合清单: 返回 (清单DataFrame, 元信息)。pit=True 时 droe/roa 走 PIT 源"""
    date_slice = prices.index
    afd.START_DATE = (date_slice[0] - pd.Timedelta(days=400)).strftime("%Y%m%d")
    afd.END_DATE = date_slice[-1].strftime("%Y%m%d")

    fwd = (prices.shift(-FWD) / prices - 1).stack()
    fwd.index.names = ["date", "asset"]

    latest_date = date_slice[-1]
    day_prices = prices.loc[latest_date]

    z_latest, ic_hist = {}, {}
    for name in factor_names:
        desc, loader = FACTOR_REGISTRY[name]
        if pit and name in PIT_FACTORS:
            print(f"  📡 {name} ({desc}) [PIT v2]...", end="", flush=True)
            s = load_pit_factor(name, date_slice)
        else:
            print(f"  📡 {name} ({desc})...", end="", flush=True)
            s = loader(date_slice)
        z = cs_rank_z(s).dropna()
        z_latest[name] = z.xs(latest_date, level="date")
        merged = pd.concat([z.rename("f"), fwd.rename("r")], axis=1, join="inner").dropna()
        ics = {}
        for d, sub in merged.groupby(level="date"):
            if len(sub) >= 30:
                ic, _ = spearmanr(sub["f"], sub["r"])
                if not np.isnan(ic):
                    ics[d] = ic
        ic_hist[name] = pd.Series(ics).sort_index()
        print(f" IC天数={len(ics)} 最新截面={len(z_latest[name])}")

    ic_df = pd.DataFrame(ic_hist).dropna().tail(TRAIN)
    weights = icir_weights(ic_df)

    # 打分（等权方案用同一框架）
    score = sum(z_latest[f] * w for f, w in weights.items() if f in z_latest).dropna()

    # 过滤
    valid = []
    for c in score.index:
        if c in st_codes or c in exclude:
            continue
        if c.startswith(("688", "689", "920", "8", "4", "9")):
            continue  # 科创/北交（行业映射外）
        if c not in day_prices.index or np.isnan(day_prices[c]) or day_prices[c] > MAX_PRICE:
            continue
        valid.append(c)
    score = score[valid]

    top = score.nlargest(top_n)
    rows = []
    for rank, (code, sc) in enumerate(top.items(), 1):
        px = float(day_prices[code])
        rows.append({
            "rank": rank, "code": code, "name": name_map.get(code, "?"),
            "price": round(px, 2), "score": round(float(sc), 3),
        })
    return pd.DataFrame(rows), {"weights": weights, "date": str(latest_date.date()),
                                "universe": len(valid), "ic_days": len(ic_df)}


def main() -> int:
    ap = argparse.ArgumentParser(description="B组试验组合清单生成器")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--both", action="store_true", help="同时生成A组(bp+ep)影子清单")
    ap.add_argument("--pit", action="store_true",
                    help="droe/roa 改读 factor_pit.db v2（PIT无前视），并把B组清单写入 "
                         "pit_shadow_track.db(strategy=b_group_pit_v2)。默认关闭，行为零变化")
    a = ap.parse_args()

    t0 = time.time()
    print(f"\n{'#' * 66}")
    print(f"  A/B 试验组合清单生成器 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'#' * 66}")

    # 最新交易日价格（回看600日给IC用）
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=60)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM stock_daily_meta ORDER BY date DESC LIMIT 620")]
    df = pd.read_sql_query(
        "SELECT code, date, close FROM stock_daily_meta WHERE date BETWEEN ? AND ? AND close > 0",
        conn, params=[min(dates), max(dates)])
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    prices = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    print(f"\n  价格截至: {prices.index[-1].date()} ({len(prices.index)}日 × {prices.shape[1]}股)")

    name_map = load_name_map()
    st_codes = load_st = {r[0][:6] for r in sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True).execute(
        "SELECT code FROM stock_info WHERE is_st=1 OR name LIKE '%ST%'").fetchall()}
    holdings = load_current_holdings()
    print(f"  ST剔除 {len(st_codes)} | 当前持仓 {len(holdings)} 只（排除重复）")

    # ── B组 ──
    print(f"\n▶ B组（droe+roa WF-icir）Top{a.top}{' [PIT v2]' if a.pit else ''}:")
    b_df, b_meta = build_list(["droe", "roa"], a.top, holdings, name_map, st_codes, prices, pit=a.pit)
    print(f"  权重: {b_meta['weights']} | 基准日 {b_meta['date']} | 备选池 {b_meta['universe']}")

    # ── A组（可选影子） ──
    a_df = None
    if a.both:
        print(f"\n▶ A组影子（bp_ep 等权z）Top{a.top}:")
        a_df, a_meta = build_list(["bp_ep"], a.top, holdings, name_map, st_codes, prices)
        print(f"  基准日 {a_meta['date']} | 备选池 {a_meta['universe']}")

    # ── 名称校验（铁律） ──
    print(f"\n▶ 名称机械校验:")
    all_codes = dict(zip(b_df["code"], b_df["name"]))
    if a_df is not None:
        all_codes.update(dict(zip(a_df["code"], a_df["name"])))
    bad = []
    for c, nm in all_codes.items():
        true_nm = name_map.get(c)
        if true_nm is None:
            bad.append((c, nm, "查无此股"))
        elif true_nm != nm:
            bad.append((c, nm, f"应为{true_nm}"))
    if bad:
        print("  ❌ 名称错误，禁止发布！")
        for c, nm, why in bad:
            print(f"     {c} {nm} → {why}")
        return 1
    print(f"  ✅ {len(all_codes)} 只名称全部与 stock_info 一致")

    # ── 输出 ──
    pit_tag = "（PIT v2源）" if a.pit else ""
    print(f"\n{'=' * 66}")
    print(f"  B组建仓清单（droe+roa WF-icir Top{a.top}）{pit_tag}基准 {b_meta['date']}")
    print(f"{'=' * 66}")
    print(f"{'名次':<4}{'代码':<8}{'名称':<8}{'现价':>8}{'分数':>8}")
    print("-" * 40)
    for _, r in b_df.iterrows():
        print(f"{r['rank']:<4}{r['code']:<8}{r['name']:<8}{r['price']:>8}{r['score']:>+8.2f}")

    if a_df is not None:
        print(f"\n{'=' * 66}")
        print(f"  A组影子清单（bp_ep Top{a.top}，不占资金）")
        print(f"{'=' * 66}")
        for _, r in a_df.iterrows():
            print(f"{r['rank']:<4}{r['code']:<8}{r['name']:<8}{r['price']:>8}{r['score']:>+8.2f}")

    # 落盘
    today = datetime.now().strftime("%Y%m%d")
    rpt = REPORT_DIR / (f"COMBO_EXPERIMENT_LIST_{today}_pit.md" if a.pit
                        else f"COMBO_EXPERIMENT_LIST_{today}.md")
    lines = [f"# A/B 试验组合清单 {datetime.now().isoformat(timespec='seconds')}", "",
             f"- 基准日: {b_meta['date']} | B组权重(WF-icir): {b_meta['weights']}",
             f"- 规则: 价格≤{MAX_PRICE:.0f} / 非ST / 非科创北交 / 排除当前持仓 / Top{a.top}等权", "",
             "## B组（droe+roa WF-icir，实盘待主人确认）", "",
             "| 名次 | 代码 | 名称 | 现价 | 分数 |", "|--:|:--|:--|--:|--:|"]
    for _, r in b_df.iterrows():
        lines.append(f"| {r['rank']} | {r['code']} | {r['name']} | {r['price']} | {r['score']:+.2f} |")
    if a_df is not None:
        lines += ["", "## A组影子（bp_ep，仅对比不下单）", "",
                  "| 名次 | 代码 | 名称 | 现价 | 分数 |", "|--:|:--|:--|--:|--:|"]
        for _, r in a_df.iterrows():
            lines.append(f"| {r['rank']} | {r['code']} | {r['name']} | {r['price']} | {r['score']:+.2f} |")
    rpt.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 清单落盘: {rpt}  (耗时 {time.time()-t0:.0f}s)")

    # ── --pit: 写入 pit_shadow_track.db 影子轨（供9月初rebase与主人裁决） ──
    if a.pit:
        base_date = int(b_meta["date"].replace("-", ""))
        conn = sqlite3.connect(PIT_SHADOW_DB)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS shadow_lists (
                date INTEGER NOT NULL, strategy TEXT NOT NULL, code TEXT NOT NULL,
                name TEXT NOT NULL, rank INTEGER, factor_value REAL, source TEXT,
                PRIMARY KEY (date, strategy, code))""")
            conn.execute("DELETE FROM shadow_lists WHERE date=? AND strategy='b_group_pit_v2'",
                         [base_date])
            conn.executemany(
                "INSERT INTO shadow_lists (date, strategy, code, name, rank, factor_value, source) "
                "VALUES (?,?,?,?,?,?,?)",
                [(base_date, "b_group_pit_v2", r["code"], r["name"], int(r["rank"]),
                  float(r["score"]), f"combo_b_list_generator --pit: factor_pit.db:{PIT_TABLE}")
                 for _, r in b_df.iterrows()])
            conn.commit()
        finally:
            conn.close()
        print(f"✅ PIT影子轨入库: pit_shadow_track.db strategy=b_group_pit_v2 date={base_date} "
              f"Top{len(b_df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())