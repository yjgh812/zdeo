# ⚠️ 2026-08-22 bps/sps 修复警示: 本脚本 bps 消费基于旧口径(close/bps, 且当时 factor_pit.bps=ref_close/bp 失真~50倍)。
# factor_pit.db 已修复(bps=bp×ref_close), 如需重跑本脚本请将 close/bps 改为 bps/close(仅历史审计, 非生产链路)。
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pit_snapshot_v2_revalidate_20260815.py — PIT快照口径二期(bp+ep)重验（三轨对比）

任务(PIT_SNAPSHOT_V2): 回答"PIT重建报告的bp/ep数字(+4~5%)在快照修正后是否变化"。

三轨(同一引擎同一universe, 唯一差异=财务值口径):
  pit_v2 — factor_pit.db.stock_factors_fundamental_pit_v2 (二期: pit_avail_date后
           首个交易日快照+5日回退+落地守卫; ep=eps, bp=close_T/bps)
  pit_v1 — stock_factors_fundamental_pit (一期: 期后首日快照=占位伪影, 对照)
  asof   — 生产现状: 原表 date=T 行当日原值 ep/bp (期末as-of含前视)

组合: bp+ep月度Top20生产结构(ep>0&bp>0→等权pct-rank→head60→行业≤4→Top20等权),
      ≤30元域(主), 全域(辅), qfq日频收益, 单边0.15%×双边平均换手。
执行: next_close(任务口径: 信号日T收盘选股, 下一交易日收盘成交)为主报,
      same_close(与factor_pit_revalidate_20260815.py同构, 连续性锚)并报。
窗口: 2019-01-01~2026-08-14(任务口径)。

输出: reports/_pit_snapshot_v2_revalidate_20260815.json + 控制台摘要
纪律: 主库/qfq库/factor_pit.db 全部 mode=ro 只读。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
OUT_JSON = Path(r"D:\zcodesj\reports\_pit_snapshot_v2_revalidate_20260815.json")

FIN_INDUSTRIES = ("银行", "证券", "保险", "多元金融", "信托")
GHOST_DATES = {20181231, 20260627}
COST = 0.0015
TOPN, CAND_POOL, MAX_PER_IND = 20, 60, 4
MAX_PRICE = 30.0
BT_START, BT_END = 20190101, 20260814
STOCK_PREFIX = ("00", "30", "60", "43", "68", "82", "83", "87", "92")


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


# ── 数据加载 ─────────────────────────────────────────────────
def qfq_calendar(s, e):
    con = ro(QFQ_DB)
    ds = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date BETWEEN ? AND ? "
        "ORDER BY date", (str(s), str(e)))]
    con.close()
    return [int(d) for d in ds if int(d) not in GHOST_DATES]


def month_first(calendar):
    df = pd.DataFrame({"d": calendar})
    return [int(x) for x in df.groupby(df["d"].astype(str).str[:6])["d"].first()]


def load_daily_qfq_close(codes, s, e):
    con = ro(QFQ_DB)
    frames = []
    codes = list(codes)
    B = 400
    for i in range(0, len(codes), B):
        chunk = codes[i:i + B]
        ph = ",".join("?" * len(chunk))
        frames.append(pd.read_sql_query(
            "SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN (%s) "
            "AND date BETWEEN ? AND ?" % ph, con, params=chunk + [s, e]))
    con.close()
    df = pd.concat(frames, ignore_index=True)
    df["code"] = df["code"].astype(str).str.zfill(6)
    piv = df.pivot_table(index="date", columns="code", values="close", aggfunc="last")
    piv.index = piv.index.astype(int)
    return piv.sort_index()


def load_exclusions():
    con = ro(MAIN_DB)
    info = pd.read_sql_query("SELECT code, name, industry, is_st FROM stock_info", con)
    con.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    st = set(info.loc[(info["is_st"].astype(str) == "1")
                      | info["name"].str.contains("ST") | info["name"].str.contains("退"),
                      "code"])
    fin = set(info.loc[info["industry"].fillna("").apply(
        lambda x: any(k in x for k in FIN_INDUSTRIES)), "code"])
    return st, fin, dict(zip(info["code"], info["industry"].fillna("UNKNOWN")))


def load_asof_rows(dates):
    con = ro(MAIN_DB)
    ph = ",".join("?" * len(dates))
    df = pd.read_sql_query(
        "SELECT date, code, close, ep, bp FROM stock_factors_fundamental "
        "WHERE date IN (%s)" % ph, con, params=list(dates))
    con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def load_pit_table(table):
    con = ro(PIT_DB)
    df = pd.read_sql_query(
        "SELECT code, report_date, pit_avail_date, eps, bps FROM %s" % table, con)
    con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


# ── 截面构建 ─────────────────────────────────────────────────
def pit_snap_at(pit, T):
    sub = pit[pit["pit_avail_date"] <= T]
    if sub.empty:
        return pd.DataFrame()
    sub = sub.sort_values(["code", "report_date"]).groupby("code", as_index=False).tail(1)
    rd = pd.to_datetime(sub["report_date"].astype(str), format="%Y%m%d")
    sub = sub[(pd.Timestamp(str(T)) - rd).dt.days <= 400]
    return sub.set_index("code")


def build_cross(mode, T, pit, raw_close_T, asof_row_T):
    """返回 index=code 的 [ep, bp] 截面"""
    if mode.startswith("pit"):
        rc = raw_close_T.set_index("code")["close"]
        s = pit_snap_at(pit, T)
        if s.empty:
            return None
        cs = pd.DataFrame({
            "ep": s["eps"],
            "bp": rc.reindex(s.index) / s["bps"]})
    else:
        if asof_row_T is None or asof_row_T.empty:
            return None
        a = asof_row_T.set_index("code")
        cs = pd.DataFrame({"ep": a["ep"], "bp": a["bp"]})
    return cs.dropna(how="all")


def pick_top(cs, ind_map, price_avail, domain, close_T):
    g = cs[(cs["ep"] > 0) & (cs["bp"] > 0)].copy()
    if g.empty:
        return []
    if domain == "le30":
        cl = close_T.reindex(g.index)
        g = g[cl <= MAX_PRICE]
        if g.empty:
            return []
    g["combo"] = (g["ep"].rank(pct=True) + g["bp"].rank(pct=True)) / 2
    g = g.sort_values("combo", ascending=False)
    g = g[g.index.isin(price_avail)]
    picks, used = [], {}
    for code in g.index[:CAND_POOL]:
        if len(picks) >= TOPN:
            break
        ind = ind_map.get(code, "UNKNOWN")
        if used.get(ind, 0) >= MAX_PER_IND:
            continue
        used[ind] = used.get(ind, 0) + 1
        picks.append(code)
    return picks


def vector_run(calendar, close_piv, signals):
    dates = list(calendar)
    cur, prev = {}, None
    rets, turns = [], []
    reb = set(signals)
    empty = pd.Series(dtype=np.float64)
    for d in dates:
        pt = close_piv.loc[d] if d in close_piv.index else empty
        pr = 0.0
        if cur and prev is not None:
            valid = {}
            for c, w in cur.items():
                if c in pt.index and c in prev.index and prev[c] > 0 and pt[c] > 0:
                    pr += w * (pt[c] / prev[c] - 1)
                    valid[c] = w
            if valid:
                drift = {c: w * (pt[c] / prev[c]) for c, w in valid.items()}
                tot = sum(drift.values())
                if tot > 0:
                    orig = sum(cur.values())
                    cur = {c: v / tot * orig for c, v in drift.items()}
        if d in reb:
            new = signals[d]
            nh = {c: 1.0 / TOPN for c in new}
            old = set(cur)
            bw = sum(nh[c] for c in set(nh) - old)
            sw = sum(cur[c] for c in old - set(nh))
            turn = (bw + sw) / 2
            pr -= turn * COST
            turns.append(turn)
            cur = nh
        rets.append(pr)
        prev = pt
    r = pd.Series(rets, index=dates)
    navs = (1 + r).cumprod()
    vol = r.std() * np.sqrt(252)
    yr = (1 + r).groupby(r.index.astype(str).str[:4]).prod() - 1
    return {
        "ann": round(float(navs.iloc[-1] ** (252 / len(r)) - 1), 4),
        "sharpe": round(float(r.mean() * 252 / vol), 3) if vol > 0 else 0.0,
        "mdd": round(float((navs / navs.cummax() - 1).min()), 4),
        "avg_month_turnover": round(float(np.mean(turns)), 4) if turns else 0,
        "n_months": len(turns),
        "n_days": len(r),
        "final_nav": round(float(navs.iloc[-1]), 4),
        "yearly": {k: round(float(v), 4) for k, v in yr.items()},
    }


def shift_next_bar(calendar, signals):
    """next_close执行: 信号日T的清单改在下一交易日成交"""
    cal = list(calendar)
    nxt = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    out = {}
    for T, picks in signals.items():
        if T in nxt:
            out[nxt[T]] = picks
    return out


# ── 主流程 ─────────────────────────────────────────────────
def main():
    t0 = time.time()
    results = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
               "window": "%d~%d" % (BT_START, BT_END), "cost": COST}

    pit_v2 = load_pit_table("stock_factors_fundamental_pit_v2")
    pit_v1 = load_pit_table("stock_factors_fundamental_pit")
    log("PIT层: v2 %d行 / v1 %d行" % (len(pit_v2), len(pit_v1)))
    st, fin, ind_map = load_exclusions()
    excl = st | fin
    log("剔除: ST/退 %d + 金融 %d = %d" % (len(st), len(fin), len(excl)))

    cal = qfq_calendar(BT_START, BT_END)
    reb = month_first(cal)
    log("交易日 %d, 月首调仓 %d 月" % (len(cal), len(reb)))
    raw_close = load_asof_rows(reb)          # date, code, close, ep, bp
    asof_snap = raw_close
    rc_idx = {d: g for d, g in raw_close.groupby("date")}
    as_idx = {d: g for d, g in asof_snap.groupby("date")}

    all_codes = sorted(set(raw_close["code"]) | set(pit_v2["code"]) | set(pit_v1["code"]))
    log("加载qfq日频close (%d只)..." % len(all_codes))
    close_piv = load_daily_qfq_close(all_codes, str(BT_START), str(BT_END))
    log("日频矩阵 %d日 × %d股" % (close_piv.shape[0], close_piv.shape[1]))

    combo, sigs_store = {}, {}
    for domain in ("le30", "all"):
        for mode, pit in (("pit_v2", pit_v2), ("pit_v1", pit_v1), ("asof", None)):
            sig = {}
            for T in reb:
                if T not in close_piv.index:
                    continue
                price_avail = set(close_piv.loc[T].dropna().index)
                g_rc = rc_idx.get(T)
                if g_rc is None:
                    continue
                cs = build_cross(mode, T, pit, g_rc, as_idx.get(T))
                if cs is None:
                    sig[T] = []
                    continue
                cs = cs[~cs.index.isin(excl)]
                cl = g_rc.set_index("code")["close"]
                sig[T] = pick_top(cs, ind_map, price_avail, domain, cl)
            sigs_store[(domain, mode)] = sig
            for exec_mode in ("same_close", "next_close"):
                s = sig if exec_mode == "same_close" else shift_next_bar(cal, sig)
                res = vector_run(cal, close_piv, s)
                combo["%s_%s_%s" % (domain, mode, exec_mode)] = res
            r = combo["%s_%s_next_close" % (domain, mode)]
            log("  %-5s %-6s next_close: 年化%+.1f%% Sharpe%+.2f MDD%.1f%% 换手%.1f%% 月%d"
                % (domain, mode, r["ann"] * 100, r["sharpe"], r["mdd"] * 100,
                   r["avg_month_turnover"] * 100, r["n_months"]))
    results["combo"] = combo

    # 月度Top20重合度(v2 vs asof / v2 vs v1, le30域)
    ov = {}
    for pair in (("pit_v2", "asof"), ("pit_v2", "pit_v1"), ("pit_v1", "asof")):
        a, b = sigs_store[("le30", pair[0])], sigs_store[("le30", pair[1])]
        vals = []
        for T in a:
            if T in b and a[T] and b[T]:
                vals.append(len(set(a[T]) & set(b[T])))
        ov["%s_vs_%s" % pair] = {
            "mean_overlap": round(float(np.mean(vals)), 2) if vals else None,
            "n_months": len(vals)}
        log("重合度 le30 %s vs %s: 月均 %.1f/20 (%d月)" % (
            pair[0], pair[1], ov["%s_vs_%s" % pair]["mean_overlap"] or 0, len(vals)))
    results["overlap"] = ov
    results["signals_le30_pit_v2_last"] = sigs_store[("le30", "pit_v2")][
        sorted(sigs_store[("le30", "pit_v2")])[-1]]

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    log("完成: %s  耗时%.0fs" % (OUT_JSON, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
