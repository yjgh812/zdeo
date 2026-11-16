#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主线龙头观察池 leader_watchlist v1 (2026-09-06)

修复断层：每日复盘认定的主线连板龙头（如 20260819 金健米业 3 板农业链龙头）
此前只存在于单日复盘文本中，次日即被遗忘——"看到过但没有持续跟踪载体"。
本模块提供唯一 owner：
  add    登记龙头（复盘流程末尾调用，幂等）
  update 每日收盘后补齐行情 + 回踩企稳 setup 检查（事件留痕，仅提请研究，非买信号）
  report 输出池状态
  drop   主人裁决移除

铁律边界（宪法 §10）：
- 观察池不是买入名单。setup_confirmed 事件 = "提请研究对象"，仍须走
  bp+ep/ml Top20 名单制或主人逐笔特批，本模块不产生任何买信号。
- 价格源 = qfq 库（dragon_quant_qfq.db 表 stock_daily_meta_qfq，只读）。
  库不可用（0字节/空表）时 fail-closed 结构化报错 exit 2，禁止静默换源。
  可用 --qfq-path 显式指向备份库（如并行重建期）。
- 登记锚价 = 登记日 qfq close（不复用白名单 CSV 的生成时点参考价）。
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE_PATH = ROOT / "data" / "leader_watchlist.json"
DEFAULT_QFQ = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")  # 2026-09-07 统一单源: 生产主库(9/6 SOP 裁定), zcodesj data/ 副本停用

# 回踩企稳 setup 检查参数（保守、机械可测；参数变更须主人裁决并留痕）
SETUP_DRAWDOWN_MIN = 0.08    # 从登记后峰值回撤下限
SETUP_DRAWDOWN_MAX = 0.30    # 回撤上限（超深回撤=趋势破坏，不算企稳）
SETUP_VOLUME_RATIO = 0.60    # 当日量 < 峰值日量的60%（缩量）
SETUP_MAX_PRICE = 30.0       # 宪法 >30 元不买不交易
COOLDOWN_DRAWDOWN = 0.40     # 峰值回撤>40% 且从未 setup → cooldown（自然冷却）

STATUS_WATCHING = "watching"
STATUS_COOLDOWN = "cooldown"
STATUS_DROPPED = "dropped"


def _fail(msg: str, code: int = 2):
    print(f"[leader_watchlist][ERROR] {msg}")
    sys.exit(code)


def _norm_code(code: str) -> str:
    code = code.strip()
    if code.upper().startswith(("SH", "SZ")):
        code = code[2:]
    if not (code.isdigit() and len(code) == 6):
        _fail(f"非法股票代码: {code!r}（须6位数字）")
    return code


def load_store() -> dict:
    if STORE_PATH.exists():
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    return {"version": 1, "created": datetime.now().strftime("%Y%m%d"), "entries": []}


def save_store(store: dict):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def open_qfq(path: Path) -> sqlite3.Connection:
    if not path.exists() or path.stat().st_size == 0:
        _fail(f"qfq 价格源不可用: {path}（不存在或0字节；主库或在建并行重建期，"
              f"可用 --qfq-path 指向 backups 下 qfq 周备最新快照，见 DATA_LOCATIONS 备份族）")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='stock_daily_meta_qfq'"
        ).fetchone()[0]
        if n == 0:
            _fail(f"qfq 价格源缺 stock_daily_meta_qfq 表: {path}")
    except sqlite3.DatabaseError as e:
        _fail(f"qfq 价格源损坏: {path}: {e}")
    return con


def fetch_series(con, code: str, since: int):
    rows = con.execute(
        "SELECT date, close, volume FROM stock_daily_meta_qfq "
        "WHERE code=? AND date>=? ORDER BY date", (code, since)
    ).fetchall()
    # 库内 date 为 TEXT(YYYYMMDD)，取回统一转 int，避免 Python 层 str/int 比较陷阱
    return [(int(r[0]), float(r[1]), float(r[2])) for r in rows]


def latest_trade_date(con) -> int:
    return con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]


def cmd_add(args):
    code = _norm_code(args.code)
    qfq_date = int(args.date.replace("-", "")) if args.date else None
    con = open_qfq(Path(args.qfq_path) if args.qfq_path else DEFAULT_QFQ)
    try:
        store = load_store()
        for e in store["entries"]:
            if e["code"] == code and e["status"] == STATUS_WATCHING:
                print(f"[leader_watchlist] {code} {e['name']} 已在池(watching, 登记日{e['registered_date']})，"
                      f"幂等跳过；如需改登记理由请先 drop 再 add")
                return
        # 锚价：登记日（缺省=库内最新交易日）的 qfq close
        if qfq_date is None:
            qfq_date = latest_trade_date(con)
        row = con.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
            (code, qfq_date)).fetchone()
        if row is None:
            _fail(f"{code} 在 {qfq_date} 无 qfq 行情（停牌/退市/日期非法），登记拒绝")
        entry = {
            "code": code,
            "name": args.name,
            "theme": args.theme or "",
            "ladder_at_register": args.ladder,
            "reason": args.reason,
            "registered_date": qfq_date,
            "register_close": row[1],
            "last_update": qfq_date,
            "last_close": row[1],
            "peak_close": row[1],
            "peak_date": qfq_date,
            "max_gain_from_register": 0.0,
            "drawdown_from_peak": 0.0,
            "status": STATUS_WATCHING,
            "events": [],
            "setup_param_fingerprint": f"dd[{SETUP_DRAWDOWN_MIN},{SETUP_DRAWDOWN_MAX}]"
                                       f"_vol{SETUP_VOLUME_RATIO}_pmax{SETUP_MAX_PRICE}",
        }
        store["entries"].append(entry)
        save_store(store)
        print(f"[leader_watchlist] 已登记 {code} {args.name} | 主题={entry['theme']} "
              f"| 登记日={qfq_date} 锚价={row[1]:.2f} | 连板高度={args.ladder} | 理由={args.reason}")
        print(f"[leader_watchlist] 注意: 观察池≠买入名单，setup_confirmed 仅提请研究（宪法§10名单制不变）")
    finally:
        con.close()


def _run_setup_check(entry, series):
    """回踩企稳检查：峰值回撤∈[8%,30%] + 收>MA5>MA10 + 缩量(当日量<峰值量60%) + <30元。
    返回事件 dict 或 None。一次命中只记一次（同日去重在 update 层保证）。"""
    closes = [c for _, c, _ in series]
    vols = [v for _, _, v in series]
    if len(closes) < 11:
        return None
    peak = max(closes)
    peak_idx = closes.index(peak)
    peak_vol = max(vols[peak_idx:]) if peak_idx < len(vols) else max(vols)
    dd = (peak - closes[-1]) / peak if peak > 0 else 0.0
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    last_date, last_close, last_vol = series[-1]
    cond_dd = SETUP_DRAWDOWN_MIN <= dd <= SETUP_DRAWDOWN_MAX
    cond_ma = last_close > ma5 > ma10
    cond_vol = last_vol < peak_vol * SETUP_VOLUME_RATIO
    cond_price = last_close < SETUP_MAX_PRICE
    if cond_dd and cond_ma and cond_vol and cond_price:
        return {
            "type": "setup_confirmed",
            "date": last_date,
            "detail": (f"回撤{dd*100:.1f}%∈[8,30] | 收{last_close:.2f}>MA5 {ma5:.2f}>MA10 {ma10:.2f} "
                       f"| 量比峰值{last_vol/peak_vol*100:.0f}%<60% | 价格{last_close:.2f}<30"),
        }
    return None


def cmd_update(args):
    con = open_qfq(Path(args.qfq_path) if args.qfq_path else DEFAULT_QFQ)
    try:
        store = load_store()
        max_date = latest_trade_date(con)
        changed = 0
        for e in store["entries"]:
            if e["status"] == STATUS_DROPPED:
                continue
            series = fetch_series(con, e["code"], int(e["registered_date"]))
            if not series:
                print(f"[leader_watchlist][WARN] {e['code']} {e['name']} 登记日{e['registered_date']}"
                      f"起无行情数据，跳过（不更新状态）")
                continue
            closes = [c for _, c, _ in series]
            vols = [v for _, _, v in series]
            peak = max(closes)
            peak_idx = closes.index(peak)
            e["last_close"] = closes[-1]
            e["last_update"] = series[-1][0]
            e["peak_close"] = peak
            e["peak_date"] = series[peak_idx][0]
            e["max_gain_from_register"] = round((peak / e["register_close"] - 1) * 100, 2)
            e["drawdown_from_peak"] = round((peak - closes[-1]) / peak * 100, 2)
            if e["status"] == STATUS_WATCHING:
                ev = _run_setup_check(e, series)
                if ev and not any(x["type"] == "setup_confirmed" for x in e["events"]):
                    e["events"].append(ev)
                    print(f"[leader_watchlist] ★ {e['code']} {e['name']} 回踩企稳 setup_confirmed "
                          f"@{ev['date']}: {ev['detail']}")
                    print(f"[leader_watchlist]   → 提请研究（非买信号；买入仍走名单制/特批，宪法§10）")
                peak_dd = e["drawdown_from_peak"]
                if not any(x["type"] == "setup_confirmed" for x in e["events"]) \
                        and peak_dd >= COOLDOWN_DRAWDOWN * 100:
                    e["status"] = STATUS_COOLDOWN
                    e["events"].append({"type": "cooldown", "date": series[-1][0],
                                        "detail": f"峰值回撤{peak_dd:.1f}%≥{COOLDOWN_DRAWDOWN*100:.0f}%且从未setup"})
                    print(f"[leader_watchlist] {e['code']} {e['name']} → cooldown（{peak_dd:.1f}%回撤未企稳）")
            changed += 1
        save_store(store)
        print(f"[leader_watchlist] update 完成: {changed} 只活跃条目, 行情补至 {max_date}, 存储={STORE_PATH}")
    finally:
        con.close()


def cmd_report(args):
    store = load_store()
    entries = [e for e in store["entries"] if e["status"] != STATUS_DROPPED]
    if not entries:
        print("[leader_watchlist] 池为空（尚无登记）")
        return
    print(f"主线龙头观察池（{len(entries)} 只活跃）— 登记锚=qfq收盘 | 观察池≠买入名单")
    print(f"{'代码':<8}{'名称':<10}{'主题':<10}{'登记日':<10}{'锚价':>7}{'现价':>7}"
          f"{'峰值涨幅':>9}{'峰回撤':>8}{'状态':>10}  事件")
    for e in entries:
        evs = "; ".join(f"{x['type']}@{x['date']}" for x in e["events"]) or "-"
        print(f"{e['code']:<8}{e['name']:<10}{e.get('theme',''):<10}{e['registered_date']:<10}"
              f"{e['register_close']:>7.2f}{e['last_close']:>7.2f}"
              f"{e['max_gain_from_register']:>8.1f}%{e['drawdown_from_peak']:>7.1f}%"
              f"{e['status']:>10}  {evs}")


def cmd_drop(args):
    store = load_store()
    code = _norm_code(args.code)
    hit = False
    for e in store["entries"]:
        if e["code"] == code and e["status"] != STATUS_DROPPED:
            e["status"] = STATUS_DROPPED
            e["events"].append({"type": "dropped", "date": datetime.now().strftime("%Y%m%d"),
                                "detail": args.reason or "主人裁决移除"})
            hit = True
    if not hit:
        _fail(f"{code} 不在池中或已移除")
    save_store(store)
    print(f"[leader_watchlist] {code} 已移除（{args.reason or '主人裁决'}）")


def main():
    ap = argparse.ArgumentParser(description="主线龙头观察池 v1（跟踪≠买入）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="登记主线龙头（复盘末尾调用，幂等）")
    p.add_argument("--code", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--theme", default="")
    p.add_argument("--ladder", type=int, default=0, help="登记时连板高度")
    p.add_argument("--reason", required=True)
    p.add_argument("--date", help="登记锚日 YYYYMMDD（缺省=库内最新交易日；历史补登用）")
    p.add_argument("--qfq-path")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("update", help="补齐行情 + setup 检查（幂等可重跑）")
    p.add_argument("--qfq-path")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("report", help="输出池状态")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("drop", help="移除（主人裁决）")
    p.add_argument("--code", required=True)
    p.add_argument("--reason", default="")
    p.set_defaults(func=cmd_drop)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
