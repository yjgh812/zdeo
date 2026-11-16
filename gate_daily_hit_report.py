#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_daily_hit_report.py — 盘后当日闸门命中报告（改革计划阶段三3.1，2026-08-18落地）

2026-08-28 数据源切换: 主源=attribution.db guard_precheck_log（生产闸门审计真源,
pre_trade_check_v2 每次评估幂等落库）; 旧 .gate_logs（enrich/final/buy 管线）自 8/21
后无写入=死源, 降级为 DB 不可读时的兜底。修复: 8/24-8/27 四期报告读死源全部伪空。

输出 reports/gate_hit_YYYYMMDD.md —— 当日闸门触发统计：
  - 评估总数 / PASS / PASS_WITH_WARN / REJECT 分布
  - 硬底线触发排行（rejects/warnings 中 R 编号正则提取=当日风险画像）
  - 通过票清单 / 评分维度零分热力图(仅旧源有 scoring 字段)
幂等：重跑覆盖当日报告文件。
用法: python gate_daily_hit_report.py [--date YYYYMMDD]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime

GATE_LOG_DIR = r"D:\zcodesj\.gate_logs"
REPORT_DIR = r"D:\zcodesj\reports"
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"

# 回归套件/团队调试用的测试代码与名称(gate_regression_test.py 基准码段),
# 统计时剔除防污染(实测8/17报告曾混入"测试股600001")
_TEST_CODES = {"600001", "600009", "600010", "600011", "600012", "600013",
               "600014", "600015", "600016", "600017", "300001", "300002"}
_TEST_NAME_MARKS = ("测试", "缺字段股", "北交测试")


def _is_test_record(r: dict) -> bool:
    code = str(r.get("code", ""))
    name = str(r.get("name", ""))
    return code in _TEST_CODES or any(m in name for m in _TEST_NAME_MARKS)


def load_today_results(date_str: str) -> tuple[list[dict], str]:
    """读当日闸门评估：主源 guard_precheck_log（DB），失败兜底旧 .gate_logs。

    返回 (rows, source)；DB 行映射为旧结构（verdict/hard_fails/hard_gates），
    R 编号从 rejects/warnings 文本正则提取。"""
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=15)
        # 2026-08-28 P1a 配套：排除 source='pre_sell' 卖出判定行（hold_reasons 含
        # "C7-R28"字样会让 R 编号命中虚增，且本报告口径=买入侧闸门 R1-R31）
        raw = con.execute(
            "SELECT id, code, name, source, verdict, rejects, warnings "
            "FROM guard_precheck_log "
            "WHERE trade_date=? AND COALESCE(source,'') != 'pre_sell' ORDER BY id",
            (date_str,)).fetchall()
        con.close()
    except Exception:
        raw = None
    if raw is not None:
        out: list[dict] = []
        for _id, code, name, source, verdict, rejects, warnings in raw:
            code = str(code).zfill(6)
            if _is_test_record({"code": code, "name": name or ""}):
                continue
            # 硬底线=rejects 中的 R 编号(真拦截); warnings 单独存 _warn_gates 不进排行
            hard_gates: dict[str, dict] = {}
            try:
                for txt in json.loads(rejects or "[]"):
                    for rid in re.findall(r"R\d+[a-zA-Z]?", str(txt)):
                        hard_gates.setdefault(rid, {"reason": str(txt)})
            except Exception:
                pass
            warn_gates: dict[str, dict] = {}
            try:
                for txt in json.loads(warnings or "[]"):
                    for rid in re.findall(r"R\d+[a-zA-Z]?", str(txt)):
                        warn_gates.setdefault(rid, {"reason": str(txt)})
            except Exception:
                pass
            out.append({"code": code, "name": name or "", "verdict": verdict,
                        "source": source, "hard_fails": list(hard_gates),
                        "hard_gates": hard_gates, "_warn_gates": warn_gates,
                        "_src_file": "guard_precheck_log"})
        return out, "guard_precheck_log"
    # 兜底: 旧 .gate_logs（8/21 后无写入, 仅 DB 不可读时使用）
    out = []
    for f in glob.glob(os.path.join(GATE_LOG_DIR, "*_gate_result.json")):
        base = os.path.basename(f)
        if not base.startswith(date_str):
            continue
        try:
            with open(f, encoding="utf-8") as fp:
                d = json.load(fp)
        except Exception:
            continue
        for r in d.get("results", []):
            if not r.get("code") or _is_test_record(r):
                continue
            r = dict(r)
            r["_src_file"] = base
            out.append(r)
    return out, ".gate_logs(legacy兜底)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args()
    ds = args.date
    iso = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}"

    results, src = load_today_results(ds)
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"gate_hit_{ds}.md")

    lines = [f"# 闸门命中报告 {iso}", ""]

    if not results:
        lines += [f"当日无闸门评估记录（数据源 {src} 无当日记录）。", ""]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[空报告] {path}（源={src}）")
        return 0

    # ---- verdict 分布 ----
    vc = Counter(r.get("verdict", "?") for r in results)
    lines += ["## 评估分布", "",
              f"- 总评估: **{len(results)}** 笔（{len(set(r['code'] for r in results))} 只去重）",
              f"- PASS: {vc.get('PASS', 0)} | PASS_WITH_WARN: {vc.get('PASS_WITH_WARN', 0)} "
              f"| REJECT: {vc.get('REJECT', 0)}", ""]

    # ---- 硬底线触发排行（仅 rejects 真拦截; warnings 另列 fail-open 热点）----
    gate_hits = Counter()
    gate_samples: dict[str, list[str]] = {}
    for r in results:
        for g in r.get("hard_fails", []):
            gate_hits[g] += 1
            if len(gate_samples.setdefault(g, [])) < 3:
                reason = (r.get("hard_gates", {}).get(g, {}) or {}).get("reason", "")
                reason = re.sub(r"\d{6}", "", str(reason))  # 剔除理由内重复代码, 防名称校验器误配对
                gate_samples[g].append(f"{r.get('name','?')}{r.get('code','')} {reason[:60]}")
    if gate_hits:
        lines += ["## 硬底线触发排行（当日真拦截画像, 仅 REJECT 拒因）", "",
                  "| 闸门 | 触发次数 | 样例 |", "|---|---|---|"]
        for g, n in gate_hits.most_common():
            sample = "; ".join(gate_samples.get(g, []))
            lines.append(f"| {g} | {n} | {sample} |")
        lines.append("")
    else:
        lines += ["## 硬底线触发排行", "", "当日无硬底线触发。", ""]

    # ---- warnings 热点（fail-open/数据缺口画像, 非拦截）----
    warn_hits = Counter()
    for r in results:
        for g in (r.get("_warn_gates") or {}):
            warn_hits[g] += 1
    if warn_hits:
        lines += ["## 警告热点（fail-open/数据缺口, 非拦截）", "",
                  ", ".join(f"{g}×{n}" for g, n in warn_hits.most_common(8)), ""]

    # ---- 通过票清单 ----
    passed = [r for r in results if r.get("verdict") == "PASS"]
    warned = [r for r in results if r.get("verdict") == "PASS_WITH_WARN"]
    if passed or warned:
        lines += ["## 通过票（按 code+verdict 去重）", ""]
        seen_pv: set[tuple[str, str]] = set()
        for label, grp in (("PASS", passed), ("WARN", warned)):
            for r in grp:
                key = (r.get("code", ""), label)
                if key in seen_pv:
                    continue
                seen_pv.add(key)
                extra = f" source={r['source']}" if r.get("source") else ""
                if r.get("amount_adjusted"):
                    extra += f" 降半仓→{r['amount_adjusted']:.0f}"
                lines.append(f"- [{label}] {r.get('name','?')}({r.get('code','')}){extra}")
        lines.append("")

    # ---- 评分维度零分热力图 ----
    dim_zeros: Counter = Counter()
    for r in results:
        for k, v in (r.get("scoring") or {}).items():
            if isinstance(v, dict) and v.get("score") == 0 and k != "D8_low_vol_penalty":
                dim_zeros[k] += 1
    if dim_zeros:
        lines += ["## 评分维度零分统计（数据质量/维度短板）", ""]
        for k, n in dim_zeros.most_common():
            lines.append(f"- {k}: {n} 笔")
        lines.append("")

    # ---- gate_state 摘要 ----
    state_f = os.path.join(GATE_LOG_DIR, f"gate_state_{ds}.json")
    if os.path.isfile(state_f):
        try:
            st = json.load(open(state_f, encoding="utf-8"))
            lines += ["## 当日风控状态（gate_state）", "",
                      f"- 闸门尝试计数 attempts_today: {st.get('attempts_today', st.get('orders_today', 0))}（G15熔断线30, t33改名）",
                      f"- 同code否决: {sum(1 for v in (st.get('rejects_by_code') or {}).values() if v >= 2)} 只达冷却线",
                      f"- 同source级联: {sum(1 for v in (st.get('rejects_by_source') or {}).values() if v >= 2)} 个源达熔断线",
                      ""]
        except Exception:
            pass

    lines.append(f"\n---\n*生成: {datetime.now().isoformat(timespec='seconds')}；"
                 f"数据源: {src}（guard_precheck_log 为主, 2026-08-28 切换）；幂等重跑覆盖。*")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[报告] {path} | 评估{len(results)}笔 "
          f"P{vc.get('PASS',0)}/W{vc.get('PASS_WITH_WARN',0)}/R{vc.get('REJECT',0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
