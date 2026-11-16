# -*- coding: utf-8 -*-
"""
W6 主线二波统计 + 信号族事件日正交矩阵 (B站学习第43轮, 2026-10-06)

立项依据(prereg_preflight 20261006 CONFLICT已论证): 命中追涨/二波家族与SENTIMENT-CYCLE族。
差异论证: ①二波统计=同行业主线段间隔分布的**描述统计**(游资"龙回头/二波"概念的定量刻画),
不构成"蹲二波"买入依据(追涨六次证伪在案); ②正交矩阵=三个已验证信号的事件日重叠结构刻画
(候选甲实质推进,为候选辛仓位模型的信号组合层铺垫),非新信号非接线。全部零交易权。

判据 LOCKED:
A. 二波: data/tmp mainline_rotation_20261006.json segments(第40轮1492段), 同行业按start排序,
   相邻段间隔=后段start-前段end(交易日近似用自然日差/段内日历, 统一用交易日索引差——
   简化: 用日期序数在dates索引中查, 40轮JSON无dates索引→用python datetime自然日差并标注口径)。
   统计: 间隔分布(≤5日/6-20/21-60/>60), 二段vs首段长度中位对比, 各行业二波次数Top。
B. 正交矩阵(共同窗2026-01-19~2026-09-30):
   - PD-031深位首板: data/deepfirst_shadow_ledger.jsonl 520行, 事件日=date(int转str), 去重
   - PD-034恐慌: data/panic_overshoot_shadow.jsonl, 事件日=event_date, 取2026窗内
   - 深贴回补: data/sold_rebuy_shadow_ledger.jsonl, 事件日=day(kind=fill), 去重
   指标: 两两 |A∩B|/min(|A|,|B|) 重叠率 + 配对最小间隔(自然日)分布中位。
   结构正交判定沿用第38轮口径: 重叠率低=时间层正交。
"""
import json
from collections import defaultdict
from datetime import datetime

SEGS = r"D:\zcodesj\tmp\mainline_rotation_20261006.json"
DEEP = r"D:\zcodesj\data\deepfirst_shadow_ledger.jsonl"
PANIC = r"D:\zcodesj\data\panic_overshoot_shadow.jsonl"
REBUY = r"D:\zcodesj\data\sold_rebuy_shadow_ledger.jsonl"
OUT = r"D:\zcodesj\tmp\second_wave_orthogonality_20261006.json"

WIN_LO, WIN_HI = "20260119", "20260930"


def d2s(x):
    return str(x)


def days_between(a, b):
    da = datetime.strptime(a, "%Y%m%d")
    db = datetime.strptime(b, "%Y%m%d")
    return abs((db - da).days)


def med(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def main():
    result = {}

    # ---------- A. 主线二波统计 ----------
    segs = json.load(open(SEGS, encoding="utf-8"))["segments"]
    by_ind = defaultdict(list)
    for s in segs:
        by_ind[s["ind"]].append(s)
    for ind in by_ind:
        by_ind[ind].sort(key=lambda x: x["start"])

    gaps, pair_rows = [], []
    for ind, lst in by_ind.items():
        for a, b in zip(lst, lst[1:]):
            g = days_between(a["end"], b["start"])
            gaps.append(g)
            pair_rows.append({"ind": ind, "first": a["start"] + "~" + a["end"],
                               "second": b["start"] + "~" + b["end"], "gap_days": g,
                               "len1": a["days"], "len2": b["days"]})
    bucket = {"<=5": 0, "6-20": 0, "21-60": 0, ">60": 0}
    for g in gaps:
        k = "<=5" if g <= 5 else ("6-20" if g <= 20 else ("21-60" if g <= 60 else ">60"))
        bucket[k] += 1
    second_longer = sum(1 for p in pair_rows if p["len2"] >= p["len1"])
    multi = sorted(((ind, len(v)) for ind, v in by_ind.items() if len(v) >= 4), key=lambda x: -x[1])
    result["second_wave"] = {
        "meta": {"locked": "同行业相邻主线段; 间隔=后段start与前段end的自然日差(口径注明非交易日差)"},
        "n_pairs": len(gaps),
        "gap_dist": bucket,
        "gap_med": med(gaps),
        "second_len_vs_first": {"n": len(pair_rows),
                                  "len1_med": med([p["len1"] for p in pair_rows]),
                                  "len2_med": med([p["len2"] for p in pair_rows]),
                                  "second_ge_first_pct": round(100 * second_longer / max(len(pair_rows), 1), 1)},
        "ind_most_segments_top8": multi[:8],
        "sample_pairs": pair_rows[-8:],
    }

    # ---------- B. 三信号事件日正交矩阵 ----------
    d1 = sorted({d2s(r["date"]) for r in map(json.loads, open(DEEP, encoding="utf-8")) if r.get("date")})
    p1 = sorted({r["event_date"] for r in map(json.loads, open(PANIC, encoding="utf-8"))
                 if WIN_LO <= r.get("event_date", "") <= WIN_HI})
    r1 = sorted({r["day"] for r in map(json.loads, open(REBUY, encoding="utf-8"))
                 if r.get("kind") == "fill" and WIN_LO <= r.get("day", "") <= WIN_HI})
    sets = {"PD031_深位首板": d1, "PD034_恐慌超跌": p1, "深贴回补": r1}

    def nearest_gap(a_set, b_set, cap=9999):
        gs = []
        bl = list(b_set)
        import bisect
        for a in a_set:
            i = bisect.bisect_left(bl, a)
            cand = []
            if i < len(bl):
                cand.append(days_between(a, bl[i]))
            if i > 0:
                cand.append(days_between(a, bl[i - 1]))
            if cand and min(cand) <= cap:
                gs.append(min(cand))
        return gs

    orth = {}
    names = list(sets)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            A, B = set(sets[names[i]]), set(sets[names[j]])
            inter = len(A & B)
            denom = min(len(A), len(B))
            gs = nearest_gap(sorted(A), sorted(B))
            orth[f"{names[i]} × {names[j]}"] = {
                "nA": len(A), "nB": len(B), "intersect": inter,
                "overlap_pct_of_min": round(100 * inter / denom, 1) if denom else None,
                "nearest_gap_med_days": med(gs),
            }
    result["orthogonality"] = {
        "meta": {"window": [WIN_LO, WIN_HI],
                  "note": "事件日集合; 重叠率分母=min(nA,nB); 间隔=自然日近邻配对中位"},
        "event_days": {k: len(v) for k, v in sets.items()},
        "pairs": orth,
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"result": result}, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    main()
