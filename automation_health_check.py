# -*- coding: utf-8 -*-
"""automation_health_check.py — ZCode 自动化派发健康巡检（只读，2026-09-10 晚建）。

背景（真实场景，非臆想）：9/10 全天 ZCode 应用未运行窗口（10:38-21:30）致
09:00/09:10(十一合一)/13:30/14:30/14:57 全被 skipped(computer_asleep_or_app_not_running)，
另有 08:00/09:26/09:40/21:30 dispatched→failed；当日无人发现——health_check_daily.py 的
cron_health 段只看 schtasks/脚本层，**ZCode 自动化层的漏跑与失败是观测盲区**。本工具补齐。

数据源：D:\\zcodesj\\.zcode\\v2\\tasks-index.sqlite（automations / automation_runs）只读。
判定（窗口默认 24h）：
  - succeeded            → OK
  - failed               → 🔴 失败（error 字段常为空，需人工/下次复现加探针）
  - skipped              → 🟡 漏派发（error=computer_asleep_or_app_not_running 属应用未运行）
  - running 且超 STALE   → 🔴 疑似卡死（默认 90 分钟）
退出码：0=全绿；2=存在 failed/skipped/疑似卡死；3=台账不可读（fail-loud，不伪健康）。
用法：python automation_health_check.py [--hours 24] [--stale-min 90] [--json]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
STALE_MIN_DEFAULT = 90


def _latest_run_ms(path: str) -> int:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        mx = con.execute("SELECT COALESCE(MAX(created_at), 0) FROM automation_runs").fetchone()[0]
        con.close()
        return int(mx or 0)
    except sqlite3.Error:
        return 0


# 2026-09-22 晚修复（知微终检,C盘重装workspace键迁移后同款盲区第二例）：宿主活注册表
# 已迁至用户级 C:\Users\Administrator\.zcode\v2\tasks-index.sqlite，D盘旧工作区副本 runs
# 停在 9/21 22:45。取 automation_runs 最新者=活源，workspace 再迁移免疫。
_DB_CANDIDATES = [
    r"C:\Users\Administrator\.zcode\v2\tasks-index.sqlite",
    os.path.join(ROOT, ".zcode", "v2", "tasks-index.sqlite"),
]
DB = max(
    (p for p in _DB_CANDIDATES if os.path.exists(p)),
    key=_latest_run_ms,
    default=_DB_CANDIDATES[-1],
)


def _ts(ms):
    return datetime.datetime.fromtimestamp(ms / 1000)


def collect(hours: int, stale_min: int) -> dict:
    if not os.path.exists(DB):
        raise FileNotFoundError(f"自动化台账不存在: {DB}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    titles = {r["automation_id"]: (r["title"] or "") for r in cur.execute("SELECT automation_id, title FROM automations")}
    since = int((datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp() * 1000)
    now = datetime.datetime.now()
    rows = []
    for r in cur.execute(
        "SELECT automation_id, scheduled_at, dispatch_status, outcome, error, updated_at "
        "FROM automation_runs WHERE scheduled_at >= ? ORDER BY scheduled_at", (since,)):
        outcome = (r["outcome"] or "").strip()
        disp = (r["dispatch_status"] or "").strip()
        # 注意：漏派发行的 outcome 为 NULL，状态在 dispatch_status='skipped'（9/10 实测）
        eff = outcome or disp
        sched = _ts(r["scheduled_at"])
        stale = False
        if eff == "running":
            ref = _ts(r["updated_at"]) if r["updated_at"] else sched
            stale = (now - ref).total_seconds() > stale_min * 60
        # 2026-09-23 补: 换绑删除旧世代 automations 行后其历史 run 变孤儿(id 不在 titles),
        # 孤儿=已删除任务的历史记录, 不计 bad(与 health_check_daily 同日同款修复)。
        known = r["automation_id"] in titles
        rows.append({
            "automation_id": r["automation_id"],
            "title": titles.get(r["automation_id"], "")[:40],
            "scheduled_at": sched.strftime("%m-%d %H:%M"),
            "dispatch_status": disp,
            "outcome": eff,
            "error": r["error"],
            "stale_running": stale,
            "orphan": not known,
            "bad": known and (eff in ("failed", "skipped") or stale),
        })
    con.close()
    n_bad = sum(1 for x in rows if x["bad"])
    return {"window_hours": hours, "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "n_runs": len(rows), "n_bad": n_bad, "runs": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--stale-min", type=int, default=STALE_MIN_DEFAULT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        d = collect(a.hours, a.stale_min)
    except Exception as e:
        print(f"[automation_health] 🔴 台账不可读: {type(e).__name__}: {e}（fail-loud，不伪健康）")
        return 3
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return 2 if d["n_bad"] else 0
    print(f"# ZCode 自动化健康 近{d['window_hours']}h（{d['generated_at']}）runs={d['n_runs']} 异常={d['n_bad']}")
    print("| 计划时间 | 自动化 | 派发 | 结果 | 备注 |")
    print("|---|---|---|---|---|")
    for x in d["runs"]:
        flag = "🔴" if x["outcome"] == "failed" else ("🟡" if x["outcome"] == "skipped" else ("🔴" if x["stale_running"] else "✅"))
        note = (x["error"] or "")[:38]
        if x["stale_running"]:
            note = f"running 超 {a.stale_min}min 疑似卡死"
        print(f"| {x['scheduled_at']} | {flag} {x['title']} | {x['dispatch_status']} | {x['outcome']} | {note} |")
    if d["n_bad"]:
        print(f"\n结论: 🔴 {d['n_bad']} 项异常（failed/skipped/卡死）——skipped 多为应用未运行窗口，"
              f"failed 无 error 详情需下次复现加探针")
        return 2
    print("\n结论: ✅ 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
