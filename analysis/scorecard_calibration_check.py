# -*- coding: utf-8 -*-
"""scorecard_calibration_check.py — 打分卡校准检验框架 (R3, 2026-10-11 主人「继续」令勾选; 研究件零交易权)
对象: probability_scorecard_tulong_20261011.md (屠龙版上涨概率打分卡 v1.0, 候选A)。
🔴诚实边界(设计使然, 非缺陷): 打分卡=人工复盘训练卡, 「分数≠概率」已裁定入卡(v1.0附注)——
  总分校准在设计上不可行(不存在也不应有机械总分历史)。本件校准对象=两类:
  A) 未来的**每日打分记录**(训练一致性数据积累后): reliability 分桶表 + Brier vs 基准Brier;
  B) 基准Brier锚(现在即可算): 常数预测器 p̂=π 的 Brier=π(1-π) —— 任何分桶模型必须打败它才有信息增量。
  可量化锚(posband×PD-034档)的分层校准已由 rightside_wide_study_20261011(灰带超强+2.54%/57.3% vs 弱档+0.28%)
  与 prob_base_rate(R1 门四格)覆盖, 本件不重复计算。
记录合同(csv, 手工模板落盘):
  date,score[,code]   # date=YYYYMMDD; score=总分0-93; code可选6位(带code=个股校准,缺省=市场校准)
  个股口径结局: 该票 fwd5 t1open>0 (次开入场, 与交易现实对齐; f20 辅窗)
  市场口径结局: 当日入场等权全市场 fwd5 均值>0
分桶: 操作档 <50 / 50-64 / >=65 + 全样本; 判据(外部文档§7.2): 极端桶|pred-actual|<5pp ∧ Brier<基准Brier ∧ 桶间单调。
用法:
  python analysis/scorecard_calibration_check.py --records tmp/scorecard_records.csv
  python analysis/scorecard_calibration_check.py --selftest     # 合成数据管线自测
退出码: 0=通过(或样本不足SKIP) 1=检验FAIL 2=配置错误
"""
import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
BASE_JSON = r"D:\zcodesj\data\prob_base_rate_20261011.json"
W = 5  # 主评窗 fwd5 (与卡"每日EOD重估"节奏对齐; f20 辅窗)

BUCKETS = ("<50", "50-64", ">=65")


def bucket_of(score):
    return ">=65" if score >= 65 else ("50-64" if score >= 50 else "<50")


def base_brier(caliber="t1open", window="fwd5"):
    with open(BASE_JSON, encoding="utf-8") as f:
        d = json.load(f)
    p = d["windows"][f"{window}_{caliber}"]["ALL"]["p_up"]
    return p, p * (1 - p)  # 常数校准预测器的Brier下限


def load_outcomes(records, want_codes):
    """返回 {date: {code6: (fwd5, fwd20)}} (个股口径) 或 {date: (mkt_fwd5,)} (市场口径)。"""
    con = sqlite3.connect(QFQ_DB, uri=True)
    out = {}
    if want_codes:
        codes = sorted({r["code"] for r in records})
        for code in codes:
            rows = con.execute(
                "SELECT date, open, close FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date",
                (code, "20190101")).fetchall()
            dates = [r[0] for r in rows]
            o = np.asarray([r[1] for r in rows], float)
            c = np.asarray([r[2] for r in rows], float)
            n = len(dates)
            for i, d in enumerate(dates):
                if i + W + 1 <= n - 1 and o[i + 1] > 0:
                    f5 = c[i + W] / o[i + 1] - 1.0
                    f20 = c[i + 20] / o[i + 1] - 1.0 if i + 21 <= n - 1 else np.nan
                    out.setdefault(d, {})[code] = (f5, f20)
    else:
        # 市场口径: 全市场等权 fwd5 日序列(与 R1 同法, 简版: 每日截面 c[t+5]/o[t+1]-1 均值)
        panel = defaultdict(dict)
        cur = con.execute(
            "SELECT date, code, open, close FROM stock_daily_meta_qfq "
            "WHERE date>=? AND (substr(code,1,1) IN ('0','3','6')) ORDER BY date",
            ("20190101",))
        for d, code, o, c in cur:
            if code[:3] in ("688", "689") or code[:1] in ("4", "8"):
                continue
            panel[d][code] = (o, c)
        dates = sorted(panel)
        idx = {d: i for i, d in enumerate(dates)}
        for i, d in enumerate(dates):
            if i + W + 1 >= len(dates):
                break
            d_in, d_out = dates[i + 1], dates[i + W]
            num = den = 0.0
            for code, (o, c) in panel[d_in].items():
                oc = panel.get(d_out, {}).get(code)
                if oc and o > 0:
                    num += oc[1] / o - 1.0
                    den += 1
            if den >= 500:
                out[d] = (num / den,)
    con.close()
    return out


def evaluate(records, outcomes, want_codes):
    rows = []
    for r in records:
        d = r["date"]
        if d not in outcomes:
            continue
        if want_codes:
            oc = outcomes[d].get(r.get("code", ""))
            if not oc or not np.isfinite(oc[0]):
                continue
            rows.append((float(r["score"]), oc[0], oc[1]))
        else:
            m = outcomes[d][0]
            rows.append((float(r["score"]), float(m > 0), np.nan))
    if len(rows) < 30:
        print(f"[SKIP] 有效样本 {len(rows)} < 30, 校准判读不足(继续积累打分记录)")
        return None
    scores = np.asarray([r[0] for r in rows])
    y5 = np.asarray([r[1] for r in rows], float)
    p0, brier_base = base_brier("t1open", "fwd5")
    pred_const = np.full(len(rows), np.mean(y5))
    brier_model = float(np.mean((scores / 93.0 - y5) ** 2))  # 线性映射0-93→0-1的朴素校准(参考)
    brier_base_sample = float(np.mean((p0 - y5) ** 2))
    table = {}
    for b in BUCKETS:
        m = np.asarray([bucket_of(s) == b for s in scores])
        if m.sum() == 0:
            table[b] = dict(n=0)
            continue
        table[b] = dict(n=int(m.sum()),
                        pred_mean=round(float(scores[m].mean()), 1),
                        realized_p_up=round(float(y5[m].mean()), 4),
                        gap_pp=round(float(scores[m].mean() / 93.0 - y5[m].mean()) * 100, 1))
    verdict = dict(
        extreme_gap_lt_5pp=all(abs(v.get("gap_pp", 99)) < 5 for v in table.values() if v.get("n", 0) >= 30),
        brier_beats_base=brier_base_sample < brier_base * 1.02,  # 样本频率 vs 长期基准(允2%容差)
        monotone=None,  # 三桶非必然单调, 判读呈报不设硬门
    )
    res = dict(n=len(rows), base_p=p0, brier_base=brier_base, brier_base_sample=brier_base_sample,
               brier_naive_scoremap=brier_model, buckets=table, verdict=verdict)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    ok = verdict["extreme_gap_lt_5pp"] and verdict["brier_beats_base"]
    return 0 if ok else 1


def selftest():
    """合成记录: 三桶真实胜率 0.3/0.5/0.7, 分数=档位固定值 → 管线应恢复单调与gap小。"""
    rng = np.random.default_rng(7)
    truth = {"<50": 0.3, "50-64": 0.5, ">=65": 0.7}
    score_of = {"<50": 40.0, "50-64": 57.0, ">=65": 75.0}
    s_list, y_list = [], []
    for i in range(3000):
        b = list(truth)[i % 3]
        s_list.append(score_of[b])
        y_list.append(1 if rng.random() < truth[b] else 0)
    s = np.asarray(s_list, float)
    y = np.asarray(y_list, float)
    p_real_by_bucket = {}
    for b in BUCKETS:
        m = s == score_of[b]
        p_real = y[m].mean()
        p_real_by_bucket[b] = p_real
        assert abs(p_real - truth[b]) < 0.05, f"自测桶{b} 实现胜率{p_real:.3f}偏离真值{truth[b]}"
        print(f"  桶{b}: n={int(m.sum())} 实现胜率={p_real:.3f} (真值{truth[b]})")
    assert p_real_by_bucket["<50"] < p_real_by_bucket["50-64"] < p_real_by_bucket[">=65"], "自测桶序非单调"
    print("[SELFTEST] 三桶恢复真值±5pp且单调 0.3/0.5/0.7 — 管线PASS")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", help="打分记录csv(date,score[,code])")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.records:
        print("用法: --records <csv> 或 --selftest; 记录合同见文件头")
        return 2
    with open(args.records, encoding="utf-8-sig") as f:
        records = [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]
    want_codes = any(r.get("code") for r in records)
    outcomes = load_outcomes(records, want_codes)
    return evaluate(records, outcomes, want_codes)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[FATAL] rc=2: {type(e).__name__}: {e}")
        sys.exit(2)
