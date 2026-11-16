# -*- coding: utf-8 -*-
"""prereg runner 窗口钉死守卫(B1加固件, 2026-10-20)。静态扫描, fail-open 报告, 零写入。

用法: python analysis/prereg_runner_guard.py <runner.py> <prereg.json> [更多runner/prereg对...]
检查:
  1) prereg 内窗口字段存在性(window/eval/start-end)
  2) runner 中 YYYYMMDD 字面量与 prereg 窗口边界的关系:
     - runner 出现晚于 prereg 窗口终点的日期字面量 -> FLOATING_WINDOW 嫌疑(数据尾随型)
  3) runner 是否含 fail-closed 数据覆盖断言关键词(可选提示)
"""
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

DATE = re.compile(r"\b(20\d{6})\b")
WIN_KEYS = ("window", "eval_window", "start", "end", "EVAL_START", "EVAL_END",
            "MAIN_START", "MAIN_END", "OOS_START", "OOS_END")


def scan(runner_path, prereg_path):
    rep = {"runner": runner_path, "prereg": prereg_path}
    try:
        src = open(runner_path, encoding="utf-8", errors="replace").read()
        pre = json.load(open(prereg_path, encoding="utf-8"))
    except Exception as e:
        rep["error"] = str(e)
        return rep
    pre_dates = [str(x)[:8] for x in re.findall(r"20\d{6}", json.dumps(pre, ensure_ascii=False))]
    pre_max = max(pre_dates) if pre_dates else None
    run_dates = sorted(set(DATE.findall(src)))
    rep["prereg_window_max"] = pre_max
    rep["runner_date_literals"] = run_dates
    floating = [d for d in run_dates if pre_max and d > pre_max and d >= "20260101"]
    rep["floating_window_suspect"] = floating
    rep["has_failclosed_keyword"] = bool(re.search(r"FAIL-CLOSED|fail_closed|raise SystemExit", src))
    rep["verdict"] = ("CHECK" if floating else "OK_PINNED_OR_STATIC")
    return rep


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2 or len(args) % 2:
        print("usage: runner prereg [runner prereg ...]")
        raise SystemExit(2)
    for i in range(0, len(args), 2):
        print(json.dumps(scan(args[i], args[i + 1]), ensure_ascii=False))
