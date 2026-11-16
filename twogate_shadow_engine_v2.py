#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""twogate_shadow_engine_v2.py — 两道门 V2 影子引擎（PREREG_TWOGATE_V2_20261001, B'' 口径）
================================================================================
裁决来源: 主人 2026-10-01「按推荐继续」终批裁决二 B''（Q/R 双轨实验支撑,
         reports/sentiment_z2s_conditional_study_20261001.md 八轮 Q/R 段）。
与 V1（twogate_shadow_engine.py, LOCKED 指纹 5e4ae13bf1a5837c）的关系:
  - V1 全程不动不停不改, 继续为在档影子;
  - V2 为并行新影子: regime 门换自建精确涨停价口径(严口径=收盘封住),
    主线门降级为清洁序列观察标签(不过门), 过门=(E1∨E5)∧regime≠冰点;
  - 信号定义/settle 口径(fwd10 次开, 双边 0.3%)/去重与 V1 同式。
判据(预声明, tmp/twogate_B2_startup_kit_20261001.md §三):
  ≥30 交易日对照: V2 过门池 fwd10 不劣于 V1 同窗池(容差-0.3pp) 且
  V2 冰点排除组显著为负(预期≈-0.89%, E5 冰点档 R 轨)。FAIL→V2 废弃归档。
用法:
  python twogate_shadow_engine_v2.py --build-lu    # 精确LU序列预计算(qfq单遍→data/sentiment_precise_lu.json)
  python twogate_shadow_engine_v2.py --fingerprint # prereg 冻结指纹
  python twogate_shadow_engine_v2.py --settle      # v2台账结算+对照读数
  from twogate_shadow_engine_v2 import gate_label_v2  # scanner 双轨调用
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import sqlite3
import sys

import numpy as np

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
LU_CACHE = os.path.join(ROOT, "data", "sentiment_precise_lu.json")
CLEAN_ML = os.path.join(ROOT, "data", "clean_industry_momentum_pctl_20261001.json")
IND_MAP_THS_DB = r"file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
LEDGER2 = os.path.join(ROOT, "data", "twogate_shadow_v2_ledger.jsonl")
STATUS2 = os.path.join(ROOT, "data", "twogate_shadow_v2_status.json")
COST_RT = 0.003

CFG2 = {
    "lu_rule": "precise_limit_price_close>=round(prev*(1+10%|20%),2)-0.005",
    "regime_cuts": [30, 60, 100], "mainline": "clean_industry_pctl>=0.8_as_label_only",
    "gate": "(E1|E5) AND regime!=ice", "settle": "fwd10_next_open_no_stop_gross_net0.3pct",
    "dup": "same as V1 scanner flow",
}


def fingerprint() -> str:
    return hashlib.sha256(json.dumps(CFG2, sort_keys=True).encode()).hexdigest()[:16]


def regime4(lu: int) -> str:
    if lu <= 30:
        return "冰点≤30"
    if lu <= 60:
        return "修复31-60"
    if lu <= 100:
        return "温暖61-100"
    return "高潮>100"


def build_lu():
    """精确涨停价口径 LU 序列预计算(qfq 全市场单遍) → data/sentiment_precise_lu.json。幂等覆盖。"""
    from collections import defaultdict
    con = sqlite3.connect(QFQ_DB, uri=True)
    rows = con.execute(
        "SELECT code, date, close FROM stock_daily_meta_qfq "
        "WHERE (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date").fetchall()
    con.close()
    per_day = defaultdict(int)
    panel_c = {}
    for code, d, c in rows:
        if code[:3] in ("688", "689") or code[:1] in ("4", "8"):
            continue
        panel_c.setdefault(code, []).append((d, c))
    for code, seq in panel_c.items():
        gem = code[:3] in ("300", "301")
        lim = 0.20 if gem else 0.10
        for i in range(1, len(seq)):
            pc, cl = seq[i - 1][1], seq[i][1]
            if not pc or pc <= 0 or cl is None:
                continue
            if cl >= round(pc * (1 + lim), 2) - 0.005:
                per_day[seq[i][0]] += 1
    out = {d: n for d, n in sorted(per_day.items())}
    with open(LU_CACHE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[V2-BUILD-LU] {len(out)} 日 -> {LU_CACHE} (最新 {max(out) if out else 'NA'})")
    return out


def load_lu() -> dict:
    if not os.path.exists(LU_CACHE):
        raise SystemExit("[V2] LU 缓存缺失——先跑 --build-lu (生成物由命令生成, 禁手编)")
    with open(LU_CACHE, encoding="utf-8") as f:
        return json.load(f)


_ML_CODES = None


def load_mainline_labels() -> dict:
    """清洁序列主线观察标签(不过门): date -> set(code)。THS L1 行业分位>=0.8。"""
    global _ML_CODES
    if _ML_CODES is not None:
        return _ML_CODES
    with open(CLEAN_ML, encoding="utf-8") as f:
        clean = json.load(f)
    con = sqlite3.connect(IND_MAP_THS_DB, uri=True)
    ind_map = {}
    for code, ind in con.execute("SELECT code, industry FROM industry_map_ths"):
        ind_map[str(code).zfill(6)] = str(ind).split("-")[0]
    con.close()
    by_ind = {}
    for c, ind in ind_map.items():
        by_ind.setdefault(ind, []).append(c)
    out = {}
    for d, pctl in clean.items():
        s = set()
        for ind, p in pctl.items():
            if p >= 0.8:
                s.update(by_ind.get(ind, ()))
        out[d] = s
    _ML_CODES = out
    return out


def gate_label_v2(code: str, signal_date, tail):
    """V2 单票标签: gate=(E1∨E5)∧regime_v2≠冰点; mainline_label 仅观察。
    tail=vipdoc 尾窗(与 V1 read_vipdoc_tail 同源, 由调用方传入)。signal_date=int YYYYMMDD。"""
    import twogate_shadow_engine as v1
    lu = load_lu().get(str(signal_date))
    if lu is None:
        return None
    g = regime4(lu)
    sig = v1.eval_prereg_signals(tail) if tail else None
    sigs = {"E1": bool(sig["E1"]), "E5": bool(sig["E5"])} if sig else {"E1": False, "E5": False}
    ml_label = code in load_mainline_labels().get(str(signal_date), set())
    gate = bool((sigs["E1"] or sigs["E5"]) and g != "冰点≤30")
    return dict(regime=g, mainline_label=ml_label, gate_pass=gate,
                signals=sigs, lu_precise=lu, engine="v2", fp=fingerprint())


def settle():
    """V2 台账结算(fwd10 次开, 与 V1 settle 同式) + 双轨对照读数。幂等。"""
    import twogate_shadow_engine as v1
    rows = []
    if os.path.exists(LEDGER2):
        with open(LEDGER2, encoding="utf-8") as fp:
            rows = [json.loads(x) for x in fp if x.strip()]
    changed = 0
    for r in rows:
        if r.get("fwd10_net") is not None:
            continue
        d8 = int(r["date"])
        tail = v1.read_vipdoc_tail(str(r["code"]))
        if not tail:
            continue
        dates = [x[0] for x in tail]
        if d8 not in dates:
            continue
        i = dates.index(d8)
        if i + 10 >= len(tail):
            continue
        fwd = tail[i + 10][4] / tail[i][4] - 1
        r["fwd10_gross"] = round(fwd, 6)
        r["fwd10_net"] = round(fwd - COST_RT, 6)
        r["settled_date"] = int(tail[i + 10][0])
        changed += 1
    with open(LEDGER2, "w", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(r, ensure_ascii=False) + "\n")

    def stat(sub):
        vals = [r["fwd10_net"] for r in sub]
        if not vals:
            return dict(n=0)
        a = np.array(vals)
        return dict(n=len(a), net_mean=round(float(a.mean()), 5),
                    win=round(float((a > 0).mean()), 4))

    settled = [r for r in rows if r.get("fwd10_net") is not None]
    pass_g = stat(settled)
    # 冰点排除组(信号命中但被 regime 精确口径拦下) —— 预期显著为负(判据②)
    ice_g = stat([r for r in settled if r.get("gate_pass") is False])
    # V1 同窗对照(判据①)
    v1_pass = None
    v1_rows = []
    v1_ledger = os.path.join(ROOT, "data", "twogate_shadow_ledger.jsonl")
    if os.path.exists(v1_ledger):
        with open(v1_ledger, encoding="utf-8") as fp:
            v1_rows = [json.loads(x) for x in fp if x.strip()]
    v1_settled_dates = {r["date"] for r in v1_rows if r.get("fwd10_net") is not None}
    v1_common = [r for r in v1_rows if r.get("fwd10_net") is not None
                 and str(r["date"]) in {x["date"] for x in settled if str(x["date"]) in v1_settled_dates}]
    v1_pass = stat(v1_common)
    diff = (round(pass_g["net_mean"] - v1_pass["net_mean"], 5)
            if pass_g.get("n") and v1_pass.get("n") else None)
    n_days = len({r["date"] for r in settled})
    status = dict(asof=max((int(r["date"]) for r in rows), default=0),
                  total_rows=len(rows), settled=len(settled),
                  pending=sum(1 for r in rows if r.get("fwd10_net") is None),
                  signal_days=n_days, v2过门=pass_g, v2冰点排除=ice_g,
                  v1同窗过门=v1_pass, 差值pp=diff,
                  judge={"J1_不劣于V1(容差-0.3pp)": bool(diff is not None and diff >= -0.003),
                         "J2_冰点排除显著负": bool(ice_g.get("n", 0) >= 20 and ice_g["net_mean"] < 0)},
                  window="≥30交易日对照后呈主人终裁切主; FAIL→V2废弃归档",
                  fingerprint=fingerprint(), engine="v2")
    with open(STATUS2, "w", encoding="utf-8") as fp:
        json.dump(status, fp, ensure_ascii=False, indent=1)
    print(f"[V2-SETTLE] 新结算 {changed} | 已结算 {len(settled)} | 未到期 {len(rows)-len(settled)}")
    print(f"[V2-READOUT] 过门 {pass_g} | 冰点排除 {ice_g} | V1同窗 {v1_pass} | 差 {diff}")
    return status


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else ""
    if a == "--build-lu":
        build_lu()
    elif a == "--fingerprint":
        print(fingerprint())
    elif a == "--settle":
        settle()
    else:
        print(__doc__)
