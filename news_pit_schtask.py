#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""news_pit_schtask.py — 新闻 PIT 存档 schtasks 通道编排器（2026-09-13 迁移落地）。

背景：21:30 新闻 PIT 原 automation 会话内 MCP cls_raw 被 example-plugin hook
劫持（9/12 21:33 FAIL 5/5，SOP(十七)③），主人「现在就执行」裁决迁 schtasks 形态。
本编排器串三步并聚合退出码（无 MCP 依赖）：
  1) cls_telegraph_fetch.py --rn 50 --out _cache/news_raw_<date>_2130.json（直连
     cls.cn 官方 API，与 cls_news_mcp 同端点同签名同字段，零伪造）
  2) news_pit_archive.py --input <上步产物> --date <当天>
  3) news_pit_archive.py --verify --date <当天>   (fail-closed)

告警合同（沿原 automation 第0步/收尾硬条款精神）：成功与失败都走 notify.py
toast 留痕，宁可重复告警不可无痕失败；notify 不可用则写 heartbeat 日志。

退出码：0 = 三步全过；2 = 任一步失败（已 toast）。
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\zcodesj")
HEARTBEAT = ROOT / "reports" / "news_pit_schtask_heartbeat.log"


def run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-W", "ignore", *cmd],
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace", cwd=str(ROOT))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def alert(title: str, content: str, level: str) -> None:
    try:
        subprocess.run([sys.executable, str(ROOT / "notify.py"), "send",
                        "--title", title, "--content", content, "--level", level],
                       capture_output=True, text=True, timeout=30)
    except Exception:
        try:
            with open(HEARTBEAT, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} {level} {title} {content}\n")
        except Exception:
            pass


def main() -> int:
    now = datetime.now()
    date = f"{now:%Y%m%d}"
    raw = ROOT / "_cache" / f"news_raw_{date}_2130.json"

    rc1, out1 = run([str(ROOT / "cls_telegraph_fetch.py"),
                     "--rn", "50", "--out", str(raw)])
    if rc1 != 0:
        alert("news_pit schtask FAIL", f"拉取失败: {out1.strip()[-120:]}", "error")
        print(f"[FAIL] step1 fetch rc={rc1}\n{out1}")
        return 2

    rc2, out2 = run([str(ROOT / "news_pit_archive.py"),
                     "--input", str(raw), "--date", date])
    if rc2 != 0:
        alert("news_pit schtask FAIL", f"归档失败: {out2.strip()[-120:]}", "error")
        print(f"[FAIL] step2 archive rc={rc2}\n{out2}")
        return 2

    rc3, out3 = run([str(ROOT / "news_pit_archive.py"), "--verify", "--date", date])
    n_lines = out3.strip().splitlines()[-1] if out3.strip() else ""
    if rc3 != 0:
        alert("news_pit schtask FAIL", f"verify失败: {n_lines[-120:]}", "error")
        print(f"[FAIL] step3 verify rc={rc3}\n{out3}")
        return 2

    alert("news_pit schtask OK", n_lines[-140:], "info")
    print(f"[OK] fetch+archive+verify 全过\n{out1}{out2}{out3}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
