"""_vbt_sweep_demo.py — 一次性参数扫描 demo（研究层预筛，临时脚本）
通过现有 vbt_ashare_adapter.run_ashare 把 MA 交叉 fast×slow 网格全部跑完，
输出总收益矩阵 top/bottom + 平台期结构。不产出生产参数，绝对数字禁对账。
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from vbt_ashare_adapter import AshareRules, load_stock, run_ashare, board_limit_pct

CODE = "600519"
FAST = np.arange(2, 31)        # 29 档
SLOW = np.arange(10, 62, 2)    # 26 档
SIZE = 100
INIT_CASH = 300_000  # 100 股×~1500 ≈ 50% 仓位，让收益量级可见（demo 用，非生产口径）
RULES = AshareRules()

df = load_stock(CODE)
close = df["close"]  # 保留 DatetimeIndex，与适配器内 shift/掩码对齐
close_n = close.to_numpy()
n = len(df)
print(f"{CODE} {board_limit_pct(CODE)*100:.0f}% 板  bars={n}  fast={FAST.size}×slow={SLOW.size}={FAST.size*SLOW.size} 组")

res = np.full((FAST.size, SLOW.size), np.nan)
trades_n = np.zeros((FAST.size, SLOW.size), dtype=int)
warm = 20  # 均线预热，忽略前 20 bar

for i, f in enumerate(FAST):
    fa = close.rolling(f).mean()
    for j, s in enumerate(SLOW):
        if f >= s:
            continue  # 快线必须 < 慢线，避免平凡组合
        sl = close.rolling(s).mean()
        up = (fa > sl) & (fa.shift(1) <= sl.shift(1))
        dn = (fa < sl) & (fa.shift(1) >= sl.shift(1))
        valid = pd.Series(True, index=close.index)
        valid.iloc[:warm] = False  # 固定跳过前 warm 个 bar 的均线预热
        up, dn = up & valid, dn & valid
        pf, *_ = run_ashare(df, up, dn, size=SIZE, init_cash=INIT_CASH, rules=RULES, code=CODE)
        res[i, j] = float(pf.total_return().iat[0])
        trades_n[i, j] = len(pf.trades.records_arr)
        if i < 3 and j < 3 and f < s:
            print(f"  [debug] fast={f} slow={s} up={int(up.sum())} trades={trades_n[i, j]} ret={res[i, j]*100:.4f}%")

# 有效组合（快<慢）的统计
valid = ~np.isnan(res)
total = int(valid.sum())
ret = res[valid]
tn = trades_n[valid]
print(f"有效组合 {total} 组：收益中位 {np.median(ret)*100:.2f}%  均值 {ret.mean()*100:.2f}%  "
      f"正收益占比 {(ret > 0).mean()*100:.1f}%  最大 {ret.max()*100:.2f}%  最小 {ret.min()*100:.2f}%")

# 平台期结构：快线固定时慢线方向，看是否存在"稳健平台"而非单点尖峰
print("\n=== 平台期结构（按快线分组，慢线维度收益范围）===")
print("快线  最大收益%  最小收益%  极差pp  交易笔数中位")
plateau = []
for i, f in enumerate(FAST):
    row = res[i]
    ok = ~np.isnan(row)
    if ok.sum() < 2:
        continue
    r = row[ok]
    lo, hi = r.min() * 100, r.max() * 100
    med_trades = int(np.median(trades_n[i][ok]))
    plateau.append((f, lo, hi, hi - lo, med_trades))
    if hi - lo >= 5:  # 极差≥5pp 视为"对慢线不敏感"的稳健段（示意，非门禁）
        print(f"  {f:>3}   {hi:8.2f}  {lo:8.2f}   {hi-lo:6.2f}   {med_trades}")
print(f"（共 {len(plateau)} 个快线组，其中极差≥5pp 的稳健组 {sum(1 for p in plateau if p[3]>=5)} 个）")

# 尖峰检测：单点最优是否与邻域显著脱节（过拟合特征）
best_i, best_j = np.unravel_index(np.nanargmax(res), res.shape)
best_val = res[best_i, best_j] * 100
neigh = []
for di in (-1, 0, 1):
    for dj in (-1, 0, 1):
        ii, jj = best_i + di, best_j + dj
        if (di, dj) != (0, 0) and 0 <= ii < FAST.size and 0 <= jj < SLOW.size and not np.isnan(res[ii, jj]):
            neigh.append(res[ii, jj] * 100)
print(f"\n全局最优 fast={FAST[best_i]} slow={SLOW[best_j]} 收益 {best_val:.2f}%  交易 {trades_n[best_i, best_j]} 笔")
print(f"  其 8 邻域收益均值 {np.mean(neigh):.2f}%  (最优-邻域均值 = {best_val - np.mean(neigh):.2f}pp)")
print(f"  若尖峰(最优>>邻域) => 过拟合特征，需 wfa_gate 一票制验证，禁直接采参数")

# 粗热力图存 CSV（可导入看板）
out = pd.DataFrame(res, index=[f"fast{f}" for f in FAST], columns=[f"slow{s}" for s in SLOW])
out.to_csv(r"D:\zcodesj\_vbt_sweep_demo_heatmap.csv", encoding="utf-8-sig")
print(f"\n热力图已存 _vbt_sweep_demo_heatmap.csv（{out.shape}）")
print("ADAPTER SWEEP DEMO DONE")
