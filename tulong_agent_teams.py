#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tulong_agent_teams.py — 屠龙多智能体团队协作状态机 (v1.1)

借鉴 NanmiCoder/dsh-agent-teams 的磁盘真相协议, 在 ZCode + Node-RED 环境落地:
  队长式委派  — 当前 ZCode 会话即队长, 用本 CLI 建队/派活/汇总
  可续聊成员  — 成员档案绑定 ZCode subagent agentId, 可被 SendMessage 唤醒续跑
  带依赖任务  — 任务有 pending/claimed/done 状态; 依赖未全部完成时禁止领取
  持久化邮箱  — 每个参与者一个邮箱文件, 成员直达消息无需队长中转
  实时活动面板 — Node-RED 读 dashboard-data 输出渲染 (http://localhost:18800/agent-teams/ui)

v1.1 增强(2026-08-18, 跨会话自主性五项):
  check-bindings        — 成员绑定/最后活跃/唤醒可行性检查(失联恢复证据)
  recovery-report       — 跨会话启动扫描全部活动团队, 输出"待队长处理清单"
  captain-decision      — 队长决策结构化解码(trigger/evidence/conclusion/action)
  query-memory          — 成员直读知识库索引(记忆系统关键词检索, 无需队长中转)
  create-task --timeout-hours + stale-check --auto-reset — 任务超时自动降级

状态目录(磁盘真相): D:/zcodesj/.agent-teams/<teamId>/{team.json, members/, tasks/, mailbox/, events.jsonl}
归档目录: D:/zcodesj/.agent-teams/archive/<teamId>/

纯标准库, 单文件。所有写操作带文件锁。
用法: python tulong_agent_teams.py <command> [args]   (help 查看全部命令)
"""
import argparse
import json
import os
import sys
import time
import shutil
from datetime import datetime
from pathlib import Path

# Windows Git Bash 下强制 utf-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path("D:/zcodesj/.agent-teams")
ARCHIVE = ROOT / "archive"
LOCK_TIMEOUT = 60  # 秒, 超过视为死锁可清除

TASK_STATUSES = ("pending", "claimed", "done")
MEMBER_STATUSES = ("idle", "working", "removed")
CAPTAIN_KEY = "captain"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def die(msg: str, code: int = 1):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


def ok(payload: dict | None = None):
    out = {"ok": True, "ts": now()}
    if payload:
        out.update(payload)
    print(json.dumps(out, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- 文件锁
class TeamLock:
    """简单文件锁: 防止队长与多个成员子 Agent 并发写坏状态。"""

    def __init__(self, team_dir: Path):
        self.lock = team_dir / ".lock"

    def __enter__(self):
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(LOCK_TIMEOUT):
            try:
                fd = self.lock.open("x")
                fd.write(f"{time.time()}")
                fd.close()
                return self
            except FileExistsError:
                # 死锁清理
                try:
                    age = time.time() - float(self.lock.read_text().strip())
                    if age > LOCK_TIMEOUT:
                        self.lock.unlink(missing_ok=True)
                except (ValueError, OSError):
                    self.lock.unlink(missing_ok=True)
                time.sleep(1)
        die(f"团队锁超时({LOCK_TIMEOUT}s): {self.lock}")

    def __exit__(self, *exc):
        self.lock.unlink(missing_ok=True)


# ---------------------------------------------------------------- 读写
def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def team_index() -> dict:
    return read_json(ROOT / "index.json", default={})


def save_team_index(idx: dict):
    write_json(ROOT / "index.json", idx)


def team_dir(team_id: str, must_exist=True) -> Path:
    d = ROOT / team_id
    if must_exist and not (d / "team.json").exists():
        alt = ARCHIVE / team_id
        if (alt / "team.json").exists():
            die(f"团队 {team_id} 已归档, 只读; 如需重启请新建团队")
        die(f"团队不存在: {team_id} (list-teams 查看)")
    return d


def load_team(d: Path) -> dict:
    return read_json(d / "team.json")


def save_team(d: Path, team: dict):
    write_json(d / "team.json", team)


def append_event(d: Path, kind: str, detail: dict):
    line = {"ts": now(), "kind": kind, **detail}
    with (d / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def read_events(d: Path, limit=20):
    p = d / "events.jsonl"
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------- 成员/任务/邮箱
def member_ids(team: dict) -> list[str]:
    return [m["id"] for m in team.get("members", []) if m.get("status") != "removed"]


def find_member(team: dict, member_id: str) -> dict:
    for m in team.get("members", []):
        if m["id"] == member_id and m.get("status") != "removed":
            return m
    die(f"成员不存在: {member_id}; 现有 {member_ids(team)}")


def task_path(d: Path, task_id: str) -> Path:
    return d / "tasks" / f"{task_id}.json"


def load_task(d: Path, task_id: str) -> dict:
    t = read_json(task_path(d, task_id))
    if t is None:
        die(f"任务不存在: {task_id}")
    return t


def save_task(d: Path, task: dict):
    write_json(task_path(d, task["id"]), task)


def all_tasks(d: Path) -> list[dict]:
    out = []
    tdir = d / "tasks"
    if tdir.exists():
        for p in sorted(tdir.glob("t*.json")):
            out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


def unsatisfied_deps(d: Path, task: dict) -> list[str]:
    bad = []
    for dep in task.get("dependsOn", []):
        dt = read_json(task_path(d, dep))
        if dt is None or dt.get("status") != "done":
            bad.append(dep)
    return bad


def mailbox_path(d: Path, key: str) -> Path:
    return d / "mailbox" / f"{key}.json"


def load_mailbox(d: Path, key: str) -> dict:
    return read_json(mailbox_path(d, key), default={"messages": []})


def save_mailbox(d: Path, key: str, box: dict):
    write_json(mailbox_path(d, key), box)


def valid_keys(team: dict) -> list[str]:
    return member_ids(team) + [CAPTAIN_KEY]


# ---------------------------------------------------------------- 命令实现
def cmd_create_team(a):
    team_id = f"team_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    d = ROOT / team_id
    if (d / "team.json").exists():
        die(f"团队ID冲突(1秒内重复建队): {team_id}")
    team = {
        "id": team_id,
        "goal": a.goal,
        "status": "active",
        "captain": "zcode-session",
        "createdAt": now(),
        "members": [],
        "taskSeq": 0,
        "memberSeq": 0,
    }
    with TeamLock(d):
        save_team(d, team)
        save_mailbox(d, CAPTAIN_KEY, {"messages": []})  # 队长邮箱
        append_event(d, "team_created", {"goal": a.goal})
        idx = team_index()
        idx[team_id] = {"goal": a.goal, "status": "active", "createdAt": now()}
        save_team_index(idx)
    ok({"team_id": team_id, "goal": a.goal, "hint": f"下一步: add-member --team {team_id}"})


def cmd_add_member(a):
    d = team_dir(a.team)
    with TeamLock(d):
        team = load_team(d)
        team["memberSeq"] += 1
        mid = f"m{team['memberSeq']}"
        for m in team["members"]:
            if m.get("name") == a.name and m.get("status") != "removed":
                die(f"角色名重复: {a.name}")
        member = {
            "id": mid,
            "name": a.name,
            "role": a.role,
            "status": "idle",
            "agentId": a.agent_id or "",
            "currentTask": "",
            "createdAt": now(),
        }
        team["members"].append(member)
        save_team(d, team)
        save_mailbox(d, mid, {"messages": []})
        append_event(d, "member_added", {"member": mid, "name": a.name})
    ok({"member_id": mid, "name": a.name, "agentId": member["agentId"] or "(未绑定,可后续bind-agent)"})


def cmd_bind_agent(a):
    d = team_dir(a.team)
    with TeamLock(d):
        team = load_team(d)
        m = find_member(team, a.member)
        m["agentId"] = a.agent_id
        save_team(d, team)
        append_event(d, "agent_bound", {"member": a.member, "agentId": a.agent_id})
    ok({"member": a.member, "agentId": a.agent_id, "hint": "此后可用 SendMessage(to=agentId) 唤醒该成员续跑"})


def cmd_set_member_status(a):
    d = team_dir(a.team)
    with TeamLock(d):
        team = load_team(d)
        m = find_member(team, a.member)
        if a.status not in MEMBER_STATUSES:
            die(f"非法成员状态: {a.status}; 合法 {MEMBER_STATUSES}")
        m["status"] = a.status
        save_team(d, team)
        append_event(d, "member_status", {"member": a.member, "status": a.status})
    ok({"member": a.member, "status": a.status})


def cmd_create_task(a):
    d = team_dir(a.team)
    with TeamLock(d):
        team = load_team(d)
        if a.owner and a.owner != CAPTAIN_KEY and a.owner not in member_ids(team):
            die(f"owner 非法: {a.owner}; 可选 {member_ids(team) + [CAPTAIN_KEY]}")
        team["taskSeq"] += 1
        tid = f"t{team['taskSeq']}"
        deps = []
        if a.depends_on:
            for dep in [x.strip() for x in a.depends_on.split(",") if x.strip()]:
                if not task_path(d, dep).exists():
                    die(f"依赖任务不存在: {dep}")
                deps.append(dep)
        task = {
            "id": tid,
            "title": a.title,
            "desc": a.desc or "",
            "owner": a.owner or "",
            "dependsOn": deps,
            "status": "pending",
            "output": "",
            "createdAt": now(),
            "claimedAt": "",
            "doneAt": "",
            "timeoutHours": a.timeout_hours or None,
        }
        save_task(d, task)
        save_team(d, team)
        append_event(d, "task_created", {"task": tid, "title": a.title, "dependsOn": deps})
    ok({"task_id": tid, "title": a.title, "owner": a.owner or "(未指派,首个claim者成为owner)",
        "dependsOn": deps, "timeoutHours": a.timeout_hours or None})


def cmd_claim_task(a):
    d = team_dir(a.team)
    with TeamLock(d):
        team = load_team(d)
        if a.by != CAPTAIN_KEY and a.by not in member_ids(team):
            die(f"--by 非法: {a.by}; 可选 {member_ids(team) + [CAPTAIN_KEY]}")
        task = load_task(d, a.task)
        if task["status"] != "pending":
            die(f"任务 {a.task} 状态为 {task['status']}, 只有 pending 可领取")
        bad = unsatisfied_deps(d, task)
        if bad:
            die(f"依赖未完成, 禁止领取: {a.task} 依赖 {task['dependsOn']}, 未完成 {bad}")
        if task["owner"] and a.by != task["owner"]:
            die(f"任务已指派给 {task['owner']}, {a.by} 不能领取")
        task["owner"] = a.by
        task["status"] = "claimed"
        task["claimedAt"] = now()
        save_task(d, task)
        if a.by != CAPTAIN_KEY:
            m = find_member(team, a.by)
            m["status"] = "working"
            m["currentTask"] = a.task
            save_team(d, team)
        append_event(d, "task_claimed", {"task": a.task, "by": a.by})
    ok({"task": a.task, "owner": a.by, "status": "claimed"})


def cmd_complete_task(a):
    d = team_dir(a.team)
    with TeamLock(d):
        team = load_team(d)
        task = load_task(d, a.task)
        if task["status"] != "claimed":
            die(f"任务 {a.task} 状态为 {task['status']}, 只有 claimed 可完成")
        task["status"] = "done"
        task["output"] = a.output or ""
        task["doneAt"] = now()
        save_task(d, task)
        if task["owner"] and task["owner"] != CAPTAIN_KEY:
            m = find_member(team, task["owner"])
            m["status"] = "idle"
            m["currentTask"] = ""
            save_team(d, team)
        append_event(d, "task_done", {"task": a.task, "output": (a.output or "")[:200]})
    ok({"task": a.task, "status": "done"})


def cmd_task_status(a):
    d = team_dir(a.team)
    with TeamLock(d):
        if a.status not in TASK_STATUSES:
            die(f"非法任务状态: {a.status}; 合法 {TASK_STATUSES}")
        task = load_task(d, a.task)
        task["status"] = a.status
        save_task(d, task)
        append_event(d, "task_status_set", {"task": a.task, "status": a.status})
    ok({"task": a.task, "status": a.status})


def cmd_send(a):
    d = team_dir(a.team)
    with TeamLock(d):
        team = load_team(d)
        keys = valid_keys(team)
        if a.from_ not in keys:
            die(f"--from 非法: {a.from_}; 可选 {keys}")
        if a.to not in keys:
            die(f"--to 非法: {a.to}; 可选 {keys}")
        box = load_mailbox(d, a.to)
        box["messages"].append({"from": a.from_, "to": a.to, "body": a.body, "ts": now(), "read": False})
        save_mailbox(d, a.to, box)
        append_event(d, "message_sent", {"from": a.from_, "to": a.to, "body": a.body[:120]})
    ok({"from": a.from_, "to": a.to, "body": a.body[:120]})


def cmd_read_mail(a):
    d = team_dir(a.team)
    with TeamLock(d):
        team = load_team(d)
        if a.who not in valid_keys(team):
            die(f"--who 非法: {a.who}")
        box = load_mailbox(d, a.who)
        unread = [m for m in box["messages"] if not m["read"]]
        if a.mark_read:
            for m in box["messages"]:
                m["read"] = True
            save_mailbox(d, a.who, box)
        payload = {"who": a.who, "unread": len(unread), "messages": box["messages"]}
        if a.mark_read:
            payload["marked"] = "all_read"
    ok(payload)


def team_snapshot(team_id: str, d: Path) -> dict:
    team = load_team(d)
    tasks = all_tasks(d)
    # 派生 blocked 态: pending 且依赖未全 done
    for t in tasks:
        if t["status"] == "pending" and unsatisfied_deps(d, t):
            t["display"] = "blocked"
        else:
            t["display"] = t["status"]
    members = []
    for m in team.get("members", []):
        if m.get("status") == "removed":
            continue
        box = load_mailbox(d, m["id"])
        unread = len([x for x in box["messages"] if not x["read"]])
        members.append({**m, "unread": unread})
    cap_box = load_mailbox(d, CAPTAIN_KEY)
    # 全团队最近直达消息(面板消息流)
    msgs = []
    for key in valid_keys(team):
        box = load_mailbox(d, key)
        for m in box["messages"]:
            msgs.append({"from": m["from"], "to": m["to"], "body": m["body"], "ts": m["ts"]})
    msgs.sort(key=lambda x: x["ts"], reverse=True)
    return {
        "id": team_id,
        "goal": team["goal"],
        "status": team["status"],
        "createdAt": team["createdAt"],
        "members": members,
        "captainUnread": len([x for x in cap_box["messages"] if not x["read"]]),
        "recentMessages": msgs[:8],
        "tasks": tasks,
        "progress": {
            "done": len([t for t in tasks if t["status"] == "done"]),
            "claimed": len([t for t in tasks if t["status"] == "claimed"]),
            "pending": len([t for t in tasks if t["status"] == "pending"]),
            "total": len(tasks),
        },
        "recentEvents": read_events(d, 15),
    }


def cmd_status(a):
    d = team_dir(a.team)
    ok(team_snapshot(a.team, d))


def cmd_list_teams(a):
    idx = team_index()
    rows = []
    for tid, meta in sorted(idx.items(), key=lambda kv: kv[1]["createdAt"], reverse=True):
        if not a.all and meta["status"] != "active":
            continue
        rows.append({"id": tid, **meta})
    ok({"teams": rows})


def cmd_archive(a):
    d = team_dir(a.team)
    with TeamLock(d):
        idx = team_index()
        if a.team not in idx:
            die(f"索引中无此团队: {a.team}")
        # 常设团队保护(2026-08-17全量实跑事故): goal 含"常设"的团队拒绝归档, 除非 --force
        team = load_team(d)
        if "常设" in (team.get("goal") or "") and not a.force:
            die(f"该团队为常设编制(goal含'常设'), 拒绝归档; 确需归档请加 --force")
        dest = ARCHIVE / a.team
        if dest.exists():
            die(f"归档目录已存在: {dest}")
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        team["status"] = "archived"
        team["archivedAt"] = now()
        save_team(d, team)
        shutil.move(str(d), str(dest))
        idx[a.team]["status"] = "archived"
        idx[a.team]["archivedAt"] = now()
        save_team_index(idx)
        append_event(dest, "team_archived", {})
    ok({"team": a.team, "archivedTo": str(ARCHIVE / a.team)})


def _task_timeout_hours(task: dict, global_hours: float) -> float:
    """任务自带 --timeout-hours 优先, 否则用全局阈值。"""
    t = task.get("timeoutHours")
    return float(t) if t else global_hours


def cmd_stale_check(a):
    """检测领取超时的任务(子Agent失联/卡死时claimed永挂)。

    --auto-reset: 超时任务自动重置为 pending(清claimedAt; 清owner以释放认领权),
    并向队长邮箱发系统通知(from=system)。不带则只报告。
    """
    d = team_dir(a.team)
    with TeamLock(d):
        team = load_team(d)
        tasks = all_tasks(d)
        now_ts = datetime.now()
        stale = []
        for t in tasks:
            if t["status"] == "claimed" and t.get("claimedAt"):
                threshold = _task_timeout_hours(t, a.hours)
                try:
                    claimed = datetime.strptime(t["claimedAt"], "%Y-%m-%d %H:%M:%S")
                    hours = (now_ts - claimed).total_seconds() / 3600
                    if hours >= threshold:
                        stale.append({"task": t["id"], "title": t["title"], "owner": t["owner"],
                                      "claimedAt": t["claimedAt"], "hours": round(hours, 1),
                                      "threshold": threshold})
                except ValueError:
                    continue
        if a.auto_reset:
            for s in stale:
                t = load_task(d, s["task"])
                s["oldOwner"] = t.get("owner", "")
                t["status"] = "pending"
                t["claimedAt"] = ""
                t["owner"] = ""  # 清owner以释放认领权, 否则原owner失联时任务被锁死
                save_task(d, t)
                append_event(d, "task_auto_reset",
                             {"task": s["task"], "reason": f"claimed超时{s['hours']}h>={s['threshold']}h",
                              "by": "system"})
                notify_captain(d, team,
                               f"⚠️ 任务{s['task']}({s['title']})超时{s['hours']}h已自动重置为pending,"
                               f"原owner {s['oldOwner']} 认领权已释放; 请重新指派或由他人领取")
            if stale:
                team = load_team(d)  # notify 不改 team, 无需重读
        payload = {"team": a.team, "stale": stale,
                   "autoReset": bool(a.auto_reset and stale),
                   "hint": f"处置: stale-check --auto-reset 自动重置; 或 SendMessage 追问 owner 的 agentId (阈值{a.hours}h, 任务级timeoutHours优先)"}
    ok(payload)


def cmd_dashboard_data(a):
    idx = team_index()
    active, archived = [], []
    for tid, meta in idx.items():
        if meta["status"] == "active":
            d = ROOT / tid
            if (d / "team.json").exists():
                active.append(team_snapshot(tid, d))
        else:
            d = ARCHIVE / tid
            if (d / "team.json").exists():
                snap = team_snapshot(tid, d)
                snap["goal"] = meta.get("goal", snap["goal"])
                archived.append(snap)
    print(json.dumps({"ok": True, "generatedAt": now(), "activeTeams": active,
                      "archivedTeams": archived[-6:]}, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- v1.1 跨会话自主性增强
def notify_captain(d: Path, team: dict, body: str):
    """系统级通知(bypass 成员校验, from=system), 落队长邮箱+事件留痕。"""
    box = load_mailbox(d, CAPTAIN_KEY)
    box["messages"].append({"from": "system", "to": CAPTAIN_KEY, "body": body, "ts": now(), "read": False})
    save_mailbox(d, CAPTAIN_KEY, box)
    append_event(d, "system_notify", {"to": CAPTAIN_KEY, "body": body[:120]})


def member_last_active(d: Path, member_id: str) -> str:
    """该成员在事件流中的最后活跃时间(agent_bound/task_claimed/task_done/message_sent)。"""
    p = d / "events.jsonl"
    if not p.exists():
        return ""
    last = ""
    for ln in p.read_text(encoding="utf-8").strip().splitlines():
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if e.get("member") == member_id or e.get("by") == member_id or e.get("from") == member_id:
            last = e.get("ts", "")
    return last


def _hours_since(ts_str: str) -> float | None:
    try:
        return round((datetime.now() - datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600, 2)
    except (ValueError, TypeError):
        return None


def cmd_check_bindings(a):
    """成员绑定与唤醒可行性检查(失联恢复证据, 不自动改状态)。

    CLI 无法探测 ZCode agentId 是否仍有效(会话级), 只能给证据:
      unbound   — 无 agentId, 无法唤醒, 需重新 spawn+bind
      stale     — 有 agentId 但 task_claimed 超时(参考阈值), 高概率失联, 需 SendMessage 实测
      unsure    — 有 agentId 且无超时任务, 标注需实测
    """
    d = team_dir(a.team)
    team = load_team(d)
    tasks = all_tasks(d)
    claimed_by = {}
    for t in tasks:
        if t["status"] == "claimed" and t.get("owner"):
            threshold = _task_timeout_hours(t, a.hours)
            hours = _hours_since(t.get("claimedAt", ""))
            claimed_by.setdefault(t["owner"], []).append(
                {"task": t["id"], "claimedAt": t["claimedAt"], "hours": hours, "threshold": threshold})
    members = []
    for m in team.get("members", []):
        if m.get("status") == "removed":
            continue
        mid = m["id"]
        agent = m.get("agentId", "")
        claimed = claimed_by.get(mid, [])
        is_stale = any(c["hours"] is not None and c["hours"] >= c["threshold"] for c in claimed)
        if not agent:
            verdict = "unbound"
        elif is_stale:
            verdict = "stale"
        else:
            verdict = "unsure"
        members.append({
            "member": mid, "name": m.get("name"), "agentId": agent,
            "status": m.get("status"), "currentTask": m.get("currentTask", ""),
            "lastActive": member_last_active(d, mid),
            "idleHours": _hours_since(member_last_active(d, mid)),
            "claimed": claimed,
            "verdict": verdict,
            "hint": {
                "unbound": "无agentId, 需重新spawn+bind-agent",
                "stale": f"有任务超时, 高概率失联; 用SendMessage实测, 失败则重新spawn",
                "unsure": "绑定存在且无超时, 需SendMessage实测确认",
            }[verdict],
        })
    ok({"team": a.team, "members": members})


def cmd_recovery_report(a):
    """跨会话启动扫描: 全部活动团队 → 失联成员/超时任务/未读邮箱 → 待队长处理清单。"""
    idx = team_index()
    scans = []
    for tid, meta in sorted(idx.items(), key=lambda kv: kv[1]["createdAt"]):
        if meta["status"] != "active":
            continue
        d = ROOT / tid
        if not (d / "team.json").exists():
            continue
        team = load_team(d)
        tasks = all_tasks(d)
        stale = []
        for t in tasks:
            if t["status"] == "claimed" and t.get("claimedAt"):
                threshold = _task_timeout_hours(t, a.hours)
                h = _hours_since(t.get("claimedAt", ""))
                if h is not None and h >= threshold:
                    stale.append({"task": t["id"], "title": t["title"], "owner": t["owner"],
                                  "hours": h, "threshold": threshold})
        dead, unread_boxes = [], {}
        for m in team.get("members", []):
            if m.get("status") == "removed":
                continue
            box = load_mailbox(d, m["id"])
            n = len([x for x in box["messages"] if not x["read"]])
            if n:
                unread_boxes[m["id"]] = n
            if not m.get("agentId"):
                dead.append({"member": m["id"], "name": m.get("name"), "reason": "unbound"})
            elif any(s["owner"] == m["id"] for s in stale):
                dead.append({"member": m["id"], "name": m.get("name"), "reason": "stale_task"})
        cap_box = load_mailbox(d, CAPTAIN_KEY)
        cap_unread = len([x for x in cap_box["messages"] if not x["read"]])
        actions = []
        for s in stale:
            actions.append(f"stale-check --team {tid} --auto-reset 重置 {s['task']}(超时{s['hours']}h)")
        for mid, n in unread_boxes.items():
            actions.append(f"唤醒{mid} read-mail --who {mid} 处理{n}封未读")
        if cap_unread:
            actions.append(f"read-mail --team {tid} --who captain --mark-read 处理{cap_unread}封未读")
        for dead_m in dead:
            actions.append(f"check-bindings --team {tid} 核实 {dead_m['member']}; 确认失联则 respawn+bind-agent")
        scans.append({
            "team": tid, "goal": team.get("goal"), "status": team.get("status"),
            "staleTasks": stale, "deadMembers": dead, "unreadMail": unread_boxes,
            "captainUnread": cap_unread, "actions": actions,
        })
    ok({"scannedAt": now(), "activeTeams": len(scans), "teams": scans})


def cmd_captain_decision(a):
    """队长决策结构化留痕(复盘/审计用): trigger → evidence → conclusion → action。"""
    d = team_dir(a.team)
    dec_dir = d / "decisions"
    dec_dir.mkdir(parents=True, exist_ok=True)
    log = dec_dir / "decisions.jsonl"
    if a.list:
        rows = []
        if log.exists():
            for ln in log.read_text(encoding="utf-8").strip().splitlines():
                try:
                    rows.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        ok({"team": a.team, "decisions": rows[-a.limit:]})
        return
    from_str = a.from_ or "captain"
    rec = {"ts": now(), "team": a.team, "from": from_str,
           "trigger": a.trigger, "evidence": a.evidence,
           "conclusion": a.conclusion, "action": a.action or "无操作"}
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    append_event(d, "captain_decision", {"from": from_str, "conclusion": a.conclusion[:120]})
    ok({"decision": rec, "hint": "决策已落盘 decisions/decisions.jsonl, 面板事件流可见"})


MEMORY_INDEX_PATTERN = None  # 延迟初始化正则


def cmd_query_memory(a):
    """成员直读知识库: 匹配记忆索引行, --detail 时再读正文命中行。"""
    import re
    global MEMORY_INDEX_PATTERN
    if MEMORY_INDEX_PATTERN is None:
        MEMORY_INDEX_PATTERN = re.compile(r"-\s*\[([^\]]+)\]\(([^)]+)\)\s*—\s*(.*)")
    mem_dir = Path(a.memory_dir)
    idx_file = mem_dir / "MEMORY.md"
    if not idx_file.exists():
        die(f"记忆索引不存在: {idx_file}")
    kw = a.keyword.lower()
    hits = []
    for ln in idx_file.read_text(encoding="utf-8").splitlines():
        m = MEMORY_INDEX_PATTERN.match(ln.strip())
        if not m:
            if kw in ln.lower():
                hits.append({"kind": "raw", "line": ln.strip()})
            continue
        title, fname, desc = m.group(1), m.group(2), m.group(3)
        if kw in title.lower() or kw in desc.lower():
            hit = {"kind": "memory", "title": title, "file": fname, "desc": desc}
            if a.detail:
                body = mem_dir / fname
                if body.exists():
                    hit["body_lines"] = _detail_lines(body, kw, a.limit)
                else:
                    hit["desc"] = desc + " ⚠️正文缺失"
            hits.append(hit)
    ok({"keyword": a.keyword, "hits": hits[: a.limit * 3] if a.detail else hits[: a.limit],
        "memoryDir": str(mem_dir)})


def _detail_lines(body: Path, kw: str, limit: int) -> list[str]:
    out = []
    try:
        for ln in body.read_text(encoding="utf-8").splitlines():
            if kw in ln.lower() and len(out) < limit:
                out.append(ln.strip()[:200])
        if not out:
            out.append("(正文无关键词命中, 以索引摘要为准)")
    except OSError:
        out.append("(正文读取失败)")
    return out


# ---------------------------------------------------------------- CLI
def build_parser():
    p = argparse.ArgumentParser(prog="tulong_agent_teams",
                                description="屠龙多智能体团队协作状态机 (磁盘真相)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("create-team", help="建队(当前会话成为队长)")
    s.add_argument("--goal", required=True, help="团队目标")
    s.set_defaults(fn=cmd_create_team)

    s = sub.add_parser("add-member", help="添加角色成员")
    s.add_argument("--team", required=True)
    s.add_argument("--name", required=True, help="角色名,如:风控官")
    s.add_argument("--role", required=True, help="职责描述")
    s.add_argument("--agent-id", default="", help="绑定的ZCode子Agent agentId(可后补)")
    s.set_defaults(fn=cmd_add_member)

    s = sub.add_parser("bind-agent", help="绑定/更新成员的ZCode子Agent ID")
    s.add_argument("--team", required=True)
    s.add_argument("--member", required=True, help="成员ID如m1")
    s.add_argument("--agent-id", required=True)
    s.set_defaults(fn=cmd_bind_agent)

    s = sub.add_parser("set-member-status", help="设置成员状态 idle/working/removed")
    s.add_argument("--team", required=True)
    s.add_argument("--member", required=True)
    s.add_argument("--status", required=True)
    s.set_defaults(fn=cmd_set_member_status)

    s = sub.add_parser("create-task", help="创建任务(可带依赖)")
    s.add_argument("--team", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--desc", default="")
    s.add_argument("--owner", default="", help="指派成员ID或captain;留空则首个claim者获得")
    s.add_argument("--depends-on", default="", help="逗号分隔的依赖任务ID,如t1,t2")
    s.add_argument("--timeout-hours", type=float, default=None,
                   help="任务级超时阈值(小时), stale-check时优先于全局阈值")
    s.set_defaults(fn=cmd_create_task)

    s = sub.add_parser("claim-task", help="领取任务(依赖未完成会被硬拒)")
    s.add_argument("--team", required=True)
    s.add_argument("--task", required=True)
    s.add_argument("--by", required=True, help="领取者: 成员ID或captain")
    s.set_defaults(fn=cmd_claim_task)

    s = sub.add_parser("complete-task", help="完成任务并写产出")
    s.add_argument("--team", required=True)
    s.add_argument("--task", required=True)
    s.add_argument("--output", default="", help="任务产出摘要")
    s.set_defaults(fn=cmd_complete_task)

    s = sub.add_parser("task-status", help="强制重置任务状态")
    s.add_argument("--team", required=True)
    s.add_argument("--task", required=True)
    s.add_argument("--status", required=True, choices=TASK_STATUSES)
    s.set_defaults(fn=cmd_task_status)

    s = sub.add_parser("send", help="投递直达消息到队友邮箱")
    s.add_argument("--team", required=True)
    s.add_argument("--from", dest="from_", required=True, help="发送者:成员ID或captain")
    s.add_argument("--to", required=True, help="接收者:成员ID或captain")
    s.add_argument("--body", required=True)
    s.set_defaults(fn=cmd_send)

    s = sub.add_parser("read-mail", help="读取邮箱")
    s.add_argument("--team", required=True)
    s.add_argument("--who", required=True, help="成员ID或captain")
    s.add_argument("--mark-read", action="store_true")
    s.set_defaults(fn=cmd_read_mail)

    s = sub.add_parser("status", help="团队全景(成员/任务/依赖/进度/事件)")
    s.add_argument("--team", required=True)
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("list-teams", help="列出团队(--all 含归档)")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_list_teams)

    s = sub.add_parser("archive", help="归档团队(磁盘移入archive/;常设团队需--force)")
    s.add_argument("--team", required=True)
    s.add_argument("--force", action="store_true", help="强制归档(含常设团队)")
    s.set_defaults(fn=cmd_archive)

    s = sub.add_parser("stale-check", help="检测领取超时的任务(子Agent失联时claimed永挂)")
    s.add_argument("--team", required=True)
    s.add_argument("--hours", type=float, default=3.0, help="超时阈值(小时),默认3;任务级timeoutHours优先")
    s.add_argument("--auto-reset", action="store_true",
                   help="超时任务自动重置为pending(清owner释放认领权)+系统通知队长邮箱")
    s.set_defaults(fn=cmd_stale_check)

    s = sub.add_parser("check-bindings", help="成员绑定/最后活跃/唤醒可行性检查(失联恢复证据)")
    s.add_argument("--team", required=True)
    s.add_argument("--hours", type=float, default=3.0, help="判定stale的参考超时阈值(小时)")
    s.set_defaults(fn=cmd_check_bindings)

    s = sub.add_parser("recovery-report", help="跨会话启动扫描全部活动团队→待队长处理清单")
    s.add_argument("--hours", type=float, default=3.0, help="判定stale的参考超时阈值(小时)")
    s.set_defaults(fn=cmd_recovery_report)

    s = sub.add_parser("captain-decision", help="队长决策结构化留痕(trigger/evidence/conclusion/action)")
    s.add_argument("--team", required=True)
    s.add_argument("--trigger", default="", help="什么触发了该决策")
    s.add_argument("--evidence", default="", help="依据的证据(成员报告/数据/规则),可多行")
    s.add_argument("--conclusion", default="", help="判断结论")
    s.add_argument("--action", default="", help="采取的行动;留空=无操作")
    s.add_argument("--from", dest="from_", default="", help="决策人(默认captain;成员代写时可用成员ID)")
    s.add_argument("--list", action="store_true", help="列出历史决策(最近--limit条)")
    s.add_argument("--limit", type=int, default=10, help="--list时返回条数")
    s.set_defaults(fn=cmd_captain_decision)

    s = sub.add_parser("query-memory", help="成员直读知识库(记忆索引关键词检索,无需队长中转)")
    s.add_argument("--keyword", required=True, help="检索关键词(标题/摘要子串,不区分大小写)")
    s.add_argument("--memory-dir", default=os.environ.get("TULONG_MEMORY_DIR",
                    "C:/Users/Administrator/.zcode/cli/memories/projects/default-ca541471c43f8e90/memory"),
                   help="记忆索引目录(默认当前项目记忆;可用环境变量TULONG_MEMORY_DIR覆盖)")
    s.add_argument("--detail", action="store_true", help="命中索引后读取正文中的关键词行")
    s.add_argument("--limit", type=int, default=5, help="返回条数/正文行数")
    s.set_defaults(fn=cmd_query_memory)

    s = sub.add_parser("dashboard-data", help="看板数据JSON(Node-RED面板消费)")
    s.set_defaults(fn=cmd_dashboard_data)

    return p


def main():
    a = build_parser().parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
