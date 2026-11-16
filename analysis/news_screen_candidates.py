# -*- coding: utf-8 -*-
"""news_screen_candidates.py — 消息面主闸(选股端, 主人令20261102「雷票为什么被选上」根因修复)。
工作流: 会话用 cls-news MCP 逐票查新闻 → 把每票判定写入 verdicts JSON → 本工具合并判定,
输出剔除/保留/观察清单+报告。判定规则(硬雷=剔除):
  业绩暴雷(单季净利降>50%或转亏) | 立案调查 | 退市风险 | 重大监管处罚 |
  停牌核查 | 大额诉讼(>净资产10%) | 高管落马/被查
软雷=观察(减持/质押/旧闻疑云), 不剔除只标注。
用法: python analysis/news_screen_candidates.py --file <候选JSON> --verdicts <判定JSON> --out <报告JSON>"""
import argparse
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"

ap = argparse.ArgumentParser()
ap.add_argument("--file", required=True, help="候选JSON(计划格式)")
ap.add_argument("--verdicts", required=True, help="会话MCP查新闻后的逐票判定JSON: "
               '{"code": {"verdict": "hard|soft|pass", "evidence": "..."}}')
ap.add_argument("--out", default="")
args = ap.parse_args()

IN_PATH = os.path.realpath(args.file)
VF_PATH = os.path.realpath(args.verdicts)
assert IN_PATH.startswith(ROOT) and VF_PATH.startswith(ROOT), "路径越界"

HARD = {"hard"}
SOFT = {"soft"}

rows = json.load(open(IN_PATH, encoding="utf-8"))
vd = json.load(open(VF_PATH, encoding="utf-8"))

removed, keep, watch, uncheck = [], [], [], []
for p in rows:
    c = p["code"]
    v = vd.get(c)
    if v is None:
        uncheck.append({"code": c, "name": p.get("name", "")})
        continue
    if v.get("verdict") in HARD:
        removed.append({"code": c, "name": p.get("name", ""),
                        "evidence": v.get("evidence", "")})
    elif v.get("verdict") in SOFT:
        watch.append({"code": c, "name": p.get("name", ""),
                      "evidence": v.get("evidence", "")})
        keep.append(p)
    else:
        keep.append(p)

print("=" * 70)
print("消息面主闸结果: 剔除%d | 观察%d | 放行%d | 未查%d" % (
    len(removed), len(watch), len(keep), len(uncheck)))
if removed:
    print("-- 剔除(硬雷) --")
    for r in removed:
        print(" ", r["code"], r["name"], "|", r["evidence"][:80])
if watch:
    print("-- 观察(软雷, 保留+标注) --")
    for r in watch:
        print(" ", r["code"], r["name"], "|", r["evidence"][:80])
if uncheck:
    print("-- 未查(须补查后方可入计划) --")
    for r in uncheck:
        print(" ", r["code"], r["name"])
if args.out:
    outp = os.path.realpath(args.out)
    assert outp.startswith(ROOT), "输出路径越界"
    json.dump({"removed": removed, "watch": watch, "keep": keep, "uncheck": uncheck},
              open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("报告落盘:", args.out)
