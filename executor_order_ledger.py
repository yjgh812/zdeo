#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""executor_order_ledger.py — executor 提交委托台账与成交归属判定（E45/E46 收口, O19/O20 核心）

背景（2026-08-26 自省 ROUND2 E45）：westock 限价委托异步成交——executor 下单后
sleep(1.5s) 的持仓差验证拍不到后续成交，真实 executor 单被盘后 reconcile 一律打
subsystems='ungated_待标注'，账本不能自证、纪律看板误伤统计（合规率 100%↔0% 双口径矛盾）。

本模块 = 两份台账的唯一 owner：
  data/executor_orders.jsonl  append-only 提交台账：每次真实提交委托成功即追加一行
                              （order_id 幂等键），reconcile 归属判定的唯一依据。
  data/open_orders.jsonl      未即时成交委托状态行：monitor 轮询更新 confirmed/status，
                              收盘仍未成交标 EXPIRED_READ（A股委托当日有效），供次日复评段(O23)。

归属判定语义（reconcile 用）：
  成交明细能唯一归属于当日 executor 提交记录（同 code 同方向、filled_at >= submitted_at、
  价格不越过限价带、累计量 ≤ 提交量）→ subsystems='trade_executor_async'；
  无法归属 → 'ungated_待标注'（真·通道外）。

路径全部为字面量常量（Mimosa 纯字面量约定）；SQL 由调用方参数绑定。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime

DATA_DIR = r"D:\zcodesj\data"
EXEC_ORDERS = os.path.join(DATA_DIR, "executor_orders.jsonl")
OPEN_ORDERS = os.path.join(DATA_DIR, "open_orders.jsonl")
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"

SUB_EXEC_ASYNC = "trade_executor_async"
SUB_UNGATED = "ungated_待标注"

# 价格匹配带宽（委托价为限价时成交价允许的费用级漂移）
PRICE_TOL = 0.005


def _append(path: str, row: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def extract_order_id(order_resp: dict) -> str | None:
    """从 executor 各处拿到的 order 响应中提取委托号。"""
    if not isinstance(order_resp, dict):
        return None
    d = order_resp.get("data")
    if isinstance(d, dict):
        oid = d.get("orderId") or d.get("order_id") or d.get("id")
        if oid:
            return str(oid)
    return order_resp.get("orderId") or order_resp.get("order_id") or None


def load_orders(trade_date: str | None = None) -> list[dict]:
    if not os.path.exists(EXEC_ORDERS):
        return []
    out = []
    with open(EXEC_ORDERS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if trade_date is None or r.get("trade_date") == trade_date:
                out.append(r)
    return out


def _code6(c: str) -> str:
    """统一裸 6 位代码（O13 2026-09-03 顺带修复）：台账历史行 code 带 sh/sz/bj 前缀，
    与 fills 裸码比较恒失配 → allocate 归属全废（9/3 naked-watch 实证 7/7 误报）。"""
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0].zfill(6)


def log_submitted(order_resp: dict, code6: str, name: str, action: str,
                  price: float, qty: int, kind: str = "single",
                  note: str = "", source: str = "",
                  source_method: str = "") -> str | None:
    """委托提交成功后登记台账（order_id 幂等）。返回 orderId 或 None(无可提取委托号)。

    source（2026-10-06 全维度自省三尾巴①）: 信号源准入字段落台账——trade_executor
    的 P0-A 准入强制 --source，但本台账此前无该列，9/28 后审计发现 25 行 source
    全空无法溯源。旧行无该字段，消费方按缺省跳过（与 c6_note 同向后兼容模式）。
    source_method（2026-11-12 溯源事故修复）: 选股来源方法id 落台账——source 记的
    是执行授权源（闸门语义），方法身份此前被 manual_confirmed 覆盖丢失（新能股份
    事故）。纯记账字段不参与闸门；旧行无该字段按缺省跳过（同向后兼容模式）。
    """
    oid = extract_order_id(order_resp)
    if not oid:
        return None
    for r in load_orders():
        if r.get("order_id") == oid:
            return oid  # 幂等：同委托只记一次
    _append(EXEC_ORDERS, {
        "order_id": oid, "code": _code6(code6), "name": name or "",
        "action": (action or "").upper(), "price": float(price or 0), "qty": int(qty or 0),
        "trade_date": datetime.now().strftime("%Y%m%d"),
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "kind": kind, "note": note, "source": source or "",
        "source_method": (source_method or "").strip(),  # 2026-11-12 溯源修复: 选股方法id, 旧行缺省跳过
        # 2026-09-03 O13: 提交即 OPEN；成交确认状态在 open_orders/trade_records，
        # 本台账 status 仅 OPEN→CANCELLED（撤单经 mark_order_status 回填）。
        "status": "OPEN",
    })
    return oid


def is_cancelled(r: dict) -> bool:
    """撤单稿判定（O13 2026-09-03）：规范字段 status='CANCELLED'；兼容读旧 cancelled 布尔
    （9/2 误挂三单撤单后行内手工标记的口径）。"""
    return r.get("status") == "CANCELLED" or bool(r.get("cancelled"))


def mark_order_status(order_id: str, status: str, note: str = "") -> bool:
    """把提交台账中指定 order_id 的行原子改写为 status（O13 2026-09-03）。

    背景：撤单信息此前不回流台账，pre_pick_checklist --auto 估算把撤单稿计入，
    致估算噪声（-22156 vs westock 真值 -1226 实证，SELF_REVIEW_ROUND7 B13）。
    order_id 不在台账时返回 False（典型=台账外直连单）——调用方必须如实上报，
    禁止静默吞掉。坏行原样保留不丢内容。
    """
    if not os.path.exists(EXEC_ORDERS):
        return False
    out_lines: list[str] = []
    hit = False
    with open(EXEC_ORDERS, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                r = json.loads(s)
            except json.JSONDecodeError:
                out_lines.append(s)
                continue
            if r.get("order_id") == order_id:
                r["status"] = str(status).upper()
                r["status_at"] = datetime.now().isoformat(timespec="seconds")
                if note:
                    r["status_note"] = note
                hit = True
            out_lines.append(json.dumps(r, ensure_ascii=False))
    if not hit:
        return False
    tmp = EXEC_ORDERS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    os.replace(tmp, EXEC_ORDERS)
    # 2026-09-10 撤单同步: 回报监控(fill_confirm_monitor)的候选池是 open_orders,
    # 只认 OPEN/PARTIAL 行; 撤单信息此前不回流 → 已撤委托被持仓差算法误确认成幻影
    # 成交双记(上海银行 9/7 两条 SELL 行实锤, 且幻影行会污染 closed_loop FIFO 配对)。
    if str(status).upper() == "CANCELLED":
        _sync_cancel_to_open_orders(order_id, note)
    return True


def _sync_cancel_to_open_orders(order_id: str, note: str = "") -> None:
    """撤单状态同步至 open_orders, 防已撤行被 poll 误确认(2026-09-10)。"""
    if not os.path.exists(OPEN_ORDERS):
        return
    out_lines: list[str] = []
    with open(OPEN_ORDERS, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                r = json.loads(s)
            except json.JSONDecodeError:
                out_lines.append(s)
                continue
            if r.get("order_id") == order_id and r.get("status") in ("OPEN", "PARTIAL"):
                r["status"] = "CANCELLED"
                r["cancelled_at"] = datetime.now().isoformat(timespec="seconds")
                if note:
                    r["cancel_note"] = note
            out_lines.append(json.dumps(r, ensure_ascii=False))
    tmp = OPEN_ORDERS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    os.replace(tmp, OPEN_ORDERS)


def log_open(code6: str, name: str, action: str, price: float, qty: int,
             base_qty: int, order_id: str | None, note: str = "",
             source: str = "") -> None:
    """executor 首次验证未成交(filled==0)时登记未决委托，供回报监控跟踪。

    note: 卖出场景携带 C6 板块判定串（准则37，2026-08-28），回报回填时落 trade_records.notes；
    旧行无该字段，消费方按缺省跳过（向后兼容）。
    source（2026-10-06 三尾巴①）: 与 executor_orders 同步落信号源，向后兼容同 note。
    """
    _append(OPEN_ORDERS, {
        "code": str(code6).zfill(6), "name": name or "", "action": (action or "").upper(),
        "price": float(price or 0), "qty": int(qty or 0),
        "base_qty": int(base_qty or 0),          # 下单前持仓基准（买卖共用绝对量对比法）
        "confirmed": 0, "status": "OPEN",
        "order_id": order_id or "",
        "trade_date": datetime.now().strftime("%Y%m%d"),
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "c6_note": note or "", "source": source or "",
    })


def load_open_rows(statuses: tuple[str, ...] = ("OPEN", "PARTIAL")) -> list[dict]:
    """读取指定状态的未决委托行（返回 dict 列表，行内容可直接改写后由 save_open_rows 落盘）。"""
    rows = []
    path = OPEN_ORDERS
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                r = {"_bad_line": True}
            r["_ln"] = i
            if statuses is None or r.get("status") in statuses:
                rows.append(r)
    return rows


def save_open_rows(all_rows: list[dict]) -> None:
    """整表回写 open_orders.jsonl（小文件，原子性靠一次性重写）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = OPEN_ORDERS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in all_rows:
            r.pop("_ln", None)
            r.pop("_bad_line", None)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, OPEN_ORDERS)


def allocate_fills_to_orders(fills: list[dict], orders: list[dict],
                             clock_skew_sec: int = 120) -> int:
    """把当日成交明细分配到当日 executor 提交记录（集合消费法，一次进程内完成）。

    对每个可归属的 fill **原地标注** ``f["_match_order"] = {"order_id":..,"price":..,"qty":..}``
    （部分成交多笔明细会指向同一订单并扣减其剩余量）。函数返回成功归属条数。

    fills: [{code6, action, price, volume, filled_at:'YYYY-MM-DD HH:MM:SS'}...]（顺序无关）
    匹配判据（从严依次校验，任一不满足视为不可归属）：
      ① 同 code6 + 同方向；② filled_at >= submitted_at - clock_skew；
      ③ 价格带宽：BUY fill ≤ limit×(1+tol)；SELL fill ≥ limit×(1-tol)；
      ④ 该订单剩余可分配量 ≥ fill.volume。
    """
    remaining = {o.get("order_id", f"_#{i}"): dict(o, code=_code6(o.get("code")),
                 _left=int(o.get("qty") or 0))
                 for i, o in enumerate(orders)}
    n_matched = 0
    for f in fills:
        c6 = str(f.get("code6") or "").zfill(6)
        act = (f.get("action") or "").upper()
        vol = int(float(f.get("volume") or 0))
        px = float(f.get("price") or 0)
        fat = str(f.get("filled_at") or "")
        fat_dt = None
        if fat:
            try:
                fat_dt = datetime.strptime(fat[:19].replace("/", "-"), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                fat_dt = None
        best_key = None
        for oid, o in remaining.items():
            if o["code"] != c6 or o["action"] != act:
                continue
            if o["_left"] < vol:
                continue
            sat = True
            if fat_dt is not None and o.get("submitted_at"):
                try:
                    sub_dt = datetime.strptime(o["submitted_at"][:19].replace("T", " "),
                                               "%Y-%m-%d %H:%M:%S")
                    if (fat_dt - sub_dt).total_seconds() < -clock_skew_sec:
                        sat = False  # 成交早于提交（超出时钟容差）→ 不是这一单
                except ValueError:
                    pass
            if not sat:
                continue
            lim = float(o.get("price") or 0)
            if act == "BUY" and lim > 0 and px > lim * (1 + PRICE_TOL):
                continue
            if act == "SELL" and lim > 0 and px < lim * (1 - PRICE_TOL):
                continue
            if best_key is None or o.get("submitted_at", "") \
                    <= remaining[best_key].get("submitted_at", ""):
                best_key = oid
        if best_key is not None:
            o = remaining[best_key]
            o["_left"] -= vol
            # 2026-08-28 准则43/E72: note(卖出理由+C6判定)随归属结果下发，导入/改判侧可恢复
            f["_match_order"] = {"order_id": o.get("order_id"), "price": float(o.get("price") or 0),
                                 "qty": int(o.get("qty") or 0), "note": o.get("note") or ""}
            n_matched += 1
    return n_matched


def reclassify_traderecords(date_str: str) -> dict:
    """把当日 trade_records 中 ungated_待标注 的成交按台账归属改判（幂等，可重复执行）。"""
    orders = load_orders(date_str)
    con = sqlite3.connect(ATTR_DB)
    rows = con.execute(
        "SELECT id, code, action, price, volume, notes FROM trade_records "
        "WHERE trade_date=? AND action IN ('BUY','SELL') AND subsystems=? ORDER BY id",
        (date_str, SUB_UNGATED)).fetchall()
    fills = [{
        "code6": str(r[1]).split(".")[-1][-6:].zfill(6) if str(r[1]).startswith(("sh", "sz", "bj"))
                 else str(r[1]).zfill(6),
        "action": r[2], "price": float(r[3] or 0), "volume": int(float(r[4] or 0)),
        "filled_at": "", "_id": r[0], "_notes": r[5] or "",
    } for r in rows]
    # 从导入行 notes '盘后审计导入 2026-08-26 09:52:38' 还原成交时刻参与时间判据
    for f in fills:
        tag = "盘后审计导入"
        if tag in f["_notes"]:
            t = f["_notes"].split(tag, 1)[1].strip()[:19]
            try:
                datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                f["filled_at"] = t
            except ValueError:
                pass
    pairs = allocate_fills_to_orders(fills, orders)
    changed = 0
    for f in fills:
        o = f.get("_match_order")
        if not o:
            continue
        # 2026-08-28 准则43/E72: 台账 note 含卖出理由+C6判定时随改判落 notes
        extra = f" | {o['note']}" if o.get("note") else ""
        new_notes = (f["_notes"] + f" | async归属 order={o.get('order_id')}" + extra).strip(" |")
        cur = con.execute(
            "UPDATE trade_records SET subsystems=?, notes=? WHERE id=?",
            (SUB_EXEC_ASYNC, new_notes, f["_id"]))
        changed += cur.rowcount
    con.commit()
    con.close()
    return {"date": date_str, "orders_in_ledger": len(orders),
            "ungated_scanned": len(fills), "reclassified_async": changed,
            "still_ungated": len(fills) - changed}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="executor 提交台账状态回填（O13 2026-09-03）")
    ap.add_argument("--mark-cancelled", metavar="ORDER_ID",
                    help="把指定委托号标记为 CANCELLED（撤单回填，幂等）")
    ap.add_argument("--mark-status", nargs=2, metavar=("ORDER_ID", "STATUS"),
                    help="通用状态回填（大写枚举：OPEN/CANCELLED）")
    a = ap.parse_args()
    if a.mark_cancelled:
        _ok = mark_order_status(a.mark_cancelled, "CANCELLED", "cancelled via ledger CLI")
    elif a.mark_status:
        _ok = mark_order_status(a.mark_status[0], a.mark_status[1], "status via ledger CLI")
    else:
        ap.error("需要 --mark-cancelled 或 --mark-status")
    print("OK" if _ok else "NOT_FOUND: order_id 不在提交台账（台账外直连单，须如实上报）")
    sys.exit(0 if _ok else 1)
