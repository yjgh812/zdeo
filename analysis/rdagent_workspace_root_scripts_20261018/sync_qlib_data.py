# -*- coding: utf-8 -*-
"""RD-Agent 数据副本同步工具(手工按需运行, 非cron)。

用途: 把生产 qlib_bin(D:\\workbuddyspace\\qlib_data\\qlib_bin)增量同步到
RD-Agent 数据桥副本(D:\\zcodesj\\rdagent_workspace\\qlib_home\\.qlib\\qlib_data\\cn_data)。
生产 qlib_bin 在 daily_chain_update 之后更新, 本工具在其后手动跑一次即可。

用法: python sync_qlib_data.py
退出码: 0=同步完成且日历前进; 2=校验失败。
"""
import subprocess
import sys
from pathlib import Path

SRC = Path(r"D:\workbuddyspace\qlib_data\qlib_bin")
DST = Path(r"D:\zcodesj\rdagent_workspace\qlib_home\.qlib\qlib_data\cn_data")


def calendar_tail(p: Path) -> str:
    return (p / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines()[-1]


def main() -> int:
    if not (SRC / "calendars" / "day.txt").exists():
        print(f"FAIL: source missing {SRC}")
        return 2
    before = calendar_tail(DST) if (DST / "calendars" / "day.txt").exists() else "(empty)"
    r = subprocess.run(
        ["robocopy", str(SRC), str(DST), "/E", "/NFL", "/NDL", "/NJH", "/R:2", "/W:2"],
        capture_output=True,
    )  # robocopy 输出为 GBK, 不解码只看退出码
    # robocopy: 0=无变化 1=有复制, 其余>=8为错误
    if r.returncode >= 8:
        print(f"FAIL: robocopy exit {r.returncode}")
        print(r.stdout[-500:])
        return 2
    after = calendar_tail(DST)
    n_inst = len(list((DST / "features").iterdir()))
    print(f"OK: calendar {before} -> {after}; instruments={n_inst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
