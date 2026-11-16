#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bp_ep_pit_shadow.py — PIT层W1影子轨清单生成器（两周灰度·第一周）

用途（FACTOR_PIT_REBUILD报告第五节灰度方案W1）:
  生产消费方选股逻辑原样复刻，但财务读新PIT层（factor_pit.db，
  get_pit_factor语义: 交易日T取pit_avail_date<=T最新一期），两轨并列输出对比。

轨道:
  1) bp_ep_prod  — 复刻 multifactor/bp_ep_monthly_picks.py 生产口径:
     legacy表 stock_factors_fundamental 当日原值(ASOF, 含最新修订), ep/bp
     等权pct-rank合成, 主板过滤(60/00), 价格<=30元, Top20。
  2) bp_ep_pit   — 同一套选股逻辑, 财务读PIT层:
     ep = snap.eps(报告期期后首日快照版本), bp = close_T/snap.bps。
  3) droe_pit    — droe单因子PIT Top50影子(对标combo_b_list_generator.py
     生产B组: 价格<=30/非ST/剔科创北交/剔当前持仓, rank-z打分)。
  4) droe_asof   — 辅助诊断轨: droe按生产finance_daily as-of口径(行shift)
     同universe Top50, 用于隔离"PIT vs ASOF"与"单因子 vs droe+roa icir"效应。

登记: pit_shadow_track.db 表 shadow_lists(date,strategy,code,name,rank,
     factor_value,source), 幂等(同date+strategy先删后插)。

铁律: 主库/因子库/归因库全部 mode=ro 只读; 代码必带stock_info核实名称;
     ST/退市风险股标注; 主库零写入。

用法:
  python bp_ep_pit_shadow.py                     # 最近交易日截面
  python bp_ep_pit_shadow.py --date 20260831     # 指定截面(月末调仓用)
  python bp_ep_pit_shadow.py --top-droe 50 --top-bpep 20
  python bp_ep_pit_shadow.py --validate-only     # 只做复刻校验+断言, 不写库
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from build_factor_pit_20260815 import get_pit_factor  # noqa: E402  PIT铁律入口

# ── 路径（主库只读） ──────────────────────────────────────────
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"      # 只读
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db" # 只读
PIT_DB = r"D:\zcodesj\factor_pit.db"                                   # 只读
ATT_DB = r"D:\zcodesj\attribution\attribution.db"                      # 只读
SHADOW_DB = r"D:\zcodesj\pit_shadow_track.db"                          # 本脚本唯一写库
REPORT_DIR = Path(r"D:\zcodesj\reports")
PROD_BPEP_CSV = Path(r"D:\zcodesj\multifactor\reports\daily_top20\bp_ep_monthly_20260813_20260815.csv")
PROD_B_MD = REPORT_DIR / "COMBO_EXPERIMENT_LIST_20260815.md"

MAX_PRICE = 30.0          # 主人8/13裁决: >30元一律不做
BOARD_ALLOWED = ("60", "00")   # 复刻 stock_filter: 仅沪/深主板
STALE_DAYS = 400          # PIT陈旧度上限(与build_factor_pit/revalidate一致)


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def connect_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=60)
    return conn


# ── 基础数据 ─────────────────────────────────────────────────
def recent_trade_date(main_conn: sqlite3.Connection) -> int:
    d, = main_conn.execute(
        "SELECT MAX(date) FROM stock_daily_hsj").fetchone()
    return int(d)


def load_closes(main_conn: sqlite3.Connection, date: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT code, close FROM stock_daily_hsj WHERE date=?", main_conn,
        params=[date])
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def load_stock_info(main_conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT code, name, is_st, status FROM stock_info", main_conn)
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def load_meta_closes(meta_conn: sqlite3.Connection, date: int) -> pd.Series:
    df = pd.read_sql_query(
        "SELECT code, close FROM stock_daily_meta WHERE date=? AND close>0",
        meta_conn, params=[date])
    s = df.set_index(df["code"].astype(str).str[:6])["close"]
    return s[~s.index.duplicated(keep="last")]


def load_st_codes(main_conn: sqlite3.Connection) -> set:
    rows = main_conn.execute(
        "SELECT code FROM stock_info WHERE is_st=1 OR name LIKE '%ST%'"
    ).fetchall()
    return {str(r[0]).zfill(6) for r in rows}


def load_current_holdings() -> set:
    """复刻 combo_b_list_generator.load_current_holdings（账本净持仓）"""
    conn = sqlite3.connect("file:%s?mode=ro" % ATT_DB, uri=True, timeout=30)
    try:
        rows = conn.execute(
            "SELECT code, action, volume FROM trade_records "
            "WHERE trade_date >= '20260810'").fetchall()
    finally:
        conn.close()
    hold = {}
    for code, action, vol in rows:
        c = str(code)[-6:]
        hold[c] = hold.get(c, 0) + (vol if action == "BUY" else -vol)
    return {c for c, v in hold.items() if v > 0}


def board_filter(codes: pd.Series) -> pd.Series:
    """复刻 stock_filter.filter_by_board: 仅60/00主板"""
    return codes[codes.str[:2].isin(BOARD_ALLOWED)
                 & ~codes.str.startswith(("300", "301", "688", "689",
                                          "8", "920", "43"))]


# ── 轨道1: bp+ep 生产复刻（legacy ASOF当日原值） ─────────────
def bp_ep_prod_track(main_conn, date: int, top_n: int = 20) -> pd.DataFrame:
    raw = pd.read_sql_query(
        "SELECT code, ep, bp, report_date FROM stock_factors_fundamental "
        "WHERE date=?", main_conn, params=[date])
    raw["code"] = raw["code"].astype(str).str.zfill(6)
    wl = set(load_stock_info(main_conn)["code"])
    raw = raw[raw["code"].isin(wl)]
    raw["ep"] = pd.to_numeric(raw["ep"], errors="coerce")
    raw["bp"] = pd.to_numeric(raw["bp"], errors="coerce")
    # 生产口径: 各因子在自己非空全集上独立pct-rank, 再inner合并(复刻load_bp_ep_rank)
    ep = raw.dropna(subset=["ep"])[["code", "ep", "report_date"]].copy()
    bp = raw.dropna(subset=["bp"])[["code", "bp"]].copy()
    ep["rank_ep"] = ep["ep"].rank(pct=True)
    bp["rank_bp"] = bp["bp"].rank(pct=True)
    merged = ep.merge(bp, on="code", how="inner")
    merged["composite_score"] = merged[["rank_ep", "rank_bp"]].mean(axis=1)
    merged = merged.sort_values("composite_score", ascending=False)
    # 复刻stock_filter.apply_filters(对merged仅板块过滤生效) + 价格≤30前置
    merged = merged[merged["code"].isin(set(board_filter(merged["code"])))]
    closes = load_closes(main_conn, date).rename(columns={"close": "close_T"})
    merged = merged.merge(closes, on="code", how="left")
    merged = merged[merged["close_T"].isna() | (merged["close_T"] <= MAX_PRICE)]
    top = merged.sort_values("composite_score", ascending=False).head(top_n)
    top = top.reset_index(drop=True)
    top["rank"] = np.arange(1, len(top) + 1)
    top = top.rename(columns={"ep": "ep_prod", "bp": "bp_prod",
                              "report_date": "report_prod"})
    return top[["rank", "code", "close_T", "composite_score",
                "ep_prod", "bp_prod", "report_prod", "rank_ep", "rank_bp"]]


# ── 轨道2: bp+ep PIT影子（期后首日快照财务+当期价） ───────────
def bp_ep_pit_track(main_conn, pit_conn, date: int,
                    top_n: int = 20) -> pd.DataFrame:
    snap = get_pit_factor(pit_conn, date, max_stale_days=STALE_DAYS)
    snap["code"] = snap["code"].astype(str).str.zfill(6)
    closes = load_closes(main_conn, date).rename(columns={"close": "close_T"})
    df = snap.merge(closes, on="code", how="inner")
    df = df[df["code"].isin(set(board_filter(df["code"])))]
    with np.errstate(divide="ignore", invalid="ignore"):
        df["bp_pit"] = np.where((df["bps"] > 0) & (df["close_T"] > 0),
                                df["bps"] / df["close_T"], np.nan)  # [2026-08-22修复] bp=每股净资产/价
    # 与生产同构: 两因子独立rank后inner合并
    eps = df.dropna(subset=["eps"])[["code", "eps", "report_date",
                                     "pit_avail_date"]].copy()
    bpv = df.dropna(subset=["bp_pit"])[["code", "bp_pit", "close_T"]].copy()
    eps["rank_ep"] = eps["eps"].rank(pct=True)
    bpv["rank_bp"] = bpv["bp_pit"].rank(pct=True)
    merged = eps.merge(bpv, on="code", how="inner")
    merged["composite_score"] = merged[["rank_ep", "rank_bp"]].mean(axis=1)
    merged = merged[merged["close_T"] <= MAX_PRICE]
    top = merged.sort_values("composite_score", ascending=False).head(top_n)
    top = top.reset_index(drop=True)
    top["rank"] = np.arange(1, len(top) + 1)
    return top[["rank", "code", "close_T", "composite_score",
                "eps", "bp_pit", "report_date", "pit_avail_date",
                "rank_ep", "rank_bp"]]


# ── 轨道3: droe PIT影子（combo_b universe规则, 单因子Top50） ──
def droe_pit_track(pit_conn, meta_conn, main_conn, date: int,
                   top_n: int = 50, exclude: set | None = None):
    snap = get_pit_factor(pit_conn, date, max_stale_days=STALE_DAYS)
    snap["code"] = snap["code"].astype(str).str.zfill(6)
    df = snap.dropna(subset=["droe"]).set_index("code")
    # rank-z（与 combo_b cs_rank_z 同式, 单日截面）
    r = df["droe"].rank()
    n = len(df)
    df["score"] = (r - (n + 1) / 2) / (n / np.sqrt(12))
    day_px = load_meta_closes(meta_conn, date)
    st = load_st_codes(main_conn)
    excl = exclude or set()
    valid = [c for c in df.index
             if c not in st and c not in excl
             and not c.startswith(("688", "689", "920", "8", "4", "9"))
             and c in day_px.index and day_px[c] <= MAX_PRICE]
    df = df.loc[valid]
    top = df.nlargest(top_n, "score").reset_index()
    top["rank"] = np.arange(1, len(top) + 1)
    top["price"] = [round(float(day_px[c]), 2) for c in top["code"]]
    return top[["rank", "code", "price", "score", "droe",
                "report_date", "pit_avail_date"]], len(valid)


# ── 轨道4: droe ASOF复刻（finance_daily行shift+期末as-of, 辅助诊断） ──
def droe_asof_track(main_conn, meta_conn, date: int, top_n: int = 50,
                    exclude: set | None = None):
    """复刻 alphalens_factor_diagnosis.load_factor_droe 的取值口径"""
    y = int(date) // 10000
    fin = pd.read_sql_query(
        "SELECT code, report_date, total_assets, total_equity, net_profit "
        "FROM finance_daily WHERE total_assets>0 AND total_equity>0 "
        "AND report_date BETWEEN ? AND ? ORDER BY code, report_date",
        main_conn, params=[(y - 2) * 10000 + 101, date])
    fin["code"] = fin["code"].astype(str).str[:6]
    fin = fin.drop_duplicates(subset=["code", "report_date"], keep="first")
    fin["roe"] = fin["net_profit"] / fin["total_equity"]
    fin["roe_lag4"] = fin.groupby("code")["roe"].shift(4)   # 行shift(生产口径)
    fin = fin.dropna(subset=["roe", "roe_lag4"])
    fin = fin[fin["report_date"] <= date]                    # as-of期末
    latest = fin.sort_values("report_date").groupby("code").tail(1)
    latest = latest.set_index("code")
    r = latest["roe"].rank(); r_l = latest["roe_lag4"].rank()
    latest["droe"] = latest["roe"] - latest["roe_lag4"]
    dr = latest["droe"].rank()
    n = len(latest)
    latest["score"] = (dr - (n + 1) / 2) / (n / np.sqrt(12))
    day_px = load_meta_closes(meta_conn, date)
    st = load_st_codes(main_conn)
    excl = exclude or set()
    valid = [c for c in latest.index
             if c not in st and c not in excl
             and not c.startswith(("688", "689", "920", "8", "4", "9"))
             and c in day_px.index and day_px[c] <= MAX_PRICE]
    latest = latest.loc[valid]
    top = latest.nlargest(top_n, "score").reset_index()
    top["rank"] = np.arange(1, len(top) + 1)
    top["price"] = [round(float(day_px[c]), 2) for c in top["code"]]
    return top[["rank", "code", "price", "score", "droe", "report_date"]], len(valid)


# ── 生产B组清单（锚点, 解自combo_b_list_generator落盘报告） ───
def load_prod_b_list(md_path: Path) -> pd.DataFrame:
    rows = []
    in_b = False
    for line in md_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## B组"):
            in_b = True
            continue
        if in_b and line.startswith("## "):
            break
        m = re.match(r"\|\s*(\d+)\s*\|\s*(\d{6})\s*\|\s*(\S+)\s*\|\s*[\d.]+\s*\|\s*([+-]?[\d.]+)\s*\|", line)
        if m:
            rows.append({"rank": int(m.group(1)), "code": m.group(2),
                         "name": m.group(3), "score": float(m.group(4))})
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("生产B组清单解析失败: %s" % md_path)
    return df


def load_prod_bpep_csv(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"code": str})
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


# ── 影子轨登记（唯一写库） ───────────────────────────────────
SHADOW_DDL = """
CREATE TABLE IF NOT EXISTS shadow_lists (
    date         INTEGER NOT NULL,     -- 截面日yyyymmdd
    strategy     TEXT    NOT NULL,     -- bp_ep_prod/bp_ep_pit/droe_pit/droe_asof/droe_roa_wficir_prod
    code         TEXT    NOT NULL,
    name         TEXT    NOT NULL,     -- stock_info核实名称(铁律)
    rank         INTEGER NOT NULL,
    factor_value REAL,                 -- 合成分或因子值
    source       TEXT    NOT NULL,     -- legacy_asof/factor_pit/finance_daily_asof_replica/combo_b_report
    PRIMARY KEY (date, strategy, code)
);
"""


def register(date: int, strategy: str, df: pd.DataFrame, value_col: str,
             source: str, name_map: dict, conn: sqlite3.Connection) -> int:
    assert not df.empty, "清单为空, 拒绝登记: %s" % strategy
    codes = df["code"].astype(str).str.zfill(6).tolist()
    names = [name_map.get(c) for c in codes]
    missing = [c for c, nm in zip(codes, names) if not nm]
    assert not missing, "名称缺失(stock_info查无), 拒绝登记: %s" % missing
    conn.execute("DELETE FROM shadow_lists WHERE date=? AND strategy=?",
                 (date, strategy))
    conn.executemany(
        "INSERT OR REPLACE INTO shadow_lists "
        "(date,strategy,code,name,rank,factor_value,source) VALUES (?,?,?,?,?,?,?)",
        [(date, strategy, c, nm, int(r),
          None if pd.isna(v) else round(float(v), 6), source)
         for c, nm, r, v in zip(codes, names, df["rank"], df[value_col])])
    conn.commit()
    return len(codes)


# ── 验收断言（FACTOR_PIT_REBUILD 5.2节W2清单预跑） ────────────
def acceptance_assertions(main_conn, pit_conn, date: int,
                          registered: bool) -> list:
    res = []

    # ① 截面覆盖 >=4100只（对齐原表同日）
    for t in (date, 20260801, 20260701):
        snap = get_pit_factor(pit_conn, t, max_stale_days=STALE_DAYS)
        leg = main_conn.execute(
            "SELECT COUNT(DISTINCT code) FROM stock_factors_fundamental "
            "WHERE date=?", (t,)).fetchone()[0]
        ok = len(snap) >= 4100
        res.append(("①截面覆盖>=4100", "T=%d: PIT %d只 vs legacy同日%d只" % (t, len(snap), leg),
                    "PASS" if ok else "FAIL"))

    # ② pit_avail_date边界抽查（次日切换报告期）
    for t_before, t_after, new_rd in (
            (20260429, 20260430, 20260331),   # Q1截止4/30
            (20250830, 20250831, 20250630),   # 中报截止8/31
            (20251030, 20251031, 20250930)):  # Q3截止10/31
        n_b = int((get_pit_factor(pit_conn, t_before, STALE_DAYS)["report_date"] == new_rd).sum())
        n_a = int((get_pit_factor(pit_conn, t_after, STALE_DAYS)["report_date"] == new_rd).sum())
        ok = (n_b == 0) and (n_a > 4000)
        res.append(("②边界切换%d" % new_rd,
                    "T-1=%d: %d只 | T=%d: %d只用%d" % (t_before, n_b, t_after, n_a, new_rd),
                    "PASS" if ok else "FAIL"))

    # ②补 年报边界(与Q1同日截止): 4/29最新=上年Q3, 4/30同时解锁年报+Q1
    n_b = int(get_pit_factor(pit_conn, 20250429, STALE_DAYS)["report_date"]
              .isin([20241231, 20250331]).sum())
    n_a = int(get_pit_factor(pit_conn, 20250430, STALE_DAYS)["report_date"]
              .isin([20241231, 20250331]).sum())
    res.append(("②边界切换年报+Q1同日",
                "T-1=20250429: %d只用2024年报/2025Q1 | T=20250430: %d只" % (n_b, n_a),
                "PASS" if (n_b == 0 and n_a > 4000) else "FAIL"))

    # ③ 无pit_avail_date>T泄漏
    leak_ok = True
    for t in (date, 20260430, 20250831, 20220701):
        snap_t = get_pit_factor(pit_conn, t, STALE_DAYS)
        if len(snap_t) and int(snap_t["pit_avail_date"].max()) > t:
            leak_ok = False
    snap = get_pit_factor(pit_conn, date, STALE_DAYS)
    leak2 = bool((snap["pit_avail_date"] <= date).all())
    res.append(("③无前视泄漏", "多时点max(pit_avail_date)<=T + 截面逐行断言: %s" % leak2,
                "PASS" if (leak_ok and leak2) else "FAIL"))

    # ④ W1影子轨已登记
    res.append(("④W1影子轨登记", "shadow_lists两轨已入库" if registered else "尚未登记",
                "PASS" if registered else "PENDING"))

    # ⑤ droe/roa列覆盖（仲裁终局依赖列）
    nn = pit_conn.execute(
        "SELECT SUM(droe IS NOT NULL), SUM(roa IS NOT NULL), COUNT(*) "
        "FROM stock_factors_fundamental_pit_v2").fetchone()
    res.append(("⑤扩展列覆盖", "droe非空%d/%d, roa非空%d/%d" % (nn[0], nn[2], nn[1], nn[2]),
                "PASS" if nn[0] > 240000 and nn[1] > 240000 else "FAIL"))
    return res


# ── 复刻忠实度校验 ───────────────────────────────────────────
def validate_replica(main_conn, name_map) -> tuple[int, int, list]:
    prod = load_prod_bpep_csv(PROD_BPEP_CSV)
    rep = bp_ep_prod_track(main_conn, 20260813, top_n=20)
    p_codes, r_codes = list(prod["code"]), list(rep["code"])
    overlap = len(set(p_codes) & set(r_codes))
    diff = [c for c in p_codes if c not in r_codes]
    log("复刻校验@20260813: 生产CSV Top%d vs 复刻Top%d 重合%d/%d"
        % (len(p_codes), len(r_codes), overlap, len(p_codes)))
    if diff:
        log("  生产有而复刻无: %s" % [(c, name_map.get(c)) for c in diff])
    return overlap, len(p_codes), diff


# ── 对比工具 ─────────────────────────────────────────────────
def fmt_period(rd) -> str:
    if pd.isna(rd):
        return "?"
    rd = int(rd)
    y, md = rd // 10000, rd % 10000
    q = {331: "Q1", 630: "H1", 930: "Q3", 1231: "FY"}.get(md, "?")
    return "%d%s(%d)" % (y, q, rd)


def flag_st(code: str, name: str, info: pd.DataFrame) -> str:
    row = info.set_index("code").loc[code] if code in set(info["code"]) else None
    if row is None:
        return "无stock_info记录"
    if str(row["is_st"]) == "1" or "ST" in str(name) or "退" in str(name):
        return "ST/退市风险"
    if str(row["status"]) not in ("normal", "1", "1.0"):
        return "status=%s" % row["status"]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="PIT影子轨W1清单生成器")
    ap.add_argument("--date", type=int, default=None, help="截面日yyyymmdd")
    ap.add_argument("--top-bpep", type=int, default=20)
    ap.add_argument("--top-droe", type=int, default=50)
    ap.add_argument("--report", type=str, default=None)
    ap.add_argument("--validate-only", action="store_true")
    a = ap.parse_args()

    t0 = time.time()
    main_conn = connect_ro(MAIN_DB)
    meta_conn = connect_ro(META_DB)
    pit_conn = connect_ro(PIT_DB)

    date = a.date or recent_trade_date(main_conn)
    info = load_stock_info(main_conn)
    name_map = dict(zip(info["code"], info["name"]))
    st_set = load_st_codes(main_conn)
    log("截面日: %d | stock_info %d只 | ST名单%d只" % (date, len(info), len(st_set)))

    # 复刻忠实度校验（生产20260813 CSV）
    val_overlap, val_n, val_diff = validate_replica(main_conn, name_map)

    # ── bp+ep 双轨 ──
    log("轨道1: bp+ep生产复刻(legacy ASOF)...")
    prod = bp_ep_prod_track(main_conn, date, a.top_bpep)
    log("轨道2: bp+ep PIT影子...")
    pit20 = bp_ep_pit_track(main_conn, pit_conn, date, a.top_bpep)

    # ── droe 影子轨 ──
    log("轨道3: droe PIT Top%d..." % a.top_droe)
    holdings = load_current_holdings()
    log("  当前持仓(账本净)>=%s: %d只" % ("20260810", len(holdings)))
    droe_pit, uni_pit = droe_pit_track(pit_conn, meta_conn, main_conn, date,
                                       a.top_droe, exclude=holdings)
    log("轨道4: droe ASOF复刻(辅助诊断)...")
    droe_asof, uni_asof = droe_asof_track(main_conn, meta_conn, date,
                                          a.top_droe, exclude=holdings)
    prod_b = load_prod_b_list(PROD_B_MD)

    # ── 重合度 ──
    ov_bpep = len(set(prod["code"]) & set(pit20["code"]))
    prod_b_codes = set(prod_b["code"])
    pit50_codes = set(droe_pit["code"])
    asof50_codes = set(droe_asof["code"])
    ov_droe_b50 = len(prod_b_codes & pit50_codes)
    ov_droe_b22 = len(prod_b_codes & set(droe_pit.head(len(prod_b))["code"]))
    ov_pit_asof = len(pit50_codes & asof50_codes)
    log("重合度: bp+ep两轨 %d/%d | B组(%d只)∩PIT Top50: %d | B组∩PIT Top%d: %d | PIT∩ASOF Top%d: %d"
        % (ov_bpep, a.top_bpep, len(prod_b), ov_droe_b50, len(prod_b), ov_droe_b22,
           a.top_droe, ov_pit_asof))

    # ── 登记 ──
    registered = False
    if not a.validate_only:
        conn = sqlite3.connect(SHADOW_DB)
        conn.executescript(SHADOW_DDL)
        n1 = register(date, "bp_ep_prod", prod, "composite_score",
                      "legacy_asof(复刻bp_ep_monthly_picks)", name_map, conn)
        n2 = register(date, "bp_ep_pit", pit20, "composite_score",
                      "factor_pit(get_pit_factor)", name_map, conn)
        n3 = register(date, "droe_pit", droe_pit, "droe",
                      "factor_pit(get_pit_factor)", name_map, conn)
        n4 = register(date, "droe_asof", droe_asof, "droe",
                      "finance_daily_asof_replica", name_map, conn)
        # 生产锚点: B组清单(combo_b报告) + 生产bp+ep CSV
        n5 = register(20260814, "droe_roa_wficir_prod", prod_b, "score",
                      "combo_b_list_generator:COMBO_EXPERIMENT_LIST_20260815.md",
                      name_map, conn)
        n6 = register(20260813, "bp_ep_prod_csv", load_prod_bpep_csv(PROD_BPEP_CSV),
                      "composite_score", "bp_ep_monthly_20260813_20260815.csv",
                      name_map, conn)
        total = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT strategy) FROM shadow_lists").fetchone()
        conn.close()
        registered = True
        log("登记: %d+%d+%d+%d+%d+%d行入库, shadow_lists累计%d行/%d策略"
            % (n1, n2, n3, n4, n5, n6, total[0], total[1]))

    # ── 验收断言 ──
    asserts = acceptance_assertions(main_conn, pit_conn, date, registered)
    for name_, detail, status in asserts:
        log("  [%s] %s: %s" % (status, name_, detail))

    # ── 名称与ST标注核验（铁律） ──
    all_lists = [("bp_ep_prod", prod), ("bp_ep_pit", pit20),
                 ("droe_pit", droe_pit), ("droe_asof", droe_asof),
                 ("droe_roa_wficir_prod", prod_b)]
    st_flags = []
    for nm, df in all_lists:
        for _, r in df.iterrows():
            f = flag_st(r["code"], name_map.get(r["code"], ""), info)
            if f:
                st_flags.append((nm, r["code"], name_map.get(r["code"]), f))
    if st_flags:
        log("ST/风险标注 %d条:" % len(st_flags))
        for s in st_flags:
            log("   %s %s %s [%s]" % s)
    else:
        log("全部清单无ST/退市风险股")

    main_conn.close(); meta_conn.close(); pit_conn.close()
    log("完成, 耗时%.0fs" % (time.time() - t0))

    # ── 结果落盘(JSON, 供报告与后续比对) ──
    out = {
        "date": date, "generated": datetime.now().isoformat(timespec="seconds"),
        "validate_replica_20260813": {"overlap": val_overlap, "n": val_n,
                                      "diff": val_diff},
        "bp_ep": {
            "prod": prod.to_dict("records"), "pit": pit20.to_dict("records"),
            "overlap": ov_bpep},
        "droe": {
            "pit_top": droe_pit.to_dict("records"),
            "asof_top": droe_asof.to_dict("records"),
            "prod_b": prod_b.to_dict("records"),
            "overlap_b_vs_pit50": ov_droe_b50,
            "overlap_b_vs_pit22": ov_droe_b22,
            "overlap_pit_vs_asof50": ov_pit_asof,
            "universe_pit": uni_pit, "universe_asof": uni_asof},
        "assertions": asserts,
        "st_flags": st_flags,
    }
    import json
    REPORT_DIR.mkdir(exist_ok=True)
    json_path = REPORT_DIR / ("_pit_shadow_w1_%d.json" % date)
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str),
                         encoding="utf-8")
    log("数值存档: %s" % json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
