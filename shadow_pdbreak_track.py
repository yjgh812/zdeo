#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shadow_pdbreak_track.py — PD-018 规则v2 影子记账器（M4，2026-09-29）。

规则v2（9/23 复盘二轮终审提案，pd018_fake_seal_empirical）：
  「连板高度≥2 ∧ 当日炸板(broken=涨停开板未回封) → 当日尾盘离场」。
  9/23 定调：先影子 20 例再进卖出链——本脚本=影子记账与结算面，**零交易权**，
  产物只供 PD-018 裁决，不得解读为信号（台账未满 20 例前 gate=FAIL/未满）。

口径:
  - 事件 = attribution.db seal_snapshot broken=1 行（三态互斥，9/22 入册）。
  - 高度 = 事件日前一交易日止的连续涨停天数（stock_daily_meta.change_pct≥9.8% 主板 /
    ≥19.8% 创业30/301；真实价口径）。
  - 执行 proxy = 事件日收盘价（规则=尾盘离场）；结算 T+1 开盘/收盘、T+3/T+5 收盘
    （stock_daily_meta，真实价，未到期=如实 pending 不当零）。
  - 对照组 = 同日 sealed=1 且高度≥2（持封组 T+1 收盘收益），用于判读炸板增量。
  - 幂等键 (date,code)；回填段(≤20260928)=回溯热身非前向影子，前向自首跑后自然积累。

用法: python shadow_pdbreak_track.py [--backfill] [--date YYYYMMDD]
输出: data/pd018_break_shadow_ledger.jsonl（替换式幂等同步）+ reports/pd018_break_shadow_<date>.md
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime

ROOT = r"D:\zcodesj"
ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db")
META_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro"
LEDGER = os.path.join(ROOT, "data", "pd018_break_shadow_ledger.jsonl")
SHADOW_MIN_N = 20  # PD-018 定调: ≥20 例才具备裁决呈报资格


def board_limit_pct(code: str) -> float:
    """[已废弃 2026-09-29] change_pct 坏字段勘误后不再使用, 保留占位防旧引用。"""
    return 19.8 if str(code)[-6:].startswith(("30", "68")) else 9.8


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="回填 seal_snapshot 全史热身")
    ap.add_argument("--date", default="", help="单日模式 YYYYMMDD(默认=库内最新日)")
    a = ap.parse_args()

    acon = sqlite3.connect(ATTR_DB)
    mcon = sqlite3.connect(META_DB, uri=True)
    cal = [str(r[0]) for r in mcon.execute(
        "SELECT DISTINCT date FROM stock_daily_meta ORDER BY date")]
    idx = {d: i for i, d in enumerate(cal)}

    if a.backfill:
        events = acon.execute(
            "SELECT date, code, name, price, prev_close FROM seal_snapshot "
            "WHERE broken=1 ORDER BY date").fetchall()
    else:
        d = a.date or str(acon.execute("SELECT MAX(date) FROM seal_snapshot").fetchone()[0])
        events = acon.execute(
            "SELECT date, code, name, price, prev_close FROM seal_snapshot "
            "WHERE broken=1 AND date=?", (d,)).fetchall()
    ctl_rows = acon.execute(
        "SELECT date, code, name, price FROM seal_snapshot WHERE sealed=1").fetchall()

    def height(code: str, before: str) -> int:
        """事件日前一交易日止的连续涨停天数（≤10 封顶）。

        2026-09-29 勘误: stock_daily_meta.change_pct 字段大面积为0(9月仅5%非零=坏字段),
        改用 close 序列自算涨停判定 close>=round(prev_close×1.1,2)(主板)/×1.2(创业)。
        """
        i = idx.get(str(before))
        if i is None or i == 0:
            return 0
        c6 = str(code)[-6:]
        lim = 1.2 if c6.startswith(("30", "68")) else 1.1
        rows = mcon.execute(
            "SELECT date, close FROM stock_daily_meta WHERE code=? AND date>? AND date<=? "
            "ORDER BY date", (c6, cal[max(0, i - 11)], cal[i - 1])).fetchall()
        h = 0
        for j in range(len(rows) - 1, 0, -1):
            prev_c, cur_c = float(rows[j - 1][1] or 0), float(rows[j][1] or 0)
            if prev_c > 0 and cur_c >= round(prev_c * lim, 2):
                h += 1
            else:
                break
        return h

    def fwd(code: str, base_date: str, col: str, n: int):
        i = idx.get(str(base_date))
        if i is None or i + n >= len(cal):
            return None
        d = cal[i + n]
        r = mcon.execute(
            f"SELECT {col} FROM stock_daily_meta WHERE code=? AND date=?",
            (str(code)[-6:], d)).fetchone()
        return (float(r[0]) if r and r[0] else None), d

    new_lines, seen = [], set()
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            try:
                r = json.loads(line)
                seen.add((str(r.get("date")), str(r.get("code"))))
            except json.JSONDecodeError:
                continue

    for ed, code, name, px, pclose in events:
        key = (str(ed), str(code))
        if key in seen:
            continue
        h = height(code, ed)
        if h < 2:
            continue  # 规则v2 仅记高度≥2
        i = idx.get(str(ed))
        rec = {"kind": "pd018_v2_break", "date": str(ed), "code": str(code), "name": str(name),
               "height": h, "exit_proxy_close": float(px) if px else None,
               "t1_open_ret": None, "t1_close_ret": None, "t3_close_ret": None,
               "t5_close_ret": None, "settled_upto": None}
        if px and i is not None:
            t1 = fwd(code, ed, "open", 1)
            if t1 and t1[0]:
                rec["t1_open_ret"] = round(t1[0] / float(px) - 1, 4)
            for lbl, col, n in (("t1_close_ret", "close", 1), ("t3_close_ret", "close", 3),
                                ("t5_close_ret", "close", 5)):
                v = fwd(code, ed, col, n)
                if v and v[0]:
                    rec[lbl] = round(v[0] / float(px) - 1, 4)
                    rec["settled_upto"] = max(rec["settled_upto"] or 0, n)
        new_lines.append(rec)

    # 对照组(同日 sealed 高度≥2 的 T+1 收盘收益, 仅回填日逐对现算不入台账)
    ctl = []
    if a.backfill:
        bset = {(str(d), str(c)) for d, c, *_ in events}
        for cd, code, name, px in ctl_rows:
            if (str(cd), str(code)) in bset:
                continue
            h = height(code, cd)
            if h >= 2 and px and idx.get(str(cd)) is not None:
                v = fwd(code, cd, "close", 1)
                if v and v[0]:
                    ctl.append(round(v[0] / float(px) - 1, 4))

    # 替换式幂等同步: 旧 break 行按 (date,code) 保留原结算, 新事件覆盖/追加;
    # 非 pd018_v2_break 行原样保留(防他种 kind 漂移)
    old_break: dict[tuple, dict] = {}
    other_lines = []
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                other_lines.append(line)
                continue
            if r.get("kind") == "pd018_v2_break":
                old_break[(str(r.get("date")), str(r.get("code")))] = r
            else:
                other_lines.append(line)
    for r in new_lines:
        old_break[(r["date"], r["code"])] = r
    all_break = sorted(old_break.values(), key=lambda x: (x["date"], x["code"]))
    with open(LEDGER, "w", encoding="utf-8") as f:
        f.writelines(other_lines)
        for r in all_break:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    t1 = [r["t1_close_ret"] for r in all_break if r.get("t1_close_ret") is not None]
    t1o = [r["t1_open_ret"] for r in all_break if r.get("t1_open_ret") is not None]
    acon.close(); mcon.close()

    def stat(v):
        if not v:
            return "—"
        v2 = sorted(v)
        med = v2[len(v2) // 2]
        win = sum(1 for x in v if x > 0)
        return f"均值{sum(v) / len(v) * 100:+.2f}% 中位{med * 100:+.2f}% 胜率{win / len(v) * 100:.0f}% n={len(v)}"

    gate = "FAIL/未满" if len(all_break) < SHADOW_MIN_N else f"样本{n_dk if (n_dk := len(all_break)) else 0}≥{SHADOW_MIN_N} 可呈报"
    lines = [f"# PD-018 规则v2 影子记账报告（{datetime.now().strftime('%Y-%m-%d %H:%M')}）", "",
             f"- 事件源=seal_snapshot broken=1 ∧ 高度≥2；台账累计 **{len(all_break)}** 例"
             f"（回填热身+前向；本轮新增 {len(new_lines)}）",
             f"- 裁决资格 gate：**{gate}**（需 ≥{SHADOW_MIN_N} 例；未满前不得解读为信号）",
             f"- 离场proxy=事件日收盘；T+1收盘 {stat(t1)}；T+1开盘 {stat(t1o)}",
             f"- 对照组(同日持封 高度≥2) T+1收盘 {stat(ctl)}",
             "- 🔴 零交易权：本产物仅供 PD-018 裁决，生产接线须主人裁决+宪法§10流程", "",
             "| 日期 | 代码 | 名称 | 高度 | T+1开 | T+1收 | T+3 | T+5 |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(all_break, key=lambda x: (x["date"], x["code"]))[-30:]:
        fmt = lambda v: f"{v * 100:+.2f}%" if isinstance(v, (int, float)) else "pending"
        lines.append(f"| {r['date']} | {r['code']} | {r['name']} | {r['height']} "
                     f"| {fmt(r.get('t1_open_ret'))} | {fmt(r.get('t1_close_ret'))} "
                     f"| {fmt(r.get('t3_close_ret'))} | {fmt(r.get('t5_close_ret'))} |")
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    with open(os.path.join(ROOT, "reports", "pd018_break_shadow.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[PD018-SHADOW] 台账={len(all_break)}例 新增={len(new_lines)} gate={gate} "
          f"T+1收盘{stat(t1)} 对照持封{stat(ctl)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
