# -*- coding: utf-8 -*-
r"""
holding_risk_backtest.py — 持仓风险评分全市场回测验证（2026-08-10 知微）
=============================================================
核心问题：评分>阈值的标的，后续5日收益是否显著为负？

方法：
  1. 全市场扫描（合规宇宙），每日计算所有股票的风险评分
  2. 按评分分档（0-0.2/0.2-0.4/0.4-0.6/0.6+），统计各档后续5日平均收益
  3. 单调性检验：高分档收益应更差（如果评分有效）
  4. 模拟"评分>0.6卖出"的回测，对比持有不动

诚实标注：
  - 全市场扫描计算量大，限制为最近2年（2024-08 ~ 2026-08）
  - 每日全市场评分不现实（5000股×500日=250万次），改为抽样：
    (a) 从 dip_backtest 信号池取标的（低开承接买入后的持仓）
    (b) 对每个信号标的，计算买入次日的风险评分
    (c) 统计高分vs低分标的的后续5日收益
  - V1 数据预检 + V2 DSR
输出：D:\zcodesj\attribution\holding_risk_backtest_result.json
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj\attribution")
from holding_risk_score import compute_holding_risk

TDX = Path(r"D:\anzhuang\tdx\vipdoc")
START = "20240801"  # 最近2年（控制计算量）
END = "20260807"
OUT = Path(r"D:\zcodesj\attribution\holding_risk_backtest_result.json")

STOCK_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301")
DAY_DTYPE = np.dtype([("date", "<u4"), ("open", "<u4"), ("high", "<u4"), ("low", "<u4"),
                      ("close", "<u4"), ("amount", "<f4"), ("volume", "<u4"), ("unused", "<u4")])

# 风险评分档位
TIERS = [("safe", 0.0, 0.2), ("low", 0.2, 0.4), ("mid", 0.4, 0.6), ("high", 0.6, 1.01)]


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


def get_index_ret20():
    """读取上证指数20日收益率序列（用于R2相对弱势）。"""
    idx_path = TDX / "sh" / "lday" / "sh000001.day"
    if not idx_path.exists():
        return {}
    idx_df = read_day(idx_path)
    idx_df = idx_df[idx_df.index >= pd.Timestamp("20240701")]
    ret20_series = idx_df["close"] / idx_df["close"].shift(21) - 1
    return dict(zip(ret20_series.index.strftime("%Y-%m-%d"), ret20_series.values))


def collect_risk_scores(args):
    """对单只股票，在每个交易日计算风险评分 + 后续5日收益。"""
    code, market = args
    day_path = TDX / market / "lday" / f"{market}{code}.day"
    if not day_path.exists() or not code.startswith(STOCK_PREFIXES):
        return []
    try:
        day = read_day(day_path)
    except Exception:
        return []
    if len(day) < 65:
        return []
    day = day[day.index >= pd.Timestamp("20240601")]  # 多读1月缓冲
    if len(day) < 65:
        return []

    records = []
    for i in range(64, len(day) - 6):  # 留5日算未来收益 + 起始65行
        dt = day.index[i]
        if dt < pd.Timestamp(START):
            continue
        # 截止当日的日线（含），用于评分
        df_until = day.iloc[: i + 1].copy()
        # 未来5日收益（T+1买入价到T+5收盘）
        future_ret5 = day["close"].iloc[i + 5] / day["close"].iloc[i] - 1
        future_ret1 = day["close"].iloc[i + 1] / day["close"].iloc[i] - 1

        # 为控制计算量，只对部分日期采样（每周一）
        if dt.dayofweek != 0:  # 周一
            continue

        result = compute_holding_risk(df_until, index_ret20=0.0)  # index_ret20 简化为0（全市场横向对比）
        if "error" in result:
            continue
        records.append({
            "code": code, "date": str(dt.date()),
            "score": result["score"],
            "R1": int(result["R1_yindie"]),
            "R2": int(result["R2_weakness"]),
            "R3": int(result["R3_breakdown"]),
            "R4": int(result["R4_volatility"]),
            "R5": int(result["R5_volume"]),
            "future_ret5": float(future_ret5),
            "future_ret1": float(future_ret1),
        })
    return records


def main():
    # 读取指数 ret20（用于 R2，但全市场横向对比时简化为0）
    # 注：严格做需逐日传 index_ret20，这里简化，R2 用 stock_ret20 < -5% 绝对阈值替代
    print("[info] 开始全市场风险评分回测...", flush=True)

    codes = []
    for market in ("sh", "sz"):
        lday = TDX / market / "lday"
        if lday.exists():
            codes += [(f.stem[2:], market) for f in lday.glob(f"{market}*.day")]
    print(f"[info] 扫描 {len(codes)} 只（窗口 {START}~{END}，每周一采样）", flush=True)

    records = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(collect_risk_scores, c) for c in codes]
        done = 0
        for f in as_completed(futs):
            records.extend(f.result())
            done += 1
            if done % 2000 == 0:
                print(f"  进度 {done}/{len(codes)}", flush=True)

    df = pd.DataFrame(records)
    if len(df) == 0:
        print("[warn] 无数据")
        return
    print(f"[info] 总采样: {len(df)} 条（{df['code'].nunique()} 只股票）")

    # V1 数据预检
    v1 = {"status": "pass", "warnings": []}
    if len(df) < 1000:
        v1["warnings"].append(f"样本偏少({len(df)})")
    score_dist = df["score"].value_counts(bins=[0, 0.2, 0.4, 0.6, 1.01], sort=False).to_dict()
    v1["score_distribution"] = {str(round(k.left, 1)): int(v) for k, v in score_dist.items()}
    print(f"\n[V1] {v1['status']}, 评分分布: {v1['score_distribution']}")

    # ===== 按评分分档统计未来收益 =====
    print("\n===== 各档未来5日收益统计 =====")
    print(f"{'档位':<12}{'样本':>8}{'未来5日均':>10}{'未来5日中位':>12}{'胜率':>8}{'未来1日均':>10}")
    tier_results = {}
    for name, lo, hi in TIERS:
        sub = df[(df["score"] >= lo) & (df["score"] < hi)]
        if len(sub) < 50:
            print(f"{name:<12}{len(sub):>8}  (样本不足)")
            continue
        avg5 = sub["future_ret5"].mean()
        med5 = sub["future_ret5"].median()
        win = (sub["future_ret5"] > 0).mean()
        avg1 = sub["future_ret1"].mean()
        tier_results[name] = {"n": len(sub), "avg_ret5": round(avg5, 4),
                              "median_ret5": round(med5, 4), "win_rate": round(win, 4),
                              "avg_ret1": round(avg1, 4)}
        print(f"{name:<12}{len(sub):>8}{avg5*100:>9.2f}%{med5*100:>11.2f}%{win*100:>7.1f}%{avg1*100:>9.2f}%")

    # 单调性检验：高分档收益应更差
    tiers_ordered = [t for t, _, _ in TIERS if t in tier_results]
    avg_rets = [tier_results[t]["avg_ret5"] for t in tiers_ordered]
    is_monotone = all(avg_rets[i] >= avg_rets[i + 1] for i in range(len(avg_rets) - 1))
    print(f"\n单调性（高分→低收益）: {'是 ✅' if is_monotone else '否 ⚠️'}（avg_rets={[round(r*100,2) for r in avg_rets]}）")

    # ===== 各维度单独贡献 =====
    print("\n===== 各维度单独命中 vs 未命中的未来5日收益对比 =====")
    print(f"{'维度':<10}{'命中样本':>8}{'命中均5d':>10}{'未命中样本':>10}{'未命中均5d':>12}{'差异':>8}")
    dim_results = {}
    for dim in ["R1", "R2", "R3", "R4", "R5"]:
        hit = df[df[dim] == 1]
        miss = df[df[dim] == 0]
        if len(hit) < 50 or len(miss) < 50:
            print(f"{dim:<10}{len(hit):>8}  (命中或未命中样本不足)")
            continue
        hit_avg = hit["future_ret5"].mean()
        miss_avg = miss["future_ret5"].mean()
        diff = hit_avg - miss_avg
        dim_results[dim] = {"hit_n": len(hit), "hit_avg5": round(hit_avg, 4),
                            "miss_n": len(miss), "miss_avg5": round(miss_avg, 4),
                            "diff": round(diff, 4)}
        flag = "✅有效" if diff < -0.005 else ("⚠️无效" if abs(diff) < 0.005 else "❌反向")
        print(f"{dim:<10}{len(hit):>8}{hit_avg*100:>9.2f}%{len(miss):>10}{miss_avg*100:>11.2f}%{diff*100:>7.2f}% {flag}")

    # ===== V2 DSR（高分档 vs 低分档收益差异的统计检验） =====
    from scipy.stats import mannwhitneyu
    high = df[df["score"] >= 0.6]["future_ret5"]
    low = df[df["score"] < 0.2]["future_ret5"]
    n_trials = 9  # 4档+5维度 = 9次试验
    if len(high) >= 30 and len(low) >= 30:
        stat, pval = mannwhitneyu(high, low, alternative="less")
        dsr_label = "✅ 显著" if pval < 0.05 / n_trials else ("🟡 边际" if pval < 0.05 else "⚠️ 不显著")
        print(f"\n[V2 DSR] 高分档 vs 低分档 Mann-Whitney U p={pval:.4f} (Bonferroni α={0.05/n_trials:.4f}) → {dsr_label}")
    else:
        pval = None
        dsr_label = "样本不足"
        print(f"\n[V2] 高分档{len(high)}或低分档{len(low)}样本不足")

    results = {
        "tier_results": tier_results,
        "dimension_results": dim_results,
        "monotonicity": {"is_monotone": bool(is_monotone), "avg_rets": avg_rets},
        "v1_precheck": v1,
        "v2_dsr": {"p_value": pval, "n_trials": n_trials, "label": dsr_label,
                   "high_n": len(high), "low_n": len(low)},
        "meta": {
            "window": f"{START}~{END}",
            "sampling": "每周一采样（控制计算量）",
            "honest_note": "评分维度权重来自经验库定性提炼非统计优化；R2简化为绝对阈值(非实时大盘对比)；全市场周一采样",
        }
    }
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n[saved] {OUT}")


if __name__ == "__main__":
    main()
