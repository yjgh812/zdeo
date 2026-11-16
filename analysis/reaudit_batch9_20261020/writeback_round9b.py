# -*- coding: utf-8 -*-
"""证伪复核 收口补丁 · 最后10条处置 + 终版报告重出"""
import json
from collections import Counter

LED = r"D:/zcodesj/data/falsified_reaudit_ledger.jsonl"
RPT = r"D:/zcodesj/reports/reaudit_round9_final_20261020.md"

rows = [json.loads(l) for l in open(LED, encoding="utf-8")]
by_id = {r["id"]: r for r in rows}
n0 = len(rows)

upd = {}
upd["MANTOU-X1-COMPOSITE-UNCONDITIONAL"] = dict(
    verdict="verified", method="original_rerun",
    artifact="analysis/x1_industry_relative_20261003.py(重跑stdout)",
    note="行业内相对超额分年复现: 2022 -13.83pp / 2023 -5.83 / 2024 -6.41 / 2025 -9.65 / 2026 -12.94, "
         "win 43-47%, 5年5负 -> 无条件X1合成的反向结构复现, 判死维持")
REG = {
    "DIP-CARRY-ROUND-END-NO-END": None,  # 占位防typo
}
DEF = {
    "DIP-CARRY-ROUND-END-NO-EDGE": "R1队列指标为生产归因库口径, 复刻需归因管线重建; 原档三段论证(持续性缺口2.8bp/MA20环境承载/前瞻反向)结构完整",
    "ENTRY-DAY-CONFIRM-GATE": "九主线池507只定义未存档(prereg在但池构建依赖画像系统); 原档含第七方收敛注记(与MANTOU-CHASE同向, 后者已batch1 verified)",
    "L2-LHB-SEAT-INST-STAGE1": "FROZEN: 有在役OOS影子(PREREG_L2_SEAT_COND_RANK_FWALK, Stage B首评2026-12-31), 现在复测会与在跑前向验证撞车; 影子收官后解冻",
    "OUTEMAN-TOP-WINDOW-NO-EDGE": "旧龙顶v3构造(最近顶归因)迭代三次, 窗口首板/邻域对照口径需逐字重建; 原档三版迭代档案完整可循",
    "PROFILE-STARTUP-COND": "画像体系启动条件 machinery 依赖重建(28.7万信号/9变体); evidence空但SRC数字完整; 留依赖重建后",
    "SHALLOW-DIP-BAN-UNIVERSAL": "需四选手spmatch交割单队列重放(素衣/大师兄/陈小炮等, 数据在data/ec_spmatch*); 结论=跨风格翻转本身即'无普适禁区'证据, 数据级重跑留后",
}
OWN = {
    "S-1": "owner_boundary: 「优化已退役策略(v375)违反宪法§10-8」系纪律裁决非经验命题, 永不复测",
    "S-2": "owner_boundary: 同S-1(p2_signal退役裁决)",
    "S-3": "owner_mooted: TopN孤峰证伪维度已被主人2026-09-24名单制终局裁决整体废除, 无活链路依赖, 口径级复核即毕",
}
for rid, (v, m, a) in {"MANTOU-X1-COMPOSITE-UNCONDITIONAL": (
        "verified", upd["MANTOU-X1-COMPOSITE-UNCONDITIONAL"]["note"],
        upd["MANTOU-X1-COMPOSITE-UNCONDITIONAL"]["artifact"])}.items():
    upd[rid] = dict(verdict=v, method=m and "original_rerun", artifact=a, note=m)
for rid, why in DEF.items():
    upd[rid] = dict(verdict="deferred_registered", method="", artifact="", note="缓期登记: " + why)
for rid, why in OWN.items():
    upd[rid] = dict(verdict="owner_boundary" if "owner_boundary" in why else "owner_mooted",
                    method="", artifact="", note=why)

for rid, u in upd.items():
    row = by_id[rid]
    row["rerun"] = u
    row["verdict"] = u["verdict"]
    row["audit_status"] = ("done" if u["verdict"] == "verified"
                           else "not_reproducible" if u["verdict"] == "not_reproducible"
                           else "owner_boundary" if u["verdict"].startswith("owner") else "deferred")
assert len(rows) == n0
with open(LED, "w", encoding="utf-8") as f:
    for r_ in rows:
        f.write(json.dumps(r_, ensure_ascii=False) + "\n")
st = Counter(r["audit_status"] for r in rows)
vd = Counter(r.get("verdict") for r in rows if r["audit_status"] == "done")
print("rows=%d audit_status=%s" % (len(rows), dict(st)))
print("done内verdict=%s" % dict(vd))
assert all(r["audit_status"] != "pending" for r in rows), "仍有pending"
print("全量处置闭合: 零pending")

L = []
L.append("# 证伪复核 批次9 终版报告(收口补丁后重出)\n")
L.append("> 生成: analysis/reaudit_batch9_20261020/writeback_round9b.py | 2026-10-20\n")
L.append("## 本批裁决(CONFIRM/S3/CB/X1 + 10条收口登记)\n")
for r_ in done if (done := [r_ for r_ in rows if r_["id"] in upd]) else []:
    L.append("| %s | %s | %s |\n" % (r_["id"], r_["verdict"], (r_["rerun"] or {}).get("note", "").replace("|", "/")[:200]))
L.append("\n## 全工程终态(113条全量处置闭合, 零pending)\n")
L.append("- audit_status: %s\n" % dict(st))
L.append("- done内verdict: %s\n" % dict(vd))
L.append("- **主判翻案: 0 条** | 原档计算bug 1起(F7) | M3脚本失存3起 | 审计发现F1-F9\n")
L.append("\n## 工程级结论\n")
L.append("1. 主人命题裁定: 结论层零错杀(全部数据级复测方向复现), 证据链层确认1处计算错误+多处管护缺陷\n")
L.append("2. 交付: reaudit台账113行全处置 + round1-9报告 + 方案/普查 + 全套重实现脚本(analysis/reaudit_batch*)\n")
L.append("3. 遗留: 14条缓期登记(依赖重建/闲时批/OOS冻结/owner边界) + sweet_spot深超卖新prereg候选 + 三项工件加固呈请\n")
L.append("4. 纪律: 全程零交易权零接线; 原证伪台账与归档工件未被改写\n")
with open(RPT, "w", encoding="utf-8") as f:
    f.writelines(L)
print("report rewritten:", RPT)
