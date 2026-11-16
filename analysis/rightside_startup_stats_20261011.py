# -*- coding: utf-8 -*-
"""右侧启动事件统计聚合 (2026-10-11): 特征×收益分层 + 波段结构(主人第二问) + 复核
读 reports/rightside_startup_20261011/events_main.csv + events_lag.csv
输出 stdout 报告素材 + stats_summary.json
主口径: 净h60(next-bar入场+0.45%费, 收盘锚); 配对出场同锁=固定持有, 止损口径仅作结构事实呈现。
"""
import os, sys, json, math
import pandas as pd
import numpy as np

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

D = r"D:\zcodesj\reports\rightside_startup_20261011"
em = pd.read_csv(os.path.join(D, "events_main.csv"))
lg = pd.read_csv(os.path.join(D, "events_lag.csv"))
em["year"] = em["d_date"] // 10000
lg["year"] = lg["d_date"] // 10000
out = {}


def agg(df, ret="f60_net", win_ret="f20_net"):
    r = df[ret].dropna()
    w = df[win_ret].dropna()
    if len(r) == 0:
        return None
    win = (r > 0).mean() * 100
    pos, neg = r[r > 0], r[r <= 0]
    plr = (pos.mean() / abs(neg.mean())) if len(pos) and len(neg) and neg.mean() != 0 else float("inf")
    t = r.mean() / (r.std(ddof=1) / math.sqrt(len(r))) if len(r) > 2 and r.std(ddof=1) > 0 else float("nan")
    return dict(n=len(r), med=round(r.median(), 2), mean=round(r.mean(), 2),
                win=round(win, 1), plr=round(plr, 2), t=round(t, 2),
                win20=round((w > 0).mean() * 100, 1) if len(w) else None,
                med20=round(w.median(), 2) if len(w) else None)


print("=" * 72)
print(f"【全样本】主事件 n={len(em)}  (2012-{int(em.year.max())})  lag样本 n={len(lg)}")
print(f"分年事件数: {em.groupby('year').size().to_dict()}")
r60 = em["f60_net"].dropna()
print(f"\nh60净收益: med={r60.median():.2f}% mean={r60.mean():.2f}% win={(r60>0).mean()*100:.1f}%")
print(f"h60毛收益: med={em.f60.dropna().median():.2f}% mean={em.f60.dropna().mean():.2f}%")
a = agg(em)
print(f"主口径聚合: {a}")
out["overall"] = a

# ---------- 2019+主口径段(2012-2018 qfq库仅214-222只/年, 数据覆盖不足, 隔离呈现) ----------
CORE = em[em.year >= 2019].copy()
print(f"\n【2019+数据完整段】n={len(CORE)} (2019年起库内≥3613只/年)")
a_core = agg(CORE)
print(f"聚合: {a_core}")
out["core_2019p"] = a_core
r60c = CORE["f60_net"].dropna()
print(f"h60净: med={r60c.median():.2f}% mean={r60c.mean():.2f}% win={(r60c>0).mean()*100:.1f}%")

# ---------- 分年 ----------
print("\n【分年】(净h60)")
by_year = {}
for y, g in em.groupby("year"):
    by_year[int(y)] = agg(g)
    print(f"  {y}: n={by_year[int(y)]['n']:5d} med60={by_year[int(y)]['med']:7.2f} win={by_year[int(y)]['win']:5.1f}% t={by_year[int(y)]['t']}")
out["by_year"] = by_year

# ---------- 特征分层 ----------
SLICES = [
    ("画像位置带posband(D日)", "posband", None),
    ("介入滞后days(段起→介入)", "days", [0, 1, 2, 4, 7, 11, 100]),
    ("段起量比vr_s", "vr_s", [0, 2.5, 4, 7, 1e9]),
    ("段起涨幅pct_s%", "pct_s", [2.99, 5, 7, 9.8, 1e9]),
    ("近10日涨停zt10", "zt10", [-0.5, 0.5, 1.5, 3.5, 100]),
    ("启动前回撤pre_dd%", "pre_dd", [-1e9, -50, -35, -25.01, 0]),
    ("启动前缩量pre_shrink", "pre_shrink", [0, 0.5, 0.75, 0.95, 1e9]),
    ("成交额档(亿元)", "amount_mv", [0, 0.5, 1.5, 5, 1e9]),
    ("乖离bias20(D日)%", "bias20", [-1e9, -5, 0, 3.01, 1e9]),
    ("介入日涨幅pct_D%", "pct_D", [-1e9, -2, 0, 2, 5, 1e9]),
    ("连续上涨consec_up", "consec_up", [-0.5, 0.5, 2.5, 5.5, 100]),
]
print("\n【特征分层】主口径=净h60 (med60/win60/n/t)")
out["slices"] = {}
for title, col, bins in SLICES:
    if col not in em.columns:
        continue
    print(f"\n-- {title}")
    if bins:
        labels = [f"{bins[i]}~{bins[i+1]}" for i in range(len(bins) - 1)]
        gcol = pd.cut(em[col], bins=bins, labels=labels)
    else:
        gcol = em[col]
    sl = {}
    for k, g in em.groupby(gcol, observed=True):
        a2 = agg(g)
        if a2 and a2["n"] >= 20:
            sl[str(k)] = a2
            print(f"  {str(k):14s} n={a2['n']:6d} med={a2['med']:7.2f} mean={a2['mean']:7.2f} win={a2['win']:5.1f}% plr={a2['plr']}")
    out["slices"][title] = sl

# ---------- lag介入时机(独立样本) ----------
print("\n【介入滞后曲线】(lag样本=每段起全部合格D, 独立计收益, 净h60)")
out["lag_curve"] = {}
for lo, hi, lab in [(0, 0, "D=段起日"), (1, 1, "D=+1"), (2, 2, "D=+2"), (3, 4, "D=+3~4"),
                    (5, 7, "D=+5~7"), (8, 15, "D=+8~15"), (16, 40, "D=+16~40")]:
    g = lg[(lg.days >= lo) & (lg.days <= hi)]
    a3 = agg(g)
    if a3:
        out["lag_curve"][lab] = a3
        print(f"  {lab:10s} n={a3['n']:6d} med={a3['med']:7.2f} mean={a3['mean']:7.2f} win={a3['win']:5.1f}%")

# ---------- 波段结构(主人第二问) ----------
print("\n【波段结构】启动介入后 涨多少/多久/何时走坏 (追踪250交易日)")
wg = em[em.max_gain.notna()]
print(f"有效波段 n={len(wg)}")
q = wg.max_gain.quantile([.1, .25, .5, .75, .9]).round(1)
print(f"波段最大涨幅max_gain分位: p10={q[.1]} p25={q[.25]} p50={q[.5]} p75={q[.75]} p90={q[.9]}")
pq = wg.peak_day.quantile([.25, .5, .75]).round(0)
print(f"峰值出现日(相对入场交易日,250日窗右删失): p25={pq[.25]} p50={pq[.5]} p75={pq[.75]}")
out["wave"] = {"n": len(wg),
               "max_gain_q": {k: float(v) for k, v in q.items()},
               "peak_day_q": {k: float(v) for k, v in pq.items()}}

# ===== 波段分类: 成功段(max_gain>=20%) vs 失败段(<20%) =====
OK_TH = 20.0
ok_w = wg[wg.max_gain >= OK_TH]
bad_w = wg[wg.max_gain < OK_TH]
print(f"\n-- 波段二分(阈值{OK_TH}%): 成功段 n={len(ok_w)} ({len(ok_w)/len(wg)*100:.1f}%) / 失败段 n={len(bad_w)} ({len(bad_w)/len(wg)*100:.1f}%)")
out["wave_split"] = {"ok_n": len(ok_w), "bad_n": len(bad_w)}

print("\n【成功段】(涨到≥20%的波段) — '涨了多少/多久后到顶':")
gq = ok_w.max_gain.quantile([.25, .5, .75, .9]).round(1)
print(f"  峰值涨幅max_gain: p25={gq[.25]} p50={gq[.5]} p75={gq[.75]} p90={gq[.9]}")
pq2 = ok_w.peak_day.quantile([.25, .5, .75]).round(0)
print(f"  峰值出现日(交易日): p25={pq2[.25]} p50={pq2[.5]} p75={pq2[.75]}")
out["wave_ok"] = {"mg_q": {k: float(v) for k, v in gq.items()}, "pk_q": {k: float(v) for k, v in pq2.items()}}

print("\n【失败段】(未涨到20%即走弱):")
sq = bad_w[bad_w.stop8_ret.notna()]
print(f"  -8%止损触发率={len(sq)/len(bad_w)*100:.1f}% 触发天数med={sq.stop8_day.median():.0f} 触发时ret_med={sq.stop8_ret.median():.2f}%")
print(f"  失败段max_gain: p50={bad_w.max_gain.median():.1f}% p75={bad_w.max_gain.quantile(.75):.1f}%")
out["wave_bad"] = {"stop_rate": round(len(sq)/len(bad_w)*100, 1),
                   "stop_day_med": float(sq.stop8_day.median()),
                   "mg_p50": float(bad_w.max_gain.median())}

print("\n【峰值后走坏】(成功段的'进入下跌趋势'确认 = 峰值后首次破MA20向下):")
pk = ok_w[ok_w.pk_brk20d_day.notna()]
print(f"  触发率={len(pk)/len(ok_w)*100:.1f}% (250日窗内)")
print(f"  峰值→走坏确认天数med={pk.pk_brk20d_day.median():.0f} 确认时自峰值回吐med={pk.pk_brk20d_dd.median():.1f}% 确认时剩余浮盈med={pk.pk_brk20d_ret.median():.1f}%")
pdd = pk.pk_brk20d_dd.quantile([.25, .5, .75]).round(1)
print(f"  自峰值回吐分位: p25={pdd[.25]} p50={pdd[.5]} p75={pdd[.75]}")
out["wave_pk_exit"] = {"rate": round(len(pk)/len(ok_w)*100, 1),
                       "day_med": float(pk.pk_brk20d_day.median()),
                       "dd_med": float(pk.pk_brk20d_dd.median()),
                       "ret_left_med": float(pk.pk_brk20d_ret.median())}

print("\n【全部波段·峰值后走坏】(不分成败, 含失败段峰值=入场附近):")
pka = wg[wg.pk_brk20d_day.notna()]
print(f"  n={len(pka)} 峰值后走坏率={len(pka)/len(wg)*100:.1f}% day_med={pka.pk_brk20d_day.median():.0f} 自峰值回吐med={pka.pk_brk20d_dd.median():.1f}%")

WAVE = [("brk20d", "破MA20且MA20向下(首次,含洗盘)"), ("brk20", "收盘破MA20"), ("brk60", "收盘破MA60"),
        ("dd15", "收盘自峰值回撤15%"), ("dd20", "收盘自峰值回撤20%"), ("stop8", "-8%止损")]
print(f"\n-- 首次破位口径(全波段, 触发率/确认时涨幅med/天数med/峰值→确认回吐med)")
out["wave_exits"] = {}
for col, lab in WAVE:
    sub = wg[wg[col + "_ret"].notna()]
    if len(sub) == 0:
        print(f"  {lab:24s} 触发率 0%")
        continue
    ret_med = sub[col + "_ret"].median()
    day_med = sub[col + "_day"].median()
    dd_col = col + "_dd_from_peak"
    dd_med = sub[dd_col].median() if dd_col in sub else None
    out["wave_exits"][col] = dict(lab=lab, n=len(sub), rate=round(len(sub) / len(wg) * 100, 1),
                                  ret_med=round(ret_med, 2), day_med=float(day_med),
                                  dd_from_peak_med=None if dd_med is None or pd.isna(dd_med) else round(dd_med, 2))
    print(f"  {lab:24s} {len(sub)/len(wg)*100:5.1f}%  ret_med={ret_med:7.2f}%  day_med={day_med:5.0f}  峰值回吐med={dd_med if dd_med is None or pd.isna(dd_med) else round(dd_med, 1)}%")

# brk20d分年
print("\n-- 主口径brk20d分年(确认时涨幅med/天数med)")
out["brk20d_by_year"] = {}
for y, g in wg[wg.brk20d_ret.notna()].groupby("year"):
    out["brk20d_by_year"][int(y)] = dict(n=len(g), ret_med=round(g.brk20d_ret.median(), 2),
                                         day_med=float(g.brk20d_day.median()))
    print(f"  {int(y)}: n={len(g):4d} 确认时ret_med={g.brk20d_ret.median():7.2f}% day_med={g.brk20d_day.median():5.0f}")

# 波段结构×画像带
print("\n-- 波段×画像位置带 (max_gain p50 / brk20d确认ret_med / day_med)")
out["wave_by_posband"] = {}
for b, g in wg.groupby("posband", observed=True):
    gg = g[g.brk20d_ret.notna()]
    out["wave_by_posband"][str(b)] = dict(n=len(g), mg_p50=round(g.max_gain.median(), 2),
                                          brk_ret_med=round(gg.brk20d_ret.median(), 2) if len(gg) else None,
                                          brk_day_med=float(gg.brk20d_day.median()) if len(gg) else None)
    print(f"  {str(b):6s} n={len(g):5d} max_gain_p50={g.max_gain.median():7.2f}% brk20d确认ret_med={gg.brk20d_ret.median() if len(gg) else 'N/A'} day_med={gg.brk20d_day.median() if len(gg) else 'N/A'}")

# ---------- 断言 ----------
n_years = em.year.nunique()
assert n_years >= 10, f"分年仅{n_years}年 < 10"
assert len(em) >= 1000, f"主事件 {len(em)} 过少"
assert em.f60_net.notna().sum() / len(em) > 0.8, "h60覆盖率异常"
print(f"\n[断言] 分年={n_years}≥10 ✓  样本={len(em)}≥1000 ✓  h60覆盖={em.f60_net.notna().mean()*100:.1f}% ✓")

json.dump(out, open(os.path.join(D, "stats_summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print(f"\n[输出] {D}/stats_summary.json")
