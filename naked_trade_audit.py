#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘后裸奔对账审计器（2026-08-13 主人指令，五大工程之"闸门物理化"）。

目的：把"无闸门不下单"从约定变成有审计的约定。每个交易日盘后比对：
  数据源A：guard_precheck_log（当日闸门判定日志，pre_trade_check_v2 自动落库）
  数据源B：trade_records（当日实际成交，由 guarded_trade postrecord 或盘后 import 写入）

判定口径（宽松）：当日每笔 BUY 成交，若当日存在同 code 且 verdict != REJECT 的
precheck 记录，视为合规；否则标记为"裸奔单"。

用法：
  # 1. 盘后审计当日（默认今天）
  python naked_trade_audit.py audit
  # 审计指定日期
  python naked_trade_audit.py audit --date 20260813
  # 2. 盘后先导入 westock MCP 成交（知微会话拉MCP存json后调用）
  python naked_trade_audit.py import --file fills_20260813.json
  #   json 格式: [{"code":"002414","name":"高德红外","action":"buy","price":14.06,
  #                "volume":3600,"filled_at":"2026-08-13 09:36:10"}, ...]

输出：控制台 JSON + D:\\zcodesj\\reports\\naked_audit_YYYYMMDD.md
退出码：0=全部合规或无成交 / 1=存在无precheck成交

2026-10-23 定性对齐（主人10-21令: 模拟盘=研究训练场, 实盘风控机制不适用）：
  本审计的职能=事实层「无precheck在场成交」的计数/明细/知会, **检测职能不弱化**
  （agent侧绕行 trade_executor 仍属违规, 线索归纪律层/violation_log）。
  定性层：10-21令后, 无precheck成交默认为训练场主人主观单（主人主权动作, INFO）,
  不再表述为"铁律1/16违反"; exit=1 含义同步改为「存在无precheck成交（知会级）」。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
REPORT_DIR = r"D:\zcodesj\reports"
TOP20_DIR = r"D:\zcodesj\multifactor\reports\daily_top20"


def norm6(code: str) -> str:
    """统一为6位纯数字代码。"""
    c = code.strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[2:]
            break
    return c.split(".")[0].zfill(6)


def to_prefixed(code: str) -> str:
    """规范为带交易所前缀小写码（与 trade_executor.norm / buy_tracker 一致）。

    2026-08-24 P1修复: 此前导入写裸码，与 executor 写入的前缀码并存 →
    同一笔成交双记（code 不同绕过去重键）、holding_horizon 前缀匹配失败。
    """
    code6 = norm6(code)
    if code6.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + code6
    if code6.startswith(("8", "920", "430")):
        return "bj" + code6
    return "sz" + code6


def latest_top20() -> set[str]:
    """读取最新一期 bp+ep 核心名单（rank<=15，名单归属判定的唯一权威源）。

    2026-08-24 P1修复: 8/24 全市场文件(-n5000, 2762行)落地后 glob 最新命中全量文件，
    "全部股票都在Top20集合" → outside_list 恒为空、R26 名单外监控失效。
    现解析 rank 列截断 <=15（与白名单 BP_EP_TOP_N 同值）；无 rank 列的旧格式
    且行数>50 时拒绝使用（WARN）防再次击穿。
    """
    files = sorted(glob.glob(os.path.join(TOP20_DIR, "bp_ep_monthly_*.csv")))
    if not files:
        return set()
    codes: set[str] = set()
    with open(files[-1], encoding="utf-8-sig") as f:
        header = f.readline().strip().split(",")
        has_rank = "rank" in header
        for line in f:
            parts = line.strip().split(",")
            if not parts or not parts[0].isdigit():
                continue
            if has_rank:
                try:
                    rank = int(float(parts[header.index("rank")]))
                except (ValueError, IndexError):
                    continue
                if not (1 <= rank <= 15):
                    continue
            codes.add(parts[0].zfill(6))
    if not has_rank and len(codes) > 50:
        print(f"[WARN] 名单文件无rank列且行数异常({len(codes)})，疑似全量文件，名单外审计口径不可信")
    return codes


def cmd_import(file_path: str) -> dict:
    """导入当日成交json到 trade_records（幂等：同 code+date+price+action 去重）。
    自动判定 list_membership（bp_ep_top20 / outside_list）。
    2026-08-26 O20(E45收口): 导入前先按 executor 提交台账(executor_orders.jsonl)做归属
    分配——能唯一归属于当日 executor 委托的成交 → subsystems='trade_executor_async'
    （notes 带 order_id）；无法归属才标 'ungated_待标注'（真·通道外）。"""
    with open(file_path, encoding="utf-8") as f:
        fills = json.load(f)
    top20 = latest_top20()
    con = sqlite3.connect(ATTR_DB)
    inserted, skipped = 0, 0
    missing_ts = dup_count = 0  # 20261011批5尾: 缺时间戳/真重复分计(skipped_dup键此前混计四类)

    # ---- 第一遍: 解析与校验(逐行容错), 收集有效 fill 结构化行 ----
    parsed = []
    for r in fills:
        # 2026-08-24 P1修复: ①逐行容错(一条坏行此前会整体回滚)；②过滤非成交状态
        # (westock 存在"待成交"挂单, 不查 status 会把挂单记为成交→次日误报裸奔)；
        # ③价格兼容 costPrice(westock 原始持仓字段)。
        try:
            status = str(r.get("status") or r.get("orderStatus") or "").strip()
            # 20261027 B层(撤单误录根因封堵): status缺失由放行改fail-closed跳过——
            # 601169 8/24@5.09已撤单订单曾因status字段缺席被录入成交(trade_records id310),
            # 经closed_loop_sync FIFO产出幻影持仓行。宁可漏行人工补, 不录非成交状态。
            if status not in ("成交", "已成交", "filled", "FILLED",
                              "全部成交", "部分成交"):
                skipped += 1
                if not status:
                    print(f"[WARN] {r.get('code', '?')} 缺status字段, fail-closed跳过"
                          "(20261027撤单误录封堵); 如属真实成交请补status后重导")
                continue
            code6 = norm6(r["code"])
            # 字段兼容：westock MCP原始格式(direction/filledAt/quantity)与整理格式(action/filled_at/volume)
            action = (r.get("action") or r.get("direction") or "").upper()
            if action not in ("BUY", "SELL"):
                skipped += 1
                continue
            filled_at = r.get("filled_at") or r.get("filledAt") or ""
            volume = r.get("volume") or int(float(r.get("quantity") or 0))
            tdate = filled_at[:10].replace("-", "") or datetime.now().strftime("%Y%m%d")
            if not filled_at:
                # 20261011批5尾P3: 缺时间戳静默归属今日=跨日补导入会错挂日期, 留痕分计
                print(f"[WARN] {r.get('code', '?')} 缺filled_at, 暂归属今日({tdate}); 跨日补导入前请补时间戳")
                missing_ts += 1
            price = float(r.get("price") or r.get("costPrice") or 0)
            if price <= 0 or volume <= 0:
                skipped += 1
                continue
        except (KeyError, TypeError, ValueError) as e:
            skipped += 1
            print(f"[WARN] 跳过坏行({type(e).__name__}: {e}): {str(r)[:120]}")
            continue
        parsed.append({"r": r, "code6": code6, "action": action, "filled_at": filled_at,
                       "tdate": tdate, "volume": volume, "price": price})

    # ---- O20: executor 提交台账归属分配（原地标注 _match_order）----
    try:
        from executor_order_ledger import (SUB_EXEC_ASYNC, SUB_UNGATED,
                                           allocate_fills_to_orders, load_orders)
        probe_date = parsed[0]["tdate"] if parsed else None
        alloc_list = [{"code6": p["code6"], "action": p["action"], "price": p["price"],
                       "volume": p["volume"], "filled_at": p["filled_at"]} for p in parsed]
        allocate_fills_to_orders(alloc_list, load_orders(probe_date))
        for p, src in zip(parsed, alloc_list):
            if "_match_order" in src:
                p["_order"] = src["_match_order"]
    except Exception as e:
        print(f"[WARN] 台账归属判定不可用({type(e).__name__}: {e})——全部按 ungated 兜底")

    for p in parsed:
        r = p["r"]
        code6 = p["code6"]
        action = p["action"]
        filled_at = p["filled_at"]
        tdate = p["tdate"]
        volume = p["volume"]
        price = p["price"]
        membership = "bp_ep_top20" if code6 in top20 else "outside_list"
        store_code = to_prefixed(code6)  # 2026-08-24 P1修复: 存前缀码(规范见 to_prefixed)
        # 2026-09-25 守卫: 查重升级为归一化五元组(裸码+YYYYMMDD)——格式漂移(裸码vs前缀码/
        # 带横杠vs无横杠)曾击穿本查重致三源重复导入(202607批×3), 协议真源=attribution/trade_records_guard.py
        from attribution.trade_records_guard import exists as _guard_exists
        ex = _guard_exists(con, store_code, tdate, action, volume, price)
        if ex:
            skipped += 1
            dup_count += 1
            # 2026-09-23 T8(幂等查重): 五元组已存在=双轨重复记账, 跳过写入留痕
            print(f"  [skip] trade_records duplicate skipped: {store_code} {tdate} "
                  f"{action} {price} x{volume}(幂等查重T8)")
            continue
        owner = p.get("_order")
        subsystems = SUB_EXEC_ASYNC if owner else SUB_UNGATED
        # 2026-08-28 准则43/E72: 台账 note(卖出理由+C6判定)随归属下发，SELL 侧不再"理由日志缺失"
        notes = f"盘后审计导入 {filled_at}"
        if owner:
            notes += f" | async归属 order={owner.get('order_id')}"
            if owner.get("note"):
                notes += f" | {owner['note']}"
        con.execute(
            "INSERT INTO trade_records (code, name, trade_date, action, price, volume, amount, "
            "strategy_version, signal_type, subsystems, market_env, sector, notes, list_membership, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (store_code, r.get("name", ""), tdate, action, price, volume,
             round(price * volume, 2), tdate, "manual", subsystems,
             "", "", notes, membership,
             datetime.now().isoformat(timespec="seconds")),
        )
        inserted += 1
    con.commit()
    con.close()
    return {"inserted": inserted, "skipped_total": skipped, "skipped_dup": dup_count,
            "missing_ts": missing_ts,
            "reconciled_async": sum(1 for p in parsed if p.get("_order")),
            "ungated_imported": inserted - sum(1 for p in parsed if p.get("_order")),
            "top20_size": len(top20)}


def cmd_recon(file_path: str) -> dict:
    """只读对账（SF-08 探针）：westock fills JSON × trade_records diff，
    标 westock 有而台账无的成交（App端/通道外未导入的早期发现）。不写入。
    2026-09-13 落地：S1 接线（cron 拉 westock MCP）待 owner 裁决，diff 逻辑先行。"""
    with open(file_path, encoding="utf-8") as f:
        fills = json.load(f)
    con = sqlite3.connect(ATTR_DB)
    parsed, skipped = [], 0
    for r in fills:
        try:
            status = str(r.get("status") or r.get("orderStatus") or "").strip()
            # 20261027 B层(撤单误录根因封堵): status缺失由放行改fail-closed跳过——
            # 601169 8/24@5.09已撤单订单曾因status字段缺席被录入成交(trade_records id310),
            # 经closed_loop_sync FIFO产出幻影持仓行。宁可漏行人工补, 不录非成交状态。
            if status not in ("成交", "已成交", "filled", "FILLED",
                              "全部成交", "部分成交"):
                skipped += 1
                if not status:
                    print(f"[WARN] {r.get('code', '?')} 缺status字段, fail-closed跳过"
                          "(20261027撤单误录封堵); 如属真实成交请补status后重导")
                continue
            code6 = norm6(r["code"])
            action = (r.get("action") or r.get("direction") or "").upper()
            if action not in ("BUY", "SELL"):
                skipped += 1
                continue
            filled_at = r.get("filled_at") or r.get("filledAt") or ""
            volume = r.get("volume") or int(float(r.get("quantity") or 0))
            tdate = filled_at[:10].replace("-", "") or datetime.now().strftime("%Y%m%d")
            price = float(r.get("price") or r.get("costPrice") or 0)
            if price <= 0 or volume <= 0:
                skipped += 1
                continue
        except Exception as e:
            skipped += 1
            print(f"[WARN] recon跳过坏行({type(e).__name__}: {e}): {str(r)[:120]}")
            continue
        parsed.append({"code6": code6, "action": action, "tdate": tdate,
                       "volume": volume, "price": price,
                       "filled_at": filled_at, "name": r.get("name", "")})
    unrecorded = []
    for p in parsed:
        store_code = to_prefixed(p["code6"])
        ex = con.execute(
            "SELECT COUNT(*) FROM trade_records WHERE code=? AND trade_date=? AND "
            "price=? AND action=? AND volume=?",
            (store_code, p["tdate"], p["price"], p["action"], p["volume"]),
        ).fetchone()[0]
        if not ex:
            unrecorded.append(p)
    con.close()
    return {"fills_total": len(parsed), "unrecorded": len(unrecorded),
            "unrecorded_detail": unrecorded, "skipped": skipped}


def cmd_audit(date: str) -> dict:
    # 2026-09-10 加固: --date 直通报告落盘路径, 非法值会造成路径穿越式乱写
    # (Mimosa 全量扫描复核时对 date 面加固)
    if not (isinstance(date, str) and len(date) == 8 and date.isdigit()):
        return {"error": f"非法 --date: {date!r}（须为 8 位数字 YYYYMMDD）"}
    con = sqlite3.connect(ATTR_DB)
    con.row_factory = sqlite3.Row
    # 当日成交
    fills = con.execute(
        "SELECT code, name, action, price, volume, amount, subsystems, list_membership, created_at "
        "FROM trade_records WHERE trade_date=? ORDER BY id", (date,),
    ).fetchall()
    # 当日 precheck 通过记录
    # 2026-08-28 P1a 配套修复：pre_sell_check 判定行（source='pre_sell'）与买入闸门同表，
    # 若不过滤 source，卖出判定行会把同票当日裸奔买入误判为合规（虚假合规）——必须显式排除。
    _BUY_GATE_SQL = ("AND COALESCE(source,'') != 'pre_sell'")
    passed = con.execute(
        "SELECT DISTINCT code FROM guard_precheck_log "
        f"WHERE trade_date=? AND verdict != 'REJECT' {_BUY_GATE_SQL}",
        (date,),
    ).fetchall()
    passed_codes = {norm6(r["code"]) for r in passed}
    precheck_total = con.execute(
        "SELECT COUNT(*) FROM guard_precheck_log "
        f"WHERE trade_date=? {_BUY_GATE_SQL}",
        (date,),
    ).fetchone()[0]

    naked, gated = [], []
    for f in fills:
        item = dict(f)
        item["code"] = norm6(f["code"])
        if f["action"] == "BUY":
            (gated if item["code"] in passed_codes else naked).append(item)

    buy_total = len(naked) + len(gated)
    compliance = (len(gated) / buy_total * 100) if buy_total else 100.0
    in_list = sum(1 for f in fills if f["action"] == "BUY" and f["list_membership"] == "bp_ep_top20")
    result = {
        "date": date,
        "buy_fills": buy_total,
        "sell_fills": sum(1 for f in fills if f["action"] != "BUY"),
        "precheck_calls": precheck_total,
        "gated_buys": len(gated),
        "naked_buys": len(naked),
        "compliance_rate": round(compliance, 1),
        "bp_ep_list_buys": in_list,
        "outside_list_buys": buy_total - in_list,
        "naked_detail": [{"code": n["code"], "name": n["name"], "price": n["price"],
                          "volume": n["volume"], "amount": n["amount"]} for n in naked],
    }
    # 20260924 空转盲区标志: 当日 trade_records 零成交时 compliance=100.0 是空集显示
    # 而非审计结论(9/23 实证: 22:00链skip→19笔漏导入, audit 假绿 100% 掩盖缺口)。
    # 消费方见 no_fill_data=true 须先核实是否空仓日; 非空仓=漏导入, 补 fills 文件后
    # import 重跑。退出码与 compliance_rate 不变(防晚链误报), 仅显式标志。
    if not fills:
        result["no_fill_data"] = True

    # 落库 experience_library（裸奔>0 才记；同日期幂等，先删后插）
    # 20261023 定性对齐(主人10-21令): lesson 从"铁律击穿"改为训练场知会口径,
    # agent侧绕行仍禁; NAKED-case_id 键与库结构不变(下游消费零破坏)。
    if naked:
        con.execute("DELETE FROM experience_library WHERE case_id=?", (f"NAKED-{date}",))
        con.execute(
            "INSERT INTO experience_library (library_type, case_id, code, name, pnl_summary, "
            "attribution_detail, core_facts, lesson, optimization_rule, created_at) "
            "VALUES ('failure_scene', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))",
            (f"NAKED-{date}", "MULTI", f"{len(naked)}笔无precheck成交",
             f"{date} 买入{buy_total}笔, 无precheck{len(naked)}笔, 合规率{compliance:.0f}%",
             json.dumps(result["naked_detail"], ensure_ascii=False),
             f"precheck调用{precheck_total}次 vs 买入{buy_total}笔",
             "10-21令后训练场口径=主人主观单(知会级); agent侧绕行仍禁, 线索归纪律层核查",
             "每笔 agent 买入仍必须经 trade_executor 留 precheck PASS 日志；盘后全量对账"),
        )
        con.commit()
    con.close()

    # 输出报告
    os.makedirs(REPORT_DIR, exist_ok=True)
    rpt = os.path.join(REPORT_DIR, f"naked_audit_{date}.md")
    with open(rpt, "w", encoding="utf-8") as f:
        f.write(f"# 裸奔对账报告 {date}\n\n")
        # O21(2026-08-26 E46口径唯一化): 本报告只回答"裸奔(无precheck在场)"单一问题;
        # 通道合规权威结论(含绕过/async归属)以 discipline_stats 全量口径为唯一出口,
        # 此处不再输出第二个"合规率"数字, 消灭 100% vs 0% 双口径矛盾。
        f.write("> 权威合规结论（分母=全量成交、含executor_async归属）见 "
                f"`reports/discipline_{date}.md`；本报告仅判定裸奔（无任何precheck在场）。\n\n")
        f.write(f"| 指标 | 数值 |\n|---|---|\n")
        f.write(f"| 买入成交 | {buy_total} 笔 |\n| 卖出成交 | {result['sell_fills']} 笔 |\n")
        f.write(f"| precheck调用 | {precheck_total} 次 |\n| 有闸门在场(precheck过) | {len(gated)} 笔 |\n")
        f.write(f"| **裸奔买入(无precheck)** | **{len(naked)} 笔** |\n")
        f.write(f"| bp+ep名单内买入 | {in_list} 笔 |\n| 名单外买入 | {buy_total - in_list} 笔 |\n\n")
        if naked:
            # 20261023 定性对齐(主人10-21令): 训练场无precheck成交默认=主人主观单(INFO知会),
            # 不再表述为"铁律1/16违反"; agent侧绕行仍禁(线索归纪律层/violation_log)。
            f.write("## 🟡 无precheck在场成交明细（10-21令后训练场口径=主人主观单, 知会级INFO）\n\n")
            f.write(f"知会：{len(naked)}笔买入无precheck记录在场（事实层计数, 检测职能不弱化）。\n")
            f.write("定性：10-21令后训练场口径默认=主人主观单; **agent侧绕行 trade_executor 仍属违规**"
                    "（线索归纪律层/violation_log 核查）。\n\n")
            f.write("| 代码 | 名称 | 价格 | 数量 | 金额 |\n|---|---|---|---|---|\n")
            for n in naked:
                f.write(f"| {n['code']} | {n['name']} | {n['price']} | {n['volume']} | {n['amount']:.0f} |\n")
        else:
            # 20260924 空转盲区: 零成交日不得输出"每笔成交均有precheck"式假绿结论
            if not fills:
                f.write("## ⚠️ 零成交空转日（库内当日无任何成交记录）\n\n")
                f.write("compliance_rate=100% 为**空集显示，非合规审计结论**。\n")
                f.write("- 若当日确为空仓/零交易日 → 本结果正常；\n")
                f.write("- 若当日有实际成交 → **fills 漏导入**（如 22:00 链 skip），"
                        "须先从 westock 快照构建 fills_YYYYMMDD.json 并 "
                        "`naked_trade_audit.py import --file` 补录后重跑本审计。\n")
                f.write("- 9/23 实证：链 skip 致 19 笔漏导入，audit 假绿 100%（见 "
                        "reports/selfcheck_full_20260924.md）。\n")
            else:
                f.write("## ✅ 无裸奔买入（每笔成交当日均有 precheck 记录在场）\n")
    result["report"] = rpt
    # 2026-08-24 P1修复(纪律缺口闭环): 裸奔>0 不再只静默落报告——当日即 notify 知会主人。
    # 20261023 定性对齐(主人10-21令): 知会级 warn(训练场主人主观单为主), 语言去"铁律违反";
    # agent侧绕行仍禁——若确属agent绕行, 由主人核查后归 violation_log。notify 失败不阻断。
    if naked:
        try:
            import subprocess as _sp, sys as _sys
            _detail = ", ".join(f"{n['code']}{n['name']}@{n['price']}×{n['volume']}" for n in naked)
            _sp.run([_sys.executable, r"D:\zcodesj\notify.py", "send",
                     "--title", f"无precheck成交知会 {date}: {len(naked)}笔(训练场口径)",
                     "--content", f"无precheck: {_detail} | 详见 {rpt} | agent侧绕行仍禁,定性核查归主人",
                     "--level", "warn"], timeout=60)
        except Exception:
            pass
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="盘后裸奔对账审计器")
    sub = p.add_subparsers(dest="mode", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    i = sub.add_parser("import")
    i.add_argument("--file", required=True)
    rc = sub.add_parser("recon")
    rc.add_argument("--file", required=True)
    args = p.parse_args()

    if args.mode == "import":
        out = cmd_import(args.file)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    if args.mode == "recon":
        out = cmd_recon(args.file)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if out["unrecorded"] > 0 else 0
    out = cmd_audit(args.date)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if out["naked_buys"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
