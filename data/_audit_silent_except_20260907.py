#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""静默吞错扫描器（2026-09-07，主人引"吞错是故障的土壤"纪律令修复优化）

用 ast 找 ExceptHandler，按捕获后行为分级：
  SILENT_PASS   捕获后仅 pass                       —— 静默吞，最恶劣
  SILENT_BREAK  捕获后仅 break/continue             —— 静默跳过，需定性
  SILENT_RETURN 捕获后仅 return None/""/[]/{}/0 等   —— 静默返回空冒充成功
  OK_TRACE      捕获后有 print/log/raise/写入        —— 合格（有留痕）
只扫核心管线活跃文件（一次性 _tmp/_wr 探索脚本不在本轮范围）。
输出：reports/SILENT_EXCEPT_AUDIT_20260907.{json,md}
"""
import ast
import json
import os
from datetime import datetime

ROOT = r"D:\zcodesj"
OUT_JSON = os.path.join(ROOT, "reports", "SILENT_EXCEPT_AUDIT_20260907.json")
OUT_MD = os.path.join(ROOT, "reports", "SILENT_EXCEPT_AUDIT_20260907.md")

FILES = [
    "daily_signal_production_v4.py", "daily_chain_update.py",
    "trade_executor.py", "trend_checker.py", "pre_trade_check_v2.py",
    "health_check_daily.py", "leader_watchlist.py", "portrait_feed.py",
    "breakout_scout.py", "qlib_bin_update_daily.py", "stop_loss_executor.py",
    "combo_c_shadow.py", "scan_market.py", "scan_all_a.py",
    "full_market_scan.py", "vibe_intraday.py", "guba_reverse_signal.py",
    "cls_news_mcp.py", "stock_name_lookup.py", "verify_stock_names.py",
    "classical_pattern_prescreen.py", "classical_pattern_roundbottom_wfa.py",
]
PA_DIR = os.path.join(ROOT, "portrait_agent")

TRACE_HINTS = ("print", "logger", "logging", "log.", "raise", "notify",
               "write", "append", "json.dump", "open(", "warnings", "emit")


def handler_grade(handler: ast.ExceptHandler) -> tuple[str, list[int]]:
    """返回 (级别, 行号列表)。body 内语句的行号。"""
    lines = [getattr(s, "lineno", 0) for s in handler.body]
    body = handler.body
    kinds = []
    for s in body:
        if isinstance(s, ast.Pass):
            kinds.append("pass")
        elif isinstance(s, (ast.Break, ast.Continue)):
            kinds.append("flow")
        elif isinstance(s, ast.Return):
            v = s.value
            if v is None or isinstance(v, (ast.Constant, ast.List, ast.Tuple)):
                # 元组首元素为错误消息字符串的也视为结构化(探针模式: None, "错误说明", flag)
                if isinstance(v, ast.Tuple) and v.elts and any(
                    isinstance(e, ast.Constant) and isinstance(e.value, str) and len(e.value) > 4
                    for e in v.elts
                ):
                    kinds.append("ret_structured")
                else:
                    kinds.append("ret_silent")
            elif isinstance(v, ast.Dict):
                ks = [k.value for k in v.keys if isinstance(k, ast.Constant)]
                kinds.append("ret_structured" if any(k in ("status", "note", "error", "verdict") for k in ks)
                             else "ret_silent")
            else:
                kinds.append("ret_val")
        elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
            src = ast.dump(s.value)
            kinds.append("trace" if any(h in src for h in TRACE_HINTS) else "expr_call")
        elif isinstance(s, ast.Raise):
            kinds.append("trace")
        else:
            kinds.append("other")
    if all(k in ("pass",) for k in kinds):
        return "SILENT_PASS", lines
    if all(k in ("pass", "flow") for k in kinds) and "flow" in kinds:
        return "SILENT_BREAK", lines
    if all(k == "ret_silent" for k in kinds):
        return "SILENT_RETURN", lines
    if any(k in ("trace", "ret_val") for k in kinds):
        return "OK_TRACE", lines
    return "MIXED_REVIEW", lines


def main() -> int:
    targets = []
    for f in FILES:
        p = os.path.join(ROOT, f)
        if os.path.isfile(p):
            targets.append(p)
    if os.path.isdir(PA_DIR):
        for f in sorted(os.listdir(PA_DIR)):
            if f.endswith(".py"):
                targets.append(os.path.join(PA_DIR, f))

    hits, errors, scanned = [], [], []
    for path in targets:
        rel = os.path.relpath(path, ROOT)
        try:
            tree = ast.parse(open(path, "r", encoding="utf-8", errors="replace").read())
        except Exception as e:
            errors.append({"file": rel, "error": str(e)})
            continue
        scanned.append(rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                grade, lines = handler_grade(node)
                if grade != "OK_TRACE":
                    hits.append({
                        "file": rel, "line": node.lineno,
                        "type": node.type and ast.dump(node.type)[:40] or "BARE",
                        "grade": grade, "body_lines": lines,
                    })

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files_scanned": len(scanned), "parse_errors": errors, "hits": hits,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)

    from collections import Counter
    by_grade = Counter(h["grade"] for h in hits)
    md = ["# 静默吞错审计 2026-09-07", "",
          f"- 扫描 {len(scanned)} 文件 | 命中 {len(hits)}：{dict(by_grade)}", ""]
    cur = None
    for h in hits:
        if h["file"] != cur:
            cur = h["file"]
            md.append(f"### {cur}")
        md.append(f"- L{h['line']} [{h['grade']}] except {h['type']}")
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))

    print(f"scanned={len(scanned)} hits={len(hits)} grades={dict(by_grade)}")
    print(f"md={OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
