# -*- coding: utf-8 -*-
"""画像名单 α/β 分解(只读, 零重算): 跨截面回归 port5 = α + β×base5 + ε, 剥离市场(池)暴露后的残差alpha.
数据: shadow_state.json 的 realizable 记录(Top500: 0612~0903 59截面; Top20口径: 0724~0903 30截面同区间对照).
判读: β≈1=随市; β>1=放大器; α显著正=真选股能力; α≈0且β>1=纯β(定论闭环)."""
import json, sys
sys.stdout.reconfigure(encoding='utf-8')
import numpy as np
from scipy import stats

s = json.load(open(r'D:\zcodesj\portrait_agent\models\shadow_state.json', encoding='utf-8'))
H = s['history']

def reg(records, label):
    if not records:
        print(f"{label}: 无数据"); return
    y = np.array([x['port5'] for x in records])   # 名单收益
    x = np.array([x['base5'] for x in records])   # 池收益(市场代理)
    n = len(y)
    r = stats.linregress(x, y)
    alpha = r.intercept; beta = r.slope
    # α 的 t 检验: 残差标准误
    resid = y - (alpha + beta * x)
    se = np.sqrt(np.sum(resid**2) / (n - 2)) / (np.std(x, ddof=1) * np.sqrt(n))
    t_alpha = alpha / se if se > 0 else np.nan
    p_alpha = 2 * (1 - stats.t.cdf(abs(t_alpha), n - 2)) if np.isfinite(t_alpha) else np.nan
    print(f"\n== {label} (n={n}截面, {records[0]['date']}~{records[-1]['date']}) ==")
    print(f"  β(市场暴露) = {beta:.3f}  (±{r.stderr:.3f}, t={beta/r.stderr:.1f})")
    print(f"  α(残差/每5日) = {alpha:+.3f}%  (t={t_alpha:+.2f}, p={p_alpha:.3f}) {'✅显著' if p_alpha<0.05 else '❌不显著'}")
    print(f"  R² = {r.rvalue**2:.3f}  |  年化α≈{alpha*48:+.1f}%(48个5日/年, 粗估)")
    return beta, alpha, t_alpha, r.rvalue**2

for tag in ('short', 'mid'):
    v500 = [x for x in H if x.get('metric')=='realizable' and x.get('topn')==500 and x['tag']==tag]
    v20  = [x for x in H if x.get('metric')=='realizable' and x.get('topn')==20  and x['tag']==tag
            and x['date'] >= '20260724']   # 同区间对照
    reg(v500, f"Top500 {tag} 全窗口")
    reg([x for x in v500 if x['date'] >= '20260724'], f"Top500 {tag} B段(0724~0903, 与Top20同区间)")
    reg([x for x in v500 if x['date'] < '20260724'], f"Top500 {tag} A段(0612~0723)")
    reg(v20, f"Top20 {tag} (0724~0903)")

# β 稳定性: 分半窗口
print("\n=== β 稳定性(分半) ===")
for tag in ('short', 'mid'):
    v500 = sorted([x for x in H if x.get('metric')=='realizable' and x.get('topn')==500 and x['tag']==tag], key=lambda h: h['date'])
    mid = len(v500)//2
    for half, seg in (('前半(0612~0730)', v500[:mid]), ('后半(0731~0903)', v500[mid:])):
        y = np.array([x['port5'] for x in seg]); x = np.array([x['base5'] for x in seg])
        r = stats.linregress(x, y)
        print(f"  {tag} {half}: β={r.slope:.3f} α={r.intercept:+.3f}% R²={r.rvalue**2:.3f}")
