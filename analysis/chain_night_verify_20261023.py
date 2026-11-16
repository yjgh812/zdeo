#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chain_night_verify_20261023.py — 晚间生产链完成度轮询+链后验证(只读, 零交易权)
==============================================================================
背景: 10/23 ~20:55 晚间链已启动(链锁 pid=39400), 本件轮询等待链尾产物落地后
  即刻做链后验证(不等明晨): 9表推进/limit_down缺口维持闭合/signal_20261023产出。
判定: 全过=今晚链闭合确认; 超时=链仍在跑(报告部分状态, 明晨自然A/B继续)。
零交易权; 唯一写动作=验证报告落盘。
"""
import json
import os
import sqlite3
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # analysis/ 的上级=仓库根
OUT = os.path.join(ROOT, "reports", "chain_night_verify_20261023.md")
SIGNAL = os.path.join(ROOT, "reports", "daily_signals", "signal_20261023.csv")
QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
ATTR = os.path.join(ROOT, "attribution", "attribution.db")
POLL_SEC = 60
TIMEOUT_MIN = 45


def table_max(db_uri, sql):
    try:
        con = sqlite3.connect(db_uri, uri=True) if "mode=ro" in db_uri else sqlite3.connect(db_uri)
        r = con.execute(sql).fetchone()
        con.close()
        return r
    except Exception as e:
        return ("ERR", str(e)[:40])


def main():
    start = time.time()
    # 阶段1: 轮询链尾产物(signal_20261023 为链内信号合并步骤产物)
    signaled = False
    while (time.time() - start) < TIMEOUT_MIN * 60:
        if os.path.exists(SIGNAL):
            signaled = True
            break
        time.sleep(POLL_SEC)
    elapsed = round((time.time() - start) / 60, 1)

    # 阶段2: 链后验证(等链尾缓冲90秒让尾部落库)
    time.sleep(90)
    checks = {}
    q = sqlite3.connect(QFQ, uri=True, timeout=30)
    checks["qfq_max"] = q.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    q.close()
    a = None
    for attempt in range(3):
        try:
            a = sqlite3.connect(ATTR, timeout=30)
            checks["limit_down_max"] = a.execute(
                "SELECT MAX(trade_date) FROM limit_down_risks").fetchone()[0]
            checks["limit_down_cnt"] = a.execute(
                "SELECT COUNT(*) FROM limit_down_risks").fetchone()[0]
            checks["limit_up_max"] = a.execute(
                "SELECT MAX(trade_date) FROM limit_up_patterns").fetchone()[0]
            a.close()
            break
        except sqlite3.OperationalError as e:
            if a is not None:
                try:
                    a.close()
                except Exception:
                    pass
                a = None
            if attempt == 2:
                checks["limit_down_max"] = f"ERR {type(e).__name__}"
                checks["limit_down_cnt"] = "ERR"
                checks["limit_up_max"] = "ERR"
            else:
                time.sleep(15)
    checks["signal_20261023"] = os.path.exists(SIGNAL)
    checks["signal_rows"] = sum(1 for _ in open(SIGNAL, encoding="utf-8-sig")) if checks["signal_20261023"] else 0

    verdicts = {
        "链完成(signal_20261023落地)": signaled,
        "qfq推进至20261023": str(checks["qfq_max"]) >= "20261023",
        "limit_down 10/22缺口维持闭合": str(checks["limit_down_max"]) >= "20261022",
        "signal行数>0": checks["signal_rows"] > 0,
    }
    n_pass = sum(1 for v in verdicts.values() if v)

    md = ["# 晚间生产链 完成度验证报告(2026-10-23)", "",
          f"- 轮询时长: {elapsed}分钟 | signal_20261023落地: {'✅' if signaled else '⏳超时(链仍在跑, 明晨自然A/B继续)'}",
          f"- qfq MAX: {checks['qfq_max']}",
          f"- limit_down: MAX={checks['limit_down_max']} / {checks['limit_down_cnt']}行 (10/22缺口已由本流手动预跑闭合)",
          f"- limit_up_patterns MAX: {checks['limit_up_max']}",
          f"- signal_20261023: {'✅' if checks['signal_20261023'] else '❌'} {checks['signal_rows']}行",
          "", "## 判定", ""]
    for k, v in verdicts.items():
        md.append(f"- {'✅' if v else '⏳/❌'} {k}: {v}")
    md += ["", f"- **n_pass={n_pass}/4**; 全过=今晚链闭合确认; signal未落地=链仍在跑(明晨自然A/B继续)",
           "- 零交易权纯呈现", ""]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(json.dumps({"verdicts": verdicts, "checks": checks, "elapsed_min": elapsed},
                     ensure_ascii=False, indent=1))
    print(f"[written] {OUT}")


if __name__ == "__main__":
    main()
