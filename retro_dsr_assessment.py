# -*- coding: utf-8 -*-
"""retro_dsr_assessment.py —— 已上线/在途核心策略的多重尝试校正追溯评估（只读一次性研究）

来源: math_knowledge_full_study_20260920.md 域四（主人令"继续"第四轮落地）
命题: 本项目已试 N=72(falsified 66+retired 6)背景下, 已有 t 锚点的核心策略在校正视线下是否仍站得住。
锚点表（t/n 均引自 SOP 台账与证伪档案已记录值, 逐条注明出处, 禁止新算改值）:
  - 组合C 冻结臂 v2   t=3.75  n≈2640(2016-2026 日线, 近似)  出处 SOP(三十) 9/9 "组合C t3.75 复现PASS"
  - 引擎A 低位事件三闸 t=3.19  n=81409(事件数)               出处 9/9 记忆 "NW t3.19"
  - PEAD 侦察(已FAIL)  t=3.68  n=7733(事件数)                 出处 9/17 记忆(严格口径蒸发93%, 不复活仅示例)
  - L-2 席位 A2 涨榜   t=+4.73 n=日度聚类                     出处 9/9 记忆 "涨榜t+4.73"
方法: ①t 三档对照: >3.0(HLZ 硬线)/2.463~3.0(灰区, 高于噪音冠军 E[max](N=72) 但低于 HLZ)/<2.463(噪音域);
      ②仅时序型(组合C)做 DSR, sr_std 无试验级台账 → 三档敏感性 {0.2,0.3,0.5}(诚实区间, 单点判定禁止);
      ③事件研究型(引擎A/PEAD/L2)的 n 为事件数非时序独立样本, PSR/DSR 公式不适用, 只做 t 对照(注明)。
红线: 本件不改任何策略地位; 灰区≠判死; 输出供 10/1 解冻评估与月度自省引用。
"""
from __future__ import annotations
import json
import math
import os
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
from tulong_math_toolkit import dsr_threshold, emax_tstat, psr  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

N_TRIALS = 72          # falsified 66 + retired 6 (2026-09-20 口径, dsr_trial_ledger 同源)
ANNUAL = math.sqrt(250)
E_MAX = emax_tstat(N_TRIALS)
HLZ = 3.0

ANCHORS = [
    {"name": "组合C 冻结臂 v2", "t": 3.75, "n": 2640, "type": "时序",
     "src": "SOP(三十) 9/9 组合C t3.75 复现PASS; n=2016-2026日线≈2640近似"},
    {"name": "引擎A 低位事件三闸", "t": 3.19, "n": 81409, "type": "事件",
     "src": "9/9 记忆 n=81409 超额+2.91% NW t3.19"},
    {"name": "PEAD 侦察(严格口径已FAIL)", "t": 3.68, "n": 7733, "type": "事件",
     "src": "9/17 记忆 t3.68; 严格口径蒸发93%已判 FAIL 不复活, 仅作校正示例"},
    {"name": "L-2 席位 A2 涨榜", "t": 4.73, "n": None, "type": "事件",
     "src": "9/9 记忆 涨榜t+4.73(Stage A 机制诊断)"},
]


def t_band(t: float) -> str:
    if t > HLZ:
        return "硬(>HLZ 3.0)"
    if t > E_MAX:
        return f"灰区({E_MAX:.3f}~3.0: 高于噪音冠军/低于HLZ)"
    return f"噪音域(<{E_MAX:.3f})"


def main() -> int:
    rows = []
    for a in ANCHORS:
        band = t_band(a["t"])
        row = {"name": a["name"], "t": a["t"], "n": a["n"], "type": a["type"], "band": band, "src": a["src"]}
        if a["type"] == "时序" and a["n"]:
            sr_per = a["t"] / math.sqrt(a["n"])
            row["sr_annual"] = round(sr_per * ANNUAL, 3)
            sens = {}
            for s in (0.2, 0.3, 0.5):
                thr = dsr_threshold(N_TRIALS, s)
                p = psr(sr_per, a["n"], 0.0, 3.0, thr / ANNUAL)
                sens[f"sr_std={s}"] = {"emax_sharpe": round(thr, 3), "dsr": round(p, 4),
                                       "verdict": "PASS>0.95" if p > 0.95 else "FAIL<=0.95"}
            row["dsr_sensitivity"] = sens
        rows.append(row)

    out = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "N_trials": N_TRIALS,
           "emax_t_pure_noise": round(E_MAX, 3), "hlz_threshold": HLZ, "rows": rows,
           "caveats": ["事件研究型 n=事件数非时序独立样本, PSR/DSR 不适用只做 t 对照",
                       "sr_std 无试验级 SR 台账 → DSR 只给三档区间, 单点判定禁止",
                       "组合C n=2640 为日线近似; N=72 为当前累积口径(策略上线时点 N 更小, 本口径偏保守)",
                       "灰区≠判死(主人 9/14 裁决: 负结果禁判死式否定)"]}
    json.dump(out, open(os.path.join(ROOT, "data", "retro_dsr_assessment_20260920.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)

    L = ["# 核心策略 DSR 追溯评估（N=72 多重尝试校正·只读一次性研究）",
         "",
         f"> E[max t](纯噪音, N=72) = {E_MAX:.3f} ｜ HLZ 硬线 = {HLZ} ｜ 锚点值全部引自台账已记录数, 本件零新算 t",
         "",
         "## t 值三档对照",
         "| 策略 | t | n | 型 | 校正档位 |", "|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['name']} | {r['t']} | {r['n'] or '-'} | {r['type']} | {r['band']} |")
    L += ["",
          "## 组合C DSR 三档敏感性（时序型唯一适用者）"]
    for r in rows:
        if "dsr_sensitivity" in r:
            L.append(f"- 年化 SR ≈ {r['sr_annual']}")
            for k, v in r["dsr_sensitivity"].items():
                L.append(f"  - {k}: 期望最大SR={v['emax_sharpe']} → DSR={v['dsr']} {v['verdict']}")
    L += ["", "## 口径局限", *[f"- {c}" for c in out["caveats"]], "",
          "## 观察结论",
          f"- 四个锚点 t 全部高于 HLZ 3.0=校正后仍硬(也全部远高于噪音冠军线 {E_MAX:.2f});",
          "- 事件型三锚(引擎A/PEAD侦察/L-2)的 n 为事件数, PSR/DSR 公式不适用, t 对照是可用上限;",
          "- 组合C DSR 结论由 sr_std 主导(0.2 过/0.5 不过)=试验级 SR 台账缺失是当前校正链的最大不确定源",
          "  → 未来 prereg 应登记各次尝试的 SR 使 N 与 sr_std 都可复算(报告域四工具1的合同字段要求)。"]
    open(os.path.join(ROOT, "reports", "retro_dsr_assessment_20260920.md"), "w", encoding="utf-8").write("\n".join(L))
    print(f"[RETRO-OK] N={N_TRIALS} E[max t]={E_MAX:.3f}")
    for r in rows:
        print(f"  {r['name']}: t={r['t']} → {r['band']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
