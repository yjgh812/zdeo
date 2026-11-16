#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""monthly_ledger_recon.py -- 屠龙系统月度账本双口径对账脚本（westock模拟盘 vs 本地attribution.db）

用法:
    python monthly_ledger_recon.py --month 202609 --mode mini
    python monthly_ledger_recon.py --month 202609 --mode full --westock-json D:/zcodesj/tmp/westock_orders_snapshot_20260923.json

模式:
    mini  只做本地侧(attribution.db)统计, 不读 westock
    full  额外解析调用方(知微/晨链会话)用 MCP 导出的 westock 订单快照 json, 做双口径并列差异

约束:
    - SQL 全部参数绑定, 禁止字符串拼接
    - sqlite 一律只读连接 (file:...?mode=ro), 本脚本绝不写库
    - 路径一律正斜杠
    - 输出 reports/monthly_ledger_recon_<yyyymmdd>.md (脚本目录下)
"""

import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
REPORTS_DIR = os.path.join(BASE_DIR, "reports").replace("\\", "/")

REALIZED_CONFLICT_NOTE = (
    "**realizedProfit 矛盾解释（固定文案）**: westock 模拟盘接口的 realizedProfit 口径**仅统计当前持仓**，"
    "已平仓部分的盈亏不在该字段内，因此其数值天然趋近于零；本地 attribution.db position_history 口径**含当月全部已平仓头寸**，"
    "故 9 月已实现盈亏为负且非零。两者不是同一口径，不能直接对数；月度账本以本地 position_history（exit_date 当月）为准，"
    "westock realizedProfit 仅作持仓侧参考。"
)


def norm_path(p):
    return str(p).replace("\\", "/")


def find_attribution_db():
    """glob 定位真实 attribution.db：必须含 trade_records 表，取文件最大者。
    根目录 D:/zcodesj/attribution.db 是残缺壳（仅 discipline_daily），会被自动排除。"""
    candidates = sorted(glob.glob("D:/zcodesj/**/attribution.db", recursive=True))
    best = None
    for p in candidates:
        p = norm_path(p)
        try:
            con = sqlite3.connect("file:" + p + "?mode=ro", uri=True)
            row = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", ("trade_records",)
            ).fetchone()
            size = os.path.getsize(p)
            con.close()
        except sqlite3.Error:
            continue
        if row and (best is None or size > best[1]):
            best = (p, size)
    if best is None:
        raise SystemExit("ERROR: 未找到含 trade_records 的 attribution.db（glob D:/zcodesj/**/attribution.db）")
    return best[0]


def connect_ro(db_path):
    return sqlite3.connect("file:" + norm_path(db_path) + "?mode=ro", uri=True)


def month_bounds(month):
    y, m = int(month[:4]), int(month[4:6])
    start = y * 10000 + m * 100
    if m == 12:
        end = (y + 1) * 10000 + 101
    else:
        end = y * 10000 + (m + 1) * 100
    return start, end


def local_side(db_path, month):
    """本地侧：trade_records 按 action 分组 + position_history 已实现盈亏。SQL 全参数绑定。"""
    start, end = month_bounds(month)
    con = connect_ro(db_path)
    rows = con.execute(
        "SELECT action, COUNT(*), ROUND(SUM(COALESCE(amount,0)),2) "
        "FROM trade_records WHERE trade_date>=? AND trade_date<? GROUP BY action",
        (str(start), str(end)),
    ).fetchall()
    total_row = con.execute(
        "SELECT COUNT(*), ROUND(SUM(COALESCE(amount,0)),2), MIN(trade_date), MAX(trade_date) "
        "FROM trade_records WHERE trade_date>=? AND trade_date<?",
        (str(start), str(end)),
    ).fetchone()
    pnl_row = con.execute(
        "SELECT COUNT(*), ROUND(SUM(COALESCE(pnl_amount,0)),2), MIN(exit_date), MAX(exit_date) "
        "FROM position_history WHERE exit_date>=? AND exit_date<?",
        (str(start), str(end)),
    ).fetchone()
    con.close()
    by_action = {r[0]: {"n": r[1], "amount": r[2]} for r in rows}
    return {
        "by_action": by_action,
        "total_n": total_row[0],
        "total_amount": total_row[1] or 0.0,
        "tr_min": total_row[2],
        "tr_max": total_row[3],
        "pnl_n": pnl_row[0],
        "pnl_sum": pnl_row[1] or 0.0,
        "pnl_exit_min": pnl_row[2],
        "pnl_exit_max": pnl_row[3],
    }


def load_westock_json(path):
    """兼容三种形态: 裸数组 / {records:[...]} / {data:{records:[...]}} / 本脚本导出的带 meta 包装。"""
    with open(norm_path(path), encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data, {}
    if isinstance(data, dict):
        recs = data.get("records") or (data.get("data") or {}).get("records")
        meta = {k: v for k, v in data.items() if k != "records" and k != "data"}
        return (recs or []), meta
    return [], {}


def westock_side(records, month):
    """westock 侧统计：当月订单。注意订单快照不含 realizedProfit 字段（该字段在持仓接口，口径仅当前持仓）。"""
    start, end = month_bounds(month)
    month_recs = []
    for r in records:
        fa = str(r.get("filledAt", ""))[:10].replace("-", "")
        if fa.isdigit() and start <= int(fa) < end:
            month_recs.append(r)
    by_dir = defaultdict(lambda: [0, 0.0])
    by_status = Counter()
    for r in month_recs:
        d = str(r.get("direction", "")).lower()
        amt = float(r.get("amount") or 0)
        by_dir[d][0] += 1
        by_dir[d][1] += amt
        by_status[str(r.get("status", ""))] += 1
    return {
        "month_n": len(month_recs),
        "by_direction": {k: {"n": v[0], "amount": round(v[1], 2)} for k, v in by_dir.items()},
        "by_status": dict(by_status),
        "realized_profit_in_json": month_recs[0].get("realizedProfit", "(订单快照无此字段)")
        if month_recs else "(无当月记录)",
        "meta_realized": None,
    }


def diff_by_code_date(records, db_path, month):
    """按 code+日期 匹配 westock 当月已成交单 vs 本地 trade_records，列出匹配不上的单。"""
    start, end = month_bounds(month)
    con = connect_ro(db_path)
    local_keys = {
        (r[0], str(r[1]))
        for r in con.execute(
            "SELECT code, trade_date FROM trade_records WHERE trade_date>=? AND trade_date<?",
            (str(start), str(end)),
        )
    }
    con.close()
    west_only, local_only_missing = [], set(local_keys)
    for r in records:
        d8 = str(r.get("filledAt", ""))[:10].replace("-", "")
        if not (d8.isdigit() and start <= int(d8) < end):
            continue
        if str(r.get("status", "")) != "已成交":
            continue
        key = (str(r.get("code", "")).lower(), d8)
        west_only_missing = key not in local_keys
        if west_only_missing:
            west_only.append((key[0], key[1], r.get("direction"), r.get("amount"), r.get("name", "")))
        local_only_missing.discard(key)
    return west_only, sorted(local_only_missing)


def main():
    ap = argparse.ArgumentParser(description="月度账本双口径对账")
    ap.add_argument("--month", default=datetime.now().strftime("%Y%m"), help="对账月份 YYYYMM，默认当月")
    ap.add_argument("--mode", choices=["mini", "full"], default="mini", help="mini=仅本地; full=含westock快照")
    ap.add_argument("--westock-json", dest="westock_json", default=None, help="westock订单快照json路径(full模式必填)")
    ap.add_argument("--db", default=None, help="可选: 直接指定 attribution.db 路径(默认自动glob)")
    args = ap.parse_args()

    db_path = norm_path(args.db) if args.db else find_attribution_db()
    local = local_side(db_path, args.month)
    today = datetime.now().strftime("%Y%m%d")

    lines = []
    lines.append("# 月度账本对账报告 %s（%s月, %s模式）" % (today, args.month, args.mode))
    lines.append("")
    lines.append("- 生成时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 本地库: " + db_path + "（只读连接）")
    lines.append("")

    # ① westock 侧
    west = None
    if args.mode == "full":
        if not args.westock_json:
            raise SystemExit("ERROR: --mode full 需要 --westock-json")
        records, meta = load_westock_json(args.westock_json)
        west = westock_side(records, args.month)
        lines.append("## ① westock 侧（订单快照: %s）" % norm_path(args.westock_json))
        lines.append("")
        if meta:
            lines.append("- 快照元信息: " + json.dumps({k: v for k, v in meta.items() if not isinstance(v, list)}, ensure_ascii=False))
        lines.append("- 当月订单笔数: %d" % west["month_n"])
        for d, v in sorted(west["by_direction"].items()):
            lines.append("  - %s: %d 笔 / %.2f 元" % (d, v["n"], v["amount"]))
        lines.append("- 状态分布: " + json.dumps(west["by_status"], ensure_ascii=False))
        lines.append("- 已实现盈亏字段(realizedProfit): %s —— 订单快照接口不含该字段；该字段位于持仓接口且口径仅当前持仓" % west["realized_profit_in_json"])
        lines.append("")

    # ② 本地侧
    lines.append("## ② 本地侧 attribution.db（%s月）" % args.month)
    lines.append("")
    lines.append("- trade_records 当月成交记录: %d 笔 / %.2f 元（日期 %s ~ %s）"
                 % (local["total_n"], local["total_amount"], local["tr_min"], local["tr_max"]))
    for a in sorted(local["by_action"]):
        v = local["by_action"][a]
        lines.append("  - %s: %d 笔 / %.2f 元" % (a, v["n"], v["amount"]))
    lines.append("- 已实现盈亏(position_history.exit_date 当月): %d 笔 / **%.2f 元**（exit %s ~ %s）"
                 % (local["pnl_n"], local["pnl_sum"], local["pnl_exit_min"], local["pnl_exit_max"]))
    lines.append("")

    # ③ 双口径并列差异
    if args.mode == "full":
        lines.append("## ③ 双口径并列差异（按 code+日期 匹配）")
        lines.append("")
        lines.append("| 口径 | 当月笔数 | 成交额(元) | 已实现盈亏(元) |")
        lines.append("|---|---|---|---|")
        w_buy = west["by_direction"].get("buy", {"n": 0, "amount": 0})
        w_sell = west["by_direction"].get("sell", {"n": 0, "amount": 0})
        lines.append("| westock(订单快照) | %d | %.2f | 无字段(持仓接口才有, 仅当前持仓) |"
                     % (west["month_n"], w_buy["amount"] + w_sell["amount"]))
        lines.append("| 本地(attribution) | %d | %.2f | %.2f |"
                     % (local["total_n"], local["total_amount"], local["pnl_sum"]))
        lines.append("")
        west_only, local_only = diff_by_code_date(records, db_path, args.month)
        lines.append("### westock 有而本地无（code+日期匹配不上，%d 条）" % len(west_only))
        lines.append("")
        for c, d, dr, amt, nm in west_only:
            lines.append("- %s %s %s %s 元（%s）" % (d, c, nm, amt, d))
        lines.append("")
        lines.append("### 本地有而 westock 快照无（%d 条）" % len(local_only))
        lines.append("")
        for c, d in local_only:
            lines.append("- %s %s" % (c, d))
        lines.append("")

    # ④ realizedProfit 矛盾解释段（固定文案）
    lines.append("## ④ " + REALIZED_CONFLICT_NOTE)
    lines.append("")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    out_path = os.path.join(REPORTS_DIR, "monthly_ledger_recon_%s.md" % today).replace("\\", "/")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[OK] 报告已生成: " + out_path)
    print("[INFO] db=" + db_path + " month=" + args.month + " mode=" + args.mode)
    print("[INFO] local: %d笔 %.2f元, pnl=%.2f" % (local["total_n"], local["total_amount"], local["pnl_sum"]))
    if west is not None:
        print("[INFO] westock: %d笔当月订单" % west["month_n"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
