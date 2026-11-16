# -*- coding: utf-8 -*-
"""诊断② 五禁区/vr120 前置过滤 post-hoc (外部方案审计B第二档·序2-1/2-2, 主人 b 令 2026-10-18)

方法: 对既有回测逐笔(baseline trades)在【买入决策日=T-1】计算禁区特征, 比较
「命中禁区 vs 未命中」两子集的事后胜率/中位收益。post-hoc 子集对比 ≠ 真过滤重跑
(不补位 rank16+), 只回答「被禁区分桶的票是否确实更差」= 负先验在 bp_ep/ml 选股
结果上是否成立。任何接线须 prereg。

🔴 在档先验引用(审计要求): POS域过滤闸 A/B FAIL(V1 -1.1pp / V2 -24.5pp, G1方向
反转被剔集反好+2.06pp, 10/9迭代轮3归档)——bp_ep 叠加画像维过滤族已有证伪成员。
本诊断的 X5(贴死底 pos250≤0.10)与该 FAIL 家族同属位置带过滤, 先验应最低;
X1-X4(量/乖离/追/突破)与 POS-FAIL 特征不同族, 属首次测试。
"""
import json
import sqlite3
from bisect import bisect_left

import numpy as np
import pandas as pd

QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DATASETS = {
    "bp_ep_top15_20261018": r"D:\zcodesj\reports\backtest_full_20261018\bp_ep_core_trades_full.csv",
    "bp_ep_top20_20261016": r"D:\zcodesj\reports\backtest_full_20261016_eod\bp_ep_core_trades_full.csv",
    "ml_top20_20261018": r"D:\zcodesj\reports\backtest_full_20261018\ml_signal_trades_full.csv",
}
OUT_JSON = r"D:\zcodesj\analysis\overlay_forbidden_zone_diag_20261018_out.json"
OUT_MD = r"D:\zcodesj\analysis\overlay_forbidden_zone_diag_20261018_out.md"

con = sqlite3.connect(QFQ, uri=True)
cur = con.cursor()
_code_cache = {}


def _series(code6: str):
    if code6 not in _code_cache:
        cur.execute(
            "SELECT date, high, low, close, volume FROM stock_daily_meta_qfq "
            "WHERE code=? ORDER BY date", (code6,))
        rows = cur.fetchall()
        dates = [r[0] for r in rows]
        _code_cache[code6] = (dates, rows)
    return _code_cache[code6]


def features(code6: str, buy_date: str):
    """买入决策日 = 买入日期前该股最后一个有行情日; 特征只用该日及以前(无前视)。"""
    d8 = buy_date.replace("-", "")
    dates, rows = _series(code6)
    i = bisect_left(dates, d8)
    if i == 0:  # 无 T-1 行情
        return None
    i -= 1  # 特征日下标
    if i < 249:
        return None  # 250 日窗不足, pos250/浅窗一律不判(缺数据不算命中)
    hi = np.array([r[1] for r in rows[i - 249:i + 1]], dtype=float)
    lo = np.array([r[2] for r in rows[i - 249:i + 1]], dtype=float)
    cl = np.array([r[3] for r in rows[i - 249:i + 1]], dtype=float)
    vo = np.array([r[4] for r in rows[i - 249:i + 1]], dtype=float)
    c = cl[-1]
    ret20 = c / cl[-21] - 1.0
    bias20 = c / cl[-20:].mean() - 1.0
    vma5, vma120 = vo[-5:].mean(), vo[-120:].mean()
    vr120 = vma5 / vma120 if vma120 and vma120 > 0 else None
    vr120_t2 = vo[-1] / vo[-121:-1].mean() if vo[-121:-1].mean() > 0 else None  # tier2口径: 当日量/前120日均量
    day_pct = c / cl[-2] - 1.0 if cl[-2] else None
    is20h = c >= hi[-20:].max() - 1e-9
    mv20 = vo[-20:].mean()
    volr20 = vo[-1] / mv20 if mv20 and mv20 > 0 else None
    rng = hi[-250:].max() - lo[-250:].min()
    pos250 = (c - lo[-250:].min()) / rng if rng and rng > 0 else None
    return dict(ret20=ret20, bias20=bias20, vr120=vr120, vr120_t2=vr120_t2, day_pct=day_pct,
                is20h=bool(is20h), volr20=volr20, pos250=pos250)


def flags(f: dict):
    x1 = f["vr120"] is not None and f["vr120"] >= 1.5 and f["ret20"] >= 0.20
    x2 = f["bias20"] >= 0.20
    x3 = f["day_pct"] is not None and f["day_pct"] >= 0.06
    x4 = f["is20h"] and f["volr20"] is not None and f["volr20"] >= 1.5
    x5 = f["pos250"] is not None and 0.0 <= f["pos250"] <= 0.10
    return dict(X1爆量态=x1, X2乖离MA20=x2, X3急追6pct=x3, X4裸突破=x4, X5贴死底=x5,
                ANY_X14=(x1 or x2 or x3 or x4))


def stats(sub: pd.DataFrame):
    n = len(sub)
    if n == 0:
        return dict(n=0)
    ret = sub["盈亏比例%"].astype(float)
    pnl = sub["盈亏金额"].astype(float)
    return dict(n=n,
                win_r=round(float((ret > 0).mean()) * 100, 2),
                win_a=round(float((pnl > 0).mean()) * 100, 2),
                med_ret=round(float(ret.median()), 3),
                pnl_sum=round(float(pnl.sum()), 0))


def main():
    out = {}
    for name, path in DATASETS.items():
        try:
            df = pd.read_csv(path, encoding="utf-8-sig")
        except Exception as e:
            out[name] = {"error": f"read fail: {e}"}
            continue
        codes = df["股票代码"].astype(str).str.zfill(6)
        feats, uncovered = [], 0
        for code6, bd in zip(codes, df["买入日期"].astype(str)):
            f = features(code6, bd)
            if f is None:
                uncovered += 1
            feats.append(f)
        df["_f"] = feats
        ok = df["_f"].notna()
        d = df[ok].copy()
        fl = d["_f"].map(flags).apply(pd.Series)
        d = pd.concat([d.reset_index(drop=True), fl.reset_index(drop=True)], axis=1)
        res = {"n_trades": int(len(df)), "n_uncovered": int(uncovered), "flags": {}}
        for flag in list(fl.columns):
            hit = d[d[flag]]
            kept = d[~d[flag]]
            res["flags"][flag] = {"hit": stats(hit), "kept": stats(kept)}
        # vr120 绝对带
        vr = d["_f"].map(lambda f: f["vr120"])
        d["_vr"] = vr
        bands = {"vr<1.0": d["_vr"] < 1.0, "vr1.0-1.5": (d["_vr"] >= 1.0) & (d["_vr"] < 1.5),
                 "vr>=1.5": d["_vr"] >= 1.5}
        res["vr_bands"] = {k: stats(d[m]) for k, m in bands.items()}
        # 组内相对带: 每个买入日期组内 vr120 前三分之一
        grp_share = []
        for _, g in d.dropna(subset=["_vr"]).groupby("买入日期"):
            k = max(1, int(np.ceil(len(g) / 3)))
            grp_share.extend(g.nlargest(k, "_vr").index)
        res["vr_top3_within_day"] = stats(d.loc[sorted(set(grp_share))])
        d["_vr2"] = d["_f"].map(lambda f: f["vr120_t2"])
        bands2 = {"t2vr<1.0": d["_vr2"] < 1.0, "t2vr1.0-1.5": (d["_vr2"] >= 1.0) & (d["_vr2"] < 1.5),
                  "t2vr>=1.5": d["_vr2"] >= 1.5}
        res["vr_bands_tier2def"] = {k: stats(d[m]) for k, m in bands2.items()}
        out[name] = res

    con.close()
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    lines = ["# 诊断② 五禁区/vr120 前置过滤 post-hoc 输出 (2026-10-18)", "",
             "> post-hoc 子集对比, 非真过滤重跑; 特征日=买入决策日 T-1(股自身上一有行情日), 无前视;",
             "> 窗口不足250日不判(计入 uncovered); vr120 口径=portrait族 vma5/vma120。",
             "> 🔴 先验引用: POS域过滤闸FAIL(V1-1.1pp/V2-24.5pp/G1反转+2.06pp)在档——X5同族先验最低。", ""]
    for name, res in out.items():
        if "error" in res:
            lines.append(f"## {name}: ERROR {res['error']}")
            continue
        lines.append(f"## {name} (n={res['n_trades']}, uncovered={res['n_uncovered']})")
        lines.append("| 禁区 | 命中n | 命中胜率%(价) | 未命中胜率% | Δpp | 命中med% | medΔ | 命中盈亏和 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for flag, v in res["flags"].items():
            h, k = v["hit"], v["kept"]
            if h.get("n", 0) == 0:
                lines.append(f"| {flag} | 0 | - | {k.get('win_r','-')} | - | - | - | - |")
                continue
            dd = round(h["win_r"] - k.get("win_r", 0), 2)
            dm = round(h["med_ret"] - k.get("med_ret", 0), 3)
            lines.append(f"| {flag} | {h['n']} | {h['win_r']} | {k.get('win_r','-')} | "
                         f"{dd} | {h['med_ret']} | {dm} | {h['pnl_sum']} |")
        lines.append("")
        lines.append("| vr120带 | n | 胜率%(价) | med% | 盈亏和 |")
        lines.append("|---|---|---|---|---|")
        for b, v in res["vr_bands"].items():
            if v.get("n", 0):
                lines.append(f"| {b} | {v['n']} | {v['win_r']} | {v['med_ret']} | {v['pnl_sum']} |")
        t3 = res["vr_top3_within_day"]
        if t3.get("n", 0):
            lines.append(f"| 组内vr前1/3 | {t3['n']} | {t3['win_r']} | {t3['med_ret']} | {t3['pnl_sum']} |")
        lines.append("")
        lines.append("tier2口径(当日量/前120日均量)对照:")
        lines.append("| 带 | n | 胜率%(价) | med% | 盈亏和 |")
        lines.append("|---|---|---|---|---|")
        for b, v in res["vr_bands_tier2def"].items():
            if v.get("n", 0):
                lines.append(f"| {b} | {v['n']} | {v['win_r']} | {v['med_ret']} | {v['pnl_sum']} |")
        lines.append("")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print("OUT:", OUT_MD)


if __name__ == "__main__":
    main()
