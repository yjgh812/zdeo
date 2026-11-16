# -*- coding: utf-8 -*-
"""PREREG_INDUSTRY_NEUTRAL_20261018 实验脚本 (ACTIVE, 指纹 9cb6b66caf7a48ee)
====================================================================
分组器实验: 全市场 bp+ep Top15 基线(V0) vs 行业内排名(V1×k) vs 行业中性化(V2×k)
审计系列: TULONG-SELECTION-CHAIN 五报告 → 呈请A → 主人两次「继续」令激活

口径合同(与既有 bt_bp_ep_realistic_backtest 对齐的要素, 向量法):
  因子  = dragon_quant.db stock_factors_fundamental, PIT 实现=调仓日只取
          pit_avail_date<=调仓日 的每股最新快照(该表自带 PIT 字段, 实测在档)
  合成  = 等权 pct-rank(ep)+pct-rank(bp) (生产口径 dir+1)
  调仓  = 因子表月末日(既有 bt 口径), 次日开盘买入, 下期调仓日收盘卖出
  宇宙  = 调仓日 close>0 且 <=30(生产 point-in-time 价阈) + 映射在册
          + 在窗K线>=60根 + ST 剔除(stock_daily_meta.name 可得时生效)
  成本  = 单边 0.004(既有向量基线 COST_VECTOR), 换仓双向计
  窗口  = 20220101~20261016; 双窗 2022-2023 发现 / 2024-2026.10 验证
  持仓  = Top15 等权, 无止损无 MDD 保护(三臂同构, 差值口径)
预钉: 主判据=V1@k=10%(文档方式三原始形态); V2@k=10% 次判据(仅V2过=降档);
      PBO 六格 = (V1,V2)×k∈{5,10,15}% 16块 CSCV, 七臂单次遍历全出
边界: 零交易权; 不接 round10 生产协议; 产出仅研究报告
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

PREREG = r"D:\zcodesj\data\prereg_industry_neutral_20261018.json"
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
FACTOR_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"
META_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro"
MAP_DB = "file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro"
RESULT = r"D:\zcodesj\data\industry_neutral_exp_result.json"
START, END = 20220101, 20261016
TOPN = 15
PRICE_MAX = 30.0
MIN_BARS = 60
COST = 0.004
K_PCTS = (0.05, 0.10, 0.15)
ARMS = ["V0"] + [("V1", k) for k in K_PCTS] + [("V2", k) for k in K_PCTS]
MAIN_V1, MAIN_V2 = ("V1", 0.10), ("V2", 0.10)


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def load_all():
    q = sqlite3.connect(QFQ_DB, uri=True)
    px = pd.read_sql_query(
        "SELECT code,date,open,close FROM stock_daily_meta_qfq WHERE date BETWEEN ? AND ?",
        q, params=(START, END))
    q.close()
    px["code"] = px["code"].astype(str).str.zfill(6)
    px["date"] = px["date"].astype(int)  # qfq date 落库为 TEXT, 因子表为 INT, 统一整数域

    f = sqlite3.connect(FACTOR_DB, uri=True)
    fac = pd.read_sql_query(
        "SELECT date,code,ep,bp,pit_avail_date FROM stock_factors_fundamental "
        "WHERE date BETWEEN ? AND ?", f, params=(START, END))
    f.close()
    fac["code"] = fac["code"].astype(str).str.zfill(6)
    fac = fac.sort_values(["code", "date"], kind="stable").reset_index(drop=True)  # 全局预排序, 逐期过滤保序免重排

    m = sqlite3.connect(META_DB, uri=True)
    cols = [c[1] for c in m.execute("PRAGMA table_info(stock_daily_meta)").fetchall()]
    name_map = {}
    if "name" in cols:
        rows = m.execute("SELECT code,name FROM stock_daily_meta").fetchall()
        name_map = {str(c).zfill(6): n for c, n in rows}
    m.close()
    return px, fac, name_map


def load_map():
    mp = sqlite3.connect(MAP_DB, uri=True)
    members = pd.read_sql_query(
        "SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", mp)
    mp.close()
    return dict(zip(members["code"], members["l1"]))


def run_once(px, fac, ind_map, name_map):
    trade_days = build_trade_dates(fac)
    px = px.sort_values(["code", "date"]).reset_index(drop=True)
    cnt_map = px.groupby("code")["date"].count().to_dict()
    px_idx = {d: g.set_index("code")[["open", "close"]] for d, g in px.groupby("date")}
    st_set = {c for c, n in name_map.items() if isinstance(n, str) and "ST" in n.upper()}
    st_used = len(st_set) > 0

    nav = {a: [1.0] for a in ARMS}
    monthly_ret = {a: [] for a in ARMS}
    n_empty = 0

    usable = [d for d in trade_days if d in px_idx]
    for i in range(len(usable) - 1):
        d, d_next = usable[i], usable[i + 1]
        # PIT 快照: numpy 数组过滤(每期两次标量比较, 快于 pandas 布尔索引); fac 已全局预排序, tail(1)=组内最新 date
        pit_ok = (fac["pit_avail_date"].values <= d) & (fac["date"].values <= d)
        snap = (fac[pit_ok]
                .groupby("code").tail(1)
                .set_index("code")[["ep", "bp"]])
        day_px = px_idx[d]
        day = snap.join(day_px["close"], how="inner").dropna(subset=["ep", "bp", "close"])
        day = day[(day["close"] > 0) & (day["close"] <= PRICE_MAX)]
        day = day[~day.index.isin(st_set)] if st_used else day
        day = day[day.index.map(lambda c: cnt_map.get(c, 0)) >= MIN_BARS]
        day = day.reset_index().rename(columns={"index": "code"})
        day["l1"] = day["code"].map(ind_map)
        day = day[day["l1"].notna()]
        if len(day) < TOPN * 4:
            n_empty += 1
            for a in ARMS:
                nav[a].append(nav[a][-1] * (1 - COST))
                monthly_ret[a].append((d_next, -COST))
            continue
        day["r_ep"] = day["ep"].rank(pct=True)
        day["r_bp"] = day["bp"].rank(pct=True)
        day["score"] = day["r_ep"] + day["r_bp"]
        day["r_ep_in"] = day.groupby("l1")["ep"].rank(pct=True)
        day["r_bp_in"] = day.groupby("l1")["bp"].rank(pct=True)
        day["score_in"] = day["r_ep_in"] + day["r_bp_in"]
        z = day.groupby("l1")[["ep", "bp"]].transform(
            lambda s: (s - s.mean()) / (s.std(ddof=0) if s.std(ddof=0) > 0 else 1.0))
        day["score_z"] = z["ep"] + z["bp"]

        sig = {"V0": set(day.nlargest(TOPN, "score")["code"])}
        for k in K_PCTS:
            # V1: 行业内 rank 前 k%(每行业>=1只) -> 合并池 -> 全市场 Top15
            keep = day[day["score_in"].rank(pct=True) >= 1 - k]
            if len(keep) > TOPN:
                sig[("V1", k)] = set(keep.nlargest(TOPN, "score")["code"])
            else:
                sig[("V1", k)] = set(keep.nlargest(TOPN, "score_in")["code"])
            # V2: 行业内 z 分 -> 合并池前 k% -> 全市场 Top15
            keepz = day[day["score_z"].rank(pct=True) >= 1 - k]
            sig[("V2", k)] = set(keepz.nlargest(TOPN, "score_z")["code"])

        pxn = px_idx[d_next]
        for a in ARMS:
            rr = []
            for c in sig[a]:
                if c in pxn.index:
                    entry, exit_px = pxn.loc[c, "open"], pxn.loc[c, "close"]
                    if entry and entry > 0:
                        rr.append(exit_px / entry - 1)
            mret = (float(np.mean(rr)) - COST) if rr else -COST
            nav[a].append(nav[a][-1] * (1 + mret))
            monthly_ret[a].append((d_next, mret))
    return nav, monthly_ret, st_used, n_empty


def build_trade_dates(fac):
    df = fac[["date"]].drop_duplicates().sort_values("date")
    df["ym"] = df["date"].astype(str).str[:6]
    return [int(x) for x in df.groupby("ym")["date"].max().tolist()]


def perf(rets, lo, hi):
    rr = [r for d, r in rets if lo <= d <= hi]
    if not rr:
        return None
    nav = [1.0]
    for r in rr:
        nav.append(nav[-1] * (1 + r))
    nav = np.array(nav)
    yrs = len(rr) / 12.0
    ann = nav[-1] ** (1 / yrs) - 1 if nav[-1] > 0 else -1.0
    mdd = float(np.min(nav / np.maximum.accumulate(nav) - 1))
    arr = np.array(rr)
    sharpe = float(arr.mean() / (arr.std(ddof=1) + 1e-12) * np.sqrt(12))
    pos, neg = arr[arr > 0], arr[arr < 0]
    plr = float(pos.mean() / abs(neg.mean())) if len(pos) and len(neg) else (
        float("inf") if len(pos) else 0.0)
    return {"n_months": len(rr), "annual": round(float(ann), 4),
            "mdd": round(mdd, 4), "sharpe": round(sharpe, 2),
            "plr_monthly": round(plr, 2), "win_monthly": round(float((arr > 0).mean()), 3)}


def cscv_pbo(monthly_by_cfg):
    cfgs = list(monthly_by_cfg)
    dates = [d for d, _ in monthly_by_cfg[cfgs[0]]]
    lookup = {c: dict(monthly_by_cfg[c]) for c in cfgs}
    R = np.array([[lookup[c].get(d, 0.0) for c in cfgs] for d in dates])
    n, S = R.shape[0], 16
    nb = n // S
    if nb < 4:
        return None, "子区间不足"
    ranks = []
    for b in range(S):
        oos = np.arange(b * nb, min((b + 1) * nb, n))
        is_ = np.setdiff1d(np.arange(n), oos)
        best = int(np.argmax(R[is_].mean(axis=0)))
        oos_mean = R[oos].mean(axis=0)
        ranks.append(float((oos_mean > oos_mean[best]).sum()) / (len(cfgs) - 1))
    return float(np.mean(np.array(ranks) < 0.5)), None


def main() -> int:
    pr = json.load(open(PREREG, encoding="utf-8"))
    body = json.dumps({"params": pr["params"], "readout": pr["readout"]},
                      ensure_ascii=False, sort_keys=True)
    fp = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    assert fp == pr["fingerprint"], f"prereg 指纹不符: {fp} != {pr['fingerprint']}"
    assert pr["status"] == "ACTIVE", "prereg 非 ACTIVE, fail-closed"
    log(f"prereg 指纹复核 PASS ({fp}), status=ACTIVE")

    px, fac, name_map = load_all()
    ind_map = load_map()
    log(f"数据: qfq {len(px)}行 | 因子 {len(fac)}行 | 映射 {len(ind_map)}只")

    nav1, mr1, st_used, n_empty = run_once(px, fac, ind_map, name_map)
    nav2, mr2, _, _ = run_once(px, fac, ind_map, name_map)
    for a in ARMS:
        assert nav1[a] == nav2[a], f"双跑不一致: {a}"
    log(f"双跑一致断言 PASS | ST过滤={'生效' if st_used else 'name源不可得未生效(如实声明)'}"
        f" | 调仓期数={len(nav1['V0'])-1} | 空池期={n_empty}")

    windows = [("全窗", START, END), ("发现窗2022-23", 20220101, 20231231),
               ("验证窗2024-26", 20240101, END)]
    results = {a: {w: perf(mr1[a], lo, hi) for w, lo, hi in windows} for a in ARMS}

    print("\n===== 七臂全窗对照 =====")
    print(f"{'臂':<14}{'年化':>9}{'MDD':>9}{'夏普':>7}{'月盈亏比':>9}{'月胜率':>8}")
    for a in ARMS:
        r = results[a]["全窗"]
        tag = "V0基线" if a == "V0" else f"{a[0]}_k{int(a[1]*100)}%" + ("(主)" if a == MAIN_V1 else ("(次)" if a == MAIN_V2 else ""))
        print(f"{tag:<14}{r['annual']*100:>8.2f}%{r['mdd']*100:>8.2f}%{r['sharpe']:>7.2f}"
              f"{r['plr_monthly']:>9.2f}{r['win_monthly']*100:>7.1f}%")

    v0 = results["V0"]["全窗"]
    v1, v2 = results[MAIN_V1]["全窗"], results[MAIN_V2]["全窗"]
    g1_v1, g1_v2 = v1["annual"] > v0["annual"] + 0.02, v2["annual"] > v0["annual"] + 0.02
    g2 = (v1["plr_monthly"] >= v0["plr_monthly"] * 0.95
          and v1["mdd"] >= v0["mdd"] * 1.15)
    dw1 = results[MAIN_V1]["发现窗2022-23"]["annual"] - results["V0"]["发现窗2022-23"]["annual"]
    dw2 = results[MAIN_V1]["验证窗2024-26"]["annual"] - results["V0"]["验证窗2024-26"]["annual"]
    g3 = (dw1 > 0) == (dw2 > 0)
    yearly = {}
    for y in (2022, 2023, 2024, 2025, 2026):
        r1 = perf(mr1[MAIN_V1], y * 10000 + 101, y * 10000 + 1231)
        r0 = perf(mr1["V0"], y * 10000 + 101, y * 10000 + 1231)
        if r1 and r0:
            yearly[y] = r1["annual"] > r0["annual"]
    g4 = sum(yearly.values()) >= 3
    d0 = dict(mr1["V0"])
    diff = np.array([r - d0[d] for d, r in mr1[MAIN_V1] if d in d0])
    t = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)) + 1e-12))
    g5 = t > 1.7
    pbo, pbo_note = cscv_pbo({f"{c[0]}_k{int(c[1]*100)}": mr1[c] for c in ARMS[1:]})
    g6 = (pbo is not None) and (pbo <= 0.5)

    print("\n===== 判定 (G1-G6) =====")
    print(f"G1 主门(+2pp): V1={'PASS' if g1_v1 else 'FAIL'}({v1['annual']*100:+.2f}% vs V0 {v0['annual']*100:+.2f}%)"
          f" V2={'PASS' if g1_v2 else 'FAIL'}({v2['annual']*100:+.2f}%)")
    print(f"G2 盈亏比/MDD守门: {'PASS' if g2 else 'FAIL'}")
    print(f"G3 双窗同向(Δ发现{dw1*100:+.2f}pp / Δ验证{dw2*100:+.2f}pp): {'PASS' if g3 else 'FAIL'}")
    print(f"G4 分年≥3/5不反号: {yearly} -> {'PASS' if g4 else 'FAIL'}")
    print(f"G5 差值月收益 t={t:.2f} (单侧阈值1.7): {'PASS' if g5 else 'FAIL'}")
    print(f"G6 PBO={pbo if pbo is not None else 'N/A('+str(pbo_note)+')'} (门≤0.5): {'PASS' if g6 else 'FAIL'}")
    kill = any(results[a]["全窗"]["annual"] < v0["annual"] - 0.03 for a in (MAIN_V1, MAIN_V2))
    if kill:
        verdict = "KILL(触发kill条款)"
    elif g1_v1 and g2 and g3 and g4 and g5:
        verdict = "PASS_CANDIDATE(V1主判据全过)"
    elif g1_v2 and g3 and g4:
        verdict = "PASS_V2_ONLY(降档: 仅V2过)"
    else:
        verdict = "FAIL(全市场排名维持, 行业中性化无增量)"
    print(f"\n总判定: {verdict}")
    log("接线零动作: 本结论仅为研究报告, round10 协议变更权在主人")

    json.dump({"verdict": verdict, "prereg_fp": fp,
               "gates": {"G1_V1": g1_v1, "G1_V2": g1_v2, "G2": g2, "G3": g3,
                         "G4": g4, "G5_t": round(t, 2), "G6_pbo": pbo},
               "arms_full": {str(a): results[a] for a in ARMS},
               "yearly": {str(y): bool(v) for y, v in yearly.items()},
               "st_filter": st_used, "n_rebalances": len(nav1["V0"]) - 1,
               "n_empty_periods": n_empty},
              open(RESULT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("结果落档 data/industry_neutral_exp_result.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
