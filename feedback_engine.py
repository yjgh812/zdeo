# -*- coding: utf-8 -*-
"""
feedback_engine.py v2 — 屠龙系统反馈闭环引擎

v2 修复:
  - 日期匹配: tracker 用 "2026-08-10", meta 用 20260810 -> 统一归一化
  - 极值计算: 从信号日起算, 不含信号日之前历史
  - max_post_drawup 初始 None(非 0.0)

每日环路自动运行，职责：
  1. 填充 buy_tracker PENDING 行（D1/D3/D5/D10/D20 前向收益 + verdict）
  2. 填充 sell_tracker PENDING 行（D1-D20 + max_post_drawup + verdict）
  3. 更新 HOLD 仓位的 max_high/max_drawup/max_low/max_drawdown
  4. 检测已平仓 → 标记 CLOSED + 填充退出数据
  5. 输出每日简报

数据源: stock_daily_meta (dragon_quant_meta.db)
主库:   dragon_quant.db
用法:   python feedback_engine.py [--brief-only]
"""
import os, sys, time, sqlite3
from pathlib import Path

DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
META_DB   = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
HORIZONS  = [1, 3, 5, 10, 20]

DDL = """
CREATE TABLE IF NOT EXISTS buy_tracker (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  name TEXT,
  buy_date TEXT NOT NULL,
  buy_price REAL,
  buy_qty INTEGER,
  signal_source TEXT DEFAULT 'manual',
  signal_score REAL,
  signal_rank INTEGER,
  stop_loss_pct REAL DEFAULT -0.05,
  d1_close REAL, d1_ret REAL,
  d3_close REAL, d3_ret REAL,
  d5_close REAL, d5_ret REAL,
  d10_close REAL, d10_ret REAL,
  d20_close REAL, d20_ret REAL,
  max_high REAL,
  max_drawup REAL,
  max_low REAL,
  max_drawdown REAL,
  status TEXT DEFAULT 'HOLD',
  sell_date TEXT,
  sell_price REAL,
  realized_pnl_pct REAL,
  exit_reason TEXT,
  hold_days INTEGER,
  verdict TEXT DEFAULT 'PENDING',
  updated_at TEXT,
  UNIQUE(code, buy_date)
);

CREATE TABLE IF NOT EXISTS sell_tracker (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL,
  name TEXT,
  sell_date TEXT NOT NULL,
  sell_price REAL,
  sell_reason TEXT,
  cost_price REAL,
  realized_pnl REAL,
  realized_pnl_pct REAL,
  d1_close REAL, d1_ret REAL,
  d3_close REAL, d3_ret REAL,
  d5_close REAL, d5_ret REAL,
  d10_close REAL, d10_ret REAL,
  d20_close REAL, d20_ret REAL,
  verdict TEXT DEFAULT 'PENDING',
  max_post_price REAL,
  max_post_drawup REAL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS trade_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  review_date TEXT NOT NULL,
  period TEXT,
  n_buys INTEGER,
  n_sells INTEGER,
  buy_winrate REAL,
  sell_winrate REAL,
  avg_hold_days REAL,
  avg_realized_pnl_pct REAL,
  top_winner TEXT,
  top_loser TEXT,
  sell_regret_rate REAL,
  pending_buy INTEGER,
  pending_sell INTEGER,
  notes TEXT,
  created_at TEXT,
  UNIQUE(review_date)
);
"""


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def p6(c):
    """归一为纯 6 位代码"""
    c = str(c)
    if "." in c:
        c = c.split(".")[0]
    if c[:2].lower() in ("sh", "sz", "bj"):
        c = c[2:]
    return c.zfill(6)


def norm_date(d):
    """日期归一化为 'YYYYMMDD' 字符串 (meta 格式)
    接受: '2026-08-10', '20260810', 20260810(int), '2026/08/10'
    """
    d = str(d).strip()
    if "-" in d:
        d = d.replace("-", "")
    if "/" in d:
        d = d.replace("/", "")
    return d


def load_panels(codes):
    """从 stock_daily_meta 加载指定代码的全历史 {date_int_str: close} + 交易日历列表"""
    m = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    p6codes = {p6(c) for c in codes}
    panels = {}
    if p6codes:
        ph = ",".join("?" * len(p6codes))
        for code, d, c in m.execute(
            f"SELECT code, date, close FROM stock_daily_meta WHERE code IN ({ph}) ORDER BY date",
            tuple(p6codes)
        ):
            panels.setdefault(code, {})[str(d)] = c
    cal = [str(r[0]) for r in m.execute(
        "SELECT DISTINCT date FROM stock_daily_meta ORDER BY date")]
    m.close()
    return panels, cal


def forward_ret(series, cal, d0_str, h):
    """前向 h 日收益。d0_str 已 norm_date 归一化。
    返回 (close_h, ret_h) 或 (None, None)"""
    d0 = norm_date(d0_str)
    if d0 not in cal:
        return None, None
    i = cal.index(d0)
    if i + h >= len(cal):
        return None, None
    d_h = cal[i + h]
    c0 = series.get(d0)
    c_h = series.get(d_h)
    if not c0 or not c_h or c0 <= 0:
        return None, None
    return c_h, round(c_h / c0 - 1, 6)


def range_extremes(series, cal, d0_str, n_days):
    """从 d0+1 起 n_days 天内的最高/最低收盘价 (不含 d0 本身)"""
    d0 = norm_date(d0_str)
    if d0 not in cal:
        return None, None
    i = cal.index(d0)
    end = min(i + n_days + 1, len(cal))
    dates = cal[i + 1: end]  # 不含 d0
    highs, lows = [], []
    for d in dates:
        v = series.get(d)
        if v and v > 0:
            highs.append(v)
            lows.append(v)
    return (max(highs) if highs else None), (min(lows) if lows else None)


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    db = sqlite3.connect(DRAGON_DB)
    db.executescript(DDL)
    db.commit()
    db.close()


def update_sell_tracker():
    """填充 sell_tracker PENDING 行"""
    db = sqlite3.connect(DRAGON_DB)
    db.row_factory = sqlite3.Row
    pending = db.execute("SELECT * FROM sell_tracker WHERE verdict='PENDING'").fetchall()
    if not pending:
        log("sell_tracker: 无 PENDING 行")
        db.close()
        return 0

    codes = [r["code"] for r in pending]
    panels, cal = load_panels(codes)
    n_updated = 0

    for row in pending:
        bare = p6(row["code"])
        series = panels.get(bare, {})
        if not series:
            continue

        updates = {}
        for h in HORIZONS:
            close_h, ret_h = forward_ret(series, cal, row["sell_date"], h)
            if close_h is not None:
                updates[f"d{h}_close"] = round(close_h, 4)
                updates[f"d{h}_ret"] = ret_h

        # 卖出后 20 日内最高价 (踏空检测)
        mx, _ = range_extremes(series, cal, row["sell_date"], 20)
        if mx:
            updates["max_post_price"] = round(mx, 4)
            sp = row["sell_price"]
            if sp and sp > 0:
                updates["max_post_drawup"] = round(mx / sp - 1, 4)

        # verdict: D5 为准
        d5_ret = updates.get("d5_ret")
        if d5_ret is not None:
            if d5_ret < 0:
                updates["verdict"] = "SELL_RIGHT"
            elif d5_ret > 0.05:
                updates["verdict"] = "SELL_WRONG"
            else:
                updates["verdict"] = "SELL_NEUTRAL"

        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [now(), row["id"]]
            db.execute(f"UPDATE sell_tracker SET {sets}, updated_at=? WHERE id=?", vals)
            n_updated += 1

    db.commit()
    db.close()
    log(f"sell_tracker: 更新 {n_updated}/{len(pending)} 行")
    return n_updated


def update_buy_tracker():
    """填充 buy_tracker: PENDING 行的前向收益 + HOLD 行的极值"""
    db = sqlite3.connect(DRAGON_DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT * FROM buy_tracker WHERE verdict='PENDING' OR status='HOLD'"
    ).fetchall()
    if not rows:
        log("buy_tracker: 无 PENDING/HOLD 行")
        db.close()
        return 0

    codes = [r["code"] for r in rows]
    panels, cal = load_panels(codes)
    n_updated = 0

    for row in rows:
        bare = p6(row["code"])
        series = panels.get(bare, {})
        if not series:
            continue

        updates = {}

        # 前向收益 (仅 verdict=PENDING 行)
        if row["verdict"] == "PENDING":
            for h in HORIZONS:
                close_h, ret_h = forward_ret(series, cal, row["buy_date"], h)
                if close_h is not None:
                    updates[f"d{h}_close"] = round(close_h, 4)
                    updates[f"d{h}_ret"] = ret_h

        # 持有期极值 (HOLD 行持续更新, 仅算买入后的)
        if row["status"] == "HOLD":
            # 用截至最新交易日的全部区间
            mx, mn = range_extremes(series, cal, row["buy_date"], 252)
            bp = row["buy_price"]
            if mx and bp and bp > 0:
                updates["max_high"] = round(mx, 4)
                updates["max_drawup"] = round(mx / bp - 1, 4)
            if mn and bp and bp > 0:
                updates["max_low"] = round(mn, 4)
                updates["max_drawdown"] = round(mn / bp - 1, 4)

        # verdict 判定 (D5)
        if row["verdict"] == "PENDING":
            d5_ret = updates.get("d5_ret")
            if d5_ret is not None:
                if d5_ret > 0:
                    updates["verdict"] = "BUY_RIGHT"
                elif d5_ret < -0.02:
                    updates["verdict"] = "BUY_WRONG"
                else:
                    updates["verdict"] = "BUY_NEUTRAL"

        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [now(), row["id"]]
            db.execute(f"UPDATE buy_tracker SET {sets}, updated_at=? WHERE id=?", vals)
            n_updated += 1

    db.commit()
    db.close()
    log(f"buy_tracker: 更新 {n_updated}/{len(rows)} 行")
    return n_updated


def close_exited_positions():
    """检测 positions 中消失的持仓 → buy_tracker 标记 CLOSED"""
    db = sqlite3.connect(DRAGON_DB)
    db.row_factory = sqlite3.Row
    held = {p6(r["code"]) for r in db.execute("SELECT code FROM positions").fetchall()}
    hold_rows = db.execute("SELECT * FROM buy_tracker WHERE status='HOLD'").fetchall()

    n_closed = 0
    for row in hold_rows:
        if p6(row["code"]) not in held:
            sell_row = db.execute(
                "SELECT * FROM sell_tracker WHERE code LIKE ? ORDER BY sell_date DESC LIMIT 1",
                (f"%{p6(row['code'])}%",)
            ).fetchone()
            if sell_row:
                sp = sell_row["sell_price"]
                bp = row["buy_price"]
                pnl = round(sp / bp - 1, 4) if bp and bp > 0 else None
                hold_days = None
                try:
                    from datetime import datetime
                    bd = datetime.strptime(norm_date(row["buy_date"]), "%Y%m%d")
                    sd = datetime.strptime(norm_date(sell_row["sell_date"]), "%Y%m%d")
                    hold_days = (sd - bd).days
                except Exception:
                    pass
                db.execute("""UPDATE buy_tracker SET status='CLOSED', sell_date=?, sell_price=?,
                    realized_pnl_pct=?, exit_reason=?, hold_days=?, updated_at=? WHERE id=?""",
                    (sell_row["sell_date"], sp, pnl, sell_row["sell_reason"], hold_days, now(), row["id"]))
            else:
                db.execute("""UPDATE buy_tracker SET status='CLOSED',
                    exit_reason='unknown_exit', updated_at=? WHERE id=?""", (now(), row["id"]))
            n_closed += 1
    db.commit()
    db.close()
    if n_closed:
        log(f"close_exited: 标记 {n_closed} 笔为 CLOSED")
    return n_closed


def daily_brief():
    """每日简报"""
    db = sqlite3.connect(DRAGON_DB)
    db.row_factory = sqlite3.Row

    print("\n" + "=" * 60)
    print("反馈闭环每日简报")
    print("=" * 60)

    # buy_tracker
    print("\n买入跟踪:")
    for r in db.execute("SELECT status, verdict, COUNT(*) n FROM buy_tracker GROUP BY status, verdict").fetchall():
        print(f"  {r['status']:<8} {r['verdict']:<14} {r['n']:>4}")

    # sell_tracker
    print("\n卖出跟踪:")
    for r in db.execute("SELECT verdict, COUNT(*) n FROM sell_tracker GROUP BY verdict").fetchall():
        print(f"  {r['verdict']:<14} {r['n']:>4}")

    # 已判定统计
    bj = db.execute("""SELECT
        SUM(CASE WHEN verdict='BUY_RIGHT' THEN 1 ELSE 0 END) wins,
        SUM(CASE WHEN verdict='BUY_WRONG' THEN 1 ELSE 0 END) losses,
        SUM(CASE WHEN verdict NOT IN ('PENDING') THEN 1 ELSE 0 END) total,
        AVG(CASE WHEN d5_ret IS NOT NULL THEN d5_ret END) avg_d5
        FROM buy_tracker WHERE verdict != 'PENDING'""").fetchone()
    if bj and bj["total"]:
        wr = bj["wins"] / bj["total"] * 100
        d5s = f" (D5均值{bj['avg_d5']:+.2%})" if bj["avg_d5"] else ""
        print(f"\n买入胜率: {bj['wins']}/{bj['total']} = {wr:.0f}%{d5s}")

    sj = db.execute("""SELECT
        SUM(CASE WHEN verdict='SELL_RIGHT' THEN 1 ELSE 0 END) sright,
        SUM(CASE WHEN verdict='SELL_WRONG' THEN 1 ELSE 0 END) swrong,
        SUM(CASE WHEN verdict NOT IN ('PENDING') THEN 1 ELSE 0 END) total
        FROM sell_tracker WHERE verdict != 'PENDING'""").fetchone()
    if sj and sj["total"]:
        sr = sj["sright"] / sj["total"] * 100
        rr = sj["swrong"] / sj["total"] * 100
        print(f"卖出正确率: {sj['sright']}/{sj['total']} = {sr:.0f}% (踏空率 {rr:.0f}%)")

    # 踏空榜 top3
    regrets = db.execute("""SELECT code,name,sell_price,max_post_drawup
        FROM sell_tracker WHERE max_post_drawup IS NOT NULL AND max_post_drawup > 0
        ORDER BY max_post_drawup DESC LIMIT 3""").fetchall()
    if regrets:
        print("\n卖出后涨幅最大 (踏空榜):")
        for r in regrets:
            print(f"  {r['code']} {r['name']:<8} 卖@{r['sell_price']:.2f} 后涨 {r['max_post_drawup']:+.1%}")

    # HOLD 中浮亏最大的
    losers = db.execute("""SELECT code,name,buy_price,max_drawdown
        FROM buy_tracker WHERE status='HOLD' AND max_drawdown IS NOT NULL
        ORDER BY max_drawdown ASC LIMIT 3""").fetchall()
    if losers:
        print("\n持仓浮亏最大:")
        for r in losers:
            print(f"  {r['code']} {r['name']:<8} @{r['buy_price']:.2f} 浮亏 {r['max_drawdown']:+.1%}")

    db.close()


def insert_buy(code, name, buy_date, buy_price, buy_qty=0,
               signal_source="manual", signal_score=None, signal_rank=None,
               stop_loss_pct=-0.05):
    """供外部调用: 买入时插入 buy_tracker"""
    db = sqlite3.connect(DRAGON_DB)
    db.execute("""INSERT OR REPLACE INTO buy_tracker
        (code, name, buy_date, buy_price, buy_qty, signal_source,
         signal_score, signal_rank, stop_loss_pct, status, verdict, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?, 'HOLD', 'PENDING', ?)""",
        (code, name, str(buy_date), buy_price, buy_qty, signal_source,
         signal_score, signal_rank, stop_loss_pct, now()))
    db.commit()
    db.close()


def insert_sell(code, name, sell_date, sell_price, sell_reason="",
                cost_price=None, realized_pnl=None, realized_pnl_pct=None):
    """供外部调用: 卖出时插入 sell_tracker"""
    db = sqlite3.connect(DRAGON_DB)
    db.execute("""INSERT OR REPLACE INTO sell_tracker
        (code, name, sell_date, sell_price, sell_reason, cost_price,
         realized_pnl, realized_pnl_pct, verdict, updated_at)
        VALUES (?,?,?,?,?,?,?,?, 'PENDING', ?)""",
        (code, name, str(sell_date), sell_price, sell_reason, cost_price,
         realized_pnl, realized_pnl_pct, now()))
    db.commit()
    db.close()


def main():
    log("=== 反馈闭环引擎 v2 启动 ===")
    init_db()
    close_exited_positions()
    update_sell_tracker()
    update_buy_tracker()
    if "--brief-only" not in sys.argv:
        daily_brief()
    log("=== 反馈闭环引擎完成 ===\n")


if __name__ == "__main__":
    main()
