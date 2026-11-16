#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prereg_preflight.py — 立项前置三查（2026-10-06 机制化，教训源头：候选丁撤回+相位仓位影子停用两次同类）

用途：任何新 prereg/新信号/新研究立项起草之前必跑——自动查三源冲突：
  1) 证伪库   falsified_rules.json        —— 该命题是否已被证伪（含同族）
  2) prereg 台账 data/PREREG_*.json       —— 是否已有在册/已停用/已激活的同族 prereg
  3) 记忆索引 MEMORY.md + reports 近期标题 —— 是否已有同主题研究/教训
输出: 结构化冲突报告(stdout JSON + 人读文本)；退出码 0=无冲突可立项 / 1=有冲突需人工复核 / 2=参数错

用法:
  python prereg_preflight.py --keywords "情绪,相位,择时" --title "相位仓位影子"
  python prereg_preflight.py --keywords "龙头,连板,追涨"

铁律: 本件只读；命中≠禁止立项，而是必须人工复核并在立项文档中引用命中条目论证条件差异。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
FALSIFIED = os.path.join(ROOT, "data", "falsified_rules.json")
FALSIFIED_ROOT = os.path.join(ROOT, "falsified_rules.json")
MEMORY = os.path.join(ROOT, "..", "MEMORY.md")
if not os.path.exists(MEMORY):
    MEMORY = r"C:\Users\Administrator\.zcode\cli\memories\projects\default-91b752de799bd5e3\memory\MEMORY.md"
REPORTS = os.path.join(ROOT, "reports")
PREREG_GLOB = os.path.join(ROOT, "data")


def scan_file(path, keywords, ctx=90):
    """全文关键词扫描, 返回 [(关键词, 片段)]"""
    hits = []
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return hits
    for kw in keywords:
        for m in re.finditer(re.escape(kw), text, re.IGNORECASE):
            s = max(0, m.start() - 40)
            frag = text[s:m.end() + ctx].replace("\n", " ")
            hits.append((kw, frag[:ctx + 60]))
    return hits


def main():
    ap = argparse.ArgumentParser(description="立项前置三查（证伪库/prereg台账/记忆库/近期报告）")
    ap.add_argument("--keywords", required=True, help="逗号分隔关键词, 如: 情绪,相位,择时")
    ap.add_argument("--title", default="", help="拟立项标题（用于 prereg 台账同名查重）")
    args = ap.parse_args()
    keywords = [k.strip() for k in re.split(r"[,，]", args.keywords) if k.strip()]
    if not keywords:
        print("[FAIL] 关键词为空", file=sys.stderr)
        return 2

    report = {"keywords": keywords, "title": args.title, "sources": {}, "conflict": False}

    # 1) 证伪库
    fp = FALSIFIED if os.path.exists(FALSIFIED) else (FALSIFIED_ROOT if os.path.exists(FALSIFIED_ROOT) else None)
    fals_hits = []
    if fp:
        fals_hits = scan_file(fp, keywords, ctx=110)
    report["falsified"] = {"path": fp or "未找到", "hits": [{"kw": k, "frag": f} for k, f in fals_hits[:10]],
                           "count": len(fals_hits)}
    if fals_hits:
        report["conflict"] = True

    # 2) prereg 台账（在册/停用/历史）
    preg_hits = []
    preg_dir = os.path.join(ROOT, "data")
    if os.path.isdir(preg_dir):
        for fn in os.listdir(preg_dir):
            if fn.startswith("PREREG_") and fn.endswith(".json"):
                p = os.path.join(preg_dir, fn)
                try:
                    d = json.load(open(p, encoding="utf-8"))
                except Exception:
                    continue
                blob = json.dumps(d, ensure_ascii=False)
                hit_kws = [kw for kw in keywords if kw.lower() in blob.lower()]
                name = d.get("name") or fn
                if hit_kws or (args.title and any(w.lower() in blob.lower() for w in args.title.split() if len(w) >= 2)):
                    status = d.get("status", "?")
                    preg_hits.append({"file": fn, "name": str(name), "status": status, "hit_kws": hit_kws})
    report["prereg_ledger"] = {"count": len(preg_hits), "hits": preg_hits}
    active = [h for h in preg_hits if "SUSPEND" not in str(h.get("status", "")).upper()
              and "FALSIFIED" not in str(h.get("status", "")).upper()
              and "WITHDRAW" not in str(h.get("status", "")).upper()]
    if preg_hits:
        report["conflict"] = True  # 有任何同族历史(含停用)都要求人工复核引用

    # 3) 记忆索引
    mem_hits = []
    if os.path.exists(MEMORY):
        mem_hits = scan_file(MEMORY, keywords, ctx=100)
    report["memory"] = {"path": MEMORY, "count": len(mem_hits),
                        "hits": [{"kw": k, "frag": f} for k, f in mem_hits[:8]]}

    # 4) 近30天 reports 标题扫描
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    recent = []
    if os.path.isdir(REPORTS):
        for fn in sorted(os.listdir(REPORTS), reverse=True):
            m = re.search(r"(20\d{6})", fn)
            if not m or m.group(1) < cutoff:
                continue
            if any(kw.lower() in fn.lower() for kw in keywords):
                recent.append(fn)
    report["recent_reports"] = recent[:10]

    # 判定
    hard = len(fals_hits)
    verdict = "CONFLICT" if (hard or preg_hits) else ("REVIEW" if mem_hits or recent else "CLEAR")
    report["verdict"] = verdict
    print(json.dumps({k: report[k] for k in ("keywords", "verdict", "conflict",
                                             "falsified", "prereg_ledger")}, ensure_ascii=False, indent=1)[:1800])
    print(f"\n[判定] {verdict} | 证伪命中{len(fals_hits)} prereg历史{len(preg_hits)} 记忆命中{len(mem_hits)} 近期报告{len(recent)}")
    print("""[规则] CONFLICT=证伪库/prereg直接命中→立项文档必须引用命中条目论证条件差异+主人裁决;
REVIEW=记忆/近期报告命中→人工复核;CLEAR=三源干净可立项。命中≠禁止, 未引用即立项=violation。""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
