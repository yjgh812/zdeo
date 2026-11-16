# -*- coding: utf-8 -*-
"""
strategy_decay_monitor.py — 策略级失效检测 (2026-08-15 M5 补缺)
=============================================================
背景: factor_decay_monitor 只监控 6 个因子的滚动 IC(因子级);策略/信号源级
失效(胜率漂移、负期望)只能靠月度人工复盘发现。本脚本按信号源滚动体检。

数据源: attribution.db verification_trades(strategy_id + T+1/T+3/T+5 前瞻收益,
由 feed_feedback_tables.py 每日收盘后刷新)。T+3 为主要口径。

判定(对齐屠龙标准 v1.0 + 验证体系):
  - A级门槛: 闭合样本≥60 + 胜率>55% + 均值>0
  - 疑似负期望: 胜率<45% 且 均值<0 且 样本≥20 → 建议退役
  - 衰减预警: 近60笔胜率 − 全史胜率 ≤ −15pp(近60样本≥20)
  - 样本<60: 标注"样本不足,继续观察"

用法:
  python strategy_decay_monitor.py            # 全量+报告(md+json)
  python strategy_decay_monitor.py --json     # JSON 摘要
退出码: 0=健康 1=有警告(样本不足/未达A级) 2=有失效/衰减预警(自动 notify)
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

BASE = "D:/zcodesj"
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
REPORTS = os.path.join(BASE, "reports")

A_MIN_N, A_WIN, A_MEAN = 60, 0.55, 0.0
NEG_N, NEG_WIN, NEG_MEAN = 20, 0.45, 0.0
DECAY_WIN_N, DECAY_DROP = 20, 0.15


def load():
    con = sqlite3.connect(ATTR_DB)
    try:
        rows = con.execute(
            "SELECT trade_date, stock_code, stock_name, strategy_id, actual_ret_t1, "
            "actual_ret_t3, actual_ret_t5 FROM verification_trades "
            "WHERE strategy_id IS NOT NULL ORDER BY trade_date").fetchall()
    finally:
        con.close()
    return rows


def stats(samples):
    """samples: [(trade_date, ret_t3), ...] → {n, closed, win_rate, mean, recent_win, recent_n, diff}
    8/16全量巡检P1修复：closed=0（如仅含未闭合样本的新信号源）时也返回全键，
    否则main()表格渲染直接KeyError崩溃。"""
    n = len(samples)
    closed = [r for r in samples if r[1] is not None]
    cn = len(closed)
    if cn == 0:
        return {"n": n, "closed": 0, "win_rate": None, "mean": None,
                "recent_n": 0, "recent_win": None, "diff": None}
    wins = [r for r in closed if r[1] > 0]
    win_rate = len(wins) / cn
    mean = sum(r[1] for r in closed) / cn
    recent = [r for r in closed[-60:]]
    rn = len(recent)
    recent_win = sum(1 for r in recent if r[1] > 0) / rn if rn else None
    return {
        "n": n, "closed": cn, "win_rate": win_rate, "mean": mean,
        "recent_n": rn, "recent_win": recent_win,
        "diff": (recent_win - win_rate) if (rn >= DECAY_WIN_N and recent_win is not None) else None,
    }


def judge(s):
    level, notes = "OK", []
    if s["closed"] < A_MIN_N:
        notes.append("样本不足(闭合%d<60),继续观察" % s["closed"])
        level = "WARN" if s["closed"] < NEG_N else level
    if s["closed"] >= NEG_N and s["win_rate"] < NEG_WIN and s["mean"] < NEG_MEAN:
        notes.append("⚠️疑似负期望(胜率%.0f%%<45%% 均值%.2f%%<0),建议考虑退役" % (s["win_rate"] * 100, s["mean"] * 100))
        level = "FAIL"
    if s.get("diff") is not None and s["diff"] <= -DECAY_DROP:
        notes.append("🟡衰减:近%d笔胜率%.0f%% 较全史%.0f%% 下降%.0fpp" % (
            s["recent_n"], s["recent_win"] * 100, s["win_rate"] * 100, -s["diff"] * 100))
        level = "FAIL" if level != "FAIL" else level
    if s["closed"] >= A_MIN_N and s["win_rate"] > A_WIN and s["mean"] > A_MEAN:
        notes.append("✅达A级(样本%d 胜率%.0f%% 均值%.2f%%)" % (s["closed"], s["win_rate"] * 100, s["mean"] * 100))
    return level, notes


def main():
    only_json = "--json" in sys.argv
    rows = load()
    by_strat = {}
    for d, code, name, sid, r1, r3, r5 in rows:
        by_strat.setdefault(sid, []).append((d, r3))

    out = {}
    warn_flags, fail_flags = [], []
    for sid in sorted(by_strat):
        s = stats(by_strat[sid])
        s["mean_pct"] = round(s.get("mean", 0) * 100, 2) if s.get("mean") is not None else None
        s["win_rate_pct"] = round(s.get("win_rate", 0) * 100, 1) if s.get("win_rate") is not None else None
        level, notes = judge(s)
        if level == "FAIL":
            fail_flags.append(sid)
        elif level == "WARN":
            warn_flags.append(sid)
        s["level"] = level
        s["notes"] = notes
        out[sid] = s

    summary = {
        "timestamp": datetime.now().isoformat(),
        "strategies": out,
        "fail": fail_flags,
        "warn": warn_flags,
    }
    if only_json:
        print(json.dumps(summary, ensure_ascii=False))
        return 2 if fail_flags else (1 if warn_flags else 0)

    md = ["# 策略级失效检测 %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "",
          "| 信号源 | 样本/闭合 | T+3胜率 | T+3均值 | 近60胜率 | 判定 | 说明 |", "|---|---|---|---|---|---|---|"]
    for sid in sorted(out):
        s = out[sid]
        md.append("| %s | %d/%d | %s | %s | %s | %s | %s |" % (
            sid, s["n"], s["closed"],
            "%.0f%%" % s["win_rate_pct"] if s["win_rate_pct"] is not None else "-",
            "%.2f%%" % s["mean_pct"] if s["mean_pct"] is not None else "-",
            "%.0f%%(%d)" % (s["recent_win"] * 100, s["recent_n"]) if s["recent_win"] is not None else "-",
            s["level"], "; ".join(s["notes"]) if s["notes"] else "-"))
    md.append("")
    md.append("**结论: %s**" % ("有失效/衰减预警" if fail_flags else ("有警告" if warn_flags else "全部健康")))
    body = "\n".join(md)
    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, "strategy_decay_%s.md" % datetime.now().strftime("%Y%m%d")), "w", encoding="utf-8") as f:
        f.write(body)
    with open(os.path.join(REPORTS, "strategy_decay_%s.json" % datetime.now().strftime("%Y%m%d")), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(body)

    if fail_flags:
        try:
            subprocess.run([sys.executable, os.path.join(BASE, "notify.py"), "send",
                            "--title", "策略级失效预警",
                            "--content", "信号源 %s 疑似失效/衰减,详见 reports/strategy_decay_%s.md" % (
                                ",".join(fail_flags), datetime.now().strftime("%Y%m%d")),
                            "--level", "error"], timeout=60)
        except Exception:
            pass
        return 2
    return 1 if warn_flags else 0


if __name__ == "__main__":
    sys.exit(main())
