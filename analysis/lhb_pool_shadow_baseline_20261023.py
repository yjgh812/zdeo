# -*- coding: utf-8 -*-
"""lhb_pool_shadow_baseline_20261023.py — 龙虎榜游资净买入样本池 影子基线（呈请事项一·影子-only 单元1）

立项依据: analysis/呈请_画像接线立项与qfq补洞_20261023.md 事项一, 主人「继续」令按推荐
选项（批准: 影子-only 起步）启动。本件=历史回放研究件, 不是每日生产管线。

数据面（全部本地只读）:
  席位=workbuddyspace/databases/fundamental_data.db lhb_seat_detail（2016+, 179.6万行, 至20261022）
  行业=fundamental_data.db industry_map | 量比=fundamental_data.db daily_basic.volume_ratio
  价格=qfq 库 stock_daily_meta_qfq（open/close/amount/change_pct, 2016+）

规则真源: 判定函数全部复用 analysis/portrait_discipline_core_20261023.py
  （classify_seat / hot_money_netbuy_pool / tradability_flag / mde_two_prop /
   detectable / loo_stable / extrapolation_gate），本件零规则复制。

声明式代理（影子件如实标注, 升级待后续单元）:
  ① 缩量分位 = qfq 20日量分位（rolling rank pct, 低=缩量; v2 起, 摆脱 daily_basic 量比
     2020 覆盖限制, 恢复 2016+ 全窗）;
  ② 涨停状态 = qfq change_pct ≥ 9.7%（主板/GEM旧制）或 ≥19.5%（创68注册制后）; ST 5% 板不单独建模(保守);
  ③ 可成交代理 = 次日成交额×0.35（开盘30分钟成交额占比经验值）, 目标买入金额固定 50 万;
  ④ 双向席位判定在 (date, code, seat) 同股同日粒度（套件丁例口径）。

边界: 纯影子·零交易权·零生产接线; 消费方=复盘对照研究。产出 reports/LHB_POOL_SHADOW_BASELINE_20261023.{json,md}
"""
import json
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj\analysis")
from portrait_discipline_core_20261023 import (  # noqa: E402  规则单一真源
    classify_seat, hot_money_netbuy_pool, tradability_flag,
    mde_two_prop, detectable, loo_stable, extrapolation_gate)

FUND = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT_JSON = r"D:\zcodesj\reports\LHB_POOL_SHADOW_BASELINE_20261023.json"
OUT_MD = r"D:\zcodesj\reports\LHB_POOL_SHADOW_BASELINE_20261023.md"
START = "20160101"
TARGET_AMT = 50e4          # P4 目标买入金额（套件口径 50 万）
EXEC_PROXY = 0.35          # 可成交代理: 次日成交额×0.35 ≈ 开盘30分钟成交额


def limit_threshold(code, date):
    """涨停判定阈值: 创业板30(2020-08-24注册制后)/科创板68 → 19.5%, 其余 9.7%。"""
    if (code.startswith("30") and date >= "20200824") or code.startswith("68"):
        return 19.5
    return 9.7


def build_paired():
    """P1池→P2配对 全管线（影子规则真源；供本件 main 与分钟可成交额验证件复用）。"""
    # ---------- 1. 席位 → 游资净买入池事件（P1 规则经 core 函数） ----------
    db = sqlite_connect(FUND)
    seats = pd.read_sql(
        "select trade_date, security_code, side, operate_dept_name, buy, sell, net "
        "from lhb_seat_detail where trade_date>='2016-01-01'", db)
    ind = pd.read_sql("select ts_code, industry_name from industry_map", db)
    db.close()
    seats["date"] = seats["trade_date"].str.replace("-", "", regex=False)
    seats["code"] = seats["security_code"].astype(str).str.zfill(6)
    seats = seats[seats["code"].str[:2].isin(("60", "00", "30", "68"))]  # 沪深股票(剔北交)
    cls_cache = {n: classify_seat(n) for n in seats["operate_dept_name"].unique()}
    seats["cls"] = seats["operate_dept_name"].map(cls_cache)
    n_total_days = seats["date"].nunique()

    events = []
    two_way_total = 0
    for (d, c), gdf in seats[seats["cls"] == "trader"].groupby(["date", "code"], sort=False):
        recs = [(r.operate_dept_name, "B" if r.side == "BUY" else "S",
                 float(r.buy) if r.buy == r.buy else 0.0,
                 float(r.sell) if r.sell == r.sell else 0.0)
                for r in gdf.itertuples(index=False)]
        pool, flagged = hot_money_netbuy_pool(recs)
        two_way_total += len(flagged)
        if pool:
            primary, ev_net = max(pool, key=lambda p: p[1])
            events.append((d, c, ev_net, primary, len(pool)))
    ev = pd.DataFrame(events, columns=["date", "code", "net_buy", "primary_trader", "n_pool_seats"])
    print(f"[1] P1池事件: 榜单股日(游资席)={len(ev):,} 双向席位剔除={two_way_total:,} "
          f"交易日={n_total_days}")

    # ---------- 2. 价格/量比/行业 日度面板 + 前瞻收益 ----------
    cq = sqlite_connect(QFQ)
    px = pd.read_sql(
        "select code, cast(date as text) as date, open, close, amount, change_pct, volume "
        "from stock_daily_meta_qfq where date>='20160101'", cq)
    cq.close()
    px = px.sort_values(["code", "date"]).reset_index(drop=True)
    # 缩量分位(v2): 20日滚动量分位(0..1, 低=缩量), qfq全历史可算, 恢复2016+全窗
    px["vol_pctile"] = px.groupby("code")["volume"].transform(
        lambda s: s.rolling(20, min_periods=15).rank(pct=True))
    g = px.groupby("code")
    px["open_next"] = g["open"].shift(-1)
    px["date_next"] = g["date"].shift(-1)
    px["amount_next"] = g["amount"].shift(-1)
    px["ret5"] = g["close"].shift(-5) / px["open_next"] - 1.0
    px["ret10"] = g["close"].shift(-10) / px["open_next"] - 1.0
    px["has_entry"] = px["open_next"].notna() & px["ret5"].notna()

    ind = ind.dropna(subset=["industry_name"]).drop_duplicates("ts_code")
    ind["code"] = ind["ts_code"].astype(str).str.split(".").str[0].str.zfill(6)
    px = px.merge(ind[["code", "industry_name"]], on="code", how="left")
    thr = [limit_threshold(c, d) for c, d in zip(px["code"], px["date"])]
    px["limit_up"] = px["change_pct"] >= np.array(thr)
    day_panel = {d: gdf for d, gdf in px.groupby("date")}

    # ---------- 3. P2 配对对照（同日+同行业+同涨停+无游资, 量比最近邻） ----------
    pool_days = ev.groupby("date")["code"].apply(set).to_dict()
    paired = []
    n_no_ctrl = 0
    for r in ev.itertuples(index=False):
        panel = day_panel.get(r.date)
        if panel is None:
            continue
        row = panel[panel["code"] == r.code]
        if row.empty:
            continue
        row = row.iloc[0]
        if not (row["has_entry"] and row["industry_name"] == row["industry_name"]
                and pd.notna(row["vol_pctile"])):
            n_no_ctrl += 1
            continue
        cands = panel[(panel["industry_name"] == row["industry_name"])
                      & (panel["limit_up"] == row["limit_up"])
                      & panel["has_entry"] & panel["vol_pctile"].notna()
                      & ~panel["code"].isin(pool_days.get(r.date, ()))]
        cands = cands[cands["code"] != r.code]
        if cands.empty:
            n_no_ctrl += 1
            continue
        ctl = cands.iloc[(cands["vol_pctile"] - row["vol_pctile"]).abs().argmin()]
        paired.append(dict(date=r.date, code=r.code, trader=r.primary_trader,
                           net_buy=r.net_buy, ev5=row["ret5"], ev10=row["ret10"],
                           ctl5=ctl["ret5"], ctl10=ctl["ret10"],
                           ex5=row["ret5"] - ctl["ret5"], ex10=row["ret10"] - ctl["ret10"],
                           exec_amt=0.0 if pd.isna(row["amount_next"]) else float(row["amount_next"]),
                           date_next=None if pd.isna(row["date_next"]) else str(row["date_next"])))
    P = pd.DataFrame(paired)
    print(f"[2] P2配对: 事件={len(ev):,} 配对成功={len(P):,} 无对照剔除={n_no_ctrl:,}")
    return ev, P


def main():
    ev, P = build_paired()

    # ---------- 4. P4 可成交过滤（50万/次日成交额×0.35 > 30% → 剔除） ----------
    flags = P.apply(lambda r: tradability_flag(TARGET_AMT, r["exec_amt"] * EXEC_PROXY), axis=1)
    P["realizable"] = [f[1] for f in flags]
    n_p4_drop = int((~P["realizable"]).sum())
    M = P[P["realizable"]].copy()
    print(f"[3] P4可成交: 不可实现剔除={n_p4_drop:,}（主统计样本={len(M):,}）")
    if M.empty:
        res = {"meta": dict(shadow="only", events=int(len(ev)), paired=int(len(P)),
                            p4_dropped=n_p4_drop, main_n=0,
                            error="主样本为空, 检查行业/量比合并覆盖率"),
               "gates": {}}
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("[ABORT] 主样本为空, 影子报告不产出结论")
        return

    # ---------- 5. 四道闸测量 ----------
    res = {"meta": dict(shadow="only", events=int(len(ev)), paired=int(len(P)),
                        p4_dropped=n_p4_drop, main_n=int(len(M)),
                        two_way_flagged=int(two_way_total),
                        proxies="缩量=qfq20日量分位最近邻/涨停=change_pct阈值/可成交=次日额×0.35/双向=同股同日席粒级")}
    out = {}
    for h in ("5", "10"):
        ex = M[f"ex{h}"] * 100.0
        win_pool = float((M[f"ev{h}"] > 0).mean()) * 100
        win_ctl = float((M[f"ctl{h}"] > 0).mean()) * 100
        delta = win_pool - win_ctl
        mde = mde_two_prop(len(M), win_ctl / 100.0)
        out[f"fwd{h}"] = {
            "n": int(len(M)),
            "pool_win_pct": round(win_pool, 2), "ctl_win_pct": round(win_ctl, 2),
            "win_delta_pp": round(delta, 2),
            "mean_excess_pp": round(float(ex.mean()), 2),
            "median_excess_pp": round(float(ex.median()), 2),
            "P3_MDE_pp_at_n": round(mde * 100, 2),
            "P3_detectable": bool(detectable(len(M), win_ctl / 100.0, abs(delta) / 100.0))
            if delta != 0 else False,
        }
    # P5 LOO（5日窗, 事件数≥5的主力游资）
    ex5 = M["ex5"] * 100.0
    base5 = float(ex5.mean())
    cnt = M["trader"].value_counts()
    loo_names = [t for t in cnt[cnt >= 5].index][:20]
    loo_vals = [float(ex5[M["trader"] != t].mean()) for t in loo_names]
    stable, weakest = loo_stable(base5, loo_vals)
    out["P5_LOO"] = {"baseline_pp": round(base5, 2), "n_traders_tested": len(loo_names),
                     "weakest_pp": round(weakest, 2), "stable": bool(stable),
                     "note": "留存阈值0.5含负值格镜像规则(discipline_core缺省), 待校准"}
    # P6 分年（5日窗年度均值超额 = 配对设计下的增量, 2026为年内partial）
    yr = M.assign(year=M["date"].str[:4]).groupby("year")["ex5"].mean() * 100.0
    out["P6_yearly_ex5_pp"] = {k: round(float(v), 2) for k, v in yr.items()}
    if "2025" in yr.index and "2026" in yr.index:
        ok, why = extrapolation_gate([float(yr["2025"]), float(yr["2026"])])
        out["P6_gate"] = {"allow": bool(ok), "reason": why}
    else:
        out["P6_gate"] = {"allow": None, "reason": "验证年不全(影子基线, 非结论)"}
    res["gates"] = out

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=float)

    # ---------- 6. 影子报告 ----------
    L = ["# 龙虎榜游资净买入样本池 · 影子基线 v2（呈请事项一单元2 · 2026-10-23）", "",
         "> ⚠️ 影子-only 零交易权：复盘对照研究件，不构成信号，不接任何生产链。",
         "> 规则真源=portrait_discipline_core（P1-P6 判定函数）；代理口径见 meta.proxies。", "",
         f"- P1 池事件 {res['meta']['events']:,}（双向席位剔除 {res['meta']['two_way_flagged']:,}）→ "
         f"P2 配对 {res['meta']['paired']:,} → P4 剔除 {res['meta']['p4_dropped']:,} → 主样本 {res['meta']['main_n']:,}", ""]
    for h in ("5", "10"):
        d = out[f"fwd{h}"]
        L.append(f"## {h}日窗（T+1开盘→T+{h}收盘）")
        L.append(f"- 池胜率 {d['pool_win_pct']}% vs 对照 {d['ctl_win_pct']}% → 配对胜率差 {d['win_delta_pp']}pp | "
                 f"均值超额 {d['mean_excess_pp']}pp / 中位 {d['median_excess_pp']}pp")
        L.append(f"- P3 功效: n={d['n']:,} → MDE={d['P3_MDE_pp_at_n']}pp；"
                 f"当前差 {d['win_delta_pp']}pp {'≥' if d['P3_detectable'] else '<'} MDE → "
                 f"{'可检' if d['P3_detectable'] else '不可检(不得下显著结论)'}")
        L.append("")
    L.append(f"## P5 leave-one-trader-out（5日窗, 主力游资{out['P5_LOO']['n_traders_tested']}名）")
    L.append(f"- 全体均值 {out['P5_LOO']['baseline_pp']}pp → 剔除后最低 {out['P5_LOO']['weakest_pp']}pp → "
             f"{'稳定' if out['P5_LOO']['stable'] else '不稳定: 个体效应嫌疑, 不应接线'}")
    L.append("")
    L.append("## P6 分年外推（5日窗均值超额pp）")
    L.append("- " + ", ".join(f"{k}={v:+.2f}" for k, v in out["P6_yearly_ex5_pp"].items()))
    L.append(f"- 外推闸: {out['P6_gate']['reason']}")
    L.append("")
    L.append("*影子基线非结论；代理口径升级（真实分钟可成交额/申万精确涨停含ST）列入下一单元。*")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(open(OUT_MD, encoding="utf-8").read())


def sqlite_connect(uri):
    import sqlite3
    return sqlite3.connect(uri, uri=True)


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
