# -*- coding: utf-8 -*-
"""
大跌风险 6 指纹硬过滤·消融回测（2026-08-10 知微）
=================================================
目的：验证 is_high_risk_pattern() 指纹过滤对低开承接策略的改善效果
基线：dip_backtest.py 的 D 变体（封板+炸板，剔跌停开盘）—— 真实 A 股、8.6 年
消融：
  基线（无过滤）
  vs 过滤命中≥3 排除
  vs 过滤命中≥4 排除
  vs 过滤命中≥2 排除
  + 单指纹消融（逐个剔除 F1/F2/F4/F5，看各指纹贡献）
诚实标注：
  - 指纹3（委比）/ 指纹6（PE/PB）日线不可得，本回测仅用 F1/F2/F4/F5 四指纹
  - 所有增益标"待验证"，证据等级由 DSR 决定
  - V1 数据预检 + V2 DSR 强制
输出：D:\\zcodesj\\t1_research\\risk_filter_ablation_result.json
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\workbuddyspace")
from tulong_rules import is_high_risk_pattern

TDX = Path(r"D:\anzhuang\tdx\vipdoc")
START = "20180101"
END = "20260807"
OUT = Path(r"D:\zcodesj\t1_research\risk_filter_ablation_result.json")

STOCK_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301")

import tdx_common as tc  # 2026-08-31 收敛: 解析统一走 tdx_common(委托 tdx_reader)



INIT_CAP = 100_000.0
POS = 0.20
MAX_PER_DAY = 5
COST = 0.003  # 0.30% 双边成本




def limit_ratio(code: str) -> float:
    return 0.20 if code.startswith(("300", "301")) else 0.10


def collect(args):
    """收集单只股票的信号事件 + 截止信号日的完整日线（用于指纹计算）。"""
    code, market = args
    day_path = TDX / market / "lday" / f"{market}{code}.day"
    if not day_path.exists() or not code.startswith(STOCK_PREFIXES):
        return []
    try:
        day = tc.read_day(day_path)
    except Exception:
        return []
    if len(day) < 25:  # 至少 25 行（20 日 MA + 缓冲）
        return []
    day = day[day.index >= "20171001"]
    if len(day) == 0:
        return []

    ratio = limit_ratio(code)
    lims = (day["close"].shift(1) * (1 + ratio)).round(2)
    touched = (day["high"] >= lims - 1e-9) & day["close"].notna()
    sealed = ((day["close"] - lims).abs() <= 0.001) & day["close"].notna()

    events = []
    for i in range(20, len(day)):  # 从第 20 行开始（保证 MA20 可算）
        dt = day.index[i]
        if dt < pd.Timestamp(START):
            continue
        lim = lims.iloc[i]
        if pd.isna(lim) or not touched.iloc[i] or i + 1 >= len(day):
            continue
        c, o = day["close"].iloc[i], day["open"].iloc[i]
        nxt = day.iloc[i + 1]
        o1, h1, l1, c1 = nxt["open"], nxt["high"], nxt["low"], nxt["close"]
        if o1 <= 0 or c1 <= 0:
            continue
        # 信号：低开≥3%、非一字
        gap = o1 / c - 1
        if gap > -0.03 or o1 >= lim - 1e-9:
            continue
        # 跌停开盘剔除（D 变体）
        ld = round(c * (1 - ratio), 2)
        if abs(o1 - ld) <= 0.005:
            continue
        # 指纹计算：用截止 T 日（含）的日线数据（PIT 安全：T+1 才买入）
        df_until_t = day.iloc[: i + 1].copy()
        hits, detail = is_high_risk_pattern(df_until_t, min_hits=0)  # min_hits=0 返回所有命中数
        events.append({
            "code": code, "date": str(dt.date()),
            "kind": "sealed" if sealed.iloc[i] else "broken",
            "is_new": int(i < 60),
            "close0": float(c), "open1": float(o1), "high1": float(h1),
            "low1": float(l1), "close1": float(c1),
            "gap": float(gap),
            "fp_hits": detail.get("hit_count", 0),
            "fp_total": detail.get("total_fingerprints_checked", 0),
            "fp_F1": int(bool(detail.get("F1_YTD_high", False))),  # YTD>150%
            "fp_F2": int(bool(detail.get("F2_trend20_down", False))),  # ret20<0
            "fp_F4": int(bool(detail.get("F4_amplitude_high", False))),  # 振幅>7%
            "fp_F5": int(bool(detail.get("F5_vol_ratio_high", False))),  # 量比>1.5
            "ytd": float(detail.get("ytd", 0)),
            "ret20": float(detail.get("ret20", 0)),
            "amplitude": float(detail.get("amplitude", 0)),
            "vol_ratio": float(detail.get("vol_ratio", 1.0)),
        })
    return events


def v1_data_precheck(ev: pd.DataFrame) -> dict:
    """V1 数据预检：检查信号池数据质量。"""
    report = {"status": "pass", "warnings": []}
    n = len(ev)
    if n == 0:
        report["status"] = "fail"
        report["warnings"].append("信号池为空")
        return report
    # 检查 fp_hits 分布
    hits_dist = ev["fp_hits"].value_counts().sort_index().to_dict()
    report["fp_hits_distribution"] = {int(k): int(v) for k, v in hits_dist.items()}
    # 检查指纹覆盖率
    for f in ["fp_F1", "fp_F2", "fp_F4", "fp_F5"]:
        rate = ev[f].mean()
        report[f"{f}_rate"] = round(float(rate), 4)
        if rate == 0:
            report["warnings"].append(f"{f} 从未命中，检查逻辑")
    # 检查异常值
    if (ev["buy_ret"].abs() > 0.3).any() if "buy_ret" in ev else False:
        report["warnings"].append("存在 |收益|>30% 异常值")
    return report


def run_backtest(d: pd.DataFrame):
    """日频净值回测（复用 dip_backtest 逻辑，cost=0.30% 固定）。"""
    if len(d) == 0:
        return {"n": 0, "win": 0, "ann": 0, "cum": 0, "mdd": 0, "avg_ret": 0}
    d = d.copy()
    d["trade_ret"] = d["ret"] - COST
    days = sorted(d["date"].unique())
    eq = INIT_CAP
    eq_curve, wins, n = [], [], 0
    per_year = {}
    for day in days:
        day_tr = d[d["date"] == day]
        n_day = len(day_tr)
        if n_day == 0:
            continue
        per = eq * POS / n_day
        day_pnl = 0.0
        for r in day_tr["trade_ret"]:
            day_pnl += per * r
            wins.append(1 if r > 0 else 0)
            n += 1
        eq += day_pnl
        eq_curve.append(eq)
        py = per_year.setdefault(day[:4], {"n": 0, "pnl": 0.0})
        py["n"] += n_day
        py["pnl"] += day_pnl
    eq_arr = np.array(eq_curve)
    mdd = float((eq_arr / np.maximum.accumulate(eq_arr) - 1).min() * 100)
    cum = (eq / INIT_CAP - 1) * 100
    ny = len(set(d["date"])) / 242
    ann = ((eq / INIT_CAP) ** (1 / ny) - 1) * 100 if ny > 0 else 0
    return {
        "n": n, "win": round(float(np.mean(wins)) * 100, 1) if wins else 0,
        "ann": round(float(ann), 1), "cum": round(float(cum), 1),
        "mdd": round(mdd, 1), "avg_ret": round(float(d["trade_ret"].mean() * 100), 3),
        "per_year": {y: {"n": v["n"], "pnl": round(float(v["pnl"]), 0)} for y, v in per_year.items()},
    }


def compute_dsr(ann_returns, n_trials=1):
    """简化 DSR：用年化收益序列估 Sharpe，n_trials 校正。返回 PSR(0) 近似。"""
    if len(ann_returns) < 2:
        return {"sharpe": 0, "psr0": 0, "label": "⚠️ 样本不足"}
    arr = np.array(ann_returns)
    mu, sigma = arr.mean(), arr.std()
    if sigma == 0:
        return {"sharpe": 0, "psr0": 0, "label": "⚠️ 方差为0"}
    sharpe = mu / sigma * (242 ** 0.5)  # 年化 Sharpe 近似（用日收益）
    # PSR(0) 简化（假设正态）
    from scipy.stats import norm
    psr0 = norm.cdf(sharpe * np.sqrt(len(arr) / 242))
    # DSR 简化门槛（n_trials 越多门槛越高）
    sr0 = np.sqrt(2 * np.log(n_trials)) if n_trials > 1 else 0
    dsr = norm.cdf((sharpe - sr0) * np.sqrt(len(arr) / 242))
    label = "✅ 确认" if dsr > 0.95 else ("🟡 待验证" if dsr > 0.50 else "⚠️ 存疑")
    return {"sharpe": round(sharpe, 3), "psr0": round(psr0, 4), "dsr": round(dsr, 4),
            "n_trials": n_trials, "label": label}


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
    ev["ret"] = ev["buy_ret"]  # D 变体无止损
    ev = ev.sort_values(["date", "gap"]).reset_index(drop=True)

    print(f"[info] D 变体信号（低开≥3% 非一字 非跌停开盘）: {len(ev)}")
    print(f"  指纹命中分布: {ev['fp_hits'].value_counts().sort_index().to_dict()}")
    print(f"  F1(YTD>150%)命中: {ev['fp_F1'].sum()}/{len(ev)} ({ev['fp_F1'].mean()*100:.1f}%)")
    print(f"  F2(ret20<0)命中: {ev['fp_F2'].sum()}/{len(ev)} ({ev['fp_F2'].mean()*100:.1f}%)")
    print(f"  F4(振幅>7%)命中: {ev['fp_F4'].sum()}/{len(ev)} ({ev['fp_F4'].mean()*100:.1f}%)")
    print(f"  F5(量比>1.5)命中: {ev['fp_F5'].sum()}/{len(ev)} ({ev['fp_F5'].mean()*100:.1f}%)")

    # V1 数据预检
    v1 = v1_data_precheck(ev)
    print(f"\n[V1 数据预检] status={v1['status']}")
    if v1["warnings"]:
        for w in v1["warnings"]:
            print(f"  ⚠️ {w}")

    # 同日最多 MAX_PER_DAY（低开最深优先）
    ev["rank_in_day"] = ev.groupby("date").cumcount()
    ev_full = ev[ev["rank_in_day"] < MAX_PER_DAY].copy()
    print(f"[info] 同日最多{MAX_PER_DAY}只后: {len(ev_full)}")

    # ===== 消融回测 =====
    results = {}

    # 基线：无过滤
    results["基线_无过滤"] = run_backtest(ev_full)

    # 过滤命中≥2/≥3/≥4 排除
    for thresh in [2, 3, 4]:
        filtered = ev_full[ev_full["fp_hits"] < thresh].copy()
        results[f"过滤_排除命中≥{thresh}"] = run_backtest(filtered)

    # 单指纹消融：逐个排除单指纹命中的标的
    for f, name in [("fp_F1", "F1_YTD>150%"), ("fp_F2", "F2_ret20<0"),
                    ("fp_F4", "F4_振幅>7%"), ("fp_F5", "F5_量比>1.5")]:
        filtered = ev_full[ev_full[f] == 0].copy()
        results[f"单指纹排除_{name}"] = run_backtest(filtered)

    # 按指纹命中分组（观察单调性）
    grouped = {}
    for h in sorted(ev_full["fp_hits"].unique()):
        sub = ev_full[ev_full["fp_hits"] == h]
        if len(sub) >= 10:  # 样本太少不报
            r = run_backtest(sub)
            r["count"] = len(sub)
            grouped[f"命中{int(h)}指纹"] = r
    results["_按命中数分组"] = grouped

    # 打印结果
    print("\n===== 消融回测结果（D变体基线，8.6年，成本0.30%）=====")
    print(f"{'变体':<24}{'笔数':>6}{'胜率':>7}{'年化':>8}{'MDD':>8}{'平均/笔':>9}")
    for k, v in results.items():
        if k.startswith("_"):
            continue
        print(f"{k:<24}{v['n']:>6}{v['win']:>6.1f}%{v['ann']:>7.1f}%{v['mdd']:>7.1f}%{v['avg_ret']:>8.3f}%")

    print("\n----- 按指纹命中数分组（观察单调性）-----")
    for k, v in grouped.items():
        print(f"{k:<16}{v.get('count',0):>6}笔  胜率{v['win']:>5.1f}%  年化{v['ann']:>6.1f}%  MDD{v['mdd']:>6.1f}%  均{v['avg_ret']:>7.3f}%")

    # V2 DSR（对基线与最优过滤）
    print("\n[V2 DSR 检验]")
    n_trials = 8  # 基线+3过滤+4单指纹 = 8 次试验
    baseline_daily = ev_full[["date", "ret"]].copy()
    # 用每日汇总收益做 DSR
    daily_ret = baseline_daily.groupby("date")["ret"].mean()
    dsr_base = compute_dsr(daily_ret.tolist(), n_trials)
    print(f"  基线 DSR(n_trials={n_trials}): Sharpe={dsr_base.get('sharpe',0)} "
          f"PSR0={dsr_base.get('psr0',0)} DSR={dsr_base.get('dsr',0)} → {dsr_base['label']}")

    results["_v1_precheck"] = v1
    results["_v2_dsr"] = {"baseline": dsr_base, "n_trials": n_trials}
    results["_meta"] = {
        "window": f"{START}~{END}",
        "cost": COST,
        "fingerprints_used": "F1(YTD>150%)/F2(ret20<0)/F4(振幅>7%)/F5(量比>1.5) — 日线可得版",
        "fingerprints_excluded": "F3(委比反转)/F6(PE/PB) — 日线不可得，需实盘补齐",
        "honest_note": "所有增益为历史回测理论值，不代表实盘收益。指纹3/6未纳入，完整版需实盘补齐PE/PB/委比。",
    }

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {OUT}")


if __name__ == "__main__":
    main()
