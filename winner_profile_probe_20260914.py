#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""winner_profile_probe_20260914.py — 23:30 涨票画像 automation 首跑成败裁决探针（一次性）。

背景：automation-03b31bcf（工作日23:30涨票画像，继承创建会话模型 GLM-5.3-Flash）
首跑裁决点 = 2026-09-14（周一）23:30 实跑，产物 = reports/winner_profile_daily/winner_profile_20260914.md。
automation_runs 台账断链（全表0行），故以产物+qfq+产物mtime 三通道机器判定，
结果 toast 提醒主人（SOP(二十) 三分支预案）。

判定（观察窗口 2026-09-14 23:35 起当日有效，其余日期静默退出；--force 跳过守卫、toast 带[TEST]）：
  A  qfq MAX>=20260914 且 winner_profile_20260914.md 存在
     -> PASS: automation 会话 GLM 回合成功，GLM 配额假设进一步证伪
  B  qfq MAX>=20260914 且产物缺失
     -> FAIL: automation 派发/LLM 回合失败，假设进一步证实
        预案 = DeepSeek 新交互会话先 CronDelete automation-03b31bcf 再按 data/_rebuild_737e364c.json 重建
  B' qfq MAX< 20260914 且 winner_profile_20260911.md mtime >= 当日23:00
     -> automation 会话成功(GLM 证伪) 但 22:00 链数据滞后（另案排查数据链）
  C  qfq MAX< 20260914 且无新鲜产物
     -> 数据链与 automation 双疑，升级主人

只读探针：不写画像产物、不动台账、不交易。产出 reports/winner_profile_probe_20260914.md + toast。
退出码：0=已判定或守卫静默退出；3=探针自身异常。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\zcodesj")

QFQ_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
OUT_DIR = r"D:\zcodesj\reports\winner_profile_daily"
OUT = r"D:\zcodesj\reports\winner_profile_probe_20260914.md"
OBS_DATE = "20260914"
PROD_914 = os.path.join(OUT_DIR, f"winner_profile_{OBS_DATE}.md")
PROD_911 = os.path.join(OUT_DIR, "winner_profile_20260911.md")
REBUILD_PKG = r"D:\zcodesj\data\_rebuild_737e364c.json"
AUTO_ID = "automation-03b31bcf-f34e-49c8-92b2-8547e8428d0b"


def qfq_max_date() -> str:
    con = sqlite3.connect("file:" + QFQ_DB + "?mode=ro", uri=True)
    try:
        row = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()
    finally:
        con.close()
    return row[0] if row and row[0] else ""


def notify(title: str, content: str, level: str) -> bool:
    try:
        r = subprocess.run(
            [sys.executable, r"D:\zcodesj\notify.py", "send",
             "--title", title, "--content", content, "--level", level],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        return r.returncode == 0
    except Exception as exc:  # toast 失败不掩盖判定结论，报告留痕为主
        print(f"[WARN] notify 失败: {exc}")
        return False


def main() -> int:
    try:
        force = "--force" in sys.argv
        now = datetime.now()
        tag = "[TEST]" if force else ""

        if not force and (now.strftime("%Y%m%d") != OBS_DATE or now.hour < 23):
            print(f"守卫退出: 当前 {now:%Y-%m-%d %H:%M} 非观察窗口({OBS_DATE} 23:35 后)，静默 exit 0")
            return 0

        qmax = qfq_max_date()
        has_914 = os.path.isfile(PROD_914)
        mtime_911 = os.path.getmtime(PROD_911) if os.path.isfile(PROD_911) else 0.0
        mtime_911_str = datetime.fromtimestamp(mtime_911).strftime("%m-%d %H:%M") if mtime_911 else "无"
        fresh_911 = mtime_911 >= datetime(2026, 9, 14, 23, 0).timestamp()

        if qmax >= OBS_DATE:
            if has_914:
                verdict, level = "A", "info"
                headline = "PASS: 23:30画像automation GLM回合成功 -> GLM配额假设进一步证伪"
                action = "无需处置；按SOP(二十)登记裁决结果即可。"
            else:
                verdict, level = "B", "error"
                headline = "FAIL: 23:30画像automation未落产物 -> GLM配额假设进一步证实"
                action = (f"预案=DeepSeek新交互会话: 1)CronDelete {AUTO_ID}(腾20上限位) "
                          f"2)按 {REBUILD_PKG} title/cron/prompt逐字CronCreate(忽略包内旧id) "
                          "3)验收enabled+nextRun=周二23:30 4)SOP回写。")
        else:
            if fresh_911:
                verdict, level = "B'", "warning"
                headline = "PARTIAL: automation会话已跑(GLM证伪) 但 qfq停" + qmax + " -> 22:00数据链滞后另案"
                action = "排查9/14的22:00 daily_chain_update；画像产物以20260911截面为准。"
            else:
                verdict, level = "C", "error"
                headline = "AMBIG: qfq停" + qmax + "且无新鲜画像产物 -> 数据链与automation双疑"
                action = ("先查22:00链(schtasks Signal_Cron)是否跑；再查automation-03b31bcf是否派发；"
                          "升级主人按SOP(二十)处置。")

        md = (
            "# 涨票画像 automation 首跑裁决探针（20260914）\n\n"
            f"- 判定时间: {now:%Y-%m-%d %H:%M:%S}{' (--force 测试模式)' if force else ''}\n"
            f"- qfq MAX(date) = {qmax}\n"
            f"- winner_profile_20260914.md: {'存在' if has_914 else '缺失'}\n"
            f"- winner_profile_20260911.md mtime: {mtime_911_str}（新鲜阈值 09-14 23:00）\n"
            f"- **判定 = {verdict}**: {headline}\n"
            f"- 处置: {action}\n"
        )
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(md)
        toast_ok = notify(
            f"{tag}涨票画像裁决 {verdict}",
            headline[:120] + " | " + action[:120],
            level)
        print(md)
        print(f"toast: {'OK' if toast_ok else 'FAIL(见报告留痕)'}")
        return 0
    except Exception as exc:
        print(f"[FAIL] 探针异常: {exc!r}")
        try:
            notify("[TEST]" if "--force" in sys.argv else "",
                   "涨票画像裁决探针自身异常: " + repr(exc)[:150], "error")
        except Exception:
            pass
        return 3


if __name__ == "__main__":
    sys.exit(main())
