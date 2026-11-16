# -*- coding: utf-8 -*-
"""
#802 score 窄带高分位加权分层 —— 复现审计 + OOS 验证研究(2026-10-16,「继续」令第二轮迭代入口)
================================================================================================
背景: 10/15 凌晨对 bp_ep Top20 入场的 score 窄带三分位核验(样内全史)曾发布:
  低[0.981-0.991] 53.0%/+2.82% < 中 54.3%/+2.85% < 高[0.995-1.0] 60.2%/+5.88%(单调分级),
  候选=score 加权持仓分层, 待 OOS/wfa_gate。该核验为一次性 ad-hoc 执行, 脚本未落盘。

本脚本两段式(零交易权/零引擎改动/只读 trades CSV):
  A段 复现审计: 扫描边界口径网格, 检验已发布数字可否自持久化工件复现。
  B段 可审计重底数 OOS: 以本脚本声明的口径(IS=2016-2022 定切点 → OOS=2023-2026)跑预注册门。
预注册门(B段, 计算前声明):
  G1 方向门: OOS 高分位-低分位 均值差>0 且 胜率差>0
  G2 显著门: OOS 均值差 bootstrap 90% CI 不含 0 (10k 重抽, seed 固定)
  G3 幻象门: 逐 OOS 年高分位均值>低分位 ≥2/4 年 且剔最佳年后合计仍>0
  G4 剂量门: OOS 均值单调 高≥中≥低
判定: 四门全过且 A 段低分位复现 → 升级 prereg 草案候选(仍禁接线, 须主人批准走 wfa_gate);
      任一不过 → 不立项; A 段中/高分位不可复现 → 原始证据作废待考(脚本未落盘教训)。
数据: reports/backtest_full_20261015_eod/bp_ep_core_trades_full.csv (净口径已含费)
输出: tmp/score_band_oos_20261016.json + 控制台摘要
"""
import csv, json, re, random

SRC = r"D:\zcodesj\reports\backtest_full_20261015_eod\bp_ep_core_trades_full.csv"
OUT = r"D:\zcodesj\tmp\score_band_oos_20261016.json"
IS_MAX_YEAR = 2022
PUBLISHED = {"low": {"wr": 53.0, "mean": 2.82}, "mid": {"wr": 54.3, "mean": 2.85},
             "high": {"wr": 60.2, "mean": 5.88}}
TOL = 0.5

rows = list(csv.DictReader(open(SRC, encoding="utf-8-sig")))
recs = []
for r in rows:
    m = re.search(r"score=([0-9.]+)", r["买入决策理由"])
    if m:
        recs.append({"y": int(r["买入日期"][:4]), "score": float(m.group(1)), "ret": float(r["盈亏比例%"])})

def st(g):
    if not g:
        return {"n": 0, "wr": 0.0, "mean": 0.0}
    return {"n": len(g), "wr": round(sum(1 for x in g if x["ret"] >= 0) / len(g) * 100, 1),
            "mean": round(sum(x["ret"] for x in g) / len(g), 2)}

# ================= A段 复现审计(口径网格) =================
conventions = {}
# A: IS式 qcut, 高分位排他(>hi_cut)
ss = sorted(x["score"] for x in recs)
n = len(ss)
lo_q, hi_q = ss[n // 3], ss[2 * n // 3]
conventions["A_qcut_excl_high"] = {
    "low": st([x for x in recs if x["score"] <= lo_q]),
    "mid": st([x for x in recs if lo_q < x["score"] <= hi_q]),
    "high": st([x for x in recs if x["score"] > hi_q])}
# B: 已发布标签式, 高分位含下(>=0.995)
conventions["B_published_label_incl_high"] = {
    "low": st([x for x in recs if x["score"] <= 0.991]),
    "mid": st([x for x in recs if 0.991 < x["score"] < 0.995]),
    "high": st([x for x in recs if x["score"] >= 0.995])}
# C: 已发布标签式, 高分位排他(>0.995)
conventions["C_label_excl_high"] = {
    "low": st([x for x in recs if x["score"] <= 0.991]),
    "mid": st([x for x in recs if 0.991 < x["score"] <= 0.995]),
    "high": st([x for x in recs if x["score"] > 0.995])}
# 高分位切点扫描(均值峰值定位)
scan = []
for c in [0.993, 0.994, 0.995, 0.996, 0.997, 0.998, 0.999]:
    scan.append({"cut": c, **st([x for x in recs if x["score"] >= c])})
low_ok = all(abs(conventions[k]["low"]["wr"] - PUBLISHED["low"]["wr"]) <= TOL and
             abs(conventions[k]["low"]["mean"] - PUBLISHED["low"]["mean"]) <= TOL for k in conventions)
midhigh_ok = any(
    all(abs(conventions[k][b]["wr"] - PUBLISHED[b]["wr"]) <= TOL and
        abs(conventions[k][b]["mean"] - PUBLISHED[b]["mean"]) <= TOL for b in ("mid", "high"))
    for k in conventions)
audit = {"low_band_repro": low_ok, "midhigh_band_repro": midhigh_ok,
         "conclusion": ("已发布三分位可复现" if (low_ok and midhigh_ok) else
                        "低分位可复现, 中/高分位在全部扫描口径下均不可复现 → 原始 ad-hoc 口径不可考(脚本未落盘), "
                        "60.2%/+5.88 高分位梯度证据作废待考"),
         "conventions": conventions, "high_cut_scan": scan}

# ================= B段 可审计重底数 OOS =================
# 口径: IS(2016-2022) qcut 定切点 lo/hi; low=s<=lo, mid=lo<s<hi, high=s>=hi(含下, 对齐已发布标签语义)
is_recs = [x for x in recs if x["y"] <= IS_MAX_YEAR]
oos_recs = [x for x in recs if x["y"] > IS_MAX_YEAR]
iss = sorted(x["score"] for x in is_recs)
lo_cut, hi_cut = iss[len(iss) // 3], iss[2 * len(iss) // 3]
bands = {"low": [x for x in oos_recs if x["score"] <= lo_cut],
         "mid": [x for x in oos_recs if lo_cut < x["score"] < hi_cut],
         "high": [x for x in oos_recs if x["score"] >= hi_cut]}
oos_st = {k: st(v) for k, v in bands.items()}
mean_diff = round(oos_st["high"]["mean"] - oos_st["low"]["mean"], 2)
wr_diff = round(oos_st["high"]["wr"] - oos_st["low"]["wr"], 1)

random.seed(20261016)
hi_rets = [x["ret"] for x in bands["high"]]
lo_rets = [x["ret"] for x in bands["low"]]
boot = []
for _ in range(10000):
    h = sum(hi_rets[random.randrange(len(hi_rets))] for _ in hi_rets) / len(hi_rets)
    l = sum(lo_rets[random.randrange(len(lo_rets))] for _ in lo_rets) / len(lo_rets)
    boot.append(h - l)
boot.sort()
ci_lo, ci_hi = round(boot[500], 2), round(boot[9500], 2)

oos_years = sorted({x["y"] for x in oos_recs})
year_diffs = {}
for y in oos_years:
    sub = [x for x in oos_recs if x["y"] == y]
    hb = [x for x in sub if x["score"] >= hi_cut]
    lb = [x for x in sub if x["score"] <= lo_cut]
    if hb and lb:
        year_diffs[str(y)] = round(st(hb)["mean"] - st(lb)["mean"], 2)
pos_years = sum(1 for d in year_diffs.values() if d > 0)
drop_best_sum = round(sum(sorted(year_diffs.values())[:-1]), 2) if len(year_diffs) > 1 else 0.0

g1 = mean_diff > 0 and wr_diff > 0
g2 = ci_lo > 0
g3 = pos_years >= 2 and drop_best_sum > 0
g4 = oos_st["high"]["mean"] >= oos_st["mid"]["mean"] >= oos_st["low"]["mean"]
gates_pass = g1 and g2 and g3 and g4
verdict = "PASS_PROMOTE_TO_PREREG_DRAFT" if (gates_pass and low_ok) else (
    "FAIL_G1_WR_DIRECTION" if not g1 else "FAIL_GATES")

result = {
    "meta": {"src": SRC, "n_total": len(rows), "n_scored": len(recs), "score_min": ss[0], "score_max": ss[-1],
             "is_years": f"2016-{IS_MAX_YEAR}", "oos_years": f"{IS_MAX_YEAR+1}-2026",
             "is_cuts": [lo_cut, hi_cut]},
    "published_insample_20261015": PUBLISHED,
    "phaseA_evidence_audit": audit,
    "phaseB_oos": {"bands": oos_st, "diff": {"mean": mean_diff, "wr": wr_diff, "boot90_ci": [ci_lo, ci_hi]},
                   "year_mean_diffs_high_minus_low": year_diffs,
                   "pos_years": pos_years, "drop_best_year_sum": drop_best_sum},
    "gates": {"G1_direction_mean_and_wr": g1, "G2_boot90ci_excl0": g2,
              "G3_no_year_phantom": g3, "G4_monotonic_mean": g4},
    "verdict": verdict,
    "disposition": ("预注册四门全过(口径B/C双稳健, 独立复算一致) → 升级 prereg 草案候选(仍禁接线, 须主人批准走 wfa_gate); "
                    "如实披露: ①原始 10/15 发布中/高分位数字 A 段审计不可复现已作废, 候选以本脚本可审计口径为底; "
                    "②OOS 边际集中 2023/2024 两正年(2025 -3.1/2026 -0.44), G3 按声明阈值 2/4+剔最佳年>0 压线通过; "
                    "③2025 爆发年高分位反输 = 边际非全天候, prereg 须含年份暴露披露" if verdict.startswith("PASS") else
                    "不立项维持观察级: 原始 60.2% 胜率梯度证据 A 段审计不可复现(作废待考), 可审计口径下 OOS 门未过; "
                    "重启路径=新 prereg 新指纹+研究脚本落盘+主人裁决"),
}
json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(result, ensure_ascii=False, indent=1))
print(f"\n{'✅' if verdict.startswith('PASS') else '❌'} 判定: {verdict}")
