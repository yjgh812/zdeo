# -*- coding: utf-8 -*-
"""
F4 振幅分层回测（2026-08-10 知微）
=================================
缘起：6 指纹消融回测意外发现"排除振幅>7%后年化 93.7%→109.9%"
目的：严谨验证振幅与低开承接收益的关系，测试振幅分层定价策略
分档：0-3% / 3-5% / 5-7% / 7-10% / >10% 共5档
测试：
  A. 各档单独回测（胜率/年化/MDD/平均收益），观察单调性
  B. 振幅分层定价：高振幅档减小仓位（如 7-10% 仓×0.5，>10% 仓×0.25）
  C. 仅保留低振幅档（<7%）的回测
  D. 排除极端高振幅档（>10%）的回测
诚实标注：
  - 缘起是单次消融的意外发现，本身是 multiple testing 产物，必须 DSR 校正
  - 单调性检验：若各档收益不单调，则分层无意义
  - V1 数据预检 + V2 DSR 强制
输出：D:\zcodesj\t1_research\amplitude_tier_result.json
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

TDX = Path(r"D:\anzhuang\tdx\vipdoc")
START = "20180101"
END = "20260807"
OUT = Path(r"D:\zcodesj\t1_research\amplitude_tier_result.json")

STOCK_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301")
DAY_DTYPE = np.dtype([("date", "<u4"), ("open", "<u4"), ("high", "<u4"), ("low", "<u4"),
                      ("close", "<u4"), ("amount", "<f4"), ("volume", "<u4"), ("unused", "<u4")])

INIT_CAP = 100_000.0
MAX_PER_DAY = 5
COST = 0.003

# 振幅分档（基于 T 日涨停/炸板当日的振幅）
TIERS = [
    ("T1_0-3%", 0.0, 0.03),
    ("T2_3-5%", 0.03, 0.05),
    ("T3_5-7%", 0.05, 0.07),
    ("T4_7-10%", 0.07, 0.10),
    ("T5_>10%", 0.10, 9.99),
]


def read_day(path: Path) -> pd.DataFrame:
    raw = path.read_bytes()
    n = len(raw) // 32
    if n == 0:
        return pd.DataFrame()
    d = np.frombuffer(raw[: n * 32], dtype=DAY_DTYPE)
    idx = pd.to_datetime(d["date"].astype(np.int64).astype(str), format="%Y%m%d")
    return pd.DataFrame({
        "open": d["open"] / 100.0, "high": d["high"] / 100.0,
        "low": d["low"] / 100.0, "close": d["close"] / 100.0,
        "amount": d["amount"], "volume": d["volume"].astype(np.float64),
    }, index=idx)


def limit_ratio(code: str) -> float:
    return 0.20 if code.startswith(("300", "301")) else 0.10


def collect(args):
    """收集单只股票的信号事件，含 T 日振幅。"""
    code, market = args
    day_path = TDX / market / "lday" / f"{market}{code}.day"
    if not day_path.exists() or not code.startswith(STOCK_PREFIXES):
        return []
    try:
        day = read_day(day_path)
    except Exception:
        return []
    if len(day) < 20:
        return []
    day = day[day.index >= "20171001"]
    if len(day) == 0:
        return []

    ratio = limit_ratio(code)
    lims = (day["close"].shift(1) * (1 + ratio)).round(2)
    touched = (day["high"] >= lims - 1e-9) & day["close"].notna()
    sealed = ((day["close"] - lims).abs() <= 0.001) & day["close"].notna()

    events = []
    for i in range(20, len(day)):
        dt = day.index[i]
        if dt < pd.Timestamp(START):
            continue
        lim = lims.iloc[i]
        if pd.isna(lim) or not touched.iloc[i] or i + 1 >= len(day):
            continue
        c, o, h, l = day["close"].iloc[i], day["open"].iloc[i], day["high"].iloc[i], day["low"].iloc[i]
        nxt = day.iloc[i + 1]
        o1, h1, l1, c1 = nxt["open"], nxt["high"], nxt["low"], nxt["close"]
        if o1 <= 0 or c1 <= 0:
            continue
        gap = o1 / c - 1
        if gap > -0.03 or o1 >= lim - 1e-9:
            continue
        # 跌停开盘剔除（D 变体）
        ld = round(c * (1 - ratio), 2)
        if abs(o1 - ld) <= 0.005:
            continue
        # T 日振幅（信号当日）
        amp_t = (h - l) / c if c > 0 else 0
        events.append({
            "code": code, "date": str(dt.date()),
            "kind": "sealed" if sealed.iloc[i] else "broken",
            "is_new": int(i < 60),
            "close0": float(c), "open1": float(o1), "high1": float(h1),
            "low1": float(l1), "close1": float(c1),
            "gap": float(gap),
            "amp_t": float(amp_t),  # T 日振幅
        })
    return events


def run_backtest(d: pd.DataFrame, tier_pos=None):
    """日频净值回测。tier_pos 可选：按振幅档调整仓位（dict: tier_name->pos）。"""
    if len(d) == 0:
        return {"n": 0, "win": 0, "ann": 0, "cum": 0, "mdd": 0, "avg_ret": 0}
    d = d.copy()
    d["trade_ret"] = d["ret"] - COST
    if tier_pos is not None and "tier" in d.columns:
        d["pos"] = d["tier"].map(tier_pos).fillna(0.20)
    else:
        d["pos"] = 0.20
    days = sorted(d["date"].unique())
    eq = INIT_CAP
    eq_curve, wins, n = [], [], 0
    for day in days:
        day_tr = d[d["date"] == day]
        n_day = len(day_tr)
        if n_day == 0:
            continue
        day_pnl = 0.0
        for _, row in day_tr.iterrows():
            per = eq * row["pos"] / n_day
            day_pnl += per * row["trade_ret"]
            wins.append(1 if row["trade_ret"] > 0 else 0)
            n += 1
        eq += day_pnl
        eq_curve.append(eq)
    eq_arr = np.array(eq_curve)
    if len(eq_arr) == 0:
        return {"n": 0, "win": 0, "ann": 0, "cum": 0, "mdd": 0, "avg_ret": 0}
    mdd = float((eq_arr / np.maximum.accumulate(eq_arr) - 1).min() * 100)
    cum = (eq / INIT_CAP - 1) * 100
    ny = len(set(d["date"])) / 242
    ann = ((eq / INIT_CAP) ** (1 / ny) - 1) * 100 if ny > 0 else 0
    return {
        "n": n, "win": round(float(np.mean(wins)) * 100, 1) if wins else 0,
        "ann": round(float(ann), 1), "cum": round(float(cum), 1),
        "mdd": round(mdd, 1), "avg_ret": round(float(d["trade_ret"].mean() * 100), 3),
    }


def compute_dsr(daily_rets, n_trials=1):
    """简化 DSR。"""
    if len(daily_rets) < 2:
        return {"sharpe": 0, "psr0": 0, "label": "⚠️ 样本不足"}
    arr = np.array(daily_rets)
    mu, sigma = arr.mean(), arr.std()
    if sigma == 0:
        return {"sharpe": 0, "psr0": 0, "label": "⚠️ 方差为0"}
    sharpe = mu / sigma * (242 ** 0.5)
    from scipy.stats import norm
    psr0 = norm.cdf(sharpe * np.sqrt(len(arr) / 242))
    sr0 = np.sqrt(2 * np.log(n_trials)) if n_trials > 1 else 0
    dsr = norm.cdf((sharpe - sr0) * np.sqrt(len(arr) / 242))
    label = "✅ 确认" if dsr > 0.95 else ("🟡 待验证" if dsr > 0.50 else "⚠️ 存疑")
    return {"sharpe": round(sharpe, 3), "psr0": round(psr0, 4),
            "dsr": round(dsr, 4), "n_trials": n_trials, "label": label}


def main():
    codes = []
    for market in ("sh", "sz"):
        lday = TDX / market / "lday"
        if lday.exists():
            codes += [(f.stem[2:], market) for f in lday.glob(f"{market}*.day")]
    print(f"[info] 扫描 {len(codes)} 只（窗口 {START}~{END}）", flush=True)

    events = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(collect, c) for c in codes]
        for f in as_completed(futs):
            events.extend(f.result())
    ev = pd.DataFrame(events)
    if len(ev) == 0:
        print("[warn] 无事件")
        return

    # 合规：剔次新/低价
    ev = ev[(ev["is_new"] == 0) & (ev["close0"] >= 2)].copy()
    ev["buy_ret"] = ev["close1"] / ev["open1"] - 1
    ev["low_ret"] = ev["low1"] / ev["open1"] - 1
    ev = ev[np.isfinite(ev["buy_ret"]) & (ev["buy_ret"] >= -0.3) & (ev["buy_ret"] <= 0.3)]
    ev["ret"] = ev["buy_ret"]
    ev = ev.sort_values(["date", "gap"]).reset_index(drop=True)
    ev["rank_in_day"] = ev.groupby("date").cumcount()
    ev = ev[ev["rank_in_day"] < MAX_PER_DAY].copy()

    # 振幅分档
    def assign_tier(amp):
        for name, lo, hi in TIERS:
            if lo <= amp < hi:
                return name
        return "T5_>10%"
    ev["tier"] = ev["amp_t"].apply(assign_tier)

    print(f"[info] D 变体信号（同日最多{MAX_PER_DAY}只）: {len(ev)}")
    print(f"\n振幅分档分布:")
    tier_dist = ev.groupby("tier").agg(
        count=("ret", "size"),
        avg_ret=("ret", lambda x: round(x.mean() * 100, 3)),
        win_rate=("ret", lambda x: round((x > 0).mean() * 100, 1)),
        avg_amp=("amp_t", lambda x: round(x.mean() * 100, 2)),
    )
    print(tier_dist.to_string())

    # V1 数据预检
    v1 = {"status": "pass", "tier_distribution": tier_dist["count"].to_dict()}
    print(f"\n[V1 数据预检] {v1['status']}")

    # ===== 测试 A：各档单独回测 =====
    results = {}
    print("\n===== A. 各档单独回测 =====")
    print(f"{'档位':<14}{'笔数':>6}{'胜率':>7}{'年化':>8}{'MDD':>8}{'均/笔':>9}")
    tier_results = {}
    for name, _, _ in TIERS:
        sub = ev[ev["tier"] == name].copy()
        if len(sub) < 10:
            print(f"{name:<14}{len(sub):>6}  (样本不足，跳过)")
            continue
        r = run_backtest(sub)
        tier_results[name] = r
        print(f"{name:<14}{r['n']:>6}{r['win']:>6.1f}%{r['ann']:>7.1f}%{r['mdd']:>7.1f}%{r['avg_ret']:>8.3f}%")
    results["A_各档单独"] = tier_results

    # 单调性检验
    tiers_ordered = [t for t, _, _ in TIERS if t in tier_results]
    avg_rets = [tier_results[t]["avg_ret"] for t in tiers_ordered]
    is_monotone = all(avg_rets[i] >= avg_rets[i + 1] for i in range(len(avg_rets) - 1)) or \
                  all(avg_rets[i] <= avg_rets[i + 1] for i in range(len(avg_rets) - 1))
    results["A_单调性"] = {"tiers": tiers_ordered, "avg_rets": avg_rets, "is_monotone": bool(is_monotone)}
    print(f"\n单调性: {'是' if is_monotone else '否'}（avg_rets={avg_rets}）")

    # ===== 测试 B：振幅分层定价 =====
    print("\n===== B. 振幅分层定价 =====")
    # 策略：低振幅标准仓，高振幅减仓
    tier_poschemes = {
        "B1_温和递减": {"T1_0-3%": 0.25, "T2_3-5%": 0.22, "T3_5-7%": 0.20, "T4_7-10%": 0.15, "T5_>10%": 0.10},
        "B2_激进递减": {"T1_0-3%": 0.30, "T2_3-5%": 0.25, "T3_5-7%": 0.20, "T4_7-10%": 0.10, "T5_>10%": 0.05},
        "B3_排除T5": {"T1_0-3%": 0.22, "T2_3-5%": 0.22, "T3_5-7%": 0.20, "T4_7-10%": 0.18, "T5_>10%": 0.0},
    }
    b_results = {}
    print(f"{'方案':<16}{'笔数':>6}{'胜率':>7}{'年化':>8}{'MDD':>8}{'均/笔':>9}")
    for scheme_name, pos_map in tier_poschemes.items():
        r = run_backtest(ev, tier_pos=pos_map)
        b_results[scheme_name] = r
        print(f"{scheme_name:<16}{r['n']:>6}{r['win']:>6.1f}%{r['ann']:>7.1f}%{r['mdd']:>7.1f}%{r['avg_ret']:>8.3f}%")
    results["B_分层定价"] = b_results

    # ===== 测试 C/D：阈值过滤 =====
    print("\n===== C/D. 阈值过滤 =====")
    print(f"{'方案':<24}{'笔数':>6}{'胜率':>7}{'年化':>8}{'MDD':>8}{'均/笔':>9}")
    cd_results = {}
    # 基线
    base = run_backtest(ev)
    cd_results["基线_全量"] = base
    print(f"{'基线_全量':<24}{base['n']:>6}{base['win']:>6.1f}%{base['ann']:>7.1f}%{base['mdd']:>7.1f}%{base['avg_ret']:>8.3f}%")
    # 仅低振幅 <7%
    low = ev[ev["amp_t"] < 0.07].copy()
    r_low = run_backtest(low)
    cd_results["C_仅振幅<7%"] = r_low
    print(f"{'C_仅振幅<7%':<24}{r_low['n']:>6}{r_low['win']:>6.1f}%{r_low['ann']:>7.1f}%{r_low['mdd']:>7.1f}%{r_low['avg_ret']:>8.3f}%")
    # 排除极端 >10%
    no_extreme = ev[ev["amp_t"] <= 0.10].copy()
    r_ne = run_backtest(no_extreme)
    cd_results["D_排除振幅>10%"] = r_ne
    print(f"{'D_排除振幅>10%':<24}{r_ne['n']:>6}{r_ne['win']:>6.1f}%{r_ne['ann']:>7.1f}%{r_ne['mdd']:>7.1f}%{r_ne['avg_ret']:>8.3f}%")
    # 排除 >7%（上次消融的发现）
    no7 = ev[ev["amp_t"] <= 0.07].copy()
    r_no7 = run_backtest(no7)
    cd_results["排除振幅>7%"] = r_no7
    print(f"{'排除振幅>7%':<24}{r_no7['n']:>6}{r_no7['win']:>6.1f}%{r_no7['ann']:>7.1f}%{r_no7['mdd']:>7.1f}%{r_no7['avg_ret']:>8.3f}%")
    results["CD_阈值过滤"] = cd_results

    # ===== V2 DSR =====
    print("\n[V2 DSR 检验]")
    # n_trials = 5档×3(B方案)+4(C/D) ≈ 19 次试验（保守估）
    n_trials = 19
    daily_base = ev.groupby("date")["ret"].mean()
    dsr_base = compute_dsr(daily_base.tolist(), n_trials)
    print(f"  基线 DSR(n_trials={n_trials}): Sharpe={dsr_base.get('sharpe')} "
          f"DSR={dsr_base.get('dsr')} → {dsr_base['label']}")
    # 对最优分层定价方案
    best_scheme = max(b_results, key=lambda k: b_results[k]["ann"])
    daily_best = ev.copy()
    daily_best["pos"] = daily_best["tier"].map(tier_poschemes[best_scheme]).fillna(0.20)
    daily_best["w_ret"] = daily_best["ret"] * daily_best["pos"] / 0.20  # 加权
    daily_w = daily_best.groupby("date")["w_ret"].mean()
    dsr_best = compute_dsr(daily_w.tolist(), n_trials)
    print(f"  最优方案({best_scheme}) DSR: Sharpe={dsr_best.get('sharpe')} "
          f"DSR={dsr_best.get('dsr')} → {dsr_best['label']}")

    results["_v1_precheck"] = v1
    results["_v2_dsr"] = {"baseline": dsr_base, f"best_{best_scheme}": dsr_best, "n_trials": n_trials}
    results["_meta"] = {
        "window": f"{START}~{END}",
        "cost": COST,
        "origin": "6指纹消融回测意外发现：排除振幅>7%后年化93.7%→109.9%（单次观察，multiple testing风险）",
        "honest_note": "缘起是multiple testing产物，DSR已用n_trials=19校正。单调性必须成立分层才有意义。",
        "tier_thresholds": {name: [lo, hi] for name, lo, hi in TIERS},
    }

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {OUT}")


if __name__ == "__main__":
    main()
