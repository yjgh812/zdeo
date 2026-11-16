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
sentiment_cycle_backfill.py — 情绪周期台账历史回填（qfq 日线推算口径，影子层）

定位：sentiment_cycle_state.py 的历史供数件。从本地 qfq 主库日线推算每日
  涨停家数/炸板家数/最高连板/二板以上家数/晋级率，复用状态机 classify+upsert
  落台账。台账中已存在的 date（agent 实拉口径）跳过不覆盖（实拉真值优先）。

口径（诚实标注，与 hithink MCP 口径存在已知差异）：
  涨停  = |change_pct - 理论涨幅| < 容差（主板10%容差0.08pp / 创业板20%容差0.15pp；
          qfq 前复权 change_pct，除权日个股存在个别偏差，影子统计可接受）
  炸板  = 盘中触及涨停价（high>=prev_close*(1+比率-0.15%)）且收盘未封
          ≈ hithink「涨停后开板未回封」的上界近似（含开盘未封情况），验收时对齐
  连板  = 连续涨停天数（streak），最高连板/二板以上家数/晋级率由此派生
  范围  = 沪深主板+创业板（剔除 688 科创 / 8·4·92 北交，与屠龙选股域一致）

用法：
  python sentiment_cycle_backfill.py --days 1          # 校准模式（只算最近1日）
  python sentiment_cycle_backfill.py --days 30         # 回填近30交易日
退出码：0 成功；2 qfq 库不可用/数据不足
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sentiment_cycle_state import classify, compute_metrics, upsert_ledger  # noqa: E402

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SOURCE_TAG = "qfq日线推算(涨停=change_pct口径,炸板=触板未封,剔科创北交)"
MAIN_TOL, CHINEXT_TOL = 0.08, 0.15
TOUCH_BUFFER = 0.0015  # 触板判定缓冲（比理论涨停价低0.15%算触及）


def limit_ratio(code: str) -> float:
    if code.startswith(("300", "301")):
        return 0.20
    if code.startswith(("688", "689", "8", "4", "92")):
        return 0.30  # 科创/北交（默认已剔除，防御性保留）
    return 0.10


def main() -> int:
    p = argparse.ArgumentParser(description="情绪台账历史回填（qfq推算）")
    p.add_argument("--days", type=int, default=30, help="回填最近N个交易日")
    args = p.parse_args()

    if not Path(QFQ_DB).exists() or Path(QFQ_DB).stat().st_size == 0:
        print(f"[FAIL] qfq 主库不可用: {QFQ_DB}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(QFQ_DB)
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date DESC LIMIT ?", (args.days + 1,))]
    if len(dates) < 2:
        print("[FAIL] 交易日不足", file=sys.stderr)
        conn.close()
        return 2
    dates = sorted(dates)
    target_dates = dates[1:]  # 首日仅作 prev 基准
    ph = ",".join("?" * len(dates))
    rows = conn.execute(
        f"SELECT code,date,high,close,change_pct FROM stock_daily_meta_qfq WHERE date IN ({ph})",
        dates).fetchall()
    conn.close()
    print(f"[INFO] 载入 {len(rows)} 行, {len(target_dates)} 个目标日 ({target_dates[0]}~{target_dates[-1]})")

    by_date: dict = {}
    for code, date, high, close, chg in rows:
        by_date.setdefault(date, []).append((code, high, close, chg))
    prev_date_map = dict(zip(dates[:-1], dates[1:]))

    # 连板 streak：逐日递推
    streak: dict = {}
    prev_date = dates[0]
    for d in target_dates:
        cur_codes = by_date.get(d, [])
        prev_codes = by_date.get(prev_date, [])
        # 当日涨停集合与昨日涨停集合
        limit_today, touch_today = set(), set()
        for code, high, close, chg in cur_codes:
            r = limit_ratio(code)
            if chg is None:
                continue
            if (r == 0.10 and MAIN_TOL >= abs(chg - 10)) or \
               (r == 0.20 and abs(chg - 20) <= CHINEXT_TOL):
                limit_today.add(code)
        limit_prev = set()
        close_prev = {}
        high_prev = {}
        for code, high, close, chg in prev_codes:
            close_prev[code], high_prev[code] = close, high
            if chg is None:
                continue
            r = limit_ratio(code)
            if (r == 0.10 and abs(chg - 10) <= MAIN_TOL) or \
               (r == 0.20 and abs(chg - 20) <= CHINEXT_TOL):
                limit_prev.add(code)
        # streak 递推
        new_streak = {}
        for code in limit_today:
            new_streak[code] = streak.get(code, 0) + 1 if code in limit_prev else 1
        promoted = len([c for c in limit_today if c in limit_prev])
        # 炸板：今日触及涨停价（相对昨收）但未封
        break_count = 0
        for code, high, close, chg in cur_codes:
            if code in limit_today or code not in close_prev or close_prev[code] in (None, 0):
                continue
            r = limit_ratio(code)
            touch_ref = close_prev[code] * (1 + r - TOUCH_BUFFER)
            if high is not None and high >= touch_ref:
                break_count += 1
        streak = new_streak
        metrics = {
            "limit_up_count": len(limit_today),
            "limit_break_count": break_count,
            "max_board": max(streak.values()) if streak else 0,
            "prev_max_board": None,  # 由前后日关系在 classify 外体现：用前一日 max_board
            "promoted_count": promoted,
            "prev_board_count": len(limit_prev),
            "source": SOURCE_TAG,
        }
        print(f"  [推算] {d} 涨停{metrics['limit_up_count']} 炸板{metrics['limit_break_count']} "
              f"最高板{max(streak.values()) if streak else 0} 再涨停{promoted}/{len(limit_prev)}")
        yield_record = {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                        "source": SOURCE_TAG, "metrics_extra": metrics}
        # prev_max_board：用昨日 streak max（需保存昨日值）
        yield_record["_cur_max"] = max(streak.values()) if streak else 0
        yield_record["_prev_max"] = getattr(main, "_last_max", None)
        main._last_max = yield_record["_cur_max"]
        YIELD_LIST.append(yield_record)
        prev_date = d

    # 二次遍历填 prev_max_board 并分类落账
    ledger_dates = set()
    ledger_path = Path(__file__).resolve().parent / "data" / "sentiment_cycle_daily.jsonl"
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    ledger_dates.add(json.loads(line).get("date"))
                except json.JSONDecodeError:
                    pass

    skipped, written = [], 0
    for i, rec in enumerate(YIELD_LIST):
        if rec["date"] in ledger_dates:
            skipped.append(rec["date"])
            continue
        m = dict(limit_up_count=rec["metrics_extra"]["limit_up_count"],
                 limit_break_count=rec["metrics_extra"]["limit_break_count"],
                 max_board=rec["_cur_max"],
                 prev_max_board=rec["_prev_max"],
                 promoted_count=rec["metrics_extra"]["promoted_count"],
                 prev_board_count=rec["metrics_extra"]["prev_board_count"],
                 down_limit_count=None, amount=None)
        stage, ev = classify(compute_metrics(m))
        record = {"date": rec["date"], "source": SOURCE_TAG, "stage": stage,
                  "evidence": ev, "metrics": compute_metrics(m),
                  "schema_version": "sentiment_cycle_v1_heuristic_unbacktested",
                  "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        total = upsert_ledger(record)
        written += 1
        print(f"  {rec['date']} 涨停{m['limit_up_count']} 炸板{m['limit_break_count']} "
              f"最高板{m['max_board']}(昨{m['prev_max_board']}) 晋级{m['promoted_count']}/"
              f"{m['prev_board_count']} -> 【{stage}】 (台账{total}行)")
    if skipped:
        print(f"[SKIP] 台账已有实拉口径，跳过 {len(skipped)} 日: {skipped}")
    print(f"[OK] 回填 {written} 日")
    return 0


YIELD_LIST: list = []

if __name__ == "__main__":
    raise SystemExit(main())
