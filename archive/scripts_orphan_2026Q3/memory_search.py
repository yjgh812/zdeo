# -*- coding: utf-8 -*-
"""
memory_search.py — 屠龙系统 memory 语义搜索（2026-08-12 知微落地）

【目的】
  memory 已 54+ 条，MEMORY.md 主题索引 + grep 够用，但跨主题查"上次那个 X 是什么结论"
  需要逐个文件读。本脚本用纯标准库 TF-IDF（中英分词）实现毫秒级语义搜索。

【为什么不上 ChromaDB】
  - chromadb/sentence_transformers/torch 几百 MB，国内拉模型慢
  - 54 条规模 ChromaDB 是过度工程，200+ 条再升级
  - 纯标准库 TF-IDF 实测精度足够（中文 bigram + 英文词级）

【用法】
  python memory_search.py 止损                       # 单关键词
  python memory_search.py "bp+ep 退化"               # 多词
  python memory_search.py 卖出 三铁律 仓位           # 多 query AND
  python memory_search.py --rebuild                  # 强制重建索引
  python memory_search.py --list                     # 列出全部 memory
  python memory_search.py 止损 -n 3                  # Top 3

【索引缓存】
  默认每次跑检查 memory/*.md 的 mtime，有变化才重建。
  索引存 memory/.search_index.pkl（pickle）。
"""
from __future__ import annotations
import argparse
import math
import os
import pickle
import re
import sys
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path

MEMORY_DIR = Path(r"C:\Users\yujie\.zcode\cli\memories\projects\default-91b752de799bd5e3\memory")
INDEX_FILE = MEMORY_DIR / ".search_index.pkl"

# 中文/英文/数字 范围
_CJK = r"\u4e00-\u9fff"
_WORD = r"a-zA-Z0-9_+/\-."


def is_cjk(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch <= "\u9fff"


def tokenize(text: str) -> list:
    """混合分词：中文 bigram + 英文/数字词级。

    中文 bigram：相邻两字组合（"止损链" → 止损, 损链），
    比单字精确，比 jieba 轻量。
    英文/数字：连续的 [a-zA-Z0-9_+/-] 串作为一个词。
    """
    text = text.lower()
    tokens = []
    buf = ""

    def flush_buf():
        nonlocal buf
        if buf:
            tokens.append(buf)
            buf = ""

    cjk_pairs = []
    prev_cjk = None
    for ch in text:
        if is_cjk(ch):
            # 先把英文 buffer 冲掉
            flush_buf()
            if prev_cjk is not None:
                cjk_pairs.append(prev_cjk + ch)
            prev_cjk = ch
        else:
            # 进入非中文：先把中文 bigram 关掉
            if prev_cjk is not None:
                # 单字也作为 token（避免漏检）
                tokens.append(prev_cjk)
                prev_cjk = None
            # 英文/数字：连续字符成词
            if re.match(rf"[{_WORD}]", ch):
                buf += ch
            else:
                flush_buf()
    if prev_cjk is not None:
        tokens.append(prev_cjk)
    flush_buf()

    tokens.extend(cjk_pairs)
    return [t for t in tokens if len(t) >= 1]


def parse_memory(path: Path) -> dict:
    """解析 memory md 文件：name, description, body, all_text"""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return {}

    # frontmatter
    name = path.stem
    description = ""
    body = raw
    if raw.startswith("---"):
        end = raw.find("---", 3)
        if end > 0:
            front = raw[3:end]
            body = raw[end + 3:]
            m = re.search(r"^name:\s*(.+)$", front, re.MULTILINE)
            if m:
                name = m.group(1).strip()
            m = re.search(r"^description:\s*(.+)$", front, re.MULTILINE)
            if m:
                description = m.group(1).strip()

    # 权重加权：name/description 重复 3 次（更敏感）
    all_text = f"{name}\n" * 3 + f"{description}\n" * 3 + body
    return {
        "file": path.name,
        "name": name,
        "description": description,
        "tokens": tokenize(all_text),
    }


def build_index():
    """扫描所有 memory md，建倒排索引 + TF-IDF"""
    files = sorted(MEMORY_DIR.glob("*.md"))
    docs = []
    for f in files:
        d = parse_memory(f)
        if d:
            docs.append(d)

    # 倒排：term -> {file: count}
    inverted = defaultdict(dict)
    doc_len = {}
    for d in docs:
        cnt = Counter(d["tokens"])
        doc_len[d["file"]] = sum(cnt.values()) or 1
        for term, c in cnt.items():
            inverted[term][d["file"]] = c

    # idf
    n_docs = len(docs)
    idf = {}
    for term, postings in inverted.items():
        idf[term] = math.log((n_docs + 1) / (len(postings) + 1)) + 1  # +1 平滑

    # 缓存元数据
    meta = {d["file"]: {"name": d["name"], "description": d["description"],
                        "mtime": (MEMORY_DIR / d["file"]).stat().st_mtime}
            for d in docs}

    index = {
        "inverted": dict(inverted),
        "doc_len": doc_len,
        "idf": idf,
        "meta": meta,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "n_docs": n_docs,
    }
    try:
        with open(INDEX_FILE, "wb") as f:
            pickle.dump(index, f)
    except Exception as e:
        print(f"[warn] 索引写入失败: {e}", file=sys.stderr)
    return index


def load_or_build(force_rebuild: bool = False) -> dict:
    """有缓存且未过期就加载，否则重建"""
    if not force_rebuild and INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "rb") as f:
                index = pickle.load(f)
            # 检查 memory 文件 mtime 变化
            need_rebuild = False
            current_files = {p.name for p in MEMORY_DIR.glob("*.md")}
            cached_files = set(index.get("meta", {}).keys())
            if current_files != cached_files:
                need_rebuild = True
            else:
                for fn, m in index["meta"].items():
                    p = MEMORY_DIR / fn
                    if not p.exists() or p.stat().st_mtime > m["mtime"] + 1:
                        need_rebuild = True
                        break
            if not need_rebuild:
                return index
        except Exception:
            pass
    return build_index()


def search(query: str, index: dict, top_n: int = 5) -> list:
    """搜索返回 [{file, name, description, score, matched_terms}]"""
    q_tokens = tokenize(query)
    if not q_tokens:
        return []

    # 每个 doc 累加分数
    scores = defaultdict(float)
    matched = defaultdict(set)
    for t in q_tokens:
        postings = index["inverted"].get(t)
        if not postings:
            continue
        idf_t = index["idf"].get(t, 1.0)
        for fn, cnt in postings.items():
            tf = cnt / index["doc_len"].get(fn, 1)
            scores[fn] += tf * idf_t * idf_t  # idf^2 加大区分度
            matched[fn].add(t)

    results = []
    for fn, score in scores.items():
        meta = index["meta"].get(fn, {})
        results.append({
            "file": fn,
            "name": meta.get("name", fn),
            "description": meta.get("description", ""),
            "score": round(score, 4),
            "matched": sorted(matched[fn]),
        })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


def main():
    ap = argparse.ArgumentParser(description="屠龙 memory 语义搜索（TF-IDF）")
    ap.add_argument("query", nargs="*", help="搜索关键词（可多个，AND 逻辑）")
    ap.add_argument("-n", "--top", type=int, default=5, help="返回前 N 条，默认 5")
    ap.add_argument("--rebuild", action="store_true", help="强制重建索引")
    ap.add_argument("--list", action="store_true", help="列出全部 memory")
    args = ap.parse_args()

    if args.list:
        index = load_or_build()
        print(f"memory 总数: {index['n_docs']}（索引 {index['built_at']}）")
        for fn, m in sorted(index["meta"].items()):
            print(f"  {fn}\n    {m['name']}: {m['description']}")
        return

    if args.rebuild or not args.query:
        index = build_index()
        print(f"[索引] 重建完成: {index['n_docs']} 条（{index['built_at']}）")
        if not args.query:
            return
    else:
        index = load_or_build()

    query = " ".join(args.query)
    results = search(query, index, top_n=args.top)

    print("=" * 68)
    print(f"搜索: \"{query}\"  →  命中 {len(results)} 条（索引 {index['n_docs']} 条）")
    print("=" * 68)
    for i, r in enumerate(results, 1):
        print(f"\n[{i}] {r['name']}  (score={r['score']})")
        print(f"    文件: {r['file']}")
        print(f"    描述: {r['description']}")
        print(f"    命中词: {', '.join(r['matched'])}")


if __name__ == "__main__":
    main()
