# -*- coding: utf-8 -*-
"""
import_tsp_ext_data.py — TSP 扩展数据入驻屠龙主库
====================================================
读取 TSP 容器拉取的同花顺概念/行业 Parquet 快照,
写入 FUND_DB concept_map / industry_map_ths 表,
使屠龙各工具可直接用 SQL 查询概念映射，不再依赖 TSP parquet 文件路径。

用法:
  python import_tsp_ext_data.py                    # 全量导入(幂等 REPLACE)
  python import_tsp_ext_data.py --only concept     # 只导概念
  python import_tsp_ext_data.py --only industry    # 只导行业

数据源(需 TSP 容器启动过至少一次, 之后持久化):
  D:/zcodesj/tickflow-stock-panel/data/ext_data/ext_gn_ths/part.parquet
  D:/zcodesj/tickflow-stock-panel/data/ext_data/ext_hy_ths/part.parquet

输出:
  FUND_DB
    - concept_map(code, name, concept, src)   — code=6位码, 概念名, 一股多行
    - industry_map_ths(code, name, industry, src) — 同花顺行业, 一股一行
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

FUND_DB = Path(r"D:\workbuddyspace\databases\fundamental_data.db")
TSP_CONCEPT_PARQUET = Path(
    r"D:\zcodesj\tickflow-stock-panel\data\ext_data\ext_gn_ths\part.parquet"
)
TSP_INDUSTRY_PARQUET = Path(
    r"D:\zcodesj\tickflow-stock-panel\data\ext_data\ext_hy_ths\part.parquet"
)

CREATE_CONCEPT = """
CREATE TABLE IF NOT EXISTS concept_map (
    code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    concept TEXT NOT NULL,
    src TEXT NOT NULL DEFAULT 'ths',
    PRIMARY KEY (code, concept)
)
"""
CREATE_INDUSTRY = """
CREATE TABLE IF NOT EXISTS industry_map_ths (
    code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    industry TEXT NOT NULL,
    src TEXT NOT NULL DEFAULT 'ths',
    PRIMARY KEY (code, industry)
)
"""


def _norm(code: str) -> str:
    """后缀/sz/sh/SH 等 → 6位纯码"""
    s = str(code).strip()
    if s.endswith((".SZ", ".SH", ".BJ")):
        s = s[:-3]
    if s.startswith(("SH", "SZ", "BJ")) and len(s) == 8:
        s = s[2:]
    return s.zfill(6)


def import_concept() -> int:
    if not TSP_CONCEPT_PARQUET.exists():
        print(f"[concept] 源文件不存在: {TSP_CONCEPT_PARQUET} (TSP 需至少启动一次)")
        return 0
    src = pd.read_parquet(TSP_CONCEPT_PARQUET)
    col = "所属概念" if "所属概念" in src.columns else src.columns[2]
    rows = []
    for symbol, name, concepts in zip(
        src["股票代码"], src["股票简称"], src[col]
    ):
        code = _norm(symbol)
        for c in str(concepts or "").split(";"):
            c = c.strip()
            if c:
                rows.append((code, str(name or ""), c, "ths"))
    con = sqlite3.connect(str(FUND_DB), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(CREATE_CONCEPT)
    con.executemany(
        "INSERT OR REPLACE INTO concept_map VALUES (?,?,?,?)", rows
    )
    con.commit()
    x = len(rows)
    n = con.execute("SELECT COUNT(DISTINCT code) FROM concept_map").fetchone()[0]
    m = con.execute("SELECT COUNT(DISTINCT concept) FROM concept_map").fetchone()[0]
    con.close()
    print(f"[concept] 导入 {x} 行 ({n} 只股票, {m} 个概念) → fundamental_data.db concept_map")
    return x


def import_industry() -> int:
    if not TSP_INDUSTRY_PARQUET.exists():
        print(f"[industry] 源文件不存在: {TSP_INDUSTRY_PARQUET}")
        return 0
    src = pd.read_parquet(TSP_INDUSTRY_PARQUET)
    col = "所属同花顺行业" if "所属同花顺行业" in src.columns else src.columns[2]
    rows = []
    for symbol, name, inds in zip(
        src["股票代码"], src["股票简称"], src[col]
    ):
        code = _norm(symbol)
        for i in str(inds or "").split(";"):
            i = i.strip()
            if i:
                rows.append((code, str(name or ""), i, "ths"))
    con = sqlite3.connect(str(FUND_DB), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(CREATE_INDUSTRY)
    con.executemany(
        "INSERT OR REPLACE INTO industry_map_ths VALUES (?,?,?,?)", rows
    )
    con.commit()
    x = len(rows)
    n = con.execute("SELECT COUNT(DISTINCT code) FROM industry_map_ths").fetchone()[0]
    m = con.execute("SELECT COUNT(DISTINCT industry) FROM industry_map_ths").fetchone()[0]
    con.close()
    print(f"[industry] 导入 {x} 行 ({n} 只股票, {m} 个行业) → fundamental_data.db industry_map_ths")
    return x


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="TSP 扩展数据导入屠龙主库")
    ap.add_argument("--only", choices=["concept", "industry"], default=None)
    a = ap.parse_args()
    if a.only in (None, "concept"):
        import_concept()
    if a.only in (None, "industry"):
        import_industry()
    print("全部完成 ✅")