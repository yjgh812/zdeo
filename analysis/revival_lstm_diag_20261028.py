# -*- coding: utf-8 -*-
"""revival_lstm_diag_20261028.py — 100%共识为0的诊断器（主人令「仔细找找」）

16种子全宇宙重训, 每模型落盘前12名(代码+原始分数), 量化:
  ①各票进前4的种子覆盖与落选时"被谁以多大分差"替代
  ②前4边界的分差密度(区分度不足=模型弱; 区分度足而翻转=边界股真噪声)
  ③唯一进入过前4的股票总数(排名不稳定面)
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

N_MODELS = 16
SEEDS = [101 + 100 * i for i in range(N_MODELS)]
OLD5 = {"300006", "300413", "300369", "603626", "300296"}

series, dates = w23.load_qfq()
uni, held = w23.base_universe(series, list(OLD5))
print(f"信号日={dates[-1]} 全宇宙={len(uni)}", flush=True)

per_seed = []   # [{code: (rank, score)}]
all_scores = {}  # code -> list of scores across seeds
for i, sd in enumerate(SEEDS, 1):
    picks, meta = w23.m_lstm(series, uni, {"T": dates[-1], "dates": dates}, seed=sd, pool_n=10**9)
    d = {}
    for c, reason in picks:
        # reason形如 "LSTM重训fwd20rank=0.4800"
        try:
            v = float(reason.split("=")[-1])
        except Exception:
            v = 0.0
        d[c] = (len(d) + 1, v)
    per_seed.append(d)
    for c, (rk, v) in d.items():
        all_scores.setdefault(c, []).append((rk, v))
    top4 = sorted(d.items(), key=lambda x: -x[1][1])[:4]
    print(f"[{i}/{N_MODELS}] seed{sd} 前4: "
          + ", ".join(f"{get_name(c) or c}({v:.4f})" for c, (rk, v) in top4), flush=True)

# 分析1: 唯一进过前4的股票数 + 各票进前4次数
in4_count = {}
for d in per_seed:
    t4 = sorted(d.items(), key=lambda x: -x[1][1])[:4]
    for c, _ in t4:
        in4_count[c] = in4_count.get(c, 0) + 1
uniq = len(in4_count)
print(f"\n① 50槽位(16模型×4席)被 {uniq} 只不同股票占据 (占比 {uniq/64*100:.0f}%)")

# 分析2: 候选股的跨种子名次/分数稳定性
print("\n② 高频票跨种子稳定性:")
for c, n in sorted(in4_count.items(), key=lambda x: -x[1])[:8]:
    rks = [rk for rk, v in all_scores.get(c, [])]
    scs = [v for rk, v in all_scores.get(c, [])]
    print(f"  {get_name(c) or c:<8} {c} 进前4 {n}/{N_MODELS} | 名次均值{np.mean(rks):.1f}±{np.std(rks):.1f} "
          f"| 分数均值{np.mean(scs):.4f}±{np.std(scs):.4f} | 种子数{len(rks)}")

# 分析3: 边界分差密度 — 每模型 rank4 与 rank5 的分数差
gaps = []
for d in per_seed:
    t = sorted(d.items(), key=lambda x: -x[1][1])
    if len(t) >= 5:
        gaps.append(t[3][1][1] - t[4][1][1])
print(f"\n③ 前4边界(rank4-rank5)分差: 中位{np.median(gaps):.5f} 均值{np.mean(gaps):.5f} "
      f"最小{min(gaps):.5f} 最大{max(gaps):.5f} (n={len(gaps)})")
gaps_all = []
for d in per_seed:
    t = sorted(d.items(), key=lambda x: -x[1][1])
    for k in range(1, min(10, len(t))):
        gaps_all.append(t[k-1][1][1] - t[k][1][1])
print(f"   前10名相邻分差全表: 中位{np.median(gaps_all):.5f} 均值{np.mean(gaps_all):.5f}")

# 分析4: 头部分数的绝对水平(模型输出是否几乎无区分度)
top_scores = sorted((v for d in per_seed for (rk, v) in d.values()), reverse=True)[:200]
print(f"④ 全部模型头部分数水平: 前200最高分范围 {min(top_scores):.4f} ~ {max(top_scores):.4f}")

out = os.path.join(ROOT, "data", "revival_lstm_diag_20261028.json")
json.dump({"seeds": SEEDS, "per_seed": [{c: {"rank": rk, "score": v} for c, (rk, v) in d.items()} for d in per_seed]},
          open(out, "w", encoding="utf-8"), ensure_ascii=False)
print("落盘:", out)
