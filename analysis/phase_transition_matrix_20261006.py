# -*- coding: utf-8 -*-
"""
W4 相位转移矩阵+四期循环检验+主线死亡日交叉 (B站学习第41轮, 2026-10-06)

立项依据(引用prereg_preflight 20261006): CONFLICT命中SENTIMENT-CYCLE证伪族(日频择时不成立)
+PREREG_EMOTION_PHASE_POSITION。差异论证: 本研究=相位间转移概率的**描述统计**(循环结构刻画),
不产出任何"据相位调仓"规则, 与第27轮勘误口径一致(描述性结构/fwd统计保持有效); emotion_phase_v2
本体PREREG_LOCKED只读, 本脚本仅读台账, 零改动零接线零交易权。

判据 LOCKED:
- 数据: data/emotion_phase_v2_ledger.jsonl 424行(phase_date,phase) 按日排序去重
- 转移矩阵: 相邻交易日相位转移计数/条件概率(5态: bottom/healing/euphoric/diverging/ebbing)
- 期望停留: run长度分布直接统计(连续同相位天数) + 1/(1-p_ii)对照
- 循环检验(主人四期框架 冰点→发酵→主升→分化/退潮):
  顺循环边集 = bottom→healing, healing→euphoric, euphoric→diverging, diverging→ebbing, ebbing→bottom
  逆循环边集 = healing→bottom, euphoric→healing(跳过diverging回发酵), diverging→euphoric, ebbing→diverging
  报告顺/逆/自环/其余四类占比
- 主线死亡日交叉: tmp/mainline_rotation_20261006.json segments(第40轮), end>=20250101子集,
  死亡日T/T+1/T+5相位分布 vs 全样本基线; 假设=死亡日富集于ebbing/diverging
"""
import json
import os
from collections import Counter, defaultdict

HERE = r"D:\zcodesj"
LEDGER = os.path.join(HERE, "data", "emotion_phase_v2_ledger.jsonl")
SEGS = os.path.join(HERE, "tmp", "mainline_rotation_20261006.json")
OUT = os.path.join(HERE, "tmp", "phase_transition_20261006.json")

STATES = ["bottom", "healing", "euphoric", "diverging", "ebbing"]
CLOCKWISE = {("bottom", "healing"), ("healing", "euphoric"), ("euphoric", "diverging"),
             ("diverging", "ebbing"), ("ebbing", "bottom")}
COUNTER_ = {("healing", "bottom"), ("euphoric", "healing"), ("diverging", "euphoric"),
            ("ebbing", "diverging")}


def classify(a, b):
    if a == b:
        return "self"
    if (a, b) in CLOCKWISE:
        return "clockwise"
    if (a, b) in COUNTER_:
        return "counter"
    return "other"  # 跳相转移


def main():
    rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
    seen, seq = set(), []
    for r in sorted(rows, key=lambda x: x["phase_date"]):
        d = r["phase_date"]
        if d in seen:
            continue
        seen.add(d)
        seq.append((d, r["phase"]))
    n = len(seq)
    print(f"ledger unique days={n} span={seq[0][0]}..{seq[-1][0]}")

    # 转移矩阵
    trans = defaultdict(Counter)
    for i in range(n - 1):
        trans[seq[i][1]][seq[i + 1][1]] += 1
    matrix = {}
    for a in STATES:
        tot = sum(trans[a].values())
        matrix[a] = {b: {"n": trans[a].get(b, 0),
                          "p": (round(trans[a].get(b, 0) / tot, 3) if tot else None)}
                      for b in STATES}

    # 转移类型占比
    tc = Counter()
    for i in range(n - 1):
        tc[classify(seq[i][1], seq[i + 1][1])] += 1
    trans_types = {k: {"n": v, "pct": round(100 * v / (n - 1), 1)} for k, v in tc.most_common()}

    # run长度分布(期望停留)
    runs = defaultdict(list)
    s, cur = 0, seq[0][1]
    for i in range(1, n + 1):
        if i == n or seq[i][1] != cur:
            runs[cur].append(i - s)
            if i < n:
                s, cur = i, seq[i][1]
    run_stats = {}
    for st in STATES:
        xs = sorted(runs.get(st, []))
        if not xs:
            run_stats[st] = {"n_runs": 0}
            continue
        k = len(xs)
        run_stats[st] = {"n_runs": k, "med_days": xs[k // 2] if k % 2 else (xs[k // 2 - 1] + xs[k // 2]) / 2,
                          "mean_days": round(sum(xs) / k, 1), "max": xs[-1]}

    # 日期索引 + 主线死亡日交叉
    didx = {d: i for i, (d, _) in enumerate(seq)}
    dates = [d for d, _ in seq]
    segs = json.load(open(SEGS, encoding="utf-8"))["segments"]
    death_days = sorted({s["end"] for s in segs if s["end"] >= "20250101" and s["end"] in didx})
    print(f"death days in ledger window: {len(death_days)}")

    def phase_at(dates_back):
        idx = didx[dates_back]
        return seq[idx][1]

    death_phase_T = Counter(phase_at(d) for d in death_days)
    death_phase_T1 = Counter(seq[min(didx[d] + 1, n - 1)][1] for d in death_days)
    death_phase_T5 = Counter(seq[min(didx[d] + 5, n - 1)][1] for d in death_days if didx[d] + 5 < n)
    base = Counter(p for _, p in seq)
    base_p = {k: round(100 * v / n, 1) for k, v in base.items()}
    dd = len(death_days)
    cross = {
        "n_death_days": dd,
        "baseline_pct": base_p,
        "death_T_pct": {k: round(100 * v / dd, 1) for k, v in death_phase_T.most_common()},
        "death_T1_pct": {k: round(100 * v / dd, 1) for k, v in death_phase_T1.most_common()},
        "death_T5_pct": {k: round(100 * v / len([d for d in death_days if didx[d] + 5 < n]), 1)
                          for k, v in death_phase_T5.most_common()},
    }
    ebb_like_T = sum(v for k, v in death_phase_T.items() if k in ("ebbing", "diverging"))
    cross["death_T_ebbing_or_diverging_pct"] = round(100 * ebb_like_T / dd, 1)

    result = {
        "meta": {"locked": "转移矩阵/run长度/循环边集全部写死; 描述统计非择时; v2台账只读",
                  "days": n, "span": [dates[0], dates[-1]],
                  "phase_base_pct": base_p},
        "transition_matrix": matrix,
        "transition_types": trans_types,
        "run_length": run_stats,
        "mainline_death_cross": cross,
        "death_days_sample": death_days[-15:],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"result": result, "seq": seq}, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    main()
