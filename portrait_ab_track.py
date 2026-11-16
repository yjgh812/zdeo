#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_ab_track.py · 画像增强排序 A/B 前向追踪台账 CLI（2026-09-05 "全面对接"裁决落地）

背景：主人裁决画像全面对接屠龙。对接形态=提示性标注（白名单 note，零拦截权）+ 本台账前向验证。
  A 臂 = 当日名单「原序前 K」(现状排序, bp+ep/MASTER-HIST 合同序)
  B 臂 = 同一名单内「按画像分重排前 K」(portrait_feed.json scores_short, 无分者排末位)
  排序价值判据 = 前 K 名质量: fwd5/fwd10 等权均值 + 命中率(>0 占比), 首验 2026-09-30。
  同池同 K 对照 → 差值纯反映画像排序增益; 若 B 无增益 → 画像维持"仅标注"不升级(纪律)。

用法（每个选股日一次，选股会话内执行；名单顺序=合同序）：
  python portrait_ab_track.py append --list 600000,000001,... --note "9/8盘前"
      （B 序自动= 按画像分重排；如需手工指定 --enhanced 覆盖）
  python portrait_ab_track.py show
  python portrait_ab_track.py eval --fwd 5 10 --k 10     # 9/30 首验
测试隔离: --file 指向副本。生产台账 data/portrait_ab_track.jsonl 勿手改。
口径: 无费用观察口径; fwd_N=open[D+1+N]/open[D+1]-1 次开入场; 样本不足结构化返回不伪造结论。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = Path(BASE) / "data" / "portrait_ab_track.jsonl"
FEED_F = Path(BASE) / "data" / "portrait_feed.json"


def _norm6(c: str) -> str:
    c = str(c).strip()
    for pre in ("sh", "sz", "SH", "SZ"):
        if c.startswith(pre):
            c = c[len(pre):]
    return c.zfill(6)


def _valid_date(d: str) -> bool:
    # 2026-09-10 B3 修复: 调用方 tulong_buy_whitelist 传 YYYYMMDD, 原 ISO-only 校验
    # 全拒 → 画像 A/B 台账 9/7 起零登记(9/30 首验哑弹); 双格式兼容
    s = str(d or "").strip()
    if re.fullmatch(r"\d{8}", s):
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    try:
        _date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _enhance_order(codes: list[str]) -> tuple[list[str], str]:
    """按画像分重排（feed 快照 scores_short）；无分/无快照 → 原序末位保持（fail-open 诚实降级）。"""
    if not FEED_F.exists():
        return codes, "feed快照缺失→B=A原序"
    try:
        snap = json.loads(FEED_F.read_text(encoding="utf-8"))
    except Exception as e:
        return codes, f"feed解析失败({e})→B=A原序"
    scores = snap.get("scores_short") or {}
    if not scores:
        return codes, "feed无short分数→B=A原序"
    ranked = sorted(codes, key=lambda c: scores.get(c, {}).get("prob", float("-inf")), reverse=True)
    return ranked, f"画像分重排(快照{snap.get('snapshot_date')}, 分数覆盖{sum(1 for c in codes if c in scores)}/{len(codes)})"


def cmd_append(args) -> int:
    ledger = Path(args.file)
    d = args.date or _date.today().isoformat()
    if not _valid_date(d):
        print(f"[FAIL] 非法日期 {d!r}"); return 2
    codes = [_norm6(x) for x in args.list.split(",") if x.strip()]
    bad = [c for c in codes if not re.fullmatch(r"\d{6}", c)]
    if bad:
        print(f"[FAIL] 非法代码 {bad}"); return 2
    codes = list(dict.fromkeys(codes))  # 保序去重
    if len(codes) < (args.k or 10):
        print(f"[FAIL] 名单{len(codes)}只 < K={args.k or 10}（同池前K对照须 K≤名单长度）"); return 2
    if args.enhanced:
        enhanced = [_norm6(x) for x in args.enhanced.split(",") if x.strip()]
        b_note = "手工指定B序"
    else:
        enhanced, b_note = _enhance_order(codes)
    row = {"date": d, "original": codes, "enhanced": enhanced, "k": args.k,
           "b_note": b_note, "notes": args.note or "", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    dup = None
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("date") == d:
                dup = o
    if dup and not args.force:
        print(f"[FAIL] {d} 已有登记行（覆盖用 --force）"); return 2
    if dup and args.force:
        lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines()
                 if not (ln.strip() and _is_same_date_row(ln, d))]
        ledger.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    if ledger.exists() and ledger.stat().st_size > 0:
        with ledger.open("rb") as f:
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                with ledger.open("a", encoding="utf-8") as f:
                    f.write("\n")
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[OK] 已{'替换' if dup else '登记'} {d}: 名单{len(codes)}只 K={args.k} | {b_note}")
    return 0


def _is_same_date_row(line: str, d: str) -> bool:
    try:
        return json.loads(line).get("date") == d
    except json.JSONDecodeError:
        return False


def cmd_show(args) -> int:
    ledger = Path(args.file)
    if not ledger.exists():
        print("[N/A] 台账不存在"); return 1
    n = 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        n += 1
        if "init" in o:
            print(f"[init] {o.get('note', '')[:70]}")
            continue
        print(f"{o.get('date')} n={len(o.get('original', []))} K={o.get('k')} {o.get('b_note', '')[:46]}")
    if n == 0:
        print("[N/A] 台账为空（每个选股日 append 一行；首验 2026-09-30）")
    return 0


def cmd_eval(args) -> int:
    rows = []
    if Path(args.file).exists():
        for line in Path(args.file).read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(o)
    if not rows:
        print(json.dumps({"status": "insufficient_data",
                          "reason": "台账无登记行（每个选股日 append 一行；首验 2026-09-30）"},
                         ensure_ascii=False))
        return 1
    # init 头行等无 date 键的行非登记行, 过滤后重判空(9/5 首验预演修复: KeyError 'date')
    rows = [r for r in rows if isinstance(r, dict) and r.get("date")]
    if not rows:
        print(json.dumps({"status": "insufficient_data",
                          "reason": "台账仅 init 头行, 无选股日登记行（每个交易日由白名单链自动 append；首验 2026-09-30）"},
                         ensure_ascii=False))
        return 1
    dedup = {r["date"]: r for r in rows}
    rows = list(dedup.values())
    k = args.k
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    px = pd.read_sql_query(
        "SELECT code,date,open FROM stock_daily_meta_qfq WHERE date>='20260101'", con)
    con.close()
    px["date"] = px["date"].astype(int)
    out = {"status": "ok", "n_days": len(rows), "k": k, "fwd": {},
           "note": "无费用观察口径; A=名单原序前K, B=画像重排前K; fwd_N=open[D+1+N]/open[D+1]-1 次开入场"}
    for N in args.fwd:
        a_rets, b_rets = [], []
        skipped = 0
        for row in rows:
            kk = min(k, row.get("k") or k)
            if not _valid_date(row["date"]):
                skipped += 1
                continue
            d_int = int(row["date"].replace("-", ""))
            after = px[px["date"] > d_int]
            days = sorted(after["date"].unique())
            if len(days) < 1 + N:
                skipped += 1
                continue
            entry, exit_ = days[0], days[N]
            o_in = px[px["date"] == entry].set_index("code")["open"]
            o_out = px[px["date"] == exit_].set_index("code")["open"]
            for key, lst in (("A", row["original"][:kk]), ("B", row["enhanced"][:kk])):
                rets = [o_out[c] / o_in[c] - 1.0 for c in lst
                        if c in o_in.index and c in o_out.index]
                (a_rets if key == "A" else b_rets).extend(rets)
        def _pack(rets):
            return {"n": len(rets),
                    "mean": round(float(np.mean(rets)), 6) if rets else None,
                    "hit_pct": round(float(np.mean([r > 0 for r in rets])) * 100, 1) if rets else None}
        out["fwd"][f"N{N}"] = {"skipped_days": skipped,
                               "A_原序前K": _pack(a_rets), "B_画像重排前K": _pack(b_rets)}
        if a_rets and b_rets:
            out["fwd"][f"N{N}"]["diff_B-A"] = round(float(np.mean(b_rets) - np.mean(a_rets)), 6)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="画像增强排序 A/B 前向追踪台账 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("append", help="登记一次选股（每选股日一行；B序自动按画像分重排）")
    p1.add_argument("--list", required=True, help="当日名单（合同序，逗号分隔）")
    p1.add_argument("--enhanced", default="", help="手工指定B序（缺省自动按画像分重排）")
    p1.add_argument("--k", type=int, default=10, help="前K名对照（默认10）")
    p1.add_argument("--note", default="")
    p1.add_argument("--date", default="")
    p1.add_argument("--file", default=str(LEDGER))
    p1.add_argument("--force", action="store_true")
    p2 = sub.add_parser("show", help="查看台账")
    p2.add_argument("--file", default=str(LEDGER))
    p3 = sub.add_parser("eval", help="fwd 对比评估（9/30 首验）")
    p3.add_argument("--fwd", type=int, nargs="+", default=[5, 10])
    p3.add_argument("--k", type=int, default=10)
    p3.add_argument("--file", default=str(LEDGER))
    a = ap.parse_args()
    return {"append": cmd_append, "show": cmd_show, "eval": cmd_eval}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
