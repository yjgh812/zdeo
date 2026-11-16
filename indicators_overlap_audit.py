# -*- coding: utf-8 -*-
"""indicators_overlap_audit.py —— 生产指标池同源性审计（描述性, 只读研究件）

来源: reports/math_knowledge_full_study_20260920.md 域五⑥（主人令"继续"第三轮落地）
命题: 动量族指标全部是 Δp 的时不变线性泛函, 区别仅是权重向量 → 截面秩相关应高度共线;
      "多指标共振"可伪装成独立证据 → 本件量化生产指标池的真实信息维度。
口径: 截面日 T=qfq 最大日期, 全市场(6/0/3 开头当日有行), qfq 主库 260 日窗:
  一阶矩动量族: ret5 / ret20 / ret60 / bias20 / bias60
  位置族: pos250 / pos60 (high-low 区间位)
  二阶矩族: vol20 (20日收益std)
  量能族: volr20 (量/20日均量)
方法: 截面 Spearman 相关矩阵 + |ρ|>0.7 单连聚类(信息族) + 相关矩阵特征值(前2主成分方差占比)。
输出: data/indicators_overlap_audit_<T>.json + reports/indicators_overlap_audit_<date>.md
红线: 纯描述性审计零判据零交易权; 不构成任何指标的降权/删除裁决(那需 prereg+主人)。
用法: python indicators_overlap_audit.py  退出码 0/2
"""
from __future__ import annotations
import itertools
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

ROOT = r"D:\zcodesj"
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
RHO_CLUSTER = 0.7          # |ρ|>0.7 归同簇(报告域五: ρ>0.7 簇只留一个代表)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_universe() -> tuple[str, pd.DataFrame]:
    con = sqlite3.connect(QFQ_DB, uri=True)
    (t_max,) = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()
    t_from = con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE code='600000' ORDER BY date DESC LIMIT 260"
    ).fetchall()
    dmin = min(r[0] for r in t_from)
    cur = con.execute(
        "SELECT code,date,close,high,low,volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND (code LIKE '6%' OR code LIKE '0%' OR code LIKE '3%') ORDER BY code,date", (dmin,))
    rows = []
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        g = [(d, c, h, l, v) for (_, d, c, h, l, v) in grp if c and c > 0]
        if len(g) < 61:      # 至少够 ret60
            continue
        closes = [x[1] for x in g]
        highs = [x[2] for x in g]
        lows = [x[3] for x in g]
        vols = [x[4] for x in g]
        last_date = g[-1][0]
        if last_date != t_max:   # 截面日在市(停牌剔除)
            continue

        def ret(h):
            return closes[-1] / closes[-1 - h] - 1 if len(closes) > h else np.nan

        def bias(h):
            m = sum(closes[-h:]) / h
            return closes[-1] / m - 1

        def pos(h):
            hh, ll = max(highs[-h:]), min(lows[-h:])
            return (closes[-1] - ll) / (hh - ll) if hh > ll else np.nan

        r20 = [closes[i] / closes[i - 1] - 1 for i in range(max(1, len(closes) - 20), len(closes))]
        rows.append({
            "code": code,
            "ret5": ret(5), "ret20": ret(20), "ret60": ret(60),
            "bias20": bias(20), "bias60": bias(60),
            "pos250": pos(250) if len(g) >= 250 else np.nan, "pos60": pos(60),
            "vol20": float(np.std(r20, ddof=1)) if len(r20) >= 10 else np.nan,
            "volr20": vols[-1] / (sum(vols[-20:]) / 20) if sum(vols[-20:]) > 0 else np.nan,
        })
    con.close()
    return str(t_max), pd.DataFrame(rows).set_index("code")


def clusters(corr: pd.DataFrame, thr: float) -> list[list[str]]:
    cols = list(corr.columns)
    parent = {c: c for c in cols}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            if abs(corr.loc[a, b]) > thr:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for c in cols:
        groups.setdefault(find(c), []).append(c)
    return sorted(groups.values(), key=len, reverse=True)


def main() -> int:
    T, df = load_universe()
    if len(df) < 500:
        print(f"[IND-FAIL] 截面样本不足 {len(df)}")
        return 2
    df = df.dropna(subset=["ret20"])            # 核心指标缺失剔除
    corr = df.corr(method="spearman")
    eig = np.sort(np.linalg.eigvalsh(corr.values))[::-1]
    evr = [round(float(e / eig.sum()), 3) for e in eig[:3]]
    clus = clusters(corr, RHO_CLUSTER)
    pairs = []
    cols = list(corr.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            pairs.append((a, b, round(float(corr.loc[a, b]), 3)))
    pairs.sort(key=lambda x: -abs(x[2]))

    out = {"T": T, "n": len(df), "rho_cluster_threshold": RHO_CLUSTER,
           "clusters": clus, "n_effective_dims": len(clus),
           "pca_evr_top3": evr,
           "pairs_by_absrho": [{"a": a, "b": b, "rho": r} for a, b, r in pairs],
           "corr": corr.round(3).to_dict()}
    stamp = datetime.now().strftime("%Y%m%d")
    json.dump(out, open(os.path.join(ROOT, "data", f"indicators_overlap_audit_{T}.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)

    L = [f"# 生产指标池同源性审计（截面 T={T}, n={len(df)} 只·描述性零判据）",
         "",
         f"> 来源 math_knowledge_full_study_20260920 域五⑥ ｜ 纯描述审计, 不构成任何指标降权/删除裁决",
         "",
         "## 信息族（|ρ|>0.7 单连聚类）",
         f"- **有效信息维度 = {len(clus)} 族**（指标池 {len(cols)} 个 → 真实维度 {len(clus)}）"]
    for g in clus:
        inner = ", ".join(g)
        L.append(f"  - 族[{inner}]")
    L += ["",
          "## 前2主成分方差占比（相关矩阵特征值）",
          f"- PC1={evr[0] * 100:.1f}% / PC2={evr[1] * 100:.1f}% / PC3={evr[2] * 100:.1f}%",
          "",
          "## 相关最强的指标对 Top10（|Spearman ρ|）"]
    for a, b, r in pairs[:10]:
        flag = " 🔴同簇" if abs(r) > RHO_CLUSTER else ""
        L.append(f"- {a} × {b}: ρ={r:+.3f}{flag}")
    L += ["",
          "## 含义（观察面）",
          "- 同簇指标互证≈重复计数: 违反「证据独立性计数纪律」的数学面(共振信号须按族去重后计 1)",
          "- 跨族组合(动量×二阶矩×量能×位置)才是真·多维证据; 生产查四/扫描器多指标命中时建议按本表族号折算",
          "- 本审计为单截面快照, 指标相关性随 regime 漂移, 复审建议月频",
          ""]
    open(os.path.join(ROOT, "reports", f"indicators_overlap_audit_{stamp}.md"), "w", encoding="utf-8").write("\n".join(L))
    print(f"[IND-OK] T={T} n={len(df)} | 有效维度={len(clus)}族 | PC1={evr[0] * 100:.1f}% PC2={evr[1] * 100:.1f}%")
    for a, b, r in pairs[:6]:
        print(f"  {a} × {b}: {r:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
