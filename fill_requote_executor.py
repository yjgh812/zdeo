# -*- coding: utf-8 -*-
"""fill_requote_executor.py — 未成交买单 pegging 二次改价执行器 v0（P1-1 / 2026-08-28）

背景（R-20260828-02，实证 reports/fill_requote_analysis_20260826.md）：
  8/26 扫买 18 笔仅 9 成交；未成交 9 笔中 7 笔当日 high≥挂单价（77.8% 盘中有机会，纯时序问题）。
  本执行器在 09:40/10:30 对未成交 BUY 单做 pegging 改价，上限=名单计划价（prev_close），绝不追高。

分类（v0，防追高/防追跌，全部本地 + 实时 quote）：
  设 ref=计划价上限(prev_close/名单价), p0=原挂单价(≈0.995×ref), cur=实时价
    DROP   cur > ref            → 追高区：撤单放弃（追高=最大亏损源，宁可等次日补单流程）
           cur < p0×0.98        → 信号深度破坏：撤单放弃（不追跌）
    REQ    p0 ≤ cur < ref       → new_price=min(ref, cur×1.002)；若 new_price>p0 → 先挂新单后立即撤老单（主人令 2026-11-06）
    HOLD   cur < p0             → 价格已低于挂单价，等待自然成交，不动
  （每单每日仅改价一次，置 requoted 标记防重复）

安全（FAIL-CLOSED）：
  - 默认 --dry-run，仅 --execute 才撤单/重挂；--execute 需 --date 当日确认
  - **真值校验（2026-10-26 缺陷修复）**：行动前拉 westock 订单真值（portfolio_paper_history），
    本地 OPEN 行若真值已成交/已撤单/系统撤单一律 SKIP 不撤不挂（execute 态回填台账），
    真值无此单(NO_LIVE_REF)不动，真值拉取失败整器拒跑 exit 3——杜绝本地滞后残影导致的重复买入
  - 新价永不超过 ref（名单计划价）；剩余量 = qty - confirmed
  - **改价顺序（主人令 2026-11-06）**：REQ=先挂新单、挂成后立即撤老单（两步间零计算；
    新单挂失败保留老单不撤；撤老失败立即重试一次仍败则标记双单并存留人工处理）
  - 改价后落账 open_orders 原行（price/order_id 更新 + requoted，老单号/新单号均留痕 note）

卖单扩展（v1 2026-11-11 主人令「批准」；缺口登记 = DAILY_WORKFLOW_SOP 三百三十八，
  11/10 中曼石油 603619 止损卖单 18.75 挂上方够不着、主人明示授权手动撤改 18.55 成交暴露）：
  设 p0=原卖单挂价, cur=实时价, floor=可接受卖价下限（CLI --floor 代码=价格 优先, 其次行 ref_price）
    HOLD     cur ≥ p0             → 市场已达挂单价，等自然成交，不动
    NO_FLOOR cur < p0 且无下限    → fail-closed 不自动改价（宁可不动单，不无授权贱卖），报表呈报待主人
    DROP     cur < floor×0.97     → 深度跌破下限3%以上=追跌区：撤单放弃不自动卖（镜像买侧追高闸，交主人/止损链）
    REQ      其余 cur < p0        → new_price=max(floor, cur×0.998)；先挂新卖单后撤老单（主人令 2026-11-06
                                    对称适用，平台实证挂单不冻结股份；双成交风险买侧主人已明示接受，卖侧同口径）
  （每单每日仅改价一次 requoted；**买侧分类/执行路径零改动**——卖侧为纯新增函数 classify_sell_row/process_sell_rows）

数据/通道：
  open rows   data/open_orders.jsonl（executor_order_ledger.load_open_rows/save_open_rows）
  实时价      westock MCP data_quote（config.json Bearer 直连 RPC，范式同 stop_loss_executor）
  撤单/重挂   portfolio_paper_cancel / portfolio_paper_trade

用法:
  python fill_requote_executor.py --date 20260828            # dry-run（只读，出决策表）
  python fill_requote_executor.py --date 20260828 --execute  # 实弹（撤单+重挂）
  python fill_requote_executor.py --date 20260828 --floor 603619=18.50 --execute
                                                             # 实弹卖单改价（--floor 给卖单价格下限）
输出: reports/fill_requote_exec_{date}.md + data/fill_requote_exec_{date}.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime

DATA_DIR = r"D:\zcodesj\data"
REPORT_DIR = r"D:\zcodesj\reports"
EXEC_ORDERS = os.path.join(DATA_DIR, "executor_orders.jsonl")
REQ_TICK = 1.002    # 改价贴近现价的让价系数（buy 需 ≥ 现价才可成交）
CHASE_TOL = 1.03     # 追高硬闸: 现价 > 计划价×1.03 才判追高(对齐 pre_trade R27b 昨收溢价>3%硬闸)
SELL_TICK = 0.998    # 卖侧让价系数（sell 需 ≤ 现价才可成交, 镜像 REQ_TICK 1.002）
SLUMP_TOL = 0.97     # 追跌硬闸: 现价 < 下限×0.97 才判深度跌破(镜像 CHASE_TOL, 不自动追跌卖出)


def _norm6(c: str) -> str:
    c = c.strip()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p) and c[len(p):].isdigit():
            return c[len(p):].zfill(6)
    return c.zfill(6) if c.isdigit() else c


def _extract_order_id(res) -> str:
    """从 portfolio_paper_trade 响应提取新委托单号(纯函数)。

    兼容两种形态: MCP包裹(res['content'][0]['text'] 内 JSON) 与 裸 dict(data.orderId)。
    任何形态不符/解析失败返回空串(调用方不回写单号, 留痕 action_detail 供人工对账)。
    """
    try:
        payload = res
        if isinstance(res, dict):
            content = res.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict):
                txt = str(content[0].get("text") or "")
                if txt:
                    payload = json.loads(txt)
        node = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(node, dict):
            node = payload if isinstance(payload, dict) else {}
        for key in ("orderId", "order_id"):
            v = node.get(key)
            if v:
                return str(v)
    except (ValueError, TypeError, AttributeError):
        return ""
    return ""


def _prefixed(code6: str) -> str:
    if code6.startswith(("sh", "sz", "bj")):
        return code6
    return ("sz" if code6[0] in "03" else "sh") + code6  # 3/0开头深市, 6开头沪市(近似,北交920未纳入)


class WsRPC:
    def __init__(self) -> None:
        url = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
        token = ""
        try:
            cfg = json.load(open(r"C:\Users\yujie\.zcode\cli\config.json", encoding="utf-8"))
            ws = cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
            token = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
        except OSError:
            pass
        self._url = url
        self._token = token or os.environ.get("WESTOCK_TOKEN", "")
        self._id = 0

    def call(self, name: str, args: dict) -> dict:
        if not self._token:
            raise RuntimeError("westock token 缺失(fail-closed)")
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                           "params": {"name": name, "arguments": args}}).encode()
        h = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(self._url, data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
        for line in raw.splitlines():
            if line.startswith("data:") and line.strip() != "data:":
                d = json.loads(line[5:].strip())
                if "result" in d:
                    return d["result"]
        raise RuntimeError(f"RPC {name} 无 result: {raw[:300]}")


def quote_map(rpc: WsRPC, code6_list: list[str],
              prev_out: dict[str, float] | None = None) -> dict[str, float]:
    """批量实时价：data_quote 返回最新价。失败返回空 dict（调用方 fail-closed 跳过该单）。
    20261119 R2: prev_out 可选参数——同一次响应顺带提取昨收(字段名多试几个, 缺失不写),
    供卖单跌停边界识别(挂价<跌停价=交易所无效单)。"""
    out: dict[str, float] = {}
    if not code6_list:
        return out
    prefixed = ",".join(_prefixed(c) for c in code6_list)
    try:
        res = rpc.call("data_quote", {"codes": prefixed})
        for c in res.get("content", []):
            if c.get("type") == "text":
                obj = json.loads(c["text"])
                data = obj.get("data") or obj
                if isinstance(data, dict):
                    items = list(data.values())          # data 为 {code: {...}} 结构
                elif isinstance(data, list):
                    items = data
                else:
                    items = [data]
                for it in items:
                    code = it.get("code") or ""
                    px = it.get("price") or it.get("latestPrice") or it.get("last") or 0
                    if code and px:
                        out[_norm6(str(code))] = float(px)
                    # R2 昨收提取(多字段名兼容, 找到才写)
                    if code and prev_out is not None:
                        pc = (it.get("prevClose") or it.get("prev_close")
                              or it.get("yesterdayClose") or it.get("preClose") or 0)
                        if pc:
                            prev_out[_norm6(str(code))] = float(pc)
    except Exception as e:
        print(f"⚠️ data_quote 失败: {e}", file=sys.stderr)
    return out


# ===== 真值校验（2026-10-26 缺陷修复）=====
# 缺陷实证（git e886e1d4 前后盘中）: 本地 open_orders 行滞后于 westock——10/26 上午 39 笔
# 提交在本地全为 OPEN，其中 6 笔 westock 已成交/已撤单；旧逻辑直接对其 cancel+重挂 =
# 对已成交单重复买入 / 对已撤单空转。行动前必须先拉 westock 订单真值做终态过滤。
LIVE_TERMINAL_FILLED = ("已成交", "部分成交")     # 西侧终态-成交类 → 本地 OPEN 行为过期残影
LIVE_TERMINAL_CANCELLED = ("已撤单", "系统撤单")  # 西侧终态-撤单类
PAGES_CAP = 20          # history 分页上界(20×200=4000 条, 防无界拉取)


def live_order_map(rpc: WsRPC, date6: str) -> dict[str, str]:
    """westock 订单真值: {order_id: live_status}。

    date=今日 → portfolio_paper_history range=today;
    历史日期 → range=history&month=YYYYMM 分页(按 orderId 集合边拉边收敛, PAGES_CAP 上界)。
    拉取失败 raise(fail-closed, 调用方拒跑)——宁可不动单, 不对盲数据撤挂。
    """
    out: dict[str, str] = {}

    def _records_of(res: dict) -> tuple[list[dict], bool]:
        for c in res.get("content", []):
            if c.get("type") == "text":
                obj = json.loads(c["text"])
                d = obj.get("data") or obj
                recs = d.get("records", []) if isinstance(d, dict) else []
                more = bool(d.get("hasMore")) if isinstance(d, dict) else False
                return recs, more
        raise RuntimeError("portfolio_paper_history 无 content/text")

    today6 = datetime.now().strftime("%Y%m%d")
    if date6 == today6:
        res = rpc.call("portfolio_paper_history", {"range": "today", "limit": 200})
        recs, _ = _records_of(res)
        for r in recs:
            oid = str(r.get("orderId") or "")
            if oid:
                out[oid] = str(r.get("status") or "")
        return out
    month = date6[:6]  # YYYYMM
    for page in range(PAGES_CAP):
        res = rpc.call("portfolio_paper_history",
                       {"range": "history", "month": month,
                        "page": page + 1, "page_size": 200})
        recs, more = _records_of(res)
        for r in recs:
            oid = str(r.get("orderId") or "")
            if oid:
                out[oid] = str(r.get("status") or "")
        if not more:
            break
    return out


def classify_row(row: dict, cur: float | None, ref: float, chase_tol: float = CHASE_TOL) -> dict:
    """单行改价决策（纯函数，无 I/O，可单测）。

    返回 dict: status ∈ {DROP, REQ, HOLD, NO_QUOTE} + 决策字段。
    DROP: 仅追高——现价 > 计划价×1.03（对齐 R27b 3% 溢价硬闸；<3% 溢价为噪声非追高）；
    REQ: 挂单价≤现价≤计划价×1.03 → 改价贴近现价但封顶计划价（获批价，非追高）；
    HOLD: 现价<挂单价（订单市场可成交，等自然成交或尾盘expire）；
    NO_QUOTE: 无实时价（fail-closed 不动）。
    """
    c6 = _norm6(row["code"])
    p0 = float(row.get("price") or 0)
    qty = int(row.get("qty") or 0)
    confirmed = int(row.get("confirmed") or 0)
    rem = qty - confirmed
    rec = {"code": row["code"], "name": row.get("name", ""), "limit": p0,
           "qty": qty, "confirmed": confirmed, "requoted": bool(row.get("requoted"))}
    if cur is None:
        rec["status"] = "NO_QUOTE"
        return rec
    rec["cur"] = cur
    if ref and cur > ref * chase_tol:
        rec["status"] = "DROP"
        rec["reason"] = "追高(现价>计划价×1.03, 对齐R27b)"
        return rec
    np_ = round(min(ref, max(cur * REQ_TICK, p0)), 2) if ref else p0
    if np_ > p0 and rem > 0 and not rec["requoted"]:
        rec["status"] = "REQ"
        rec["new_price"] = np_
        return rec
    rec["status"] = "HOLD"
    rec["reason"] = "现价低于挂单价(市场可成交)或改价无收益/已改过一次/余量0"
    return rec


def classify_sell_row(row: dict, cur: float | None, floor: float,
                      lower_lim: float = 0.0) -> dict:
    """单行卖单改价决策（纯函数，无 I/O，可单测；2026-11-11 主人批准扩展）。

    返回 dict: status ∈ {HOLD, NO_FLOOR, DROP, REQ, NO_QUOTE} + 决策字段。
    floor 缺失/≤0 且挂上方够不着 → NO_FLOOR（fail-closed：宁可不动单，不无授权贱卖）。
    DROP: cur < floor×0.97 深度跌破下限3%以上 = 追跌区（镜像买侧 CHASE_TOL 追高硬闸），
          撤单放弃不自动卖，交主人/止损链处置。
    REQ: 挂上方够不着且现价未跌破下限 → new_price=max(floor, cur×0.998) 封底下限
         （floor 是已授权最低卖价，非无授权贱卖）。
    HOLD: 现价≥挂单价（市场可达挂单价，等自然成交）或改价无收益/已改过一次/余量0。
    20261119 R2: lower_lim=当日跌停价(昨收×(1-幅度))。挂单价 p0 < lower_lim 时该单为
    交易所无效单(11/19慈文传媒实证: 挂7.31<跌停7.38 永不撮合), "现价≥挂价=可成交"假设
    失效 → 判 REQ 重挂跌停价(优先于一切HOLD分支)。lower_lim=0(缺昨收)时跳过本检查
    保持原行为 fail-safe。
    """
    p0 = float(row.get("price") or 0)
    qty = int(row.get("qty") or 0)
    confirmed = int(row.get("confirmed") or 0)
    rem = qty - confirmed
    rec = {"code": row["code"], "name": row.get("name", ""), "limit": p0,
           "qty": qty, "confirmed": confirmed,
           "requoted": bool(row.get("requoted")), "side": "SELL", "floor": floor or None}
    if cur is None:
        rec["status"] = "NO_QUOTE"
        return rec
    rec["cur"] = cur
    # R2 跌停无效单识别: 挂价低于跌停价 = 废单, 撤旧重挂跌停价(未改过价时)
    if lower_lim and lower_lim > 0 and p0 < lower_lim:
        if rem > 0 and not rec["requoted"]:
            rec["status"] = "REQ"
            rec["new_price"] = lower_lim
            rec["reason"] = (f"废单重挂: 挂价{p0}<跌停价{lower_lim}(交易所不撮合, "
                             f"重挂跌停价排队)")
            return rec
        rec["status"] = "HOLD"
        rec["reason"] = (f"挂价{p0}<跌停价{lower_lim}为废单但已改过价/余量0, "
                         f"交主人处置(fail-closed)")
        return rec
    if not floor or floor <= 0:
        if cur < p0:
            rec["status"] = "NO_FLOOR"
            rec["reason"] = "挂上方够不着且无价格下限(fail-closed不自动改价, 需主人给下限)"
        else:
            rec["status"] = "HOLD"
            rec["reason"] = "现价已达挂单价(市场可成交)"
        return rec
    if cur < floor * SLUMP_TOL:
        rec["status"] = "DROP"
        rec["reason"] = f"深度跌破下限(现价{cur}<下限{floor}×0.97, 镜像买侧追高闸, 交主人/止损链)"
        return rec
    np_ = round(max(floor, cur * SELL_TICK), 2)
    if cur < p0 and np_ < p0 and rem > 0 and not rec["requoted"]:
        rec["status"] = "REQ"
        rec["new_price"] = np_
        return rec
    rec["status"] = "HOLD"
    rec["reason"] = ("现价已达挂单价(市场可成交)" if cur >= p0
                     else "下限≥挂单价/改价无收益或已改过一次/余量0")
    return rec


def process_sell_rows(a, rpc: WsRPC, live: dict[str, str], q: dict[str, float],
                      sell_rows: list[dict], floor_map: dict[str, float],
                      decided: list[dict],
                      prev_map: dict[str, float] | None = None) -> bool:
    """卖单 pegging 处理（2026-11-11 主人批准扩展；买侧路径零触碰）。

    真值终态语义与买侧同源（LIVE_TERMINAL_* / NO_LIVE_REF 一律不撤不挂）。
    行动顺序镜像买侧（主人令 2026-11-06 对称适用）：REQ=先挂新卖单、挂成后立即撤老单
    （两步间零计算；新单挂失败 fail-closed 保留老单；撤老失败重试一次仍败则标记双单并存留人工）。
    返回 changed（execute 态有撤/挂动作时 True）。
    """
    changed = False
    prev_map = prev_map or {}   # 20261119 R2: 昨收表(缺省空=fail-safe跳过跌停检查)
    from executor_order_ledger import mark_order_status
    for r in sell_rows:
        oid = str(r.get("order_id") or "")
        live_st = live.get(oid)
        c6 = _norm6(r["code"])
        rec = {"code": r["code"], "name": r.get("name", ""), "side": "SELL",
               "limit": float(r.get("price") or 0), "qty": int(r.get("qty") or 0),
               "confirmed": int(r.get("confirmed") or 0),
               "requoted": bool(r.get("requoted")), "live": live_st}
        # ---- 真值终态/无真值引用: 一律跳过不撤不挂（语义同买侧 2026-10-26 修复）----
        if live_st is None:
            rec["status"] = "SKIP_LIVE"
            rec["reason"] = "westock真值无此单(NO_LIVE_REF), 不对盲单撤挂"
            rec["action_taken"] = ("skip_no_mark(无真值引用不擅标)" if a.execute
                                   else "dry-run(skip)")
            decided.append(rec)
            continue
        if live_st in LIVE_TERMINAL_FILLED or live_st in LIVE_TERMINAL_CANCELLED:
            rec["status"] = "SKIP_LIVE"
            rec["reason"] = f"westock真值={live_st}, 本地OPEN行为滞后残影"
            if a.execute:
                mark = ("FILLED" if live_st in LIVE_TERMINAL_FILLED else "CANCELLED")
                if oid:
                    mark_order_status(oid, mark, note="fill_requote卖侧真值校验回填 20261111扩展")
                    rec["action_taken"] = f"ledger_marked_{mark}"
                else:
                    rec["action_taken"] = "skip_no_mark(无真值引用不擅标)"
            else:
                rec["action_taken"] = "dry-run(skip)"
            decided.append(rec)
            continue
        # ---- 正常决策: 下限 = CLI --floor(优先) 或 行 ref_price ----
        floor = floor_map.get(c6) or float(r.get("ref_price") or 0)
        # 20261119 R2: 跌停价=昨收×(1-幅度), 昨收取行情prev_map(quote_map顺带提取),
        # 缺失时 lower_lim=0 → classify_sell_row 跳过废单检查保持原行为 fail-safe
        lower_lim = 0.0
        _pc = prev_map.get(c6, 0)
        if _pc > 0:
            try:
                from _common import limit_prices
                lower_lim = limit_prices(r["code"], _pc)[1]
            except Exception:
                lower_lim = 0.0
        rec.update(classify_sell_row(r, q.get(c6), floor, lower_lim))
        if rec["status"] in ("DROP", "REQ") and a.execute and not rec["requoted"]:
            if rec["status"] == "REQ":
                # 先挂新卖单、挂成后立即撤老单（镜像买侧主人令 2026-11-06）
                try:
                    res = rpc.call("portfolio_paper_trade", {
                        "code": r["code"], "direction": "sell",
                        "price": rec["new_price"],
                        "quantity": rec["qty"] - rec["confirmed"]})
                    new_oid = _extract_order_id(res)
                    r["price"] = rec["new_price"]
                    r["requoted"] = True
                    r["note"] = (r.get("note") or "") + \
                        f"|REQUOTE-SELL p0->{rec['new_price']} rem={rec['qty']-rec['confirmed']}"
                    if new_oid:
                        r["order_id"] = new_oid
                        if oid:
                            r["note"] += f"|老单号{oid[-12:]}"
                        r["note"] += f"|新单号{new_oid[-12:]}"
                    else:
                        if oid:
                            r["note"] += f"|老单号{oid[-12:]}(已撤)新单号未取到(需人工对账)"
                        else:
                            r["note"] += "|新单号未取到(需人工对账)"
                    rec["action_taken"] = f"placed_then_cancel@{rec['new_price']}x{rec['qty']-rec['confirmed']}"
                    rec["action_detail"] = str(res)[:200]
                    changed = True
                    if oid:  # 挂成后立即撤老单(两步间零计算); 失败立即重试一次, 仍败留双单并存标记
                        try:
                            rpc.call("portfolio_paper_cancel", {"order_id": oid})
                        except Exception:
                            try:
                                rpc.call("portfolio_paper_cancel", {"order_id": oid})
                                rec["cancel_retried"] = True
                            except Exception as ce:
                                rec["cancel_failed"] = str(ce)[:120]
                                rec["double_live"] = True
                                r["note"] += "|老单未撤成双单并存需人工撤⚠️"
                except Exception as e:
                    # 新单挂失败 → fail-closed 保留老单原样
                    rec["action_taken"] = f"requote_place_failed_keep_old:{e}"
            else:  # DROP: 只撤老单无新单（深度跌破下限, 不自动追跌卖出, 交主人/止损链）
                try:
                    if oid:
                        rpc.call("portfolio_paper_cancel", {"order_id": oid})
                    r["status"] = "CANCELLED"
                    r["note"] = (r.get("note") or "") + f"|REQUOTE-SELL-DROP {rec['reason']}"
                    rec["action_taken"] = "cancelled"
                    changed = True
                except Exception as e:
                    rec["action_taken"] = f"cancel_failed:{e}"
        elif rec["status"] == "NO_FLOOR" and a.execute:
            rec["action_taken"] = "no_action_fail_closed(无下限不自动改价)"
        elif not a.execute:
            rec["action_taken"] = "dry-run"
        decided.append(rec)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="未成交单 pegging 改价执行器(BUY+SELL, fail-closed)")
    ap.add_argument("--date", required=True, help="交易日 YYYYMMDD")
    ap.add_argument("--execute", action="store_true", help="实弹: 撤单+重挂(默认 dry-run 只读)")
    ap.add_argument("--floor", default="",
                    help="卖单改价价格下限(代码=价格,逗号分隔,如 603619=18.50);缺省时卖单 fail-closed 不自动改价")
    a = ap.parse_args()
    floor_map: dict[str, float] = {}
    for tok in a.floor.split(","):
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        try:
            floor_map[_norm6(k.strip())] = float(v)
        except ValueError:
            print(f"⚠️ --floor 片段无效忽略: {tok}", file=sys.stderr)

    from executor_order_ledger import load_open_rows, save_open_rows, _append
    all_rows = load_open_rows(None)
    rows = [r for r in all_rows if r.get("action") == "BUY"
            and str(r.get("trade_date", "")) == a.date
            and r.get("status") in ("OPEN", "PARTIAL")
            and not r.get("_bad_line")]
    sell_rows = [r for r in all_rows if r.get("action") == "SELL"
                 and str(r.get("trade_date", "")) == a.date
                 and r.get("status") in ("OPEN", "PARTIAL")
                 and not r.get("_bad_line")]
    if not rows and not sell_rows:
        print(f"[{a.date}] 无当日未成交 BUY/SELL 单（dry-run 空验证）")
        return 0

    rpc = WsRPC()
    # ---- 真值校验(fail-closed, 2026-10-26 缺陷修复): 行动前先拉 westock 订单真值 ----
    # 本地 OPEN 行滞后于真值时, 旧逻辑会对已成交单重挂=重复买入。拉取失败直接拒跑。
    try:
        live = live_order_map(rpc, a.date)
    except Exception as e:
        print(f"⛔ westock 订单真值拉取失败, 拒绝运行(fail-closed): {e}", file=sys.stderr)
        return 3
    from executor_order_ledger import mark_order_status
    checked: list[dict] = []
    for r in rows:
        oid = str(r.get("order_id") or "")
        st = live.get(oid)
        if st is None:
            # 真值侧无此单(跨通道单/参数异常) → 不动(fail-closed, 不对盲单撤挂)
            checked.append({"row": r, "live": "NO_LIVE_REF"})
        elif st in LIVE_TERMINAL_FILLED:
            checked.append({"row": r, "live": st})      # 已成交残影 → 跳过, execute 态回填 FILLED
        elif st in LIVE_TERMINAL_CANCELLED:
            checked.append({"row": r, "live": st})      # 已撤单残影 → 跳过, execute 态回填 CANCELLED
        else:
            checked.append({"row": r, "live": st})      # 待成交 → 正常决策

    rows = [c["row"] for c in checked]
    codes = sorted({_norm6(r["code"]) for r in rows} | {_norm6(r["code"]) for r in sell_rows})
    prev_map: dict[str, float] = {}   # R2: 同一次行情顺带提取昨收(卖侧跌停边界用)
    q = quote_map(rpc, codes, prev_out=prev_map)

    decided: list[dict] = []
    changed = False
    for ck in checked:
        r = ck["row"]
        live_st = ck["live"]
        c6 = _norm6(r["code"])
        rec = {"code": r["code"], "name": r.get("name", ""), "limit": float(r.get("price") or 0),
               "qty": int(r.get("qty") or 0), "confirmed": int(r.get("confirmed") or 0),
               "requoted": bool(r.get("requoted")), "live": live_st}
        # ---- 真值终态/无真值引用: 一律跳过不撤不挂(2026-10-26 修复核心) ----
        if live_st in LIVE_TERMINAL_FILLED or live_st in LIVE_TERMINAL_CANCELLED or live_st == "NO_LIVE_REF":
            rec["status"] = "SKIP_LIVE"
            rec["reason"] = f"westock真值={live_st}, 本地OPEN行为滞后残影"
            if a.execute:
                oid = str(r.get("order_id") or "")
                mark = ("FILLED" if live_st in LIVE_TERMINAL_FILLED
                        else "CANCELLED" if live_st in LIVE_TERMINAL_CANCELLED else "")
                if oid and mark:
                    mark_order_status(oid, mark, note="fill_requote真值校验回填 20261026修复")
                    rec["action_taken"] = f"ledger_marked_{mark}"
                else:
                    rec["action_taken"] = "skip_no_mark(无真值引用不擅标)"
            else:
                rec["action_taken"] = "dry-run(skip)"
            decided.append(rec)
            continue
        ref = float(r.get("ref_price") or 0)
        p0 = float(r.get("price") or 0)
        if not ref:
            ref = p0 / 0.995 if p0 else 0  # 老行无 ref_price 时反推
        rec = classify_row(r, q.get(c6), ref)
        rec["live"] = live_st
        if rec["status"] in ("DROP", "REQ") and a.execute and not rec["requoted"]:
            oid = r.get("order_id") or ""
            if rec["status"] == "REQ":
                # ---- 主人令 2026-11-06: 未成交买单改价 = 先挂新单、挂成后立即撤老单 ----
                # 先撤后挂会在慢计算/慢动作期间留下「账上无单」空窗错过成交; 平台实证
                # (8/13高德红外)老单未撤时新单挂得进(不冻结资金), 代价=秒级窗口内价格
                # 崩到老单价双成交(主人明示接受)。两步之间不插入任何计算; 新单挂失败
                # fail-closed: 保留老单原样, 绝不先撤后挂。
                try:
                    res = rpc.call("portfolio_paper_trade", {
                        "code": r["code"], "direction": "buy",
                        "price": rec["new_price"],
                        "quantity": rec["qty"] - rec["confirmed"]})
                    new_oid = _extract_order_id(res)
                    r["price"] = rec["new_price"]
                    r["requoted"] = True
                    r["note"] = (r.get("note") or "") + \
                        f"|REQUOTE p0->{rec['new_price']} rem={rec['qty']-rec['confirmed']}"
                    if new_oid:
                        r["order_id"] = new_oid
                        if oid:
                            r["note"] += f"|老单号{oid[-12:]}"
                        r["note"] += f"|新单号{new_oid[-12:]}"
                    else:
                        if oid:
                            r["note"] += f"|老单号{oid[-12:]}(已撤)新单号未取到(需人工对账)"
                        else:
                            r["note"] += "|新单号未取到(需人工对账)"
                    rec["action_taken"] = f"placed_then_cancel@{rec['new_price']}x{rec['qty']-rec['confirmed']}"
                    rec["action_detail"] = str(res)[:200]
                    changed = True
                    if oid:  # 挂成后立即撤老单(两步间零计算); 失败立即重试一次, 仍败留双单并存标记
                        try:
                            rpc.call("portfolio_paper_cancel", {"order_id": oid})
                        except Exception:
                            try:
                                rpc.call("portfolio_paper_cancel", {"order_id": oid})
                                rec["cancel_retried"] = True
                            except Exception as ce:
                                rec["cancel_failed"] = str(ce)[:120]
                                rec["double_live"] = True
                                r["note"] += "|老单未撤成双单并存需人工撤⚠️"
                except Exception as e:
                    # 新单挂失败 → fail-closed 保留老单原样(先挂后撤的核心收益)
                    rec["action_taken"] = f"requote_place_failed_keep_old:{e}"
            else:  # DROP: 只撤老单无新单, 顺序语义不变
                try:
                    if oid:
                        rpc.call("portfolio_paper_cancel", {"order_id": oid})
                    r["status"] = "CANCELLED"
                    r["note"] = (r.get("note") or "") + f"|REQUOTE-DROP {rec['reason']}"
                    rec["action_taken"] = "cancelled"
                    changed = True
                except Exception as e:
                    rec["action_taken"] = f"cancel_failed:{e}"
        elif not a.execute:
            rec["action_taken"] = "dry-run"
        decided.append(rec)

    # ---- 卖单 pegging（2026-11-11 主人批准扩展；买侧上方路径零触碰）----
    # 20261119 R2: 传入昨收表供跌停边界识别(挂价<跌停价=废单自动重挂)
    changed = process_sell_rows(a, rpc, live, q, sell_rows, floor_map, decided,
                                prev_map=prev_map) or changed

    if a.execute and changed:
        save_open_rows(all_rows)
        print("✅ open_orders 已回写")

    # 产物
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, f"fill_requote_exec_{a.date}.json"), "w",
              encoding="utf-8") as f:
        json.dump({"date": a.date, "execute": a.execute, "rows": decided},
                  f, ensure_ascii=False, indent=1)
    cnt = {}
    for d in decided:
        cnt[d["status"]] = cnt.get(d["status"], 0) + 1
    lines = [f"# 未成交单 pegging 改价（BUY+SELL）{'实弹' if a.execute else 'DRY-RUN(只读)'} {a.date}\n",
             f"**决策分布**: {cnt}\n\n",
             "| 代码 | 名称 | 方向 | 挂单价 | 现价 | 真值状态 | 决策 | 动作 |\n",
             "|------|------|------|--------|------|----------|------|------|\n"]
    for d in decided:
        lines.append(f"| {d['code']} | {d['name']} | {d.get('side', 'BUY')} | {d.get('limit','-')} | {d.get('cur','-')} "
                     f"| {d.get('live','-')} | **{d['status']}** | {d.get('action_taken','-')} |\n")
    md = os.path.join(REPORT_DIR, f"fill_requote_exec_{a.date}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"📄 {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
