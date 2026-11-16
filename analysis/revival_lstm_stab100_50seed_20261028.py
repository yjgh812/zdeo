# -*- coding: utf-8 -*-
"""revival_lstm_stab100_50seed_20261028.py — 50种子共识率选股（主人令「稳定性要提到100%」）

50个独立种子全宇宙重训, 每只股票统计「被多少个模型排进前4」= 共识率;
入选=共识率100%(50/50)的股票, 按平均名次取≤4只; 不足100%共识的宁缺毋滥(主人令:少于可以)。
确定性: 种子枚举固定+每种子内部确定 → 全程逐字节可复现。
"""
import importlib.util, os, sys, json
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "w23", os.path.join(ROOT, "analysis", "revival_wave23_scanner_20261028.py"))
w23 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w23)

from stock_name_lookup import get_name

N_MODELS = 50
SEEDS = [101 + 100 * i for i in range(N_MODELS)]  # 101,201,...,5001
OLD5 = {"300006", "300413", "300369", "603626", "300296"}

series, dates = w23.load_qfq()
uni, held = w23.base_universe(series, list(OLD5))
print(f"信号日={dates[-1]} 全宇宙={len(uni)} 模型数={N_MODELS}", flush=True)

top4_count = {}     # code -> 进前4的模型数
rank_sum = {}       # code -> 名次和(并列决胜)
top4_sets = []
for i, sd in enumerate(SEEDS, 1):
    picks, meta = w23.m_lstm(series, uni, {"T": dates[-1], "dates": dates}, seed=sd, pool_n=10**9)
    ranks = {c: n + 1 for n, (c, _) in enumerate(picks)}
    t4 = set(c for c, rk in ranks.items() if rk <= 4)
    top4_sets.append(t4)
    for c, rk in ranks.items():
        if rk <= 4:
            top4_count[c] = top4_count.get(c, 0) + 1
        rank_sum[c] = rank_sum.get(c, 0.0) + rk
    print(f"[{i}/{N_MODELS}] seed{sd}: 满4共识票={len(top4_count)} (当前100%共识: "
          f"{[get_name(c) or c for c, n in sorted(top4_count.items(), key=lambda x: -x[1]) if n == i]})",
          flush=True)

# 共识率选股
full_agree = [(c, 1.0, rank_sum[c] / N_MODELS) for c, n in top4_count.items() if n == N_MODELS]
full_agree.sort(key=lambda x: x[2])
final = full_agree[:4]

print("\n=== 共识率分布(前4入围票) ===")
for c, n in sorted(top4_count.items(), key=lambda x: -x[1])[:10]:
    nm = get_name(c) or "?"
    print(f"  {nm:<8} {c} 共识率 {n}/{N_MODELS} = {n/N_MODELS*100:.0f}%")

plan = []
print("\n=== 入选(共识率100%) ===")
for c, rate, mr in final:
    price = float(series[c]["close"][-1])
    nm = get_name(c) or "?"
    print(f"  {nm:<8} {c} @{price:.2f} 平均名次{mr:.1f}")
    plan.append({"code": c, "name": nm, "source": "revival_lstm",
                 "price": round(price, 2), "volume": 100, "sector": "",
                 "prev_close": round(float(series[c]["close"][-2]), 2),
                 "note": f"主人令稳定性100%: {N_MODELS}模型共识率100% 平均名次{mr:.1f}",
                 "execute": True})
if len(plan) < 4:
    print(f"⚠️ 100%共识票仅{len(plan)}只<4: 按主人令「少于可以」如实缺额, 不降低共识标准凑数")

out = os.path.join(ROOT, "data", "revival_lstm4_plan_20261029.json")
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("已覆盖明早0923计划文件:", out)
