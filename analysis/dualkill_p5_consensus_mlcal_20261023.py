#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dualkill_p5_consensus_mlcal_20261023.py — 10/19双杀协议 P5: 双引擎共识度分层+ml IC季度校准
==========================================================================================
依据: dualkill_verification_protocol_20261019 建议4(主人批准 P5; 带10/8确认闸FAIL先例注记, 预期保守)。
  共识标签: 每调仓日 bp_ep Top15(生产gated口径) × ml Top20(stock_ml_scores_v22, 生产ml腿口径)
    → dual/bp_only/ml_only 三标签, fwd20等权收益+全宇宙等权超额。
  ml IC 季度校准: 逐采样日 ml_prob 全截面 vs fwd20超额 RankIC, 季度聚合; 全窗十分位单调性。
背景: ml打分腿已于8/29退役出生产组合(config 1.0/0.0), 本研究为再评估诊断, 非权重提案。
零交易权纯呈现; n=采样受限, 方向性结论。
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
ML_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT_DIR = os.path.join(ROOT, "reports", "dualkill_p5_consensus_mlcal_20261023")
WINDOW_START = "2025-01-01"
HOLD_BARS = 20
REBALANCE_BARS = 22


def dash(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def rank_ic(scores, rets):
    """scores/rets: 同序两列表。Spearman。"""
    n = len(scores)
    if n < 10:
        return None

    def rk(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0] * n
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    a, b = rk(scores), rk(rets)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = (sum((x - ma) ** 2 for x in a) * sum((x - mb) ** 2 for x in b)) ** 0.5
    return cov / va if va else 0.0


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ladder_tpl",
                                                  os.path.join(HERE, "bt_full_report_ladder_20261019.py"))
    tpl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tpl)
    loader = tpl.TulongDataLoader(tpl.DB_PATH, tpl.QFQ_PATH)
    ml_con = sqlite3.connect(ML_DB, uri=True)

    # px 全窗
    q = sqlite3.connect(QFQ, uri=True)
    rows = q.execute("SELECT date, code, close FROM stock_daily_meta_qfq "
                     "WHERE date>=20250101 AND date<=20261231 AND close>0").fetchall()
    q.close()
    days = sorted({str(r[0]) for r in rows})
    px = defaultdict(dict)
    for dt, code, c in rows:
        px[str(code).zfill(6)][str(dt)] = float(c)
    day_idx = {d: i for i, d in enumerate(days)}
    rbs = [days[i] for i in range(0, len(days), REBALANCE_BARS) if i + HOLD_BARS < len(days)]

    def port_fwd(codes, i):
        if i + HOLD_BARS >= len(days) or not codes:
            return None
        sd, ed = days[i], days[i + HOLD_BARS]
        rets = []
        for c in codes:
            p = px.get(c)
            if not p:
                continue
            a, b = p.get(sd), p.get(ed)
            if a and b:
                rets.append(b / a - 1.0)
        if len(rets) < 5:
            return None
        r = sum(rets) / len(rets)
        mk = []
        allc = sorted(px.keys())
        step = max(1, len(allc) // 500)
        for c in allc[::step]:
            a, b = px[c].get(sd), px[c].get(ed)
            if a and b:
                mk.append(b / a - 1.0)
        return r - (sum(mk) / len(mk) if mk else 0), len(rets)

    # 1. 共识分层
    labels = {"dual": [], "bp_only": [], "ml_only": []}
    overlaps = []
    for rb in rbs:
        i = day_idx[rb]
        os.environ.pop("TULONG_LADDER_SIGNAL", None)
        try:
            bp15, _ = tpl.bp_ep_signal(loader, dash(rb))
        except Exception:
            continue
        if not bp15:
            continue
        bp15 = sorted(bp15, key=lambda c: -bp15[c])[:15]
        ml_rows = ml_con.execute(
            "SELECT code, ml_prob FROM stock_ml_scores_v22 WHERE date=? "
            "ORDER BY ml_prob DESC LIMIT 20",
            (int(rb.replace("-", "")),)).fetchall()
        ml20 = [str(c).zfill(6) for c, _p in ml_rows]
        if len(ml20) < 10:
            continue
        bp_set, ml_set = set(bp15), set(ml20)
        overlaps.append(len(bp_set & ml_set))
        for name, codes in [("dual", bp_set & ml_set), ("bp_only", bp_set - ml_set),
                            ("ml_only", ml_set - bp_set)]:
            e = port_fwd(sorted(codes), i)
            if e:
                labels[name].append({"date": rb, "excess": round(e[0], 4), "n": e[1]})

    # 2. ml IC 季度 + 十分位校准
    ic_by_q = defaultdict(list)
    deciles = defaultdict(list)
    sample = days[::3]
    for d in sample:
        i = day_idx.get(d)
        if i is None or i + HOLD_BARS >= len(days):
            continue
        ed = days[i + HOLD_BARS]
        sc = ml_con.execute("SELECT code, ml_prob FROM stock_ml_scores_v22 WHERE date=?",
                            (int(d.replace("-", "")),)).fetchall()
        if len(sc) < 100:
            continue
        pairs = []
        for c, prob in sc:
            c6 = str(c).zfill(6)
            p = px.get(c6)
            a, b = (p.get(d), p.get(ed)) if p else (None, None)
            if a and b:
                pairs.append((float(prob), b / a - 1.0))
        if len(pairs) < 100:
            continue
        mk = []
        allc = sorted(px.keys())
        step = max(1, len(allc) // 500)
        for c in allc[::step]:
            a, b = px[c].get(d), px[c].get(ed)
            if a and b:
                mk.append(b / a - 1.0)
        mk_ret = sum(mk) / len(mk)
        rets_ex = [r - mk_ret for _s, r in pairs]
        scores = [s for s, _r in pairs]
        ic = rank_ic(scores, rets_ex)
        if ic is not None:
            ic_by_q[d[:4] + "Q" + str((int(d[4:6]) - 1) // 3 + 1)].append(ic)
        srt = sorted(pairs, key=lambda x: -x[0])
        dn = len(srt) // 10
        if dn:
            for dec in range(10):
                chunk = srt[dec * dn:(dec + 1) * dn] if dec < 9 else srt[9 * dn:]
                deciles[dec].append(sum(r for _s, r in chunk) / len(chunk))
    ml_con.close()

    def agg_label(series):
        if not series:
            return None
        xs = [s["excess"] for s in series]
        wins = [x for x in xs if x > 0]
        return {"batches": len(series), "win": round(len(wins) / len(xs), 3),
                "avg_excess": round(sum(xs) / len(xs), 4)}
    ic_summary = {q: {"mean_ic": round(sum(v) / len(v), 4), "n": len(v),
                      "t_like": round((sum(v) / len(v)) / (statistics.pstdev(v) or 1) * len(v) ** 0.5, 2)}
                  for q, v in sorted(ic_by_q.items())}
    dec_summary = {f"D{dec+1}": round(sum(v) / len(v), 4) for dec, v in sorted(deciles.items())}
    out = {"window": WINDOW_START, "avg_overlap_bp15x_ml20": round(sum(overlaps) / len(overlaps), 2) if overlaps else None,
           "consensus_labels": {k: agg_label(v) for k, v in labels.items()},
           "ml_ic_by_quarter_fwd20": ic_summary,
           "ml_decile_fwd20_avg_excess": dec_summary,
           "context_note": "ml打分腿8/29已退役出生产(config 1.0/0.0); 本研究为诊断非权重提案; "
                           "10/8确认闸FAIL先例在案预期保守; 行业/市值暴露未含(边界)",
           "zero_trade_power": True}
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(out, open(os.path.join(OUT_DIR, "p5_consensus_mlcal_result.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"overlap": out["avg_overlap_bp15x_ml20"],
                      "labels": out["consensus_labels"],
                      "ic_by_q": ic_summary,
                      "deciles": dec_summary}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    import statistics
    main()
