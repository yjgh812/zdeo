# -*- coding: utf-8 -*-
"""
test_constitution_mechanism.py — 宪法机制化三件落地回归测试（2026-08-23）
=====================================================================
覆盖：
  T1 正常：宪法文件齐全 → check_constitution() == OK
  T2 故障注入：缺 1 个文件 → == ERROR 且 desc 含「缺失」
  T3 边界：文件存在但内容无关 → 仍 OK（只查存在性，不误判内容）
  T4 协议一致性：protocol.md 含 type=constitution + B1-B6；m7.json 合法且含宪法职责信
  T5 端到端：health_check_daily.py --json 的 constitution.status == OK 且含 3 文件

隔离说明：T1-T3 用 tempfile 临时目录 + monkeypatch 模块 BASE，不动生产文件；
T5 跑真实巡检（含闸门冒烟，约 1-3 分钟）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import importlib.util

BASE = r"D:\zcodesj"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_constitution_dir(files):
    d = tempfile.mkdtemp(prefix="constitution_test_")
    for fn in files:
        with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
            f.write("# test fixture\n")
    return d


def main():
    hc = load_module("health_check_daily", os.path.join(BASE, "health_check_daily.py"))
    fails = []
    tmp_dirs = []

    try:
        # T1 齐全
        d1 = make_constitution_dir(["AGENTS.md", "PROJECT_BOUNDARY.md", "CONSTITUTION_SELF_CHECK.md"])
        tmp_dirs.append(d1)
        hc.BASE = d1
        st, desc = hc.check_constitution()
        ok = (st == "OK" and len(desc) == 3)
        print("T1 齐全->OK     :", "PASS" if ok else "FAIL", st, desc)
        if not ok:
            fails.append("T1")

        # T2 缺文件
        d2 = make_constitution_dir(["AGENTS.md"])
        tmp_dirs.append(d2)
        hc.BASE = d2
        st, desc = hc.check_constitution()
        ok = (st == "ERROR" and any("缺失" in x for x in desc))
        print("T2 缺文件->ERROR:", "PASS" if ok else "FAIL", st, desc)
        if not ok:
            fails.append("T2")

        # T3 内容无关仍 OK（只查存在性，不扫描内容）
        d3 = make_constitution_dir(["AGENTS.md", "PROJECT_BOUNDARY.md", "CONSTITUTION_SELF_CHECK.md"])
        tmp_dirs.append(d3)
        hc.BASE = d3
        st, desc = hc.check_constitution()
        ok = (st == "OK")
        print("T3 内容无关->OK  :", "PASS" if ok else "FAIL", st)
        if not ok:
            fails.append("T3")
    finally:
        for d in tmp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    # T4 协议一致性
    proto_p = os.path.join(BASE, ".agent-teams", "team_20260816_221708", "protocol.md")
    proto = open(proto_p, encoding="utf-8").read()
    ok = ("type=constitution" in proto) and ("B1-B6" in proto)
    m7 = json.load(open(os.path.join(BASE, ".agent-teams", "team_20260816_221708", "mailbox", "m7.json"), encoding="utf-8"))
    ok = ok and any("宪法" in m["body"] for m in m7["messages"])
    print("T4 protocol/m7   :", "PASS" if ok else "FAIL", "type=constitution:", "type=constitution" in proto, "| m7职责信:", any("宪法" in m["body"] for m in m7["messages"]))
    if not ok:
        fails.append("T4")

    # T5 端到端 --json（真实巡检）
    r = subprocess.run([sys.executable, os.path.join(BASE, "health_check_daily.py"), "--json"],
                       capture_output=True, text=True, timeout=300, encoding="utf-8", errors="replace")
    d5 = json.loads(r.stdout)
    con = d5.get("constitution", {})
    ok = (con.get("status") == "OK") and (len(con.get("files", [])) == 3)
    print("T5 端到端--json  :", "PASS" if ok else "FAIL", json.dumps(con, ensure_ascii=False))
    if not ok:
        fails.append("T5")

    print("")
    print("RESULT:", "ALL PASS" if not fails else "FAILED: " + ",".join(fails))
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
