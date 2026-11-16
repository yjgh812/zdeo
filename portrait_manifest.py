#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_manifest.py — 画像生成物防手改 manifest 单一 owner（2026-10-11 批1' 落地）。

背景: 外部审计 PORTRAIT_AUDIT_20261011 §2.3 要求生成物哈希防手改。其原骨架(单一静态
data/portrait_manifest.json)结构性不成立——三份生成物每日重生成, 静态哈希必日日 FAIL。
本件口径 = 各生成器 build 成功后**自写分片 manifest**(data/manifests/<name>.manifest.json),
CI(check_portrait_permissions.py) 只做"当前文件哈希 vs 当次 build 记录"一致性校验:
  哈希不符 = build 之后被手改 → FAIL; 生成器重跑即自愈(宪法§5: 生成物只读不手改, 改源重跑)。

用法(from 生成器, 数据文件落盘之后调用):
  from portrait_manifest import write_manifest
  write_manifest("portrait_feed", {"data/portrait_feed.json": OUT_F})
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os

MANIFEST_DIR = os.path.join(r"D:\zcodesj", "data", "manifests")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def write_manifest(name: str, files: dict) -> str:
    """files: {仓库相对路径: 绝对路径}。必须在数据文件写盘之后调用(哈希=当次产物)。"""
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    doc = {
        "manifest_version": "portrait.manifest.v1",
        "generator": name,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "files": {rel: _sha256(abs_path) for rel, abs_path in sorted(files.items())},
    }
    out = os.path.join(MANIFEST_DIR, f"{name}.manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return out
