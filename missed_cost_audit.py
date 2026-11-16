#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""错过成本周归因 missed_cost_audit v1 (2026-09-06)

问题：买入白名单（reports/buy_whitelist_*.csv）中被负规则压制的候选
（tier<1.0x 降权，或 note 含 禁追/首亏/增亏/预减/减亏/loss_case/归因风险），
事后涨了多少从未统计——"炸板禁追""业绩首亏过滤"等规则的机会成本不量化，
规则参数只能凭感觉调。典型案例：20260824 白名单中金健米业(600127) 0.25x
压制@8.65，此后至 20260902 高点 +60%。

口径（诚实边界）：
- 锚价 = qfq 库 D0（白名单文件日期）close，与 T+N close 同库同口径算 pct；
  不用 CSV price 列（那是生成时点参考价，非收盘）。
- 观察性统计，非策略验证：白名单本身已经过筛（mass_scan/master_hist 候选池），
  结论仅量化"规则在白名单内的机会成本"，禁止外推为全市场规律、禁止据此接线。
- 窗口不足的记录如实计入覆盖率，不虚评。
- 价格源 fail-closed：qfq 库不可用 exit 2（可 --qfq-path 指向备份快照）。

用法：
  python missed_cost_audit.py run                       # 全量历史
  python missed_cost_audit.py run --since 20260820     # 起始日期过滤
"""
import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WHITELIST_GLOB = ROOT / "reports" / "buy_whitelist_*.csv"
OUT_MD = ROOT / "reports" / "missed_cost_audit_{date}.md"
OUT_JSON = ROOT / "reports" / "missed_cost_audit_{date}.json"
DEFAULT_QFQ = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")  # 2026-09-07 统一单源: 生产主库(9/6 SOP 裁定), zcodesj data/ 副本停用

WINDOWS = [5, 10, 20]
# note 负标记词（压制证据）；'预增'为正标记不会误中
SUPPRESS_KEYWORDS = ["禁追", "首亏", "增亏", "预减", "减亏", "loss_case", "归因风险"]


def _fail(msg: str, code: int = 2):
    print(f"[missed_cost_audit][ERROR] {msg}")
    sys.exit(code)


def parse_tier(tier: str) -> float:
    tier = (tier or "").strip()
    if tier.endswith("x"):
        try:
            return float(tier[:-1])
        except ValueError:
            return 1.0
    return 1.0


def is_suppressed(tier: float, note: str) -> tuple:
    if tier < 1.0:
        return True, f"tier={tier}x降权"
    hit = [k for k in SUPPRESS_KEYWORDS if k in note]
    return (True, "+".join(hit)) if hit else (False, "")


def load_whitelists(since: int):
    files = sorted(WHITELIST_GLOB.parent.glob(WHITELIST_GLOB.name))
    records = []
    for f in files:
        try:
            d0 = int(f.stem.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if d0 < since:
            continue
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                code = (row.get("code") or "").strip()
                if not (code.isdigit() and len(code) == 6):
                    continue
                tier = parse_tier(row.get("tier", ""))
                note = row.get("note") or ""
                sup, why = is_suppressed(tier, note)
                records.append({"d0": d0, "code": code, "name": (row.get("name") or "").strip(),
                                "source": (row.get("source") or "").strip(),
                                "tier": tier, "suppressed": sup, "suppress_reason": why})
    # 同日同码去重（保留首条）
    seen, deduped = set(), []
    for r in records:
        key = (r["d0"], r["code"])
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


def open_qfq(path: Path) -> sqlite3.Connection:
    if not path.exists() or path.stat().st_size == 0:
        _fail(f"qfq 价格源不可用: {path}（不存在或0字节；主库并行重建期可用 "
              f"--qfq-path 指向 backups 下 qfq 周备最新快照，见 DATA_LOCATIONS 备份族）")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                        "AND name='stock_daily_meta_qfq'").fetchone()[0]
        if n == 0:
            _fail(f"qfq 价格源缺 stock_daily_meta_qfq 表: {path}")
    except sqlite3.DatabaseError as e:
        _fail(f"qfq 价格源损坏: {path}: {e}")
    return con


def compute(con, records):
    for r in records:
        rows = con.execute("SELECT date, close FROM stock_daily_meta_qfq "
                           "WHERE code=? AND date>=? ORDER BY date",
                           (r["code"], r["d0"])).fetchall()
        if not rows or int(rows[0][0]) != r["d0"]:
            r["skip"] = "d0无行情"
            continue
        d0_close = float(rows[0][1])
        r["d0_close"] = d0_close
        closes = [float(x[1]) for x in rows]
        for w in WINDOWS:
            if len(closes) > w:
                r[f"t{w}"] = round((closes[w] / d0_close - 1) * 100, 2)
            else:
                r[f"t{w}"] = None
    return [r for r in records if "skip" not in r]


def group_stats(recs):
    out = {"n": len(recs)}
    for w in WINDOWS:
        vals = [r[f"t{w}"] for r in recs if r[f"t{w}"] is not None]
        out[f"t{w}_coverage"] = f"{len(vals)}/{len(recs)}"
        if vals:
            vals_sorted = sorted(vals)
            out[f"t{w}_mean"] = round(sum(vals) / len(vals), 2)
            out[f"t{w}_median"] = round(vals_sorted[len(vals_sorted) // 2], 2)
            out[f"t{w}_gt10_pct"] = round(sum(1 for v in vals if v > 10) / len(vals) * 100, 1)
            out[f"t{w}_gt20_pct"] = round(sum(1 for v in vals if v > 20) / len(vals) * 100, 1)
        else:
            out[f"t{w}_mean"] = out[f"t{w}_median"] = None
            out[f"t{w}_gt10_pct"] = out[f"t{w}_gt20_pct"] = None
    return out


def cmd_run(args):
    since = int(args.since.replace("-", "")) if args.since else 0
    records = load_whitelists(since)
    if not records:
        _fail(f"无白名单记录（glob={WHITELIST_GLOB}, since={since}）", 1)
    con = open_qfq(Path(args.qfq_path) if args.qfq_path else DEFAULT_QFQ)
    try:
        valid = compute(con, records)
    finally:
        con.close()
    skipped = len(records) - len(valid)
    sup = [r for r in valid if r["suppressed"]]
    nor = [r for r in valid if not r["suppressed"]]
    sup_stats, nor_stats = group_stats(sup), group_stats(nor)

    # 错失榜：被压制组按可得最长窗口涨幅排序
    def best_t(r):
        for w in reversed(WINDOWS):
            if r[f"t{w}"] is not None:
                return w, r[f"t{w}"]
        return 0, None

    missed = sorted(sup, key=lambda r: (best_t(r)[1] if best_t(r)[1] is not None else -999),
                    reverse=True)[:args.top]

    today = datetime.now().strftime("%Y%m%d")
    result = {
        "version": 1, "generated": today,
        "scope": {"since": since, "files": str(WHITELIST_GLOB), "qfq_path": args.qfq_path or str(DEFAULT_QFQ)},
        "totals": {"records": len(records), "valid": len(valid), "skipped_no_quote": skipped,
                   "suppressed": len(sup), "normal": len(nor)},
        "suppressed_stats": sup_stats, "normal_stats": nor_stats,
        "missed_top": [{"d0": r["d0"], "code": r["code"], "name": r["name"], "source": r["source"],
                        "tier": f"{r['tier']}x", "why": r["suppress_reason"],
                        "d0_close": r.get("d0_close"),
                        **{f"t{w}": r[f"t{w}"] for w in WINDOWS}} for r in missed],
        "honesty_note": ("观察性统计非策略验证：白名单已经过筛（候选池非全市场随机），"
                         "仅量化负规则在白名单内的机会成本；禁止外推全市场、禁止据此直接接线。"),
    }

    lines = [
        f"# 错过成本周归因 missed_cost_audit {today}",
        "",
        f"- 范围: since={since or '全部'} | 记录 {len(records)}（有效 {len(valid)}，D0 无行情跳过 {skipped}）",
        f"- 被压制组 {len(sup)} 只 vs 正常组 {len(nor)} 只 | 锚价=qfq D0 收盘，同库算 pct",
        "",
        "## 组间对照（T+N 交易日收益 %）",
        "",
        "| 组 | 指标 | T+5 | T+10 | T+20 |",
        "|---|---|---|---|---|",
    ]
    for label, st in [("被压制(降权/负标记)", sup_stats), ("正常(1.0x无负标记)", nor_stats)]:
        cells = []
        for w in WINDOWS:
            cov = st[f"t{w}_coverage"]
            if st[f"t{w}_mean"] is None:
                cells.append(f"n/a (覆盖{cov})")
            else:
                cells.append(f"均{st[f't{w}_mean']}/中位{st[f't{w}_median']} (>10%: {st[f't{w}_gt10_pct']}%) 覆盖{cov}")
        lines.append(f"| {label} | 均值/中位 | {cells[0]} | {cells[1]} | {cells[2]} |")
    lines += ["", f"## 错失榜 Top{args.top}（被压制组，按可得最长窗口涨幅降序）", "",
              "| 白名单日 | 代码 | 名称 | 来源 | 压制 | 理由 | D0收 | T+5% | T+10% | T+20% |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in missed:
        fmt = lambda v: f"{v:.2f}" if v is not None else "n/a"
        lines.append(f"| {r['d0']} | {r['code']} | {r['name']} | {r['source']} | {r['tier']}x "
                     f"| {r['suppress_reason']} | {fmt(r.get('d0_close'))} "
                     f"| {fmt(r['t5'])} | {fmt(r['t10'])} | {fmt(r['t20'])} |")
    lines += ["", f"> {result['honesty_note']}", ""]

    OUT_JSON.with_name(OUT_JSON.name.format(date=today)).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.with_name(OUT_MD.name.format(date=today)).write_text("\n".join(lines), encoding="utf-8")
    print(f"[missed_cost_audit] 完成: 有效{len(valid)}/被压制{len(sup)}/正常{len(nor)}")
    if sup_stats["t10_mean"] is not None and nor_stats["t10_mean"] is not None:
        print(f"[missed_cost_audit] T+10 对照: 被压制均值 {sup_stats['t10_mean']}% "
              f"vs 正常组 {nor_stats['t10_mean']}%")
    print(f"[missed_cost_audit] 报告: {OUT_MD.with_name(OUT_MD.name.format(date=today))}")


def main():
    try:
        from trade_day_guard import is_trading_day
        if is_trading_day() is False:
            print("[missed_cost_audit] 休市日(交易日历判定), 优雅跳过")
            return 0
        # None=日历不可用, fail-open继续(周报类任务, 误跑无害)
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="白名单错过成本周归因 v1（观察性统计）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="全量归因（幂等可重跑）")
    p.add_argument("--since", help="起始白名单日期 YYYYMMDD")
    p.add_argument("--qfq-path")
    p.add_argument("--top", type=int, default=15)
    p.set_defaults(func=cmd_run)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
