#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""violation_log — 宪法 §12 违规闭环物化（2026-08-26 O4, E37 收口）
=====================================================================
背景: 宪法 §12 要求 violation_log.jsonl 承载违规闭环（首警/二打回/三上报），
但该文件自宪法生效以来从未存在（8/26 自省 E37）。本模块为唯一物化入口。

五类违规（宪法 §12）:
  naked_trade      裸奔/绕闸门（含通道绕过 executor, E31）
  fake_evidence    伪造证据/伪健康
  no_reasoning_gate 未过推理闸即实现
  retired_route    触碰已否定路线
  doc_unsync       文档未回写

处罚梯度: 同 (type) 近30日 第1次=warn(首警) 第2次=reject(打回) ≥3次=escalate(上报主人)

用法:
  python violation_log.py add --type naked_trade --actor m1 --rule "铁律1" --detail "13笔通道绕过"
  python violation_log.py check          # 22:00 核验: 文件存在性 + 近7日计数
  python violation_log.py list [--days 7]

退出码: add=0正常/2参数错; check=0存在/1缺失或近7日有未消化违规
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta

LOG_PATH = r"D:\zcodesj\violation_log.jsonl"
VALID_TYPES = ("naked_trade", "fake_evidence", "no_reasoning_gate", "retired_route", "doc_unsync")
HEARTBEAT_TYPE = "heartbeat"  # 心跳位(2026-10-06 三尾巴③): 非违规类型, 统计消费方按五类枚举不受污染


def _load() -> list[dict]:
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def add_entry(vtype: str, actor: str, rule: str, detail: str) -> dict:
    """追加一条违规记录（幂等键: 事件日+type+rule，同事件不重复记）。

    2026-08-29 修复：幂等键原为"登记日+type+rule"，跨日重跑旧日统计
    （discipline_stats --date YYYYMMDD / --rebuild）会穿透重复登记同案
    （8/28 对 8/24/8/26 旧案重复登记2条、repeat 虚高即此）。现幂等键取
    detail 前 8 位事件日（YYYYMMDD），无日期前缀的 detail 退回登记日。
    """
    if vtype not in VALID_TYPES:
        raise ValueError(f"未知违规类型 {vtype}, 合法: {VALID_TYPES}")
    today = datetime.now().strftime("%Y-%m-%d")
    _ev8 = detail[:8] if len(detail) >= 8 and detail[:8].isdigit() else None
    entries = _load()
    if any(e.get("type") == vtype and e.get("rule") == rule and
           (e.get("date") == today
            or (_ev8 and str(e.get("detail", ""))[:8] == _ev8))
           for e in entries):
        return {"appended": False, "reason": "同事件同规则已记录(幂等)"}
    # 处罚梯度: 同 type 近30日计数
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    prior = sum(1 for e in entries if e.get("type") == vtype and e.get("date", "") >= cutoff)
    action = "warn" if prior == 0 else ("reject" if prior == 1 else "escalate")
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "date": today,
             "type": vtype, "actor": actor, "rule": rule, "detail": detail[:500],
             "repeat_30d": prior + 1, "action": action}
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"appended": True, **entry}


def write_heartbeat() -> dict:
    """每日心跳位（2026-10-06 全维度自省三尾巴③，幂等键=当日）。

    背景（9/28 自省）: 真源长期零新增时无法区分「零违规」与「断写」——9/8 O-N2
    合并后 root 台账 9/24 起零新增，直到 9/28 自省才发现无法证伪。心跳行让断写
    可检测（最新心跳滞后≥2日=写链断，health_check 每日宪法段调用本函数）。
    心跳不进 VALID_TYPES：check()/discipline_stats 等消费方按五类枚举统计，不受污染。
    """
    entries = _load()
    today = datetime.now().strftime("%Y-%m-%d")
    if any(e.get("type") == HEARTBEAT_TYPE and e.get("date") == today for e in entries):
        return {"appended": False, "reason": "当日心跳已存在(幂等)", "date": today}
    cutoff7 = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    total_viol = sum(1 for e in entries if e.get("type") in VALID_TYPES)
    recent7 = sum(1 for e in entries
                  if e.get("type") in VALID_TYPES and e.get("date", "") >= cutoff7)
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "date": today,
             "type": HEARTBEAT_TYPE, "actor": "system", "rule": "心跳位",
             "detail": f"每日心跳 违规total={total_viol} 近7日={recent7}"}
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"appended": True, **entry}


def heartbeat_lag_days() -> int | None:
    """最新心跳距今日历日数（无心跳返回 None），供 health_check 断链检测。"""
    hb = [e for e in _load() if e.get("type") == HEARTBEAT_TYPE and e.get("date")]
    if not hb:
        return None
    last = max(e["date"] for e in hb)
    return (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days


def check() -> dict:
    """22:00 十三合一核验入口: 文件存在 + 近7日违规计数（有违规 → 退出1 提示当周消化）。

    2026-10-06 三尾巴③: recent 只计五类违规行——心跳行不入 pending_digest，
    否则每日心跳会让「当周有未消化违规」恒真。
    """
    entries = _load()
    if not os.path.exists(LOG_PATH):
        return {"ok": False, "error": f"violation_log 不存在: {LOG_PATH}", "total": 0}
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recent = [e for e in entries
              if e.get("type", "") in VALID_TYPES and e.get("date", "") >= cutoff]
    out = {"ok": True, "total": len(entries), "recent_7d": len(recent),
           "by_type": {t: sum(1 for e in recent if e["type"] == t) for t in VALID_TYPES if any(e["type"] == t for e in recent)},
           "pending_digest": bool(recent)}
    lag = heartbeat_lag_days()
    if lag is not None:
        out["heartbeat_lag_days"] = lag
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="宪法§12 违规闭环 violation_log 工具")
    sub = p.add_subparsers(dest="cmd", required=True)
    ap_add = sub.add_parser("add")
    ap_add.add_argument("--type", required=True, choices=VALID_TYPES)
    ap_add.add_argument("--actor", default="system")
    ap_add.add_argument("--rule", required=True)
    ap_add.add_argument("--detail", default="")
    sub.add_parser("check")
    sub.add_parser("heartbeat")
    ap_list = sub.add_parser("list")
    ap_list.add_argument("--days", type=int, default=7)
    a = p.parse_args()

    if a.cmd == "add":
        out = add_entry(a.type, a.actor, a.rule, a.detail)
        print(json.dumps(out, ensure_ascii=False))
        return 0
    if a.cmd == "heartbeat":
        out = write_heartbeat()
        print(json.dumps(out, ensure_ascii=False))
        return 0
    if a.cmd == "check":
        out = check()
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out.get("ok") and not out.get("pending_digest") else 1
    # list
    cutoff = (datetime.now() - timedelta(days=a.days)).strftime("%Y-%m-%d")
    for e in [e for e in _load() if e.get("date", "") >= cutoff]:
        print(json.dumps(e, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
