#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘前催化自动扫描 (Premarket Catalyst Scan)
==============================================
数据流闭环:
  财联社电报(官方直连) + 金十快讯
      → 拉取昨晚15:00至今全部电报
      → mainline_radar.detect_themes() 主题识别
      → 涨停范式库召回
      → 输出盘前主线催化报告

解决: mainline_radar --catalyst 需要手动输入催化描述的痛点,
现在全自动从财联社电报提取。

用法:
    python premarket_catalyst_scan.py             # 默认扫昨晚15:00至今(财联社)
    python premarket_catalyst_scan.py --source xiaoshi  # 数据源改为小石快讯
    python premarket_catalyst_scan.py --hours 14  # 扫最近14小时
    python premarket_catalyst_scan.py --json      # JSON输出
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import os
from datetime import datetime, timedelta

# 复用已有模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cls_api import get_telegraph  # 财联社直连(签名已破解)
from mainline_radar import detect_themes, query_patterns, CATALYST_THEME_MAP


def collect_catalyst_text_xiaoshi(hours: float = None, since_yesterday_close: bool = True) -> tuple[str, list]:
    """小石数据源: 拉快讯, 返回(催化文本, 归一化条目)。小石失败返回("", [])。"""
    try:
        sys.path.insert(0, r"D:\zcodesj\xiaoshi")
        from xiaoshi_news_bridge import fetch_flash_since
    except Exception:
        return "", []
    now = datetime.now()
    if since_yesterday_close:
        start = (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(hours=hours or 14)
    start_ts = int(start.timestamp())
    try:
        items = fetch_flash_since(start_ts, limit=100)
    except Exception:
        return "", []
    # 拼催化文本(标题+内容, 高urgency/高score加权重复)
    texts = []
    for it in items:
        text = it.get("text") or ""
        if not text:
            continue
        texts.append(text)
        if it.get("urgency") == "高" or (it.get("score") or 0) >= 60:
            texts.append(text)
    return "\n".join(texts), items


def collect_catalyst_text(hours: float = None, since_yesterday_close: bool = True) -> tuple[str, list]:
    """拉取财联社电报, 返回(催化文本, 原始条目列表)"""
    # 计算起始时间
    now = datetime.now()
    if since_yesterday_close:
        start = (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(hours=hours or 14)
    start_ts = int(start.timestamp())

    # 翻页拉取电报(最多500条, 覆盖~20小时)
    all_items = []
    last_time = None
    for _ in range(5):
        items = get_telegraph(rn=100, last_time=last_time)
        if not items:
            break
        all_items.extend(items)
        oldest = items[-1].get("ctime", 0)
        if oldest <= start_ts:
            break
        last_time = oldest

    # 过滤时间窗内的
    in_window = [i for i in all_items if i.get("ctime", 0) >= start_ts]

    # 拼催化文本(标题+内容, 重点电报加权重复)
    texts = []
    for it in in_window:
        title = it.get("title") or ""
        content = re.sub(r"<[^>]+>", "", it.get("content", "") or "")
        text = f"{title} {content}".strip()
        if text:
            texts.append(text)
            # A/B级重点电报重复一次, 提高关键词命中率
            if it.get("level") in ("A", "B"):
                texts.append(text)

    return "\n".join(texts), in_window


def extract_evidence(catalyst_text: str, theme: dict) -> list[str]:
    """提取命中该主题关键词的电报证据"""
    evidences = []
    keywords = theme.get("catalyst_keywords", [])
    for line in catalyst_text.split("\n"):
        if not line.strip():
            continue
        hits = [k for k in keywords if k.lower() in line.lower()]
        if hits:
            evidences.append({"text": line[:120], "hits": hits[:3]})
    return evidences[:5]  # 每主题最多5条证据


def main() -> int:
    p = argparse.ArgumentParser(description="盘前催化自动扫描(财联社电报/小石快讯→主线雷达)")
    p.add_argument("--source", choices=["cls", "xiaoshi"], default="xiaoshi",
                   help="数据源: xiaoshi=小石快讯(默认, 失败自动回退财联社), cls=财联社")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--hours", type=float, help="扫描最近N小时(默认从昨日15:00收盘起)")
    g.add_argument("--yesterday-close", action="store_true", default=True,
                   help="从昨日15:00扫到当前(默认)")
    p.add_argument("--json", action="store_true", help="JSON输出")
    p.add_argument("--min-score", type=float, default=6.0, help="范式可预判性阈值")
    args = p.parse_args()

    since_close = args.hours is None
    src_label = {"cls": "财联社电报", "xiaoshi": "小石快讯"}[args.source]
    print(f"📡 拉取{src_label} ({'昨日15:00至今' if since_close else f'最近{args.hours}小时'})...",
          file=sys.stderr)

    if args.source == "xiaoshi":
        catalyst_text, items = collect_catalyst_text_xiaoshi(hours=args.hours, since_yesterday_close=since_close)
        source_name = "小石快讯"
        # 小石失败/空 → 自动回退财联社(保持盘前情报可用)
        if not catalyst_text.strip():
            catalyst_text, items = collect_catalyst_text(hours=args.hours, since_yesterday_close=since_close)
            source_name = "财联社电报(小石回退)"
            print("[回退] 小石快讯无数据, 已自动回退财联社电报", file=sys.stderr)
    else:
        catalyst_text, items = collect_catalyst_text(hours=args.hours, since_yesterday_close=since_close)
        source_name = "财联社电报"
    print(f"   获取 {len(items)} 条{source_name}, 文本{len(catalyst_text)}字符", file=sys.stderr)

    if not catalyst_text.strip():
        print("无电报数据", file=sys.stderr)
        return 1

    # 主题识别
    print("🎯 主题识别...", file=sys.stderr)
    triggered = detect_themes(catalyst_text)

    if not triggered:
        report = {"scan_time": datetime.now().isoformat(), "telegraph_count": len(items),
                  "source": source_name,
                  "triggered_themes": [], "message": f"{source_name}中未识别到主线催化关键词"}
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("\n" + "=" * 60)
            print("  盘前催化扫描报告")
            print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
            print(f"  数据源: {source_name} | {len(items)}条")
            print("=" * 60)
            print("\n  未识别到主线催化关键词(平静盘)")
        return 0

    # 范式库召回 + 证据提取
    report_themes = []
    for t in triggered:
        theme_name = t["theme"]
        theme_def = CATALYST_THEME_MAP.get(theme_name, {})
        evidences = extract_evidence(catalyst_text, theme_def)
        patterns = query_patterns(theme_name, min_score=args.min_score)

        report_themes.append({
            "theme": theme_name,
            "catalyst_strength": t.get("catalyst_strength", 0),
            "matched_keywords": t.get("hit_keywords", []),
            "evidences": evidences,
            "pattern_leaders": [
                {"code": p.get("code"), "name": p.get("name"),
                 "predictability": p.get("predictability"),
                 "driver": (p.get("core_driver") or "")[:60]}
                for p in patterns[:5]
            ],
        })

    # 输出
    if args.json:
        print(json.dumps({
            "scan_time": datetime.now().isoformat(),
            "source": source_name,
            "telegraph_count": len(items),
            "triggered_themes": report_themes,
        }, ensure_ascii=False, indent=2))
    else:
        print("\n" + "=" * 60)
        print(f"  🔥 盘前催化扫描报告 ({source_name}→主线雷达)")
        print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"  {source_name}: {len(items)}条 | 命中主题: {len(report_themes)}个")
        print("=" * 60)

        for th in sorted(report_themes, key=lambda x: -x["catalyst_strength"]):
            print(f"\n  🎯 {th['theme']}  催化强度{th['catalyst_strength']:.1f}")
            print(f"     命中关键词: {', '.join(th['matched_keywords'][:6])}")
            if th["evidences"]:
                print(f"     📰 电报证据:")
                for ev in th["evidences"][:3]:
                    print(f"        • [{','.join(ev['hits'])}] {ev['text'][:80]}")
            if th["pattern_leaders"]:
                print(f"     📈 范式库龙头(历史可复用):")
                for ld in th["pattern_leaders"][:3]:
                    print(f"        • {ld['code']} {ld['name']} (可预判性{ld['predictability']}) {ld['driver'][:40]}")

        print("\n  💡 使用: 结合开盘竞价确认主线, 参考mainline_radar策略")
    return 0


if __name__ == "__main__":
    sys.exit(main())