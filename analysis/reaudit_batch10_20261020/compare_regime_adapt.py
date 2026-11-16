# -*- coding: utf-8 -*-
"""REGIME-ADAPT-STAGE1 96折重跑对拍器(reaudit round10)。数字全部工件直出。

读入: analysis/reaudit_batch10_20261020/regime_adapt_results_original.bak.jsonl (原档96折)
      attribution/regime_adapt_stage1_results.jsonl (重跑96折, 跑完从原路径移入本目录)
输出: analysis/reaudit_batch10_20261020/compare_result.json
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ORIG = os.path.join(HERE, "regime_adapt_results_original.bak.jsonl")
RERUN = os.path.join(HERE, "regime_adapt_results_rerun.jsonl")
OUT = os.path.join(HERE, "compare_result.json")


def load(path):
    rows = {}
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
            rows[(r["arm"], r["month"])] = r
        except Exception:
            continue
    return rows


def arm_stats(rows, arm):
    ms = sorted(m for (a, m) in rows if a == arm)
    v = [rows[(arm, m)]["rankic"] for m in ms]
    n = len(v)
    mean = sum(v) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in v) / (n - 1)) if n > 1 else float("nan")
    t = mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    yearly = {}
    for m, x in zip(ms, v):
        yearly.setdefault(m[:4], []).append(x)
    yearly_mean = {y: sum(x) / len(x) for y, x in yearly.items()}
    return {"n_folds": n, "months": ms, "rankic_mean": round(mean, 6), "rankic_t": round(t, 2),
            "auc_mean": round(sum(rows[(arm, m)]["auc"] for m in ms) / n, 6),
            "topq_mean": round(sum(rows[(arm, m)]["topq_excess"] or 0 for m in ms) / n, 6),
            "yearly_rankic_mean": {y: round(x, 6) for y, x in yearly_mean.items()}}


def paired(rows_a, rows_b, arm_a, arm_b, key="rankic"):
    ms = sorted(m for (a, m) in rows_a if a == arm_a and (arm_b, m) in rows_b)
    d = [rows_a[(arm_a, m)][key] - rows_b[(arm_b, m)][key] for m in ms]
    n = len(d)
    mean = sum(d) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (n - 1)) if n > 1 else float("nan")
    t = mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    yearly = {}
    for m, x in zip(ms, d):
        yearly.setdefault(m[:4], []).append(x)
    return {"n_pairs": n, "delta_mean": round(mean, 6), "delta_t": round(t, 2),
            "yearly_delta_mean": {y: round(sum(x) / len(x), 6) for y, x in yearly.items()}}


def fold_diff(orig, rerun):
    diffs = []
    for k in sorted(orig):
        if k not in rerun:
            diffs.append({"fold": k, "kind": "missing_in_rerun"})
            continue
        for f in ("rankic", "auc", "topq_excess", "n_fit", "n_val", "n_oos", "rounds"):
            a, b = orig[k].get(f), rerun[k].get(f)
            if a is None or b is None:
                continue
            if abs(a - b) > (1e-9 if f != "rankic" else 1e-9):
                diffs.append({"fold": k, "field": f, "orig": a, "rerun": b,
                              "delta": round(b - a, 8)})
    return diffs


if __name__ == "__main__":
    o, r = load(ORIG), load(RERUN)
    res = {
        "orig_folds": len(o), "rerun_folds": len(r),
        "arms": {a: {"orig": arm_stats(o, a), "rerun": arm_stats(r, a)}
                 for a in ("A0", "B1", "B2")},
        "paired": {
            "B1_minus_A0": {"orig": paired(o, o, "B1", "A0"), "rerun": paired(r, r, "B1", "A0")},
            "B2_minus_A0": {"orig": paired(o, o, "B2", "A0"), "rerun": paired(r, r, "B2", "A0")},
        },
        "fold_level_diff_count": len(fold_diff(o, r)),
        "fold_level_diff_sample": fold_diff(o, r)[:12],
    }
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", OUT)
    for a in ("A0", "B1", "B2"):
        print(a, "orig mean=%.6f t=%s | rerun mean=%.6f t=%s" % (
            res["arms"][a]["orig"]["rankic_mean"], res["arms"][a]["orig"]["rankic_t"],
            res["arms"][a]["rerun"]["rankic_mean"], res["arms"][a]["rerun"]["rankic_t"]))
    for p in ("B1_minus_A0", "B2_minus_A0"):
        po, pr = res["paired"][p]["orig"], res["paired"][p]["rerun"]
        print(p, "orig d=%.6f t=%s | rerun d=%.6f t=%s" % (
            po["delta_mean"], po["delta_t"], pr["delta_mean"], pr["delta_t"]))
    print("fold_level_diffs:", res["fold_level_diff_count"])
