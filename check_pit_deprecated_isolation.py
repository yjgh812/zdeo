# -*- coding: utf-8 -*-
"""check_pit_deprecated_isolation.py — PIT 弃用隔离 CI 门 (2026-10-18, DeepSeek外部审计吸收项V6)

背景: PIT v1 实证 98.7% 行前视, 已改名 stock_factors_fundamental_pit_v1_deprecated 隔离,
     生产唯一真源 = stock_factors_fundamental_pit_v2 (factor_pit.db)。
     宪法§7 SQLite 块: 财务因子禁止 factor_daily (稀疏致静默失效)。
盲区(DeepSeek B-3): 改名隔离≠物理隔离, 无防回归闸——任何新脚本硬编码旧表名仍可静默读到。
本门=代码层防回归: 扫描 D:\\zcodesj 活跃树 *.py, 命中两类禁用模式即违规:
  P1: fundamental_pit_v1        (v1 弃用表任何引用, 含 v1_deprecated)
  P2: factor_daily 独立词       (禁用稀疏表; 排除 factor_daily_ic 等 IC 研究表)
豁免白名单=本文件内 WHITELIST (路径子串→理由), 空为常态; archive/ 隔离区不扫描。
出口码: 0=过 1=违规 2=配置错误。接 health_check_daily (违规 ERROR 级, 配置异常 WARN)。
范围声明: 只扫本仓 (D:\\zcodesj); workbuddyspace 归其域巡检, 不在本门越界。

用法: python check_pit_deprecated_isolation.py [--root D:\\zcodesj]
"""
import argparse
import os
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_ROOT = r"D:\zcodesj"
EXCLUDE_DIRS = {"archive", ".git", "backups", "qlib_bin", ".zcode", "__pycache__", "node_modules"}
PIT_DB = os.path.join(DEFAULT_ROOT, "factor_pit.db")

# (正则, 说明)—— P1 裸词(墓碑名任何引用); P2 收窄到 SQL 语境(引号包名/FROM/JOIN),
# 排除 factor_daily_ic 等 IC 研究表与注释性 bare 提及
FORBIDDEN = [
    (re.compile(r"fundamental_pit_v1"), "P1 v1弃用表引用(改名隔离≠可引用)"),
    (re.compile(r"(?:[\"']factor_daily[\"']|(?:FROM|JOIN)\s+factor_daily(?![A-Za-z0-9_]))", re.IGNORECASE),
     "P2 禁用稀疏表factor_daily SQL引用(宪法§7)"),
]

# 路径子串 → 豁免理由 (注释性提及/迁移说明等不构成读取路径的场合; 空为常态)
WHITELIST = {
    # "factor_pit_recheck.py": "docstring 迁移史注记, 非 SQL 引用"  # 示例格式
}


def tombstone_check():
    """墓碑自检: v2 真源与 v1_deprecated 墓碑都必须在库(墓碑被删=隔离退化)"""
    try:
        con = sqlite3.connect("file:" + PIT_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=15)
        tables = {t for (t,) in con.execute("select name from sqlite_master where type='table'")}
        con.close()
        missing = []
        if "stock_factors_fundamental_pit_v2" not in tables:
            missing.append("stock_factors_fundamental_pit_v2(生产真源缺失)")
        if "stock_factors_fundamental_pit_v1_deprecated" not in tables:
            missing.append("stock_factors_fundamental_pit_v1_deprecated(墓碑缺失=隔离退化)")
        return missing
    except Exception as e:
        return [f"factor_pit.db 打开失败: {e}"]


def scan(root):
    violations = []
    scanned = 0
    self_path = os.path.abspath(__file__)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS
                       and not d.startswith(".venv") and d != "venv"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            if os.path.abspath(path) == self_path:
                continue  # 门自身定义含禁用名字面量, 自排除(非消费方)
            rel = os.path.relpath(path, root)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        for pat, desc in FORBIDDEN:
                            if pat.search(line):
                                violations.append((rel, i, desc, line.strip()[:160]))
            except Exception as e:
                print(f"[WARN] 读取失败 {rel}: {e}")
            scanned += 1
    return scanned, violations


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    args = ap.parse_args()
    if not os.path.isdir(args.root):
        print("[FAIL] root 不存在: " + args.root)
        return 2

    # 墓碑自检(缺失=配置退化 → rc2 WARN 级, 不冒充违规)
    missing = tombstone_check()

    scanned, violations = scan(args.root)
    # 白名单豁免
    kept = [v for v in violations
            if not any(w in v[0].replace("\\", "/") for w in WHITELIST)]
    exempted = len(violations) - len(kept)

    for rel, ln, desc, text in kept:
        print(f"[FAIL] {rel}:{ln} {desc} | {text}")

    print(f"[SUMMARY] 扫描 {scanned} 个py | 禁用模式命中 {len(kept)} (白名单豁免 {exempted}) "
          f"| 墓碑自检: {'PASS' if not missing else 'MISSING ' + ';'.join(missing)}")
    if not missing and not kept:
        print("[PASS] PIT 弃用隔离 CI 门通过 (v1墓碑在库+活跃树零引用)")
        return 0
    if kept:
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
