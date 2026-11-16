#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""style_ab_track.py 契约测试（2026-09-05）

覆盖：append 正常/防重复/force 幂等替换/非法代码/非法日期/空 original、
show、eval 数值路径(真实DB历史日)/insufficient 分支/future 日跳过计数/fwd<1 拒绝。
运行：python tests/test_style_ab_track.py（全部通过 exit 0；任一失败 exit 1 并打印 FAIL 行）
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

BASE = r"D:\zcodesj"
sys.path.insert(0, BASE)
sys.path.insert(0, str(Path(BASE) / "tests"))

import style_ab_track as m

TMP = Path(tempfile.mkdtemp(prefix="sat_test_"))
PASS, FAIL = [], []


def run(argv):
    sys.argv = ["style_ab_track.py"] + argv
    try:
        rc = m.main()
    except SystemExit as e:
        rc = e.code
    return rc


def case(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name}{' | ' + detail if detail and not cond else ''}")
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" | {detail}" if not cond else ""))


def rows(fp: Path):
    return [json.loads(x) for x in fp.read_text(encoding="utf-8").splitlines() if x.strip()]


F1 = TMP / "l1.jsonl"
# T1 append 正常（无 init 行的空文件）
rc = run(["append", "--original", "sh600000,sz000001", "--filtered", "600000",
          "--note", "t1", "--date", "2026-08-20", "--file", str(F1)])
case("T1 append 正常 rc=0", rc == 0)
case("T1 行内容", len(rows(F1)) == 1 and rows(F1)[0]["original"] == ["000001", "600000"]
     and rows(F1)[0]["filtered"] == ["600000"])
# T2 防重复
rc = run(["append", "--original", "600519", "--date", "2026-08-20", "--file", str(F1)])
case("T2 同日无 force 拒绝 rc=2", rc == 2 and len(rows(F1)) == 1)
# T3 force 幂等替换
rc = run(["append", "--original", "600519", "--filtered", "600519",
          "--date", "2026-08-20", "--force", "--file", str(F1)])
case("T3 force 替换 rc=0 且仍 1 行", rc == 0 and len(rows(F1)) == 1
     and rows(F1)[0]["original"] == ["600519"])
# T4 非法代码拒收
rc = run(["append", "--original", "ABCD,600000", "--date", "2026-08-21", "--file", str(F1)])
case("T4 非法代码拒收 rc=2", rc == 2 and len(rows(F1)) == 1)
# T5 非法日期拒收
rc = run(["append", "--original", "600000", "--date", "2026/09/05", "--file", str(F1)])
case("T5 非法日期拒收 rc=2", rc == 2 and len(rows(F1)) == 1)
# T6 空 original 拒收
rc = run(["append", "--original", " ,", "--date", "2026-08-21", "--file", str(F1)])
case("T6 空 original 拒收 rc=2", rc == 2)
# T7 --fwd<1 拒绝
rc = run(["eval", "--fwd", "0", "--file", str(F1)])
case("T7 fwd<1 拒绝 rc=2", rc == 2)
# T8 eval 数值路径（真实 DB，历史日期 2026-08-20；A=600519 单票，B 含已退市样边 600000/000001）
rc = run(["eval", "--fwd", "5", "--file", str(F1)])
case("T8 eval 数值路径 rc=0", rc == 0)
# T9 future 日跳过计数（动态未来日=今日+30天, 必无后向 bar → skipped=1; 原硬编码 2026-09-30
# 已成历史日致夹具腐烂, 20261023 修正）
F2 = TMP / "l2.jsonl"
FUTURE = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
run(["append", "--original", "600000", "--date", FUTURE, "--file", str(F2)])
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = run(["eval", "--fwd", "5", "--file", str(F2)])
out = json.loads(buf.getvalue())
case("T9 future 日计入 skipped", rc == 0 and out.get("skipped_days", 0) == 1
     and out["fwd"]["N5"]["B_original"]["n"] == 0)
# T10 insufficient 分支
F3 = TMP / "l3.jsonl"
F3.write_text(json.dumps({"init": True, "note": "x"}, ensure_ascii=False) + "\n", encoding="utf-8")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = run(["eval", "--file", str(F3)])
case("T10 insufficient rc=1", rc == 1 and json.loads(buf.getvalue())["status"] == "insufficient_data")
# T11 无换行符黏行防护
F4 = TMP / "l4.jsonl"
F4.write_text('{"date":"2026-08-19","original":["600000"],"filtered":[],"notes":"x"}', encoding="utf-8")  # 无 \n
rc = run(["append", "--original", "600519", "--date", "2026-08-20", "--file", str(F4)])
case("T11 无换行符黏行防护", rc == 0 and len(rows(F4)) == 2 and rows(F4)[1]["date"] == "2026-08-20")
# T12 show 可用
rc = run(["show", "--file", str(F1)])
case("T12 show rc=0", rc == 0)

print(f"\n===== {len(PASS)} PASS / {len(FAIL)} FAIL =====")
sys.exit(1 if FAIL else 0)
