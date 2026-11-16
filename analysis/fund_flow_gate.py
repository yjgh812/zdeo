# -*- coding: utf-8 -*-
"""fund_flow_gate.py — 资金流现状标记器(WARN模式, 主人令20261102「资金跑了股价下跌」根因修复)。
选股端标准工序: 候选池生成后必跑, 输出"资金流预警"票清单(不自动拦——冰山冷热反例:
10/30双窗流出但次日+8.6%, 资金流作信号不作判决, 预警呈主人裁决)。
规则(20261102校准): 任一窗口命中 → WARN标记
  当日主力净流出≤-2000万 | 5日≤-1500万 | 20日≤-3000万
用法: python analysis/fund_flow_gate.py --file <候选JSON> [--out <报告JSON>] [--limit N]
东财fflow日K接口, Mimosa加固=ALLOWED_HOST字面量+https+urlencode+secid白名单(verify11_em同构)。"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"

ap = argparse.ArgumentParser()
ap.add_argument("--file", required=True, help="候选JSON(计划格式)")
ap.add_argument("--out", default="")
ap.add_argument("--limit", type=int, default=0, help="只验前N票(调试)")
args = ap.parse_args()

IN_PATH = os.path.realpath(args.file)
assert IN_PATH == args.file or IN_PATH.startswith(ROOT), "输入路径越界"

# ---- 阈值(20261102校准: 上海银行5日-2.1亿/安徽建工20日-7207万/东方嘉盛5日-1453万均命中) ----
T1, T5, T20 = -2000e4, -1500e4, -3000e4
ALLOWED_HOST = "push2his.eastmoney.com"

def secid(code):
    if not re.match(r"^\d{6}$", str(code)):
        raise ValueError("非法代码: %r" % code)
    return ("1." if code[0] == "6" else "0.") + code

def fetch_main_flow(code):
    from urllib.parse import urlencode
    qs = urlencode({"lmt": "25", "klt": "101", "secid": secid(code),
                    "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57"})
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                f"https://{ALLOWED_HOST}/api/qt/stock/fflow/daykline/get?{qs}",
                headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read().decode("utf-8"))
            kl = (d.get("data") or {}).get("klines") or []
            return [(p.split(",")[0], float(p.split(",")[1])) for p in kl]
        except Exception as e:
            last = e
            time.sleep(2.0 * (attempt + 1))
    raise last

rows = json.load(open(IN_PATH, encoding="utf-8"))
todo = rows[: args.limit] if args.limit else rows

warns, passes, errs = [], [], []
for i, p in enumerate(todo):
    c = p["code"]
    try:
        main = fetch_main_flow(c)
        if len(main) < 20:
            errs.append((c, p.get("name", ""), "历史不足20日"))
            continue
        t1 = main[-1][1]
        t5 = sum(v for _, v in main[-5:])
        t20 = sum(v for _, v in main[-20:])
        hits = []
        if t1 <= T1:
            hits.append("当日%+.0f万" % (t1 / 1e4))
        if t5 <= T5:
            hits.append("5日%+.0f万" % (t5 / 1e4))
        if t20 <= T20:
            hits.append("20日%+.0f万" % (t20 / 1e4))
        rec = {"code": c, "name": p.get("name", ""), "d1": round(t1 / 1e4),
               "d5": round(t5 / 1e4), "d20": round(t20 / 1e4), "hits": hits}
        if hits:
            warns.append(rec)
            print("WARN", c, p.get("name", ""), "|", "; ".join(hits), "(资金流预警, 呈主人裁决不自动拦)")
        else:
            passes.append(rec)
            print("PASS", c, p.get("name", ""), "| 当日%+.0f万 5日%+.0f万 20日%+.0f万" % (t1 / 1e4, t5 / 1e4, t20 / 1e4))
    except Exception as e:
        errs.append((c, p.get("name", ""), type(e).__name__))
        print("ERR ", c, p.get("name", ""), type(e).__name__, "(限流则稍后重跑)")
    time.sleep(1.5)

print("-" * 70)
print("资金流标记完成: 预警%d / 正常%d / 错误%d" % (len(warns), len(passes), len(errs)))
if args.out:
    outp = os.path.realpath(args.out)
    assert outp.startswith(ROOT), "输出路径越界"
    json.dump({"thresholds": {"d1": T1, "d5": T5, "d20": T20},
               "warn": warns, "pass": passes, "err": errs},
              open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("报告落盘:", args.out)
