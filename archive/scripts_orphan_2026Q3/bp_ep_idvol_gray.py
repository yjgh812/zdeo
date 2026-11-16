#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bp_ep_idvol_gray.py — bp_ep+id_vol 模拟盘灰度轨道 (方案A, 主人2026-08-15批准)

轨道: 独立10万元账本, 月度换仓, 与生产bp+ep并行; 观察期 2026-09-01 建仓 ~ 2026-11-30 评估。
口径与回测完全一致: 月末信号(上月最后交易日) → 次月首个交易日收盘成交;
域 = 60/00/30/68 非ST, 信号日 raw close≤30元, ep>0&P/B>0 (factor_pit PIT), 行业≤4, Top20;
成本 = 0.4%单边(与回测源头bp_ep_amp_monthly_v2统一, B1-P0); 涨跌停收盘不可买则跳过(验证②:影响可忽略)。

子命令:
  --init                初始化账本 ledger.json (现金10万)
  --run [--date YYYYMMDD]  主流程(供cron调用): 若非本月首交易日则退出;
                           信号生成→按当日收盘成交→更新账本→拥挤度体检→降档判定
  --signal --date YYYYMMDD  仅生成信号文件(验证用, 不入账本)
  --evaluate            观察期评估(2026-11-30): 月收益/IC/决策

文件:
  D:\\zcodesj\\gray_idvol_track\\ledger.json      账本
  D:\\zcodesj\\gray_idvol_track\\signal_*.md     信号与订单(含名称, 铁律)
  D:\\zcodesj\\gray_idvol_track\\report_*.md     月度执行报告
用法: python D:/zcodesj/bp_ep_idvol_gray.py --run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

sys.path.insert(0, r"D:\zcodesj")
from stock_name_lookup import require_name  # noqa: E402 铁律: 名称唯一接口
import importlib.util  # noqa: E402
_spec_sp = importlib.util.spec_from_file_location(
    "sp", r"D:/zcodesj/aurumq_alpha101_spotcheck_20260815.py")
sp = importlib.util.module_from_spec(_spec_sp)
_spec_sp.loader.exec_module(sp)
from factor_methodology_tools_20260815 import decorr_factor  # noqa: E402

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
TRACK_DIR = Path(r"D:\zcodesj\gray_idvol_track")
VARIANTS = {"v1": ["bp_ep", "iv"], "v6": ["bp_ep", "iv_dc", "alpha015"]}
# v1=原批准方案(bp_ep+id_vol); v6=扩展变体(bp_ep+id_vol_deCorr+alpha015, 2026-08-15扩展测试)
LEDGER = TRACK_DIR / "ledger.json"
FACTOR_DB = r"D:\zcodesj\factor_idvol.db"
STOCK_PREFIX = ("00", "30", "60", "68")
GHOST_DATES = {20181231, 20260627}
# B1-P0 成本口径统一 (2026-08-17): 原 COST=0.001(0.1%) 与回测源头 bp_ep_amp_monthly_v2.py
# (COST=0.004 单边, 月度调仓固定成本) 不一致 → 统一为 0.004, 防灰度轨收益虚高。
# 说明: 诚实基线 DX-1 用市值分桶动态成本(小盘30bp×51%笔数), 灰度轨月度调仓近似
#       取回测源头的固定单边 0.4% (含佣金+印花税+滑点代理), 与 --backtest 对照同口径。
COST = 0.004
TOPN, CAND_POOL, MAX_PER_IND = 20, 60, 4
MAX_STALE_DAYS = 400
MIN_CROSS = 50
INIT_CASH = 100_000.0
EVAL_MONTH = "202611"

# ═══════════════ B1-P0 修复 (2026-08-17) ═══════════════
# risk-engineer B-RISK 复核: v1 表 (stock_factors_fundamental_pit) 存在占位值污染
#   (000001 2026Q1 eps 差 10.5 倍: v1=0.0605 vs v2=0.638; [0.8,1.25] 占比 0.0% 异常)。
# 修复: 默认读 v2 表 (snap_rule 溯源, 落地守卫后快照), 提供 --pit-source 开关;
#       v1 表仅诊断用(--pit-source v1), 禁止生产默认; tail 回退占比>告警阈值时计数告警。
PIT_TABLE_V2 = "stock_factors_fundamental_pit_v2"
PIT_TABLE_V1 = "stock_factors_fundamental_pit"
TAIL_WARN_PCT = 0.25        # snap_rule='tail' 占比告警阈值 (实测 26.3%, >25% 触发)
TAIL_WARN_ABS = 500         # tail 绝对行数告警下限
# ════════════════════════════════════════════════════════

t0 = time.time()


def log(m):
    try:
        print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)
    except UnicodeEncodeError:
        # Windows GBK 控制台兜底: 剔除非 ASCII 后再打, 不阻断流程 (B1-P0)
        ascii_m = m.encode("ascii", "ignore").decode("ascii")
        print("[%s] %s" % (time.strftime("%H:%M:%S"), ascii_m), flush=True)


# ═══════════════ 数据 ═══════════════

def load_prices() -> pd.DataFrame:
    like = " OR ".join(f"code LIKE '{p}%'" for p in STOCK_PREFIX)
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=300)
    try:
        df = pd.read_sql_query(
            f"SELECT code, date, close, volume, change_pct FROM stock_daily_meta_qfq "
            f"WHERE ({like}) AND close > 0 ORDER BY date, code", con)
    finally:
        con.close()
    df["date"] = df["date"].astype(int)
    df = df[~df["date"].isin(GHOST_DATES)]
    df["code"] = df["code"].astype(str).str.zfill(6)
    piv = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    piv.index = pd.to_datetime(piv.index.astype(str), format="%Y%m%d")
    chg = df.pivot_table(index="date", columns="code", values="change_pct", aggfunc="last").sort_index()
    chg.index = pd.to_datetime(chg.index.astype(str), format="%Y%m%d")
    vol = df.pivot_table(index="date", columns="code", values="volume", aggfunc="last").sort_index()
    vol.index = pd.to_datetime(vol.index.astype(str), format="%Y%m%d")
    return piv, chg, vol


def load_st_and_industry():
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
    try:
        info = pd.read_sql_query("SELECT code, name, industry, is_st FROM stock_info", con)
    finally:
        con.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    st = set(info.loc[(info["is_st"].astype(str) == "1")
                      | info["name"].fillna("").str.contains("ST")
                      | info["name"].fillna("").str.contains("退"), "code"])
    ind = dict(zip(info["code"], info["industry"].fillna("UNKNOWN")))
    return st, ind


PIT_SOURCE = "v2"  # 全局因子源开关 (--pit-source 覆盖); B1-P0 默认 v2


def load_pit() -> pd.DataFrame:
    """读 PIT 因子表 (B1-P0: 默认 v2 快照口径表, 含落地守卫与 snap_rule 溯源)。

    v2 = stock_factors_fundamental_pit_v2 (snap_rule: first/fwd5/tail, 守卫后快照);
    v1 = stock_factors_fundamental_pit (旧表, 2026Q1 占位值污染: 000001 eps 差 10.5 倍) ——
         **仅诊断用**, 生产默认禁用。
    """
    tbl = PIT_TABLE_V2 if PIT_SOURCE == "v2" else PIT_TABLE_V1
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            f"SELECT code, report_date, pit_avail_date, eps, bps, snap_rule FROM {tbl}",
            con)
    finally:
        con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["avail_ts"] = pd.to_datetime(df["pit_avail_date"].astype(str), format="%Y%m%d")
    # B1-P0: tail 回退计数告警 (v2 实测 26.3% > 25% 阈值; v1 无 snap_rule 列)
    if PIT_SOURCE == "v2" and "snap_rule" in df.columns:
        n_tail = int((df["snap_rule"] == "tail").sum())
        n_all = max(len(df), 1)
        tail_pct = n_tail / n_all
        if tail_pct > TAIL_WARN_PCT and n_tail > TAIL_WARN_ABS:
            log(f"⚠️ [PIT-tail告警] snap_rule=tail 回退 {n_tail} 行 ({tail_pct*100:.1f}% > "
                f"{TAIL_WARN_PCT*100:.0f}%), 含年报分区/长停牌样本, 注意财务值可能偏旧")
    return df


def load_raw_close_at(dates: list) -> dict:
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=300)
    try:
        ph = ",".join("?" * len(dates))
        df = pd.read_sql_query(
            f"SELECT date, code, close FROM stock_factors_fundamental WHERE date IN ({ph})",
            con, params=[int(d) for d in dates])
    finally:
        con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return {pd.Timestamp(str(d)): g.set_index("code")["close"]
            for d, g in df.groupby("date")}


def id_vol_monthly(close: pd.DataFrame, dates: list, win=20) -> pd.DataFrame:
    ret = close.pct_change()
    out = {}
    for s in dates:
        r = ret.loc[:s].tail(win + 1)
        if len(r) < win:
            continue
        r = r.iloc[1:]
        mkt = r.mean(axis=1).values
        mkt_d = mkt - mkt.mean()
        denom = (mkt_d ** 2).sum()
        if denom == 0:
            continue
        r_d = r.sub(r.mean(axis=0), axis=1)
        beta = (r_d.values.T @ mkt_d) / denom
        resid = r_d.values - np.outer(mkt_d, beta)
        out[s] = pd.Series(resid.std(axis=0), index=r.columns)
    return pd.DataFrame(out).T


# ═══════════════ 信号生成 (与回测同口径) ═══════════════

def generate_signal(signal_date: pd.Timestamp, prices: pd.DataFrame, pit: pd.DataFrame,
                    st_codes: set, ind_map: dict, rc_idx: dict, iv: pd.DataFrame,
                    iv_dc: pd.DataFrame, alpha015: pd.DataFrame, variant: str) -> list[dict]:
    """返回 Top20 订单列表 [{code,name,score,close,industry}]"""
    rc = rc_idx.get(signal_date)
    if rc is None:
        return []
    elig = rc.index[(rc <= 30.0) & (~rc.index.isin(st_codes))
                    & rc.index.str[:2].isin(STOCK_PREFIX)]
    elig = elig.intersection(prices.columns)
    sub = pit[pit["avail_ts"] <= signal_date]
    sub = sub.sort_values(["code", "avail_ts"]).groupby("code", as_index=False).tail(1)
    sub = sub[(signal_date - sub["avail_ts"]).dt.days <= MAX_STALE_DAYS].set_index("code")
    if sub.empty or len(elig) < MIN_CROSS or signal_date not in iv.index:
        return []
    eps = sub["eps"].reindex(elig)
    pb = sub["bps"].reindex(elig) / rc  # [2026-08-22修复] bp=每股净资产/价, 原 rc/bps 反向
    cs = pd.DataFrame({"eps": eps, "pb": pb}).dropna()
    cs = cs[(cs["eps"] > 0) & (cs["pb"] > 0)]
    if len(cs) < MIN_CROSS:
        return []
    cs["bp_ep"] = (cs["eps"].rank(pct=True) + cs["pb"].rank(pct=True)) / 2
    cs["iv"] = iv.loc[signal_date].reindex(cs.index)
    if "iv_dc" in VARIANTS[variant] and signal_date in iv_dc.index:
        cs["iv_dc"] = iv_dc.loc[signal_date].reindex(cs.index)
    if "alpha015" in VARIANTS[variant]:
        cs["alpha015"] = alpha015.loc[signal_date].reindex(cs.index)
    comps = VARIANTS[variant]
    sub2 = cs[comps].dropna()
    if len(sub2) < MIN_CROSS:
        return []
    parts = []
    for c in comps:
        r = sub2[c].rank(pct=True)
        if c in ("iv", "iv_dc"):
            r = -r
        parts.append(r)
    score = (sum(parts) / len(parts)).sort_values(ascending=False)
    orders, used = [], {}
    for code in score.index[:CAND_POOL]:
        if len(orders) >= TOPN:
            break
        ind = ind_map.get(code, "UNKNOWN")
        if used.get(ind, 0) >= MAX_PER_IND:
            continue
        used[ind] = used.get(ind, 0) + 1
        orders.append({"code": code, "name": require_name(code),
                       "score": round(float(score[code]), 4),
                       "close": float(rc[code]), "industry": ind})
    return orders


def write_signal_file(orders: list[dict], signal_date: pd.Timestamp, exec_date) -> Path:
    f = TRACK_DIR / f"signal_{signal_date.strftime('%Y%m%d')}.md"
    lines = [f"# bp_ep+id_vol 灰度轨道信号 — 信号日 {signal_date.date()} → 成交日 {exec_date.date()}",
             "",
             "| # | 代码 | 名称 | 分数 | 信号日raw收盘 | 行业 |",
             "|---|---|---|---|---|---|"]
    for i, o in enumerate(orders, 1):
        lines.append("| %d | %s | %s | %.4f | %.2f | %s |" % (
            i, o["code"], o["name"], o["score"], o["close"], o["industry"]))
    f.write_text("\n".join(lines), encoding="utf-8")
    return f


# ═══════════════ 账本 ═══════════════

def load_ledger() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    return None


def init_ledger() -> dict:
    l = {"cash": INIT_CASH, "positions": {}, "state": "active", "degrade_months": 0,
         "history": [], "created": datetime.now().isoformat(timespec="seconds")}
    LEDGER.parent.mkdir(exist_ok=True)
    LEDGER.write_text(json.dumps(l, ensure_ascii=False, indent=1), encoding="utf-8")
    return l


def apply_rebalance(ledger: dict, orders: list[dict], exec_date: pd.Timestamp,
                    prices: pd.DataFrame, chg: pd.DataFrame) -> dict:
    """按 exec 日收盘成交: 卖旧→买新(每只权益/20, 整手), 涨跌停不可买则跳过; 0.1%单边成本"""
    px = prices.loc[exec_date] if exec_date in prices.index else pd.Series(dtype=float)
    ch = chg.loc[exec_date] if exec_date in chg.index else pd.Series(dtype=float)
    nav0 = ledger["cash"] + sum(v["shares"] * px.get(c, 0.0)
                                for c, v in ledger["positions"].items())
    # 卖出全部旧仓
    proceeds = 0.0
    for c, v in ledger["positions"].items():
        p = px.get(c)
        if p == p and p > 0:
            proceeds += v["shares"] * p * (1 - COST)
    ledger["cash"] += proceeds
    ledger["positions"] = {}
    # 买入新仓
    equity = ledger["cash"]
    per = equity / max(len(orders), 1)
    bought, skipped = [], []
    for o in orders:
        c = o["code"]
        p = px.get(c)
        if p != p or p <= 0:
            skipped.append((c, "无价格"))
            continue
        lim = 0.198 if c[:3] in ("300", "301", "688") else 0.098
        cg = ch.get(c)
        if cg == cg and cg >= lim * 100 - 0.1:  # change_pct 单位=百分数(如1.2=1.2%)
            skipped.append((c, "涨停收盘不可买"))
            continue
        shares = int(per / (p * 100)) * 100
        if shares < 100:
            skipped.append((c, "资金不足1手"))
            continue
        cost = shares * p
        if cost > ledger["cash"]:
            skipped.append((c, "现金不足"))
            continue
        ledger["cash"] -= cost * (1 + COST)
        ledger["positions"][c] = {"shares": shares, "price": float(p)}
        bought.append(c)
    nav1 = ledger["cash"] + sum(v["shares"] * px.get(c, 0.0)
                                for c, v in ledger["positions"].items())
    ledger["history"].append({
        "exec_date": exec_date.strftime("%Y%m%d"),
        "bought": bought, "skipped": skipped,
        "nav_start": round(nav0, 2), "nav_end": round(nav1, 2),
        "monthly_ret": round(nav1 / nav0 - 1, 6) if nav0 > 0 else 0.0,
    })
    return ledger


# ═══════════════ 拥挤度 ═══════════════

def _upsert_factor_db(db: str, factor_name: str, series: pd.DataFrame) -> int:
    """月截面 upsert 进因子库 (幂等)"""
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS factor_price_volume "
                "(date INTEGER, code TEXT, factor TEXT, value REAL)")
    rows = []
    for s in series.index:
        for code, v in series.loc[s].dropna().items():
            rows.append((int(s.strftime("%Y%m%d")), code, factor_name, float(v)))
    con.executemany(
        "INSERT OR REPLACE INTO factor_price_volume VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()
    return len(rows)


def update_factor_dbs(iv: pd.DataFrame, alpha015: pd.DataFrame) -> None:
    """id_vol + alpha015 月截面入库 (灰度轨道两分量, 幂等)"""
    n1 = _upsert_factor_db(FACTOR_DB, "id_vol", iv)
    n2 = _upsert_factor_db(r"D:/zcodesj/factor_alpha015.db", "alpha015", alpha015)
    log("      因子库更新: id_vol %d 行, alpha015 %d 行" % (n1, n2))


def run_crowding() -> dict:
    """双因子拥挤度体检 (id_vol + alpha015, V6两分量)"""
    details, level_warn, slope_warn = [], False, False
    for db, f in ((FACTOR_DB, "id_vol"), (r"D:/zcodesj/factor_alpha015.db", "alpha015")):
        r = subprocess.run(
            [sys.executable, r"D:\zcodesj\factor_crowding_monitor.py",
             "--db", db, "--factors", f],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = r.stdout
        if "未触发" not in out:
            level_warn = True
        if "SLOPE_WARN" in out:
            slope_warn = True
        for line in out.splitlines():
            if "结论" in line or "SLOPE_WARN" in line:
                details.append("[%s] %s" % (f, line.strip()))
    return {"level_warn": level_warn, "slope_warn": slope_warn,
            "detail": "\n".join(details[-8:])}


# ═══════════════ 主流程 ═══════════════

def is_first_trading_day(today: datetime, prices: pd.DataFrame) -> bool:
    """今日是否本月首个交易日 (交易日历取qfq价格索引)"""
    ymd = int(today.strftime("%Y%m%d"))
    # 8/16全量巡检P0修复：原生datetime无to_period（pandas方法），先归一为pd.Timestamp
    ts_today = pd.Timestamp(today.date())
    month_days = prices.index[prices.index.to_period("M") == ts_today.to_period("M")]
    if len(month_days) == 0:
        return False
    first = month_days.min()
    return first == ts_today and int(first.strftime("%Y%m%d")) == ymd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--signal", action="store_true")
    ap.add_argument("--date", default=None, help="YYYYMMDD 信号日(默认最新月末)")
    ap.add_argument("--track", default="v1", choices=list(VARIANTS), help="轨道变体")
    ap.add_argument("--pit-source", default="v2", choices=["v2", "v1"],
                    help="PIT 因子表源: v2=快照口径(默认,含落地守卫/snap_rule) / "
                         "v1=旧表(有2026Q1占位值污染,仅诊断用,生产禁用)")
    ap.add_argument("--evaluate", action="store_true")
    a = ap.parse_args()
    global TRACK_DIR, LEDGER, PIT_SOURCE
    PIT_SOURCE = a.pit_source
    TRACK_DIR.mkdir(exist_ok=True)

    track = a.track
    TRACK_DIR = TRACK_DIR / track
    TRACK_DIR.mkdir(exist_ok=True)
    LEDGER = TRACK_DIR / "ledger.json"

    if a.init:
        l = init_ledger()
        log("账本初始化[%s]: cash=%.0f, state=%s" % (track, l["cash"], l["state"]))
        return 0

    if a.evaluate:
        return evaluate()

    # ── 主流程 ──
    today = datetime.now()
    log("[1/5] 加载数据...")
    o = sp.load_data()
    prices, vol = o["close"], o["volume"]
    chg = prices.pct_change() * 100.0  # 百分数单位(与qfq库change_pct列一致)
    pit = load_pit()
    st_codes, ind_map = load_st_and_industry()
    cal = prices.index
    ym_all = cal.to_period("M")
    month_ends_all = sorted(pd.Series(cal, index=ym_all).groupby(level=0).max())
    iv_all = id_vol_monthly(prices, month_ends_all)
    iv_dc_all = decorr_factor(iv_all, N=6)
    alpha015_all = sp.alpha015(o)

    if a.date:
        signal_date = pd.Timestamp(a.date)
        exec_date = signal_date
        is_first = True
    else:
        # 信号日 = 上月最后交易日; 成交日 = 本月首个交易日
        if not is_first_trading_day(today, prices):
            log("今日(%s)非本月首个交易日, 退出(由cron每日09:15调用, 首交易日才执行)" % today.date())
            return 0
        this_month = prices.index[prices.index.to_period("M") == pd.Timestamp(today.date()).to_period("M")]
        exec_date = pd.Timestamp(this_month.min().date())
        prev_month = prices.index[prices.index < exec_date]
        signal_date = pd.Timestamp(prev_month.max().date()) if len(prev_month) else None
        is_first = True
    if signal_date is None:
        log("无信号日, 退出")
        return 1

    log("      信号日 %s → 成交日 %s" % (signal_date.date(), exec_date.date()))

    # 数据新鲜度守卫 (fail-closed, 仅生产cron路径): 成交日收盘必须在最新数据中, 否则退出
    # (--date 显式日期为测试模式, 不守卫)
    last_day = prices.index.max()
    if a.date is None:
        if exec_date not in prices.index or exec_date < last_day:
            log("FATAL: 成交日 %s 数据不可用或非最新 (最新数据日 %s) — 可能16:30数据链路未完成, 拒绝成交" % (
                exec_date.date(), last_day.date()))
            return 2
        px_e = prices.loc[exec_date].dropna()
        if len(px_e) < 1000:
            log("FATAL: 成交日 %s 有效价格仅 %d 只 (<1000), 数据疑似未更新, 拒绝成交" % (exec_date.date(), len(px_e)))
            return 2
        log("      数据新鲜度OK: 成交日收盘 %d 只股票" % len(px_e))

    log("[2/5] 生成信号 (变体 %s)..." % track)
    rc_idx = load_raw_close_at([int(signal_date.strftime("%Y%m%d"))])
    orders = generate_signal(signal_date, prices, pit, st_codes, ind_map, rc_idx,
                             iv_all, iv_dc_all, alpha015_all, track)
    if not orders:
        log("信号为空, 退出")
        return 1
    sf = write_signal_file(orders, signal_date, exec_date)
    log("      Top20 信号已写: %s" % sf.name)

    if a.signal:
        log("仅生成信号, 不入账本 (验证模式)")
        return 0

    log("[3/5] 账本成交...")
    ledger = load_ledger()
    if ledger is None:
        log("账本不存在, 先 --init")
        return 1
    if ledger["state"] == "terminated":
        log("轨道已终止, 跳过")
        return 0
    half = ledger["state"] == "degraded_half"
    if half:
        log("      状态=degraded_half: 半仓执行")
        orders = orders[:10]
    apply_rebalance(ledger, orders, exec_date, prices, chg)
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    h = ledger["history"][-1]
    log("      成交: 买%d只, 跳过%d个, 月收益%+.2f%%, 净值%.0f" % (
        len(h["bought"]), len(h["skipped"]), h["monthly_ret"] * 100, h["nav_end"]))

    log("[4/5] 拥挤度体检 (id_vol + alpha015)...")
    # 降采样到月末截面再入库 (alpha015为日频面板, 需按月末取)
    alpha015_monthly = alpha015_all.reindex(month_ends_all).dropna(how="all")
    update_factor_dbs(iv_all, alpha015_monthly)
    crow = run_crowding()
    log("      level_warn=%s slope_warn=%s" % (crow["level_warn"], crow["slope_warn"]))
    if crow["level_warn"] or crow["slope_warn"]:
        ledger["degrade_months"] += 1
        if ledger["degrade_months"] >= 2 and ledger["state"] == "active":
            ledger["state"] = "degraded_half"
            log("      降档: 连续%d月拥挤警示 → degraded_half (半仓)" % ledger["degrade_months"])
        if ledger["degrade_months"] >= 3:
            ledger["state"] = "terminated"
            log("      终止: 连续%d月拥挤警示 → terminated" % ledger["degrade_months"])
    else:
        ledger["degrade_months"] = 0
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")

    log("[5/5] 写执行报告...")
    rep = TRACK_DIR / f"report_{exec_date.strftime('%Y%m%d')}.md"
    rep.write_text(
        "# 灰度轨道执行报告 %s\n\n- 信号: %s\n- 买入: %s\n- 跳过: %s\n- 月收益: %+.2f%%\n"
        "- 净值: %.2f\n\n## 拥挤度\n%s\n" % (
            exec_date.date(), sf.name, ", ".join(h["bought"]),
            str(h["skipped"]), h["monthly_ret"] * 100, h["nav_end"], crow["detail"]),
        encoding="utf-8")
    log("完成 → %s" % rep.name)

    # 观察期评估: 2026-12-01 换仓后历史≥3条 → 自动评估(方案A: 3个月观察期闭环)
    if len(ledger["history"]) >= 3 and today.date() >= datetime(2026, 12, 1).date():
        log("触发观察期评估...")
        evaluate()
    return 0


def evaluate() -> int:
    ledger = load_ledger()
    if not ledger or not ledger["history"]:
        log("账本为空, 无法评估")
        return 1
    hist = ledger["history"]
    rets = pd.Series([h["monthly_ret"] for h in hist],
                     index=pd.DatetimeIndex([h["exec_date"] for h in hist]))
    nav = (1 + rets).cumprod()
    n_months = len(rets)
    n_years = n_months / 12
    cagr = (nav.iloc[-1]) ** (1 / max(n_years, 1e-9)) - 1 if n_years > 0.3 else nav.iloc[-1] - 1
    vol = rets.std() * np.sqrt(12)
    sharpe = cagr / vol if vol > 0 else 0.0
    dd = (nav / nav.cummax() - 1).min()
    log("观察期评估 (%d个月): CAGR%+.1f%% Sharpe%.2f MDD%.1f%%" % (
        n_months, cagr * 100, sharpe, dd * 100))
    baseline_sharpe = 0.20  # bp_ep 基线回测 Sharpe
    decision = "continue"
    if sharpe < baseline_sharpe or cagr < 0.02:
        decision = "terminate"
    log("决策: %s (Sharpe%.2f vs 基线%.2f, CAGR%+.1f%%)" % (decision, sharpe, baseline_sharpe, cagr * 100))
    rep = TRACK_DIR / "evaluation_20261130.md"
    rep.write_text(
        "# bp_ep+id_vol 灰度轨道观察期评估\n\n- 期数: %d个月\n- CAGR: %+.1f%%\n- Sharpe: %.2f\n- MDD: %.1f%%\n"
        "- 决策: **%s** (终止条件: Sharpe<%.2f 或 CAGR<2%%)\n" % (
            n_months, cagr * 100, sharpe, dd * 100, decision, baseline_sharpe),
        encoding="utf-8")
    log("报告: %s" % rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
