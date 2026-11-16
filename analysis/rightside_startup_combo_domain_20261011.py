# -*- coding: utf-8 -*-
"""右侧启动买点·组合优势域验证 (2026-10-11, 主人令"继续": 报告§八单因子分层→多条件叠加闭环)
纪律:
  - 条件先验: 只取报告§五单因子分层中方向一致且n充足的顶层条件, 不做网格搜索
  - 递进叠加: 1→4条逐级统计 n/mean/win/plr, 观察边际增益
  - 分年稳定性: 2019+数据完整段分年正负(防单段市况拟合)
  - 过拟合声明: 条件为同一数据集内选出, 存在选择偏差, 组合域结论降半级置信,
    实弹启用前须 60 交易日前向影子验证(prereg式)
主口径: 净h60(next-bar入场+0.45%费, 收盘锚); 基线=全样本。
"""
import os, sys, math
import pandas as pd
import numpy as np

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

D = r"D:\zcodesj\reports\rightside_startup_20261011"
em = pd.read_csv(os.path.join(D, "events_main.csv"), dtype={"code": str})
em["code"] = em["code"].str.zfill(6)
em["year"] = em["d_date"] // 10000
core = em[em.year >= 2019].copy()   # 2019+数据完整段为主口径


def agg(df, label):
    r = df["f60_net"].dropna()
    if len(r) < 30:
        return dict(n=len(r), note="n<30不统计")
    w20 = df["f20_net"].dropna()
    pos, neg = r[r > 0], r[r <= 0]
    plr = pos.mean() / abs(neg.mean()) if len(pos) and len(neg) and neg.mean() != 0 else float("inf")
    t = r.mean() / (r.std(ddof=1) / math.sqrt(len(r))) if len(r) > 2 else float("nan")
    posy = 0
    years = []
    for y, g in df.dropna(subset=["f60_net"]).groupby("year"):
        if len(g) >= 15:
            years.append((int(y), round(g.f60_net.mean(), 2)))
            if g.f60_net.mean() > 0:
                posy += 1
    return dict(n=len(r), mean=round(r.mean(), 2), med=round(r.median(), 2),
                win=round((r > 0).mean() * 100, 1), plr=round(plr, 2), t=round(t, 2),
                win20=round((w20 > 0).mean() * 100, 1),
                pos_year=f"{posy}/{len(years)}", years=years)


print("=" * 76)
print("基线(2019+全样本):", agg(core, "base"))
print()

# ---- 条件定义(先验, 来自报告§五方向一致的顶层单因子) ----
CONDS = [
    ("C1 画像深位/灰带(pos60<=40)", core.pos60.notna() & (core.pos60 <= 40)),
    ("C2 启动前极致缩量(pre_shrink<=0.75)", core.pre_shrink <= 0.75),
    ("C3 小票域(amount<1.5亿)", core.amount_mv < 1.5),
    ("C4 介入日不追涨(pct_D<5%)", core.pct_D < 5),
]
# 递进叠加
mask = pd.Series(True, index=core.index)
print("【递进叠加】(每级加一条, 2019+段, 净h60)")
for label, cmask in CONDS:
    mask = mask & cmask
    a = agg(core[mask], label)
    print(f"{label:38s} n={a['n']:5d} mean={a.get('mean','—'):>7} win={a.get('win','—'):>6}% plr={a.get('plr','—'):>5} t={a.get('t','—')} 正年={a.get('pos_year','—')}")
full_mask = mask
a_full = agg(core[full_mask], "full")

print()
print("【组合优势域分年明细】(全部4条件)")
for y, m in a_full.get("years", []):
    print(f"  {y}: {m:+.2f}%")

# ---- 单条件对照(各条件单独的效果, 验证叠加的边际贡献) ----
print()
print("【单条件对照】(验证每条在其余条件基础上的边际)")
for i, (label, cmask) in enumerate(CONDS):
    m2 = pd.Series(True, index=core.index)
    for j, (_, cm2) in enumerate(CONDS):
        if j != i:
            m2 = m2 & cm2
    a_with = agg(core[m2 & cmask], "with")
    a_wo = agg(core[m2 & ~cmask], "without")
    print(f"  去掉{label[:2]}: 有={a_with.get('mean','—')}(n={a_with['n']}) 无={a_wo.get('mean','—')}(n={a_wo['n']})")

# ---- 最宽松组合(2条核心: 位置带+缩量)的样本量与分年 ----
print()
m2 = (core.pos60.notna() & (core.pos60 <= 40)) & (core.pre_shrink <= 0.75)
a2 = agg(core[m2], "C1+C2")
print(f"【2条核心组合】pos60<=40 & pre_shrink<=0.75: n={a2['n']} mean={a2['mean']} win={a2['win']}% plr={a2['plr']} 正年={a2['pos_year']}")
for y, m in a2.get("years", []):
    print(f"  {y}: {m:+.2f}%")

# ---- 交叉验证式稳健性: 前后半段分割 ----
print()
mid = 20221001
for half, g in [("前半2019-2022/9", core[(core.d_date < mid)]), ("后半2022/10-2026", core[(core.d_date >= mid)])]:
    m3 = (g.pos60.notna() & (g.pos60 <= 40)) & (g.pre_shrink <= 0.75) & (g.amount_mv < 1.5) & (g.pct_D < 5)
    a3 = agg(g[m3], half)
    print(f"组合域{half}: n={a3['n']} mean={a3.get('mean','—')} win={a3.get('win','—')}%")

json_out = {"full": a_full, "core2": a2}
import json
json.dump(json_out, open(os.path.join(D, "combo_domain.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print(f"\n[输出] {D}/combo_domain.json")
