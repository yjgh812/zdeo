#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# ⛔ 停用封存（2026-09-20 主人裁决 PD-03 选 C 停用）：
#    sentiment_cycle 设施整体停用（falsified 第63条 SENTIMENT-CYCLE-V1：
#    全史重放 n=2604 无判别力+"退潮"标签方向相反，与 P-B1 双路同向证伪）。
#    台账 data/sentiment_cycle_daily.jsonl 只读封存；替代覆盖=MARKETSTATE_2000
#    +limit_up_patterns 库。本脚本随设施封存禁止运行；
#    复活仅允许以可回测新判据走 prereg→影子→三闸，禁止复活 v1 阈值。
#    裁决材料：reports/sentiment_cycle_verdict_material_20260920.md
# ---------------------------------------------------------------------------
r"""
sentiment_cycle_eval.py — 情绪阶段区分度诊断（Phase 2 初标，纯描述性统计）

定位：读情绪台账（sentiment_cycle_daily.jsonl）+ qfq 等权日收益（与回填同域：
  沪深主板+创业板，剔科创/北交），输出三张诊断表：
  ① 阶段→次日/未来5日等权收益（均值/中位/胜率/N）
  ② 阶段转移矩阵（今日→明日）
  ③ 判定输入分布（涨停家数/炸板率/最高板 按阶段）——解释阶段失衡成因

反数据窥探纪律：本脚本只做描述诊断，不调阈值、不产交易规则。
  据此调 v1 阈值须走 prereg 立项（冻结新阈值→样本外验证）。
退出码：0 成功
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = Path(__file__).resolve().parent / "data" / "sentiment_cycle_daily.jsonl"
STAGES = ["冰点", "修复", "分歧", "狂热", "退潮", "unknown"]


def load_ew_returns(d0: str) -> dict:
    conn = sqlite3.connect(QFQ_DB)
    rows = conn.execute(
        "SELECT date, AVG(change_pct) FROM stock_daily_meta_qfq "
        "WHERE date>=? AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%') "
        "GROUP BY date ORDER BY date", (d0.replace("-", ""),)).fetchall()
    conn.close()
    return {f"{d[:4]}-{d[4:6]}-{d[6:]}": r for d, r in rows if r is not None}


def fwd_stats(rows: list, ew: dict, horizon: int) -> dict:
    """阶段 → 未来 horizon 日等权累计收益样本。"""
    dates = sorted(ew)
    cum = {}
    for i, d in enumerate(dates):
        prod = 1.0
        ok = False
        for j in range(i + 1, min(i + 1 + horizon, len(dates))):
            prod *= (1 + ew[dates[j]] / 100.0)
            ok = True
        if ok:
            cum[d] = (prod - 1) * 100
    out = defaultdict(list)
    for r in rows:
        v = cum.get(r["date"])
        if v is not None:
            out[r["stage"]].append(v)
    return out


def tbl(title: str, stats: dict) -> None:
    print(f"\n{title}")
    print(f"{'阶段':<6}{'N':>5}{'均值%':>9}{'中位%':>9}{'胜率':>8}")
    for s in STAGES:
        v = sorted(stats.get(s, []))
        if not v:
            continue
        n = len(v)
        mean = sum(v) / n
        med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
        win = sum(1 for x in v if x > 0) / n
        print(f"{s:<6}{n:>5}{mean:>9.2f}{med:>9.2f}{win:>7.0%}")


def main() -> int:
    rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows.sort(key=lambda r: r["date"])
    if len(rows) < 30:
        print("[FAIL] 台账不足30日", file=sys.stderr)
        return 2
    ew = load_ew_returns(rows[0]["date"])
    in_ew = [r for r in rows if r["date"] in ew]
    print(f"台账 {len(rows)} 日，与等权收益域对齐 {len(in_ew)} 日 "
          f"({in_ew[0]['date']}~{in_ew[-1]['date']})")

    # ① 次日 / 未来5日
    next_stats = defaultdict(list)
    dates_ew = sorted(ew)
    next_map = {d: dates_ew[i + 1] for i, d in enumerate(dates_ew[:-1])}
    for r in in_ew:
        nd = next_map.get(r["date"])
        if nd is not None:
            next_stats[r["stage"]].append(ew[nd])
    tbl("【① 阶段→次日等权收益】", next_stats)
    tbl("【① 阶段→未来5日等权累计收益】", fwd_stats(in_ew, ew, 5))

    # ② 转移矩阵
    print("\n【② 阶段转移矩阵（行=今日，列=明日，单位%）】")
    trans = defaultdict(Counter)
    by_date = {r["date"]: r["stage"] for r in in_ew}
    for d, nd in next_map.items():
        if d in by_date and nd in by_date:
            trans[by_date[d]][by_date[nd]] += 1
    used = [s for s in STAGES if any(trans[s].values())]
    print(f"{'':<8}" + "".join(f"{s:>8}" for s in used))
    for s in used:
        tot = sum(trans[s].values())
        print(f"{s:<8}" + "".join(f"{trans[s][t] / tot:>7.0%} " for t in used))

    # ③ 判定输入分布
    print("\n【③ 判定输入分布（按阶段，解释阶段失衡）】")
    agg = defaultdict(lambda: {"lu": [], "br": [], "mb": []})
    for r in in_ew:
        m = r.get("metrics") or {}
        if m.get("break_rate") is not None:
            agg[r["stage"]]["br"].append(m["break_rate"])
        if m.get("limit_up_count") is not None:
            agg[r["stage"]]["lu"].append(m["limit_up_count"])
        if m.get("max_board") is not None:
            agg[r["stage"]]["mb"].append(m["max_board"])
    print(f"{'阶段':<6}{'涨停均值':>9}{'炸板率均值':>10}{'最高板中位':>10}")
    for s in STAGES:
        a = agg.get(s)
        if not a or not a["br"]:
            continue
        lu = sum(a["lu"]) / len(a["lu"]) if a["lu"] else float("nan")
        br = sum(a["br"]) / len(a["br"])
        mb = sorted(a["mb"])[len(a["mb"]) // 2] if a["mb"] else float("nan")
        print(f"{s:<6}{lu:>9.0f}{br:>9.0%}{mb:>10}")

    dist = Counter(r["stage"] for r in in_ew)
    print(f"\n阶段占比: " + " ".join(f"{s}{dist.get(s, 0)}({dist.get(s, 0) / len(in_ew):.0%})"
                                    for s in used))
    print("⚠️ 纯描述诊断：不调阈值、不产交易规则；v1 阈值再标定须 prereg 立项。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
