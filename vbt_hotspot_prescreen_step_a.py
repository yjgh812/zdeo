#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""hotspot_breakout 参数 OAT 预筛 · Step A：向量化信号生成（系统 python）。

目的：把 tulong_hotspot_breakout_picker 的形态判据（breakout_metrics + is_breakout_box）
在全市场 × 5 年窗口上向量化重放，对 OAT 参数组合（单轴扰动生产现值）生成信号清单，
供 Step B（.venv_vbt 引擎）做 A 股规则化回测。

预筛问的是敏感性结构（哪根轴是悬崖/是否有平台期），不是全局最优（B3：网格计入等效N）。

防漂移对账（A1）：随机抽股票×日期，向量化因子 vs picker 原函数 breakout_metrics
（喂截至该日的40根K，绕开 picker._read_k40 无日期上界的已知缺陷），数值与判据
布尔必须一致。不一致 → exit 2，禁止产出信号。

范围对齐 picker（不含：主线过滤/业绩预筛/市值过滤——正交层，见报告；含：board_ok
+ name_ok 剔除铁律、价格≤20、当日上涨、未涨停、22根历史门槛）。

运行（系统 python，工作目录 D:\zcodesj）：
  python vbt_hotspot_prescreen_step_a.py [--verify-only]
输出：_cache/vbt_hotspot_prescreen_signals.db（重建式幂等，先删后建）
"""
from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT_DB = Path(r"D:\zcodesj\_cache\vbt_hotspot_prescreen_signals.db")

# 窗口：信号期 20210828~20260827（终点=8/27收盘完整，排除8/28盘中快照风险）；load 前移暖机
WARM_START = "20210501"
SIG_START = "20210828"
SIG_END = "20260827"

# 生产现值（tulong_hotspot_breakout_picker 8/28 版）
BASE_P = dict(rng20_cap=25.0, chg20_cap=15.0, vr_min=1.5, disc=0.98, pct_min=3.0, price_cap=20.0)

# OAT 网格（单轴扰动，其余=现值；跨轴重复组合去重后 17 组）
OAT_AXES = {
    "rng20_cap": [15.0, 20.0, 25.0, 30.0, 35.0],
    "vr_min": [1.2, 1.5, 2.0, 2.5],
    "disc": [0.95, 0.98, 1.00, 1.02],
    "pct_min": [2.0, 3.0, 5.0],
    "chg20_cap": [10.0, 15.0, 20.0],
}

sys.path.insert(0, BASE)
from tulong_buy_whitelist import board_ok, name_ok, norm6  # noqa: E402


def build_grid() -> list[dict]:
    """OAT 组合：BASE + 每轴取值（其余保持现值），参数向量去重。"""
    seen: set[tuple] = set()
    out: list[dict] = []
    cand = [("BASE", None)] + [(ax, v) for ax, vs in OAT_AXES.items() for v in vs]
    for axis, val in cand:
        p = dict(BASE_P)
        if axis != "BASE":
            p[axis] = val
        key = tuple(sorted(p.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append({"param_id": f"P{len(out):02d}", "axis": f"{axis}={val}" if axis != "BASE" else "BASE",
                    "is_base": axis == "BASE", **p})
    return out


def load_universe() -> set[str]:
    """屠龙宇宙：board_ok 剔板块 + name_ok 剔 ST（名称缺失剔除——预筛从严，报告注明）。"""
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    rows = con.execute("SELECT code, name FROM industry_map_ths").fetchall()
    con.close()
    uni = set()
    for code, name in rows:
        c6 = norm6(str(code))
        if board_ok(c6) and name and name_ok(str(name).strip()):
            uni.add(c6)
    return uni


def load_wide() -> pd.DataFrame:
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    df = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? ORDER BY code,date",
        con, params=(WARM_START, SIG_END))
    con.close()
    df["code"] = df["code"].map(lambda c: norm6(str(c)))
    return df


def compute_factors(g: pd.DataFrame) -> pd.DataFrame:
    """向量化因子（口径逐条对齐 breakout_metrics：横盘窗口=前21根不含当日，ma20 含当日）。"""
    hi_prev21 = g["high"].shift(1).rolling(21).max()
    lo_prev21 = g["low"].shift(1).rolling(21).min()
    f = pd.DataFrame(index=g.index)
    f["hi21"] = hi_prev21
    f["rng20"] = (hi_prev21 - lo_prev21) / lo_prev21 * 100.0
    f["chg20"] = (g["close"].shift(1) / g["close"].shift(21) - 1.0) * 100.0
    f["vr"] = g["volume"] / g["volume"].shift(1).rolling(21).mean()
    f["ma20"] = g["close"].rolling(20).mean()
    f["prev_close"] = g["close"].shift(1)
    # 对齐 picker 生产口径：breakout_metrics 返回值带 round，is_breakout_box 用 round 值判定
    f["rng20"] = f["rng20"].round(1)
    f["chg20"] = f["chg20"].round(1)
    f["vr"] = f["vr"].round(2)
    f["hi21"] = f["hi21"].round(2)
    f["ma20"] = f["ma20"].round(3)
    return f


def shape_ok(f_row: pd.Series, close: float, pct_today: float, p: dict) -> bool:
    """形态判据（对齐 is_breakout_box，参数可调）。"""
    return bool(
        f_row["rng20"] <= p["rng20_cap"] and f_row["chg20"] <= p["chg20_cap"]
        and f_row["vr"] >= p["vr_min"]
        and close >= f_row["hi21"] * p["disc"]
        and pct_today >= p["pct_min"]
        and close > f_row["ma20"]
    )


def verify_against_picker(df: pd.DataFrame, f_parts: dict[str, pd.DataFrame],
                          uni: set[str], n_sample: int = 30) -> list[str]:
    """A1 判据对账：抽样 (code,date)，向量化因子 vs picker.breakout_metrics/is_breakout_box 原函数。"""
    from tulong_hotspot_breakout_picker import breakout_metrics, is_breakout_box
    rng = random.Random(20260828)
    cand_codes = [c for c in f_parts if c in uni]
    if not cand_codes:
        return ["因子池为空，无法对账"]
    fails: list[str] = []
    checked = tried = 0
    while checked < n_sample and tried < n_sample * 60:
        tried += 1
        code = rng.choice(cand_codes)
        f = f_parts[code]
        g = df[df["code"] == code].set_index("date")
        rows = f[(f.index >= SIG_START) & f["prev_close"].notna() & f["vr"].notna() & f["ma20"].notna()]
        if not len(rows):
            continue
        d = rng.choice(rows.index)
        # picker 原函数：喂截至 d 的最近 40 根（对齐 _read_k40 切片语义，但有日期上界）
        k40 = g.loc[:d].tail(40)[["open", "close", "high", "low", "volume"]].values.tolist()
        k40 = [[i + 1, *row] for i, row in enumerate(k40)]  # date 槽位占位（breakout_metrics 不读 date）
        m_pick = breakout_metrics(k40)
        fr = f.loc[d]
        price = float(g.at[d, "close"])
        pct_today = (price / float(fr["prev_close"]) - 1.0) * 100.0
        if m_pick is None:
            fails.append(f"{code}@{d}: picker None(len={len(k40)}) 但向量化有值")
            checked += 1
            continue
        for k_vec, k_pick, tol in (("rng20", "rng20", 0.05), ("chg20", "chg20", 0.05),
                                   ("vr", "vr", 0.005), ("hi21", "hi20", 0.005),
                                   # ma20 容差覆盖 round 边界翻转：picker=Python sum/20，
                                   # 向量化=numpy rolling mean，第15位微差可致 round 后差 0.001
                                   ("ma20", "ma20", 0.0015)):
            a, b = float(fr[k_vec]), float(m_pick[k_pick])
            if abs(a - b) > tol + 1e-9:
                fails.append(f"{code}@{d}: {k_vec} vec={a:.6f} picker={b:.6f}")
        b_vec = shape_ok(fr, price, pct_today, BASE_P)
        b_pick = is_breakout_box(m_pick, pct_today, price)
        if b_vec != b_pick:
            fails.append(f"{code}@{d}: 判据 vec={b_vec} picker={b_pick}")
        checked += 1
    if checked < n_sample:
        fails.append(f"抽样不足: {checked}/{n_sample}")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-only", action="store_true", help="只跑 A1 判据对账不写库")
    a = ap.parse_args()

    t0 = time.time()
    uni = load_universe()
    print(f"[info] 宇宙: board_ok+name_ok 后 {len(uni)} 只")

    df = load_wide()
    print(f"[info] qfq 长表: {len(df)} 行 ({WARM_START}~{SIG_END})，{df['code'].nunique()} 只，{time.time()-t0:.0f}s")

    # 逐股因子（一次计算，所有参数组合共用）
    f_parts: dict[str, pd.DataFrame] = {}
    for code, g in df.groupby("code", sort=True):
        if code not in uni:
            continue
        f_parts[code] = compute_factors(g.set_index("date"))
    print(f"[info] 因子计算完成: {len(f_parts)} 只，{time.time()-t0:.0f}s")

    # A1 判据对账（先于任何信号产出；失败 exit 2）
    fails = verify_against_picker(df, f_parts, uni)
    if fails:
        print(f"[FAIL] A1 判据对账 {len(fails)} 处不一致（前10条）：")
        for x in fails[:10]:
            print("  ", x)
        return 2
    print("[PASS] A1 判据对账：30 抽样数值+判据布尔全一致")

    if a.verify_only:
        return 0

    # 粗筛+形态：全量向量化（对齐 scan_qfq_candidates：价格帽现值+当日上涨+未涨停）
    grid = build_grid()
    f_all = pd.concat([f.assign(code=c) for c, f in f_parts.items()])
    f_all = f_all.reset_index().rename(columns={"index": "date"}).set_index(["code", "date"])
    close_s = df.set_index(["code", "date"])["close"].reindex(f_all.index)
    lim = pd.Series([20.0 if c[:3] in ("300", "301") else 10.0
                     for c in f_all.index.get_level_values("code")], index=f_all.index)
    limit_price = (f_all["prev_close"] * (1.0 + lim / 100.0)).round(2)
    pct_today = (close_s / f_all["prev_close"] - 1.0) * 100.0
    coarse = (
        (f_all.index.get_level_values("date") >= SIG_START)
        & f_all["prev_close"].notna() & f_all["vr"].notna() & f_all["ma20"].notna()
        & (close_s > 0) & (close_s <= BASE_P["price_cap"])
        & (pct_today > 0) & (close_s < limit_price)
    )
    n_coarse = int(coarse.sum())
    fc = f_all[coarse]
    fc_close, fc_pct = close_s[coarse], pct_today[coarse]
    print(f"[info] 信号期粗筛总行: {n_coarse}，开始 {len(grid)} 参数组合向量化扫描")

    sig_rows: list[tuple] = []
    counts = {}
    for p in grid:
        sm = (
            (fc["rng20"] <= p["rng20_cap"]) & (fc["chg20"] <= p["chg20_cap"])
            & (fc["vr"] >= p["vr_min"])
            & (fc_close >= fc["hi21"] * p["disc"])
            & (fc_pct >= p["pct_min"])
            & (fc_close > fc["ma20"])
        )
        hits = fc[sm]
        counts[p["param_id"]] = len(hits)
        h_close = fc_close[sm]
        sig_rows.extend(
            (p["param_id"], c, str(d), float(h_close.loc[(c, d)]),
             float(fc.at[(c, d), "vr"]), float(fc.at[(c, d), "rng20"]),
             float(fc.at[(c, d), "chg20"]), float(fc.at[(c, d), "hi21"]),
             float(fc.at[(c, d), "ma20"]))
            for (c, d) in hits.index)
    param_rows = [(p["param_id"], p["axis"], int(p["is_base"]), p["rng20_cap"],
                   p["chg20_cap"], p["vr_min"], p["disc"], p["pct_min"], p["price_cap"])
                  for p in grid]

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    if OUT_DB.exists():
        OUT_DB.unlink()
    con = sqlite3.connect(OUT_DB)
    con.execute("CREATE TABLE params (param_id TEXT, axis TEXT, is_base INT, "
                "rng20_cap REAL, chg20_cap REAL, vr_min REAL, disc REAL, pct_min REAL, price_cap REAL)")
    con.execute("CREATE TABLE signals (param_id TEXT, code TEXT, signal_date TEXT, "
                "close REAL, vr REAL, rng20 REAL, chg20 REAL, hi21 REAL, ma20 REAL)")
    con.execute("CREATE TABLE meta (k TEXT, v TEXT)")
    con.executemany("INSERT INTO params VALUES (?,?,?,?,?,?,?,?,?)", param_rows)
    con.executemany("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?)", sig_rows)
    con.execute("INSERT INTO meta VALUES ('window', ?)", (f"{SIG_START}~{SIG_END}",))
    con.execute("INSERT INTO meta VALUES ('n_params', ?)", (str(len(grid)),))
    con.execute("INSERT INTO meta VALUES ('n_coarse_rows', ?)", (str(n_coarse),))
    con.execute("INSERT INTO meta VALUES ('equivalent_N', ?)",
                (f"OAT {len(grid)}组合×3持有期={len(grid)*3}次评估（B3红线记录）",))
    con.commit()
    n_stocks = con.execute("SELECT COUNT(DISTINCT code) FROM signals").fetchone()[0]
    con.close()
    for p in grid:
        print(f"  {p['param_id']} [{p['axis']}] 信号 {counts.get(p['param_id'], 0)}")
    print(f"[done] {OUT_DB} | 组合{len(grid)}（BASE={counts.get('P00', 0)}）总信号{len(sig_rows)} "
          f"涉及{n_stocks}只 | {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
