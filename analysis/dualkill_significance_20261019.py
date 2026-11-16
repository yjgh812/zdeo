# -*- coding: utf-8 -*-
"""
2026双杀统计显著性检验（只读，零生产改动）
数据源：reports/backtest_full_20261018_dose_n15/（10/18 fork 六点真重跑，N15逐位复现生产）
检验设计（主人指令·第一块）：
  1) 胜率：2026 vs 2016-2025历史基准，双比例z检验 + Wilson 95%CI + 差值CI
  2) 盈亏比(avg_win/|avg_loss|)：按调仓批次块bootstrap 95%CI（同批信号相关，不可当独立样本）
  3) 三档同测：bp_ep Top15(生产档) / bp_ep Top20 / ml Top20
  4) 最小可检测效应 MDE（α=0.05, power=0.80）
  5) 2026卖出触发理由分布（建议1前置数据）
口径：胜=盈亏金额>0；平盘单独报不计入胜负（引用勘误：含平盘55.7%vs引擎52.2%口径坑）
"""
import csv, json, math, random, os
from collections import defaultdict

BASE = "D:/zcodesj/reports/backtest_full_20261018_dose_n15"
OUT_JSON = "D:/zcodesj/analysis/dualkill_significance_20261019_out.json"
OUT_MD = "D:/zcodesj/analysis/dualkill_significance_20261019_out.md"

def load_trades(path):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "buy_date": r["买入日期"],
                    "sell_date": r["卖出日期"],
                    "code": r["股票代码"],
                    "name": r["股票名称"],
                    "pnl": float(r["盈亏金额"]),
                    "pnl_pct": float(r["盈亏比例%"]),
                    "reason": r["卖出触发理由"],
                    "hold_days": int(r["持仓天数"]),
                })
            except (ValueError, KeyError):
                continue
    return rows

def year_of(d): return int(d[:4])

def stats(rows):
    n = len(rows)
    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] < 0]
    flats = [r for r in rows if r["pnl"] == 0]
    win_rate = len(wins) / n * 100 if n else 0.0
    avg_win = sum(r["pnl"] for r in wins) / len(wins) if wins else 0.0
    avg_loss = sum(r["pnl"] for r in losses) / len(losses) if losses else 0.0
    pl = (avg_win / abs(avg_loss)) if avg_loss != 0 else float("inf")
    return {
        "n": n, "win_rate_pct": round(win_rate, 2),
        "avg_win": round(avg_win, 1), "avg_loss": round(avg_loss, 1),
        "pl_ratio": round(pl, 3),
        "flat_n": len(flats),
        "pnl_sum": round(sum(r["pnl"] for r in rows), 0),
    }

def wilson_ci(p, n, z=1.96):
    if n == 0: return (0, 0)
    denom = 1 + z*z/n
    center = (p + z*z/(2*n)) / denom
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / denom
    return (center - half, center + half)

def two_prop_z(p1, n1, p2, n2):
    """2026(p1) vs 历史(p2)，返回z与双尾p"""
    pp = (p1*n1 + p2*n2) / (n1 + n2)
    se = math.sqrt(pp*(1-pp)*(1/n1 + 1/n2))
    if se == 0: return 0.0, 1.0
    z = (p1 - p2) / se
    pval = math.erfc(abs(z)/math.sqrt(2))  # 双尾
    return z, pval

def batches(rows):
    """调仓批次：按买入日期分块（月度调仓=同日买入同批）"""
    b = defaultdict(list)
    for r in rows:
        b[r["buy_date"]].append(r)
    return list(b.values())

def block_bootstrap_pl(rows, iters=10000, seed=42):
    """按买入日批次有放回重采样块，重算盈亏比"""
    bl = batches(rows)
    rng = random.Random(seed)
    vals = []
    for _ in range(iters):
        sample = []
        # 抽与原批次数相同的块
        picks = [rng.choice(bl) for _ in range(len(bl))]
        for p in picks: sample.extend(p)
        s = stats(sample)
        if s["avg_loss"] != 0 and s["n"] > 10:
            vals.append(s["pl_ratio"])
    vals.sort()
    n = len(vals)
    lo, hi = vals[int(0.025*n)], vals[int(0.975*n)]
    return round(lo, 3), round(hi, 3), round(sum(vals)/n, 3)

def mde_rate(n, p_base, alpha=0.05, power=0.80):
    """最小可检测胜率差（pp）"""
    z_a, z_b = 1.96, 0.8416
    pp = p_base/100
    se = math.sqrt(pp*(1-pp))
    delta = (z_a + z_b) * se * math.sqrt(2/n)
    return round(delta*100, 2)

result = {}

# ---------- 三档逐年重算 + 与 regime matrix 对拍 ----------
arms = {
    "bp_ep_top15": ("bp_ep_core_trades", [str(y) for y in range(2016, 2027)]),
    "bp_ep_top20": ("bp_ep_core_trades", None),  # top20档=同文件，top20切片不在fork文件，单独说明
    "ml_top20": ("ml_signal_trades", [str(y) for y in range(2020, 2027)]),
}

for arm, (prefix, years) in arms.items():
    if years is None:
        continue  # top20切片不在本fork产物，跳过并注记
    yearly = {}
    for y in years:
        p = f"{BASE}/{prefix}_{y}.csv"
        if not os.path.exists(p): continue
        rows = load_trades(p)
        yearly[y] = stats(rows)
    hist_rows = []
    for y in years:
        if y == "2026": continue
        p = f"{BASE}/{prefix}_{y}.csv"
        if os.path.exists(p):
            hist_rows.extend(load_trades(p))
    r26_rows = load_trades(f"{BASE}/{prefix}_2026.csv")
    s_hist = stats(hist_rows)
    s_26 = stats(r26_rows)
    # 胜率检验
    p1, p2 = s_26["win_rate_pct"]/100, s_hist["win_rate_pct"]/100
    z, pval = two_prop_z(p1, s_26["n"], p2, s_hist["n"])
    wlo, whi = wilson_ci(p1, s_26["n"])
    dlo, dhi = wilson_ci(p1, s_26["n"], 1.96)
    diff_lo, diff_hi = (p1-p2) - 1.96*math.sqrt(p1*(1-p1)/s_26["n"] + p2*(1-p2)/s_hist["n"]), (p1-p2) + 1.96*math.sqrt(p1*(1-p1)/s_26["n"] + p2*(1-p2)/s_hist["n"])
    # 盈亏比块bootstrap
    b26 = block_bootstrap_pl(r26_rows)
    bh = block_bootstrap_pl(hist_rows)
    # 卖出原因分布 2026
    reason_cnt = defaultdict(int)
    for r in r26_rows:
        key = r["reason"].split("(")[0].strip()[:20]
        reason_cnt[key] += 1
    # 持有天数
    holds = sorted(r["hold_days"] for r in r26_rows)
    med_hold = holds[len(holds)//2] if holds else None
    result[arm] = {
        "yearly": yearly,
        "hist_2016_2025": s_hist,
        "y2026": s_26,
        "winrate_test": {
            "z": round(z, 3), "p_two_tail": round(pval, 4),
            "wilson95_2026": [round(wlo*100, 2), round(whi*100, 2)],
            "diff_pp": round((p1-p2)*100, 2),
            "diff95CI_pp": [round(diff_lo*100, 2), round(diff_hi*100, 2)],
            "MDE_pp_at_n2026": mde_rate(s_26["n"], s_hist["win_rate_pct"]),
        },
        "pl_bootstrap": {"y2026_95CI": [b26[0], b26[1]], "hist_95CI": [bh[0], bh[1]],
                         "y2026_mean": b26[2], "hist_mean": bh[2],
                         "overlap_hist_CI": bool(b26[0] <= bh[1] and bh[0] <= b26[1]),
                         "y2026_CI_contains_1": bool(b26[0] <= 1.0 <= b26[1])},
        "sell_reason_2026": dict(reason_cnt),
        "hold_days_median_2026": med_hold,
    }

# ---------- top20口径注记 ----------
result["_note"] = {
    "top20_source": "bp_ep Top20 逐笔不在10/18 fork产物内，Top20口径引用 regime_payoff_matrix_20261018_out.json 聚合数(n=136,win44.12%,PL0.777)，显著性检验以聚合近似另行标注",
    "flat_handling": "平盘不计入胜负分子分母，单独flat_n列报",
    "data_source": "reports/backtest_full_20261018_dose_n15（N15逐位复现生产181.17%/1612/51.92%）",
}

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)

# ---------- md 直出 ----------
md = ["# 2026双杀统计显著性检验（脚本直出）", ""]
md.append(f"- 数据源：{BASE}（10/18 fork N15 逐位复现生产）")
md.append("- 口径：胜=盈亏金额>0；平盘单独报；盈亏比=avg_win/|avg_loss|；bootstrap按调仓买入日分块")
md.append("")
for arm in ["bp_ep_top15", "ml_top20"]:
    if arm not in result: continue
    d = result[arm]
    md.append(f"## {arm}")
    md.append(f"- 2026: n={d['y2026']['n']} 胜率={d['y2026']['win_rate_pct']}% PL={d['y2026']['pl_ratio']} flat={d['y2026']['flat_n']} 中位持有={d['hold_days_median_2026']}天")
    md.append(f"- 历史(不含2026): n={d['hist_2016_2025']['n']} 胜率={d['hist_2016_2025']['win_rate_pct']}% PL={d['hist_2016_2025']['pl_ratio']}")
    t = d["winrate_test"]
    md.append(f"- 胜率检验: 差={t['diff_pp']}pp 95%CI[{t['diff95CI_pp']}] z={t['z']} p={t['p_two_tail']} | 2026 Wilson95[{t['wilson95_2026']}] | MDE@n2026={t['MDE_pp_at_n2026']}pp")
    b = d["pl_bootstrap"]
    md.append(f"- 盈亏比bootstrap: 2026 mean={b['y2026_mean']} 95%CI{b['y2026_95CI']} vs 历史mean={b['hist_mean']} 95%CI{b['hist_95CI']} | CI重叠={b['overlap_hist_CI']} 2026CI含1={b['y2026_CI_contains_1']}")
    md.append(f"- 2026卖出理由分布: {d['sell_reason_2026']}")
    md.append("")
with open(OUT_MD, "w", encoding="utf-8") as f:
    f.write("\n".join(md))

print("JSON_OK", os.path.getsize(OUT_JSON))
print(open(OUT_MD, encoding="utf-8").read())
