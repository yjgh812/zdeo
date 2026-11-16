#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""hotspot_breakout 主线层增量评估 · Step C：形态∩主线 子集构建（系统 python）。

背景：VBT_HOTSPOT_PRESCREEN_20260828 证明形态层无 edge（45 组全负）。本步评估生产
picker 的关键增量层——主线过滤（漏斗6：一级行业 ∈ 当日涨停≥5 主线集合）。

数据源决策（勘查结论）：
  - attribution.db limit_up_patterns 仅覆盖 20260810 后 15 个交易日 → 5 年回放不可用
  - 改用 qfq 库自算涨停池：close >= round(prev_close×(1+lim),2)（与 picker 生产
    scan_qfq_candidates 涨停判定同口径同数据源）；主线 = ths 一级行业当日涨停≥5

与生产口径的已知偏差（报告注明）：
  1. 生产 screener 先按粗产业(tags.industry)聚合≥5 再映射 ths 一级（会纳入
     "产业合计≥5 但单一 ths 一级<5"的行业）；本回放直接 ths 一级聚合≥5，略严
  2. 行业映射用 industry_map_ths 当前快照，历史行业漂移不回追
  3. 涨停池用全市场（含科创北交）计行业热度，候选过滤仍限屠龙宇宙
  4. 市值层（P91）：daily_basic 2024-01-15 起密集覆盖；此前及缺记录 fail-open 放行
     （与 picker load_circ_mv 失败放行的生产语义一致）

A2 对账：对范式库覆盖日（20260810~20260828），qfq 自算涨停集合 vs limit_up_patterns
涨停股集合求交集占比——验证涨停识别正确性（主线聚合的输入层）。

输出：向 _cache/vbt_hotspot_prescreen_signals.db 追加 P90（BASE∩主线）/P91（P90∩市值）。

运行：python vbt_hotspot_prescreen_step_c.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
SIG_DB = Path(r"D:\zcodesj\_cache\vbt_hotspot_prescreen_signals.db")

WARM_START = "20210501"
SIG_START = "20210828"
SIG_END = "20260827"
MAINLINE_MIN = 5          # 对齐 screener MAINLINE_MIN
FLOAT_MV_CAP = 100.0      # 对齐 picker FLOAT_MV_CAP
MV_EFFECTIVE_FROM = "20240115"  # daily_basic 密集覆盖起点


def board_lim(code: str) -> float:
    c = str(code)
    if c.startswith(("300", "301", "688")):
        return 20.0
    if c.startswith(("8", "920", "4")):
        return 30.0
    return 10.0


def load_ind1() -> tuple[dict[str, str], dict[str, str]]:
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    rows = con.execute("SELECT code, name, industry FROM industry_map_ths").fetchall()
    con.close()
    ind1: dict[str, str] = {}
    names: dict[str, str] = {}
    for code, name, ind in rows:
        c6 = str(code).zfill(6)
        if name:
            names[c6] = str(name).strip()
        a = (ind or "").split("-")[0].strip()
        if a:
            ind1[c6] = a
    return ind1, names


def daily_limitup_by_ind1() -> pd.DataFrame:
    """全市场逐日涨停 → ths 一级行业聚合计数（含科创北交计热度）。"""
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    df = pd.read_sql_query(
        "SELECT code,date,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? ORDER BY code,date",
        con, params=(WARM_START, SIG_END))
    con.close()
    df["code"] = df["code"].map(lambda c: str(c).split(".")[0].zfill(6))
    g = df.sort_values(["code", "date"])
    g["prev_close"] = g.groupby("code")["close"].shift(1)
    g = g.dropna(subset=["prev_close"])
    g["lim"] = g["code"].map(board_lim)
    g["limit_price"] = (g["prev_close"] * (1.0 + g["lim"] / 100.0)).round(2)
    zt = g[g["close"] >= g["limit_price"]].copy()
    zt["ind1"] = zt["code"].map(lambda c: IND1.get(c, ""))
    zt = zt[zt["ind1"] != ""]
    agg = zt.groupby(["date", "ind1"]).size().reset_index(name="n_zt")
    # 每日涨停集合也留一份（A2 对账用）
    return agg, zt[["date", "code"]]


def a2_verify(zt_pairs: pd.DataFrame) -> bool:
    """A2：范式库覆盖日，qfq 自算涨停股集合 vs limit_up_patterns 集合交集占比。"""
    con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=30)
    pat = pd.read_sql_query(
        "SELECT trade_date, code FROM limit_up_patterns", con)
    con.close()
    pat["code"] = pat["code"].map(lambda c: str(c).split(".")[0].zfill(6))
    pat = pat[pat["trade_date"] <= SIG_END]  # 涨停池仅算到 SIG_END，8/28 范式行不在对比窗
    mine = set(zip(zt_pairs["date"], zt_pairs["code"]))
    ratios = []
    for d, sub in pat.groupby("trade_date"):
        theirs = {(d, c) for c in sub["code"]}
        inter = len(theirs & mine)
        ratios.append((d, len(theirs), inter, inter / max(len(theirs), 1)))
    print("[A2] qfq自算涨停 vs 范式库（日期, 范式库只数, 交集, 占比）:")
    ok = True
    for d, n, i, r in sorted(ratios):
        print(f"  {d}: {n:>3} 只, 交集 {i:>3}, 占比 {r:.1%}")
        if r < 0.80:
            ok = False
    return ok


IND1: dict[str, str] = {}


def main() -> int:
    t0 = time.time()
    global IND1
    IND1, _names = load_ind1()
    print(f"[info] ths 一级行业映射: {len(IND1)} 只")

    agg, zt_pairs = daily_limitup_by_ind1()
    print(f"[info] 涨停池自算完成: {len(zt_pairs)} 行（全市场含科创北交），{time.time()-t0:.0f}s")

    # A2 涨停识别对账（先于子集产出）
    if not a2_verify(zt_pairs):
        print("[FAIL] A2 涨停识别交集占比 <80%，禁止产出主线子集")
        return 2
    print("[PASS] A2 涨停识别对账")

    # 每日主线行业集合
    mainline = agg[agg["n_zt"] >= MAINLINE_MIN]
    ml_map: dict[str, set[str]] = {}
    for d, sub in mainline.groupby("date"):
        ml_map[str(d)] = set(sub["ind1"])
    in_win = [d for d in ml_map if SIG_START <= d <= SIG_END]
    n_days_with_ml = len(in_win)
    avg_ml = sum(len(ml_map[d]) for d in in_win) / max(n_days_with_ml, 1)
    print(f"[info] 信号期主线覆盖: {n_days_with_ml} 个交易日有主线，平均 {avg_ml:.1f} 个主线行业/日")

    # 市值表（2024 起）
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    mv = pd.read_sql_query(
        "SELECT trade_date, ts_code, circ_mv FROM daily_basic WHERE trade_date>=?",
        con, params=(MV_EFFECTIVE_FROM,))
    con.close()
    mv["code"] = mv["ts_code"].str.split(".").str[0]
    mv["circ_mv_yi"] = mv["circ_mv"] / 1e4  # 万→亿
    mv = mv[["trade_date", "code", "circ_mv_yi"]]

    # 读 BASE 信号 → 构建子集
    sdb = sqlite3.connect(f"file:{SIG_DB}?mode=ro", uri=True, timeout=60)
    base_sig = pd.read_sql_query(
        "SELECT param_id, code, signal_date, close, vr, rng20, chg20, hi21, ma20 "
        "FROM signals WHERE param_id='P00'", sdb)
    sdb.close()
    print(f"[info] BASE 信号 {len(base_sig)} 行")

    base_sig["ind1"] = base_sig["code"].map(lambda c: IND1.get(c, ""))
    base_sig["ml_hit"] = [
        bool(ind and d in ml_map and ind in ml_map[d])
        for ind, d in zip(base_sig["ind1"], base_sig["signal_date"])]
    p90 = base_sig[base_sig["ml_hit"]].copy()

    # P91 = P90 ∩ 市值层（创业板放行；主板 2024 起有市值<100亿才过，缺失 fail-open）
    p91 = p90.merge(mv, left_on=["signal_date", "code"],
                    right_on=["trade_date", "code"], how="left")
    is_cyb = p91["code"].str[:3].isin(["300", "301"])
    has_mv = p91["circ_mv_yi"].notna()
    mv_ok = (~has_mv) | (p91["circ_mv_yi"] < FLOAT_MV_CAP)
    p91 = p91[is_cyb | mv_ok].copy()

    # 写库（追加 P90/P91；先清旧，幂等）
    wcon = sqlite3.connect(SIG_DB)
    wcon.execute("DELETE FROM signals WHERE param_id IN ('P90','P91')")
    wcon.execute("DELETE FROM params WHERE param_id IN ('P90','P91')")
    wcon.execute("INSERT INTO params VALUES ('P90','BASE∩主线(ths一级≥5)',0,25.0,15.0,1.5,0.98,3.0,20.0)")
    wcon.execute("INSERT INTO params VALUES ('P91','BASE∩主线∩市值(fail-open@2024前)',0,25.0,15.0,1.5,0.98,3.0,20.0)")
    cols = ["param_id", "code", "signal_date", "close", "vr", "rng20", "chg20", "hi21", "ma20"]
    def _rows(pid: str, frame: pd.DataFrame):
        return [(pid, r.code, r.signal_date, r.close, r.vr, r.rng20, r.chg20, r.hi21, r.ma20)
                for r in frame[cols].itertuples(index=False)]
    wcon.executemany("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?)", _rows("P90", p90))
    wcon.executemany("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?)", _rows("P91", p91))
    wcon.execute("INSERT INTO meta VALUES ('mainline_days', ?)", (str(n_days_with_ml),))
    wcon.commit()
    n90 = wcon.execute("SELECT COUNT(*) FROM signals WHERE param_id='P90'").fetchone()[0]
    n91 = wcon.execute("SELECT COUNT(*) FROM signals WHERE param_id='P91'").fetchone()[0]
    wcon.close()
    print(f"[done] P90 主线子集 {len(p90)}（写库 {n90}）| P91 +市值层 {len(p91)}（写库 {n91}）"
          f"| BASE保留率 {len(p90)/max(len(base_sig),1):.1%} | {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
