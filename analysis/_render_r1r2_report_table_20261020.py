# -*- coding: utf-8 -*-
"""从 add_rule_baseline_r1r2_result_20261020.json 脚本直出结果总表, 追加至报告 md(计数不转抄纪律)"""
import json

SRC = "analysis/add_rule_baseline_r1r2_result_20261020.json"
DST = "reports/add_rule_baseline_r1r2_result_20261020.md"

d = json.load(open(SRC, encoding="utf-8"))
lines = ["", "---", "", "## 结果总表（脚本直出自 result JSON，非人工转抄）", ""]
r0 = d["configs"]["R0"]
_ev = [v for v in d["configs"].values() if isinstance(v, dict) and v.get("n_events")]
_r0win = next((v["r0_win"] for v in _ev if v.get("r0_win") is not None), None)
_r0tail = next((v["tail_r0"] for v in _ev if v.get("tail_r0") is not None), None)
_r0mean = r0.get("r0_mean") or next((v["r0_mean"] for v in _ev), None)
lines.append(
    f"R0 基线锚点: n={d['meta']['n_trades']}笔 | 均值={_r0mean} | 胜率={_r0win} | "
    f"DSR={r0.get('dsr'):.4f} | 右尾Top5%贡献={_r0tail}"
)
lines.append("")
lines.append("| 配置 | 事件数 | 配对增量均值 | 配对正占比 | 加仓批均值 | 加仓批触S-5率 | 全臂胜率差 | 右尾(arm/R0) | DSR | 额外换手 |")
lines.append("|---|---|---|---|---|---|---|---|---|---|")
for k, v in d["configs"].items():
    if k == "R0" or not v.get("n_events"):
        continue
    lines.append(
        f"| {k} | {v['n_events']} | {v['paired_mean']:+.4f} | {v['paired_pos']} | "
        f"{v['add_mean']:+.4f} | {v['add_stop_rate']} | {v['win_delta']:+.4f} | "
        f"{v['tail_arm']}/{v['tail_r0']} | {v.get('dsr'):.4f} | {v['extra_turnover']} |"
    )
lines.append("")
lines.append(f"- PBO(quant-integrity CSCV): {d.get('pbo_raw', {}).get('pbo')} "
             f"({d.get('pbo_note')}; prob_oos_loss={d.get('pbo_raw', {}).get('prob_oos_loss')})")
lines.append(f"- DSR API 样例(R0): {json.dumps(d.get('dsr_api_sample', {}), ensure_ascii=False)}")
lines.append(f"- 读回验证: {d.get('readback')}")
lines.append("")

with open(DST, "a", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"[render] → {DST} 追加{len(lines)}行")
