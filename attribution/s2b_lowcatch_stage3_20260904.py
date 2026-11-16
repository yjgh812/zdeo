#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2B-LOWCATCH-STAGE3：涨停聚集行业低位补涨组合级回测（PREREG: reports/PREREG_S2B_LOWCATCH_STAGE3_20260904.md）

截面每14天，T-1行业涨停≥5 → 行业内低位组(mom20∈[-20%,中位])取动量升序前10，
T+1开盘买入(涨停跳过)、T+10收盘卖出，费用0.15%(敏感性0.30%)。
对照：全市场等权/高位组/随机组。纯只读。
"""
import bisect
import hashlib
import json
import math
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

CONFIG = {
    "start": "20210106", "end": "20260813",   # 末截面保证 T+10 ≤ 20260827 数据边界
    "step_days": 14, "signal_k": 5,
    "n_pick": 10, "mom_win": 20, "hold": 10,
    "mom_floor": -20.0, "max_price": 30.0, "min_amt": 3000e4,
    "fee": 0.0015, "fee_hi": 0.0030,
    "limit_main": 9.8, "limit_300": 19.6,
    "gates": {"G1_years_min": 4, "G2_mdd_max": 25.0, "G3_t": 2.0},
    "seed": 20260904,
}
FINGERPRINT = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:16]

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT_JSON = r"D:\zcodesj\results\s2b_stage3_20260904.json"


def main():
    con = sqlite3.connect(FUND)
    ind_map, names = {}, {}
    for code, name, ind in con.execute("select code, name, industry from industry_map_ths"):
        ind_map[code] = ind.split("-")[1] if "-" in ind else ind
        names[code] = name or ""
    con.close()

    con = sqlite3.connect(QFQ)
    lu = defaultdict(int)
    for code, d, cp, c, h in con.execute(
            "select code, date, change_pct, close, high from stock_daily_meta_qfq "
            "where date >= '20201101' and ("
            " ((code like '60%' or code like '00%') and change_pct >= ? and close >= high)"
            " or (code like '30%' and change_pct >= ? and close >= high))",
            (CONFIG["limit_main"], CONFIG["limit_300"])):
        ind = ind_map.get(code)
        if ind:
            lu[(d, ind)] += 1
    px = pd.read_sql("select code, date, open, close, amount from stock_daily_meta_qfq "
                     "where date >= '20201101'", con)
    con.close()
    print(f"涨停聚合keys={len(lu)} px行={len(px)}", file=sys.stderr)
    piv_o = px.pivot_table(index="date", columns="code", values="open")
    piv_c = px.pivot_table(index="date", columns="code", values="close")
    piv_a = px.pivot_table(index="date", columns="code", values="amount")
    piv_pc = px.pivot_table(index="date", columns="code", values="close")  # 占位
    qd = list(piv_c.index)
    prev_close = piv_c.shift(1)

    d0 = pd.to_datetime(CONFIG["start"], format="%Y%m%d")
    d1 = pd.to_datetime(CONFIG["end"], format="%Y%m%d")
    crosses = []
    cur = d0
    while cur <= d1:
        i = bisect.bisect_left(qd, cur.strftime("%Y%m%d"))
        if i < len(qd):
            crosses.append(qd[i])
        cur += pd.Timedelta(days=CONFIG["step_days"])

    rng = np.random.default_rng(CONFIG["seed"])
    port, bench, hi_port, rnd_port = [], [], [], []
    aux = {"empty_periods": 0, "skip_limit": 0, "avg_n": [], "dose": defaultdict(list)}

    for T in crosses:
        i = bisect.bisect_left(qd, T)
        if i < CONFIG["mom_win"] + 5 or i + 1 + CONFIG["hold"] >= len(qd):
            continue
        Tm1 = qd[i - 1]
        buy_d, sell_d = qd[i + 1], qd[i + 1 + CONFIG["hold"]]
        main_inds = {}
        for (d, ind), n in lu.items():
            if d == Tm1 and n >= CONFIG["signal_k"]:
                main_inds[ind] = n
        # universe
        close_T = piv_c.loc[T].dropna()
        uni = [c for c in close_T.index if c[:2] in ("60", "00", "30")
               and "ST" not in names.get(c, "")
               and close_T[c] <= CONFIG["max_price"]]
        amt20 = piv_a.iloc[max(0, i - 20):i][uni].mean()
        uni = [c for c in uni if c in amt20.index and amt20[c] > CONFIG["min_amt"]]

        def ret10(codes):
            rs = []
            for c in codes:
                op = piv_o.loc[buy_d, c] if c in piv_o.columns else np.nan
                cl = piv_c.loc[sell_d, c] if c in piv_c.columns else np.nan
                pc = prev_close.loc[T, c] if c in prev_close.columns else np.nan
                if any(pd.isna(x) for x in (op, cl)):
                    continue
                if pd.notna(pc) and pc > 0 and op / pc - 1 >= (CONFIG["limit_300"] if c.startswith("30") else CONFIG["limit_main"]) / 100:
                    aux["skip_limit"] += 1
                    continue
                rs.append((cl / op - 1) * 100)
            return rs

        def fee_ret(codes):
            rs = ret10(codes)
            return (float(np.mean(rs)) - CONFIG["fee"] * 100) if rs else 0.0

        # 基准
        bench.append({"T": T, "ret": fee_ret(uni)})
        # 信号组
        if not main_inds:
            port.append({"T": T, "ret": 0.0, "n": 0})
            hi_port.append({"T": T, "ret": 0.0, "n": 0})
            rnd_port.append({"T": T, "ret": 0.0, "n": 0})
            aux["empty_periods"] += 1
            continue
        p0 = qd[i - 1 - CONFIG["mom_win"]]
        mom = (piv_c.loc[Tm1] / piv_c.loc[p0] - 1) * 100
        lo_codes, hi_codes = [], []
        for ind in sorted(main_inds):  # 9/5修: set迭代顺序受PYTHONHASHSEED随机化影响,截断候选集非确定
            incodes = [c for c in uni if ind_map.get(c) == ind]
            if len(incodes) < 4:
                continue
            med = mom[incodes].median()
            lows = sorted([c for c in incodes if CONFIG["mom_floor"] <= mom.get(c, np.nan) <= med],
                          key=lambda c: (mom.get(c, 0.0), c))[:CONFIG["n_pick"]]
            highs = sorted([c for c in incodes if mom.get(c, np.nan) > med],
                           key=lambda c: (-mom.get(c, 0.0), c))[:CONFIG["n_pick"]]
            lo_codes += lows
            hi_codes += highs
            # 剂量
            if lows:
                aux["dose"]["5-8" if main_inds[ind] < 9 else ">=9"].append(ind)
        lo_codes = lo_codes[:CONFIG["n_pick"]]
        hi_codes = hi_codes[:CONFIG["n_pick"]]
        pool_rnd = sorted(set(lo_codes) | set(hi_codes))
        rnd_codes = list(rng.choice(pool_rnd, size=min(CONFIG["n_pick"], len(pool_rnd)), replace=False)) if pool_rnd else []
        port.append({"T": T, "ret": fee_ret(lo_codes), "n": len(lo_codes)})
        hi_port.append({"T": T, "ret": fee_ret(hi_codes), "n": len(hi_codes)})
        rnd_port.append({"T": T, "ret": fee_ret(rnd_codes), "n": len(rnd_codes)})
        aux["avg_n"].append(len(lo_codes))

    pdf = pd.DataFrame(port)
    bdf = pd.DataFrame(bench)
    hdf = pd.DataFrame(hi_port)
    rdf = pd.DataFrame(rnd_port)
    n_per_year = 365.0 / CONFIG["step_days"]

    def yearly(df):
        yd = defaultdict(list)
        for r in df.itertuples():
            yd[r.T[:4]].append(r.ret)
        return {y: float(np.mean(v)) for y, v in sorted(yd.items())}

    def cum_stats(rets, fee):
        r = np.array(rets, dtype=float) - (fee - CONFIG["fee"]) * 100  # 费用调整
        eq = np.cumprod(1 + r / 100)
        years = len(r) / n_per_year
        total = eq[-1] - 1
        ann = (eq[-1]) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else float("nan")
        peak = np.maximum.accumulate(eq)
        mdd = float(((eq / peak) - 1).min() * 100)
        return {"total_pct": total * 100, "ann_pct": ann * 100, "mdd_pct": mdd}

    res = {
        "n_periods": len(pdf),
        "s2b": cum_stats(pdf["ret"].tolist(), CONFIG["fee"]),
        "s2b_fee_hi": cum_stats(pdf["ret"].tolist(), CONFIG["fee_hi"]),
        "bench": cum_stats(bdf["ret"].tolist(), CONFIG["fee"]),
        "hi_ctrl": cum_stats(hdf["ret"].tolist(), CONFIG["fee"]),
        "rnd_ctrl": cum_stats(rdf["ret"].tolist(), CONFIG["fee"]),
    }
    exc = (pdf["ret"].values - bdf["ret"].values)
    yr_p, yr_b = yearly(pdf), yearly(bdf)
    yr_exc = {y: yr_p.get(y, 0) - yr_b.get(y, 0) for y in set(yr_p) | set(yr_b)}
    res["yearly_exc"] = yr_exc
    res["years_exc_positive"] = sum(1 for v in yr_exc.values() if v > 0)
    res["years_total"] = len(yr_exc)
    diff_hi = (pdf["ret"].values - hdf["ret"].values)
    t_hi = float(np.mean(diff_hi) / (np.std(diff_hi, ddof=1) / math.sqrt(len(diff_hi)))) if len(diff_hi) > 2 else float("nan")
    res["vs_hi_paired_t"] = t_hi
    res["monthly_win_rate_vs_bench"] = float(np.mean(exc > 0))
    res["aux"] = {"empty_periods": aux["empty_periods"],
                  "skip_limit": aux["skip_limit"],
                  "avg_n_pick": float(np.mean(aux["avg_n"])) if aux["avg_n"] else 0.0,
                  "dose_periods": {k: len(v) for k, v in aux["dose"].items()}}
    # 剂量：5-8 vs >=9 期收益差
    for k in ("5-8", ">=9"):
        pass

    g = CONFIG["gates"]
    v = {
        "G1_exc_pos": res["s2b"]["ann_pct"] > res["bench"]["ann_pct"],
        "G1_years": res["years_exc_positive"] >= g["G1_years_min"],
        "G2_mdd": res["s2b"]["mdd_pct"] > -g["G2_mdd_max"],
        "G3_t": (t_hi if t_hi == t_hi else -9) >= g["G3_t"],
        "G4_fee": res["s2b_fee_hi"]["ann_pct"] > res["bench"]["ann_pct"],
    }
    final = "PASS_CANDIDATE" if all(v.values()) else ("WEAK" if (v["G1_exc_pos"] and v["G1_years"]) else "FAIL")

    out = {"fingerprint": FINGERPRINT, "config": CONFIG, "verdicts": v, "final": final,
           "results": res}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"fingerprint": FINGERPRINT, "final": final, "verdicts": v, "results": res},
                     ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
