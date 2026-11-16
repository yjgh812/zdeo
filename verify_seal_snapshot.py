# -*- coding: utf-8 -*-
"""
verify_seal_snapshot.py — 封单快照落库自验证（只读，供 14:57 cron 使用）
=====================================================================
用法:
    python verify_seal_snapshot.py --date 20260827
校验 D:\\zcodesj\\attribution\\attribution.db 的 seal_snapshot 表是否存在 date=<date> 的行。
- 行数 > 0 → 输出 OK 并退出码 0
- 行数 = 0 → 输出 FAIL 并退出码 1（供 cron 判为未落库失败）

背景：8/25、8/26 两天 14:57 cron 脚本"执行成功但未落库"（seal_snapshot 无当日行），
本脚本把静默失败转为显式失败（fail-closed），cron 据此通知主人。
作者: 知微 | 日期: 2026-08-27
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ATTR_DB = Path(r"D:\zcodesj\attribution\attribution.db")  # 与 seal_snapshot.py 同源(唯一真源)


def main() -> int:
    p = argparse.ArgumentParser(description="校验 seal_snapshot 当日是否落库")
    p.add_argument("--date", required=True, help="YYYYMMDD")
    args = p.parse_args()

    if not ATTR_DB.exists():
        print(f"FAIL: 归因库不存在 {ATTR_DB}")
        return 1
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM seal_snapshot WHERE date=?", (args.date,)
            ).fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error as e:
        print(f"FAIL: 查询异常 {type(e).__name__}: {e}")
        return 1

    if n > 0:
        print(f"OK: seal_snapshot date={args.date} 行数={n}，已落库")
        return 0
    print(f"FAIL: seal_snapshot date={args.date} 行数=0，未落库")
    return 1


if __name__ == "__main__":
    sys.exit(main())
