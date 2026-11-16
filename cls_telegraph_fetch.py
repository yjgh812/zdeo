#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cls_telegraph_fetch.py — 财联社电报直连拉取器（schtasks 通道，2026-09-13 落地）。

背景：21:30 新闻 PIT 的 automation 会话内 MCP cls_raw 被 example-plugin hook
劫持（9/12 21:33 FAIL 5/5 平台级实锤，SOP(十七)③/(十五)⑥ 建议迁 schtasks）。
本脚本 = 无 MCP 依赖的等价采集面：直连 cls.cn 官方 telegraph API（与 cls_raw
同源同字段），roll_data 原样透传写入 _cache JSON（供 news_pit_archive.py
--input 消费），id/ctime/title/content 等字段零伪造零加工。

签名口径（cls.cn 公开实现）：sign = sha1(sha1(sorted_query_string)) 小写 hex。
请求失败短重试 2 次（单时点采样语义：失败如实退出留日志，禁止补采历史）。

退出码：0 = 成功且条数>0（--out 已写）；2 = 拉取失败/空数组（fail-closed）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

API_URL = "https://www.cls.cn/v1/roll/get_roll_list"  # 与 cls_news_mcp._cls_request 同端点
SV = "8.7.9"  # 与 cls_news_mcp 同版本参数
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.cls.cn/telegraph",
    "Accept": "application/json, text/plain, */*",
}


def cls_sign(params: dict) -> str:
    """md5(sha1_hex(排序参数串))，与 cls_news_mcp._cls_sign 同合同。"""
    parts = []
    for k in sorted(params.keys()):
        v = params[k]
        if v is None:
            continue
        parts.append(f"{k}={v}")
    query = "&".join(parts)
    sha1_hex = hashlib.sha1(query.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1_hex.encode("utf-8")).hexdigest()


def fetch(rn: int, timeout: int, retries: int) -> list | None:
    params = {"app": "CailianpressWeb", "os": "web", "sv": SV, "rn": rn}
    signed = dict(params)
    signed["sign"] = cls_sign(params)
    url = f"{API_URL}?{ '&'.join(f'{k}={v}' for k, v in signed.items()) }"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            roll = (payload.get("data") or {}).get("roll_data")
            if isinstance(roll, list) and roll:
                return roll
            last_err = f"roll_data 空/非列表: {type(roll).__name__}"
        except Exception as exc:
            last_err = repr(exc)
        if attempt < retries:
            time.sleep(3)
    print(f"[FAIL] 拉取失败({retries}次): {last_err}", file=sys.stderr)
    return None


def main() -> int:
    try:
        ap = argparse.ArgumentParser()
        ap.add_argument("--rn", type=int, default=50)
        ap.add_argument("--timeout", type=int, default=20)
        ap.add_argument("--retries", type=int, default=2)
        ap.add_argument("--out", default=None, help="输出 json 路径；默认 _cache/news_raw_YYYYMMDD_HHMM.json")
        args = ap.parse_args()

        now = datetime.now()
        out = Path(args.out) if args.out else Path(
            rf"D:\zcodesj\_cache\news_raw_{now:%Y%m%d}_{now:%H%M}.json")
        out.parent.mkdir(parents=True, exist_ok=True)

        roll = fetch(args.rn, args.timeout, args.retries)
        if not roll:
            return 2

        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(roll, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out)
        has_id = sum(1 for r in roll if r.get("id") not in (None, ""))
        print(f"[OK] 条数={len(roll)} 含id={has_id} -> {out}")
        return 0
    except Exception as exc:
        print(f"[FAIL] 异常: {exc!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
