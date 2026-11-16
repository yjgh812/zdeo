# -*- coding: utf-8 -*-
"""revival_lstm_ensemble_20261028.py — LSTM 多种子集成稳定选股（主人令「随机性=不稳定性,要稳定的,怎么做」）

配方=Wave23 revival_lstm 同款(5特征/60窗/rank标签/Top800/3epochs), 10个固定种子分别重训,
每只股票取10模型打分均值排序 = 集成稳定名单; 稳定性指标=跨种子两两Top50重合率均值+全排序秩相关。
输出: 前4只(剔除原5只/持仓) → data/revival_lstm4_plan_20261029.json 覆盖更新(RevivalLstm4_0923读取同名文件)。
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

SEEDS = [101 * (i + 1) for i in range(20)]  # 20261028 主人令: 20种子 × 全宇宙(无池盲区, pool_n=10**9=全量3575)
OLD5 = {"300006", "300413", "300369", "603626", "300296"}

series, dates = w23.load_qfq()
uni, held = w23.base_universe(series, list(OLD5))
print(f"信号日={dates[-1]} 宇宙={len(uni)}")

score_sum = {}
top50_sets = []
for i, sd in enumerate(SEEDS, 1):
    picks, meta = w23.m_lstm(series, uni, {"T": dates[-1], "dates": dates}, seed=sd, pool_n=10**9)
    ranks = {c: n + 1 for n, (c, _) in enumerate(picks)}
    for c, rk in ranks.items():
        score_sum.setdefault(c, []).append(len(ranks) - rk)  # 越前名次分越高
    top50_sets.append(set(c for c, rk in ranks.items() if rk <= 50))
    print(f"seed{sd}: 训练{meta.get('n_train')}票 打分{len(ranks)}只(全宇宙)", flush=True)

# 稳定性指标: 跨种子两两Top50重合率均值
pairs, ovs = [], []
for a in range(len(top50_sets)):
    for b in range(a + 1, len(top50_sets)):
        ov = len(top50_sets[a] & top50_sets[b]) / 50.0
        pairs.append(ov)
print(f"稳定性=跨种子Top50两两重合率均值: {np.mean(pairs)*100:.1f}% (>=60%稳定 / <40%排序即噪声)")

mean_score = {c: float(np.mean(v)) for c, v in score_sum.items()}
ranked = sorted(mean_score.items(), key=lambda x: -x[1])
print("\n=== 集成排名前10 ===")
from stock_name_lookup import get_name
for c, v in ranked[:10]:
    print(f"  {get_name(c) or '?':<8} {c} 集成分={v:.1f}/{len(SEEDS)}种子")

top4 = ranked[:4]
plan = []
for c, v in top4:
    price = float(series[c]["close"][-1])
    plan.append({"code": c, "name": get_name(c) or "?", "source": "revival_lstm",
                 "price": round(price, 2), "volume": 100, "sector": "",
                 "prev_close": round(float(series[c]["close"][-2]), 2),
                 "note": f"主人令稳定集成选股 10种子集成分={v:.1f}", "execute": True})
out = os.path.join(ROOT, "data", "revival_lstm4_plan_20261029.json")
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n=== 集成稳定新4只（已覆盖明早0923计划文件）===")
for p in plan:
    print(f"  {p['name']:<8} {p['code']} @{p['price']}")
print("产物:", out)
