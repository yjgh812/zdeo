#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fill_confirm_monitor.py — westock 异步成交回报监控与账本归属治理（E45/E46 收口, O19/O20/O23）

背景（2026-08-26 自省 ROUND2 E45）：限价贴现委托多为异步成交，executor 的 sleep(1.5s)
持仓差验证拍不到后续回报，真实 executor 单被盘后 reconcile 一律打 'ungated_待标注'。
本监控器补上"提交 → 回报 → 回填"闭环：

模式（互斥选择其一）:
  poll       默认。轮询 data/open_orders.jsonl 未决行，用持仓差确认增量成交，
             成交部分回填 attribution.trade_records(subsystems='trade_executor_async')
             + dragon_quant.buy_tracker(BUY)，并更新台账 confirmed/status。
  --expire   盘后兜底（A股委托当日有效）：当日仍未成交的 BUY/SELL 行标 EXPIRED_READ，
             并生成次日复评段 reports/pending_review_YYYYMMDD.md（O23，仅列示不决策）。
  --reclassify D  对该日 trade_records 中 ungated_待标注 行按提交台账归属改判（幂等）。
  --reconcile [--history]  E19 卖出单生命周期闸（2026-10-16 第三轮全维自省呈请，
             「继续」令批准）：open_orders 未了结行（OPEN/PARTIAL/EXPIRED_READ）↔
             westock 成交明细 订单号直对账——真已成交（含 EXPIRED_READ 误标）改判
             FILLED、真已撤改判 CANCELLED，并 mark_order_status 传播回提交台账；
             无匹配维持原状态如实列示。--history 拉 recent 3月 fills 做存量清洗
             （首例 SELL_OPEN=41）。fail-closed：fills 拉取失败 exit 3。
  --bootstrap-from-log F  一次性从 buy-batch 执行日志(JSON)补录提交台账（历史救援）。
  --naked-watch  盘中裸奔实时检测（O12 2026-09-03）：westock 当日成交明细 vs executor
             提交台账 allocate 归属，无法归属的成交分钟级告警(notify+reports 落盘)；
             循环模式仅交易时段 09:25-15:10 运行到点自退（资源上界），--once 单次
             全量对账（手动/验收）。数据源=portfolio_paper_history 直连（与 22:00
             审计同源）；拉取失败 fail-closed exit 3 不误报。

退出码: 0 正常 / 1 naked-watch 检出台账外成交(已告警) / 2 无可执行工作（无害）/ 3 数据源失败(fail-closed, 拒绝假成功)。

用法示例：
  python fill_confirm_monitor.py                      # poll（buy-batch 后、cron 兜底均可）
  python fill_confirm_monitor.py --expire             # 15:05 或 22:00 管线收盘步调用
  python fill_confirm_monitor.py --reclassify 20260826
  python fill_confirm_monitor.py --bootstrap-from-log reports/buy_batch_exec_20260826.log
  python fill_confirm_monitor.py --naked-watch --once # 单次裸奔对账（手动/验收）
  python fill_confirm_monitor.py --naked-watch        # 盘中循环（09:30 启动, 15:10 自退）

SQL 全部参数绑定；路径为字面量常量（Mimosa 约定）。
"""
from __future__ import annotations

import argparse
import re
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace")

from executor_order_ledger import (  # noqa: E402
    ATTR_DB, EXEC_ORDERS, OPEN_ORDERS, SUB_EXEC_ASYNC,
    load_open_rows, save_open_rows, load_orders,
    reclassify_traderecords, extract_order_id,
    allocate_fills_to_orders,  # O12 2026-09-03: naked-watch 与 22:00 审计同一归属规则
    mark_order_status,  # S2 2026-09-28: poll 确认 FILLED 传播回写提交台账（断链修复）
)

DQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
REPORT_DIR = r"D:\zcodesj\reports"


def norm6(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.split(".")[0].zfill(6)


def to_prefixed(code6: str) -> str:
    if code6.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + code6
    if code6.startswith(("8", "920", "430")):
        return "bj" + code6
    return "sz" + code6


def _positions_map() -> dict[str, int]:
    """westock 实时持仓 {code6: qty}。失败抛异常 → 上层 fail-closed exit 3。"""
    from westock_client import WeStockClient  # noqa: PLC0415 (延迟导入便于 CLI 自检)
    ws = WeStockClient()
    data = ws.positions().get("data", {})
    out = {}
    for p in data.get("positions", []):
        q = int(float(p.get("quantity") or 0))
        out[norm6(p.get("code", ""))] = q
    return out


def _already_recorded(con: sqlite3.Connection, store_code: str, tdate: str,
                      price: float, volume: int, action: str) -> bool:
    """与盘后导入相同的幂等键（code+date+price+action+volume 五元组）。"""
    n = con.execute(
        "SELECT COUNT(*) FROM trade_records WHERE code=? AND trade_date=? AND price=? "
        "AND volume=? AND action=?",
        (store_code, tdate, round(price, 4), int(volume), action)).fetchone()[0]
    return n > 0


def _record_fill(row: dict, filled_now: int, now_iso: str, fill_date: str | None = None) -> None:
    """把回报确认的增量成交回填双库（BUY 记 buy_tracker, SELL 不涉及）。

    2026-08-28 准则37: SELL 行携带台账 c6_note（do_sell 下单时写入）一并落 notes，
    保证挂单卖出路径的 C6 板块判定同样落库；旧行无该字段则保持原样（向后兼容）。
    """
    code6 = norm6(row["code"])
    store_code = to_prefixed(code6)
    # 20261027 A层(async双写根因修复): 优先用westock真实成交日(fill_date, 由cmd_poll经
    # _fetch_today_fills匹配回填); 台账行trade_date是提交日——跨日成交(9/1提交9/2成交)曾以
    # 提交日入账, 与盘后审计导入(成交日)构成同单双日期行, 经closed_loop_sync FIFO产出幻影孪生行
    today = fill_date or row.get("trade_date") or datetime.now().strftime("%Y%m%d")
    note = f"async回报确认 order={row.get('order_id') or ''}".strip()
    if row.get("c6_note"):
        # 2026-08-28 准则43: c6_note 现为全量卖出注记(理由+C6+执行)，仅在其无执行口径时补标
        _n = str(row["c6_note"])
        note = f"{note} | {_n}" + ("" if "执行=" in _n else " | 执行=挂单回报口径")
    con = sqlite3.connect(ATTR_DB)
    if not _already_recorded(con, store_code, today, row["price"], filled_now, row["action"]):
        con.execute(
            "INSERT INTO trade_records (code,name,trade_date,action,price,volume,amount,"
            "strategy_version,signal_type,subsystems,market_env,sector,notes,list_membership,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (store_code, row.get("name") or "", today, row["action"], row["price"], filled_now,
             round(row["price"] * filled_now, 2), today, "manual", SUB_EXEC_ASYNC, "", "",
             note, "", now_iso))
    else:
        # 2026-09-23 T8(幂等查重): 五元组已存在=双轨重复记账, 跳过写入留痕
        print(f"  [skip] trade_records duplicate skipped: {store_code} {today} "
              f"{row['action']} {row['price']} x{filled_now}(幂等查重T8)")
    con.commit()
    con.close()
    if row["action"] == "BUY":
        con2 = sqlite3.connect(DQ_DB)
        con2.execute(
            "INSERT INTO buy_tracker (code,name,buy_date,buy_price,buy_qty,signal_source,"
            "stop_loss_pct,status,verdict,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(code,buy_date) DO UPDATE SET "
            "buy_qty=buy_tracker.buy_qty+excluded.buy_qty, "
            "buy_price=ROUND((buy_tracker.buy_price*buy_tracker.buy_qty"
            "+excluded.buy_price*excluded.buy_qty)"
            "/(buy_tracker.buy_qty+excluded.buy_qty),4), updated_at=excluded.updated_at",
            (store_code, row.get("name") or "", today, row["price"], filled_now,
             "async_confirmed", -0.08, "HOLD", "PENDING", now_iso))
        con2.commit()
        con2.close()


def cmd_poll() -> dict:
    """持仓差轮询：把未决委托的增量成交回填账本。"""
    # 2026-08-27 修复: rows 与 all_rows 必须同源(同一批对象), 否则 `x is r` 身份比较
    # 永不匹配 → next() 抛 StopIteration 首跑即崩(E45 首验实锤); 改为从 all_rows 派生。
    all_rows = load_open_rows(None)  # 含其他状态行，整体读出保持行序
    rows = [r for r in all_rows
            if r.get("status") in ("OPEN", "PARTIAL") and not r.get("_bad_line")]
    if not rows:
        print("[POLL] 无未决委托（首次运行或已全部确认/过期）", flush=True)
        return {"mode": "poll", "rows": 0}
    try:
        pos = _positions_map()
    except Exception as e:
        print(f"[FAIL-CLOSED] westock 持仓获取失败({type(e).__name__}: {e})——拒绝假成功",
              file=sys.stderr)
        sys.exit(3)

    # 20261027 A层: 当日westock成交明细一次拉取(失败降级None=维持提交日旧语义,不阻断回报回填)
    _today_fills = None
    try:
        _today_fills = _fetch_today_fills()
    except Exception as _e:
        print(f"[WARN] 当日成交明细不可得({type(_e).__name__}: {_e})——回报确认日降级为台账提交日",
              file=sys.stderr)
    changed_total = {}
    for r in rows:
        idx = next(i for i, x in enumerate(all_rows) if x is r)
        # 2026-08-27 修复: 持仓映射键为裸6位(norm6), 行内code带sh/sz前缀——不归一则恒查0股零成交
        c6 = norm6(r["code"])
        now_q = pos.get(c6, 0)
        base = int(r.get("base_qty") or 0)
        conf = int(r.get("confirmed") or 0)
        if r["action"] == "BUY":
            delta = min(now_q - base - conf, int(r["qty"]) - conf)
        else:
            delta = min(base - now_q - conf, int(r["qty"]) - conf)
        if delta <= 0:
            continue
        now_iso = datetime.now().isoformat(timespec="seconds")
        _fill_date = None
        if _today_fills:
            _cands = [f for f in _today_fills
                      if f.get("code6") == c6 and f.get("action") == r["action"]
                      and f.get("status") == "已成交"]
            if _cands:
                _fd = max(f["filled_at"] for f in _cands)[:10].replace("-", "")
                if _fd >= (r.get("trade_date") or ""):
                    _fill_date = _fd
        _record_fill(r, delta, now_iso, fill_date=_fill_date)
        r["confirmed"] = conf + delta
        all_rows[idx] = r
        changed_total[c6] = changed_total.get(c6, 0) + delta
        print(f"[FILLED] {r['action']} {c6} {r.get('name')} +{delta}股 → "
              f"{r['confirmed']}/{r['qty']}", flush=True)
        if r["confirmed"] >= int(r["qty"]):
            r["status"] = "FILLED"
            all_rows[idx] = r
            # S2(2026-09-28 全维度自省): 传播回写 executor 提交台账。背景=下单线程内
            # sleep(1.5s) 持仓差验证拍不到大单成交(9/28 弘信电子 sz300657 5600股实证,
            # open_orders 已 FILLED 而 executor_orders 恒 OPEN), monitor 轮询确认后的
            # FILLED 是更强证据, 必须回流同一状态 owner; mark 返回 False=台账外直连单,
            # 如实打印不静默。
            _oid = str(r.get("order_id") or "")
            if _oid and not mark_order_status(
                    _oid, "FILLED",
                    f"monitor轮询持仓差确认{r['confirmed']}/{r['qty']}股, 传播回写S2"):
                print(f"[WARN] order_id={_oid} 不在 executor_orders 台账(台账外直连单?)",
                      flush=True)
        elif r["status"] == "OPEN":
            r["status"] = "PARTIAL"
            all_rows[idx] = r
    save_open_rows(all_rows)
    return {"mode": "poll", "rows": len(rows), "filled_codes": changed_total}


def cmd_expire(date_str: str) -> dict:
    """收盘兜底：未成交委托当日有效失效标注 + 次日复评段产物（O23）。"""
    # 2026-09-11 加固（9/10 实锤）：先跑一次 poll（持仓差确认）再标过期——否则
    # "已成交但 poll 缺席"的委托会被 --expire 误标 EXPIRED_READ（9/10 14:30 链缺席
    # → 4 笔已成交委托被标过期，pending_review 把已持仓列成"未成交候选"、挂单成交率失真）。
    # 子进程隔离：poll 的 westock 失败会 exit(3)，不得阻断过期主流程（复评段仍须产出）。
    try:
        _p = subprocess.run([sys.executable, os.path.abspath(__file__)],
                            capture_output=True, text=True, timeout=300,
                            encoding="utf-8", errors="replace")
        _tail = ((_p.stdout or "") + (_p.stderr or "")).strip()[-160:].replace("\n", " ")
        print(f"[expire] 前置 poll rc={_p.returncode} {_tail}", flush=True)
    except Exception as e:
        print(f"[expire][WARN] 前置 poll 失败({type(e).__name__}: {e})，继续过期流程", flush=True)
    # E19（2026-10-16 第三轮全维自省）卖出侧 fail-closed 加固：前置 poll 是建议制，
    # 失败时旧逻辑对 SELL 行无条件标 EXPIRED_READ——10/15 离线窗口实锤五笔真已成交
    # （蓝丰/泉峰/3买）被标过期且 confirmed=0。新语义：过期标注前拉成交明细对账，
    #   fills 可得 → 命中成交→FILLED / 命中撤单→CANCELLED / 无匹配→EXPIRED_READ（真未成交）；
    #   fills 不可得 → SELL 行保持 OPEN 不误标（宁可留待次日对账，不制造假过期），
    # 买入行维持原过期语义（复评段 O23 不依赖成交判定）。
    try:
        _expire_fills = _fetch_history_fills()
        _expire_fills_ok = True
    except Exception as e:  # noqa: BLE001 — 数据源失败按 fail-closed 降级：只豁免 SELL
        _expire_fills_ok = False
        _expire_fills = []
        print(f"[expire][FAIL-CLOSED] 成交明细不可得({type(e).__name__}: {e})"
              "——本轮 SELL 行不标过期（保持 OPEN 待对账）", flush=True)
    # 2026-08-27 修复: 同 cmd_poll, rows 从 all_rows 派生保证同源(身份比较可用)
    all_rows = load_open_rows(None)
    rows = [r for r in all_rows
            if r.get("status") in ("OPEN", "PARTIAL") and not r.get("_bad_line")]
    expired, partial_kept, sells_kept_open = [], [], []
    for r in rows:
        # 2026-09-10 修复: 原实现只处理 trade_date==date_str 的行, 断档日(9/4、9/5、9/7、
        # 9/8 等 cmd_expire 未跑的日子)遗留的 OPEN/PARTIAL 行永久不过期 → 后续 poll 会把
        # 陈化行误确认成幻影成交(上海银行 9/7 双记同族)。改为: 所有 trade_date<=date_str
        # 的未了结行一并过期(委托当日有效, 隔日必然已失效); 未来日期行跳过。
        td = r.get("trade_date") or ""
        if td > date_str:
            continue
        idx = next(i for i, x in enumerate(all_rows) if x is r)
        if r["action"] != "BUY":
            if not _expire_fills_ok:
                sells_kept_open.append(r)
                continue
            _f = _match_fill(r, _expire_fills)
            if _f is not None and _f["status"] in LIFECYCLE_FILLED_STATES:
                new_st = _apply_fill_outcome(r, _f, all_rows, idx,
                                             datetime.now().isoformat(timespec="seconds"))
                print(f"[expire] SELL 命中成交改判 FILLED: {r.get('name')} "
                      f"{r.get('confirmed')}/{r.get('qty')} @{_f['filled_at']}", flush=True)
                continue
            if _f is not None and _f["status"] in LIFECYCLE_CANCELLED_STATES:
                _apply_fill_outcome(r, _f, all_rows, idx,
                                    datetime.now().isoformat(timespec="seconds"))
                print(f"[expire] SELL 命中撤单改判 CANCELLED: {r.get('name')}", flush=True)
                continue
            r["status"] = "EXPIRED_READ"
            expired.append(r)
            all_rows[idx] = r
            continue
        # 未成交部分次日复评（不自动续挂，不追价）
        left = int(r["qty"]) - int(r.get("confirmed") or 0)
        if left > 0:
            r["status"] = "EXPIRED_READ"
            r["qty_left_for_review"] = left
            expired.append(r)
            all_rows[idx] = r
        else:
            partial_kept.append(r)
    save_open_rows(all_rows)
    # 复评段 md（只列示当日 EXPIRED_READ 且属 BUY 的候选，供次日早报定级）
    # 2026-08-27 修复: 从 all_rows 汇总当日全部 EXPIRED_READ 行(含此前运行已过期的),
    # 否则幂等重跑会把先前过期行从 md 中丢失。
    path = os.path.join(REPORT_DIR, f"pending_review_{date_str}.md")
    lines = [
        f"# 次日复评段（未成交候选）{date_str}\n",
        "> A股委托当日有效，以下为收盘未成交的贴现买单余量；仅供次日名单复评定级，"
        "不自动续挂、不自动追价（E49 收口纪律）。\n",
        "| 代码 | 名称 | 委托价 | 余量(股) | 已成 | 状态 |\n|---|---|---|---|---|---|\n",
    ]
    md_rows = [r for r in all_rows
               if r.get("status") == "EXPIRED_READ"
               and (r.get("trade_date") or "") == date_str and r.get("action") == "BUY"]
    for r in sorted(md_rows, key=lambda x: -(int(x["qty"]) - int(x.get("confirmed") or 0))):
        lines.append(
            f"| {r['code']} | {r.get('name','')} | {r['price']} "
            f"| {int(r['qty']) - int(r.get('confirmed') or 0)} "
            f"| {r.get('confirmed', 0)} | {r['status']} |\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"📄 复评段已输出: {path}", flush=True)
    return {"mode": "expire", "date": date_str, "expired": len(expired),
            "partial_kept": len(partial_kept), "sells_kept_open": len(sells_kept_open),
            "report": path}


def cmd_bootstrap(log_path: str, trade_date: str | None = None) -> dict:
    """从 buy-batch 日志 JSON 补录提交台账（一次性历史救援，order_id 幂等）。

    trade_date: 台账交易日归属；缺省从日志文件名 buy_batch_exec_YYYYMMDD*.log 提取,
    都没有才回落到今天——执行常跨零点(深夜复盘)，绝不能用 now() 冒充历史交易日。"""
    with open(log_path, encoding="utf-8") as f:
        doc = json.load(f)
    base_name = os.path.basename(log_path)
    td = None
    for tok in base_name.replace(".", "_").split("_"):
        if len(tok) == 8 and tok.isdigit():
            td = tok
            break
    if trade_date:
        td = trade_date
    if not td:
        td = datetime.now().strftime("%Y%m%d")
    results = doc.get("results", [])
    existing = {o.get("order_id") for o in load_orders()}
    added = 0
    for r in results:
        order_resp = r.get("order_resp")
        if not isinstance(order_resp, dict):
            continue
        oid = extract_order_id(order_resp)
        if not oid or oid in existing:
            continue
        d = order_resp.get("data") or {}
        qty = int(float(d.get("quantity") or r.get("filled_qty") or 0)) \
            if d.get("quantity") else int((r.get("sizing") or {}).get("shares") or 0)
        px = float(d.get("price") or 0)
        if qty <= 0 or px <= 0:
            continue
        with open(EXEC_ORDERS, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "order_id": oid, "code": norm6(r.get("code", "")),
                "name": r.get("name", ""), "action": "BUY", "price": px, "qty": qty,
                "trade_date": td,
                # 日志无提交时刻——以当日 00:00 作为保守下界参与 >= submitted_at 时间判据,
                # bootstrapped=true 显式标记时刻近似（不冒充精确值）
                "submitted_at": f"{td[:4]}-{td[4:6]}-{td[6:8]}T00:00:00",
                "kind": "buy_batch_bootstrap",
                "note": "bootstrapped from exec log, 时刻近似",
                "bootstrapped": True,
            }, ensure_ascii=False) + "\n")
        added += 1
    print(f"[BOOTSTRAP] 新增台账 {added} 条（日志共 {len(results)} 笔结果, trade_date={td}）",
          flush=True)
    return {"mode": "bootstrap", "added": added, "trade_date": td}


def cmd_reprice_scan() -> dict:
    """P1-2 pegging 改价监控(只读零交易动作, WINRATE_UPGRADE_PLAN_20260829):

    背景: 执行率 45% 缺口=贴现挂单不追价; 但 westock API 无改单/单笔撤单(仅 cancel_all
    全撤)——自动"撤+重挂"会误伤同时挂着的卖出单且存在双成交竞态, 故第一版只做
    检测+建议清单, 自动化待 API 单笔撤单能力或主人明确授权原子性风险。

    判定(每笔当日未决 BUY)——2026-09-24 名单制废除版:
      白名单 limit_price 上限已随「全面废除TopN名单限制」退役, 原 REPRICE/WAIT_CAP
      名单上限语义作废, 本监控降级为纯信息呈现:
      mkt ≤ 挂价 → NORMAL(可成交区间, 队列问题);
      mkt > 挂价 → ABOVE_LIMIT(现价高于挂价, 仅供参考, 无自动改价依据);
      改价与否由主人/人工按盘面自行决策。
    """
    from westock_client import WeStockClient

    today = datetime.now().strftime("%Y%m%d")
    all_rows = load_open_rows(None)
    # 2026-09-02 修复: 去掉 trade_date==today 过滤——OPEN/PARTIAL 状态本身即活单标志,
    # 隔夜挂单(如 9/1 23:46 P0-E 减半卖单, 次日开盘成交)被旧过滤漏报成"无未决卖出";
    # 历史行已由 --expire 步骤标 EXPIRED_READ, 无需日期兜底。坏行仍排除。
    pend_buys = [r for r in all_rows if r.get("status") in ("OPEN", "PARTIAL")
                 and r.get("action") == "BUY"
                 and not r.get("_bad_line")]
    pend_sells = [r for r in all_rows if r.get("status") in ("OPEN", "PARTIAL")
                  and r.get("action") == "SELL"]

    # 2026-10-10 A1兜底(主人「继续」令批准 owner_decisions_20261012 A1): 事件驱动限价
    # 卖单(executor挂单note带A1_EVENT_SELL标志)在 ≥10:00 仍未决 → 输出转市价升级建议。
    # 建议制: cancel_all 无单笔撤单 API, 自动撤改待 MCP 单笔 cancel 接入后另批升级;
    # 未成交=当日价格始终高于 open×0.995(持仓强势), 延迟卖出风险有限(回放未成交2笔
    # 次日 open +2.79%), 但兜底呈报是 A1 硬性前提防仓位滞留。
    try:
        from trade_executor import EVENT_SELL_NOTE_TAG
    except Exception:
        EVENT_SELL_NOTE_TAG = "A1_EVENT_SELL"  # executor 缺失时按字面量兜底(同值)
    _now_hm = datetime.now().strftime("%H:%M")
    a1_fallback = ([r for r in pend_sells
                    if EVENT_SELL_NOTE_TAG in str(r.get("note") or "")]
                   if _now_hm >= "10:00" else [])

    advice, ws = [], None
    for r in pend_buys:
        try:
            if ws is None:
                ws = WeStockClient()
            q = ws.quote(r["code"]).get("data", {})
            q = q.get(r["code"], q) if isinstance(q, dict) else {}
            mkt = q.get("price")
        except Exception as e:
            advice.append({"code": r["code"], "name": r.get("name"), "verdict": "QUOTE_FAIL",
                           "note": f"行情获取失败({type(e).__name__}) 跳过"})
            continue
        if not mkt:
            advice.append({"code": r["code"], "name": r.get("name"), "verdict": "QUOTE_EMPTY"})
            continue
        px = float(r.get("price") or 0)
        if mkt <= px:
            v, newpx, note = "NORMAL", None, ""
        else:
            v, newpx, note = "ABOVE_LIMIT", None, "现价高于挂价——白名单上限已废除(2026-09-24), 无自动改价依据, 仅供参考"
        advice.append({"code": r["code"], "name": r.get("name"), "verdict": v,
                       "order_price": px, "mkt": mkt,
                       "suggest_new_price": newpx, "note": note})

    n_reprice = sum(1 for a_ in advice if a_["verdict"] == "REPRICE")
    # A1 兜底建议(≥10:00 未决事件驱动限价卖单 → 转市价)
    for r in a1_fallback:
        try:
            if ws is None:
                ws = WeStockClient()
            q = ws.quote(r["code"]).get("data", {})
            q = q.get(r["code"], q) if isinstance(q, dict) else {}
            mkt = q.get("price")
        except Exception:
            mkt = None
        advice.append({"code": r["code"], "name": r.get("name"),
                       "verdict": "A1_FALLBACK_SELL_MARKET",
                       "order_price": r.get("price"), "mkt": mkt,
                       "suggest_new_price": mkt,
                       "note": ("A1事件驱动限价卖单≥10:00未成交 → 转市价建议"
                                "(建议制: 人工/盘中链采纳; 无单笔撤单API)")})
    sell_detail = "; ".join(
        f"{r.get('name') or r.get('code')} {r.get('qty')}股@{r.get('price')}"
        f"(挂单日{r.get('trade_date')})" for r in pend_sells)
    out = {"mode": "reprice-scan", "trade_date": today, "pending_buys": len(pend_buys),
           "pending_sells_note": (f"共 {len(pend_sells)} 笔未决卖出在挂(含隔夜): {sell_detail}"
                                  "——自动化撤单重挂被禁止的原因(cancel_all 无单笔撤单)"
                                  ) if pend_sells else "无未决卖出",
           "advice": advice,
           "reprice_cnt": n_reprice,
           "a1_fallback_cnt": len(a1_fallback),
           "a1_fallback_codes": [r.get("code") for r in a1_fallback],
           "note": "P1-2 v1=只读监控: 建议清单供 09:40/10:30 盘中人工/主人决策; "
                   "执行率45%→65%验收依赖后续自动化授权(API 缺单笔撤单)或人工采纳; "
                   "A1_EVENT_SELL 未决卖单 ≥10:00 输出转市价建议(A1_FALLBACK_SELL_MARKET)"}
    path = os.path.join(REPORT_DIR, f"reprice_advice_{today}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    out["report"] = path
    return out


# ── O12（2026-09-03）：盘中裸奔实时检测 ─────────────────────────────────────
# 背景（SELF_REVIEW_ROUND7 A12/B14）：9/3 五笔 54.77 万直连 MCP 绕 executor，
# 09:53 犯错至 21:41 violation 登记滞后 12 小时（唯一兜底=22:00 naked_audit）。
# 本模式把"成交 vs 提交台账归属"对账前移到盘中分钟级。

NAKED_FILLED_STATES = ("已成交", "部分成交", "成交")
NAKED_WATCH_START, NAKED_WATCH_END = 925, 1510  # 循环模式时段守卫(HHMM)


def _parse_cn_ts(s: str) -> "datetime | None":
    """兼容 'YYYY-MM-DD HH:MM:SS' / 'T' 分隔 / 无秒 三种时刻格式; 失败返回 None。"""
    s = str(s or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _precheck_exempt_map(trade_date: str) -> dict[str, list[tuple[datetime, str]]]:
    """当日 guard_precheck_log 放行时刻表 {code6: [(放行dt, buy|sell), ...]}。

    2026-09-23 naked_watch×guarded_paper_trade 联动(B9配套): 会话直单三步流程的
    第一步 --precheck 落库 guard_precheck_log, 但本模式原判定只认 executor 提交
    台账, 预检过的委托成交后仍被误报裸奔。豁免口径(与盘后 naked_trade_audit 一致):
      buy  侧: source!='pre_sell' 且 verdict!='REJECT'（买入闸门放行;
               卖出判定行显式排除, 防 8/28 P1a 同款虚假合规）;
      sell 侧: source=='pre_sell' 且 verdict=='SELL_OK'（HOLD=建议保留不算放行）。
    查询失败返回空表(不豁免, 宁可误报不可漏报); SQL 参数绑定。
    """
    try:
        con = sqlite3.connect(ATTR_DB)
        rows = con.execute(
            "SELECT ts, code, verdict, source FROM guard_precheck_log WHERE trade_date=?",
            (trade_date,)).fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001 — 豁免通道失败降级为不豁免
        print(f"[WARN] guard_precheck_log 查询失败({type(e).__name__}: {e})"
              "——本轮不启用已预检豁免", flush=True)
        return {}
    out: dict[str, list[tuple[datetime, str]]] = {}
    for ts, code, verdict, source in rows:
        dt = _parse_cn_ts(ts)
        if dt is None:
            continue
        src = str(source or "")
        vd = str(verdict or "")
        if src == "pre_sell":
            if vd != "SELL_OK":
                continue
            kind = "sell"
        else:
            if vd == "REJECT":
                continue
            kind = "buy"
        out.setdefault(norm6(code or ""), []).append((dt, kind))
    return out


def _fill_precheck_exempt(fill: dict, exempt_map: dict, window_min: int) -> bool:
    """单笔成交是否命中"已预检"豁免: 同 code 同方向、放行时刻在
    [成交时刻-window_min, 成交时刻] 内。成交时刻无法解析 → 不豁免(保守)。"""
    hits = exempt_map.get(fill.get("code6", ""))
    if not hits:
        return False
    fdt = _parse_cn_ts(fill.get("filled_at"))
    if fdt is None:
        return False
    want = "sell" if str(fill.get("action", "")).upper() == "SELL" else "buy"
    lo = fdt.timestamp() - window_min * 60
    return any(kind == want and lo <= dt.timestamp() <= fdt.timestamp() + 60
               for dt, kind in hits)


def _fetch_today_fills() -> list[dict]:
    """westock 当日成交明细（与 22:00 naked_audit 同源 portfolio_paper_history 直连）。

    失败抛异常 → 上层 fail-closed exit 3，拒绝假成功。"""
    from westock_client import WeStockClient  # noqa: PLC0415
    ws = WeStockClient()
    r = ws.call("portfolio_paper_history", {"range": "today", "limit": 200})
    if not isinstance(r, dict) or not r.get("ok"):
        raise RuntimeError(f"portfolio_paper_history 返回异常: {str(r)[:200]}")
    records = (r.get("data") or {}).get("records") or []
    out = []
    for x in records:
        out.append({
            "code6": norm6(x.get("code", "")),
            "action": str(x.get("direction") or "").upper(),
            "price": float(x.get("price") or 0),
            "volume": int(float(x.get("quantity") or 0)),
            "filled_at": str(x.get("filledAt") or ""),
            "status": str(x.get("status") or ""),
            "order_id": str(x.get("orderId") or ""),
            "name": x.get("name") or "",
        })
    return out


# ── E19（2026-10-16 第三轮全维自省）：卖出单生命周期闸 ─────────────────────
# 10/15 实锤：离线窗口令前置 poll 缺席，--expire 把五笔真已成交（蓝丰/泉峰 2 卖 +
# 浦发/健康元/巴比 3 买）标 EXPIRED_READ 且 confirmed=0，executor 台账 SELL 恒 OPEN，
# ledger_status_recon SELL_OPEN=41 存量。本节补上"订单号直对账"的第三条确认路径
# （poll=持仓差、naked-watch=台账归属，本节=委托号↔回报号精确匹配），三路同一真值源。

LIFECYCLE_FILLED_STATES = ("已成交", "部分成交", "成交")
LIFECYCLE_CANCELLED_STATES = ("已撤单", "系统撤单", "撤单")


def _fetch_history_fills(limit: int = 500) -> list[dict]:
    """westock 近 3 月合并成交明细（--reconcile --history 存量清洗与 expire 预对账共用；
    与 _fetch_today_fills 同源同构）。失败抛异常 → 上层 fail-closed。"""
    from westock_client import WeStockClient  # noqa: PLC0415
    ws = WeStockClient()
    r = ws.call("portfolio_paper_history", {"range": "recent", "limit": limit})
    if not isinstance(r, dict) or not r.get("ok"):
        raise RuntimeError(f"portfolio_paper_history(recent) 返回异常: {str(r)[:200]}")
    records = (r.get("data") or {}).get("records") or []
    out = []
    for x in records:
        out.append({
            "code6": norm6(x.get("code", "")),
            "action": str(x.get("direction") or "").upper(),
            "price": float(x.get("price") or 0),
            "volume": int(float(x.get("quantity") or 0)),
            "filled_at": str(x.get("filledAt") or ""),
            "status": str(x.get("status") or ""),
            "order_id": str(x.get("orderId") or ""),
            "name": x.get("name") or "",
        })
    return out


def _fill_day(fill: dict) -> str:
    return str(fill.get("filled_at") or "").replace("-", "").replace(":", "") \
        .replace(" ", "")[:8]


def _match_fill(row: dict, fills: list[dict]) -> dict | None:
    """open_orders 行 ↔ westock 成交明细：order_id 精确匹配优先；无委托号的历史行
    才允许五元组兜底（code+action+价格2位+数量+交易日）。
    有委托号但回报侧无此号 = 真无匹配（委托号命名空间权威，禁五元组猜测误配）。"""
    oid = str(row.get("order_id") or "")
    if oid:
        for f in fills:
            if f["order_id"] == oid:
                return f
        return None
    c6, act = norm6(row["code"]), str(row.get("action") or "").upper()
    px, qy = round(float(row.get("price") or 0), 2), int(row.get("qty") or 0)
    td = str(row.get("trade_date") or "")
    for f in fills:
        if (f["code6"] == c6 and f["action"] == act
                and round(f["price"], 2) == px and int(f["volume"]) == qy
                and (not td or _fill_day(f) == td)):
            return f
    return None


def _apply_fill_outcome(r: dict, f: dict, all_rows: list[dict], idx: int,
                        now_iso: str) -> str:
    """按成交明细裁决单行状态并落副作用（增量回填 _record_fill 幂等 + mark_order_status
    传播回提交台账）。返回新状态；f 状态不在成交/撤单两类时维持原状。"""
    old = r.get("status")
    if f["status"] in LIFECYCLE_FILLED_STATES:
        conf = min(int(f["volume"]), int(r["qty"]))
        delta = conf - int(r.get("confirmed") or 0)
        if delta > 0:
            _record_fill(r, delta, now_iso)
        r["confirmed"] = max(conf, int(r.get("confirmed") or 0))
        r["status"] = "FILLED"
        r["reconcile_note"] = (f"lifecycle对账改判 {old}→FILLED "
                               f"order={f['order_id']} @{f['filled_at']}")
        all_rows[idx] = r
        _oid = str(r.get("order_id") or "")
        if _oid and not mark_order_status(
                _oid, "FILLED", f"lifecycle对账回填(原{old}), E19卖出单生命周期闸"):
            print(f"[WARN] order_id={_oid} 不在 executor_orders 台账(台账外直连单?)",
                  flush=True)
        return "FILLED"
    if f["status"] in LIFECYCLE_CANCELLED_STATES:
        r["status"] = "CANCELLED"
        r["reconcile_note"] = f"lifecycle对账改判 {old}→CANCELLED order={f['order_id']}"
        all_rows[idx] = r
        _oid = str(r.get("order_id") or "")
        if _oid:
            mark_order_status(_oid, "CANCELLED", "lifecycle对账回填, E19卖出单生命周期闸")
        return "CANCELLED"
    return str(old)


def cmd_reconcile(history: bool = False) -> dict:
    """E19 生命周期对账：未了结行 ↔ westock 成交明细订单号直配改判（详见模块 docstring）。"""
    try:
        fills = _fetch_history_fills() if history else _fetch_today_fills()
    except Exception as e:
        print(f"[FAIL-CLOSED] westock 成交明细获取失败({type(e).__name__}: {e})"
              "——拒绝假成功（无数据源不改判任何状态）", file=sys.stderr)
        sys.exit(3)
    all_rows = load_open_rows(None)
    targets = [r for r in all_rows
               if r.get("status") in ("OPEN", "PARTIAL", "EXPIRED_READ")
               and not r.get("_bad_line")]
    corrections, unmatched, seen_fill = [], [], set()
    now_iso = datetime.now().isoformat(timespec="seconds")
    for r in targets:
        idx = next(i for i, x in enumerate(all_rows) if x is r)
        f = _match_fill(r, fills)
        if f is None or id(f) in seen_fill:
            if f is None:
                unmatched.append({"code": norm6(r["code"]), "action": r["action"],
                                  "name": r.get("name"), "status": r.get("status"),
                                  "trade_date": r.get("trade_date")})
            continue
        seen_fill.add(id(f))
        new_st = _apply_fill_outcome(r, f, all_rows, idx, now_iso)
        # 命中即记（含幂等复核命中：状态已是目标态但台账/回报完成闭环确认）
        corrections.append({"code": norm6(r["code"]), "name": r.get("name"),
                            "action": r["action"], "new": new_st,
                            "fill_at": f["filled_at"]})
    save_open_rows(all_rows)
    out = {"mode": "reconcile", "history": history, "fills": len(fills),
           "targets": len(targets), "corrections": corrections,
           "unmatched": unmatched}
    if corrections:
        detail = "; ".join(f"{c['action']} {c['code']} {c.get('name')}→{c['new']}"
                           for c in corrections[:8])
        print(f"[LIFECYCLE] 对账改判 {len(corrections)} 笔: {detail}", flush=True)
        _naked_notify("卖出单生命周期对账改判(E19)",
                      f"改判 {len(corrections)} 笔（EXPIRED_READ/OPEN→真值态）: {detail}")
    else:
        print(f"[LIFECYCLE] 对账干净（targets={len(targets)} 全部维持原状态）", flush=True)
    if unmatched:
        print(f"[LIFECYCLE] 无匹配未了结行 {len(unmatched)} 笔（如实列示不擅断）:",
              "; ".join(f"{u['action']} {u['code']}@{u['trade_date']}"
                        for u in unmatched[:8]), flush=True)
    return out


def _naked_notify(title: str, content: str) -> None:
    """告警外发（notify.py CLI）；发送失败不阻断（告警仍落盘 reports/）。"""
    try:
        subprocess.run(
            [sys.executable, r"D:\zcodesj\notify.py", "send",
             "--title", title, "--content", content, "--level", "error"],
            timeout=30, capture_output=True)
    except Exception as e:  # noqa: BLE001 — 告警通道失败只降级不崩监控
        print(f"[WARN] notify 发送失败({type(e).__name__}: {e})——告警仍落盘 reports/", flush=True)


def cmd_naked_watch(once: bool = False, interval_sec: int = 180,
                    precheck_window_min: int = 30) -> int:
    """盘中裸奔实时检测。返回进程退出码：0 干净 / 1 检出台账外成交（已告警）/ 3 数据源失败。

    归属判据复用 allocate_fills_to_orders（与 22:00 审计同一条规则，单一语义源）：
    当日成交能归属当日 executor 提交记录（同 code 同向、时间、价格带、剩余量）→ 合规；
    无法归属且状态为成交类 → 疑似裸奔，立即告警。告警幂等键=fill 五元组，
    存 data/naked_watch_state.json，重复扫描不重复告警。
    2026-09-23 已预检豁免：台账外成交若在成交前 precheck_window_min 分钟内存在同 code
    的 guard_precheck_log 放行记录（verdict 非 REJECT, 口径见 _precheck_exempt_map），
    不算裸奔, 标注「已预检」——guarded_paper_trade 三步流程的单不再误报；
    豁免笔次幂等存 state["precheck_exempt"] 并在报告中独立留痕（不混入告警表）。
    """
    now = datetime.now()
    if not once:
        t = now.hour * 100 + now.minute
        if not (NAKED_WATCH_START <= t <= NAKED_WATCH_END):
            print(f"[NAKED-WATCH] 非交易时段({now:%H:%M})，循环模式拒绝启动"
                  f"（守卫 {NAKED_WATCH_START}-{NAKED_WATCH_END}；手动对账请加 --once）")
            return 2
    state_path = os.path.join(os.path.dirname(EXEC_ORDERS), "naked_watch_state.json")
    state: dict = {"alerted": []}
    if os.path.exists(state_path):
        try:
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state.get("alerted"), list):
                state["alerted"] = []
        except Exception:  # noqa: BLE001 — 状态文件损坏时重建（告警可能重复一次，可接受）
            state = {"alerted": []}
    md_path = os.path.join(REPORT_DIR, f"naked_watch_{now:%Y%m%d}.md")
    if not os.path.exists(md_path):
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# 盘中裸奔实时检测 {now:%Y%m%d}\n\n"
                    "> O12 数据源=westock 当日成交 vs executor 提交台账（allocate_fills_to_orders "
                    "同一归属规则）。本表为实时告警留痕，权威合规结论以当日 naked_audit/discipline 为准。\n\n"
                    "| 告警时刻 | 方向 | 代码 | 名称 | 价格 | 数量 | 金额(元) | 成交时间 |\n"
                    "|---|---|---|---|---|---|---|---|\n")

    def _scan() -> int:
        fills = _fetch_today_fills()  # 异常向上 → 连续失败计数
        today = now.strftime("%Y%m%d")
        orders = load_orders(today)
        allocate_fills_to_orders(fills, orders)
        cands = [f for f in fills
                 if not f.get("_match_order")
                 and f.get("status") in NAKED_FILLED_STATES]
        # 2026-09-23 已预检豁免: 有台账外成交候选才查 guard_precheck_log(无候选零开销)
        exempt_map = _precheck_exempt_map(today) if cands else {}
        fresh, exempt = [], []
        for f in cands:
            key = f"{f['code6']}|{f['action']}|{f['price']}|{f['volume']}|{f['filled_at']}"
            if key in state["alerted"]:
                continue
            if _fill_precheck_exempt(f, exempt_map, precheck_window_min):
                if key not in state.setdefault("precheck_exempt", []):
                    state["precheck_exempt"].append(key)
                    exempt.append((key, f))
                continue
            fresh.append((key, f))
        if exempt:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            with open(md_path, "a", encoding="utf-8") as f:
                f.write(f"\n### 已预检豁免 {datetime.now():%H:%M:%S}"
                        f"（成交前{precheck_window_min}分钟内同code放行记录, 不构成裸奔告警）\n\n"
                        "| 方向 | 代码 | 名称 | 价格 | 数量 | 成交时间 |\n"
                        "|---|---|---|---|---|---|\n")
                for _, x in exempt:
                    f.write(f"| {x['action']} | {x['code6']} | {x['name']} | {x['price']} "
                            f"| {x['volume']} | {x['filled_at']} |\n")
            edetail = "; ".join(
                f"{x['action']} {x['code6']} {x['name']} {x['volume']}股@{x['price']}"
                for _, x in exempt)
            print(f"[已预检] 豁免台账外成交 {len(exempt)} 笔（不算裸奔）: {edetail}", flush=True)
        if fresh:
            state["alerted"].extend(k for k, _ in fresh)
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            with open(md_path, "a", encoding="utf-8") as f:
                for _, x in fresh:
                    amt = x["price"] * x["volume"]
                    f.write(f"| {datetime.now():%H:%M:%S} | {x['action']} | {x['code6']} "
                            f"| {x['name']} | {x['price']} | {x['volume']} | {amt:,.0f} "
                            f"| {x['filled_at']} |\n")
            detail = "; ".join(
                f"{x['action']} {x['code6']} {x['name']} {x['volume']}股@{x['price']}"
                for _, x in fresh)
            print(f"[🔴NAKED] 检出台账外成交 {len(fresh)} 笔: {detail}", flush=True)
            _naked_notify("屠龙裸奔实时告警",
                          f"检出台账外成交 {len(fresh)} 笔（未过 trade_executor）：{detail}")
        elif exempt:
            print(f"[NAKED-WATCH] {datetime.now():%H:%M:%S} 对账干净"
                  f"（成交 {len(fills)} 笔，台账 {len(orders)} 单；"
                  f"台账外 {len(exempt)} 笔均已预检豁免）", flush=True)
        else:
            print(f"[NAKED-WATCH] {datetime.now():%H:%M:%S} 对账干净"
                  f"（成交 {len(fills)} 笔，台账 {len(orders)} 单，全部可归属）", flush=True)
        return len(fresh)

    if once:
        try:
            n = _scan()
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL-CLOSED] westock 成交明细获取失败({type(e).__name__}: {e})"
                  "——拒绝假成功", file=sys.stderr)
            return 3
        return 1 if n else 0

    fail_streak, deadline = 0, now.replace(hour=15, minute=10, second=0, microsecond=0)
    while datetime.now() < deadline:
        try:
            n = _scan()
            fail_streak = 0
        except Exception as e:  # noqa: BLE001
            fail_streak += 1
            print(f"[WARN] 成交明细拉取失败({type(e).__name__}: {e})，"
                  f"连续失败 {fail_streak}/3", flush=True)
            if fail_streak >= 3:
                print("[FAIL-CLOSED] 连续 3 次拉取失败，监控退出（exit 3）", file=sys.stderr)
                return 3
        time.sleep(max(60, interval_sec))
    print("[NAKED-WATCH] 到达 15:10 收盘时点，监控自动退出（资源上界）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="westock 异步成交回报监控与账本归属治理")
    p.add_argument("--expire", nargs="?", const=datetime.now().strftime("%Y%m%d"),
                   metavar="YYYYMMDD", help="收盘兜底: 未成交委托失效标注+次日复评段")
    p.add_argument("--reclassify", metavar="YYYYMMDD", help="按提交台账归属改判当日ungated行")
    p.add_argument("--bootstrap-from-log", metavar="LOG_JSON",
                   help="从buy-batch日志JSON补录提交台账(历史救援)")
    p.add_argument("--trade-date", metavar="YYYYMMDD",
                   help="配合 --bootstrap-from-log 显式指定交易日(缺省取日志文件名日期)")
    p.add_argument("--reprice-scan", action="store_true",
                   help="P1-2 改价监控: 当日未决买单 vs 白名单限价, 输出改价建议清单(只读)")
    p.add_argument("--reconcile", action="store_true",
                   help="E19 生命周期对账: 未了结行↔westock成交明细订单号直配改判"
                        "(EXPIRED_READ误标/OPEN未回填→FILLED, 真撤→CANCELLED)")
    p.add_argument("--history", action="store_true",
                   help="配合 --reconcile: 拉 recent 3月 fills 做存量清洗(首例SELL_OPEN=41)")
    p.add_argument("--naked-watch", action="store_true",
                   help="O12 盘中裸奔实时检测(循环, 交易时段 09:25-15:10 守卫到点自退)")
    p.add_argument("--once", action="store_true",
                   help="配合 --naked-watch: 单次全量对账(手动/验收, 不受时段守卫限制)")
    p.add_argument("--interval", type=int, default=180,
                   help="naked-watch 轮询间隔秒(默认180, 下限60)")
    p.add_argument("--precheck-window-min", type=int, default=30,
                   help="naked-watch 已预检豁免窗口分钟(默认30): 成交前N分钟内同code存在"
                        "guard_precheck_log 放行记录(verdict非REJECT)则不算裸奔")
    a = p.parse_args()

    # 2026-09-11 Mimosa 审计加固: argv 日期直接进报告/缓存路径拼接, 未校验可路径穿越
    for _lbl, _v in (("expire", a.expire), ("reclassify", a.reclassify)):
        if _v and not re.fullmatch(r"\d{8}", str(_v)):
            print(f"[FAIL-CLOSED] --{_lbl} 非法日期(须YYYYMMDD): {_v!r}")
            return 2

    if a.naked_watch:
        return cmd_naked_watch(once=a.once, interval_sec=a.interval,
                               precheck_window_min=a.precheck_window_min)
    if a.reconcile:
        res = cmd_reconcile(history=a.history)
    elif a.bootstrap_from_log:
        res = cmd_bootstrap(a.bootstrap_from_log, a.trade_date)
    elif a.reclassify:
        res = reclassify_traderecords(a.reclassify)
    elif a.reprice_scan:
        res = cmd_reprice_scan()
    elif a.expire:
        res = cmd_expire(a.expire)
    else:
        res = cmd_poll()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
