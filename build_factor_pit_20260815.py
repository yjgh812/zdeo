#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_factor_pit_20260815.py — 因子库披露日PIT重建层（根因修复工程）

背景（2026-08-15 审计定案）:
  主库 stock_factors_fundamental(1580万行) 的 report_date 是"报告期末as-of":
  生成器 tulong_daily_update.py Phase4 用 bisect_right(rds, d)-1 —— 交易日一过
  报告期末即使用该期数据（铁证: report_date MMDD 100% = 0331/0630/0930/1231;
  2022-07-01 全部4143只已用20220630中报）。财务因子最多提前约4个月看到未来
  （BPEP审计: 前视贡献+20.65pp; DROE终审: 旧IC 0.068七成是前视）。

本脚本建立 PIT 重建层（不改主库，主库只读）:
  新库 D:/zcodesj/factor_pit.db, 新表 stock_factors_fundamental_pit
  - 保留原表全部因子列(ep/bp/sp/roe/lev/ato/pm) + code + report_date(原报告期末)
  - 新增 pit_avail_date = 法定披露截止日(该报告期财务数据最早可合法使用日):
      Q1(0331)→当年04-30 | 中报(0630)→当年08-31 | Q3(0930)→当年10-31
      年报(1231)→次年04-30
      (20260815生成端修复后, 主表已自带 pit_avail_date 列, 本脚本优先直读,
       主表无该列/个别缺失时回退本地映射, 幂等)
    (fina_indicator 实测: Q1披露滞后p95=30d、年报p95=119d, 截止日规则对≥95%
     个股晚于实际公告日 → 保守无前视; 参考 factor_gpoa_study.py 标定)
  - 每股财务量恢复(价格相关, 供任意交易日×任意价格口径重建因子):
      eps = ep列本身(生成器定义 ep=ROE/100×BPS=EPS, 与价无关)
      bps = bp × ref_close  (存储 bp=bps/close=1/PB 账面市值比 ⇒ bps=bp×close)
      sps = sp × ref_close  (存储 sp=每股收入/close=1/PS ⇒ sps=sp×close)
      [2026-08-22 修复: 原 bps=ref_close/bp 方向反了致失真~50倍(600519 10321 vs 真实~195);
       下游 v3/quantbyqlib 原按 close/失真bps 往返抵消使 bp 因子值近似正确, 已同步改为 bps/close]
    ref_* 取该报告期在原表的**期后首个交易日**快照(最保守PIT: 最早可得版本,
      避免使用财务库后续修订; 与 BPEP 审计 strictPIT 口径一致)
  - 扩展列 droe/roa(来自 finance_daily 季度网格, 同一法定截止映射):
      roe_q = 净利润(累计)/期末净资产;  droe = roe_q − 4季前同季(完整季度网格)
      roa  = 净利润(累计)/期末总资产
  - 幂等增量: 按 report_date 分区处理, 续跑时对 report_date >= 已有MAX(report_date)
    的分区先删后插; 全程进度日志。

消费语义（铁律）:
  某交易日 T 可用的是 pit_avail_date <= T 的**最新一期**报告:
      get_pit_factor(conn, T)  —— 见下方函数与 --selftest

用法:
  python build_factor_pit_20260815.py              # 增量/全量构建(v1表)
  python build_factor_pit_20260815.py --rebuild    # 删表全量重建(v1表)
  python build_factor_pit_20260815.py --selftest   # 构建后自测消费模板(v1表)

════════════════════════════════════════════════════════════════════════
二期(20260815): 快照口径V2 — factor_pit_v2 表 (PIT_SNAPSHOT_V2_20260815.md)
════════════════════════════════════════════════════════════════════════
v1缺陷(W1影子轨发现): 快照取"报告期后首个交易日", 此时源库还是披露前占位值
  → PIT层锁进占位值(2026Q1中位偏离15.8倍), bp+ep影子Top20为伪影。
v2规则(本任务):
  1) 快照日 = pit_avail_date(法定披露截止日)之后、该股在源库有行的首个日期;
     个股该日无行(停牌等) → 向后在 [快照日, 快照日+5个分区交易日] 窗口内取最近一日;
     窗口内仍无行(含年报分区整体早于pit_avail结束的情形) → 回退该股分区最后一行;
  2) 落地守卫(landing guard, 截面级): 源库新期真值由批刷新落库, 个别报告期会
     晚于法定截止(2026Q1因5/25~7/1源库断档, 真值7/2才落库, 5/6快照仍是占位值,
     中位偏离17.8倍)。守卫仅捕获**大幅占位污染**(与W1报告⑤.2⑥"偏离>50%告警"
     口径一致): 若快照日截面 ep 与该分区终值截面 中位数比偏离>25% 或 ±25%内
     占比<80%, 则向后找到首个"已落地"日期作为截面快照日。轻污染(<25%, 如2024Q1
     的~14%)不触发, 保持任务口径原样。
  3) 消费语义不变: 交易日T可用 pit_avail_date<=T 的最新一期(法定截止对齐);
     值=对应期"披露截止后首个(落地守卫后)交易日"快照。
  4) 逐行带 snap_rule 溯源: first(截面快照日当日)/fwd5(+5日内回退)/tail(分区
     末行回退, 年报期/长停牌), build_meta 记录各期落地守卫决策日志。

用法:
  python build_factor_pit_20260815.py --v2               # 增量/全量构建 v2 表
  python build_factor_pit_20260815.py --v2 --rebuild     # 删v2表全量重建
  python build_factor_pit_20260815.py --v2 --no-guard    # 关闭落地守卫(纯任务字面口径, A/B用)
  python build_factor_pit_20260815.py --selftest-v2      # v2表自测(含2026Q1占位值回归断言)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"   # 只读
PIT_DB = r"D:\zcodesj\factor_pit.db"

TBL_PIT = "stock_factors_fundamental_pit_v2"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TBL_PIT} (
    code           TEXT    NOT NULL,
    report_date    INTEGER NOT NULL,          -- 原报告期末 YYYYMMDD(0331/0630/0930/1231)
    pit_avail_date INTEGER NOT NULL,          -- 法定披露截止日 = 最早可合法使用日
    -- ── 原表全部因子列(报告期快照, ref_date日口径) ──
    ep    REAL,                               -- =EPS(ROE/100×BPS), 期后首日版本
    bp    REAL,                               -- =bps/ref_close(期后首日P/B倒数, 1/PB)
    sp    REAL,                               -- =每股收入/ref_close(1/PS)
    roe   REAL, lev REAL, ato REAL, pm REAL,
    -- ── 每股财务量(价格相关) ──
    eps   REAL,                               -- =ep
    bps   REAL,                               -- =bp×ref_close(每股净资产) [2026-08-22修复]
    sps   REAL,                               -- =sp×ref_close(每股收入)   [2026-08-22修复]
    -- ── 扩展因子(finance_daily, 同法定截止映射) ──
    droe  REAL,                               -- roe_q − 4季前同季roe_q
    roa   REAL,                               -- 净利(累计)/总资产
    -- ── 元数据 ──
    ref_date  INTEGER,                        -- 该报告期在原表首个交易日(as-of语义)
    ref_close REAL,                           -- ref_date日原始收盘价
    n_days    INTEGER,                        -- 该(code,report_date)在原表交易日行数
    PRIMARY KEY (code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_pit_avail ON {TBL_PIT}(pit_avail_date);
CREATE INDEX IF NOT EXISTS idx_pit_code  ON {TBL_PIT}(code, pit_avail_date);
"""

META_SQL = f"""
CREATE TABLE IF NOT EXISTS build_meta (
    key TEXT PRIMARY KEY, value TEXT
);
"""

# ════════════════════════════════════════════════════════════════════════
# 二期 V2: 快照口径 = pit_avail_date 后首个交易日 (+5日回退 + 落地守卫)
# ════════════════════════════════════════════════════════════════════════
TBL_PIT_V2 = "stock_factors_fundamental_pit_v2"

CREATE_SQL_V2 = f"""
CREATE TABLE IF NOT EXISTS {TBL_PIT_V2} (
    code           TEXT    NOT NULL,
    report_date    INTEGER NOT NULL,          -- 原报告期末 YYYYMMDD(0331/0630/0930/1231)
    pit_avail_date INTEGER NOT NULL,          -- 法定披露截止日 = 最早可合法使用日
    -- ── 因子列(pit_avail_date后首个交易日快照, 落地守卫后) ──
    ep    REAL,
    bp    REAL,
    sp    REAL,
    roe   REAL, lev REAL, ato REAL, pm REAL,
    -- ── 每股财务量(价格相关) ──
    eps   REAL,                               -- =ep
    bps   REAL,                               -- =bp×ref_close(每股净资产) [2026-08-22修复]
    sps   REAL,                               -- =sp×ref_close(每股收入)   [2026-08-22修复]
    -- ── 扩展因子(finance_daily, 同法定截止映射, 与v1相同不受快照口径影响) ──
    droe  REAL,
    roa   REAL,
    -- ── 元数据 ──
    ref_date  INTEGER,                        -- v2快照日(pit_avail后首个有行日/守卫后)
    ref_close REAL,                           -- ref_date日原始收盘价
    n_days    INTEGER,                        -- 该(code,report_date)在源表交易日行数
    snap_rule TEXT NOT NULL,                  -- first/fwd5/tail 溯源(见docstring)
    PRIMARY KEY (code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_pit2_avail ON {TBL_PIT_V2}(pit_avail_date);
CREATE INDEX IF NOT EXISTS idx_pit2_code  ON {TBL_PIT_V2}(code, pit_avail_date);
CREATE INDEX IF NOT EXISTS idx_pit2_rd    ON {TBL_PIT_V2}(report_date);
"""

# 落地守卫参数(仅捕获大幅占位污染; 与W1报告"偏离>50%告警"同向, 阈值收紧到25%)
GUARD_MIN_PAIRS = 200        # 截面配对数低于此值不评估守卫(早期年份覆盖小)
GUARD_MED_LO, GUARD_MED_HI = 0.80, 1.25    # 中位数比(ref/v_snap)可接受区间
GUARD_BAND, GUARD_BAND_MIN = 0.25, 0.80    # |ratio-1|<=25% 的个股占比下限
FWD_WINDOW = 5               # 个股停牌回退窗口(分区交易日)


def load_partition(main_conn: sqlite3.Connection, rd: int) -> pd.DataFrame:
    """整分区载入源表(全部行, 供v2多时点选快照)"""
    df = pd.read_sql_query(
        """SELECT date AS d, code, close, ep, bp, sp, roe, lev, ato, pm,
                  pit_avail_date
           FROM stock_factors_fundamental WHERE report_date = ?""", main_conn,
        params=[rd])
    return df


def detect_landing(df: pd.DataFrame, pa: int, use_guard: bool) -> tuple:
    """落地守卫: 返回 (截面快照日D0, 守卫决策dict)。

    D0 = 落地守卫生效时首个"已落地"日期, 否则 pit_avail 后分区首个日期;
    分区在 pit_avail 后无任何日期(年报分区) → D0=None(全部走tail回退)。
    """
    dates = sorted(int(d) for d in df["d"].unique())
    post = [d for d in dates if d > pa]
    dec = {"pit_avail": pa, "n_dates": len(dates),
           "post_first": post[0] if post else None, "guard": "off"}
    if not post:
        dec["d0"] = None
        return None, dec
    d0 = post[0]
    if not use_guard:
        dec.update({"d0": d0})
        return d0, dec
    # 参考终值: 分区最后5个交易日的每股ep中位数(成熟值)
    ref_dates = set(dates[-5:])
    ref = (df[df["d"].isin(ref_dates)].dropna(subset=["ep"])
           .groupby("code")["ep"].median())
    ref = ref[ref > 0]
    for d in post:
        v = df.loc[df["d"] == d].dropna(subset=["ep"]).set_index("code")["ep"]
        m = pd.concat([v, ref], axis=1, keys=["v", "r"]).dropna()
        m = m[m["v"] > 0]
        if len(m) < GUARD_MIN_PAIRS:
            continue
        ratio = m["r"] / m["v"]
        med = float(ratio.median())
        band = float((ratio.sub(1).abs() <= GUARD_BAND).mean())
        if GUARD_MED_LO <= med <= GUARD_MED_HI and band >= GUARD_BAND_MIN:
            dec.update({"d0": int(d), "guard": "pass@first" if d == post[0] else
                        "advanced", "med_ratio": round(med, 4),
                        "band_share": round(band, 4), "n_pairs": int(len(m))})
            return int(d), dec
    # 无日期通过(极端) → 保持字面口径首个日期, 记录告警
    dec.update({"d0": int(d0), "guard": "no_landing_found(kept_literal)"})
    return int(d0), dec


def extract_partition_v2(main_conn: sqlite3.Connection, rd: int,
                         src_pa: int | None, use_guard: bool = True
                         ) -> tuple[pd.DataFrame, dict]:
    """v2分区提取: 每股取 pit_avail 后首个(守卫后截面日±5日内)有行快照"""
    df = load_partition(main_conn, rd)
    if df.empty:
        return df, {}
    computed = pit_avail_date(rd)
    if src_pa is not None and int(src_pa) != computed:
        log("警告: 报告期%d 生成端pit=%s 与法定映射%d不一致(回退映射)" % (rd, src_pa, computed))
    pa = computed
    df["code"] = df["code"].astype(str)

    d0, dec = detect_landing(df, pa, use_guard)
    dec["rd"] = rd

    if d0 is None:
        # 年报型分区: pit_avail 后无任何行 → 每股取分区最后一行(tail)
        snap = df.sort_values("d").groupby("code", as_index=False).tail(1).copy()
        snap["snap_rule"] = "tail"
    else:
        dates = sorted(int(d) for d in df["d"].unique())
        i0 = dates.index(d0)
        window = dates[i0:i0 + 1 + FWD_WINDOW]     # D0 + 后5个分区交易日
        win = df[df["d"].isin(window)].sort_values("d")
        first = win.groupby("code", as_index=False).head(1).copy()
        first["snap_rule"] = np.where(first["d"] == d0, "first", "fwd5")
        have = set(first["code"])
        tail = (df[~df["code"].isin(have)].sort_values("d")
                .groupby("code", as_index=False).tail(1).copy())
        tail["snap_rule"] = "tail"
        snap = pd.concat([first, tail], ignore_index=True)

    snap = snap.rename(columns={"d": "ref_date", "close": "ref_close"})
    with np.errstate(divide="ignore", invalid="ignore"):
        snap["eps"] = snap["ep"]
        # 2026-08-22 修复: 存储 bp=bps/close(账面市值比 1/PB)、sp=每股收入/价格(1/PS),
        # 故每股量恢复应为 bps=bp×ref_close、sps=sp×ref_close(此前 ref_close/bp 方向反了, 失真~50倍)
        snap["bps"] = np.where((snap["bp"] > 0) & (snap["ref_close"] > 0),
                               snap["bp"] * snap["ref_close"], np.nan)
        snap["sps"] = np.where((snap["sp"] > 0) & (snap["ref_close"] > 0),
                               snap["sp"] * snap["ref_close"], np.nan)
    snap["report_date"] = rd
    snap["pit_avail_date"] = pa
    ndays = df.groupby("code")["d"].count()
    snap["n_days"] = snap["code"].map(ndays).fillna(0).astype("int64")
    return snap, dec


def log(msg: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ────────────────────────────────────────────────────────────
# 法定披露截止日映射（保守无前视；与 factor_gpoa_study.py / BPEP审计一致）
# ────────────────────────────────────────────────────────────
def pit_avail_date(report_date: int) -> int:
    y, md = report_date // 10000, report_date % 10000
    if md == 331:
        return y * 10000 + 430
    if md == 630:
        return y * 10000 + 831
    if md == 930:
        return y * 10000 + 1031
    if md == 1231:
        return (y + 1) * 10000 + 430
    # 非标准报告期末(理论上不存在, 见摸底): 按所属季度保守取季报截止
    m = (md // 100 - 1) // 3 * 3 + 3          # 季末月
    qend = y * 10000 + m * 100 + 30
    return pit_avail_date(qend) if qend != report_date else y * 10000 + 430


# ────────────────────────────────────────────────────────────
# 主库分区提取：每(code, report_date)取期后首个交易日快照
# 20260815起生成端(tulong_daily_update.py Phase4)已在主表写入 pit_avail_date,
# 优先直读生成端值(省一次映射, 且以生成端为准); 缺失/无该列时回退本脚本映射(幂等)。
# ────────────────────────────────────────────────────────────
def _extract_sql(src_has_pit: bool) -> str:
    sel_pit = ", pit_avail_date" if src_has_pit else ""
    return f"""
SELECT code, date AS ref_date, close AS ref_close,
       ep, bp, sp, roe, lev, ato, pm,
       n_days{sel_pit}
FROM (
    SELECT code, date, close, ep, bp, sp, roe, lev, ato, pm,
           COUNT(*) OVER (PARTITION BY code) AS n_days,
           ROW_NUMBER() OVER (PARTITION BY code ORDER BY date) AS rn
           {sel_pit}
    FROM stock_factors_fundamental
    WHERE report_date = ?
) t
WHERE rn = 1
"""


def extract_partition(main_conn: sqlite3.Connection, rd: int,
                      src_has_pit: bool = False) -> pd.DataFrame:
    df = pd.read_sql_query(_extract_sql(src_has_pit), main_conn, params=[rd])
    if df.empty:
        return df
    computed = pit_avail_date(rd)
    if src_has_pit and "pit_avail_date" in df.columns:
        src = df.pop("pit_avail_date")
        n_bad = int((src.notna() & (src != computed)).sum())
        if n_bad:
            log("警告: 报告期%d 生成端pit与法定映射不一致%d行(回退映射)" % (rd, n_bad))
            df["pit_avail_date"] = computed
        elif src.notna().all():
            df["pit_avail_date"] = src.astype("int64")
        else:                       # 生成端个别缺失(理论不发生)→兜底
            df["pit_avail_date"] = src.fillna(computed).astype("int64")
    else:
        df["pit_avail_date"] = computed
    # 每股财务量恢复(价格相关: bp/sp=1/PB、1/PS, 故 bps=bp×ref_close、sps=sp×ref_close)
    # 2026-08-22 修复: 此前 ref_close/bp 方向反了导致 bps 失真~50倍(600519 10321 vs 真实~195)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["eps"] = df["ep"]
        df["bps"] = np.where((df["bp"] > 0) & (df["ref_close"] > 0),
                             df["bp"] * df["ref_close"], np.nan)
        df["sps"] = np.where((df["sp"] > 0) & (df["ref_close"] > 0),
                             df["sp"] * df["ref_close"], np.nan)
    df["report_date"] = rd
    return df


# ────────────────────────────────────────────────────────────
# droe / roa（finance_daily 完整季度网格, shift(4)严格同季差分）
# ────────────────────────────────────────────────────────────
def compute_droe_roa(main_conn: sqlite3.Connection) -> pd.DataFrame:
    fin = pd.read_sql_query(
        """SELECT code, report_date, net_profit, total_equity, total_assets
           FROM finance_daily""", main_conn)
    fin["code"] = fin["code"].astype(str).str.zfill(6)
    fin = fin.drop_duplicates(subset=["code", "report_date"], keep="last")
    for c in ("net_profit", "total_equity", "total_assets"):
        fin[c] = pd.to_numeric(fin[c], errors="coerce")
    fin["rd"] = pd.to_datetime(fin["report_date"].astype(str), format="%Y%m%d")
    fin["q"] = fin["rd"].dt.to_period("Q")

    def piv(col):
        return fin.pivot_table(index="code", columns="q", values=col, aggfunc="last")

    P_np, P_te, P_ta = piv("net_profit"), piv("total_equity"), piv("total_assets")
    full_q = pd.period_range(fin["q"].min(), fin["q"].max(), freq="Q").rename("q")
    P_np = P_np.reindex(columns=full_q)
    P_te = P_te.reindex(columns=full_q)
    P_ta = P_ta.reindex(columns=full_q)

    roe_q = P_np / P_te.where(P_te != 0)
    droe = roe_q - roe_q.shift(4, axis=1)          # 同季同比差分(完整网格, 缺期不借位)
    roa = P_np / P_ta.where(P_ta != 0)

    out = pd.concat(
        {"droe": droe.stack(future_stack=True), "roa": roa.stack(future_stack=True)},
        axis=1).dropna(how="all").reset_index()
    ts = out["q"].dt.to_timestamp(how="end").dt.normalize()
    out["report_date"] = ts.dt.strftime("%Y%m%d").astype(int)
    # 报告期实际为季末(摸底: finance_daily MMDD 100%标准季末)
    rd_actual = fin.set_index(["code", "q"])["report_date"]
    out["report_date"] = [rd_actual.get((c, q), r)
                          for c, q, r in zip(out["code"], out["q"], out["report_date"])]
    log("droe/roa 网格: %d 行 (droe非空 %d, roa非空 %d)"
        % (len(out), out["droe"].notna().sum(), out["roa"].notna().sum()))
    return out[["code", "report_date", "droe", "roa"]]


# ────────────────────────────────────────────────────────────
# 构建（幂等增量）
# ────────────────────────────────────────────────────────────
def build(rebuild: bool = False) -> None:
    t0 = time.time()
    main_conn = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True, timeout=120)
    pit_conn = sqlite3.connect(PIT_DB)
    pit_conn.executescript(CREATE_SQL)
    pit_conn.executescript(META_SQL)

    if rebuild:
        pit_conn.execute("DROP TABLE IF EXISTS %s" % TBL_PIT)
        pit_conn.executescript(CREATE_SQL)
        log("已删表, 全量重建")

    periods = [r[0] for r in main_conn.execute(
        "SELECT DISTINCT report_date FROM stock_factors_fundamental "
        "WHERE report_date IS NOT NULL ORDER BY report_date")]
    log("主库报告期分区数: %d (%d ~ %d)" % (len(periods), periods[0], periods[-1]))

    # 20260815生成端修复后主表自带 pit_avail_date → 优先直读(省一次映射)
    src_has_pit = any(r[1] == "pit_avail_date" for r in main_conn.execute(
        "PRAGMA table_info(stock_factors_fundamental)"))
    log("pit_avail_date来源: %s" % ("主表直读(生成端)" if src_has_pit
                                   else "本脚本映射(主表无该列)"))

    # 增量: 只处理 report_date >= 已有最大值（先删重叠期, 幂等）
    max_rd = pit_conn.execute(
        "SELECT MAX(report_date) FROM %s" % TBL_PIT).fetchone()[0]
    if max_rd is not None:
        periods = [p for p in periods if p >= max_rd]
        pit_conn.execute("DELETE FROM %s WHERE report_date >= ?" % TBL_PIT, (max_rd,))
        pit_conn.commit()
        log("增量续跑: 从 %s 起（重叠期已清理）, 待处理 %d 期" % (max_rd, len(periods)))
    else:
        log("空库, 全量构建 %d 期" % len(periods))

    # droe/roa 预计算(一次性, 小表~30万行)
    log("计算 droe/roa (finance_daily 季度网格)...")
    dr = compute_droe_roa(main_conn)
    dr_idx = dr.set_index(["code", "report_date"])[["droe", "roa"]]

    cols = ["code", "report_date", "pit_avail_date", "ep", "bp", "sp", "roe",
            "lev", "ato", "pm", "eps", "bps", "sps", "droe", "roa",
            "ref_date", "ref_close", "n_days"]
    total = 0
    for i, rd in enumerate(periods, 1):
        tp = time.time()
        df = extract_partition(main_conn, rd, src_has_pit=src_has_pit)
        if df.empty:
            log("[%3d/%3d] %d: 0行(跳过)" % (i, len(periods), rd))
            continue
        # join droe/roa
        df = df.set_index(["code", "report_date"])
        for c in ("droe", "roa"):
            df[c] = dr_idx[c].reindex(df.index)
        df = df.reset_index()
        df = df[cols]
        df.to_sql(TBL_PIT, pit_conn, if_exists="append", index=False)
        pit_conn.commit()
        total += len(df)
        log("[%3d/%3d] 报告期 %d: %5d行  pit_avail=%d  累计%d行  %.1fs"
            % (i, len(periods), rd, len(df), pit_avail_date(rd), total,
               time.time() - tp))

    # 汇总与元数据
    n, codes, rd_min, rd_max = pit_conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT code), MIN(report_date), MAX(report_date) "
        "FROM %s" % TBL_PIT).fetchone()
    meta = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "source": "dragon_quant.db.stock_factors_fundamental(只读) + finance_daily",
        "rows": n, "codes": codes, "report_date_range": "%d~%d" % (rd_min, rd_max),
        "pit_rule": "法定披露截止日: 0331→0430, 0630→0831, 0930→1031, 1231→次年0430",
        "snapshot": "期后首个交易日快照(ref_date/ref_close), 每股量: eps=ep, bps=bp×ref_close, sps=sp×ref_close [2026-08-22修复]",
        "droe_def": "净利润(累计)/期末净资产 − 4季前同季(完整季度网格shift4)",
        "roa_def": "净利润(累计)/期末总资产",
        "known_limits": "不含退市股(继承原表幸存者偏差); ref快照后财务修订未回溯; bps基于原始未复权价",
    }
    pit_conn.executemany("INSERT OR REPLACE INTO build_meta VALUES (?,?)",
                         [(str(k), str(v)) for k, v in meta.items()])
    pit_conn.commit()
    pit_conn.execute("PRAGMA optimize")
    pit_conn.close()
    main_conn.close()
    log("完成: %s 行 / %d 只 / 报告期 %d~%d  总耗时 %.0fs"
        % (format(n, ","), codes, rd_min, rd_max, time.time() - t0))


# ────────────────────────────────────────────────────────────
# 消费端查询模板（PIT铁律入口）
# ────────────────────────────────────────────────────────────
GET_PIT_SQL = f"""
SELECT p.*
FROM {TBL_PIT} p
JOIN (SELECT code, MAX(report_date) AS rd
      FROM {TBL_PIT}
      WHERE pit_avail_date <= :T
      GROUP BY code) m
  ON p.code = m.code AND p.report_date = m.rd
"""


def get_pit_factor(conn: sqlite3.Connection, date: int,
                   max_stale_days: int = 400) -> pd.DataFrame:
    """交易日T(YYYYMMDD int)每股可合法使用的最新一期报告财务快照。

    语义: pit_avail_date <= T 的最新一期 report_date（法定披露截止日对齐,
    保守无前视）。max_stale_days: 报告期距T的最大日历日陈旧度(默认400天,
    超期视为财务过旧剔除, 与 factor_gpoa_study.py 一致)。

    返回列: code, report_date, pit_avail_date, ep, bp, sp, roe, lev, ato, pm,
            eps, bps, sps, droe, roa, ref_date, ref_close, n_days

    示例(结合当日价格重建估值因子):
        snap = get_pit_factor(conn, 20220801)
        ep_T = snap["eps"]                       # 生产口径 ep=EPS
        bp_T = snap["bps"] / close_T             # 当日BP(账面市值比 1/PB, bps=每股净资产) [2026-08-22修复]
    """
    df = pd.read_sql_query(GET_PIT_SQL, conn, params={"T": int(date)})
    if df.empty or max_stale_days is None:
        return df
    rd = pd.to_datetime(df["report_date"].astype(str), format="%Y%m%d")
    td = pd.Timestamp(str(date))
    df = df[(td - rd).dt.days <= max_stale_days].reset_index(drop=True)
    return df


def selftest() -> int:
    """消费模板自测: 验证PIT时点语义(以2022-07-01为例)"""
    conn = sqlite3.connect("file:%s?mode=ro" % PIT_DB, uri=True)
    try:
        n, = conn.execute("SELECT COUNT(*) FROM %s" % TBL_PIT).fetchone()
        log("自测: %s 共 %s 行" % (TBL_PIT, format(n, ",")))
        # T=2022-07-01: 中报0630的pit_avail=0831 > T, 必不可用;
        #               最新可用应为2021年报(1231, avail=20220430)或2022Q1(0331, avail=0430)
        for T, expect in ((20220701, 20220331), (20220901, 20220630),
                          (20220505, 20220331), (20221103, 20220930)):
            snap = get_pit_factor(conn, T)
            got = snap["report_date"].max() if len(snap) else None
            status = "OK" if got == expect else "FAIL"
            log("  T=%d: 截面%d只, 最新可用报告期=%s (期望%d) [%s]"
                % (T, len(snap), got, expect, status))
            if status == "FAIL":
                return 1
        # 抽查: 同一T下每股只取一期且pit_avail_date<=T
        snap = get_pit_factor(conn, 20220701)
        assert snap["code"].is_unique, "每股应只有一期"
        assert (snap["pit_avail_date"] <= 20220701).all(), "可用日越界(前视!)"
        log("  每股单期 + pit_avail_date<=T 约束: OK")
        log("自测全部通过")
        return 0
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════
# V2 构建(幂等增量) + V2 自测
# ════════════════════════════════════════════════════════════════════════
GET_PIT_SQL_V2 = f"""
SELECT p.*
FROM {TBL_PIT_V2} p
JOIN (SELECT code, MAX(report_date) AS rd
      FROM {TBL_PIT_V2}
      WHERE pit_avail_date <= :T
      GROUP BY code) m
  ON p.code = m.code AND p.report_date = m.rd
"""


def get_pit_factor_v2(conn: sqlite3.Connection, date: int,
                      max_stale_days: int = 400) -> pd.DataFrame:
    """V2消费模板: 与get_pit_factor同语义, 读factor_pit_v2(快照口径二期)。

    返回列: 同v1全部列 + snap_rule(first/fwd5/tail)
    """
    df = pd.read_sql_query(GET_PIT_SQL_V2, conn, params={"T": int(date)})
    if df.empty or max_stale_days is None:
        return df
    rd = pd.to_datetime(df["report_date"].astype(str), format="%Y%m%d")
    td = pd.Timestamp(str(date))
    return df[(td - rd).dt.days <= max_stale_days].reset_index(drop=True)


def build_v2(rebuild: bool = False, use_guard: bool = True) -> None:
    t0 = time.time()
    main_conn = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True, timeout=120)
    pit_conn = sqlite3.connect(PIT_DB)
    pit_conn.executescript(CREATE_SQL_V2)
    pit_conn.executescript(META_SQL)

    if rebuild:
        pit_conn.execute("DROP TABLE IF EXISTS %s" % TBL_PIT_V2)
        pit_conn.executescript(CREATE_SQL_V2)
        log("[v2] 已删表, 全量重建")

    periods = [r[0] for r in main_conn.execute(
        "SELECT DISTINCT report_date FROM stock_factors_fundamental "
        "WHERE report_date IS NOT NULL ORDER BY report_date")]
    log("[v2] 主库报告期分区数: %d (%d ~ %d)" % (len(periods), periods[0], periods[-1]))

    # 生成端pit_avail_date列直读(分区级众数校验)
    pa_rows = main_conn.execute(
        """SELECT report_date, pit_avail_date, COUNT(*) n FROM stock_factors_fundamental
           WHERE report_date IS NOT NULL GROUP BY report_date, pit_avail_date""")
    pa_map = {}
    for rd, pa, n in pa_rows:
        if rd not in pa_map or n > pa_map[rd][1]:
            pa_map[rd] = (pa, n)

    max_rd = pit_conn.execute(
        "SELECT MAX(report_date) FROM %s" % TBL_PIT_V2).fetchone()[0]
    if max_rd is not None:
        periods = [p for p in periods if p >= max_rd]
        pit_conn.execute("DELETE FROM %s WHERE report_date >= ?" % TBL_PIT_V2, (max_rd,))
        pit_conn.commit()
        log("[v2] 增量续跑: 从 %s 起（重叠期已清理）, 待处理 %d 期" % (max_rd, len(periods)))
    else:
        log("[v2] 空表, 全量构建 %d 期" % len(periods))

    log("[v2] 计算 droe/roa (finance_daily 季度网格, 与v1相同)...")
    dr = compute_droe_roa(main_conn)
    dr_idx = dr.set_index(["code", "report_date"])[["droe", "roa"]]

    cols = ["code", "report_date", "pit_avail_date", "ep", "bp", "sp", "roe",
            "lev", "ato", "pm", "eps", "bps", "sps", "droe", "roa",
            "ref_date", "ref_close", "n_days", "snap_rule"]
    landing_log, total = [], 0
    for i, rd in enumerate(periods, 1):
        tp = time.time()
        snap, dec = extract_partition_v2(main_conn, rd, pa_map.get(rd, (None, 0))[0],
                                         use_guard=use_guard)
        if snap is None or snap.empty:
            log("[v2][%3d/%3d] %d: 0行(跳过)" % (i, len(periods), rd))
            continue
        snap = snap.set_index(["code", "report_date"])
        for c in ("droe", "roa"):
            snap[c] = dr_idx[c].reindex(snap.index)
        snap = snap.reset_index()[cols]
        snap.to_sql(TBL_PIT_V2, pit_conn, if_exists="append", index=False)
        pit_conn.commit()
        total += len(snap)
        landing_log.append(dec)
        log("[v2][%3d/%3d] 报告期 %d: %5d行 pit_avail=%d d0=%s guard=%s 累计%d行 %.1fs"
            % (i, len(periods), rd, len(snap), pit_avail_date(rd), dec.get("d0"),
               dec.get("guard"), total, time.time() - tp))

    n, codes, rd_min, rd_max = pit_conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT code), MIN(report_date), MAX(report_date) "
        "FROM %s" % TBL_PIT_V2).fetchone()
    rules = dict(pit_conn.execute(
        "SELECT snap_rule, COUNT(*) FROM %s GROUP BY snap_rule" % TBL_PIT_V2).fetchall())
    meta = {
        "v2_generated": datetime.now().isoformat(timespec="seconds"),
        "v2_source": "dragon_quant.db.stock_factors_fundamental(只读) + finance_daily",
        "v2_rows": n, "v2_codes": codes,
        "v2_report_date_range": "%d~%d" % (rd_min, rd_max),
        "v2_pit_rule": "法定披露截止日: 0331→0430, 0630→0831, 0930→1031, 1231→次年0430(消费语义与v1一致)",
        "v2_snapshot": ("pit_avail_date后首个有行交易日快照; 个股停牌+5分区交易日回退; "
                        "年报分区/窗口无行回退分区末行(tail); 落地守卫%s"
                        % ("开(中位比[%.2f,%.2f]+±%.0f%%内占比>=%.0f%%, 仅捕获大幅占位污染如2026Q1)"
                           % (GUARD_MED_LO, GUARD_MED_HI, GUARD_BAND * 100, GUARD_BAND_MIN * 100)
                           if use_guard else "关(--no-guard, 纯字面口径)")),
        "v2_snap_rule_counts": str(rules),
        "v2_known_limits": ("年报分区无pit_avail后行→tail回退值可能含披露前部分真值(仅影响缺Q1行的个股); "
                            "守卫后快照日可能晚于个别回测T(法定已披露、源库晚落库, 见报告§机理); "
                            "幸存者偏差继承源表; bps基于原始未复权价"),
    }
    pit_conn.executemany("INSERT OR REPLACE INTO build_meta VALUES (?,?)",
                         [("v2_" + k if not k.startswith("v2_") else k, str(v))
                          for k, v in meta.items()])
    pit_conn.execute("INSERT OR REPLACE INTO build_meta VALUES ('v2_landing_log', ?)",
                     (json.dumps(landing_log, ensure_ascii=False),))
    pit_conn.commit()
    pit_conn.execute("PRAGMA optimize")
    pit_conn.close()
    main_conn.close()
    log("[v2] 完成: %s 行 / %d 只 / 报告期 %d~%d  snap_rule=%s  总耗时 %.0fs"
        % (format(n, ","), codes, rd_min, rd_max, rules, time.time() - t0))


def selftest_v2() -> int:
    """V2自测: PIT语义 + 2026Q1占位值回归断言(W1报告⑤.2⑥的落地)"""
    import json as _json
    conn = sqlite3.connect("file:%s?mode=ro" % PIT_DB, uri=True)
    try:
        n, = conn.execute("SELECT COUNT(*) FROM %s" % TBL_PIT_V2).fetchone()
        log("[v2自测] %s 共 %s 行" % (TBL_PIT_V2, format(n, ",")))
        for T, expect in ((20220701, 20220331), (20220901, 20220630),
                          (20220505, 20220331), (20221103, 20220930),
                          (20260506, 20260331)):
            snap = get_pit_factor_v2(conn, T)
            got = snap["report_date"].max() if len(snap) else None
            status = "OK" if got == expect else "FAIL"
            log("  T=%d: 截面%d只, 最新可用报告期=%s (期望%d) [%s]"
                % (T, len(snap), got, expect, status))
            if status == "FAIL":
                return 1
        snap = get_pit_factor_v2(conn, 20220701)
        assert snap["code"].is_unique, "每股应只有一期"
        assert (snap["pit_avail_date"] <= 20220701).all(), "可用日越界(前视!)"

        # 断言A(占位值回归): T=20260814, 2026Q1快照eps应≈生产当前ep(W1时中位差15.8倍)
        snap = get_pit_factor_v2(conn, 20260814)
        q1 = snap[snap["report_date"] == 20260331].set_index("code")["eps"]
        main_conn = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True)
        cur = pd.read_sql_query(
            """SELECT code, ep FROM stock_factors_fundamental
               WHERE date=20260814 AND report_date=20260331""", main_conn)
        main_conn.close()
        cur["code"] = cur["code"].astype(str)
        cur = cur.set_index("code")["ep"]
        m = pd.concat([q1, cur], axis=1, keys=["pit2", "prod"]).dropna()
        m = m[(m["pit2"] > 0) & (m["prod"] > 0)]
        ratio = (m["prod"] / m["pit2"])
        med = float(ratio.median())
        ok = 0.8 <= med <= 1.25
        log("  [v2断言A] 2026Q1快照eps vs 生产当前ep: n=%d 中位比=%.3f (v1时15.8倍) [%s]"
            % (len(m), med, "PASS" if ok else "FAIL"))

        # 断言B(快照日口径): 全表 ref_date > pit_avail_date 的行占比(年报tail回退除外)
        tot, after, tail = conn.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN ref_date > pit_avail_date THEN 1 ELSE 0 END),
                      SUM(CASE WHEN snap_rule='tail' THEN 1 ELSE 0 END)
               FROM %s""" % TBL_PIT_V2).fetchone()
        log("  [v2断言B] ref_date>pit_avail_date: %d/%d (%.1f%%), tail回退%d行"
            % (after, tot, after / tot * 100, tail))
        log("[v2自测] 完成")
        return 0 if ok else 1
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="删表全量重建")
    ap.add_argument("--selftest", action="store_true", help="仅运行消费模板自测(v1)")
    ap.add_argument("--v2", action="store_true",
                    help="构建/自测二期快照口径表 factor_pit_v2")
    ap.add_argument("--no-guard", action="store_true",
                    help="v2构建时关闭落地守卫(纯字面口径, A/B对照用)")
    ap.add_argument("--selftest-v2", action="store_true",
                    help="仅运行v2表自测(含2026Q1占位值回归断言)")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.selftest_v2:
        return selftest_v2()
    if a.v2:
        build_v2(rebuild=a.rebuild, use_guard=not a.no_guard)
        return selftest_v2()
    build(rebuild=a.rebuild)
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
