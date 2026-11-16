# -*- coding: utf-8 -*-
"""证伪结论全量复核 · 阶段0 普查器

只读输入: data/falsified_rules.json (派生索引层, 本轮不改写), PROJECT_BOUNDARY.md
产出:
  1. data/falsified_reaudit_ledger.jsonl — 107 rules + 6 retired_strategies 逐条台账骨架
     (证据存活/风险旗标/嫌疑度/影响面/分层, audit_status=pending)
  2. reports/reaudit_stage0_census_20261020.md — 普查报告(脚本直出)

分层规则(方案 §5):
  硬证伪族(falsified*/rejected_falsified/forsaken/rejected_retired) -> A/B/C/D
  其余族(fail_route_open/observation/no_status/other) -> REG(登记+口径复核, 不重算)
嫌疑度: evidence_missing 或 no_quant 或 (pre_sep09 且 single_source_like) -> high;
        任一旗标 -> med; 无旗标 -> low
影响面: PROJECT_BOUNDARY §3 点名该 id 或含闸门/执行/接线语义 -> H; 含退役/残影/废止 -> L; 其余 M
fail-closed: 行数断言 + 逐行回读解析 + 全局文件索引上限
"""
import json
import os
import re
import hashlib
from collections import Counter

ROOT = r"D:/zcodesj"
SRC = os.path.join(ROOT, "data", "falsified_rules.json")
BND = os.path.join(ROOT, "PROJECT_BOUNDARY.md")
OUT = os.path.join(ROOT, "data", "falsified_reaudit_ledger.jsonl")
RPT = os.path.join(ROOT, "reports", "reaudit_stage0_census_20261020.md")

SKIP_DIRS = {".git", "__pycache__", ".zcode", "qlib_bin", "node_modules"}
FILE_CAP = 300000
PATH_EXTS = (".py", ".md", ".json", ".jsonl", ".txt", ".csv", ".db")
SEP_DATE = "2026-09-01"  # 互证纪律成型前


def build_file_index():
    idx = {}
    n = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            idx.setdefault(f, []).append(os.path.join(dirpath, f))
            n += 1
            if n > FILE_CAP:
                raise SystemExit("FAIL-CLOSED: file index cap exceeded %d" % FILE_CAP)
    return idx


def classify_ref(raw, idx):
    """对一条 evidence 引用判定存活状态。返回 (kind, status, resolved_or_note)。"""
    s = raw.strip().replace("\\", "/")
    s = re.sub(r"^D:/zcodesj/", "", s)
    if not re.search(r"\.(" + "|".join(e.strip(".") for e in PATH_EXTS) + r")($|/)", s, re.I):
        return ("prose", "n/a", "")
    # 支持一个引用串内多个脚本(如 'a.py/b.py/c.py')
    tokens = [t for t in re.split(r"[/\\]", s) if t.lower().endswith(PATH_EXTS)]
    results = []
    for tok in tokens:
        full = os.path.join(ROOT, tok) if "/" in tok or "\\" in tok else None
        if full and os.path.exists(full):
            results.append((tok, "ok", tok))
            continue
        hits = idx.get(tok, [])
        if len(hits) == 1:
            rel = os.path.relpath(hits[0], ROOT).replace("\\", "/")
            results.append((tok, "relinked", rel))
        elif len(hits) > 1:
            rels = [os.path.relpath(h, ROOT).replace("\\", "/") for h in hits[:3]]
            results.append((tok, "relink_ambiguous", ";".join(rels)))
        else:
            results.append((tok, "missing", ""))
    if not results:
        return ("path", "missing", "")
    st = set(r[1] for r in results)
    if "missing" in st:
        bad = [r[0] for r in results if r[1] == "missing"]
        return ("path", "partial_missing", ",".join(bad))
    if "relink_ambiguous" in st:
        amb = [r[2] for r in results if r[1] == "relink_ambiguous"]
        return ("path", "relink_ambiguous", ";".join(amb))
    if "relinked" in st:
        return ("path", "relinked", ";".join(r[2] for r in results if r[1] == "relinked"))
    return ("path", "ok", "")


def extract_date(entry):
    for k in ("date", "verdict_date", "registered"):
        v = str(entry.get(k, "")).strip()
        m = re.search(r"20\d{2}-\d{2}-\d{2}", v)
        if m:
            return m.group(0)
    blob = json.dumps(entry, ensure_ascii=False)
    m = re.search(r"20\d{2}-\d{2}-\d{2}", blob)
    return m.group(0) if m else None


def subject_of(entry):
    for k in ("rule", "hypothesis", "summary", "reason", "verdict", "title"):
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


HARD_PREFIX = ("falsified", "rejected_falsified", "forsaken", "rejected_retired")


def family_of(status):
    s = status.strip()
    if s.startswith(HARD_PREFIX):
        return "hard_falsified"
    if s.startswith("observation"):
        return "observation"
    if s.startswith("fail"):
        return "fail_route_open"
    if s == "?":
        return "no_status"
    return "other"


def main():
    src_bytes = open(SRC, "rb").read()
    src_md5 = hashlib.md5(src_bytes).hexdigest()
    data = json.loads(src_bytes.decode("utf-8"))
    rules = data["rules"]
    retired = data.get("retired_strategies", [])
    print("source md5=%s rules=%d retired=%d" % (src_md5, len(rules), len(retired)))

    txt = open(BND, encoding="utf-8").read()
    m = re.search(r"## 3\..*?(?=\n## )", txt, re.S)
    sec3 = m.group(0) if m else ""
    print("boundary sec3 chars=%d" % len(sec3))

    idx = build_file_index()
    print("file index entries=%d" % len(idx))

    rows = []

    def process(rid, entry, kind):
        blob = json.dumps(entry, ensure_ascii=False)
        status = str(entry.get("status", "?"))
        fam = "retired_strategies" if kind == "retired" else family_of(status)
        date = extract_date(entry)
        ev_out = []
        ev_missing = False
        n_path_ok = 0
        for raw in entry.get("evidence", []) or []:
            if not isinstance(raw, str):
                continue
            k, st, res = classify_ref(raw, idx)
            ev_out.append({"raw": raw, "kind": k, "status": st, "resolved": res})
            if st in ("missing", "partial_missing"):
                ev_missing = True
            if st == "ok":
                n_path_ok += 1
        has_quant = bool(re.search(r"-?[0-9]+[.][0-9]+%|p=0[.][0-9]+|n=[0-9]{2,}", blob))
        has_ci = bool(re.search(r"CI|置信|bootstrap|t=[≥0-9]|t≥", blob, re.I))
        has_2nd = "cross_check_2nd_impl" in entry
        flags = []
        if date and date < SEP_DATE:
            flags.append("pre_sep09")
        if not re.search(r"互证", blob) and n_path_ok <= 2:
            flags.append("single_source_like")
        if not has_quant:
            flags.append("no_quant")
        if not has_ci:
            flags.append("no_ci")
        if ev_missing:
            flags.append("evidence_missing")
        if flags.count("evidence_missing") or not has_quant or (
            "pre_sep09" in flags and "single_source_like" in flags
        ):
            susp = "high"
        elif flags:
            susp = "med"
        else:
            susp = "low"
        if re.search(re.escape(rid), sec3):
            impact = "H"
        elif re.search(r"闸|gate|executor|接线|买入|卖出|止损|信号源", blob):
            impact = "H"
        elif re.search(r"退役|残影|废弃|废止|过时", blob):
            impact = "L"
        else:
            impact = "M"
        if fam == "hard_falsified":
            if impact == "H" and susp == "high":
                layer = "A"
            elif ev_missing:
                layer = "D"
            elif impact == "L":
                layer = "C"
            else:
                layer = "B"
        else:
            layer = "REG"
        rows.append({
            "id": rid,
            "family": fam,
            "status_raw": status[:200],
            "date": date,
            "subject": subject_of(entry)[:160],
            "evidence": ev_out,
            "has_quant": has_quant,
            "has_ci": has_ci,
            "has_2nd_impl": has_2nd,
            "risk_flags": flags,
            "suspicion": susp,
            "impact": impact,
            "layer": layer,
            "audit_status": "pending",
            "reopen_criteria_draft": None,
            "rerun": None,
        })

    for rid in sorted(rules):
        process(rid, rules[rid], "rule")
    for i, s in enumerate(retired, 1):
        rid = str(s.get("name") or s.get("ts_code") or s.get("strategy") or "RETIRED-%d" % i) if isinstance(s, dict) else "RETIRED-%d" % i
        process(rid, s if isinstance(s, dict) else {"status": "retired", "evidence": [str(s)]}, "retired")

    # fail-closed 断言 + 逐行回读
    assert len(rows) == len(rules) + len(retired), "row count mismatch"
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate ids"
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    back = [json.loads(l) for l in open(OUT, encoding="utf-8")]
    assert len(back) == len(rows)
    for b in back:
        assert b["id"] and b["family"] and b["layer"] and b["audit_status"] == "pending"
    print("ledger written+readback OK rows=%d" % len(back))

    # ---- 报告(脚本直出) ----
    fam_c = Counter(r["family"] for r in rows)
    lay_c = Counter(r["layer"] for r in rows)
    ev_c = Counter(st for r in rows for e in r["evidence"] for st in [e["status"]])
    flag_c = Counter(f for r in rows for f in r["risk_flags"])
    hard = [r for r in rows if r["family"] == "hard_falsified"]
    a_list = sorted([r for r in hard if r["layer"] == "A"], key=lambda r: (r["date"] or "9999"))
    d_list = [r for r in hard if r["layer"] == "D"]

    def esc(x):
        return str(x).replace("|", "/")

    L = []
    L.append("# 证伪结论全量复核 · 阶段0 普查报告\n")
    L.append("> 生成: analysis/falsified_reaudit_census_20261020.py (脚本直出) | 源台账 md5=`%s`\n" % src_md5)
    L.append("> 分层规则/嫌疑度定义见 reports/falsified_conclusion_reaudit_plan_20261020.md §5; 嫌疑/影响为启发式初判, 批次1前人工复核\n")
    L.append("## 总览\n")
    L.append("- 台账行: %d (rules %d + retired %d) -> data/falsified_reaudit_ledger.jsonl\n" % (len(rows), len(rules), len(retired)))
    L.append("- 族分布: %s\n" % dict(fam_c))
    L.append("- 硬证伪族分层: %s\n" % {k: v for k, v in sorted(lay_c.items()) if k in "ABCD"})
    L.append("- 证据存活: %s\n" % dict(ev_c))
    L.append("- 风险旗标: %s\n" % dict(flag_c))
    L.append("## A层候选(影响H x 嫌疑高, 按日期升序)\n")
    L.append("| id | date | flags | subject |\n|---|---|---|---|\n")
    for r in a_list:
        L.append("| %s | %s | %s | %s |\n" % (r["id"], r["date"] or "?", ",".join(r["risk_flags"]), esc(r["subject"][:70])))
    if not a_list:
        L.append("(无)\n")
    L.append("\n## D层候选(证据失配, 待人工确认真失存)\n")
    L.append("| id | date | missing evidence |\n|---|---|---|\n")
    for r in d_list:
        miss = ";".join(e["resolved"] or e["raw"] for e in r["evidence"] if "missing" in e["status"])
        L.append("| %s | %s | %s |\n" % (r["id"], r["date"] or "?", esc(miss[:120])))
    if not d_list:
        L.append("(无)\n")
    L.append("\n## B层全表(硬证伪族, 重跑+断言对拍)\n")
    L.append("| id | date | susp | impact | flags |\n|---|---|---|---|---|\n")
    for r in sorted([r for r in hard if r["layer"] == "B"], key=lambda r: (r["date"] or "9999")):
        L.append("| %s | %s | %s | %s | %s |\n" % (r["id"], r["date"] or "?", r["suspicion"], r["impact"], ",".join(r["risk_flags"])))
    L.append("\n## C层全表(影响L, 口径复核不重算)\n")
    L.append("| id | date | reason |\n|---|---|---|\n")
    for r in sorted([r for r in hard if r["layer"] == "C"], key=lambda r: (r["date"] or "9999")):
        L.append("| %s | %s | %s |\n" % (r["id"], r["date"] or "?", esc(r["subject"][:80])))
    L.append("\n## REG族(登记+口径复核, 不重算)\n")
    L.append("| id | family | date | subject |\n|---|---|---|---|\n")
    for r in sorted([r for r in rows if r["layer"] == "REG"], key=lambda r: (r["family"], r["date"] or "9999")):
        L.append("| %s | %s | %s | %s |\n" % (r["id"], r["family"], r["date"] or "?", esc(r["subject"][:60])))
    L.append("\n## 批次1翻案判据模板(DRAFT, 待主人批准后逐条实例化)\n")
    L.append("```text\n")
    L.append("对每条A层条目, 翻案(overturned)须同时满足:\n")
    L.append("  1) 独立重实现(不复用原代码路径)在当前单一真源数据(qfq wb/PIT)上复现该命题检验;\n")
    L.append("  2) 原判据指标方向反转, 且 95% CI(块bootstrap)不含0;\n")
    L.append("  3) 分年稳健: 多数年份方向一致, 非单一regime产物;\n")
    L.append("  4) 差异可归因到 M1-M12 某一类(归因不清不判翻案, 判 not_reproducible 或 verified-with-caveat)。\n")
    L.append("输出: verified / overturned / not_reproducible 三分法, 逐条写入 reaudit 台账。\n")
    L.append("```\n")
    with open(RPT, "w", encoding="utf-8") as f:
        f.writelines(L)
    print("report written: %s" % RPT)
    print("A=%d B=%d C=%d D=%d REG=%d" % (
        lay_c["A"], lay_c["B"], lay_c["C"], lay_c["D"], lay_c["REG"]))


if __name__ == "__main__":
    main()
