#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
discipline_stats.py — 纪律违规每日统计器（A6 纪律看板核心，2026-08-16 落地）
=====================================================================
四类违规口径（主人 8/16 裁决，与 L4 risk-engineer 笔记一致）：
  批量    = R3/R8（execution_guard 硬上限 3 只/分化日 2 只；pre_trade_check_v2 提示口径）
  超价    = BUY 成交价 > 30 元（S-3 铁律，中恒电气 44.77 案例；含裸奔未过闸补漏）
  非主线  = admit 无信号源/非白名单 + 不在今日作战清单（S-6）
  同分钟  = 观察性指标（8/14 主人裁决同一分钟可多笔，不判违规；单分钟≥3 标黄 ≥5 标红）

双源交叉（弥补裸奔日违规不进 precheck 的漏洞，8/13 裸奔 20 笔 precheck 仅 1 次）：
  源A guard_precheck_log（attribution\attribution.db）按 rejects 文本分类
  源B trade_records（同库）当日 BUY 成交：price>30 超价 / code∉作战清单 非主线 / 无 precheck PASS 裸奔

输出：
  D:/zcodesj/reports/discipline_YYYYMMDD.json   （机器可读，3019 看板直读）
  D:/zcodesj/reports/discipline_YYYYMMDD.md     （周会复盘用）
落库：
  D:/zcodesj/attribution.db（根，3019 看板库）discipline_daily 表（date PRIMARY KEY，幂等覆盖）

用法：
  python discipline_stats.py --date 20260813    # 统计指定日（可回补历史）
  python discipline_stats.py                    # 统计今日
  python discipline_stats.py --rebuild          # 回补 20260813 至今全部历史基线
  python discipline_stats.py --weekly           # 生成近7交易日周报 discipline_weekly_*.md
退出码：0=正常 1=违规>0(ERROR级) 2=参数错误/异常
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, date, timedelta

ATTR_BIZ_DB = r"D:\zcodesj\attribution\attribution.db"   # 归因业务库（guard_precheck_log/trade_records）
ATTR_DASH_DB = r"D:\zcodesj\attribution.db"              # 看板库（discipline_daily 落库，3019 直读）
REPORT_DIR = r"D:\zcodesj\reports"
PLAN_DIR = r"D:\zcodesj\trade_plans"
PLAN_FILE = r"D:\zcodesj\trade_plans\trade_plan_latest.md"
MAX_PRICE = 30.0
MINUTE_PEAK_WARN = 3   # 同分钟≥3 笔标黄
MINUTE_PEAK_ERR = 5    # 同分钟≥5 笔标红
# 2026-08-28 E68 口径治理: 同分钟峰值只统计交易时段内分钟(09:25-15:05, 含集合竞价与收盘),
# 时段外分钟=盘后回填产物(fill_confirm_monitor 批量落库 created_at 同分钟), 不计峰值单列计数
TRADE_WINDOW_START_MIN = 9 * 60 + 25
TRADE_WINDOW_END_MIN = 15 * 60 + 5

# ── 违规分类关键词（与 pre_trade_check_v2.py / m5_impulse_blocker.py 文本对齐）──
K_BATCH = ("R3批量", "R8分化", "批量买")
K_OVERRICE = ("元上限",)   # 超价铁律唯一文本标记（"股价 X > 30 元上限"），精确匹配防误分类
K_OFFPLAN = ("admit闸门", "作战清单", "白名单", "信号源")
# 注意: admit 无信号源属"非主线"分类（S-6 无信号源不买）

def norm6(code: str) -> str:
    c = str(code or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
            break
    return re.sub(r"\D", "", c).zfill(6)[-6:]


def _codes_from_md_table(text: str) -> set:
    """作战清单/补单计划 md 表格行 '| 600961 |' 或 '| 601009 南京银行 |' → 代码集合。"""
    codes = set()
    for m in re.finditer(r"\|\s*(\d{6})\s*[|\s]", text):
        codes.add(m.group(1))
    return codes


def load_plan_codes(date_str: str | None = None) -> set:
    """合法买入代码多源并集（2026-08-28 E68 口径治理 + 逐次扩源）。

    非主线判定的分母口径 = ①当日作战清单 trade_plan_{date}.md（缺则 latest）
    ∪ ②当日买入白名单 buy_whitelist_{date}.csv（R26 名单制唯一权威源）
    ∪ ③当日补单计划 rebuy_plan_{date}.md（S-A 补单为白名单延后执行，设计内行为）
    ∪ ④revival/batch_exec 系列计划 JSON（2026-10-28, 34 笔误判 offplan 适配）
    ∪ ⑤executor 台账 source=bp_ep_core 主令通道单（2026-10-29 主人裁决, SOP一百八十三）。
    此前仅读 trade_plan_latest.md，8/27 补单 9 笔全被误判 offplan(22) 致 level=ERROR 误报。
    日期态优先，历史 --rebuild 亦按当日文件回放。
    """
    candidates: list[str] = []
    if date_str:
        candidates.append(os.path.join(PLAN_DIR, f"trade_plan_{date_str}.md"))
        candidates.append(os.path.join(REPORT_DIR, f"buy_whitelist_{date_str}.csv"))
        candidates.append(os.path.join(REPORT_DIR, f"rebuy_plan_{date_str}.md"))
    candidates.append(PLAN_FILE)
    codes: set = set()
    for path in candidates:
        try:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except Exception:
            continue
        if path.endswith(".csv"):
            for line in content.splitlines()[1:]:
                first = line.split(",")[0].strip().strip('"')
                if re.fullmatch(r"\d{6}", first):
                    codes.add(first)
        else:
            codes |= _codes_from_md_table(content)
    # 2026-10-28 RevivalExecAll_0923 合一链适配(主人令「ALL listed ×100股 0923提交」架构):
    # revival 系列计划 JSON(code 字段)为合法买入源——此前三源不含 revival 计划,
    # 今晨 revival 系 34 笔被误判 offplan(8/28 E68 误报同构); 今日三计划已全部 .executed 消费,
    # 历史日回放用当日文件, 幂等无害。
    data_dir = os.path.join(os.path.dirname(REPORT_DIR), "data")
    suffixes = (f"_{date_str}.json", f"_{date_str}.executed", ".json", ".executed") if date_str \
        else (".json", ".executed")
    for stem in ("revival_batch_plan", "revival_w2_batch_plan", "revival_wave23_batch_plan",
                 "batch_exec_breakout", "batch_exec_breakout_a"):
        for suffix in suffixes:
            p = os.path.join(data_dir, f"{stem}{suffix}")
            try:
                if not os.path.exists(p):
                    continue
                arr = json.load(open(p, encoding="utf-8"))
                for it in arr if isinstance(arr, list) else []:
                    c6 = str(it.get("code", "")).zfill(6)[-6:]
                    if re.fullmatch(r"\d{6}", c6):
                        codes.add(c6)
            except Exception:
                continue
    # 2026-10-29 U100BP旗舰通道纳入(主人裁决「U100BP通道纳入纪律计划源口径」):
    # executor_orders.jsonl 中 source=bp_ep_core 的主人令「选股→复验→模拟盘买入」流
    # (SOP一百八十三)即计划凭证——通道单自带信号日/combo分/主人令越闸注记, 由 selection
    # 会话按主人令逐笔落台账。此前多源不含该通道, 10/27 五笔旗舰单被误判 offplan(22)
    # (8/28 E68 与 10/28 revival 误报同构)。
    try:
        eo_path = os.path.join(data_dir, "executor_orders.jsonl")
        with open(eo_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if "bp_ep_core" not in str(o.get("source", "")):
                    continue
                if date_str and str(o.get("trade_date", "")) != date_str:
                    continue
                c6 = norm6(o.get("code", ""))
                if re.fullmatch(r"\d{6}", c6):
                    codes.add(c6)
    except Exception:
        pass
    return codes


def classify_reject_text(text: str) -> str:
    """将一条闸门拒绝原因归入四类（批量/超价/非主线/其他）。"""
    if not text:
        return "other"
    for k in K_BATCH:
        if k in text:
            return "batch"
    for k in K_OFFPLAN:
        if k in text:
            return "offplan"
    for k in K_OVERRICE:
        if k in text:
            return "overprice"
    return "other"


def parse_minute(created_at: str, notes: str = "") -> str | None:
    """优先从 notes 的 filled_at 解析真实成交分钟，其次 created_at。"""
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", notes or "")
    if m:
        return m.group(1)[11:16]
    m = re.search(r"(\d{2}:\d{2})", created_at or "")
    return m.group(1) if m else None


def _order_fill_rate(date_str: str) -> dict:
    """当日委托成交率（E53/E66/E76 监控，2026-08-28）：读 data/open_orders.jsonl。
    filled=全部成交(含部分>0), total=当日全部 BUY 委托, expired=收盘失效。"""
    path = r"D:\zcodesj\data\open_orders.jsonl"
    if not os.path.exists(path):
        return {"filled": 0, "total": 0, "expired": 0, "rate": None}
    filled = expired = total = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if (r.get("trade_date") or "") != date_str:
                continue
            total += 1
            st = r.get("status", "")
            conf = int(r.get("confirmed") or 0)
            if st == "FILLED" or (st == "PARTIAL" and conf > 0):
                filled += 1
            elif st == "EXPIRED_READ":
                expired += 1
    return {"filled": filled, "total": total, "expired": expired,
            "rate": round(filled / total * 100, 1) if total else None}


def cmd_daily(date_str: str) -> dict:
    """统计单日纪律违规。"""
    plan_codes = load_plan_codes(date_str)
    con = sqlite3.connect(ATTR_BIZ_DB)
    con.row_factory = sqlite3.Row

    # ── 源A: guard_precheck_log 拒绝分类 ──
    # 2026-08-28 P1a 配套修复：排除 source='pre_sell' 卖出判定行（与买入闸门同表），
    # 否则 pre_sell 行会虚增 precheck_total 并把同票裸奔买入误判合规（虚假合规）。
    prechecks = con.execute(
        "SELECT code, verdict, rejects, warnings FROM guard_precheck_log "
        "WHERE trade_date=? AND COALESCE(source,'') != 'pre_sell'",
        (date_str,),
    ).fetchall()
    precheck_total = len(prechecks)
    passed_codes = {norm6(r["code"]) for r in prechecks if r["verdict"] != "REJECT"}
    cls = {"batch": 0, "overprice": 0, "offplan": 0, "other": 0}
    reject_detail = []
    for r in prechecks:
        if r["verdict"] != "REJECT":
            continue
        try:
            reasons = json.loads(r["rejects"] or "[]")
        except Exception:
            reasons = []
        for reason in reasons:
            c = classify_reject_text(reason)
            cls[c] += 1
            reject_detail.append({"code": norm6(r["code"]), "type": c, "reason": reason[:120]})

    # 2026-09-02 特批豁免: 宪法§10.4 名单外逐笔特批(manual_confirmed)是 sanctioned 通道,
    # 原口径把特批成交计入 offplan → total_viol≥4 判 ERROR 级 → 闸门冒烟 FAIL(9/2 五笔特批实证)。
    # 豁免集合 = 当日 manual_confirmed 且非 REJECT 的闸门记录(仅 REJECT 的特批尝试不豁免);
    # 特批成交单列 manual 透明呈现, 不计 offplan 违规。
    manual_codes = {
        norm6(r["code"]) for r in con.execute(
            "SELECT code, verdict FROM guard_precheck_log "
            "WHERE trade_date=? AND source='manual_confirmed'", (date_str,)).fetchall()
        if r["verdict"] != "REJECT"
    }

    # 2026-09-14 闸门反事实验证( reports/gate_audit_20260914.md ): 触发追高类WARN
    # (R-S5/R31/R6)仍执行的成交 fwd5 胜率仅 7%(均值-5.62%, n=17), 是放行组亏损主力;
    # 剔除后放行组 fwd5 胜率 43%→48%。现行口径下 WARN 放行=合规, 纪律统计不可见 →
    # 新增透明口径 chase_warn(不计违规/不改 level), 供主人裁决是否升级为约束。
    CHASE_WARN_GATES = ("R-S5", "R31", "R6")
    chase_warn_codes: dict[str, set] = {}
    for r in prechecks:
        if r["verdict"] == "REJECT":
            continue
        try:
            warns = json.loads(r["warnings"] or "[]")
        except Exception:
            continue
        for w in warns:
            # [fail-open] 前缀=数据缺失标注(9/14 B3落库规范化), 历史行无前缀用 in 兜底
            if w.startswith("[fail-open]") or "fail-open" in w:
                continue
            for g in CHASE_WARN_GATES:
                if w.startswith(g):
                    chase_warn_codes.setdefault(norm6(r["code"]), set()).add(g)


    # ── 源B: trade_records 当日 BUY 成交违规扫描 ──
    # 2026-08-26 O3: 新增"通道绕过(ungated)"判定——成交当日有 precheck 记录但落库
    # subsystems 非 trade_executor（reconcile 补录/另链路直连MCP），executor 双层防线
    # 未参与 = 违规（E31: 8/24-25 买入 28/33 笔此形态, limit_price 全失效）。
    fills = con.execute(
        "SELECT code, name, action, price, created_at, notes, list_membership, subsystems "
        "FROM trade_records WHERE trade_date=? ORDER BY id", (date_str,),
    ).fetchall()
    buy_total = 0
    naked = []
    ungated = []
    chase_warn = []
    overprice = []
    offplan = []
    manual = []  # 特批成交(宪法§10.4 manual_confirmed), 不计违规仅透明呈现
    minutes: dict[str, int] = {}
    afterhours = 0
    for f in fills:
        code6 = norm6(f["code"])
        if f["action"] != "BUY":
            continue
        buy_total += 1
        price = f["price"] or 0
        sub = (f["subsystems"] or "").strip()
        # 超价判定（price<=0 跳过，8/15 缺价导入场景）
        if price > MAX_PRICE:
            overprice.append({"code": code6, "name": f["name"], "price": price})
        # 非主线判定（清单缺失时跳过，仅 admits 判定）
        # 2026-09-02: manual_confirmed 特批成交单列 manual, 不计入 offplan 违规(透明见报告)
        if plan_codes and code6 not in plan_codes:
            if code6 in manual_codes:
                manual.append({"code": code6, "name": f["name"], "price": price})
            else:
                offplan.append({"code": code6, "name": f["name"], "price": price})
        # 裸奔判定（当日无 precheck PASS 记录）
        if code6 not in passed_codes:
            naked.append({"code": code6, "name": f["name"], "price": price})
        elif "trade_executor" not in sub:
            # 有 precheck 但未经 executor 端到端（通道绕过, E31）
            ungated.append({"code": code6, "name": f["name"], "price": price,
                            "subsystems": sub or "(空)"})
        # 追高WARN执行透明口径(2026-09-14): 触发 R-S5/R31/R6 WARN 仍执行, 仅呈现
        if code6 in chase_warn_codes:
            chase_warn.append({"code": code6, "name": f["name"], "price": price,
                               "gates": sorted(chase_warn_codes[code6])})
        # 同分钟观察（notes filled_at 优先）
        # 2026-08-28 E68: 交易时段(09:25-15:05)外分钟=盘后回填产物(fill_confirm_monitor
        # 批量落库同分钟 created_at), 不计入峰值; 单列 afterhours 计数保持透明。
        mm = parse_minute(f["created_at"], f["notes"])
        if mm:
            _m = int(mm[:2]) * 60 + int(mm[3:5])
            if TRADE_WINDOW_START_MIN <= _m <= TRADE_WINDOW_END_MIN:
                minutes[mm] = minutes.get(mm, 0) + 1
            else:
                afterhours += 1
    con.close()

    fill_rate = _order_fill_rate(date_str)

    compliant = buy_total - len(naked) - len(ungated)
    compliance = round(compliant / buy_total * 100, 1) if buy_total else 100.0
    minute_peak = max(minutes.values()) if minutes else 0
    minute_peak_min = max(minutes, key=minutes.get) if minutes else ""
    minute_level = "ok" if minute_peak < MINUTE_PEAK_WARN else ("warn" if minute_peak < MINUTE_PEAK_ERR else "err")

    total_viol = len(overprice) + len(offplan) + cls["batch"]
    level = "ERROR" if (naked or ungated or total_viol >= 4 or compliance < 80) else (
        "WARN" if (total_viol > 0 or compliance < 95) else "INFO")
    # 同分钟观察不改变级别（主人裁决可多笔），仅提示

    result = {
        "date": date_str,
        "level": level,
        "buy_total": buy_total,
        "precheck_calls": precheck_total,
        "naked_buys": len(naked),
        "ungated_buys": len(ungated),
        "compliance_rate": compliance,
        "violations": {
            "batch_reject": cls["batch"],          # 源A 闸门拦截的批量违规
            "overprice": len(overprice) + cls["overprice"],   # 源B成交 + 源A拦截
            "offplan": len(offplan) + cls["offplan"],
            "manual_confirmed": len(manual),  # 特批成交笔数(sanctioned 通道, 不计违规)
            "other_reject": cls["other"],
            "minute_peak": minute_peak,
            "minute_peak_min": minute_peak_min,
            "minute_level": minute_level,
            "afterhours_backfill_rows": afterhours,   # 盘后回填行数(不计入峰值, E68)
            "order_fill_rate": fill_rate.get("rate"),  # 当日委托成交率(E53/E66/E76)
            "order_fill_expired": fill_rate.get("expired"),
            "chase_warn_buys": len(chase_warn),  # 追高WARN执行笔数(9/14透明口径, 不计违规)
        },
        "plan_sources": "trade_plan+buy_whitelist+rebuy_plan+revival_batch+u100bp旗舰(E68口径+20261028/29扩源)",
        "naked_detail": naked,
        "ungated_detail": ungated,
        "overprice_detail": overprice,
        "offplan_detail": offplan,
        "manual_detail": manual,
        "chase_warn_detail": chase_warn,  # 追高WARN执行明细(9/14, 仅透明呈现)
        "reject_detail": reject_detail,
        "plan_codes_count": len(plan_codes),
    }

    # ── 违规闭环物化（2026-08-26 O4, 宪法§12）: naked/ungated 自动记 violation_log ──
    try:
        import violation_log as vl
        if naked:
            vl.add_entry("naked_trade", "m1", "铁律1:无闸门不下单",
                         f"{date_str} 裸奔买入{len(naked)}笔: "
                         + ",".join(n["code"] for n in naked))
        if ungated:
            vl.add_entry("naked_trade", "m1", "铁律1:必须走trade_executor",
                         f"{date_str} 通道绕过{len(ungated)}笔(有precheck非executor): "
                         + ",".join(n["code"] for n in ungated))
    except Exception:
        pass  # 违规记录失败不阻断统计主流程

    # ── 落库看板库（幂等覆盖）──
    dcon = sqlite3.connect(ATTR_DASH_DB)
    dcon.execute("""CREATE TABLE IF NOT EXISTS discipline_daily (
        date TEXT PRIMARY KEY,
        level TEXT, buy_total INTEGER, precheck_calls INTEGER,
        naked_buys INTEGER, compliance_rate REAL,
        batch_reject INTEGER, overprice INTEGER, offplan INTEGER,
        other_reject INTEGER, minute_peak INTEGER, minute_peak_min TEXT, minute_level TEXT,
        violations_json TEXT, created_at TEXT)""")
    # 2026-08-26 O3: ungated_buys 列（通道绕过, E31）——旧库幂等补列
    try:
        dcon.execute("ALTER TABLE discipline_daily ADD COLUMN ungated_buys INTEGER")
    except sqlite3.OperationalError:
        pass  # 列已存在
    dcon.execute(
        "INSERT OR REPLACE INTO discipline_daily "
        "(date,level,buy_total,precheck_calls,naked_buys,compliance_rate,"
        "batch_reject,overprice,offplan,other_reject,minute_peak,minute_peak_min,"
        "minute_level,violations_json,created_at,ungated_buys) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (date_str, level, buy_total, precheck_total, len(naked), compliance,
         cls["batch"], len(overprice) + cls["overprice"], len(offplan) + cls["offplan"],
         cls["other"], minute_peak, minute_peak_min, minute_level,
         json.dumps(result, ensure_ascii=False), datetime.now().isoformat(timespec="seconds"),
         len(ungated)))
    dcon.commit()
    dcon.close()

    # ── 输出 json + md ──
    os.makedirs(REPORT_DIR, exist_ok=True)
    jpath = os.path.join(REPORT_DIR, f"discipline_{date_str}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    mpath = os.path.join(REPORT_DIR, f"discipline_{date_str}.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(f"# 纪律违规统计 {date_str}  等级: {level}\n\n")
        f.write("| 指标 | 数值 |\n|---|---|\n")
        f.write(f"| 买入成交 | {buy_total} 笔 |\n| precheck调用 | {precheck_total} 次 |\n")
        f.write(f"| **裸奔买入** | **{len(naked)} 笔** |\n| **通道绕过(有闸门非executor)** | **{len(ungated)} 笔** |\n")
        f.write(f"| 合规率(分母=全量成交) | {compliance:.1f}% |\n")
        f.write(f"| 批量(闸门拦截) | {cls['batch']} |\n| 超价(成交+拦截) | {len(overprice)+cls['overprice']} |\n")
        f.write(f"| 非主线(成交+拦截) | {len(offplan)+cls['offplan']} |\n")
        f.write(f"| 主人特批(宪法§10.4,不计违规) | {len(manual)} 笔 |\n")
        f.write(f"| 同分钟峰值 | {minute_peak} 笔 @ {minute_peak_min} ({minute_level}) |\n")
        _fr = fill_rate.get("rate")
        _fr_s = f"{_fr}% (成交{fill_rate.get('filled')}/{fill_rate.get('total')}委托, 过期{fill_rate.get('expired')})" \
            if _fr is not None else "N/A (当日无委托台账)"
        f.write(f"| 挂单成交率(当日委托) | {_fr_s} |\n")
        if afterhours:
            f.write(f"| 盘后回填行(不计入峰值) | {afterhours} 行 |\n")
        f.write(f"| 非主线口径 | 作战清单∪白名单∪补单计划 三源并集 |\n\n")
        if naked:
            f.write("## 裸奔明细\n\n| 代码 | 名称 | 价格 |\n|---|---|---|\n")
            for n in naked:
                f.write(f"| {n['code']} | {n['name']} | {n['price']} |\n")
        if ungated:
            f.write("\n## 通道绕过明细（有precheck但未经executor, E31口径）\n\n| 代码 | 名称 | 价格 | subsystems |\n|---|---|---|---|\n")
            for n in ungated:
                f.write(f"| {n['code']} | {n['name']} | {n['price']} | {n['subsystems']} |\n")
        if overprice:
            f.write("\n## 超价明细\n\n| 代码 | 名称 | 价格 |\n|---|---|---|\n")
            for n in overprice:
                f.write(f"| {n['code']} | {n['name']} | {n['price']} |\n")
        if offplan:
            f.write("\n## 非主线明细（清单外成交）\n\n| 代码 | 名称 | 价格 |\n|---|---|---|\n")
            for n in offplan:
                f.write(f"| {n['code']} | {n['name']} | {n['price']} |\n")
        if manual:
            f.write("\n## 主人特批成交（manual_confirmed，宪法§10.4 sanctioned 通道，不计违规）\n\n| 代码 | 名称 | 价格 |\n|---|---|---|\n")
            for n in manual:
                f.write(f"| {n['code']} | {n['name']} | {n['price']} |\n")
        if minute_level != "ok":
            f.write(f"\n## ⚠️ 同分钟观察: 峰值 {minute_peak} 笔 @ {minute_peak_min}（8/14 主人裁决可多笔，仅提示）\n")
    result["report"] = mpath
    return result


def cmd_weekly(end_date: str = None) -> dict:
    """生成近 7 交易日周报（从 discipline_daily 聚合）。"""
    end = end_date or datetime.now().strftime("%Y%m%d")
    dcon = sqlite3.connect(ATTR_DASH_DB)
    dcon.row_factory = sqlite3.Row
    rows = dcon.execute(
        "SELECT * FROM discipline_daily WHERE date<=? ORDER BY date DESC LIMIT 7", (end,)
    ).fetchall()
    dcon.close()
    rows = list(reversed(rows))
    if not rows:
        print("无纪律数据（先跑 --rebuild 或 --date 回补）")
        return {"error": "no data"}
    tot = {"buy": sum(r["buy_total"] for r in rows), "naked": sum(r["naked_buys"] for r in rows),
           "ungated": sum((r["ungated_buys"] or 0) if "ungated_buys" in r.keys() else 0 for r in rows),
           "batch": sum(r["batch_reject"] for r in rows), "over": sum(r["overprice"] for r in rows),
           "off": sum(r["offplan"] for r in rows), "other": sum(r["other_reject"] for r in rows)}
    comp = round((tot["buy"] - tot["naked"] - tot["ungated"]) / tot["buy"] * 100, 1) if tot["buy"] else 100.0
    summary = {
        "end_date": end, "days": len(rows),
        "total_buy": tot["buy"], "total_naked": tot["naked"], "total_ungated": tot["ungated"],
        "compliance_rate": comp,
        "batch": tot["batch"], "overprice": tot["over"], "offplan": tot["off"], "other": tot["other"],
        "daily": [dict(r) for r in rows],
    }
    mpath = os.path.join(REPORT_DIR, f"discipline_weekly_{end}.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(f"# 纪律周报 · 至 {end}（{len(rows)} 交易日）\n\n")
        f.write("| 指标 | 数值 |\n|---|---|\n")
        f.write(f"| 买入总笔数 | {tot['buy']} |\n| 裸奔总数 | {tot['naked']} |\n| 通道绕过总数 | {tot['ungated']} |\n| **周合规率** | **{comp:.1f}%** |\n")
        f.write(f"| 批量(闸门拦截) | {tot['batch']} |\n| 超价 | {tot['over']} |\n| 非主线 | {tot['off']} |\n")
        f.write(f"| 其他闸门拒绝 | {tot['other']} |\n\n")
        f.write("## 逐日明细\n\n| 日期 | 等级 | 买入 | 裸奔 | 合规率 | 批量 | 超价 | 非主线 | 同分钟峰值 |\n|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['date']} | {r['level']} | {r['buy_total']} | {r['naked_buys']} | "
                    f"{r['compliance_rate']:.1f}% | {r['batch_reject']} | {r['overprice']} | "
                    f"{r['offplan']} | {r['minute_peak']}@{r['minute_peak_min']} |\n")
    summary["report"] = mpath
    return summary


def cmd_rebuild() -> dict:
    """回补 20260813 至今全部历史基线。"""
    con = sqlite3.connect(ATTR_BIZ_DB)
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT trade_date FROM trade_records WHERE trade_date>='20260813' "
        "UNION SELECT DISTINCT trade_date FROM guard_precheck_log WHERE trade_date>='20260813' "
        "ORDER BY 1")]
    gd = [r[0] for r in con.execute(
        "SELECT DISTINCT trade_date FROM guard_precheck_log WHERE trade_date>='20260813' ORDER BY 1")]
    con.close()
    all_dates = sorted(set(dates) | set(gd))
    out = {}
    for d in all_dates:
        out[d] = cmd_daily(d)
    return {"rebuilt": len(all_dates), "dates": all_dates}


def main() -> int:
    p = argparse.ArgumentParser(description="纪律违规每日统计器")
    p.add_argument("--date", default=None, help="YYYYMMDD，默认今日")
    p.add_argument("--rebuild", action="store_true", help="回补 20260813 起历史基线")
    p.add_argument("--weekly", action="store_true", help="生成周报")
    a = p.parse_args()

    if a.rebuild:
        out = cmd_rebuild()
        print(json.dumps({"rebuilt": out["rebuilt"]}, ensure_ascii=False))
        return 0
    if a.weekly:
        out = cmd_weekly()
        print(json.dumps({k: out[k] for k in ("end_date", "days", "compliance_rate", "report") if k in out},
                          ensure_ascii=False))
        return 0
    date_str = a.date or datetime.now().strftime("%Y%m%d")
    out = cmd_daily(date_str)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if out["level"] == "ERROR" else 0


if __name__ == "__main__":
    raise SystemExit(main())
