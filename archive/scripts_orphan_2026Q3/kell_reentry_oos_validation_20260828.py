# -*- coding: utf-8 -*-
"""
kell_reentry_oos_validation_20260828.py — K1 重入 A 规则·逐年 OOS 稳健性验证（研究，只读）
============================================================
背景：ML20 短线簿重入 A 经成本建模确认正期望（N5 +7.88% 净/中位 +5.37%）。prereg 承诺的下一步=
      逐年 out-of-time 稳健性检验（rule 固定无参数 → one-vote，按年分解看是否稳定边沿 vs 某段行情侥幸）。

方法：复用 kell_reentry_cost_model 的真实交易模拟（触发日收盘进场 → S-5 实际退出或 +20d，扣 0.26% 成本），
  按止损发生年分桶，报告每年命中数/净均值/中位/胜率/S5率；若多数年份为正 → 稳健边沿；
  若仅个别年份为正或随 regime 大幅摆动 → 周期侥幸，不成立。
对照：TOP15 价值簿（预期全证否）。
"""
import sys, statistics, time
sys.path.insert(0, r"D:/zcodesj")
from collections import defaultdict
from kell_reentry_cost_model_20260828 import load_events, load_qfq, simulate_reentry, COST

REPORT = r"D:\zcodesj\reports\K1_REENTRY_OOS_VALIDATION_20260828.md"
BOOKS = [
    ("ML20(短线动量)", r"D:\zcodesj\reports\round10_adopted_combo_20260827\ml_hold20_pitv2_trades_20*.csv"),
    ("TOP15(bp_ep价值)", r"D:\zcodesj\reports\round10_adopted_combo_20260827\bp_ep_top15_pitv2_trades_20*.csv"),
]

def run(tag, pat):
    evs = load_events(pat)
    qfq = load_qfq(sorted({e["code"] for e in evs}))
    # per anchor × year: hits with realized net ret
    per = {a: defaultdict(list) for a in ("A", "B")}
    for ev in evs:
        series = qfq.get(ev["code"])
        if not series: continue
        year = ev["stop_date"][:4]
        for a in ("A", "B"):
            r = simulate_reentry(ev, series, 20, a)
            if r:
                per[a][year].append(r["ret"])
    return per, len(evs)

def main():
    t0 = time.time()
    md = ["# K1 重入 A 规则 · 逐年 OOS 稳健性验证", "",
          f"> **日期**：2026-08-28 | **性质**：ML20 分歧的正式 OOS 验证（prereg 后续，rule 固定无参数 one-vote）",
          f"> **口径**：触发日收盘进场 → S-5 实际退出或持有至+20d；扣往返成本 {COST*100:.2f}%；按止损年分桶",
          "> **判据**：多数年份为正且量级稳定 → 稳健边沿可上报主人；仅个别年份正 → 周期侥幸，关闭分歧。", ""]
    for tag, pat in BOOKS:
        per, n_ev = run(tag, pat)
        md.append(f"## {tag}（止损事件 {n_ev} 笔）")
        md.append("")
        for a in ("A", "B"):
            years = sorted(per[a])
            if not years:
                continue
            md.append(f"### 触发 {a}（逐年）")
            md.append("")
            md.append("| 年份 | 命中 n | 净均值 | 净中位 | 胜率 |")
            md.append("|---|---|---|---|---|")
            pos_years = 0
            for y in years:
                v = per[a][y]
                mean = statistics.mean(v)
                if mean > 0:
                    pos_years += 1
                md.append(f"| {y} | {len(v)} | {mean*100:+.2f}% | {statistics.median(v)*100:+.2f}% | "
                          f"{sum(1 for x in v if x>0)/len(v)*100:.0f}% |")
            md.append(f"| **逐年正向占比** | — | **{pos_years}/{len(years)} 年均值为正** | | |")
            md.append("")
    md.append("## 判读")
    md.append("- 重入 A 在 ML20 上逐年一致性若 ≥ 60% 年份为正 → 稳健边沿，上报主人立项评审（独立 prereg + WFA 门禁）。")
    md.append("- TOP15 价值簿预期各年为负/近零，作为对照确认簿别特异性。")
    md.append("- 注：ML20 簿仅 2020-2026（round10 该策略起始），年样本最少 2026 年（部分年）。")
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"[K1-OOS] 报告已写: {REPORT} ({time.time()-t0:.0f}s)")

if __name__ == "__main__":
    main()
