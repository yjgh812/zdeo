# -*- coding: utf-8 -*-
"""lhb_exec30_validate_20261023.py — 影子单元3: 真实开盘30分钟成交额实测校验 ×0.35 代理

背景: 影子基线 v2（analysis/lhb_pool_shadow_baseline_20261023.py）P4 可成交过滤使用
"次日成交额×0.35≈开盘30分钟成交额"声明式代理。本件用本地 vipdoc 5分钟线（tdx_reader
.read_min5, TDX 原生收盘分钟 575=09:35 规范, 前6根bar=09:30-10:00）对覆盖窗实测校验。

覆盖真相（探测实证）: TDX 分钟库为滚动窗, 600000 首bar=2026-01-12（约200交易日）——
2016-2025 全覆盖不可行, 本件只在覆盖窗 [20260112,∞) 内实测, 量化代理偏差并检验 P4 判定
稳定性; 代理对历史段的外推假设（日内量形时稳性）如实声明。

规则真源: P 管线复用 build_paired()（零复制）; P4 判定复用 discipline_core.tradability_flag。
边界: 纯影子·零交易权·零生产接线。产出 reports/LHB_EXEC30_VALIDATE_20261023.{json,md}
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import tdx_reader as tr  # noqa: E402  既有读取件(09:35规范单一真源)
from portrait_discipline_core_20261023 import tradability_flag  # noqa: E402
from lhb_pool_shadow_baseline_20261023 import build_paired, TARGET_AMT  # noqa: E402

COVER_FROM = 20260112     # vipdoc 分钟滚动窗实测起点（探测实证）
OUT_JSON = r"D:\zcodesj\reports\LHB_EXEC30_VALIDATE_20261023.json"
OUT_MD = r"D:\zcodesj\reports\LHB_EXEC30_VALIDATE_20261023.md"


def market_of(code):
    return "sh" if code[:2] in ("60", "68") else "sz"


def real30_map(codes):
    """{code: {date_int: 开盘30分钟成交额}}（每股只读一次 .lc5）。"""
    out = {}
    for i, code in enumerate(sorted(codes)):
        try:
            rows = tr.read_min5(market_of(code), code)
        except Exception:
            out[code] = {}
            continue
        acc = {}
        for r in rows:
            if r["time"] <= 1000:  # 09:35..10:00 六根 = 09:30-10:00
                acc[r["date"]] = acc.get(r["date"], 0.0) + float(r["amount"])
        out[code] = acc
        if (i + 1) % 500 == 0:
            print(f"  分钟读取 {i + 1}/{len(codes)}", flush=True)
    return out


def main():
    print("[0] 重建配对集（复用 build_paired 零复制）...", flush=True)
    _, P = build_paired()
    cov = P[P["date_next"].notna()].copy()
    cov["dn"] = cov["date_next"].astype(float).astype(int)
    cov = cov[cov["dn"] >= COVER_FROM]
    print(f"[1] 覆盖窗事件(入场日≥{COVER_FROM}): {len(cov):,}")

    rmap = real30_map(set(cov["code"]))
    cov["real30"] = [rmap.get(c, {}).get(d) for c, d in zip(cov["code"], cov["dn"])]
    cov = cov[cov["real30"].notna() & (cov["exec_amt"] > 0)].copy()
    cov["real_share"] = cov["real30"] / cov["exec_amt"]
    print(f"[2] 实测可用: {len(cov):,}（其余为分钟缺档/停牌衔接）")

    s = cov["real_share"]
    q = s.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    proxy_bias = float((s - 0.35).abs().mean())
    res = {
        "meta": {"cover_from": COVER_FROM, "n": int(len(cov)),
                 "proxy": "次日成交额×0.35", "real": "vipdoc lc5 前6根bar(09:30-10:00) amount 和"},
        "real_share": {"mean": round(float(s.mean()), 4), "std": round(float(s.std()), 4),
                       "p10": round(float(q[0.10]), 4), "p25": round(float(q[0.25]), 4),
                       "p50": round(float(q[0.50]), 4), "p75": round(float(q[0.75]), 4),
                       "p90": round(float(q[0.90]), 4),
                       "mean_abs_dev_from_035": round(proxy_bias, 4)},
    }

    # P4 判定: 代理 vs 实测
    cov["ok_proxy"] = [tradability_flag(TARGET_AMT, a * 0.35)[1] for a in cov["exec_amt"]]
    cov["ok_real"] = [tradability_flag(TARGET_AMT, a)[1] for a in cov["real30"]]
    agree = float((cov["ok_proxy"] == cov["ok_real"]).mean())
    n_drop_proxy = int((~cov["ok_proxy"]).sum())
    n_drop_real = int((~cov["ok_real"]).sum())
    res["p4_verdict"] = {
        "proxy_dropped": n_drop_proxy, "real_dropped": n_drop_real,
        "agreement_pct": round(agree * 100, 2),
        "note": "判定分歧方向与规模如实呈报; 主统计口径是否切换待主人裁决",
    }

    # 覆盖子集 5日窗超额在两种 P4 口径下的稳定性
    for tag, col in (("proxy", "ok_proxy"), ("real", "ok_real")):
        sub = cov[cov[col]]
        res[f"ex5_{tag}"] = {"n": int(len(sub)),
                             "mean_excess_pp": round(float(sub["ex5"].mean() * 100), 2),
                             "median_excess_pp": round(float(sub["ex5"].median() * 100), 2)}

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=float)

    L = ["# 开盘30分钟可成交额 ×0.35 代理 实测校验（影子单元3 · 2026-10-23）", "",
         "> ⚠️ 影子-only 零交易权。覆盖窗=本地 vipdoc 分钟滚动窗（2026-01-12 起），",
         "> 历史段外推假设=日内量形时稳性（如实声明，不可证伪于本件）。", "",
         f"- 实测样本 n={res['meta']['n']:,}（入场日≥{COVER_FROM}，配对集内）",
         f"- 真实30分钟占比: 均值 {res['real_share']['mean']} / 中位 {res['real_share']['p50']} / "
         f"σ={res['real_share']['std']} / P10-P90 [{res['real_share']['p10']}, {res['real_share']['p90']}]",
         f"- 代理常数 0.35 的平均绝对偏差: {res['real_share']['mean_abs_dev_from_035']}", "",
         f"## P4 判定对照（50万目标, 阈值30%）",
         f"- 代理口径剔除 {res['p4_verdict']['proxy_dropped']} vs 实测口径剔除 {res['p4_verdict']['real_dropped']}"
         f"（判定一致率 {res['p4_verdict']['agreement_pct']}%）", "",
         "## 覆盖子集 5日窗超额稳定性",
         f"- 代理P4口径: n={res['ex5_proxy']['n']:,} 均值 {res['ex5_proxy']['mean_excess_pp']}pp "
         f"中位 {res['ex5_proxy']['median_excess_pp']}pp",
         f"- 实测P4口径: n={res['ex5_real']['n']:,} 均值 {res['ex5_real']['mean_excess_pp']}pp "
         f"中位 {res['ex5_real']['median_excess_pp']}pp", "",
         "*影子校验非结论；主统计口径切换待主人裁决。*"]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(open(OUT_MD, encoding="utf-8").read())


if __name__ == "__main__":
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
