# -*- coding: utf-8 -*-
"""ml_signal 输出层质量门(只读, 画像质量门方法论推广第一站):
动机=画像线实锤"分数塌缩→名单接近随机抽样→必然无alpha"; ml_signal(v22)是10/1调仓的直接依赖
(production_combo 内 ml 权重40), 若其分数塌缩则同款风险. 本检查回答: ml 分数在近40分区是否具备区分度.
检查项(对齐 portrait_output_gate): ①唯一分数占比 ②Top20边界并列组 ③Top20跨度/池std ④Top20相邻重合%.
另: ml 实际使用形态=bp_ep池内排序(非全市场Top20), 故补"池内分位结构"检查.
"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd

DL = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro'
con = sqlite3.connect(DL, uri=True, timeout=60)
df = pd.read_sql_query("SELECT code, date, ml_prob FROM stock_ml_scores_v22 ORDER BY date", con)
con.close()
df['date'] = df['date'].astype(str)
dates = sorted(df['date'].unique())[-40:]
df = df[df['date'].isin(dates)]
print(f"ml_signal 近{len(dates)}分区 {dates[0]}~{dates[-1]}")

rows = []
tops = {}
for T, g in df.groupby('date'):
    g = g.sort_values('ml_prob', ascending=False).reset_index(drop=True)
    p = g['ml_prob'].values
    if len(p) < 100:
        continue
    rows.append({'date': T, 'n': len(p), 'uniq': len(np.unique(p)) / len(p),
                 'tie20': int((p == p[19]).sum()), 'gap20': float(p[19] - p[20]),
                 'span20_std': float((p[0] - p[19]) / p.std()) if p.std() > 0 else np.nan,
                 'p10': float(p[int(len(p)*0.1)]), 'p50': float(np.median(p))})
    tops[T] = set(g['code'].head(20))
r = pd.DataFrame(rows)
ds = sorted(tops)
ov = np.median([len(tops[ds[i]] & tops[ds[i+1]]) / 20 for i in range(len(ds)-1)]) * 100

print(f"\n=== ml_signal(v22) 区分度(近{len(r)}分区, 全市场5207只) ===")
print(f"  唯一分数占比   中位={r['uniq'].median():.4f}")
print(f"  Top20边界并列  中位={r['tie20'].median():.0f}  边界分差中位={r['gap20'].median():.6f}")
print(f"  Top20跨度/池std 中位={r['span20_std'].median():.2f}")
print(f"  Top20相邻重合  中位={ov:.0f}%")
print(f"  p10分位={r['p10'].median():.4f}  p50中位={r['p50'].median():.4f}  (p10>p50? {r['p10'].median()>r['p50'].median()})")

# 判定(对齐画像门, 跨度阈值1.5/唯一率0.5/重合40%)
fails = []
if r['uniq'].median() < 0.5: fails.append(f"唯一率{r['uniq'].median():.2f}<0.5")
if r['span20_std'].median() < 1.5: fails.append(f"跨度/池std={r['span20_std'].median():.2f}<1.5")
if ov < 40: fails.append(f"相邻重合{ov:.0f}%<40%")
print(f"  → 判定: {'FAIL: ' + '; '.join(fails) if fails else '**PASS(区分度达标)**'}")

# 池内结构(实际使用形态: bp_ep候选池内排序) — 用 ml 自己分档近似(无池数据时以pct分层替代)
print("\n=== 分数分位结构(全市场十分位, 近40分区中位) ===")
dec_rows = []
for T, g in df.groupby('date'):
    g = g.sort_values('ml_prob', ascending=False)
    n = len(g)
    dec = {}
    for d in range(1, 11):
        seg = g.iloc[int((d-1)*n/10):int(d*n/10)]['ml_prob']
        dec[d] = float(seg.mean())
    dec_rows.append(dec)
dd = pd.DataFrame(dec_rows)
print("  各十分位均值: " + " ".join(f"D{d}={dd[d].median():.4f}" for d in range(1, 11)))
print(f"  D1-D10 差={dd[1].median()-dd[10].median():.4f} (分位分离度)")
