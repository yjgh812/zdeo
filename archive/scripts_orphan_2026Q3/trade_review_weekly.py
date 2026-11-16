#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trade_review_weekly.py — A2 trade_review 周报生成器（2026-08-17 知微·A2 落地）

【用途】
  基于 buy/sell_tracker（主库）+ dual_ledger 双账簿（归因库）生成周报：
  胜率（买入/卖出正确率）、持有期、遗憾率（卖出后踏空）、三账核对段、dual_ledger 分组。
  写入主库 trade_review 表（feedback_engine.py DDL 已建，本脚本补写入逻辑）。

【口径】
  - verdict 复用 feedback_engine.py 逻辑：BUY D5>0→BUY_RIGHT、D5<-2%→BUY_WRONG、否则 NEUTRAL；
    SELL D5<0→SELL_RIGHT、D5>5%→SELL_WRONG、否则 NEUTRAL
  - 遗憾率 = 卖出后 20 日内 max_post_drawup>5% 的占比
  - dual_ledger 双账簿：list_membership × subsystems 分组，**过滤 price>0 AND amount>0**
    （剔除 session_reconcile_20260815 的 17 条 price=0.0 脏数据）
  - verdict 未出（PENDING）的行不算入胜率分子分母，另列 pending 计数

【用法】
  python trade_review_weekly.py                      # dry-run：只输出 Markdown 报告
  python trade_review_weekly.py --write              # 写 trade_review 表（幂等，review_date UNIQUE）
  python trade_review_weekly.py --since 20260810     # 指定起始日（默认本周一）
  python trade_review_weekly.py --end 20260816       # 指定截止日（默认今日）
  python trade_review_weekly.py --json               # JSON 摘要输出
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys
from collections import Counter

DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
REPORT_DIR = r"D:\zcodesj\reports"
BACKUP_DIR = r"D:\deepseeksj\deliverables\backups\A2_20260817"

REGRET_THRESHOLD = 0.05  # 卖出后20日涨幅>5% 计为踏空遗憾


def ro(path):
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


def norm_date(d):
    return str(d).replace("-", "")[:8]


def this_monday():
    today = datetime.date.today()
    return (today - datetime.timedelta(days=today.weekday())).strftime("%Y%m%d")


def buy_verdict(d5):
    """BUY 判定口径（对齐 feedback_engine）：D5>0→RIGHT；D5<-2%→WRONG；否则 NEUTRAL"""
    if d5 is None:
        return None
    if d5 > 0:
        return "BUY_RIGHT"
    if d5 < -0.02:
        return "BUY_WRONG"
    return "BUY_NEUTRAL"


def sell_verdict(d5):
    if d5 is None:
        return None
    if d5 < 0:
        return "SELL_RIGHT"
    if d5 > 0.05:
        return "SELL_WRONG"
    return "SELL_NEUTRAL"


def main():
    ap = argparse.ArgumentParser(description="A2 trade_review 周报生成器")
    ap.add_argument("--write", action="store_true", help="写 trade_review 表（默认 dry-run）")
    ap.add_argument("--since", default=this_monday(), help="起始日 YYYYMMDD（默认本周一）")
    ap.add_argument("--end", default=datetime.date.today().strftime("%Y%m%d"), help="截止日 YYYYMMDD（默认今日）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    since, end = args.since, args.end
    db = ro(DRAGON_DB)
    db.row_factory = sqlite3.Row
    attr = ro(ATTR_DB)
    attr.row_factory = sqlite3.Row

    # ---------- 1) buy_tracker（日期格式兼容：库内 '2026-08-13' vs 参数 '20260810'） ----------
    since_dash = f"{since[:4]}-{since[4:6]}-{since[6:]}"
    end_dash = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    buys = [dict(r) for r in db.execute(
        "SELECT * FROM buy_tracker WHERE buy_date BETWEEN ? AND ? ORDER BY buy_date",
        (since_dash, end_dash))]
    # ---------- 2) sell_tracker ----------
    sells = [dict(r) for r in db.execute(
        "SELECT * FROM sell_tracker WHERE sell_date BETWEEN ? AND ? ORDER BY sell_date",
        (since_dash, end_dash))]
    db.close()

    # ---------- 3) dual_ledger：归因库 trade_records（过滤脏数据） ----------
    ledger = [dict(r) for r in attr.execute(
        """SELECT list_membership, subsystems, action, amount, code, name, trade_date
           FROM trade_records
           WHERE price>0 AND amount>0 AND trade_date BETWEEN ? AND ?
           ORDER BY trade_date""", (since, end))]
    # 全量（不限区间）用于三账核对
    ledger_all = [dict(r) for r in attr.execute(
        """SELECT list_membership, subsystems, action, amount, code, name, trade_date
           FROM trade_records WHERE price>0 AND amount>0""")]
    # 2026-08-29: position_history=冻结历史表(写入链路死亡, kb/rules/data-field-semantics.md §五)
    # 三账核对中该侧行数小属预期(增量平仓在 trade_records), 勿报缺失事故
    pos_hist = [dict(r) for r in attr.execute(
        "SELECT code, name, entry_date, exit_date, pnl_pct, pnl_amount, holding_days FROM position_history")]
    attr.close()

    # ---------- 4) 指标计算 ----------
    n_buys, n_sells = len(buys), len(sells)
    # 已判定子集（优先 D5 口径，与 feedback_engine 一致；D5 未填则用 realized_pnl 兜底并标注）
    buy_judged = [b for b in buys if b.get("d5_ret") is not None or b.get("realized_pnl_pct") is not None]
    sell_judged = [s for s in sells if s.get("d5_ret") is not None]

    b_right = sum(1 for b in buy_judged
                  if buy_verdict(b.get("d5_ret")) == "BUY_RIGHT"
                  or (b.get("d5_ret") is None and (b.get("realized_pnl_pct") or 0) > 0))
    b_judged_n = len(buy_judged)
    buy_winrate = round(b_right / b_judged_n * 100, 1) if b_judged_n else None

    s_right = sum(1 for s in sell_judged if sell_verdict(s.get("d5_ret")) == "SELL_RIGHT")
    s_judged_n = len(sell_judged)
    sell_winrate = round(s_right / s_judged_n * 100, 1) if s_judged_n else None

    closed = [b for b in buys if b.get("status") == "CLOSED" and b.get("realized_pnl_pct") is not None]
    hold_days = [b["hold_days"] for b in closed if b.get("hold_days")]
    avg_hold = round(sum(hold_days) / len(hold_days), 1) if hold_days else None
    realized = [b["realized_pnl_pct"] for b in closed]
    avg_realized = round(sum(realized) / len(realized) * 100, 2) if realized else None

    # 遗憾率（卖出后踏空）
    regret_n = sum(1 for s in sells if (s.get("max_post_drawup") or 0) > REGRET_THRESHOLD)
    regret_denom = sum(1 for s in sells if s.get("max_post_drawup") is not None)
    regret_rate = round(regret_n / regret_denom * 100, 1) if regret_denom else None

    # top winner / loser（已判定）
    cand = []
    for b in buy_judged:
        v = b.get("d5_ret") if b.get("d5_ret") is not None else b.get("realized_pnl_pct")
        cand.append((b.get("name") or b["code"], v))
    for s in sell_judged:
        cand.append((s.get("name") or s["code"], s.get("d5_ret")))
    top_winner = max(cand, key=lambda x: x[1])[0] if cand else None
    top_loser = min(cand, key=lambda x: x[1])[0] if cand else None

    pending_buy = sum(1 for b in buys if b.get("verdict") == "PENDING")
    pending_sell = sum(1 for s in sells if s.get("verdict") == "PENDING")

    # ---------- 5) 三账核对 ----------
    ledger_buys = sum(1 for r in ledger_all if r["action"] == "BUY")
    ledger_sells = sum(1 for r in ledger_all if r["action"] == "SELL")
    tracker_buys = sum(1 for b in buys)
    pos_hist_n = len(pos_hist)
    pos_hist_closed = sum(1 for p in pos_hist if p.get("exit_date"))
    notes = (
        f"三账核对: trade_records(干净) BUY={ledger_buys}/SELL={ledger_sells} | "
        f"buy_tracker 区间={tracker_buys} | position_history 全量={pos_hist_n}(平仓{pos_hist_closed}); "
        f"脏数据过滤: trade_records price<=0 17条已剔除; "
        f"verdict 依赖 D5, meta 截止日窗口内未填行计入 PENDING"
    )

    # ---------- 6) dual_ledger 分组 ----------
    grp = {}
    for r in ledger:
        key = (r["list_membership"] or "unknown", r["subsystems"] or "unknown")
        g = grp.setdefault(key, {"n": 0, "amt": 0.0})
        g["n"] += 1
        g["amt"] += r["amount"] or 0
    ledger_lines = []
    for (lm, ss), g in sorted(grp.items(), key=lambda x: -x[1]["n"]):
        ledger_lines.append(f"  {lm or '<NULL>':14s} | {ss or '<NULL>':22s} | {g['n']:3d}笔 | {g['amt']:>12,.0f}元")

    report = {
        "review_date": end, "period": f"{since}-{end}",
        "n_buys": n_buys, "n_sells": n_sells,
        "buy_winrate": buy_winrate, "sell_winrate": sell_winrate,
        "avg_hold_days": avg_hold, "avg_realized_pnl_pct": avg_realized,
        "top_winner": top_winner, "top_loser": top_loser,
        "sell_regret_rate": regret_rate, "pending_buy": pending_buy,
        "pending_sell": pending_sell, "notes": notes,
        "ledger": ledger_lines,
    }

    # ---------- 7) 输出 ----------
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        md = [
            "# trade_review 周报 %s" % end, "",
            f"> 区间 {since} ~ {end} | 生成 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
            "## 核心指标", "",
            "| 指标 | 值 |", "|---|---|",
            f"| 买入笔数 | {n_buys} |",
            f"| 卖出笔数 | {n_sells} |",
            f"| 买入正确率 | {buy_winrate if buy_winrate is not None else '样本不足'}% |",
            f"| 卖出正确率 | {sell_winrate if sell_winrate is not None else '样本不足'}% |",
            f"| 平均持有天数 | {avg_hold if avg_hold is not None else '—'} |",
            f"| 平均已实现盈亏 | {avg_realized if avg_realized is not None else '—'}% |",
            f"| 最佳标的 | {top_winner or '—'} |",
            f"| 最差标的 | {top_loser or '—'} |",
            f"| 遗憾率(卖后涨>5%) | {regret_rate if regret_rate is not None else '—'}% |",
            f"| 待判定(买/卖) | {pending_buy} / {pending_sell} |", "",
            "## 三账核对", "",
            f"> {notes}", "",
            "## dual_ledger 双账簿分组（干净数据，过滤 price<=0）", "",
            "| 名单归属 | 信号源 | 笔数 | 金额(元) |", "|---|---|---|---|",
        ]
        for (lm, ss), g in sorted(grp.items(), key=lambda x: -x[1]["n"]):
            md.append("| %s | %s | %d | %s |" % (
                lm or "<NULL>", ss or "<NULL>", g["n"], format(g["amt"], ",.0f")))
        md.append("")
        if n_buys == 0 and n_sells == 0:
            md.append("> ⚠️ 区间内无交易记录（可能非交易日/区间设置问题）")
        body = "\n".join(md)
        os.makedirs(REPORT_DIR, exist_ok=True)
        out = os.path.join(REPORT_DIR, f"trade_review_{end}.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(body)
        print(body)
        print(f"\n报告 -> {out}")

    # ---------- 8) 写库（--write，幂等） ----------
    if args.write:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        # 写库前备份 trade_review 现有内容
        w = sqlite3.connect(DRAGON_DB)
        try:
            existing = w.execute("SELECT COUNT(*) FROM trade_review").fetchone()[0]
            if existing:
                rows = w.execute("SELECT * FROM trade_review").fetchall()
                bak = os.path.join(BACKUP_DIR, f"trade_review_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
                with open(bak, "w", encoding="utf-8") as f:
                    f.write("id,review_date,period,n_buys,n_sells,buy_winrate,sell_winrate,avg_hold_days,avg_realized_pnl_pct,top_winner,top_loser,sell_regret_rate,pending_buy,pending_sell,notes,created_at\n")
                    for r in rows:
                        f.write(",".join(str(x) if x is not None else "" for x in r) + "\n")
                print(f"写库前备份 -> {bak}")
            w.execute("""INSERT OR REPLACE INTO trade_review
                (review_date, period, n_buys, n_sells, buy_winrate, sell_winrate,
                 avg_hold_days, avg_realized_pnl_pct, top_winner, top_loser,
                 sell_regret_rate, pending_buy, pending_sell, notes, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (end, f"{since}-{end}", n_buys, n_sells, buy_winrate, sell_winrate,
                 avg_hold, avg_realized, top_winner, top_loser, regret_rate,
                 pending_buy, pending_sell, notes,
                 datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            w.commit()
            n = w.execute("SELECT COUNT(*) FROM trade_review").fetchone()[0]
            print(f"[写库] trade_review 现有 {n} 行（review_date={end} 已 upsert）")
        finally:
            w.close()

    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
