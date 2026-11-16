# -*- coding: utf-8 -*-
"""B3 重启三信号·历史回测可行性原型 · 2026-10-19 「继续」令第三轮
数据: reports/backtest_full_20261018_dose_n15/{bp_ep_core_trades_full.csv(2016起), ml_signal_trades_full.csv(2020起)}
尺度适配声明: prereg 定义的 S2/S3 是20日/交易日尺度; 本原型用月批次尺度(与生产调仓同构)做可行性,
   日线级组合序列需先跑回测件(后续工程)。S3用生产fork净口径代理raw(2024-2026才有L0a_raw)。
S1(OOS影子8周正+60笔): 低位修复族影子10/20才激活, 无历史 → 本轮N/A。
评估纪律: 阈值判定主段=2018-2025(热身24期); 2026段单列只陈述不进CI("2026只验证一次")。
"""
import csv
import json
import sys
import random
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")
BASE = "D:/zcodesj/reports/backtest_full_20261018_dose_n15"
OUT = "D:/zcodesj/analysis/b3_restart_feasibility_20261019_out.json"

def batches(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    by = defaultdict(list)
    for r in rows:
        by[r["买入日期"]].append(float(r["盈亏比例%"]))
    return {d: sum(v) / len(v) for d, v in sorted(by.items())}

bp_m = {d[:7]: q for d, q in batches(f"{BASE}/bp_ep_core_trades_full.csv").items()}
ml_m = {d[:7]: q for d, q in batches(f"{BASE}/ml_signal_trades_full.csv").items()}
common = sorted(set(bp_m) & set(ml_m))
print(f"共同月批次: {len(common)} ({common[0]}~{common[-1]})")

def roll(seq_ym, i, k=2):
    w = [seq_ym[y] for y in common[max(0, i - k + 1):i + 1]]
    return sum(w) / len(w)

combo = {ym: 0.5 * bp_m[ym] + 0.5 * ml_m[ym] for ym in common}

# ---------- S3: 引擎复苏检测(月批次适配: 双引擎roll2由负转正且持续>=2期) ----------
s3_events = []  # (ym, 触发时点)
state = "unknown"
run_pos = 0
for i, ym in enumerate(common):
    if ym[:4] == "2026" and ym <= "2026-09":
        pass  # 2026照算, 单列报告
    rb, rm = roll(bp_m, i), roll(ml_m, i)
    both_pos = rb > 0 and rm > 0
    both_neg = rb < 0 and rm < 0
    if state in ("unknown", "cold"):
        if both_neg:
            state = "cold"
            run_pos = 0
        elif both_pos:
            run_pos += 1
            if run_pos >= 2:
                state = "hot"
                s3_events.append(ym)
        else:
            run_pos = 0
    elif state == "hot":
        if both_neg:
            state = "cold"
            run_pos = 0
print(f"S3 复苏触发: {len(s3_events)} 次: {s3_events}")

# S3 前向检验: 触发后3期 combo 均值 vs 无条件3期均值 (主段2018-2025; 2026单列)
def fwd3(ym_list, start_i):
    w = [combo[common[j]] for j in range(start_i + 1, min(start_i + 4, len(common)))]
    return sum(w) / len(w) if len(w) == 3 else None

main_triggers = [ym for ym in s3_events if "2018" <= ym <= "2025"]
y2026_triggers = [ym for ym in s3_events if ym >= "2026"]
fwd_main = [fwd3(common, common.index(ym)) for ym in main_triggers]
fwd_main = [x for x in fwd_main if x is not None]
fwd_all = [fwd3(common, i) for i in range(24, len(common) - 3)]
fwd_all = [x for x in fwd_all if x is not None]
res = {"S3": {"n_trigger_main": len(main_triggers), "triggers_main": main_triggers,
              "triggers_2026": y2026_triggers,
              "fwd3_mean": round(sum(fwd_main) / len(fwd_main), 3) if fwd_main else None,
              "uncond_fwd3_mean": round(sum(fwd_all) / len(fwd_all), 3)}}
if len(fwd_main) >= 10:
    random.seed(42)
    obs = sum(fwd_main) / len(fwd_main) - sum(fwd_all) / len(fwd_all)
    boots = []
    pool = fwd_all[:]
    for _ in range(1000):
        s = [random.choice(pool) for _ in range(len(fwd_main))]
        boots.append(sum(s) / len(s) - sum(fwd_all) / len(fwd_all))
    boots.sort()
    lo, hi = boots[24], boots[974]
    res["S3"]["diff"] = round(obs, 3)
    res["S3"]["bootstrap95CI"] = [round(lo, 3), round(hi, 3)]
    res["S3"]["verdict"] = "显著为正(CI下界>0)=复苏后确实更好" if lo > 0 else ("显著为负" if hi < 0 else "CI跨0=不可判")
print(f"S3 主段触发={len(main_triggers)} 前向3期均值={res['S3']['fwd3_mean']} vs 无条件={res['S3']['uncond_fwd3_mean']}")
if "verdict" in res["S3"]:
    print(f"   差异={res['S3']['diff']} CI={res['S3']['bootstrap95CI']} → {res['S3']['verdict']}")
print(f"   2026段触发: {y2026_triggers}")

# ---------- S2: 波动率结构突变(月批次适配: combo滚动3期std > 扩展P75 且持续>=2期) ----------
import math
def rolling_std(ym_list, i, k=3):
    w = [combo[y] for y in ym_list[max(0, i - k + 1):i + 1]]
    if len(w) < 2:
        return None
    m = sum(w) / len(w)
    return math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1))

s2_events = []
state = "normal"
run_hi = 0
for i, ym in enumerate(common):
    if i < 24:
        continue
    hist = [rolling_std(common, j) for j in range(24, i)]
    hist = [h for h in hist if h is not None]
    v = rolling_std(common, i)
    if v is None or len(hist) < 24:
        continue
    hist_sorted = sorted(hist)
    p75 = hist_sorted[int(len(hist_sorted) * 0.75)]
    hi = v > p75
    if state == "normal":
        run_hi = run_hi + 1 if hi else 0
        if run_hi >= 2:
            state = "alert"
            s2_events.append(ym)
    else:
        if not hi:
            state = "normal"
            run_hi = 0
print(f"S2 高波结构触发: {len(s2_events)} 次: {s2_events}")

main2 = [ym for ym in s2_events if "2018" <= ym <= "2025"]
y2026_2 = [ym for ym in s2_events if ym >= "2026"]
fwd2 = [fwd3(common, common.index(ym)) for ym in main2]
fwd2 = [x for x in fwd2 if x is not None]
res["S2"] = {"n_trigger_main": len(main2), "triggers_main": main2, "triggers_2026": y2026_2,
             "fwd3_mean": round(sum(fwd2) / len(fwd2), 3) if fwd2 else None,
             "uncond_fwd3_mean": res["S3"]["uncond_fwd3_mean"]}
if len(fwd2) >= 10:
    random.seed(43)
    obs = sum(fwd2) / len(fwd2) - sum(fwd_all) / len(fwd_all)
    boots = []
    for _ in range(1000):
        s = [random.choice(fwd_all) for _ in range(len(fwd2))]
        boots.append(sum(s) / len(s) - sum(fwd_all) / len(fwd_all))
    boots.sort()
    lo, hi = boots[24], boots[974]
    res["S2"]["diff"] = round(obs, 3)
    res["S2"]["bootstrap95CI"] = [round(lo, 3), round(hi, 3)]
    res["S2"]["verdict"] = "显著为正" if lo > 0 else ("显著为负" if hi < 0 else "CI跨0=不可判")
print(f"S2 主段触发={len(main2)} 前向3期均值={res['S2']['fwd3_mean']}")
if "verdict" in res["S2"]:
    print(f"   差异={res['S2']['diff']} CI={res['S2']['bootstrap95CI']} → {res['S2']['verdict']}")
print(f"   2026段触发: {y2026_2}")

res["_meta"] = {
    "scale_adaptation": "月批次尺度原型; prereg的20日交易日尺度需日线级组合序列(后续回测件)",
    "S3_proxy": "生产fork净口径批次(非raw; L0a_raw仅2024-2026)",
    "S1": "N/A: 低位修复族影子2026-10-20才激活, 无历史",
    "eval_main": "2018-2025(热身24期)", "y2026_policy": "单列陈述不进CI(2026只验证一次)",
}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("[output]", OUT)
