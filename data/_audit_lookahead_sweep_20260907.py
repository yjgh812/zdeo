# -*- coding: utf-8 -*-
"""一次性防前视静态审计（2026-09-07，主人裁决'③都要'触发）。

灵感来源 TradingAgents-astock 的 lookahead_guard 三层测试（工具带日期参数
但实现忽略日期=静默用今天的数=前视）。对屠龙核心管线做三类静态扫描：

  C1 死日期参数: 函数签名带日期语义参数但函数体内零引用
     ——潜在'签名宣称支持时点、实现静默用最新'。
  C2 now/today 调用点: datetime.now()/date.today()/Timestamp.now() 清单
     ——本机为北京时区暂无实害，登记跨时区迁移风险点。
  C3 最新值陷阱: MAX( / .iloc[-1] / .tail(1) / sort_values(...).iloc[0] 行级清单
     ——用于历史复盘口径则是前视，用于'读最新做监控'则合法，需人工归档。

只读审计，不修改任何被扫文件。结果落 reports/anti_lookahead_audit_20260907.md。
"""
import ast
import json
import os
import re
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
OUT_MD = os.path.join(ROOT, "reports", "anti_lookahead_audit_20260907.md")
OUT_JSON = os.path.join(ROOT, "reports", "anti_lookahead_audit_20260907.json")

DATE_PARAM_RE = re.compile(
    r"^(date|asof|as_of|trade_date|trading_date|ann_date|announce_date|"
    r"end_date|start_date|curr_date|current_date|dt|asof_date|base_date)$"
)
C3_PATTERNS = [
    (r"\bMAX\s*\(", "SQL_MAX"),
    (r"\.iloc\[-1\]", "iloc[-1]"),
    (r"\.tail\s*\(\s*1\s*\)", "tail(1)"),
    (r"\.head\s*\(\s*1\s*\)", "head(1)"),
]


def collect_files(mode="core"):
    """mode=core: 核心管线基线（首跑口径）; mode=extra: attribution/ + 根目录其余研究/回测脚本。"""
    files = []
    if mode == "core":
        top_py = [
            "daily_signal_production_v4.py", "daily_chain_update.py",
            "trade_executor.py", "trend_checker.py", "pre_trade_check_v2.py",
            "health_check_daily.py", "leader_watchlist.py", "portrait_feed.py",
            "breakout_scout.py", "qlib_bin_update_daily.py",
            "stop_loss_executor.py", "combo_c_shadow.py",
        ]
        for f in top_py:
            p = os.path.join(ROOT, f)
            if os.path.isfile(p):
                files.append(p)
        pa = os.path.join(ROOT, "portrait_agent")
        if os.path.isdir(pa):
            for f in sorted(os.listdir(pa)):
                if f.endswith(".py"):
                    files.append(os.path.join(pa, f))
        return files
    # extra: 根目录全部 .py（排除 core 已扫+一次性审计自身）+ attribution/
    core_names = {
        "daily_signal_production_v4.py", "daily_chain_update.py",
        "trade_executor.py", "trend_checker.py", "pre_trade_check_v2.py",
        "health_check_daily.py", "leader_watchlist.py", "portrait_feed.py",
        "breakout_scout.py", "qlib_bin_update_daily.py",
        "stop_loss_executor.py", "combo_c_shadow.py",
        "_audit_lookahead_sweep_20260907.py",
    }
    for f in sorted(os.listdir(ROOT)):
        p = os.path.join(ROOT, f)
        if f.endswith(".py") and os.path.isfile(p) and f not in core_names:
            files.append(p)
    attr = os.path.join(ROOT, "attribution")
    if os.path.isdir(attr):
        for f in sorted(os.listdir(attr)):
            if f.endswith(".py"):
                files.append(os.path.join(attr, f))
    return files


def param_used_in_body(func_node, pname):
    for node in ast.walk(func_node):
        if isinstance(node, ast.Name) and node.id == pname:
            return True
        if isinstance(node, ast.Attribute):
            pass
        if isinstance(node, ast.arg):
            continue
    return False


def walk_functions(tree, relpath):
    """yield (relpath, func_qualname, lineno, node)"""
    stack = [(tree, "")]
    while stack:
        parent, prefix = stack.pop()
        for node in ast.iter_child_nodes(parent):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = (prefix + "." + node.name) if prefix else node.name
                yield relpath, qual, node.lineno, node
                stack.append((node, qual))
            elif isinstance(node, ast.ClassDef):
                stack.append((node, (prefix + "." + node.name) if prefix else node.name))


def check_c1_dead_date_params(tree, relpath):
    hits = []
    for relpath_, qual, lineno, fn in walk_functions(tree, relpath):
        args = [a.arg for a in fn.args.args + fn.args.kwonlyargs if a.arg not in ("self", "cls")]
        date_args = [a for a in args if DATE_PARAM_RE.match(a)]
        if not date_args:
            continue
        unused = [a for a in date_args if not param_used_in_body(fn, a)]
        if unused:
            hits.append({
                "file": relpath, "func": qual, "line": lineno,
                "unused_date_params": unused,
            })
    return hits


def check_c2_now_calls(tree, relpath, src):
    hits = []
    pats = [
        (r"datetime\.now\s*\(", "datetime.now()"),
        (r"\bdate\.today\s*\(", "date.today()"),
        (r"pd\.Timestamp\.now\s*\(", "pd.Timestamp.now()"),
        (r"\bnow\(\)", "now()"),
    ]
    for i, line in enumerate(src.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for pat, label in pats:
            if re.search(pat, line):
                hits.append({"file": relpath, "line": i, "label": label,
                             "text": line.strip()[:120]})
                break
    return hits


def check_c3_latest_value(src, relpath):
    hits = []
    for i, line in enumerate(src.splitlines(), 1):
        s = line.strip()
        if s.startswith("#"):
            continue
        for pat, label in C3_PATTERNS:
            if re.search(pat, line):
                hits.append({"file": relpath, "line": i, "label": label,
                             "text": s[:120]})
                break
    return hits


def main():
    mode = "extra" if "--extra" in sys.argv else "core"
    suffix = "_full" if mode == "extra" else ""
    out_md = OUT_MD.replace(".md", f"{suffix}.md")
    out_json = OUT_JSON.replace(".json", f"{suffix}.json")
    files = collect_files(mode)
    c1, c2, c3, errors = [], [], [], []
    scanned = []
    for path in files:
        rel = os.path.relpath(path, ROOT)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            tree = ast.parse(src)
        except Exception as e:
            errors.append({"file": rel, "error": str(e)})
            continue
        scanned.append(rel)
        c1.extend(check_c1_dead_date_params(tree, rel))
        c2.extend(check_c2_now_calls(tree, rel, src))
        c3.extend(check_c3_latest_value(src, rel))

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "files_scanned": scanned,
        "parse_errors": errors,
        "c1_dead_date_params": c1,
        "c2_now_calls": c2,
        "c3_latest_value": c3,
    }
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)

    # ---- markdown ----
    lines = []
    lines.append("# 防前视静态审计报告（2026-09-07）\n")
    lines.append("> 触发：主人裁决候选③'都要'。方法=TradingAgents-astock lookahead_guard 同构视角的静态扫描。")
    lines.append("> 定性：静态扫描是'线索清单'，每条需人工复核语义后才能定性真缺陷/合法用法，本报告不直接下'有前视'结论。\n")
    lines.append(f"- 扫描文件数：{len(scanned)}（根目录核心管线 {len([f for f in files]) - len([f for f in files if os.path.dirname(f).endswith('portrait_agent')])} + portrait_agent {len([f for f in files if os.path.dirname(f).endswith('portrait_agent')])}）")
    lines.append(f"- 解析失败：{len(errors)}")
    lines.append(f"- C1 死日期参数命中：{len(c1)}")
    lines.append(f"- C2 now/today 调用点：{len(c2)}")
    lines.append(f"- C3 最新值模式行：{len(c3)}\n")

    lines.append("## C1 死日期参数（带日期签名但体内零引用）\n")
    if c1:
        for h in c1:
            lines.append(f"- `{h['file']}:{h['line']}` `{h['func']}()` 未使用参数 {h['unused_date_params']}")
    else:
        lines.append("（零命中）")
    lines.append("")

    lines.append("## C2 now/today 调用点（语义需人工归档：判今天 vs 当数据日期）\n")
    by_file = {}
    for h in c2:
        by_file.setdefault(h["file"], []).append(h)
    for f in sorted(by_file):
        lines.append(f"### {f}（{len(by_file[f])} 处）")
        for h in by_file[f]:
            lines.append(f"- L{h['line']} [{h['label']}] `{h['text']}`")
        lines.append("")

    lines.append("## C3 最新值模式（MAX(/iloc[-1]/tail(1)，按文件聚合计数+样例）\n")
    by_file3 = {}
    for h in c3:
        by_file3.setdefault(h["file"], []).append(h)
    for f in sorted(by_file3):
        items = by_file3[f]
        labels = {}
        for h in items:
            labels[h["label"]] = labels.get(h["label"], 0) + 1
        lines.append(f"### {f}（{len(items)} 行：{labels}）")
        for h in items[:5]:
            lines.append(f"- L{h['line']} [{h['label']}] `{h['text']}`")
        if len(items) > 5:
            lines.append(f"- …其余 {len(items)-5} 行见 JSON")
        lines.append("")

    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"mode={mode} scanned={len(scanned)} errors={len(errors)} c1={len(c1)} c2={len(c2)} c3={len(c3)}")
    print(f"md={out_md}")
    print(f"json={out_json}")


if __name__ == "__main__":
    main()
