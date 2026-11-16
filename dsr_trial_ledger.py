# -*- coding: utf-8 -*-
"""dsr_trial_ledger.py —— DSR 尝试台账工具（prereg 验收的数学折价器, 只读）

来源: reports/math_knowledge_full_study_20260920.md 域4（HLZ t≥3.0 / DSR / 多重检验）
数学: owner = tulong_math_toolkit（emax_tstat/dsr_threshold/psr/fwer/bonferroni_alpha）。
N 语义: 本项目"已独立尝试的策略/信号次数"——真源 = data/falsified_rules.json
        (rules 66 + retired_strategies 6, 2026-09-20 口径) + 可选 --extra 手动补记
        (在途 prereg 影子等)。N 低估=DSR 造假, 台账完整性是 prereg 合同字段。
用法:
  python dsr_trial_ledger.py --show                 # 看 N 构成
  python dsr_trial_ledger.py --tstat 2.5 --days 250 # 评估一个 t=2.5/n=250日 的新信号
  可选: --skew --kurt --sr-std(年化, 默认0.5启发式) --n 覆盖N --extra K
退出码: 0 正常(判定结果在 stdout JSON), 2 参数/数据失败
红线: 本工具只提供数学折价意见, 不替代 prereg/主人裁决; 判据写进 prereg 后冻结。
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
from tulong_math_toolkit import (  # noqa: E402
    bonferroni_alpha, dsr_threshold, emax_tstat, fwer, psr)

FALSIFIED = os.path.join(ROOT, "data", "falsified_rules.json")
ANNUAL = math.sqrt(250)  # A股年化惯例(报告域3: 对比必须同k)
DEFAULT_SR_STD = 0.5     # 年化启发式默认: 无试验级SR台账时的保守值, 显式标注


def trial_count(keyword: str | None) -> dict:
    d = json.load(open(FALSIFIED, encoding="utf-8"))
    rules = d.get("rules", {})
    retired = d.get("retired_strategies") or []
    if keyword:
        hit = {k: v for k, v in rules.items()
               if keyword.upper() in k.upper()
               or keyword in json.dumps(v, ensure_ascii=False)}
        return {"source": "falsified_rules.json", "keyword": keyword,
                "rules": len(hit), "retired": 0, "N": len(hit)}
    return {"source": "falsified_rules.json", "rules": len(rules),
            "retired": len(retired), "N": len(rules) + len(retired)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="仅显示 N 构成")
    ap.add_argument("--tstat", type=float, help="新信号的 t 统计量")
    ap.add_argument("--days", type=int, default=250, help="样本交易日数(默认250)")
    ap.add_argument("--skew", type=float, default=0.0)
    ap.add_argument("--kurt", type=float, default=3.0)
    ap.add_argument("--sr-std", type=float, default=DEFAULT_SR_STD,
                    help=f"试验间年化SR标准差(默认{DEFAULT_SR_STD}启发式)")
    ap.add_argument("--n", type=int, help="手动覆盖 N(须在 prereg 中说明理由)")
    ap.add_argument("--extra", type=int, default=0, help="在途prereg等补记次数")
    ap.add_argument("--keyword", default=None, help="证伪档案关键词过滤(族内N)")
    a = ap.parse_args()

    try:
        tc = trial_count(a.keyword)
    except Exception as e:
        print(f"[DSR-FAIL] 证伪档案读取失败: {e}")
        return 2
    N = a.n if a.n else tc["N"] + a.extra
    if N < 2:
        print("[DSR-FAIL] N<2 无法计算")
        return 2

    out = {"N": N, "N_source": tc if not a.n else "manual override", "sr_std_annual": a.sr_std,
           "sr_std_is_heuristic": a.sr_std == DEFAULT_SR_STD,
           "emax_tstat_pure_noise": round(emax_tstat(N), 3),
           "fwer_at5pct": round(fwer(0.05, N), 4),
           "bonferroni_alpha": round(bonferroni_alpha(0.05, N), 6),
           "emax_sharpe_annual": round(dsr_threshold(N, a.sr_std), 4),
           "hlz_rule": "t>=3.0 (Harvey-Liu-Zhu 2016)"}

    if a.tstat is not None:
        sr_per = a.tstat / math.sqrt(a.days)          # 日频SR
        sr_ann = sr_per * ANNUAL
        star_per = out["emax_sharpe_annual"] / ANNUAL
        p = psr(sr_per, a.days, a.skew, a.kurt, star_per)
        out["eval"] = {"tstat": a.tstat, "days": a.days, "sr_annual": round(sr_ann, 4),
                       "dsr_threshold_annual": out["emax_sharpe_annual"],
                       "dsr": round(p, 4), "verdict": "PASS(>0.95)" if p > 0.95 else "FAIL(<=0.95)"}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
