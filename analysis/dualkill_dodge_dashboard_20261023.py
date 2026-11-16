#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dualkill_dodge_dashboard_20261023.py — 「躲」条款三项触发指标历史仪表盘(只读)
==============================================================================
依据: dualkill_verification_protocol_20261019 落地段「躲仅作风险预算」:
  "预测分歧度高+闸门通过率低+gap波动高 同时达历史高分位 → 总仓100%→70%降档"。
本脚本把三个定性条件操作化为可计算序列(定义为本脚本提出, 待主人确认):
  I1 分歧度  = 1 - |bp_ep Top15 ∩ ml Top20|/15 (ml数据自2020-01-02起)
  I2 闸门通过率 = 候选池价格≤30存活率 len(close<=30)/len(全池) (仅价格闸代理,
                失败形态过滤未仪表化)
  I3 gap波动 = gated-Top15 入场日(T+1)缺口绝对值均值
窗口: 2020-01-02(ml覆盖起点)~最新, 月度调仓批; 2020-2024=历史基线, 2025-2026=当前regime。
零交易权纯呈现; 降档决策权在主人。
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
OUT_DIR = os.path.join(ROOT, "reports", "dualkill_dodge_dashboard_20261023")


def dash(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ladder_tpl",
                                                  os.path.join(HERE, "bt_full_report_ladder_20261019.py"))
    tpl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tpl)
    loader = tpl.TulongDataLoader(tpl.DB_PATH, tpl.QFQ_PATH)
    ml_con = sqlite3.connect(ML_DB, uri=True)
    q = sqlite3.connect(QFQ, uri=True)
    rows = q.execute("SELECT date, code, open, close FROM stock_daily_meta_qfq "
                     "WHERE date>=20200102 AND date<=20261231 AND close>0 AND open>0").fetchall()
    q.close()
    days = sorted({str(r[0]) for r in rows})
    px = defaultdict(dict)
    for dt, code, o, c in rows:
        px[str(code).zfill(6)][str(dt)] = (float(o), float(c))
    day_idx = {d: i for i, d in enumerate(days)}
    rbs = [days[i] for i in range(0, len(days), 22) if i + 20 < len(days)]
    print(f"[dodge] 窗口 {days[0]}~{days[-1]} 调仓批 {len(rbs)}")

    # 序列缓存: 74次信号调用耗时长, 落盘后重跑直接复用
    cache_path = os.path.join(OUT_DIR, "dodge_series_cache.json")
    series = None
    if os.path.exists(cache_path):
        series = json.load(open(cache_path, encoding="utf-8"))
        print(f"[dodge] 使用序列缓存 {len(series)} 批")
    if series is None:
        series = []
        for k, rb in enumerate(rbs):
            i = day_idx[rb]
            year = rb[:4]
            os.environ.pop("TULONG_LADDER_SIGNAL", None)
            try:
                bp15, _ = tpl.bp_ep_signal(loader, dash(rb))
            except Exception as e:
                print(f"[warn] {rb} 信号失败: {type(e).__name__}")
                continue
            if not bp15:
                continue
            bp15 = sorted(bp15, key=lambda c: -bp15[c])[:15]
            bp_set = {str(c).zfill(6) for c in bp15}

            overlap = None
            try:
                ml_rows = ml_con.execute(
                    "SELECT code FROM stock_ml_scores_v22 WHERE date=? "
                    "ORDER BY ml_prob DESC LIMIT 20", (int(rb.replace("-", "")),)).fetchall()
                ml_set = {str(r[0]).zfill(6) for r in ml_rows}
                if len(ml_set) >= 10:
                    overlap = len(bp_set & ml_set) / 15.0
            except sqlite3.Error:
                pass

            pass_rate = None
            try:
                df = loader.get_fundamental_factors(dash(rb))
                if df is not None and len(df) >= 20:
                    pass_rate = round(len(df[df["close"] <= 30]) / len(df), 4)
            except Exception:
                pass

            gaps = []
            if i + 1 < len(days):
                nxt = days[i + 1]
                for c in bp_set:
                    p = px.get(c)
                    if not p:
                        continue
                    cur = p.get(rb)
                    nxt_px = p.get(nxt)
                    if cur and nxt_px:
                        gaps.append(abs(nxt_px[0] / cur[1] - 1.0))
            gap_vol = round(sum(gaps) / len(gaps) * 100, 3) if gaps else None
            series.append({"date": rb, "year": year,
                           "I1_divergence": round(1 - overlap, 3) if overlap is not None else None,
                           "I2_pass_rate": pass_rate,
                           "I3_gap_vol_pct": gap_vol})
            if k % 20 == 0:
                print(f"  ..{rb} I1={series[-1]['I1_divergence']} I2={pass_rate} I3={gap_vol}")
        os.makedirs(OUT_DIR, exist_ok=True)
        json.dump(series, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)

    # 年度聚合 + 当前regime分位
    def agg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    by_year = defaultdict(lambda: {"I1": [], "I2": [], "I3": []})
    for s in series:
        y = by_year[s["year"]]
        y["I1"].append(s["I1_divergence"])
        y["I2"].append(s["I2_pass_rate"])
        y["I3"].append(s["I3_gap_vol_pct"])
    yearly = {y: {"I1_divergence": agg(v["I1"]), "I2_pass_rate": agg(v["I2"]),
                  "I3_gap_vol_pct": agg(v["I3"]), "batches": len(v["I1"])}
              for y, v in sorted(by_year.items())}
    fields = {"I1": "I1_divergence", "I2": "I2_pass_rate", "I3": "I3_gap_vol_pct"}
    hist = {"I1": [], "I2": [], "I3": []}
    cur = {"I1": [], "I2": [], "I3": []}
    for s in series:
        tgt = hist if s["year"] in ("2020", "2021", "2022", "2023", "2024") else (
            cur if s["year"] in ("2025", "2026") else None)
        if tgt is None:
            continue
        for key, fld in fields.items():
            if s[fld] is not None:
                tgt[key].append(s[fld])
    percentile = {}
    for key in ("I1", "I2", "I3"):
        h = sorted(hist[key])
        c = [v for v in cur[key] if v is not None]
        if h and c:
            avg_c = sum(c) / len(c)
            below = sum(1 for x in h if x <= avg_c)
            percentile[key] = {"hist_mean": round(sum(h) / len(h), 4),
                               "current_mean": round(avg_c, 4),
                               "current_vs_hist_pct": round(below / len(h) * 100, 1)}
    # 降档触发评估(操作化定义版): 三项同时≥历史90分位
    trigger = {}
    for key, direction in [("I1", "high"), ("I2", "low"), ("I3", "high")]:
        h = sorted(hist[key])
        c = [v for v in cur[key] if v is not None]
        if not h or not c:
            trigger[key] = "数据不足"
            continue
        avg_c = sum(c) / len(c)
        cut = h[int((0.9 if direction == "high" else 0.1) * (len(h) - 1))]
        hit = (avg_c >= cut) if direction == "high" else (avg_c <= cut)
        trigger[key] = {"hist_cut": round(cut, 4), "current_mean": round(avg_c, 4),
                        "triggered": bool(hit)}
    n_hit = sum(1 for v in trigger.values() if isinstance(v, dict) and v["triggered"])
    out = {"window": [days[0], days[-1]], "n_batches": len(series),
           "yearly": yearly, "current_vs_hist_baseline_percentile": percentile,
           "dodge_trigger_90pct_ops": {"definition_owner_confirm_required": True,
                                       "per_indicator": trigger,
                                       "n_of_3_triggered": n_hit,
                                       "rule": "三项同时触发→降档条件成立(操作化定义版, 待主人确认定义)"},
           "series": series}
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(out, open(os.path.join(OUT_DIR, "dodge_dashboard.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"yearly": yearly, "percentile": percentile,
                      "trigger": trigger, "n_of_3": n_hit}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
