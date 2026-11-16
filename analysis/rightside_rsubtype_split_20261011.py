# -*- coding: utf-8 -*-
"""右侧启动 R 子类型拆分统计 (呈请3收口后的推荐替代路径, 2026-10-11)

来源: PROMPT-RIGHT-STARTUP-20261011 补审呈请(§7.3)——第四口径不立项, 其真增量=R1-R6
  子类型维度拆分可在宽定义既有样本上零新定义补分组统计(本件), 独立prereg可选不做(未获明示)。
样本: 宽定义 v2 口径逐位复刻(stage9: 剔688/689/43/83/87/92/ST名册risk_st_20261010/退市幽灵maxdate<20250901/600298),
  = tmp/rightside_wide_outcomes.csv 过滤后应≈69,379 (自检)。
特征(预声明, 从qfq逐股重算): 
  pos250 = (close-250日low)/(250日high-250日low)*100, min_periods=250(不足=NaN, 画像口径);
  vr120  = 当日量 / 前120日均量(shift1, min_periods=60); 
  big_day = 当日change_pct>=9.5 (R37同源近似口径; 创业板20%板宽下非严格涨停, 如实注记)。
分带(预声明): pos250 <30[R1标签]/30-50[R2]/50-80/>=80/NaN; vr120 <0.8/0.8-1.2[R3]/1.2-1.5[R4]/>=1.5/NaN;
  big_day 是[R5域]/否。R6主线跳过(winner5历史化未建, 不伪造, 如实注记)。
交集格(预声明单格): pos250<50 ∧ vr120<1.5 (五条件族A∧C在宽样本), 分年稳健轻检。
判据: f20/f60(毛, 宽研究口径) + ex60(等权超额列宽研究已备); 块bootstrap仅对交集格(seed20261011)。
输出: stdout + tmp/rightside_rsubtype_split_20261011.json + 报告节由本件打印素材直出。
性质: 描述性分组件零交易权; 不改变宽定义任何收官结论。
"""
import os, sys, json, sqlite3, time
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
OUT = r"D:/zcodesj/tmp/rightside_rsubtype_split_20261011.json"
SEED = 20261011


def stat(v, pct=True):
    v = np.asarray([x for x in v if x == x], dtype=float)
    if len(v) < 5:
        return {"n": int(len(v))}
    t = float(v.mean() / (v.std(ddof=1) / len(v) ** 0.5))
    m = float(v.mean()) * (100 if pct else 1)
    return {"n": int(len(v)), "f20_mean_pct": round(m, 3), "win": round(float((v > 0).mean()) * 100, 1), "t": round(t, 2)}


def block_boot_ci(vals_by_date, block=5, nboot=1000, seed=SEED):
    rng = np.random.default_rng(seed)
    dates = sorted(vals_by_date)
    blocks = [dates[k:k + block] for k in range(0, len(dates), block)]
    means = []
    for _ in range(nboot):
        pick = rng.integers(0, len(blocks), size=len(blocks))
        vals = [x for bi in pick for d in blocks[bi] for x in vals_by_date[d]]
        if vals:
            means.append(float(np.mean(vals)))
    lo, hi = np.percentile(means, [2.5, 97.5])
    return [round(lo, 3), round(hi, 3)]


def main():
    t0 = time.time()
    oc = pd.read_csv(r"D:/zcodesj/tmp/rightside_wide_outcomes.csv", dtype={"code": str})
    st_set = set(json.load(open(r"D:/zcodesj/tmp/risk_st_20261010.json", encoding="utf-8"))["codes"])
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    stale = {c for c, mx in con.execute("SELECT code, MAX(date) FROM stock_daily_meta_qfq GROUP BY code").fetchall() if mx < "20250901"}
    def drop(c):
        return c.startswith(("688", "689")) or c[:2] in ("43", "83", "87", "92") or c in st_set or c in stale
    oc = oc[~oc["code"].map(drop) & (oc["code"] != "600298")].reset_index(drop=True)
    print(f"[v2] rows={len(oc)} (目标69,379) codes={oc.code.nunique()}")
    ok = abs(len(oc) - 69379) <= 25  # 容差: st名册/幽灵码快照时点漂移(实测-18行=0.03%)
    print(f"[check1] {'PASS' if ok else 'FAIL'}(差{len(oc)-69379})")

    sig = pd.read_csv(r"D:/zcodesj/tmp/rightside_wide_signals.csv", usecols=["code", "date", "change_pct", "volume"], dtype={"code": str})
    codes = sorted(oc.code.unique())
    frames = []
    for k in range(0, len(codes), 1200):
        chunk = codes[k:k + 1200]
        q = ",".join("?" * len(chunk))
        frames.append(pd.read_sql_query(
            f"SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE code IN ({q}) AND date>='20140101' ORDER BY code,date",
            con, params=chunk))
    con.close()
    px = pd.concat(frames, ignore_index=True)
    g = px.groupby("code", group_keys=False)
    rmin = g["close"].transform(lambda s: s.rolling(250, min_periods=250).min())
    rmax = g["close"].transform(lambda s: s.rolling(250, min_periods=250).max())
    px["pos250"] = np.where((rmax - rmin) > 0, (px["close"] - rmin) / (rmax - rmin) * 100, np.nan)
    vma = g["volume"].transform(lambda s: s.shift(1).rolling(120, min_periods=60).mean())
    px["vr120"] = np.where(vma > 0, px["volume"] / vma, np.nan)
    feat = px[["code", "date", "pos250", "vr120"]]
    feat["date"] = feat["date"].astype(int)
    em = oc.merge(sig, on=["code", "date"], how="left").merge(feat, on=["code", "date"], how="left")
    em["big_day"] = em["change_pct"] >= 9.5
    print(f"[feat] pos250 NaN率={em.pos250.isna().mean()*100:.1f}% vr120 NaN率={em.vr120.isna().mean()*100:.1f}% big_day率={em.big_day.mean()*100:.1f}% ({time.time()-t0:.0f}s)")

    y20, y60, ex = em.f20.values, em.f60.values, em.ex60.values
    out = {"meta": {"date": "2026-10-11", "spec": "PROMPT补审呈请R1-R6拆分(宽定义v2样本零新定义)",
                    "sample_n": int(len(em)), "v2_repro": bool(ok),
                    "note": "R6主线跳过(winner5历史化未建); big_day=9.5%近似口径(创业板20%板宽非严格涨停)",
                    "nan": {"pos250": round(float(em.pos250.isna().mean()) * 100, 1),
                            "vr120": round(float(em.vr120.isna().mean()) * 100, 1)}},
           "pos250_band": {}, "vr120_band": {}, "big_day": {}, "cross_AC": {}}

    def band_stats(mask, key, dic):
        s = stat(y20[mask])
        s["f60_mean_pct"] = stat(y60[mask]).get("f20_mean_pct")
        s["f60_win"] = stat(y60[mask]).get("win")
        dic[key] = s

    pb = pd.cut(em.pos250, [-1, 30, 50, 80, 1000], labels=["<30[R1]", "30-50[R2]", "50-80", ">=80"])
    for lab in ["<30[R1]", "30-50[R2]", "50-80", ">=80"]:
        band_stats((pb == lab).values, lab, out["pos250_band"])
    vb = pd.cut(em.vr120, [-1, 0.8, 1.2, 1.5, 1000], labels=["<0.8", "0.8-1.2[R3]", "1.2-1.5[R4]", ">=1.5"])
    for lab in ["<0.8", "0.8-1.2[R3]", "1.2-1.5[R4]", ">=1.5"]:
        band_stats((vb == lab).values, lab, out["vr120_band"])
    band_stats(em.big_day.values, "big_day>=9.5[R5域]", out["big_day"])
    band_stats(~em.big_day.values, "非big_day", out["big_day"])

    cross = (em.pos250 < 50) & (em.vr120 < 1.5)
    s = stat(y20[cross.values])
    s["f60_mean_pct"] = stat(y60[cross.values]).get("f20_mean_pct")
    byd = {}
    sub = em[cross.values]
    for d, v in zip(sub.date, y20[cross.values]):
        if v == v:
            byd.setdefault(int(d), []).append(float(v))
    s["ci95_f20"] = block_boot_ci(byd)
    out["cross_AC"] = {"desc": "pos250<50 ∧ vr120<1.5 (五条件族A∧C)", **s}
    # 交集格分年
    yr = {}
    sub2 = sub.assign(f20=y20[cross.values])
    sub2["year"] = sub2.date // 10000
    for y_, gg in sub2.groupby("year"):
        yr[int(y_)] = {"n": int(len(gg)), "mean_pct": round(float(gg.f20.mean()) * 100, 2)}
    out["cross_AC_by_year"] = yr

    print("\n=== pos250带 (f20 mean/win/t) ===")
    for k2, v in out["pos250_band"].items():
        print(f"  {k2}: {v}")
    print("=== vr120带 ===")
    for k2, v in out["vr120_band"].items():
        print(f"  {k2}: {v}")
    print("=== 当日>=9.5% ===")
    for k2, v in out["big_day"].items():
        print(f"  {k2}: {v}")
    print("=== 交集格 pos250<50∧vr120<1.5 ===")
    print(f"  {out['cross_AC']}")
    print(f"  分年: {json.dumps({k2: v for k2, v in yr.items() if v['n'] >= 30}, ensure_ascii=False)}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
