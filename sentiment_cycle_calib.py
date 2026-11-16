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
sentiment_cycle_calib.py — 情绪阈值 v2 预注册标定（PREREG-SENTIMENT-THRESHOLD-V2）

执行 prereg 冻结内容：v1/v2 双口径对台账 250 日重分类对比（纯离线，不写台账），
按 prereg 过闸判据 P1/P2/P3 出 verdict，样本外指标回写 prereg 文件存档（只读一次）。

反窥探合同：阈值数值锚定 data/prereg_sentiment_threshold_v2_20260909.json
（脚本启动时断言关键数字在冻结字符串中，防实现漂移）；禁止按收益回调阈值。
退出码：0 完成；2 台账/prereg 不可用；3 指纹校验失败
"""
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREREG = ROOT / "data" / "prereg_sentiment_threshold_v2_20260909.json"
LEDGER = ROOT / "data" / "sentiment_cycle_daily.jsonl"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STAGES = ["冰点", "修复", "分歧", "狂热", "退潮", "unknown"]


def v2_classify(m: dict) -> tuple[str, str]:
    """v2 重分类（阈值=prereg 冻结：炸板率口径 +9pp 平移）。返回 (阶段, 依据)"""
    lu, lb, mb, pmb = (m.get("limit_up_count"), m.get("limit_break_count"),
                       m.get("max_board"), m.get("prev_max_board"))
    br = m.get("break_rate")
    if None not in (lu, mb) and lu < 30 and mb <= 2:
        return "冰点", f"涨停{lu}<30 且最高板{mb}<=2"
    if None not in (lu, mb, br) and lu >= 80 and mb >= 5 and br < 0.24:
        return "狂热", f"涨停{lu}>=80 且最高板{mb}>=5 且炸板率{br:.0%}<24%"
    if mb is not None and pmb is not None and (pmb - mb) >= 2:
        return "退潮", f"最高板{pmb}→{mb}回落{pmb - mb}级"
    if br is not None and br > 0.42 and (mb is None or mb <= 3):
        return "退潮", f"炸板率{br:.0%}>42% 且最高板{mb}<=3"
    if br is not None and br > 0.42:
        return "分歧", f"炸板率{br:.0%}>42%"
    if None not in (lu, mb) and lu >= 30:
        return "修复", f"涨停{lu}>=30 且最高板{mb}"
    return "unknown", "关键指标缺失"


def load_ew(d0: str) -> dict:
    conn = sqlite3.connect(QFQ_DB)
    rows = conn.execute(
        "SELECT date, AVG(change_pct) FROM stock_daily_meta_qfq "
        "WHERE date>=? AND (code LIKE '60%' OR code LIKE '00%' OR code LIKE '30%') "
        "GROUP BY date ORDER BY date", (d0.replace("-", ""),)).fetchall()
    conn.close()
    return {f"{d[:4]}-{d[4:6]}-{d[6:]}": r for d, r in rows if r is not None}


def next_ret_stats(rows: list, ew: dict) -> dict:
    dates_ew = sorted(ew)
    nxt = {d: dates_ew[i + 1] for i, d in enumerate(dates_ew[:-1])}
    out = defaultdict(list)
    for r in rows:
        if r["date"] in ew and nxt.get(r["date"]) is not None:
            out[r["stage"]].append(ew[nxt[r["date"]]])
    return out


def brief(stats: dict) -> str:
    parts = []
    for s in ("分歧", "退潮", "修复", "狂热", "冰点"):
        v = stats.get(s, [])
        if v:
            parts.append(f"{s}:均值{sum(v) / len(v):+.2f}%(n={len(v)})")
    return " | ".join(parts)


def main() -> int:
    preg = json.loads(PREREG.read_text(encoding="utf-8"))
    core = json.dumps({"t": preg["frozen_thresholds_v2"], "c": preg["pass_criteria"],
                       "s": preg["sample_split"]}, ensure_ascii=False, sort_keys=True)
    if hashlib.sha256(core.encode("utf-8")).hexdigest()[:8] != preg.get("fingerprint"):
        print("[FAIL] prereg 指纹不符（文件被改动）", file=sys.stderr)
        return 3
    for needle in ("0.42", "0.24", ">= 2"):
        if needle not in preg["frozen_thresholds_v2"]["退潮"] + preg["frozen_thresholds_v2"]["狂热"]:
            print(f"[FAIL] prereg 冻结串缺少关键阈值 {needle}", file=sys.stderr)
            return 3

    rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows.sort(key=lambda r: r["date"])
    ew = load_ew(rows[0]["date"])
    rows = [r for r in rows if r["date"] in ew]
    mm = re.search(r"前\s*(\d+)\s*日", preg["sample_split"]["in_sample"])
    if not mm:
        print("[FAIL] prereg in_sample 缺'前 N 日'切分描述", file=sys.stderr)
        return 3
    n_in = int(mm.group(1))
    split_in, split_out = rows[:n_in], rows[n_in:]
    print(f"prereg {preg['id']} 指纹 {preg['fingerprint']} | 台账 {len(rows)} 日 "
          f"样本内 {len(split_in)} / 样本外 {len(split_out)}")

    # v2 重分类（不写台账，纯离线）
    for r in rows:
        r["stage_v2"] = v2_classify(r.get("metrics") or {})[0]

    print("\n【P1 分布均衡（全样本）】")
    verdict_p1 = True
    for tag in ("stage", "stage_v2"):
        dist = Counter(r[tag] for r in rows)
        n = len(rows)
        extreme = dist.get("狂热", 0) + dist.get("冰点", 0)
        div_pct = dist.get("分歧", 0) / n
        ok = extreme >= 5 and div_pct <= 0.45
        if tag == "stage_v2":
            verdict_p1 = ok
        print(f"  {tag}: 狂热{dist.get('狂热', 0)}+冰点{dist.get('冰点', 0)}={extreme}日, "
              f"分歧{dist.get('分歧', 0)}({div_pct:.0%}) -> {'PASS' if ok else 'FAIL'}")

    print("\n【P2 方向保持（样本内）】")
    v1_in = next_ret_stats(split_in, ew)
    v2_in = next_ret_stats([{**r, "stage": r["stage_v2"]} for r in split_in], ew)
    print(f"  v1: {brief(v1_in)}")
    print(f"  v2: {brief(v2_in)}")
    ok2 = (v2_in.get("分歧") and v2_in.get("退潮")
           and sum(v2_in["分歧"]) / len(v2_in["分歧"]) > sum(v2_in["退潮"]) / len(v2_in["退潮"]))
    print(f"  P2 -> {'PASS' if ok2 else 'FAIL'}")

    print("\n【P3 样本外确认（最后50日，只跑一次）】")
    v1_out = next_ret_stats(split_out, ew)
    v2_out = next_ret_stats([{**r, "stage": r["stage_v2"]} for r in split_out], ew)
    print(f"  v2: {brief(v2_out)}")
    ok3 = bool(v2_out.get("分歧") and v2_out.get("退潮")
               and sum(v2_out["分歧"]) / len(v2_out["分歧"]) > sum(v2_out["退潮"]) / len(v2_out["退潮"]))
    print(f"  P3 -> {'PASS' if ok3 else 'FAIL'}")

    verdict = "v2过闸候选（仍影子，接线须主人裁决）" if (verdict_p1 and ok2 and ok3) \
        else "不过闸：v1维持观察级，禁止就地调参（第二候选走新prereg）"
    print(f"\n【VERDICT】{verdict}")

    preg["out_of_sample_results"] = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "v2_next_ret_brief": brief(v2_out),
        "P1": "PASS" if verdict_p1 else "FAIL", "P2": "PASS" if ok2 else "FAIL",
        "P3": "PASS" if ok3 else "FAIL", "verdict": verdict,
    }
    PREREG.write_text(json.dumps(preg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 样本外结果已存档 {PREREG.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
