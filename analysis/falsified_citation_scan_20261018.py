# -*- coding: utf-8 -*-
"""falsified_citation_scan_20261018.py — 证伪库引用率只读扫描 (2026-10-18, DeepSeek外部审计吸收项V8)

盲区(DeepSeek F-1): 证伪库一周 99→107 条, 但"证伪引用率"从未统计——107 条里多少立完再没人引用?
防复活资产有退化为证伪业绩簿风险。
本件=只读扫描(phase 1, 不改 falsified_rules.json 合同——last_cited_at 字段回写归主人裁决):
  1) 取 107 规则 ID + 退役策略键作引用 token(带词边界, 防 E2 吞 E20);
  2) 活跃树全量文本扫描(root/analysis/reports/attribution/portrait_agent/data/multifactor/tests, 排除archive/venv);
  3) 引用日期归因: git 最后一次触该文件的提交日(tracked) / mtime(未跟踪如 reports/);
  4) 分桶: ≤30天新引用 / 31-90天陈化 / >90天冷置 / 活跃树零引用。
已知局限(如实): token 近似=按 ID 引用惯例(S-5/E2/#49式), 以名称代称的引用不计; SOP 单体文件
日期取 git 口径(不按行位置断代)。季度复跑: 重跑本件即可, 输出幂等。
输出: analysis/falsified_citation_scan_20261018_out.md + 同名 .json (analysis/ 内, 非 data/ 合同)
"""
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"D:\zcodesj"
RULES = os.path.join(ROOT, "data", "falsified_rules.json")
OUT_MD = os.path.join(ROOT, "analysis", "falsified_citation_scan_20261018_out.md")
OUT_JSON = os.path.join(ROOT, "analysis", "falsified_citation_scan_20261018.json")
SELF = os.path.abspath(__file__)
EXCLUDE_DIRS = {"archive", ".git", "backups", "qlib_bin", ".zcode", "__pycache__", "node_modules", "venv"}
EXTS = {".py", ".md", ".json", ".txt"}
EXCLUDE_FILES = {"data/falsified_rules.json",
                 "analysis/falsified_citation_scan_20261018.py",
                 "analysis/falsified_citation_scan_20261018_out.md",
                 "analysis/falsified_citation_scan_20261018.json"}
TZ8 = timezone(timedelta(hours=8))


def main():
    d = json.load(open(RULES, encoding="utf-8"))
    rules = d["rules"]
    retired = d.get("retired_strategies", {})
    ids = list(rules.keys())

    # token 编译: 单遍 alternation, 组号→ID
    pats, gmap = [], {}
    for i, rid in enumerate(ids):
        esc = re.escape(rid)
        pats.append(f"(?P<g{i}>(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9]))")
        gmap[f"g{i}"] = rid
    big = re.compile("|".join(pats))

    # 文件日期表: 一遍 git log 建 path→最后提交日 (新→旧, 首见即最新)
    t0 = time.time()
    file_date = {}
    try:
        out = subprocess.run(["git", "log", "--format=%cI", "--name-only", "--"],
                             cwd=ROOT, capture_output=True, timeout=300).stdout.decode("utf-8", "replace")
        dateline = re.compile(r"^\d{4}-\d{2}-\d{2}T")
        cur_date = None
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue  # git log 布局=日期/空行/文件群——空行不重置当前日期
            if dateline.match(line):
                cur_date = line[:10]
            elif cur_date and line not in file_date:
                file_date[line] = cur_date
        print(f"[git log 建日期表 {len(file_date)} 文件 {time.time()-t0:.0f}s]")
    except Exception as e:
        print(f"[WARN] git log 失败({e}), 全部退化 mtime 口径")

    # 扫描
    hits = defaultdict(list)
    scanned = 0
    t1 = time.time()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [x for x in dirnames if x not in EXCLUDE_DIRS and not x.startswith(".venv")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in EXTS:
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            if rel in EXCLUDE_FILES or os.path.abspath(path) == SELF:
                continue
            try:
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    continue  # >2MB 跳过(SOP 790KB 在内, 超大行文件另有索引)
                text = open(path, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            scanned += 1
            found = set()
            for m in big.finditer(text):
                found.add(gmap[m.lastgroup])
            if found:
                dt = file_date.get(rel)
                if not dt:
                    dt = datetime.fromtimestamp(os.path.getmtime(path), TZ8).date().isoformat()
                for rid in found:
                    hits[rid].append((rel, dt))
    print(f"[扫描 {scanned} 文件 {time.time()-t1:.0f}s, 命中ID {len(hits)}/{len(ids)}]")

    # 分桶
    today = datetime.now(TZ8).date()
    def bucket_of(dt):
        if not dt:
            return "zero"
        dd = (today - datetime.strptime(dt[:10], "%Y-%m-%d").date()).days
        return "fresh30" if dd <= 30 else ("stale90" if dd <= 90 else "cold90p")

    rows = []
    for rid in ids:
        hs = hits.get(rid, [])
        last_dt = max((h[1] for h in hs), default=None)
        rows.append({"id": rid, "n_files": len(hs), "last_cited": last_dt,
                     "bucket": bucket_of(last_dt), "reason": str(rules[rid].get("reason", ""))[:70]})
    retired_rows = []
    for rid in retired:
        hs = hits.get(rid, [])
        last_dt = max((h[1] for h in hs), default=None)
        retired_rows.append({"id": rid, "n_files": len(hs), "last_cited": last_dt, "bucket": bucket_of(last_dt)})

    by_bucket = defaultdict(list)
    for r in rows:
        by_bucket[r["bucket"]].append(r)
    order = ["fresh30", "stale90", "cold90p", "zero"]

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"generated": today.isoformat(), "rules_total": len(ids),
                   "rows": rows, "retired_rows": retired_rows}, f, ensure_ascii=False, indent=1)

    md = ["# 证伪库引用率扫描(V8, DeepSeek外部审计吸收项 F-1)", "",
          f"> 脚本 analysis/falsified_citation_scan_20261018.py 只读直出 | 规则 {len(ids)} 条 + 退役策略 {len(retired)} 条 | 扫描 {scanned} 文件",
          "> 口径: token=规则ID(词边界)/引用日期=git最后触文件日(tracked)或mtime(未跟踪)/已知局限见文件头", "",
          "## 分桶汇总", ""]
    label = {"fresh30": "≤30天有引用", "stale90": "31-90天陈化", "cold90p": ">90天冷置", "zero": "活跃树零引用"}
    for b in order:
        md.append(f"- **{label[b]}**: {len(by_bucket.get(b, []))} 条")
    zero = by_bucket.get("zero", [])
    md += ["", "## 零引用清单(防复活资产退化预警——『没人记得=容易被复活』)", ""]
    if zero:
        md += ["| ID | reason截取 |", "|---|---|"]
        for r in sorted(zero, key=lambda x: x["id"]):
            md.append(f"| {r['id']} | {r['reason']} |")
    else:
        md.append("(无)")
    md += ["", "## >90天冷置清单", "", "| ID | 最后引用 | n文件 | reason截取 |", "|---|---|---|---|"]
    for r in sorted(by_bucket.get("cold90p", []), key=lambda x: x["id"]):
        md.append(f"| {r['id']} | {r['last_cited']} | {r['n_files']} | {r['reason']} |")
    md += ["", "## 高引用TOP15(活得好好的防复活资产, 对照用)", "", "| ID | n文件 | 最后引用 |", "|---|---|---|"]
    for r in sorted(rows, key=lambda x: -x["n_files"])[:15]:
        md.append(f"| {r['id']} | {r['n_files']} | {r['last_cited']} |")
    md += ["", "## 结论边界", "",
           "- 本件只读, 不回写 last_cited_at 字段(合同变更归主人裁决); 若批准回填, 本 json 即回填源;",
           "- token 近似: 以名称代称(不写ID)的引用不计 → 零引用清单是『疑似』而非定谳, 处置前人工复核;",
           "- 退役策略引用率单列 json(retired_rows), 未入分桶主表。"]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"[OK] 分桶: " + " / ".join(f"{label[b]}={len(by_bucket.get(b, []))}" for b in order))
    print(f"[OK] 已直出 {OUT_MD}")


if __name__ == "__main__":
    sys.exit(main())
