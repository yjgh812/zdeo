#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intervention_effect_audit.py — 干预效果审计（呈现层，零交易权）。

问题（2026-11-07 主人「其他继续完善」令）：实盘过渡方法论中的“干预效果”指标
（被人工干预的单 vs 纯规则单，收益差多少）此前无专项统计——归因层记了原因但不做这个对比。

数据源（全部只读）：attribution.db position_history（平仓持仓：exit_reason/pnl_pct/exit_date）。
分类（依据 exit_reason 标签约定，优先级：规则 > 执行干预 > 决策干预 > 未识别）：
  规则退出   : S-5止损 / 波动止盈 / 持有周期退出 / 主线方向法规则 / 主线清仓 / 趋势走坏 / PD-0
               （“规则触发+主人令执行”的行仍归规则侧，但单列标注不混入干预侧，供人工复核）
  执行干预   : 撤旧 / 重挂 / 让价 / 复挂 / REQUOTE —— 决策未变，仅执行方式被改
  决策干预   : 主人令 / 主人指令 / 主人显式授权 / 正则 主人[0-9]，且无前置规则关键词
  理由缺失   : 盘后审计导入丢失原因 / 卖出:后为空 —— 数据损失如实披露，不计入对比
  未匹配     : 有文本但无标签命中 —— 如实列示供人工复核，不默默归类
判据（方法论指南口径，双侧各 n≥10 才判，否则“样本不足不做方向性结论”）：
  决策干预均值低于规则退出均值 30%+ → 干预负价值信号（呈主人裁决）；
  干预效果为正且差距≥30% → 提示把干预模式编码为过滤条件候选（呈主人裁决）。
输出：reports/intervention_effect_audit_YYYYMMDD.md + stdout 摘要。
退出码：0 正常（含样本不足）/ 3 数据源不可用（fail-closed，拒绝假成功）。
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = r"D:\zcodesj\attribution\attribution.db"
REPORT_DIR = r"D:\zcodesj\reports"
MIN_SIDE_N = 10           # 双侧样本下限（指南口径）
UNRECOGNIZED_LIST_MAX = 20
MIXED_LIST_MAX = 30

RULE_KWS = ("S-5", "波动止盈", "持有周期退出", "主线方向法规则",
            "主线清仓", "趋势走坏", "空头排列", "PD-0", "S5_stoploss")
EXEC_INT_KWS = ("撤旧", "重挂", "让价", "复挂", "REQUOTE")
_DECISION_RE = re.compile(r"主人[0-9]")


def _classify(reason: str) -> tuple[str, str]:
    """按标签约定分类一条 exit_reason。返回 (类别, 标注)。
    理由缺失（盘后审计导入丢失原因 / 卖出:后为空）单独归类，如实披露数据损失，
    不与“分类器未覆盖”混装。"""
    t = reason
    if "理由=" in t:
        t = t.split("理由=", 1)[1]
    t = t.split(" | C6", 1)[0].strip()
    if (not t or t == "卖出:" or t.startswith("盘后审计导入")
            or t.startswith("|C6") or t.startswith("| C6")
            or t.startswith("台账卫生补录")):
        return ("理由缺失", t[:40])
    has_rule = any(k in t for k in RULE_KWS)
    has_exec = any(k in t for k in EXEC_INT_KWS)
    has_dec = ("主人令" in t or "主人指令" in t or "主人显式授权" in t
               or "主人「" in t or "主人今日已批" in t
               or bool(_DECISION_RE.search(t)))
    if has_rule:
        tag = []
        if has_exec:
            tag.append("含执行干预标注")
        if has_dec:
            tag.append("含主人令执行")
        return ("规则退出", "+".join(tag))
    if has_exec:
        return ("执行干预", "")
    if has_dec:
        return ("决策干预", "")
    return ("未匹配", t[:40])


def _load_closed() -> list[dict]:
    if not os.path.exists(DB):
        raise FileNotFoundError(f"{DB} 不存在——fail-closed 拒绝假成功")
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT code,name,entry_date,exit_date,pnl_pct,exit_reason "
            "FROM position_history "
            "WHERE pnl_pct IS NOT NULL AND exit_date IS NOT NULL "
            "ORDER BY exit_date DESC").fetchall()
    finally:
        conn.close()
    keys = ("code", "name", "entry_date", "exit_date", "pnl_pct", "exit_reason")
    return [dict(zip(keys, r)) for r in rows]


def _stats(rs: list[dict]) -> dict | None:
    vals = [float(r["pnl_pct"]) for r in rs]
    n = len(vals)
    if n == 0:
        return None
    s = sorted(vals)
    return {
        "n": n,
        "mean": sum(vals) / n,
        "median": s[n // 2],
        "win": sum(1 for v in vals if v > 0) / n * 100,
    }


def _fmt_stats(s: dict | None) -> str:
    if s is None:
        return "无样本"
    return (f"n={s['n']} 均值{s['mean']:+.2f}% 中位{s['median']:+.2f}% "
            f"胜率{s['win']:.0f}%")


def _verdict_lines(rule_s: dict | None, dec_s: dict | None,
                   label: str = "规则退出") -> list[str]:
    """判据只对「决策干预 vs 规则退出」出具（执行干预由 A1 滑点判据承担执行质量面）。
    label 区分全量规则桶与纯规则桶（无标注行），混合行含执行干预深亏会拖低全量桶。"""
    out = []
    n_r = rule_s["n"] if rule_s else 0
    n_d = dec_s["n"] if dec_s else 0
    if n_r >= MIN_SIDE_N and n_d >= MIN_SIDE_N:
        gap = rule_s["mean"] - dec_s["mean"]
        rel = gap / abs(rule_s["mean"]) if rule_s["mean"] else float("nan")
        if rel >= 0.30:
            out.append(f"🔔 决策干预均值比{label}低 {gap:+.2f}pp（相对{rel*100:.0f}%）"
                       f"≥30% → 干预负价值信号（呈主人裁决）")
        elif rel <= -0.30:
            out.append(f"📈 干预效果为正：决策干预均值比{label}高 {-gap:+.2f}pp"
                       f"（相对{abs(rel)*100:.0f}%）≥30% → 建议提取干预模式为"
                       f"过滤条件候选（呈主人裁决）")
        else:
            out.append(f"⚖️ 差距<30%：{label}均值{rule_s['mean']:+.2f}% vs "
                       f"决策干预均值{dec_s['mean']:+.2f}%（相对差{rel*100:+.0f}%）"
                       f"→ 未见显著干预损益差")
    else:
        out.append(f"⏳ 样本不足（{label}n={n_r}，决策干预n={n_d}，双侧需≥{MIN_SIDE_N}）"
                   f"——不做方向性结论（反臆想纪律）")
    return out


def main() -> int:
    try:
        rows = _load_closed()
    except Exception as e:  # noqa: BLE001
        print(f"[INT-AUDIT] [FAIL-CLOSED] 数据源不可用({type(e).__name__}: {e})")
        return 3
    cls: dict[str, list[dict]] = {"规则退出": [], "执行干预": [], "决策干预": [],
                                  "理由缺失": [], "未匹配": []}
    for r in rows:
        reason = str(r.get("exit_reason") or "")
        c, tag = _classify(reason)
        r2 = {**r, "tag": tag}
        cls[c].append(r2)

    rule_s = _stats(cls["规则退出"])
    exec_s = _stats(cls["执行干预"])
    dec_s = _stats(cls["决策干预"])
    pure_s = _stats([r for r in cls["规则退出"] if not r["tag"]])
    verdicts = _verdict_lines(rule_s, dec_s)
    verdicts += _verdict_lines(pure_s, dec_s, label="纯规则(无标注)")

    lines = [
        f"# 干预效果审计 {datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"- 数据源：attribution.db position_history 平仓持仓（只读），共 {len(rows)} 笔",
        "- 分类优先级：规则 > 执行干预 > 决策干预 > 未识别；“规则触发+主人令执行”"
        "归规则侧并单列标注；判据仅对「决策干预 vs 规则退出」出具，双侧各≥10笔。",
        "",
        "## 分类统计",
        "",
        "| 类别 | 笔数 | 均值 | 中位 | 胜率 |",
        "|---|---|---|---|---|",
        f"| 规则退出 | {rule_s['n'] if rule_s else 0} | "
        f"{rule_s['mean']:+.2f}% | {rule_s['median']:+.2f}% | "
        f"{rule_s['win']:.0f}% |" if rule_s else "| 规则退出 | 0 | - | - | - |",
        f"| 执行干预 | {exec_s['n'] if exec_s else 0} | "
        f"{exec_s['mean']:+.2f}% | {exec_s['median']:+.2f}% | "
        f"{exec_s['win']:.0f}% |" if exec_s else "| 执行干预 | 0 | - | - | - |",
        f"| 决策干预 | {dec_s['n'] if dec_s else 0} | "
        f"{dec_s['mean']:+.2f}% | {dec_s['median']:+.2f}% | "
        f"{dec_s['win']:.0f}% |" if dec_s else "| 决策干预 | 0 | - | - | - |",
        f"| 理由缺失 | {len(cls['理由缺失'])} | - | - | - |",
        f"| 未匹配 | {len(cls['未匹配'])} | - | - | - |",
        "",
        "## 判据结论",
        "",
    ]
    lines += [f"- {v}" for v in verdicts]
    lines += ["", f"## 理由缺失 {len(cls['理由缺失'])} 笔（盘后审计导入丢失原因/卖出:后为空——"
              "数据损失如实披露，不计入对比）", ""]
    if cls["理由缺失"]:
        ex = "；".join(f"{r['name']}({r['exit_date']})" for r in cls["理由缺失"][:3])
        lines.append(f"- 示例: {ex} 等")
    lines += ["", "## 未匹配行（分类器未覆盖，人工复核清单）", ""]
    if cls["未匹配"]:
        lines += ["| 代码 | 名称 | 平仓日 | 收益% | exit_reason 摘要 |", "|---|---|---|---|---|"]
        for r in cls["未匹配"][:UNRECOGNIZED_LIST_MAX]:
            lines.append(f"| {r['code']} | {r['name']} | {r['exit_date']} "
                         f"| {float(r['pnl_pct']):+.2f} | {r['tag']} |")
        if len(cls["未匹配"]) > UNRECOGNIZED_LIST_MAX:
            lines.append(f"*（另有 {len(cls['未匹配']) - UNRECOGNIZED_LIST_MAX} 行未列示）*")
    else:
        lines.append("（无）")
    lines += ["", "## 决策干预行（复核清单）", ""]
    if cls["决策干预"]:
        lines += ["| 代码 | 名称 | 平仓日 | 收益% | 理由摘要 |", "|---|---|---|---|---|"]
        for r in cls["决策干预"][:MIN_SIDE_N * 3]:
            lines.append(f"| {r['code']} | {r['name']} | {r['exit_date']} "
                         f"| {float(r['pnl_pct']):+.2f} | {str(r['exit_reason'])[:60]} |")
    else:
        lines.append("（无）")
    lines += ["", "## 混合行（规则+主人令/执行干预标注，人工复核）", ""]
    mixed = [r for r in cls["规则退出"] if r["tag"]]
    if mixed:
        lines += ["| 代码 | 名称 | 平仓日 | 收益% | 标注 |", "|---|---|---|---|---|"]
        for r in mixed[:MIXED_LIST_MAX]:
            lines.append(f"| {r['code']} | {r['name']} | {r['exit_date']} "
                         f"| {float(r['pnl_pct']):+.2f} | {r['tag']} |")
        if len(mixed) > MIXED_LIST_MAX:
            lines.append(f"*（另有 {len(mixed) - MIXED_LIST_MAX} 行未列示）*")
    else:
        lines.append("（无）")

    path = os.path.join(REPORT_DIR,
                        f"intervention_effect_audit_{datetime.now():%Y%m%d}.md")
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[INT-AUDIT] 规则{rule_s['n'] if rule_s else 0} "
          f"执行干预{exec_s['n'] if exec_s else 0} "
          f"决策干预{dec_s['n'] if dec_s else 0} "
          f"理由缺失{len(cls['理由缺失'])} 未匹配{len(cls['未匹配'])} -> {path}")
    for v in verdicts:
        print(f"[INT-AUDIT] {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
