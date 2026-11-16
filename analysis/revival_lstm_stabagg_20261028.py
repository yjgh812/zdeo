# -*- coding: utf-8 -*-
"""revival_lstm_stabagg_20261028.py — 100种子 Borda百分位聚合 稳定选股（修复区分度不足的排序漂移）

主人令「稳定性要提到100%」诊断后修复:
  根因=模型头部打分挤在0.011带宽内, 相邻分差0.0002 << 种子噪声±0.0055(25倍) → 原始分排序第4席=噪声。
  修复=逐种子名次百分位(去尺度漂移) + 100种子Borda均值聚合 + 共识准入(进前4比例≥80%才入选)。
选股=均值百分位前4且共识≥80%; 不足如实缺额。落盘 evidence json + 覆盖明早0923计划文件。
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

N_MODELS = 100
SEEDS = [101 + 100 * i for i in range(N_MODELS)]  # 101..5001
OLD5 = {"300006", "300413", "300369", "603626", "300296"}

series, dates = w23.load_qfq()
uni, held = w23.base_universe(series, list(OLD5))
print(f"信号日={dates[-1]} 全宇宙={len(uni)} 模型数={N_MODELS}", flush=True)

pct_sum = {}    # code -> 名次百分位和(1.0=第一名)
in4_count = {}  # code -> 进前4次数
top50_sets = []
for i, sd in enumerate(SEEDS, 1):
    picks, meta = w23.m_lstm(series, uni, {"T": dates[-1], "dates": dates}, seed=sd, pool_n=10**9)
    n = len(picks)
    ranks = {c: n2 + 1 for n2, (c, _) in enumerate(picks)}
    for c, rk in ranks.items():
        pct_sum[c] = pct_sum.get(c, 0.0) + 1.0 - (rk - 1) / max(n - 1, 1)  # 名次百分位(高=前)
        if rk <= 4:
            in4_count[c] = in4_count.get(c, 0) + 1
    top50_sets.append(set(c for c, rk in ranks.items() if rk <= 50))
    if i % 10 == 0:
        ovs = []
        for a in range(len(top50_sets)):
            for b in range(a + 1, len(top50_sets)):
                ovs.append(len(top50_sets[a] & top50_sets[b]) / 50.0)
        print(f"[{i}/{N_MODELS}] 累计Top50重合率均值={np.mean(ovs)*100:.1f}% "
              f"进前4≥80%共识票={sum(1 for c,n in in4_count.items() if n/i>=0.8)}", flush=True)

mean_pct = {c: v / N_MODELS for c, v in pct_sum.items()}
agree4 = {c: n / N_MODELS for c, n in in4_count.items()}
ranked = sorted(mean_pct.items(), key=lambda x: -x[1])

print("\n=== 稳定层(进前4比例≥80%)按均值百分位排序 ===")
sel = []
for c, mp in ranked:
    a = agree4.get(c, 0.0)
    if a >= 0.8:
        nm = get_name(c) or "?"
        price = float(series[c]["close"][-1])
        print(f"  {nm:<8} {c} 均值百分位={mp:.3f} 进前4共识={a*100:.0f}% @{price:.2f}")
        sel.append((c, mp, a))
        if len(sel) >= 4:
            break

plan = []
for c, mp, a in sel:
    price = float(series[c]["close"][-1])
    nm = get_name(c) or "?"
    plan.append({"code": c, "name": nm, "source": "revival_lstm",
                 "price": round(price, 2), "volume": 100, "sector": "",
                 "prev_close": round(float(series[c]["close"][-2]), 2),
                 "note": f"主人令稳定性≥80%共识准入 Borda100种子 均值百分位={mp:.3f} 进前4共识={a*100:.0f}%",
                 "execute": True})
out = os.path.join(ROOT, "data", "revival_lstm4_plan_20261029.json")
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
ev = {"n_models": N_MODELS, "universe": len(uni),
      "stability_top50_pairs_mean": None,
      "selected": [{"code": c, "name": get_name(c) or "?", "mean_pct": round(mp, 4),
                    "agree4": round(a, 3)} for c, mp, a in sel]}
json.dump(ev, open(os.path.join(ROOT, "data", "revival_lstm_stabagg_evidence_20261028.json"),
                   "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n入选{len(plan)}只 已覆盖明早0923计划: {out}")
