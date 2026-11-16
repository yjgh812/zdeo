#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小石新闻/公告/简报桥接层 (xiaoshi_news_bridge)
==================================================
屠龙系统新闻链路统一封装小石 (Xiaoshi) 只读接口，输出与现有消费方兼容结构。

目标:
  - premarket_catalyst_scan.py 可 --source xiaoshi 走小石 flash/briefings
  - tulong_morning_brief.py    可优先取小石 briefings_latest + flash
  - 公告/行业新闻/事件时间轴统一入口

兼容映射 (小石 -> 屠龙消费形状):
  flash item:
    id, title, content(=analysis 兜底 title), source(=original_source 兜底 source),
    ctime(→时间戳), urgency, score, affected_sectors, tags, time, url
  briefings: report_type, report_date, title, content

约束:
  - 只读, 不落库(由消费方决定); 密钥经 xiaoshi_client 读环境变量, 不落盘明文
  - 429/空结果遵守小石契约; 空列表≠失败(可能平静盘/无公告)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import xiaoshi_client as xc
except ImportError:
    xc = None

_SOURCE_NAME = "xiaoshi"


def _to_ts(iso: str | None) -> int:
    """ISO 时间/日期 -> epoch 秒。兼容 '2026-08-22T11:34:00+08:00' 与 '2026-08-22'。"""
    if not iso:
        return 0
    s = str(iso).strip()
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        try:
            return int(datetime.fromisoformat(s).timestamp())
        except Exception:
            return 0


def available() -> bool:
    return xc is not None


def normalize_flash(items: list[dict]) -> list[dict]:
    """小石 flash items -> 屠龙兼容条目(含 title/content/ctime/source/urgency/score)。"""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        title = it.get("title") or ""
        content = it.get("analysis") or it.get("content") or title
        out.append({
            "id": it.get("id"),
            "title": title,
            "content": content,
            "text": f"{title} {content}".strip(),
            "source": it.get("original_source") or it.get("source") or _SOURCE_NAME,
            "ctime": _to_ts(it.get("time")),
            "time": it.get("time"),
            "urgency": it.get("urgency"),
            "score": it.get("score"),
            "score_version": it.get("score_version"),
            "affected_sectors": it.get("affected_sectors") or [],
            "tags": it.get("tags") or [],
            "url": it.get("original_url") or it.get("url"),
        })
    return out


def fetch_flash(limit: int = 50, industry: str = "", min_urgency: str = "") -> list[dict]:
    """拉取小石快讯, 返回归一化条目。min_urgency 过滤(如 '高')。"""
    if xc is None:
        return []
    data = xc.flash(limit=limit, industry=industry or None)
    items = normalize_flash(data.get("items", []) if isinstance(data, dict) else [])
    if min_urgency:
        items = [i for i in items if i["urgency"] == min_urgency]
    return items


def fetch_flash_since(since_ts: int, limit: int = 100) -> list[dict]:
    """拉取小石快讯并过滤 ctime >= since_ts(按返回排序取最新, 尽力覆盖)。"""
    items = fetch_flash(limit=limit)
    in_win = [i for i in items if i["ctime"] >= since_ts]
    in_win.sort(key=lambda i: i["ctime"], reverse=True)
    return in_win


def fetch_announcements(code: str, days: int = 365, page_size: int = 20) -> list[dict]:
    """小石公告列表, 返回 data 数组(每项含标题/时间/来源)。"""
    if xc is None:
        return []
    data = xc.announcements(code, days=days, page=1, page_size=page_size)
    items = data.get("data", []) if isinstance(data, dict) else []
    return items


def fetch_briefing(report_type: str = "morning") -> dict:
    """小石晨报/晚报。返回 {report_type, report_date, title, content, cached}。"""
    if xc is None:
        return {}
    data = xc.briefings_latest(report_type=report_type)
    if not isinstance(data, dict):
        return {}
    d = data.get("data") or {}
    return {
        "report_type": data.get("report_type") or report_type,
        "report_date": d.get("report_date") if isinstance(d, dict) else None,
        "title": (d.get("title") if isinstance(d, dict) else None) or data.get("title"),
        "content": (d.get("content") if isinstance(d, dict) else None) or data.get("content") or "",
        "cached": data.get("cached"),
    }


def fetch_industry_briefing(industry: str) -> dict:
    """小石行业简报。industry 须为规范行业名(先读 taxonomy)。"""
    if xc is None:
        return {}
    try:
        data = xc.briefings_industry_latest(industry)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    d = data.get("data") or data
    if isinstance(d, dict):
        return {"industry": industry, "title": d.get("title"), "content": d.get("content") or ""}
    return {"industry": industry, "content": str(data)[:1000]}


def fetch_industry_taxonomy() -> list[str]:
    """行业新闻规范名清单。小石返回 {"count":20, "data":[{code,name,aliases}]}。"""
    if xc is None:
        return []
    try:
        data = xc.news_industry_taxonomy()
        if isinstance(data, dict):
            items = data.get("data") or []
            if items:
                return [x.get("name", "") for x in items if isinstance(x, dict) and x.get("name")]
            return list(data.keys())
        if isinstance(data, list):
            return [x if isinstance(x, str) else x.get("name", "") for x in data]
    except Exception:
        pass
    return []


def source_note() -> str:
    return "数据源：小石 Xiaoshi（只读）｜ 小石新闻为非穷尽覆盖，个股新闻需另加外部核验"


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="小石新闻桥接层自测")
    ap.add_argument("--flash", action="store_true", help="拉最新快讯")
    ap.add_argument("--briefing", action="store_true", help="拉晨报")
    ap.add_argument("--ann", type=str, help="拉个股公告, 如 600519")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not available():
        print("xiaoshi_client 不可用", file=sys.stderr)
        sys.exit(1)

    if a.flash:
        items = fetch_flash(limit=a.limit)
        if a.json:
            import json
            print(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            print(f"小石快讯 {len(items)} 条:")
            for i in items[:a.limit]:
                print(f"  [{i['ctime']}] [{i['urgency']}] {i['title'][:50]}"
                      f"（{i['source']}）score={i['score']}")
    elif a.briefing:
        b = fetch_briefing()
        print(f"小石晨报: {b.get('title')} (cached={b.get('cached')})")
        print((b.get("content") or "")[:800])
    elif a.ann:
        items = fetch_announcements(a.ann)
        print(f"小石公告 {a.ann}: {len(items)} 条")
        for x in items[:10]:
            print("  ", x)
    else:
        ap.print_help()
