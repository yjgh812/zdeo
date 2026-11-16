#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tulong_whitelist_push.py — 买入白名单精选推送 (Telegram, 经 notify.py 统一通道)
================================================================
读 reports/buy_whitelist_YYYYMMDD.csv, 精选格式化后经 notify.py 的 telegram 通道推送。
telegram 未配置时明确提示(不静默), 白名单仍已生成不影响交易准备。

用法: python tulong_whitelist_push.py [--date YYYYMMDD] [--top N]
  --date  默认取最新 buy_whitelist 文件
  --top   仅推送前 N 条 (0=全部)
"""
import argparse
import csv
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import notify  # noqa: E402  (复用配置/通道/日志)


def latest_whitelist():
    files = sorted(BASE.glob("reports/buy_whitelist_*.csv"))
    return files[-1] if files else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="YYYYMMDD, 默认取最新")
    ap.add_argument("--top", type=int, default=0, help="仅推送前 N 条 (0=全部)")
    args = ap.parse_args()

    f = BASE / "reports" / f"buy_whitelist_{args.date}.csv" if args.date else latest_whitelist()
    if f is None or not f.exists():
        print("未找到 buy_whitelist CSV")
        return 2

    rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
    if not rows:
        print(f"{f.name} 为空")
        return 2
    if args.top > 0:
        rows = rows[: args.top]

    date_tag = f.stem.replace("buy_whitelist_", "")
    body_lines = []
    for r in rows:
        code = r.get("code", "")
        name = r.get("name", "")
        price = r.get("price", "")
        src = r.get("source", "")
        tier = r.get("tier", "")
        note = (r.get("note") or "").strip()
        body_lines.append(f"{code} {name} 价{price} [{src}/{tier}] {note[:60]}".rstrip())
    content = "\n".join(body_lines)

    cfg = notify.load_cfg()
    tg = cfg.get("channels", {}).get("telegram", {})
    if not (tg.get("bot_token") and tg.get("chat_id")):
        print("⚠️ telegram 未配置(bot_token/chat_id)。白名单已生成但未推送。建 Bot 步骤见 reports/TELEGRAM_BOT_SETUP_20260823.md")
        return 1

    title = f"📋 屠龙买入白名单精选 {date_tag} ({len(rows)}条)"
    return notify.deliver(cfg, title, content, "info", channel="telegram")


if __name__ == "__main__":
    sys.exit(main())
