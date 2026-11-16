# -*- coding: utf-8 -*-
"""
rps_rotation.py — 板块涨幅轮动矩阵(RPS) + 主线跟踪摘要 (移植自 tickflow-stock-panel)
===================================================================================
来源: TSP backend/app/services/rps_rotation.py — 概念涨幅轮动矩阵思想
适配: 屠龙系统 CLI 版, pandas + 主库 dragon_quant.db, 无新依赖。

算法(与TSP同口径):
  个股日涨幅 → join 维度映射(一股多概念多行) → 按(日期, 维度)平均涨幅
  → 每日全维度降序排名 → 轮动矩阵 + RPS动量摘要(主线判定)

数据源:
  industry      : 主库 stock_info.industry (东财110行业, 4586股覆盖)
  concept       : 同花顺概念(FUND_DB concept_map, import_tsp_ext_data 常态化入库)
  industry_ths  : 同花顺行业(FUND_DB industry_map_ths, 2026-08-15 新增)
  --from-db (默认 True): 从 FUND_DB 读, 脱离 TSP parquet 依赖
  --from-db=False      : 回退 TSP 容器 parquet 快照(兼容旧环境)

用法:
  python rps_rotation.py                     # 行业矩阵 近12交易日 Top15
  python rps_rotation.py --days 20 --top 20  # 更长窗口更多行
  python rps_rotation.py --kind concept      # 同花顺概念矩阵(FUND_DB)
  python rps_rotation.py --kind industry_ths # 同花顺行业矩阵
  python rps_rotation.py --json out.json     # 机器可读输出(喂 daily_review/主线雷达)

输出三块:
  1) 轮动矩阵热力表: 行=维度(近5日平均RPS降序), 列=交易日(新→旧), 单元格=平均涨幅%
  2) 主线摘要: 今日Top10 / 5日平均RPS Top10(主线) / 新晋 / 退潮 / 最长连榜
  3) [--json] 全量矩阵+摘要
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
TSP_CONCEPT_PARQUET = Path(r"D:\zcodesj\tickflow-stock-panel\data\ext_data\ext_gn_ths\part.parquet")
TSP_INDUSTRY_PARQUET = Path(r"D:\zcodesj\tickflow-stock-panel\data\ext_data\ext_hy_ths\part.parquet")


def load_pct_matrix(days: int) -> pd.DataFrame:
    """最近 N 交易日(按日历天*2+15 余量拉取) → DataFrame[code, date, pct]。"""
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    maxd = con.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0]
    # 余量: N交易日 ≈ 1.5N 日历天, 再+15 缓冲首日 prev
    start = int((pd.Timestamp(str(maxd)) - pd.Timedelta(days=int(days * 1.5) + 15)).strftime("%Y%m%d"))
    df = pd.read_sql_query(
        "SELECT code, date, close FROM stock_daily_hsj WHERE date >= ? AND vol > 0",
        con, params=(start,), parse_dates=None,
    )
    con.close()
    df = df.sort_values(["code", "date"], kind="mergesort")
    df["prev"] = df.groupby("code")["close"].shift(1)
    df = df.dropna(subset=["prev"])
    df["pct"] = (df["close"] / df["prev"] - 1.0) * 100
    # 只留最近 N 个交易日
    keep_dates = sorted(df["date"].unique())[-days:]
    return df[df["date"].isin(keep_dates)][["code", "date", "pct"]]


def load_dimension_map(kind: str, *, from_db=True) -> pd.DataFrame:
    """→ DataFrame[code, dim] (一股多行)。

    from_db=True (默认): concept/industry_ths 从 FUND_DB 读 (import_tsp_ext_data 入库后),
      不依赖 TSP parquet。主库 industry 从 MAIN_DB stock_info 读。
    from_db=False: 回退到 TSP parquet 快照 (兼容旧环境)。
    """
    _FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"

    if kind == "industry":
        con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
        m = pd.read_sql_query(
            "SELECT code, industry AS dim FROM stock_info "
            "WHERE industry IS NOT NULL AND industry != ''", con)
        con.close()
        return m

    if from_db:
        table = "concept_map" if kind == "concept" else "industry_map_ths"
        col = "concept" if kind == "concept" else "industry"
        try:
            con = sqlite3.connect(f"file:{_FUND_DB}?mode=ro", uri=True)
            m = pd.read_sql_query(
                f"SELECT code, {col} AS dim FROM {table}", con)
            con.close()
            if not m.empty:
                return m
        except Exception:
            pass
        # DB 空/表不存在 → fallback
        if kind == "concept" and from_db:
            print("[info] concept_map 表空或不存在, 回退 parquet; 先跑 import_tsp_ext_data.py")

    # concept/industry_ths: TSP 同花顺概念快照 (fallback)
    pq = TSP_CONCEPT_PARQUET if kind == "concept" else TSP_INDUSTRY_PARQUET
    if not pq.exists():
        print(f"[error] {kind} 快照不存在: {pq}\n"
              f"        请启动 TSP 容器或先跑 import_tsp_ext_data.py" if kind == "concept" else "")
        raise SystemExit(2)
    src = pd.read_parquet(pq)
    col = "所属概念" if "所属概念" in src.columns else src.columns[2]
    pairs = []
    for code, concepts in zip(src["code"].astype(str).str.zfill(6), src[col]):
        for c in str(concepts or "").split(";"):
            c = c.strip()
            if c:
                pairs.append((code, c))
    return pd.DataFrame(pairs, columns=["code", "dim"])


def build_rotation(days: int, kind: str, *, from_db=True) -> dict:
    """核心: 个股涨幅 → 维度平均 → 每日排名 → 矩阵+摘要。"""
    pct = load_pct_matrix(days)
    dmap = load_dimension_map(kind, from_db=from_db)
    j = pct.merge(dmap, on="code", how="inner")
    # 维度日覆盖守卫: 成分股<4 的维度当日噪声大, 剔除(TSP sector 监控同思想: 覆盖率门槛)
    cnt = j.groupby(["date", "dim"])["code"].nunique()
    good = cnt[cnt >= 4].index
    j = j.set_index(["date", "dim"]).loc[good].reset_index()
    agg = j.groupby(["date", "dim"])["pct"].mean().rename("avg_pct").reset_index()
    agg["rank"] = agg.groupby("date")["avg_pct"].rank(ascending=False, method="min").astype(int)
    agg["n_dim"] = agg.groupby("date")["dim"].transform("count")
    agg["rps"] = (1.0 - agg["rank"] / agg["n_dim"]) * 100  # 0-100, 100=当日最强

    matrix = agg.pivot_table(index="dim", columns="date", values="avg_pct")
    matrix = matrix[sorted(matrix.columns, reverse=True)]  # 新→旧

    # ── 主线摘要 ──
    top10_by_day = {d: set(g.sort_values("rank")["dim"].head(10)) for d, g in agg.groupby("date")}
    dates = sorted(top10_by_day)  # 旧→新
    today, prev_dates = dates[-1], dates[:-1]

    def streak(dim: str) -> int:
        """连续(含今日)出现在Top10的天数。"""
        s = 0
        for dd in reversed(dates):
            if dim in top10_by_day[dd]:
                s += 1
            else:
                break
        return s

    top10_today = top10_by_day[today]
    rps5 = agg[agg["date"].isin(dates[-5:])].groupby("dim")["rps"].mean().sort_values(ascending=False)
    mainline = [(k, round(v, 1)) for k, v in rps5.head(10).items()]

    prev4 = set().union(*[top10_by_day[d] for d in prev_dates[-4:]]) if prev_dates else set()
    emerging = sorted(top10_today - prev4)
    was3 = set.intersection(*[top10_by_day[d] for d in prev_dates[-3:]]) if len(prev_dates) >= 3 else set()
    fading = sorted(was3 - top10_today)
    streaks = sorted(((streak(d), d) for d in top10_today), reverse=True)[:5]

    return {
        "kind": kind, "days": days,
        "dates": [str(d) for d in matrix.columns],
        "matrix": matrix, "mainline": mainline,
        "top10_today": sorted(top10_today), "emerging": emerging,
        "fading": fading, "streaks": streaks,
        "n_dim_total": int(agg["dim"].nunique()),
    }


def _heat(v: float) -> str:
    """单元格: 涨幅% + 色阶字符。"""
    if pd.isna(v):
        return "   ·  "
    ch = "█" if v >= 3 else "▓" if v >= 1.5 else "▒" if v > 0 else "░" if v > -1.5 else "▁" if v > -3 else "▂"
    return f"{v:+5.1f}{ch}"


def print_report(r: dict, top: int) -> None:
    m: pd.DataFrame = r["matrix"]
    # 矩阵列已按 新→旧 排; 行按近5日平均涨幅排, 与主线摘要口径一致
    row_score = m.iloc[:, :5].mean(axis=1).sort_values(ascending=False)
    show = row_score.head(top).index
    dates = list(m.columns)  # 新→旧, dates[0]=最新交易日
    print(f"\n=== {'概念' if r['kind'] == 'concept' else '行业'}涨幅轮动矩阵 "
          f"({r['n_dim_total']}个维度 × {len(dates)}交易日, {dates[-1]}→{dates[0]}) ===")
    hdr = "        " + "".join(str(d)[4:] for d in dates)  # MMDD
    print(hdr)
    for dim in show:
        row = "".join(_heat(m.loc[dim, d]) + " " for d in dates)
        print(f"{dim[:6]:>6} {row}")

    print(f"\n── 今日Top10 ({dates[0]}): {' | '.join(r['top10_today'])}")
    print("── 近5日平均RPS主线Top10:")
    for i, (k, v) in enumerate(r["mainline"], 1):
        print(f"   {i:>2}. {k:<14} RPS={v}")
    if r["emerging"]:
        print(f"── 新晋(今日Top10且近4日从未): {' | '.join(r['emerging'])}")
    if r["fading"]:
        print(f"── 退潮(前3日连续Top10今日跌出): {' | '.join(r['fading'])}")
    if r["streaks"]:
        print("── 连榜(连续Top10天数): " + ", ".join(f"{d}×{s}" for s, d in r["streaks"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="板块RPS轮动矩阵+主线跟踪(移植自TSP)")
    ap.add_argument("--days", type=int, default=12, help="交易日窗口(默认12)")
    ap.add_argument("--kind", choices=["industry", "concept", "industry_ths"], default="industry")
    ap.add_argument("--top", type=int, default=15, help="矩阵显示行数")
    ap.add_argument("--from-db", action=argparse.BooleanOptionalAction, default=True,
                    help="默认从 FUND_DB 读概念映射; --no-from-db 回退 TSP parquet")
    ap.add_argument("--json", help="机器可读输出路径")
    a = ap.parse_args()

    res = build_rotation(max(5, min(30, a.days)), a.kind, from_db=a.from_db)
    print_report(res, a.top)
    if a.json:
        out = {
            "kind": res["kind"], "days": res["days"], "dates": res["dates"],
            "matrix": {str(k): {str(d): (None if pd.isna(v) else round(float(v), 3))
                                for d, v in row.items()} for k, row in res["matrix"].iterrows()},
            "mainline": res["mainline"], "top10_today": res["top10_today"],
            "emerging": res["emerging"], "fading": res["fading"],
            "streaks": res["streaks"],
        }
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[json] → {a.json}")
