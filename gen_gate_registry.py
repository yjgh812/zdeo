#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_gate_registry.py — R 闸门注册表生成器（S3, 2026-09-28 全维度自省落地）。

痛点（自省 B3）：R1-R33 编号无单一真源——定义散落在 pre_trade_check_v2.py
（实现即定义）、DAILY_WORKFLOW_SOP.md（逐闸裁决记录）、l0_system_health.py（冒烟），
且存在编号复用（旧 R19 锤子线→现 R19 解禁回避）、退役闸参数残留等漂移。

本脚本 = 注册表的**生成物**（真源=代码引用+已记录裁决，双输入自动聚合，禁止手改产物）：
  输入1: pre_trade_check_v2.py / trade_executor.py 内 R\\d+[a-z]? 字面引用（锚定所在函数）
  输入2: 下方 CURATED 裁决状态表（仅收录**有文档裁决**的条目；无裁决=unknown，如实标注）
  输出:  data/gate_registry.json + reports/gate_registry.md
用法:  python gen_gate_registry.py   （闸门新增/退役时随 change_sync 重跑）

status 口径:
  ACTIVE   实现在位且有消费记录
  RETIRED  已按裁决退役（保留编号占位，注明裁决日期与原因，防编号复用复发）
  SHADOW   已产未验（影子/观察态，不得当拦截证据引用）
  UNKNOWN  代码有引用但裁决档案缺语义登记 → 待考古补登，禁止凭印象填
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

ROOT = r"D:\zcodesj"
SOURCES = {
    "pre_trade_check_v2.py": os.path.join(ROOT, "pre_trade_check_v2.py"),
    "trade_executor.py": os.path.join(ROOT, "trade_executor.py"),
}
OUT_JSON = os.path.join(ROOT, "data", "gate_registry.json")
OUT_MD = os.path.join(ROOT, "reports", "gate_registry.md")

GATE_RE = re.compile(r"\bR(\d{1,2}[a-z]?|S5)\b")
FUNC_RE = re.compile(r"^def ([A-Za-z_0-9]+)")

# 已记录裁决（证据列 = 裁决出处；只收文档可指认项）
CURATED: dict[str, dict] = {
    "R1":   {"status": "RETIRED", "note": "结构性空转下线", "evidence": "SOP 2026-09-10 六闸退役（R1/R14-R18）；CLI 参数留兼容"},
    "R14":  {"status": "RETIRED", "note": "结构性空转下线", "evidence": "SOP 2026-09-10 六闸退役"},
    "R15":  {"status": "RETIRED", "note": "结构性空转下线", "evidence": "SOP 2026-09-10 六闸退役"},
    "R16":  {"status": "RETIRED", "note": "结构性空转下线", "evidence": "SOP 2026-09-10 六闸退役"},
    "R17":  {"status": "RETIRED", "note": "结构性空转下线", "evidence": "SOP 2026-09-10 六闸退役"},
    "R18":  {"status": "RETIRED", "note": "结构性空转下线", "evidence": "SOP 2026-09-10 六闸退役"},
    "R19":  {"status": "ACTIVE", "note": "解禁回避（编号复用：旧 R19 锤子线 8/14 已移出，本号现义=解禁回避；复用风险由本表登记）",
     "evidence": "pre_trade_check_v2 头注 + SOP R19 段 + PD-013 影子(R19集中度影子观察, 与解禁回避同号双用途=待裁决考古项)"},
    "R20":  {"status": "RETIRED", "note": "旧 R20 V型反转 8/14 移出判定块", "evidence": "pre_trade_check_v2 头注"},
    "R22":  {"status": "ACTIVE", "note": "东财vs同花顺双口径资金闸", "evidence": "SOP R22 段 + 22:00 第十三步(双口径采集为其数据源)"},
    "R25":  {"status": "ACTIVE", "note": "崩盘结构闸（六闸退役后承接崩盘判定）", "evidence": "pre_trade_check_v2 头注 2026-09-10"},
    "R26":  {"status": "RETIRED", "note": "bp_ep TopN 名单衍生判定（对照）随名单制废除退役", "evidence": "主人 2026-09-24 两道令 + AGENTS §10 注记"},
    "R27":  {"status": "RETIRED", "note": "名单价衍生判定（批量价差）随名单制废除退役", "evidence": "主人 2026-09-24 两道令 + AGENTS §10 注记"},
    "R28":  {"status": "ACTIVE", "note": "C7-R28 持有期守卫（reconcile 回填 buy_tracker 使其次日生效）", "evidence": "22:00 链第二步 reconcile_pending_fills 注记"},
    "R30a": {"status": "ACTIVE", "note": "行业集中度闸+豁免口", "evidence": "SOP R30a 段"},
    "R31":  {"status": "ACTIVE", "note": "追高拦截主力闸（六闸退役后与 R-S5/R32/R7 共担）；9/28 复证=回踩一般规则已证伪, 本闸禁做回踩买入依据",
     "evidence": "pre_trade_check_v2 头注 + sold_rider_recon 纪律行"},
    "R32":  {"status": "ACTIVE", "note": "追高拦截族", "evidence": "pre_trade_check_v2 头注 2026-09-10"},
    "R33":  {"status": "SHADOW", "note": "资金活跃度负面清单接闸：已产未验（60日影子未满），不得当拦截证据", "evidence": "SOP 2026-09-24 R33 段 + fixture 9cd9017"},
    "S5":   {"status": "ACTIVE", "note": "R-S5 追高/S-5 止损执行族（-8% 触发即执行）", "evidence": "AGENTS §10 铁律2 + stop_loss_executor"},
    # —— 2026-09-29 凌晨考古补登（S3 欠账清零轮，语义提炼自 pre_trade_check_v2/trade_executor 实现注释）——
    "R1b":  {"status": "ACTIVE", "note": "挂单价与实时市价偏离熔断（±5% 外 fail-closed，执行端最后防线）", "evidence": "trade_executor.py L88/L417（2026-08-27，科森参考价滞后 +20% 案）"},
    "R3":   {"status": "ACTIVE", "note": "批量买上限（与 R8 共用当日状态跟踪源）", "evidence": "pre_trade_check_v2 L56/L135 当日状态跟踪段"},
    "R4":   {"status": "RETIRED", "note": "防御蓝筹行业黑名单——主人 8/14 裁决「行业不该拦截」，判定块已移除", "evidence": "pre_trade_check_v2 L72 [已移除] 标记"},
    "R5":   {"status": "ACTIVE", "note": "40日振幅提示位（8/14 十年回测 WRONG 降级：仅 WARN 不拦截）", "evidence": "pre_trade_check_v2 L767 + --amplitude-40d 参数"},
    "R6":   {"status": "ACTIVE", "note": "收高闸（9/10 无证据降级为提示位；主人逐笔特批豁免口在位，与 R-S5/R31 同构）", "evidence": "pre_trade_check_v2 L673/L718 证据研究同批注记"},
    "R7":   {"status": "ACTIVE", "note": "盘中个股/板块异动兜底闸（与 R-S5/R31/R32 共担追高拦截）", "evidence": "pre_trade_check_v2 L7/L172/L672"},
    "R8":   {"status": "ACTIVE", "note": "分化日状态/批量买上限（与 R3 同源）", "evidence": "pre_trade_check_v2 L56/L135"},
    "R10":  {"status": "RETIRED", "note": "单笔金额上限废除——2026-10-26 主人令「从此不再限定模拟盘持仓的持股数量以及仓位大小」（与铁律3持仓≤50只/G14/A1/A3/sizing权重帽同批退役；G10 提示位不受影响）", "evidence": "AGENTS §10.3 + PROJECT_BOUNDARY §6 2026-10-26 行；判定块已移除"},
    "R13":  {"status": "ACTIVE", "note": "重复买入防线（8/13 高德红外 9:34/9:36 双单 7200 股事故防线；持仓源 fail-open 但独立生效）", "evidence": "pre_trade_check_v2 L811 + _held_codes"},
    "R23":  {"status": "ACTIVE", "note": "持仓加仓需显式确认（8/20 TCL科技 裸奔加仓案：加仓=买入必过闸门）", "evidence": "pre_trade_check_v2 L815"},
    "R24":  {"status": "ACTIVE", "note": "板块高潮禁追（读最新快照，sector∈高潮板块 → reject；fail-open 显式标注）", "evidence": "pre_trade_check_v2 L167/L187（2026-08-21 主人批准）"},
    "R27b": {"status": "ACTIVE", "note": "昨收溢价 WARN 提示（8/28 建，与 R28 hotspot 追价 REJECT 互补的无专属追价闸防线）", "evidence": "pre_trade_check_v2 L84/L630"},
    "R30b": {"status": "ACTIVE", "note": "组合级行业占比上限25%（PD-024② 2026-10-01 主人「继续」批准转实: 单一申万L1持仓市值(含拟买)/总资产>25% 封新买 REJECT 存量不动; westock实时持仓+portfolio.totalAssets(三层嵌套 data.portfolio)+stock_sector_sw1; fail-open标注; --owner-approve-r30b 特批; 单测7/7+实弹交运簇12.5% PASS）", "evidence": "pre_trade_check_v2 check_r30b_cluster + test_r30b_cluster_gate.py"},
    "R34":  {"status": "ACTIVE", "note": "脉冲次日资金提示（PD-018② 2026-10-02 批准 WARN 档: 板块T-1净流入≥10亿→次日买入WARN防一日游; 实时资金本地不可得=未验证维度明示; 单例证据不足硬闸 R27b 同构; em_flow_snapshot 按申万L1聚合, 裸码前缀归一化——单测抓出恒0静默失效真bug已修）", "evidence": "pre_trade_check_v2 check_r34_pulse_nextday + test_r34_r35_gates.py 11/11"},
    "R35":  {"status": "ACTIVE", "note": "卖后同日回补冷却闸（PD-018③ 2026-10-02 批准 REJECT 硬闸: 同码当日有SELL成交→回补须间隔≥30分钟∧买价<卖价, 否则REJECT——瑞捷300977反向T违规的机制化封锁; --owner-approve-r35 特批同构默认关; trade_records 当日SELL真源）", "evidence": "pre_trade_check_v2 check_r35_resell_cooldown + test_r34_r35_gates.py; PD-018④尾盘批量均价线限价=trade_executor do_buy avg_price截断(时段≥14:00∧当日已买≥3只)"},
    # —— 2026-10-18 DeepSeek外部审计 V7 漂移补登（主人「继续」按推荐批准; 裁决出处=文档可指认项）——
    "R36":  {"status": "SHADOW", "note": "高位缩量接货 WARN（PD-030① 影子: 判据 fwd20 中位≤-2% 且 win≤42% ≥60交易日, 影子未满不得当拦截证据）", "evidence": "git e1231f1 两馒头案 PD-030 十项全部落地 2026-10-03 + PENDING_DECISIONS PD-030 处置"},
    "R37":  {"status": "ACTIVE", "note": "情绪档门（弱档<40家 REJECT / 中档 WARN / 强档 clear, fail-open; 实测0930强档63家与择时状态机交叉一致）", "evidence": "PD-036 主人 AskUserQuestion 批①②③ 2026-10-07 git 6f7aac5"},
    "R38":  {"status": "ACTIVE", "note": "期望闸（gauge 组级 T20E 中位<0 × LOW_VOL → REJECT / 非弱档 WARN; 缺证据 fail-open; 特批 --owner-approve-r38）", "evidence": "SOP九十六 主人令「修复优化迭代」2026-10-08 git 90b21e1"},
    "R39":  {"status": "ACTIVE", "note": "落刀闸（开盘跳空≤-3% / 现价深跌≤-5%, 腾讯真实行情源; 特批 --owner-approve-r39）", "evidence": "SOP九十六 git 90b21e1 同上"},
    "R41":  {"status": "ACTIVE", "note": "主线对齐 WARN-only（候选申万L1 ∉ 方向层C1 量能主线集 → WARN 提请不拦截; 升 REJECT 须主人定义低位批例外条款）", "evidence": "SOP九十七 git 5960c72 2026-10-08"},
    "R2":   {"status": "RETIRED", "note": "历史空号无文档裁决——缺口呈证登记, 永久占位防编号复用（非实质裁决性退役）", "evidence": "analysis/gate_gap_crosscheck_20261018_out.md 全库 gate 形态扫描零定义痕迹 2026-10-18"},
    "R9":   {"status": "RETIRED", "note": "历史空号无文档裁决——缺口呈证登记, 永久占位防编号复用（非实质裁决性退役）", "evidence": "analysis/gate_gap_crosscheck_20261018_out.md 同上"},
    "R11":  {"status": "RETIRED", "note": "历史空号无文档裁决——缺口呈证登记, 永久占位防编号复用（非实质裁决性退役）", "evidence": "analysis/gate_gap_crosscheck_20261018_out.md 同上"},
    "R21":  {"status": "RETIRED", "note": "历史空号无文档裁决——缺口呈证登记, 永久占位防编号复用（非实质裁决性退役）", "evidence": "analysis/gate_gap_crosscheck_20261018_out.md 同上"},
    "R29":  {"status": "RETIRED", "note": "历史空号无文档裁决——缺口呈证登记, 永久占位防编号复用（非实质裁决性退役）", "evidence": "analysis/gate_gap_crosscheck_20261018_out.md 同上"},
    "R40":  {"status": "RETIRED", "note": "历史空号无文档裁决——缺口呈证登记, 永久占位防编号复用（非实质裁决性退役）", "evidence": "analysis/gate_gap_crosscheck_20261018_out.md 同上"},
}


def _scan(path: str, label: str) -> dict[str, dict]:
    """收集 gate_id -> {file, n_refs, functions(最近函数名去重)}。"""
    hits: dict[str, dict] = {}
    if not os.path.exists(path):
        return hits
    cur_fn = "<module>"
    for line in open(path, encoding="utf-8", errors="replace"):
        fm = FUNC_RE.match(line)
        if fm:
            cur_fn = fm.group(1)
        for g in GATE_RE.findall(line):
            gid = "R" + g if not g.startswith("S") else "R-" + g
            # normalize: GATE_RE returns like '19','30a','S5'
            gid = "R-S5" if g == "S5" else ("R" + g)
            e = hits.setdefault(gid, {"refs": 0, "functions": []})
            e["refs"] += 1
            if cur_fn not in e["functions"] and len(e["functions"]) < 6:
                e["functions"].append(cur_fn)
    for v in hits.values():
        v["file"] = label
    return hits


def main() -> int:
    agg: dict[str, dict] = {}
    for label, path in SOURCES.items():
        for gid, info in _scan(path, label).items():
            a = agg.setdefault(gid, {"refs": 0, "functions": [], "files": []})
            a["refs"] += info["refs"]
            a["files"].append(label)
            for fn in info["functions"]:
                if fn not in a["functions"]:
                    a["functions"].append(fn)

    def _sort_key(g: str) -> tuple[int, str]:
        m = re.match(r"R-?S?(\d+)", g)
        return (int(m.group(1)) if m else 999, g)

    registry = []
    for gid in sorted(agg.keys(), key=_sort_key):
        cur = CURATED.get(gid) or CURATED.get(gid.lstrip("R-")) or CURATED.get(gid.replace("R-", "S"))
        info = agg[gid]
        entry = {
            "gate": gid,
            "refs_in_code": info["refs"],
            "files": sorted(set(info["files"])),
            "anchor_functions": info["functions"][:6],
            "status": cur["status"] if cur else "UNKNOWN",
            "note": cur["note"] if cur else "代码有引用但裁决档案缺语义登记——待考古补登，禁止凭印象填",
            "evidence": cur["evidence"] if cur else "无（UNKNOWN）",
        }
        registry.append(entry)

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generator": "gen_gate_registry.py",
        "⚠️真源声明": "本文件为生成物，禁止手改；状态变更须改 CURATED/重跑本脚本，"
                     "裁决出处只收文档可指认项（UNKNOWN=诚实的空白，不是可填的默认值）",
        "entries": registry,
        "counts": {s: sum(1 for r in registry if r["status"] == s)
                   for s in ("ACTIVE", "RETIRED", "SHADOW", "UNKNOWN")},
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    lines = ["# R 闸门注册表（生成物，禁止手改）", "",
             f"- 生成：{out['generated_at']} | 生成器 gen_gate_registry.py",
             f"- 状态计数：{out['counts']}",
             "- 扫描范围=pre_trade_check_v2.py + trade_executor.py 两份执行链代码；仅 SOP/研究文档出现"
             "而无代码实现的编号（如 R21/R29 等）不在本表，考古补登时并入。"
             "2026-10-18 缺口呈证后，六缺号（R2/R9/R11/R21/R29/R40）已以 CURATED 占位登记——"
             "未来代码若引用任一缺号，将以占位状态出表（防复用闸生效），而非 UNKNOWN。"
             "UNKNOWN=实现有引用但裁决档案缺登记——2026-09-29 凌晨考古清零后若再现，"
             "即新增闸未同步登记（change_sync 应拦）。",
             "- 编号复用警示：R19 一号双义（解禁回避+集中度影子观察），拆号前引用 R19 必须现场核对语境；"
             "旧 R19 锤子线/旧 R20 V反 8/14 已移出（RETIRED 行留位防复用）", "",
             "| 闸 | 状态 | 代码引用数 | 语义（仅登记有裁决项） | 证据出处 |",
             "|---|---|---|---|---|"]
    for r in registry:
        lines.append("| %s | %s | %d | %s | %s |" % (
            r["gate"], r["status"], r["refs_in_code"], r["note"][:90], r["evidence"][:70]))
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[REGISTRY] entries=%d counts=%s -> %s / %s" % (
        len(registry), out["counts"], OUT_JSON, OUT_MD))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
