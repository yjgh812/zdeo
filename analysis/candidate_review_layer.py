# -*- coding: utf-8 -*-
"""candidate_review_layer.py — 选股候选×复盘结果对齐标注器(零交易权, 只读输入)。
主人令20261102「选股方法要连通复盘结果和消息面情况」之复盘半边:
  输入=计划/候选JSON(code,name[,source])
  复盘真源=data/breakout_mainline_latest.json(主线判别器: 当日强方向行业)
          + data/direction_census_*.json(方向普查: 市场广度UP/DOWN→相位)
  输出=逐票[行业|是否主线|市场相位]标注 + 汇总, 报告落盘JSON。
用法: python candidate_review_layer.py --file <候选JSON> [--out <报告JSON>]"""
import argparse
import glob
import io
import json
import os
import sqlite3
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"

ap = argparse.ArgumentParser()
ap.add_argument("--file", required=True)
ap.add_argument("--out", default="")
args = ap.parse_args()

IN_PATH = os.path.realpath(args.file)
assert IN_PATH == args.file or IN_PATH.startswith(ROOT), "输入路径越界"

# 1 主线真源(当日强方向行业)
ml = json.load(open(os.path.join(ROOT, "data", "breakout_mainline_latest.json"), encoding="utf-8"))
from collections import defaultdict
ind_ret = {}
for c in ml.get("candidates_prefilter", []):
    if c.get("is_mainline"):
        ind_ret[c["l1"]] = c["ind_ret"]
mainline = {k for k, v in ind_ret.items() if v > 0}
ml_date = ml.get("asof")

# 2 市场相位(方向普查最新一日)
cens = sorted(glob.glob(os.path.join(ROOT, "data", "direction_census_*.json")))
cens = [p for p in cens if os.path.basename(p) >= "direction_census_20261030"]
phase = "N/A"
if cens:
    dc = json.load(open(cens[-1], encoding="utf-8"))
    cnt = dc.get("counts", {})
    up, dn = cnt.get("UP", 0), cnt.get("DOWN", 0)
    tot = up + dn + cnt.get("BASE", 0)
    ratio = up / max(1, up + dn)
    if ratio >= 0.6:
        phase = "活跃(涨/跌=%.2f)" % ratio
    elif ratio >= 0.35:
        phase = "中性(涨/跌=%.2f)" % ratio
    else:
        phase = "防御(涨/跌=%.2f)" % ratio

# 3 行业映射
con = sqlite3.connect("file:" + os.path.join(ROOT, "data", "industry_group_map_snapshot.db") + "?mode=ro", uri=True)
ind = {str(k)[-6:]: v for k, v in con.execute("select code,l1 from l1_member")}
con.close()

# 4 逐票标注
rows = json.load(open(IN_PATH, encoding="utf-8"))
out_rows, n_main = [], 0
for p in rows:
    l1 = ind.get(p["code"], "?")
    hit = l1 in mainline
    n_main += hit
    out_rows.append({"code": p["code"], "name": p.get("name", ""), "source": p.get("source", ""),
                     "industry": l1, "in_mainline": hit})
for r in out_rows:
    print("%s %-10s [%s] %s" % (r["code"], r["name"], r["industry"], "✓主线" if r["in_mainline"] else "△非主线"))
print("-" * 60)
print("主线日=%s | 市场相位=%s | 主线行业=%s" % (ml_date, phase, sorted(mainline)))
print("对齐率: %d/%d = %.0f%%" % (n_main, len(out_rows), 100.0 * n_main / max(1, len(out_rows))))
if args.out:
    outp = os.path.realpath(args.out)
    assert outp.startswith(ROOT), "输出路径越界"
    json.dump({"review_date": ml_date, "phase": phase, "mainline": sorted(mainline),
               "aligned": n_main, "total": len(out_rows), "rows": out_rows},
              open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("报告落盘:", args.out)
