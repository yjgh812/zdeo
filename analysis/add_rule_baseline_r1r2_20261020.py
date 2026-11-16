# -*- coding: utf-8 -*-
"""
add_rule_baseline_r1r2_20261020.py — R1/R2 规则加仓基线条件事件研究(呈请②执行件, 零交易权)
================================================================================
规格冻结(2026-10-20, 与 reports/external_addposition_plan2_audit_20261020.md §6.3 一致):
  基础批 = reports/backtest_full_20261019_ladder_L0b/bp_ep_core_trades_full.csv
           (bp_ep 月度Top15 生产S-3口径, 1778笔全史, 引擎 next_open/止损0.08 语义已内含于台账)
  R1 触发 = 持仓窗内首个 t(晚于买入日): close_t ≥ (1+X)·P0 且 code ∈ 当日 stock_signals 日度池
  R2 触发 = 买入日后 N 个交易日内首个 t: code ∈ 当日池 且 基础批实际存活(以台账实现窗为准)
  执行   = 触发日次一有交易 bar 的 open(qfq); 一字/极强开盘不可成交过滤:
           open 较前收涨幅≥9.5%(10cm)/≥19%(20cm=300开头) 且 open==high → 跳过并计数
  加仓批退出:
    解耦臂: 自身 S-5=0.92·P1(收盘判定,当日close离场); 未触发则随原卖出日以原卖出价格离场
    联动臂: Pb=(P0+P1)/2, 加仓执行日起首个 close≤0.92·Pb → 全部仓位当日 close 离场;
            未触发则原卖出日原卖出价格全离场
  成本   = 加仓批往返 0.2%(自收益率扣减); 基础批用台账已含成本的原值
  网格   = R1 X∈{3,5,8}% × {联动,解耦} + R2 N∈{3,5} × {联动,解耦} = 10 配置, n_trials=10
  判读   = 加仓批 vs 基础批同窗(加仓执行时刻→原卖出)配对增量期望为一阶判据;
           右尾=Top5%盈利单贡献占比(arm vs R0); DSR(等摊日收益, n_trials=10); PBO=quant_integrity(可得则报)
输出 = analysis/add_rule_baseline_r1r2_result_20261020.json + 控制台摘要
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SIG_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
TRADES_CSV = os.environ.get(
    "R12_TRADES_CSV",
    r"D:\zcodesj\reports\backtest_full_20261019_ladder_L0b\bp_ep_core_trades_full.csv")
OUT_JSON = os.environ.get(
    "R12_OUT_JSON",
    r"D:\zcodesj\analysis\add_rule_baseline_r1r2_result_20261020.json")
ADD_COST = 0.002          # 加仓批往返成本
STOPS = {"coupled": None, "decoupled": None}  # 占位, 语义见触发循环
X_GRID = [0.03, 0.05, 0.08]
N_GRID = [3, 5]
ARMS = ("coupled", "decoupled")
TOP_TAIL_Q = 0.05


def _dnorm(d):
    d = str(d)
    return d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:]}"


def load_qfq(codes):
    db = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    bars = {}
    for c in codes:
        rows = [(_dnorm(d), o, h, l, cl) for d, o, h, l, cl in db.execute(
            "select date,open,high,low,close from stock_daily_meta_qfq where code=? order by date", (c,)
        )]
        bars[c] = rows
    db.close()
    return bars


def load_signal_pool(codes, since="20150101"):
    db = sqlite3.connect(f"file:{SIG_DB}?mode=ro", uri=True)
    pool = defaultdict(set)
    for c in codes:
        for (d,) in db.execute(
            "select date from stock_signals where code=? and date>=?", (c, since)
        ):
            pool[c].add(_dnorm(d))
    db.close()
    return pool


def limit_unfillable(prev_close, o, h, code):
    if prev_close <= 0:
        return False
    ratio = o / prev_close - 1
    cap = 0.19 if str(code).startswith(("300", "301")) else 0.095
    return ratio >= cap and o >= h


def walk_trade(code, bars, pool, e_date, d_date, p0, x=None, n_hold=None, need_sig=True):
    """返回触发事件列表(每个事件: 触发日idx/执行日idx/执行价/臂无关部分)"""
    rows = bars.get(code) or []
    dates = [r[0] for r in rows]
    try:
        i0 = dates.index(e_date)
    except ValueError:
        return None
    # 持仓窗: 买入日次一 bar 起, 至原卖出日(含); 若卖出日不在该code bar序列(退市缺口)用其后首个存在日截断
    try:
        iD = min(j for j, dt in enumerate(dates) if dt >= d_date and j >= i0)
    except ValueError:
        iD = len(rows) - 1
    evs = []
    for j in range(i0 + 1, iD + 1):
        dt, o, h, l, c = rows[j]
        if dates[j] > d_date:
            break
        if x is not None:                      # R1/R1p
            sig_ok = (not need_sig) or dates[j] in pool.get(code, ())
            trig = c >= (1 + x) * p0 and sig_ok
        else:                                   # R2
            trig = (j - i0) <= n_hold and dates[j] in pool.get(code, ())
        if trig and j + 1 <= iD:
            nd, no = rows[j + 1][0], rows[j + 1][1]
            if limit_unfillable(c, no, rows[j + 1][2], code):
                continue
            evs.append({"t": j, "exec_j": j + 1, "exec_px": no})
            break                               # 每笔最多一次加仓(一档)
    return {"i0": i0, "iD": iD, "rows": rows, "evs": evs}


def simulate(trade, w, mode="decoupled"):
    """返回 (事件dict或None). 收益率为小数, 已扣加仓批成本."""
    p0 = float(trade["买入价格"]); p_exit = float(trade["卖出价格"])
    r0 = p_exit / p0 - 1
    if w is None or not w["evs"]:
        return None
    ev = w["evs"][0]
    rows, i0, iD = w["rows"], w["i0"], w["iD"]
    je, p1 = ev["exec_j"], ev["exec_px"]
    # 基础批同窗收益(自加仓执行时刻至原卖出)
    base_same_win = p_exit / p1 - 1
    add_ret = None; base_ret_arm = r0; stopped = False
    if mode == "decoupled":
        stop_px = 0.92 * p1
        for j in range(je, iD + 1):
            if rows[j][4] <= stop_px:
                add_ret = rows[j][4] / p1 - 1 - ADD_COST
                stopped = True
                break
        if add_ret is None:
            add_ret = p_exit / p1 - 1 - ADD_COST
        comb = 0.5 * r0 + 0.5 * add_ret          # qty 相等 0.5/0.5
    else:                                        # coupled
        pb = (p0 + p1) / 2
        stop_px = 0.92 * pb
        for j in range(je, iD + 1):
            if rows[j][4] <= stop_px:
                allpx = rows[j][4]
                add_ret = allpx / p1 - 1 - ADD_COST
                base_ret_arm = allpx / p0 - 1
                stopped = True
                comb = allpx / pb - 1
                break
        if not stopped:
            add_ret = p_exit / p1 - 1 - ADD_COST
            comb = 0.5 * r0 + 0.5 * add_ret
    return {
        "code": trade["股票代码"], "entry": trade["买入日期"], "exit": trade["卖出日期"],
        "days": int(trade["持仓天数"]), "r0": r0, "base_same_win": base_same_win,
        "add_ret": add_ret, "comb": comb, "base_arm": base_ret_arm,
        "stopped": stopped, "p1": p1, "paired": add_ret - base_same_win,
    }


def dsr_manual(daily, n_trials):
    """Bailey & López de Prado Deflated Sharpe(手工实现, 免外部API分歧)"""
    import math
    n = len(daily)
    if n < 20:
        return None
    mean = sum(daily) / n
    var = sum((x - mean) ** 2 for x in daily) / (n - 1)
    skew = sum((x - mean) ** 3 for x in daily) / n / (var ** 1.5) if var > 0 else 0
    kurt = sum((x - mean) ** 4 for x in daily) / n / (var ** 2) if var > 0 else 3
    sr = mean / math.sqrt(var) * math.sqrt(252) if var > 0 else 0.0
    emc = 0.5772156649
    z1 = _ppf(1 - 1.0 / n_trials)
    z2 = _ppf(1 - 1.0 / (n_trials * math.e))
    sr0 = math.sqrt(var / 252 * (1 if True else 1))  # 期望SR0=0, 方差项按日频估计的SR方差
    v_sr = (1 - skew * sr / math.sqrt(252) + (kurt - 1) / 4 * (sr ** 2) / 252) / (n - 1)
    sr0 = math.sqrt(max(v_sr, 1e-12)) * ((1 - emc) * z1 + emc * z2) * math.sqrt(252)
    psr = _ncdf((sr - sr0) * math.sqrt(n / (1 - skew * sr / math.sqrt(252) + (kurt - 1) / 4 * sr ** 2)))
    return psr


def _ppf(p):
    """标准正态分位数(Beasley-Springer-Moro 简表近似足够)"""
    import math
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _ncdf(x):
    import math
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def spread_daily(evs, key):
    """把每事件的收益按持有天数均摊成日收益序列(年化判定用), 返回list"""
    out = []
    for e in evs:
        d = max(int(e["days"]), 1)
        out.extend([e[key] / d] * d)
    return out


def top_tail_share(rets):
    pos = sorted([r for r in rets if r > 0], reverse=True)
    if not pos:
        return None
    k = max(1, int(len(rets) * TOP_TAIL_Q))
    tot = sum(pos)
    return sum(pos[:k]) / tot if tot > 0 else None


def main():
    trades = pd.read_csv(TRADES_CSV, dtype={"股票代码": str})
    trades["股票代码"] = trades["股票代码"].str.zfill(6)
    codes = sorted(trades["股票代码"].unique())
    print(f"[load] trades={len(trades)} codes={len(codes)}", flush=True)
    bars = load_qfq(codes)
    empty = sum(1 for c in codes if not bars.get(c))
    print(f"[load] qfq codes empty={empty}", flush=True)
    pool = load_signal_pool(codes)

    configs = []
    for x in X_GRID:
        for mode in ARMS:
            configs.append((f"R1_X{int(x*100)}_{mode}", dict(x=x, mode=mode)))
            configs.append((f"R1P_X{int(x*100)}_{mode}", dict(x=x, mode=mode, need_sig=False)))
    for n in N_GRID:
        for mode in ARMS:
            configs.append((f"R2_N{n}_{mode}", dict(n_hold=n, mode=mode)))

    r0_rets = trades["盈亏比例%"] / 100.0
    results = {"meta": {"n_trades": len(trades), "n_trials": len(configs),
                        "trades_csv": TRADES_CSV, "add_cost": ADD_COST}, "configs": {}}
    r0_days = list(trades["持仓天数"])
    daily_matrix = {"R0": spread_daily([{"days": d, "v": r} for d, r in zip(r0_days, r0_rets)], "v")}
    tail0 = top_tail_share(list(r0_rets))
    wins0 = sum(1 for r in r0_rets if r > 0)

    for name, kw in configs:
        evs = []
        for idx, t in trades.iterrows():
            w = walk_trade(t["股票代码"], bars, pool, t["买入日期"], t["卖出日期"],
                           float(t["买入价格"]), kw.get("x"), kw.get("n_hold"),
                           kw.get("need_sig", True))
            if w is None or not w["evs"]:
                continue
            evs.append((idx, t, w))
        rows = []
        for idx, t, w in evs:
            r = simulate(t, w, mode=kw["mode"])
            if r:
                r["idx"] = idx
                rows.append(r)
        if not rows:
            results["configs"][name] = {"n_events": 0}
            continue
        # 全臂口径: 非事件交易沿用R0收益, 事件交易替换为组合收益
        comb_series = list(r0_rets)
        for r in rows:
            comb_series[r["idx"]] = r["comb"]
        paired = [r["paired"] for r in rows]
        add_rets = [r["add_ret"] for r in rows]
        stop_rate = sum(1 for r in rows if r["stopped"]) / len(rows)
        daily_matrix[name] = spread_daily([{"days": d, "v": v} for d, v in zip(r0_days, comb_series)], "v")
        n = len(comb_series)
        arm_win = sum(1 for c in comb_series if c > 0) / n
        arm_mean = sum(comb_series) / n
        wins_ = [c for c in comb_series if c > 0]; losses_ = [c for c in comb_series if c < 0]
        tail_arm = top_tail_share(comb_series)
        res = {
            "n_events": len(rows), "trigger_rate": round(len(rows) / len(trades), 4),
            "add_mean": round(sum(add_rets) / len(add_rets), 5),
            "add_med": round(sorted(add_rets)[len(add_rets)//2], 5),
            "add_win": round(sum(1 for a in add_rets if a > 0) / len(add_rets), 4),
            "add_stop_rate": round(stop_rate, 4),
            "paired_mean": round(sum(paired) / len(paired), 5),
            "paired_pos": round(sum(1 for p in paired if p > 0) / len(paired), 4),
            "arm_mean": round(arm_mean, 5),
            "r0_mean": round(float(r0_rets.mean()), 5),
            "arm_win": round(arm_win, 4),
            "r0_win": round(wins0 / len(r0_rets), 4),
            "win_delta": round(arm_win - wins0 / len(r0_rets), 4),
            "pl_ratio_arm": round((sum(wins_) / max(1, len(wins_))) / max(1e-9, abs(sum(losses_) / max(1, len(losses_)))), 3) if wins_ and losses_ else None,
            "pl_ratio_r0": round((sum(r for r in r0_rets if r > 0) / max(1, wins0)) / max(1e-9, abs(sum(r for r in r0_rets if r < 0) / max(1, len(r0_rets) - wins0))), 3),
            "tail_r0": round(tail0, 4) if tail0 is not None else None,
            "tail_arm": round(tail_arm, 4) if tail_arm is not None else None,
            "extra_turnover": round(len(rows) / len(trades), 4),
        }
        results["configs"][name] = res

    # DSR per config + R0(官方实现: quant_integrity.deflated_sharpe)
    try:
        import quant_integrity as qi
        _n = len(configs)
        for name, ser in daily_matrix.items():
            d_ = qi.deflated_sharpe(ser, _n)
            if name == "R0":
                results["dsr_api_sample"] = {k: (round(v, 5) if isinstance(v, float) else v)
                                             for k, v in d_.items()}
            val = None
            for k in ("dsr", "deflated_sharpe", "prob", "probability"):
                if k in d_ and isinstance(d_[k], (int, float)):
                    val = d_[k]; break
            results["configs"].setdefault(name, {})["dsr"] = val
    except Exception as e:
        results["dsr_note"] = f"quant_integrity DSR失败: {type(e).__name__}: {e}"

    # PBO via quant_integrity(矩阵=配置×观测)
    try:
        from quant_integrity import probability_backtest_overfitting
        keys = [k for k in daily_matrix if k != "R0"]
        L = min(len(daily_matrix[k]) for k in daily_matrix)
        mat = [[daily_matrix[k][i] for i in range(L)] for k in keys]
        p_ = probability_backtest_overfitting(mat, n_groups=8, labels=keys)
        results["pbo_raw"] = {k: (round(v, 5) if isinstance(v, float) else v)
                              for k, v in p_.items() if isinstance(v, (int, float, str))}
        results["pbo_note"] = "quant_integrity CSCV, configs=%d, L=%d" % (len(keys), L)
    except Exception as e:
        results["pbo"] = None
        results["pbo_note"] = f"unavailable: {type(e).__name__}: {e}"

    # 读回验证
    tot_ev = sum(v.get("n_events", 0) for v in results["configs"].values()
                 if isinstance(v, dict) and "n_events" in v)
    assert tot_ev > 0, "读回验证失败: 零事件"
    results["readback"] = {"total_events_all_configs": tot_ev}

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"[done] events_sum={tot_ev} → {OUT_JSON}", flush=True)
    for name in [c[0] for c in configs]:
        r = results["configs"][name]
        if r.get("n_events"):
            print(f"{name}: ev={r['n_events']} add_mean={r['add_mean']} paired={r['paired_mean']} "
                  f"winΔ={r['win_delta']} tail={r['tail_arm']} dsr={r.get('dsr')}", flush=True)


if __name__ == "__main__":
    main()
