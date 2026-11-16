#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""check_portrait_permissions.py — 画像产物零交易权 CI 门（2026-10-11 批1' 落地）。

外部审计 PORTRAIT_AUDIT_20261011 §2.3 骨架的修正落地版, 对原骨架三处修正:
  ① 扫描面: 原骨架 gates/**/execution/** 目录不存在(实测, 静默零覆盖) → 改为显式闸门链
     文件清单(实测=根目录两件) + 目录存在时才扫描;
  ② manifest: 原骨架单一静态清单对每日重生成物必日日 FAIL → 改为生成器自写分片 manifest
     (portrait_manifest.py 单一 owner), 本件只做一致性校验, 生成器重跑即自愈;
  ③ 新增 --root 沙箱模式供负例测试(默认真库, 测试不碰真文件)。

边界注记: selection_preflight_check.py 读 feed 属呈现层合法读取(零拦截权), 不入禁读清单;
mainline_startup_scan/breakout_scout 同理。

检查项:
  C1 交易链零画像引用(显式清单逐行扫描)
  C2 生成物 permissions: 决策四位(ranking/filtering/pre_trade_gate/trade_execution)全 false,
     display=true
  C3 生成物必备元数据: schema_version/owner/built_at/asof*/qfq_max_date/permissions
  C4 manifest 哈希一致(防手改; 缺 manifest = 生成器未升级批1' → FAIL)
  C5 三源 asof 互差 ≤3 自然日(feed T-1截面 / 线B与winner5 T 时序容差; feed unavailable 时豁免)

退出码: 0=通过 / 1=违规 / 2=配置错误。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 显式闸门链清单(2026-10-11 实测: 交易链=根目录两件, 双 grep 零画像引用基准;
# gates/execution 目录当前不存在, 存在时才追加扫描)
FORBIDDEN_EXPLICIT = ["trade_executor.py", "pre_trade_check_v2.py"]
FORBIDDEN_DIR_GLOBS = ["gates", "execution"]
PORTRAIT_READ_PAT = re.compile(
    r"portrait_feed|portrait_scores|portrait_gate|portrait_softweight|universe_portrait|"
    r"winner5|portrait_agent|portrait_manifest",
    re.IGNORECASE,
)

PERMISSIONS_DECISION_FALSE = ["ranking", "filtering", "pre_trade_gate", "trade_execution"]
META_REQUIRED = ["schema_version", "owner", "built_at", "qfq_max_date", "permissions"]
EXPECTED_MANIFESTS = ["portrait_feed", "universe_portrait", "winner5"]
ASOF_MAX_NATURAL_GAP = 3

FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"[FAIL] {msg}")


def main() -> int:
    ap = argparse.ArgumentParser(description="画像产物零交易权 CI 门")
    ap.add_argument("--root", default=r"D:\zcodesj", help="仓库根(沙箱测试用)")
    args = ap.parse_args()
    ROOT = os.path.abspath(args.root)
    DATA = os.path.join(ROOT, "data")

    # ---- C1 交易链零画像引用 ----
    print("== C1 交易链零画像引用 ==")
    scan_targets = [os.path.join(ROOT, f) for f in FORBIDDEN_EXPLICIT]
    for d in FORBIDDEN_DIR_GLOBS:
        dp = os.path.join(ROOT, d)
        if os.path.isdir(dp):
            for dirpath, _dirs, files in os.walk(dp):
                scan_targets.extend(os.path.join(dirpath, f) for f in files if f.endswith(".py"))
    n_scanned = 0
    for path in scan_targets:
        if not os.path.isfile(path):
            continue
        n_scanned += 1
        with open(path, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f, 1):
                if PORTRAIT_READ_PAT.search(line):
                    fail(f"{os.path.relpath(path, ROOT)}:{i} 交易链出现画像引用: {line.strip()[:120]}")
    print(f"  扫描 {n_scanned} 个闸门链文件")

    # ---- C2/C3 生成物元数据与权限位 ----
    products = {
        "portrait_feed": (os.path.join(DATA, "portrait_feed.json"), "snapshot_date"),
        "universe_portrait": (os.path.join(DATA, "universe_portrait_latest.json"), "asof"),
        "winner5": (os.path.join(DATA, "universe_portrait_winner5_latest.json"), "asof"),
    }
    asofs = {}
    print("== C2/C3 生成物元数据与权限位 ==")
    for name, (path, asof_key) in products.items():
        rel = os.path.relpath(path, ROOT)
        if not os.path.isfile(path):
            fail(f"{rel} 缺失(生成器未跑或被删)")
            continue
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            fail(f"{rel} 解析失败: {type(e).__name__}: {e}")
            continue
        perms = doc.get("permissions")
        if not isinstance(perms, dict):
            fail(f"{rel} 缺 permissions 元数据(批1' 未落地或被手改)")
        else:
            for key in PERMISSIONS_DECISION_FALSE:
                if perms.get(key) is not False:
                    fail(f"{rel} permissions.{key} 必须为 false, 实际={perms.get(key)!r}")
            if perms.get("display") is not True:
                fail(f"{rel} permissions.display 应为 true")
        for key in META_REQUIRED:
            if key not in doc:
                fail(f"{rel} 缺必备元数据 {key}")
        asof = doc.get(asof_key)
        qfq_max = doc.get("qfq_max_date")
        if asof:
            asofs[name] = str(asof)
        if name == "portrait_feed":
            # feed fail-closed 态: unavailable 非 null 时 asof/qfq_max 可缺(C5 豁免), 元数据仍在
            if doc.get("unavailable"):
                print(f"  [{name}] fail-closed 态: {doc.get('unavailable')}")
                continue
        if asof and qfq_max and str(asof) != str(qfq_max):
            fail(f"{rel} asof({asof}) != qfq_max_date({qfq_max}) (混合口径)")
        print(f"  [{name}] asof={asof} qfq_max={qfq_max} schema={doc.get('schema_version')}")

    # ---- C4 manifest 防手改 ----
    print("== C4 manifest 哈希防手改 ==")
    mdir = os.path.join(DATA, "manifests")
    for name in EXPECTED_MANIFESTS:
        mpath = os.path.join(mdir, f"{name}.manifest.json")
        if not os.path.isfile(mpath):
            fail(f"缺 data/manifests/{name}.manifest.json (生成器批1' 未落地?)")
            continue
        try:
            with open(mpath, encoding="utf-8") as f:
                man = json.load(f)
        except Exception as e:
            fail(f"{name}.manifest.json 解析失败: {e}")
            continue
        for rel, expected in man.get("files", {}).items():
            fpath = os.path.join(ROOT, rel)
            if not os.path.isfile(fpath):
                fail(f"manifest 列出的文件缺失: {rel}")
                continue
            import hashlib
            h = hashlib.sha256()
            with open(fpath, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            actual = "sha256:" + h.hexdigest()
            if actual != expected:
                fail(f"{rel} 哈希不符疑被手改(build后变更): 期望{expected[:20]}… 实际{actual[:20]}… (生成器重跑即自愈)")
        print(f"  [{name}] {len(man.get('files', {}))} 文件核验完成 (built_at={man.get('built_at')})")

    # ---- C5 三源 asof 互差 ----
    print("== C5 三源 asof 互差 ==")
    if len(asofs) >= 2:
        vals = [datetime.datetime.strptime(v, "%Y%m%d") for v in asofs.values()]
        gap = (max(vals) - min(vals)).days
        if gap > ASOF_MAX_NATURAL_GAP:
            fail(f"三源 asof 互差 {gap} 自然日 > {ASOF_MAX_NATURAL_GAP}: {asofs}")
        print(f"  asof 集={asofs} 互差={gap}d (容差{ASOF_MAX_NATURAL_GAP}d)")

    print(f"== 结果: {'PASS' if not FAILS else f'{len(FAILS)} FAIL'} ==")
    return 1 if FAILS else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # 配置/环境错误 ≠ 违规
        print(f"[FATAL] rc=2: {type(e).__name__}: {e}")
        sys.exit(2)
