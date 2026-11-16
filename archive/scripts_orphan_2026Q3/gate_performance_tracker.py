#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_performance_tracker.py — 闸门判决胜率追踪器 (t35/P2-2, m3策略监督官)

目标: 给闸门系统真实反馈——通过的股票后来涨没涨、被拒的股票后来跌没跌。

三个功能:
  --record  记录某日(默认今日) .gate_logs 下所有闸门判决到 gate_track.db
  --track   收盘后更新每只已记录股票的后 1/3/5 交易日收益 (qfq收盘)
  --report  月末胜率报告: 通过票胜率 vs 拒绝票胜率 vs 整体胜率

数据源:
  判决留痕: D:/zcodesj/.gate_logs/{YYYYMMDD}_*_gate_result.json (strategy_gate.py write_gate_log)
  行情    : qfq库 stock_daily_meta_qfq (code 6位, date YYYYMMDD, close 前复权)

口径约定(一致性优先, 保证 PASS/REJECT 两组可比):
  entry_price = 判决日当日 qfq 收盘价
  ret_Nd      = 判决日后第 N 个交易日 qfq 收盘 / entry_price - 1
  通过组      = verdict in (PASS, PASS_WITH_WARN); 拒绝组 = REJECT
  测试留痕(名称含"测试"/"test")不纳入追踪
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

# ========== 路径配置 ==========
GATE_LOG_DIR = r"D:\zcodesj\.gate_logs"
TRACK_DB = r"D:\zcodesj\gate_track.db"
# qfq库唯一真源 (daily_chain_update.py:45 口径; 2026-08-29 清理: databases/ 下曾是0字节占位残骸, 已删)
QFQ_DB_CANDIDATES = [
    r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db",
]
REPORT_DIR = r"D:\zcodesj\.gate_logs"

FORWARD_DAYS = (1, 3, 5)  # 后N日收益窗口


def _resolve_qfq_db() -> str:
    """返回真正含 stock_daily_meta_qfq 数据的 qfq 库路径。"""
    for path in QFQ_DB_CANDIDATES:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            continue
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            n = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='stock_daily_meta_qfq'"
            ).fetchone()[0]
            conn.close()
            if n:
                return path
        except sqlite3.Error:
            continue
    raise FileNotFoundError(f"未找到含 stock_daily_meta_qfq 的 qfq 库: {QFQ_DB_CANDIDATES}")


# ========== 追踪表 ==========
DDL = """
CREATE TABLE IF NOT EXISTS gate_track (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,            -- 判决日 YYYYMMDD
    code        TEXT NOT NULL,
    name        TEXT,
    verdict     TEXT NOT NULL,            -- PASS / PASS_WITH_WARN / REJECT
    score       REAL,
    hard_fails  TEXT,                     -- 硬底线否决项, 逗号分隔
    entry_price REAL,                     -- 判决日 qfq 收盘
    batch_total INTEGER,                  -- 该轮候选总数(识别全市场扫描/单票)
    source_file TEXT,                     -- 判决留痕文件
    created_at  TEXT,
    ret_1d      REAL,
    ret_3d      REAL,
    ret_5d      REAL,
    exit_date   TEXT,
    exit_price  REAL,
    track_status TEXT DEFAULT 'open',     -- open / closed / no_price
    UNIQUE(date, code)
);
CREATE INDEX IF NOT EXISTS idx_gate_track_date ON gate_track(date);
CREATE INDEX IF NOT EXISTS idx_gate_track_status ON gate_track(track_status);
"""


def _open_track_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    return conn


# ========== 功能1: 记录当日判决 ==========
# 回归/冒烟测试的合成名: 含"测试"/test, 或 负价股/正常股X/低开X/高开X 等夹具名
_TEST_NAME_RE = re.compile(r"测试|test|负价股|^(正常股|低开|高开|跌停股|涨停股)[A-Z]?$", re.I)


def _is_test_row(name: str) -> bool:
    return bool(_TEST_NAME_RE.search(name or ""))


def _parse_gate_file(path: str):
    """解析一个 gate_result 文件, 返回 [(code,name,verdict,score,hard_fails,batch_total), ...]。
    兼容批量格式(results)与单股/回放格式(single/replay.result)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []

    rows = []
    if isinstance(d, dict) and isinstance(d.get("results"), list):
        total = d.get("total") or len(d["results"])
        for r in d["results"]:
            if not isinstance(r, dict) or not r.get("code") or not r.get("verdict"):
                continue
            rows.append((
                str(r["code"]).zfill(6),
                r.get("name") or "",
                r["verdict"],
                r.get("score"),
                ",".join(r.get("hard_fails") or []),
                total,
            ))
    elif isinstance(d, dict) and isinstance(d.get("single"), dict):
        r = d["single"]
        if r.get("code") and r.get("verdict"):
            rows.append((
                str(r["code"]).zfill(6),
                r.get("name") or "",
                r["verdict"],
                r.get("score"),
                ",".join(r.get("hard_fails") or r.get("rejected_gates") or []),
                1,
            ))
    # replay 文件为历史错误模式回放, 不属于真实判决, 不记录
    return rows


def cmd_record(date: str, track_db: str, qfq_db: str) -> int:
    files = sorted(glob.glob(os.path.join(GATE_LOG_DIR, f"{date}_*_gate_result.json")))
    if not files:
        print(f"[record] {date} 无闸门留痕文件 ({GATE_LOG_DIR}/{date}_*_gate_result.json)")
        return 0

    # 同日同票多轮判决 -> 以当日最后一轮为准 (文件按时间戳排序, 后者覆盖前者)
    latest = {}
    n_test = 0
    for fp in files:
        for code, name, verdict, score, hard_fails, total in _parse_gate_file(fp):
            if _is_test_row(name):
                n_test += 1
                continue
            latest[code] = (name, verdict, score, hard_fails, total, os.path.basename(fp))

    if not latest:
        print(f"[record] {date} 留痕文件 {len(files)} 个, 过滤测试后无有效判决")
        return 0

    # entry_price = 判决日 qfq 收盘
    qconn = sqlite3.connect(f"file:{qfq_db}?mode=ro", uri=True)
    codes = list(latest.keys())
    marks = ",".join("?" * len(codes))
    prices = dict(qconn.execute(
        f"SELECT code, close FROM stock_daily_meta_qfq WHERE date=? AND code IN ({marks})",
        [date] + codes).fetchall())
    qconn.close()

    conn = _open_track_db(track_db)
    now = datetime.now().isoformat(timespec="seconds")
    n_new, n_upd, n_noprice = 0, 0, 0
    for code, (name, verdict, score, hard_fails, total, src) in latest.items():
        entry = prices.get(code)
        if entry is None:
            n_noprice += 1
        exists = conn.execute(
            "SELECT 1 FROM gate_track WHERE date=? AND code=?", (date, code)
        ).fetchone()
        conn.execute(
            """INSERT INTO gate_track
               (date, code, name, verdict, score, hard_fails, entry_price,
                batch_total, source_file, created_at, track_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(date, code) DO UPDATE SET
                 name=excluded.name, verdict=excluded.verdict, score=excluded.score,
                 hard_fails=excluded.hard_fails, entry_price=excluded.entry_price,
                 batch_total=excluded.batch_total, source_file=excluded.source_file,
                 created_at=excluded.created_at""",
            (date, code, name, verdict, score, hard_fails, entry, total, src, now,
             "open" if entry is not None else "no_price"))
        if exists:
            n_upd += 1
        else:
            n_new += 1
    conn.commit()

    vcounts = {}
    for v in latest.values():
        vcounts[v[1]] = vcounts.get(v[1], 0) + 1
    print(f"[record] {date}: 留痕文件 {len(files)} 个, 有效判决 {len(latest)} 只 "
          f"(新增 {n_new}, 更新 {n_upd}, 缺行情 {n_noprice}, 过滤测试 {n_test}) 判决分布 {vcounts}")
    conn.close()
    return len(latest)


# ========== 功能2: 更新后N日收益 ==========
def cmd_track(track_db: str, qfq_db: str) -> int:
    conn = _open_track_db(track_db)
    qconn = sqlite3.connect(f"file:{qfq_db}?mode=ro", uri=True)

    open_rows = conn.execute(
        "SELECT id, date, code, entry_price FROM gate_track WHERE track_status='open'"
    ).fetchall()
    if not open_rows:
        print("[track] 无待追踪记录")
        conn.close(); qconn.close()
        return 0

    # 交易日历: qfq库 distinct date
    cal = [r[0] for r in qconn.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]

    n_closed, n_partial = 0, 0
    for rid, date, code, entry in open_rows:
        if not entry:
            conn.execute("UPDATE gate_track SET track_status='no_price' WHERE id=?", (rid,))
            continue
        later = [d for d in cal if d > date]
        if not later:
            continue
        updates = {}
        for nd in FORWARD_DAYS:
            if len(later) >= nd:
                px = qconn.execute(
                    "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                    (code, later[nd - 1])).fetchone()
                if px and px[0]:
                    updates[f"ret_{nd}d"] = round(px[0] / entry - 1, 6)
        if "ret_5d" in updates:
            exit_date = later[FORWARD_DAYS[-1] - 1]
            exit_px = qconn.execute(
                "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                (code, exit_date)).fetchone()
            conn.execute(
                """UPDATE gate_track SET ret_1d=?, ret_3d=?, ret_5d=?,
                   exit_date=?, exit_price=?, track_status='closed' WHERE id=?""",
                (updates.get("ret_1d"), updates.get("ret_3d"), updates["ret_5d"],
                 exit_date, exit_px[0] if exit_px else None, rid))
            n_closed += 1
        elif updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE gate_track SET {sets} WHERE id=?",
                         list(updates.values()) + [rid])
            n_partial += 1
    conn.commit()
    latest_cal = cal[-1] if cal else "?"
    print(f"[track] 待追踪 {len(open_rows)} 只: 完成5日 {n_closed}, 部分更新 {n_partial} "
          f"(qfq最新交易日 {latest_cal})")
    conn.close(); qconn.close()
    return n_closed


# ========== 功能3: 月末胜率报告 ==========
def _winrate(rows):
    """rows: [(ret_5d, score)] -> dict"""
    n = len(rows)
    if n == 0:
        return {"n": 0, "wins": 0, "winrate": None, "avg_ret": None}
    wins = sum(1 for r in rows if r[0] is not None and r[0] > 0)
    rets = [r[0] for r in rows if r[0] is not None]
    return {
        "n": n,
        "wins": wins,
        "winrate": round(wins / n * 100, 1) if n else None,
        "avg_ret": round(sum(rets) / len(rets) * 100, 2) if rets else None,
    }


def cmd_report(month: str, track_db: str, save: bool = True) -> str:
    conn = _open_track_db(track_db)
    like = f"{month}%"
    total = conn.execute(
        "SELECT COUNT(*) FROM gate_track WHERE date LIKE ?", (like,)).fetchone()[0]
    open_n = conn.execute(
        "SELECT COUNT(*) FROM gate_track WHERE date LIKE ? AND track_status='open'",
        (like,)).fetchone()[0]

    def fetch(verdicts):
        marks = ",".join("?" * len(verdicts))
        return conn.execute(
            f"""SELECT ret_5d, score FROM gate_track
                WHERE date LIKE ? AND track_status='closed' AND verdict IN ({marks})""",
            [like] + verdicts).fetchall()

    pass_rows = fetch(["PASS", "PASS_WITH_WARN"])
    rej_rows = fetch(["REJECT"])
    all_rows = pass_rows + rej_rows

    sp, sr, sa = _winrate(pass_rows), _winrate(rej_rows), _winrate(all_rows)

    # 逐票明细(已完成5日追踪的)
    detail = conn.execute(
        """SELECT date, code, name, verdict, score, entry_price, exit_price, ret_5d
           FROM gate_track WHERE date LIKE ? AND track_status='closed'
           ORDER BY date, verdict, ret_5d DESC""", (like,)).fetchall()

    lines = []
    a = lines.append
    a(f"# 闸门判决胜率月报 — {month}")
    a("")
    a(f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}")
    a(f"- 当月记录判决: {total} 只; 已完成5日追踪: {sa['n']}; 追踪中: {open_n}")
    a(f"- 口径: entry=判决日qfq收盘, ret_5d=后第5个交易日qfq收盘收益, 胜=ret_5d>0")
    a("")
    a("| 组别 | 样本数 | 胜(5日>0) | 胜率 | 平均5日收益 |")
    a("|---|---|---|---|---|")
    a(f"| 通过票(PASS+WARN) | {sp['n']} | {sp['wins']} | "
      f"{sp['winrate'] if sp['winrate'] is not None else '-'}% | "
      f"{sp['avg_ret'] if sp['avg_ret'] is not None else '-'}% |")
    a(f"| 拒绝票(REJECT) | {sr['n']} | {sr['wins']} | "
      f"{sr['winrate'] if sr['winrate'] is not None else '-'}% | "
      f"{sr['avg_ret'] if sr['avg_ret'] is not None else '-'}% |")
    a(f"| 整体 | {sa['n']} | {sa['wins']} | "
      f"{sa['winrate'] if sa['winrate'] is not None else '-'}% | "
      f"{sa['avg_ret'] if sa['avg_ret'] is not None else '-'}% |")
    a("")
    if sp["n"] and sr["n"]:
        edge = round(sp["winrate"] - sr["winrate"], 1)
        a(f"**闸门区分度(通过胜率-拒绝胜率): {edge:+.1f}pp** "
          + ("→ 闸门有区分价值" if edge > 0 else "→ 闸门区分度不足, 需复盘评分/硬底线"))
        a("")
    if detail:
        a("## 明细(已完成追踪)")
        a("")
        a("| 日期 | 代码 | 名称 | 判决 | 评分 | 入场 | 5日后 | 5日收益% |")
        a("|---|---|---|---|---|---|---|---|")
        for d, code, name, verdict, score, ep, xp, r5 in detail:
            r5s = f"{r5*100:+.2f}" if r5 is not None else "-"
            a(f"| {d} | {code} | {name} | {verdict} | {score} | {ep} | {xp} | {r5s} |")
    report = "\n".join(lines)
    print(report)

    if save:
        out = os.path.join(REPORT_DIR, f"gate_winrate_{month}.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[report] 已保存 {out}")
    conn.close()
    return report


# ========== CLI ==========
def main() -> int:
    p = argparse.ArgumentParser(description="闸门判决胜率追踪器 (t35/P2-2)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--record", action="store_true", help="记录指定日(默认今日)闸门判决")
    g.add_argument("--track", action="store_true", help="更新后1/3/5交易日收益")
    g.add_argument("--report", action="store_true", help="输出月度胜率报告")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                   help="判决日 YYYYMMDD (record用, 默认今日)")
    p.add_argument("--month", default=datetime.now().strftime("%Y%m"),
                   help="报告月份 YYYYMM (report用, 默认当月)")
    p.add_argument("--db", default=TRACK_DB, help="追踪库路径")
    p.add_argument("--no-save", action="store_true", help="report 不落盘")
    args = p.parse_args()

    qfq_db = _resolve_qfq_db()
    if args.record:
        return 0 if cmd_record(args.date, args.db, qfq_db) >= 0 else 1
    if args.track:
        cmd_track(args.db, qfq_db)
        return 0
    if args.report:
        cmd_report(args.month, args.db, save=not args.no_save)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
