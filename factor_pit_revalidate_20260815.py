# ⚠️ 2026-08-22 bps/sps 修复警示: 本脚本 bps 消费基于旧口径(close/bps, 且当时 factor_pit.bps=ref_close/bp 失真~50倍)。
# factor_pit.db 已修复(bps=bp×ref_close), 如需重跑本脚本请将 close/bps 改为 bps/close(仅历史审计, 非生产链路)。
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_pit_revalidate_20260815.py — 四因子(bp/ep/droe/roa)严格PIT重验 + bp+ep组合复验

数据层:
  - D:/zcodesj/factor_pit.db.stock_factors_fundamental_pit_v2 (PIT层, v2口径; 2026-08-26 PIT v1残留清偿后
    v1 表已更名 _v1_deprecated, 本模块 load_pit_full 2026-09-02 迁 v2, 列名 code/report_date/pit_avail_date/eps/bps/droe/roa 两代同构)
    PIT轨: get_pit_factor 语义 = pit_avail_date<=T 的最新一期报告(法定披露截止日, 保守无前视)
    ASOF轨(对照): 原表 date=T 行的 ep/bp/close (期末as-of, 前视口径);
                  droe/roa 对照 = report_date<=T 的最新期(期末as-of)
  - 价格: dragon_quant_qfq.db.stock_daily_meta_qfq (qfq前复权, 组合收益唯一价格源;
    hsj有指数串码污染禁止用于收益——IC截面排序不受影响但统一用qfq最稳)
  - 原始close(因子重建/域过滤): 主库 stock_factors_fundamental.date=T 行(生成器同源自洽)

Part A  四因子月度RankIC (2019-01~2026-08, 月首调仓, 次月首收益, 非ST非金融):
         bp=close_T/bps, ep=EPS(生产口径), droe, roa —— PIT vs ASOF 并排
Part B  bp+ep月度Top20等权组合 (生产结构: ep>0&bp>0→等权pct-rank→head60→行业≤4→Top20,
         M-FIRST调仓, qfq日频收益, 单边0.15%成本), 全域与≤30元域两轨,
         2018-01-02~2026-08-10 与 BPEP审计完全同窗对照
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
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
OUT_JSON = Path(r"D:\zcodesj\reports\_factor_pit_revalidate_20260815.json")

FIN_INDUSTRIES = ("银行", "证券", "保险", "多元金融", "信托")
MIN_CROSS = 50
MAX_STALE_DAYS = 400
STOCK_PREFIX = ("00", "30", "60", "43", "68", "82", "83", "87", "92")
GHOST_DATES = {20181231, 20260627}
COST = 0.0015
TOPN, CAND_POOL, MAX_PER_IND = 20, 60, 4

IC_START, IC_END = 20190101, 20260814
BT_START, BT_END = 20180102, 20260810   # 与BPEP审计T6完全同窗


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


# ════════════════════════════════ 数据加载 ════════════════════════════════

def qfq_calendar(s, e):
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    ds = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date BETWEEN ? AND ? "
        "ORDER BY date", (str(s), str(e)))]
    con.close()
    return [int(d) for d in ds if int(d) not in GHOST_DATES]   # qfq.date为TEXT→转int


def month_first(calendar):
    df = pd.DataFrame({"d": calendar})
    df["ym"] = df["d"].astype(str).str[:6]
    return [int(x) for x in df.groupby("ym")["d"].first().tolist()]


def load_monthly_prices_qfq(calendar):
    """每月前5交易日 qfq close → 每股月首价(5日内首个有效价, bfill技巧)"""
    df = pd.DataFrame({"d": calendar})
    df["ym"] = df["d"].astype(str).str[:6]
    keep = sorted(set(df.groupby("ym")["d"].apply(lambda x: list(x.head(5))).sum()))
    like = " OR ".join(f"code LIKE '{p}%'" for p in STOCK_PREFIX)
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    ph = ",".join("?" * len(keep))
    rows = pd.read_sql_query(
        f"SELECT code, date, close FROM stock_daily_meta_qfq "
        f"WHERE ({like}) AND close > 0 AND date IN ({ph})",
        con, params=[str(k) for k in keep])
    con.close()
    rows["date"] = rows["date"].astype(int)
    rows["code"] = rows["code"].astype(str).str.zfill(6)
    piv = rows.pivot_table(index="date", columns="code", values="close", aggfunc="last")
    piv.index = piv.index.astype(int)
    out, idx = [], []
    yms = pd.Series(piv.index, index=piv.index).astype(str).str[:6]
    for ym, g in piv.groupby(yms):
        out.append(g.bfill().iloc[0])       # 每股5日内首个有效价
        idx.append(g.index[0])
    return pd.DataFrame(out, index=pd.DatetimeIndex(
        pd.to_datetime([str(i) for i in idx], format="%Y%m%d")))


def load_daily_qfq_close(codes, s, e):
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
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
    con = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True)
    info = pd.read_sql_query("SELECT code, name, industry, is_st FROM stock_info", con)
    con.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    st = set(info.loc[(info["is_st"].astype(str) == "1")
                      | info["name"].str.contains("ST") | info["name"].str.contains("退"),
                      "code"])
    fin = set(info.loc[info["industry"].fillna("").apply(
        lambda x: any(k in x for k in FIN_INDUSTRIES)), "code"])
    return st, fin, dict(zip(info["code"], info["industry"].fillna("UNKNOWN")))


def load_asof_snap(dates):
    """原表 date∈dates 行(期末as-of对照轨): code,date,close,ep,bp,roe"""
    con = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True)
    ph = ",".join("?" * len(dates))
    df = pd.read_sql_query(
        "SELECT date, code, close, ep, bp, roe FROM stock_factors_fundamental "
        "WHERE date IN (%s)" % ph, con, params=list(dates))
    con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def load_raw_close(dates):
    con = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True)
    ph = ",".join("?" * len(dates))
    df = pd.read_sql_query(
        "SELECT date, code, close FROM stock_factors_fundamental WHERE date IN (%s)" % ph,
        con, params=list(dates))
    con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def load_pit_full():
    con = sqlite3.connect("file:%s?mode=ro" % PIT_DB, uri=True)
    df = pd.read_sql_query(
        "SELECT code, report_date, pit_avail_date, eps, bps, droe, roa "
        "FROM stock_factors_fundamental_pit_v2", con)
    con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


# ════════════════════════════════ 截面构建 ════════════════════════════════

def pit_snap_at(pit, T):
    """pit_avail_date<=T 每股最新报告期 + 陈旧度<=400天"""
    sub = pit[pit["pit_avail_date"] <= T]
    if sub.empty:
        return pd.DataFrame()
    sub = sub.sort_values(["code", "report_date"]).groupby("code", as_index=False).tail(1)
    rd = pd.to_datetime(sub["report_date"].astype(str), format="%Y%m%d")
    sub = sub[(pd.Timestamp(str(T)) - rd).dt.days <= MAX_STALE_DAYS]
    return sub.set_index("code")


def asof_snap_at(pit, T):
    """report_date<=T 每股最新报告期(期末as-of, 原表语义)"""
    sub = pit[pit["report_date"] <= T]
    if sub.empty:
        return pd.DataFrame()
    return sub.sort_values(["code", "report_date"]).groupby("code", as_index=False).tail(1)\
        .set_index("code")


def build_cross(mode, T, pit, raw_close_T, asof_row_T=None):
    """返回该调仓日因子截面 index=code: ep(EPS口径), bp(P/B), droe, roa

    pit轨:  pit_avail_date<=T最新报告期的eps/bps + 当日原始close → bp=close/bps
    asof轨: ep/bp 直接取原表 date=T 行原值(生产现状, 期末as-of含前视);
            droe/roa 取 report_date<=T 最新期(期末as-of)"""
    if mode == "pit":
        rc = raw_close_T.set_index("code")["close"]
        s = pit_snap_at(pit, T)
        if s.empty:
            return None
        cs = pd.DataFrame({
            "ep": s["eps"],
            "bp": rc.reindex(s.index) / s["bps"],
            "droe": s["droe"], "roa": s["roa"]})
    else:
        if asof_row_T is None or asof_row_T.empty:
            return None
        a = asof_row_T.set_index("code")
        s = asof_snap_at(pit, T)
        cs = pd.DataFrame({
            "ep": a["ep"], "bp": a["bp"],
            "droe": s["droe"].reindex(a.index) if not s.empty else np.nan,
            "roa": s["roa"].reindex(a.index) if not s.empty else np.nan})
    return cs.dropna(how="all")


# ════════════════════════════════ Part A: 四因子 RankIC ════════════════════════════════

def run_ic(mp, pit, excl, reb_dates, raw_close, asof_snap):
    fwd = mp.shift(-1) / mp - 1
    rows = []
    rc_idx = {d: g for d, g in raw_close.groupby("date")}
    as_idx = {d: g for d, g in asof_snap.groupby("date")}
    for T in reb_dates:
        r = fwd.loc[pd.Timestamp(str(T))].dropna()
        if len(r) < MIN_CROSS:
            continue
        g_rc = rc_idx.get(T)
        for mode in ("pit", "asof"):
            cs = build_cross(mode, T, pit, g_rc, as_idx.get(T))
            if cs is None:
                continue
            cs = cs[~cs.index.isin(excl)]
            for f in ("ep", "bp", "droe", "roa"):
                fv = cs[f].replace([np.inf, -np.inf], np.nan).dropna()
                common = fv.index.intersection(r.index)
                if len(common) < MIN_CROSS:
                    continue
                ic, _ = spearmanr(fv[common], r[common])
                if not np.isnan(ic):
                    rows.append({"date": str(T), "mode": mode, "factor": f,
                                 "rank_ic": float(ic), "n": int(len(common))})
    return pd.DataFrame(rows)


def ic_summary(ic_df):
    out = {}
    for (mode, f), g in ic_df.groupby(["mode", "factor"]):
        s = g.set_index("date")["rank_ic"].dropna()
        if len(s) < 12:
            continue
        yr = s.groupby(s.index.str[:4]).mean()
        out[f"{f}_{mode}"] = {
            "ic_mean": round(float(s.mean()), 4),
            "ic_std": round(float(s.std()), 4),
            "icir": round(float(s.mean() / s.std()), 3) if s.std() > 0 else 0.0,
            "ic_win": round(float((s > 0).mean()), 3),
            "t_stat": round(float(s.mean() / s.std() * np.sqrt(len(s))), 2),
            "n": int(len(s)),
            "yearly_ic": {k: round(float(v), 4) for k, v in yr.items()},
        }
    return out


# ════════════════════════════════ Part B: bp+ep Top20 组合 ════════════════════════════════

def pick_top(cs, ind_map, price_avail, domain, close_T):
    """生产 _rebalance 口径: ep>0&bp>0→等权pct-rank→可用价过滤→head60→行业≤4→Top20"""
    g = cs[(cs["ep"] > 0) & (cs["bp"] > 0)].copy()
    if g.empty:
        return []
    if domain == "le30":
        cl = close_T.reindex(g.index)
        g = g[cl <= 30.0]
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
    """日频向量引擎(审计T6同构): qfq收益, 调仓日按双边平均换手计0.15%单边成本"""
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
            pr -= turn * COST                    # 调仓日净收益含交易成本
            turns.append(turn)
            cur = nh
        rets.append(pr)
        prev = pt
    r = pd.Series(rets, index=dates)
    navs2 = (1 + r).cumprod()
    vol = r.std() * np.sqrt(252)
    yr = (1 + r).groupby(r.index.astype(str).str[:4]).prod() - 1
    return {
        "ann": round(float(navs2.iloc[-1] ** (252 / len(r)) - 1), 4),
        "sharpe": round(float(r.mean() * 252 / vol), 3) if vol > 0 else 0.0,
        "mdd": round(float((navs2 / navs2.cummax() - 1).min()), 4),
        "avg_month_turnover": round(float(np.mean(turns)), 4) if turns else 0,
        "n_months": len(turns),
        "n_days": len(r),
        "final_nav": round(float(navs2.iloc[-1]), 4),
        "yearly": {k: round(float(v), 4) for k, v in yr.items()},
    }


def run_combo(calendar, close_piv, pit, ind_map, reb_dates, raw_close, asof_snap,
              domain, mode):
    sig = {}
    rc_idx = {d: g for d, g in raw_close.groupby("date")}
    as_idx = {d: g for d, g in asof_snap.groupby("date")}
    for T in reb_dates:
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
        cl = g_rc.set_index("code")["close"]
        sig[T] = pick_top(cs, ind_map, price_avail, domain, cl)
    return vector_run(calendar, close_piv, sig), sig


# ════════════════════════════════ 主流程 ════════════════════════════════

def main():
    t0 = time.time()
    results = {"generated": time.strftime("%Y-%m-%d %H:%M:%S")}

    log("[0] 加载PIT层...")
    pit = load_pit_full()
    log("      %d 行 / %d 只" % (len(pit), pit["code"].nunique()))
    st, fin, ind_map = load_exclusions()
    log("[0] 剔除集: ST/退 %d, 金融 %d" % (len(st), len(fin)))

    # ── Part A: 四因子IC 2019-2026 ──
    log("[A] 四因子RankIC (2019-01~2026-08, 月首调仓, qfq次月收益, 非ST非金融)")
    cal_a = qfq_calendar(IC_START, IC_END)
    reb_a = month_first(cal_a)
    mp = load_monthly_prices_qfq(cal_a)
    log("      月度价格矩阵 %d月 × %d股" % (mp.shape[0], mp.shape[1]))
    raw_close_a = load_raw_close(reb_a)
    asof_a = load_asof_snap(reb_a)
    ic_df = run_ic(mp, pit, st | fin, reb_a, raw_close_a, asof_a)
    results["ic"] = ic_summary(ic_df)
    for k in sorted(results["ic"]):
        v = results["ic"][k]
        log("      %-12s IC%+.4f ICIR%+.3f t%+.2f 胜率%.0f%% n=%d"
            % (k, v["ic_mean"], v["icir"], v["t_stat"], v["ic_win"] * 100, v["n"]))

    # ── Part B: bp+ep Top20 组合, 与BPEP审计同窗 ──
    log("[B] bp+ep月度Top20 (生产结构, M-FIRST, qfq, 0.15%%单边) 2018-01-02~2026-08-10")
    cal_b = qfq_calendar(BT_START, BT_END)
    reb_b = month_first(cal_b)
    raw_close_b = load_raw_close(reb_b)
    asof_b = load_asof_snap(reb_b)
    all_codes = sorted(set(raw_close_b["code"]) | set(pit["code"]))
    log("      加载qfq日频close (%d只)..." % len(all_codes))
    close_piv = load_daily_qfq_close(all_codes, BT_START, BT_END)
    log("      日频矩阵 %d日 × %d股" % (close_piv.shape[0], close_piv.shape[1]))

    combo = {}
    for domain in ("all", "le30"):
        for mode in ("pit", "asof"):
            res, sig = run_combo(cal_b, close_piv, pit, ind_map, reb_b,
                                 raw_close_b, asof_b, domain, mode)
            combo["%s_%s" % (domain, mode)] = res
            log("      %-10s %-4s 年化%+.1f%% Sharpe%+.2f MDD%.1f%% 换手%.1f%% 月数%d"
                % (domain, mode, res["ann"] * 100, res["sharpe"], res["mdd"] * 100,
                   res["avg_month_turnover"] * 100, res["n_months"]))
    results["combo"] = combo

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    log("完成, 结果: %s  耗时%.0fs" % (OUT_JSON, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
