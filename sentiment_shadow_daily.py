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
sentiment_shadow_daily.py — 情绪周期状态机每日影子喂料探针（M1'，自省第二期硬排期件）

定位（自省第一期 M1 / 第二期 M1'，两期未动后于 2026-09-15 落地）：
  纯本地喂料 wrapper：从本地库取情绪四元组 -> 按 sentiment_cycle_state.py 喂料合同
  写临时 JSON -> 调用状态机 -> 台账 + 每日影子报告。
  只读观测零接线：不进信号、不拦交易、不改任何生产链（影子期判据见 prereg 待主人裁决）。

数据源（全部本地只读，零 MCP 依赖，规避 hook/平台层风险）：
  D:/zcodesj/attribution/attribution.db  limit_up_patterns（涨停家数/最高板/二板以上/seal_status炸板次数）
                                          limit_down_risks（跌停家数）
  D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db  stock_daily_meta_qfq MAX(date)=目标日 T

口径差异如实标注（相对原 MCP 喂料合同）：
  limit_break_count 本地口径=当日封板记录 seal_status 中"N炸"次数合计（炸板事件数），
  非 hithink"触板未封家数"，为近似值；source 标注 local_db 供影子期评估时区分。

用法：python sentiment_shadow_daily.py [--date YYYYMMDD]（默认 qfq MAX(date)，同日重跑幂等覆盖）
退出码：0 成功 / 2 数据缺失 / 3 状态机失败
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\zcodesj")
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STATE_SCRIPT = ROOT / "sentiment_cycle_state.py"
TMP_JSON = ROOT / "data" / "_sentiment_shadow_input.json"
LEDGER = ROOT / "data" / "sentiment_cycle_daily.jsonl"


def ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def prev_trade_date(con_qfq, t: str) -> str:
    row = con_qfq.execute(
        "SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date<?", (t,)).fetchone()
    return str(row[0]) if row and row[0] else ""


def collect(t: str) -> dict:
    con = ro(ATTR_DB)
    qc = ro(QFQ_DB)
    try:
        tp = prev_trade_date(qc, t)
        if not tp:
            raise RuntimeError(f"T={t} 无前一交易日")

        def day_stats(d):
            n, mx = con.execute(
                "SELECT COUNT(*),MAX(limit_count) FROM limit_up_patterns WHERE trade_date=?",
                (int(d),)).fetchone()
            ge2 = con.execute(
                "SELECT COUNT(*) FROM limit_up_patterns WHERE trade_date=? AND limit_count>=2",
                (int(d),)).fetchone()[0]
            seals = con.execute(
                "SELECT seal_status FROM limit_up_patterns WHERE trade_date=?",
                (int(d),)).fetchall()
            brk = 0
            for (s,) in seals:
                if s:
                    import re
                    m = re.search(r"(\d+)炸", str(s))
                    if m:
                        brk += int(m.group(1))
            return {"n": n or 0, "max_board": mx or 0, "ge2": ge2, "breaks": brk}

        cur, prev = day_stats(t), day_stats(tp)
        dl = con.execute(
            "SELECT COUNT(*) FROM limit_down_risks WHERE trade_date=?",
            (int(t),)).fetchone()[0]
    finally:
        con.close()
        qc.close()
    if not cur["n"]:
        raise RuntimeError(f"T={t} limit_up_patterns 无记录（库未更新?）")
    return {
        "date": f"{t[:4]}-{t[4:6]}-{t[6:]}",
        "limit_up_count": cur["n"],
        "limit_break_count": cur["breaks"],
        "max_board": cur["max_board"],
        "prev_max_board": prev["max_board"],
        "promoted_count": None,
        "prev_board_count": prev["ge2"],
        "board_ge2_today": cur["ge2"],
        "down_limit_count": dl,
        "amount": None,
        "source": "local_db(limit_up_patterns/limit_down_risks) break_count=近似口径",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="YYYYMMDD，默认 qfq MAX(date)")
    a = ap.parse_args()
    qc = ro(QFQ_DB)
    t = a.date or str(qc.execute(
        "SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
    qc.close()

    feed = collect(t)
    TMP_JSON.write_text(json.dumps(feed, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    r = subprocess.run([sys.executable, "-u", str(STATE_SCRIPT),
                        "--input", str(TMP_JSON)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        print(f"[sentiment_shadow] state machine rc={r.returncode}")
        return 3

    # 台账尾部读回当日判定 + 近 20 日序列
    rows = [json.loads(x) for x in
            LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]
    today = [x for x in rows if x.get("date") == feed["date"]]
    stage = (today[-1].get("stage") if today else None) or "N/A"
    recent = [(x.get("date"), x.get("stage")) for x in rows[-20:]]

    out = ROOT / "reports" / f"sentiment_shadow_{t}.md"
    lines = [
        f"# 情绪状态影子探针 {t}",
        "",
        f"- 生成: {datetime.now().isoformat(timespec='seconds')} | 只读观测零接线（M1'）",
        f"- 四元组(T={t}): 涨停 {feed['limit_up_count']} / 最高板 {feed['max_board']}"
        f"(昨 {feed['prev_max_board']}) / 炸板次数(近似口径) {feed['limit_break_count']}"
        f" / 跌停 {feed['down_limit_count']}；二板以上 今 {feed['board_ge2_today']}"
        f" vs 昨 {feed['prev_board_count']}",
        f"- **当日状态判定: {stage}**",
        f"- 近 20 日序列: {recent}",
        "- 口径注: limit_break_count=seal_status\"N炸\"次数合计（近似），"
        "非 hithink 触板未封家数；影子期评估时按 source=local_db 区分。",
        "- 铁律: 本探针只读不入闸；状态→交易语义接线须 prereg+主人裁决。",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[sentiment_shadow] T={t} stage={stage} -> {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
