#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# ⛔ 停用封存（2026-09-20 主人裁决 PD-03 选 C 停用）：
#    sentiment_cycle 设施整体停用（falsified 第63条 SENTIMENT-CYCLE-V1：
#    全史重放 n=2604 无判别力+"退潮"标签方向相反，与 P-B1 双路同向证伪）。
#    台账 data/sentiment_cycle_daily.jsonl 只读封存；替代覆盖=MARKETSTATE_2000
#    +limit_up_patterns 库。本脚本随设施封存禁止运行；
#    复活仅允许以可回测新判据走 prereg→影子→三闸，禁止复活 v1 阈值。
#    裁决材料：reports/sentiment_cycle_verdict_material_20260920.md
# ---------------------------------------------------------------------------
r"""
sentiment_cycle_calib_v3.py — 情绪状态机第二候选预注册标定（PREREG-SENTIMENT-TRIPLE-V3）

三分制重分类（高/中/低，梯队结构主导：连板高度+晋级率，弃用炸板率）。
主判据 P1/P2/P4（样本内自含，P4=前100/后100 分段稳定性，针对 regime 依赖）；
P3 样本外参考项（先验污染已在 prereg 声明）。纯离线不写台账。
退出码：0 完成；2 数据不可用；3 指纹不符
"""
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREREG = ROOT / "data" / "prereg_sentiment_triple_v3_20260909.json"
LEDGER = ROOT / "data" / "sentiment_cycle_daily.jsonl"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def v3_classify(m: dict) -> str:
    mb, pmb, promo = m.get("max_board"), m.get("prev_max_board"), m.get("promotion_rate")
    if mb is None:
        return "unknown"
    high = mb >= 5 or (promo is not None and promo >= 0.40)
    low = mb <= 2 or (pmb is not None and (pmb - mb) >= 2)
    if high and low:
        return "低"   # 防守优先（prereg 冻结）
    return "低" if low else ("高" if high else "中")


def load_ew(d0: str) -> dict:
    conn = sqlite3.connect(QFQ_DB)
    rows = conn.execute(
        "SELECT date, AVG(change_pct) FROM stock_daily_meta_qfq "
        "WHERE date>=? AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%') "
        "GROUP BY date ORDER BY date", (d0.replace("-", ""),)).fetchall()
    conn.close()
    return {f"{d[:4]}-{d[4:6]}-{d[6:]}": r for d, r in rows if r is not None}


def next_stats(rows: list, ew: dict, states=("高", "低")) -> dict:
    dates_ew = sorted(ew)
    nxt = {d: dates_ew[i + 1] for i, d in enumerate(dates_ew[:-1])}
    out = defaultdict(list)
    for r in rows:
        nd = nxt.get(r["date"])
        if r["date"] in ew and nd is not None and r.get("state") in states:
            out[r["state"]].append(ew[nd])
    return out


def mean(v):
    return sum(v) / len(v) if v else None


def main() -> int:
    preg = json.loads(PREREG.read_text(encoding="utf-8"))
    core = json.dumps({"t": preg["frozen_thresholds_v3"], "c": preg["pass_criteria"],
                       "s": preg["sample_split"]}, ensure_ascii=False, sort_keys=True)
    if hashlib.sha256(core.encode("utf-8")).hexdigest()[:8] != preg.get("fingerprint"):
        print("[FAIL] prereg 指纹不符", file=sys.stderr)
        return 3
    rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows.sort(key=lambda r: r["date"])
    ew = load_ew(rows[0]["date"])
    rows = [r for r in rows if r["date"] in ew]
    n_in = int(re.search(r"前\s*(\d+)\s*日", preg["sample_split"]["in_sample"]).group(1))
    split_in, split_out = rows[:n_in], rows[n_in:]
    print(f"prereg {preg['id']} 指纹 {preg['fingerprint']} | 台账 {len(rows)} 日 "
          f"样本内 {len(split_in)} / 样本外 {len(split_out)}")

    for r in rows:
        r["state"] = v3_classify(r.get("metrics") or {})

    dist = Counter(r["state"] for r in rows)
    n = len(rows)
    hi_p, lo_p = dist.get("高", 0) / n, dist.get("低", 0) / n
    p1 = 0.15 <= hi_p <= 0.55 and 0.10 <= lo_p <= 0.50
    print(f"\n【P1 分布均衡】高{dist.get('高', 0)}({hi_p:.0%}) 中{dist.get('中', 0)}({dist.get('中', 0) / n:.0%}) "
          f"低{dist.get('低', 0)}({lo_p:.0%}) unknown{dist.get('unknown', 0)} -> {'PASS' if p1 else 'FAIL'}")

    s_in = next_stats(split_in, ew)
    hi, lo = mean(s_in.get("高", [])), mean(s_in.get("低", []))
    p2 = hi is not None and lo is not None and hi > lo
    print(f"【P2 样本内方向】高:{hi:+.2f}%(n={len(s_in.get('高', []))}) "
          f"低:{lo:+.2f}%(n={len(s_in.get('低', []))}) -> {'PASS' if p2 else 'FAIL'}")

    half = len(split_in) // 2
    h1 = next_stats(split_in[:half], ew)
    h2 = next_stats(split_in[half:], ew)
    d1 = (mean(h1.get("高")) - mean(h1.get("低"))) if h1.get("高") and h1.get("低") else None
    d2 = (mean(h2.get("高")) - mean(h2.get("低"))) if h2.get("高") and h2.get("低") else None
    p4 = d1 is not None and d2 is not None and d1 > 0 and d2 > 0
    print(f"【P4 分段稳定性】前半 高-低差:{d1:+.2f}% | 后半:{d2:+.2f}% "
          f"-> {'PASS(两半均高>低)' if p4 else 'FAIL'}")

    s_out = next_stats(split_out, ew)
    ho, loo = mean(s_out.get("高", [])), mean(s_out.get("低", []))
    if ho is None or loo is None:
        p3 = None
        p3_s = "N/A（样本外某态无样本）"
    else:
        p3 = ho > loo
        p3_s = f"高:{ho:+.2f}%(n={len(s_out.get('高', []))}) 低:{loo:+.2f}%(n={len(s_out.get('低', []))}) " \
               f"-> {'一致(完整过闸)' if p3 else '反转(条件过闸+复核条款)'}"
    print(f"【P3 样本外参考(污染声明在案)】{p3_s}")

    if not (p1 and p2 and p4):
        verdict = "此路线关闭：梯队结构三分制未过主判据，不再第三轮调阈值（判死须档案引用）"
        grade = "FAIL"
    elif p3 is True:
        verdict = "完整过闸：v3 三分制过闸候选（仍影子，接线须主人裁决）"
        grade = "PASS_FULL"
    else:
        verdict = "条件过闸：主判据过但样本外反转/缺失（污染风险在案），影子运行+台账积累后复核"
        grade = "PASS_CONDITIONAL"

    print(f"\n【VERDICT】{verdict}")
    preg["out_of_sample_results"] = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dist": {k: dist.get(k, 0) for k in ("高", "中", "低", "unknown")},
        "in_high": hi, "in_low": lo, "half1_diff": d1, "half2_diff": d2,
        "out_high": ho, "out_low": loo,
        "P1": "PASS" if p1 else "FAIL", "P2": "PASS" if p2 else "FAIL",
        "P4": "PASS" if p4 else "FAIL", "P3": ("PASS" if p3 else "FAIL") if p3 is not None else "N/A",
        "grade": grade, "verdict": verdict,
    }
    PREREG.write_text(json.dumps(preg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 结果已存档 {PREREG.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
