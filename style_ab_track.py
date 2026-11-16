#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""style_ab_track.py · 不追高闸 A/B 前向追踪台账 CLI（2026-09-05）

背景：9/5 主人裁决 A（style-gate stage-5）落地 A/B 前向追踪：实盘走过滤后名单=A 级，
原始名单纸面=B 级，台账 data/style_ab_track.jsonl，首验 2026-09-30（fwd5/fwd10 双口径）。
缺口审计发现台账零写入方（仅初始化行）——本 CLI 把"每次选股登记一行"变成一条命令，
并把 9/30 对比做成现成 eval 模式，消除首验哑弹风险。

用法：
  登记（每个选股日一次，在选股会话内执行）：
    python style_ab_track.py append --original 600000,000001,300750 --filtered 600000 --note "9/8盘前七步法"
  查看：
    python style_ab_track.py show
  评估（9/30 及以后；fwd_N = open[D+1+N]/open[D+1]-1 等权，次开入场口径，无费用观察口径）：
    python style_ab_track.py eval --fwd 5 10
  测试隔离：--file 可指向副本（eval/append 均支持），生产台账勿手改。

口径与边界：
  - A 级=filtered（不追高过滤后，实盘实际执行名单）；B 级=original（原始名单，纸面对照）。
  - eval 为无费用观察口径（名单级对照，非成交级）；样本不足时结构化返回，不伪造结论。
  - 本工具零生产链改动：不接 cron、不进 executor，纪律写入依赖选股会话执行（SOP 归 style-gate 主线）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = Path(BASE) / "data" / "style_ab_track.jsonl"


def _norm6(c: str) -> str:
    c = str(c).strip()
    for pre in ("sh", "sz", "SH", "SZ"):
        if c.startswith(pre):
            c = c[len(pre):]
    return c.zfill(6)


_DATE_RE = None


def _valid_date(d: str) -> bool:
    """YYYY-MM-DD 或 YYYYMMDD 且为真实日历日(2026-09-10 B2 修复: 台账登记方
    daily_trade_plan 直写 YYYYMMDD, eval 只认 ISO → 6 期全被格式跳过=9/30 首验哑弹)。"""
    import re as _re
    from datetime import date as _d
    s = str(d or "").strip()
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        if _re.fullmatch(r"\d{8}", s):
            s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
        else:
            return False
    try:
        _d.fromisoformat(s)
        return True
    except ValueError:
        return False


def cmd_append(args) -> int:
    ledger = Path(args.file)
    # 输入验证 fail-closed：日期与代码在写入前钉死，防脏行进台账（eval 崩溃源）
    if not _valid_date(args.date or _date.today().isoformat()):
        print(f"[FAIL] 非法日期 {args.date!r}（须 YYYY-MM-DD 真实日历日）")
        return 2
    original = sorted({_norm6(x) for x in args.original.split(",") if x.strip()})
    filtered = sorted({_norm6(x) for x in args.filtered.split(",") if x.strip()})
    import re as _re
    bad = [c for c in original + filtered if not _re.fullmatch(r"\d{6}", c)]
    if bad:
        print(f"[FAIL] 非法代码 {bad}（norm6 后须 6 位数字；板块/指数/词组不可入台账）")
        return 2
    row = {
        "date": args.date or _date.today().isoformat(),
        "original": original,
        "filtered": filtered,
        "notes": args.note or "",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if not row["original"]:
        print("[FAIL] --original 为空（原始名单必填，B 级对照基线）")
        return 2
    dup_date = None
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("date") == row["date"] and "init" not in d:
                dup_date = d
    if dup_date and not args.force:
        print(f"[FAIL] {row['date']} 已有登记行（SOP 合同=append 幂等；覆盖用 --force）")
        return 2
    if dup_date and args.force:
        # SOP 合同=append 幂等：--force 同日替换（非追加），防 eval 重复计数
        lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines()
                 if not (ln.strip() and _is_same_date_row(ln, row["date"]))]
        ledger.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    # 黏行防护：既有内容无换行符结尾时先补
    if ledger.exists() and ledger.stat().st_size > 0:
        with ledger.open("rb") as f:
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                with ledger.open("a", encoding="utf-8") as f:
                    f.write("\n")
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    action = "替换" if dup_date else "登记"
    print(f"[OK] 已{action} {row['date']}: original={len(row['original'])}只 filtered={len(row['filtered'])}只")
    return 0


def _is_same_date_row(line: str, d: str) -> bool:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return False
    return obj.get("date") == d and "init" not in obj


def cmd_show(args) -> int:
    ledger = Path(args.file)
    if not ledger.exists():
        print("[N/A] 台账不存在")
        return 1
    n = 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        n += 1
        if "init" in d:
            print(f"[init] {d.get('note', '')[:60]}")
        else:
            print(f"{d.get('date')} orig={len(d.get('original', []))} "
                  f"filt={len(d.get('filtered', []))} note={d.get('notes', '')[:40]}")
    if n == 0:
        print("[N/A] 台账为空")
    return 0


def cmd_eval(args) -> int:
    ledger = Path(args.file)
    rows = []
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "init" not in d:
                rows.append(d)
    if not rows:
        print(json.dumps({"status": "insufficient_data",
                          "reason": "台账无登记行（每个选股日需 append 一行；首验 2026-09-30）"},
                         ensure_ascii=False))
        return 1
    if any(n < 1 for n in args.fwd):
        print("[FAIL] --fwd 须 >=1（持有 N 个交易日）")
        return 2
    # 同日重复行防护（手工编辑事故兜底）：同日保留最后一行
    dedup = {}
    for r in rows:
        dedup[r["date"]] = r
    if len(dedup) != len(rows):
        rows = list(dedup.values())
        print(f"[WARN] 台账存在同日重复行，已按最后行去重（{len(rows)} 期有效）")

    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    px = pd.read_sql_query(
        "SELECT code,date,open FROM stock_daily_meta_qfq WHERE date>='20260101'", con)
    con.close()
    px["date"] = px["date"].astype(int)
    out = {"status": "ok", "n_days": len(rows), "fwd": {},
           "note": "无费用观察口径; fwd_N=open[D+1+N]/open[D+1]-1 等权次开入场"}
    for N in args.fwd:
        a_rets, b_rets = [], []
        skipped = 0
        for row in rows:
            if not _valid_date(row["date"]):
                skipped += 1
                continue
            d_int = int(row["date"].replace("-", ""))
            after = px[px["date"] > d_int]
            days = sorted(after["date"].unique())
            if len(days) < 1 + N:
                skipped += 1        # 数据尾部/未来日：无完整 fwd 窗口
                continue
            entry_date, exit_date = days[0], days[N]
            o_in = px[px["date"] == entry_date].set_index("code")["open"]
            o_out = px[px["date"] == exit_date].set_index("code")["open"]
            for key, lst in (("A", row["filtered"]), ("B", row["original"])):
                rets = [o_out[c] / o_in[c] - 1.0
                        for c in lst if c in o_in.index and c in o_out.index]
                (a_rets if key == "A" else b_rets).extend(rets)
        out["skipped_days"] = skipped if args.fwd.index(N) == 0 else out.get("skipped_days", skipped)
        out["fwd"][f"N{N}"] = {
            "skipped_days": skipped,
            "A_filtered": {"n": len(a_rets),
                           "mean": round(float(np.mean(a_rets)), 6) if a_rets else None},
            "B_original": {"n": len(b_rets),
                           "mean": round(float(np.mean(b_rets)), 6) if b_rets else None},
        }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="不追高闸 A/B 前向追踪台账 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("append", help="登记一次选股（每选股日一行）")
    p1.add_argument("--original", required=True, help="原始名单，逗号分隔")
    p1.add_argument("--filtered", default="", help="不追高过滤后名单（实盘 A 级）")
    p1.add_argument("--note", default="")
    p1.add_argument("--date", default="", help="默认今天")
    p1.add_argument("--file", default=str(LEDGER))
    p1.add_argument("--force", action="store_true")
    p2 = sub.add_parser("show", help="查看台账")
    p2.add_argument("--file", default=str(LEDGER))
    p3 = sub.add_parser("eval", help="fwd 对比评估（9/30 首验用）")
    p3.add_argument("--fwd", type=int, nargs="+", default=[5, 10])
    p3.add_argument("--file", default=str(LEDGER))
    a = ap.parse_args()
    return {"append": cmd_append, "show": cmd_show, "eval": cmd_eval}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
