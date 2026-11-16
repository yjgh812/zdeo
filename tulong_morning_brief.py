#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘前财经早报（2026-08-21 建，屠龙系统新增功能①）

工作日 08:30 生成，聚合各类股市消息：
  1. 财联社重点电报（A/B级，昨日 15:00 起）—— cls_api 直连（签名已破解）
  2. 隔夜美股映射信号 —— reports/us_overnight_signal_{date}.json（07:30 已生成）
  3. 昨日复盘要点 —— reports/daily_review_{last}.json 优先，DAILY_REVIEW_{last}.md 兜底
  4. 最近宏观闸门状态 —— reports/macro_regime_log.txt 末条

设计：
  - 脚本只做"可直连/可读本地"的部分，输出草稿到 morning_brief_{date}_draft.md
  - 「今日财经日历」「实时快讯」两段留模板，由 cron 中知微调 MCP（jin10 日历/东财快讯/
    华尔街见闻）补全后定稿为 morning_brief_{date}.md（draft 方案避免重复运行覆盖定稿）
  - 产出 JSON 供 cron 汇报（--json）
  - 2026-08-22 起: 数据源默认小石晨报+快讯(xiaoshi_news_bridge), CLS 兜底; --source cls 可切回

用法：
    python tulong_morning_brief.py                 # 今日(默认小石晨报+快讯)
    python tulong_morning_brief.py --source cls    # 数据源改为财联社
    python tulong_morning_brief.py --date 20260821 # 指定日期
    python tulong_morning_brief.py --json          # JSON 输出
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, r"D:/zcodesj")

REPORT_DIR = r"D:\zcodesj\reports"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def last_trade_date() -> str:
    """qfq 库最近交易日（YYYYMMDD）。"""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=20)
        d = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        con.close()
        return str(d)
    except Exception:
        # 2026-08-24 P2修复: qfq 不可用时回退 _today() 会把周一早报的新闻窗收窄到当日,
        # 丢掉上周五全天电报；返回昨日并打印原因（宁可多取一天，不静默丢数据）
        print('[warn] last_trade_date: qfq 库不可用，回退昨日', file=sys.stderr)
        from datetime import timedelta
        return (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')


def fetch_cls_important(since_ts: int, max_batches: int = 4) -> list[dict]:
    """拉取财联社 A/B 级重点电报（ctime >= since_ts），翻页直到越过边界或达上限。"""
    from cls_api import get_telegraph  # 顶层 import 会拉 requests，局部加载更稳
    out: list[dict] = []
    last_time = None
    for _ in range(max_batches):
        try:
            items = get_telegraph(rn=50, last_time=last_time, only_important=True)
        except Exception as e:
            # 2026-08-24 P2修复: 静默 break → 打印原因（区分"没新闻"与"接口挂"）
            print(f"[warn] 财联社拉取中断(已取{len(out)}条): {type(e).__name__}: {e}", file=sys.stderr)
            break
        if not items:
            break
        out.extend(items)
        oldest = min(int(i.get("ctime") or 0) for i in items)
        if oldest < since_ts:
            break
        last_time = oldest
    # 过滤边界 + 去重(按 ctime+title)
    seen = set()
    filtered = []
    for i in out:
        ct = int(i.get("ctime") or 0)
        if ct < since_ts:
            continue
        key = (ct, (i.get("title") or "")[:40])
        if key in seen:
            continue
        seen.add(key)
        filtered.append(i)
    filtered.sort(key=lambda i: i.get("ctime") or 0, reverse=True)
    return filtered


def fetch_xiaoshi_brief(since_ts: int) -> tuple[dict, list]:
    """拉取小石晨报 + 快讯(ctime>=since_ts)。失败返回({}, [])。"""
    sys.path.insert(0, r"D:\zcodesj\xiaoshi")
    try:
        from xiaoshi_news_bridge import fetch_briefing, fetch_flash_since
    except Exception:
        return {}, []
    try:
        brief = fetch_briefing("morning")
        items = fetch_flash_since(since_ts, limit=100)
    except Exception as e:
        # 2026-08-24 P2修复: 打印降级原因（json 的 count=0 才能区分"无内容"与"接口失败"）
        print(f"[warn] 小石源失败回退CLS: {type(e).__name__}: {e}", file=sys.stderr)
        return {}, []
    return brief, items


def fmt_xiaoshi(items: list[dict], limit: int = 25) -> str:
    """小石快讯归一化条目 -> 文本(标题/来源/时间/urgency/score)。"""
    lines = []
    for i in items[:limit]:
        ts = i.get("ctime") or 0
        t = datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "--:--"
        tag = f"[{i['urgency']}]" if i.get("urgency") else ""
        sc = f"score={i.get('score'):.0f}" if isinstance(i.get("score"), (int, float)) else ""
        src = i.get("source") or ""
        lines.append(f"{t} | {src} {tag} {sc} | {i['title']}")
    return "\n".join(lines)


def fmt_cls(items: list[dict], limit: int = 25) -> str:
    from cls_api import format_telegraph
    return format_telegraph(items[:limit], with_content=True)


def read_us_signal(date_str: str) -> dict:
    p = os.path.join(REPORT_DIR, f"us_overnight_signal_{date_str}.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def us_signal_md(sig: dict) -> str:
    if not sig:
        return "- 未生成（07:30 美股映射未跑或失败）"
    lines = []
    sectors = sig.get("sectors", {})
    if not sectors:
        return f"- 信号文件存在但无板块数据（{sig.get('note', '')}）"
    for name, s in sectors.items():
        st = s.get("strength", "NO_DATA")
        icon = {"STRONG_UP": "🔴强多", "UP": "🟠偏多", "FLAT": "⚪中性", "DOWN": "🟢偏空",
                "STRONG_DOWN": "🔵强空", "NO_DATA": "⚫无数据"}.get(st, st)
        pct = s.get("signal_pct")
        pct_s = f"{pct:+.1f}%" if isinstance(pct, (int, float)) else "--"
        n = s.get("stock_count", 0)
        lines.append(f"- {name}：{icon}（信号 {pct_s}，{n} 只样本）")
    return "\n".join(lines)


def read_yesterday_review(last_date: str) -> dict:
    """昨日复盘要点：daily_review_{last}.json 优先，md 兜底。"""
    jp = os.path.join(REPORT_DIR, f"daily_review_{last_date}.json")
    if os.path.exists(jp):
        try:
            with open(jp, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("market_env"):
                return d
        except Exception:
            pass
    mp = os.path.join(REPORT_DIR, f"DAILY_REVIEW_{last_date}.md")
    if os.path.exists(mp):
        try:
            with open(mp, encoding="utf-8") as f:
                text = f.read()
            heads = [ln.strip() for ln in text.splitlines() if ln.startswith("#")]
            return {"market_env": "（md 版复盘，详见报告）", "main_sectors": heads[:10], "risk_warnings": []}
        except Exception:
            pass
    return {}


def read_macro_regime() -> dict:
    p = os.path.join(REPORT_DIR, "macro_regime_log.txt")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        if not lines:
            return {}
        last = lines[-1]
        m = re.match(r"\[([^\]]+)\]\s*REGIME=(\S+)\s*SCORE=(\S+)\s*仓位=(\S+)\s*风格=(.*)", last)
        if m:
            return {"time": m.group(1), "regime": m.group(2), "score": m.group(3),
                    "position": m.group(4), "style": m.group(5).strip()}
        return {"raw": last}
    except Exception:
        return {}


def build_md(date_str: str, last: str, cls_items: list, sig: dict, rev: dict, macro: dict,
             source: str = "cls", xiaoshi_brief: dict = None, xiaoshi_items: list = None) -> str:
    wd = {"Monday": "周一", "Tuesday": "周二", "Wednesday": "周三", "Thursday": "周四",
          "Friday": "周五", "Saturday": "周六", "Sunday": "周日"}.get(
              datetime.strptime(date_str, "%Y%m%d").strftime("%A"), "")
    L = []
    L.append(f"# 📰 屠龙财经早报 · {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}（{wd}）")
    L.append("")
    L.append(f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} ｜ 最近交易日 {last[:4]}-{last[4:6]}-{last[6:]} ｜ "
             f"本稿为草稿，待知微补全日历/快讯后定稿")
    L.append("")

    # 1 隔夜外盘 & 美股映射
    L.append("## 🌙 隔夜外盘与美股映射")
    L.append("")
    L.append(us_signal_md(sig))
    L.append("")

    if source == "xiaoshi":
        # 小石晨报摘要
        L.append("## 📰 小石晨报摘要")
        L.append("")
        if xiaoshi_brief and (xiaoshi_brief.get("title") or xiaoshi_brief.get("content")):
            L.append(f"**{xiaoshi_brief.get('title', '')}**")
            L.append("")
            L.append("```")
            L.append((xiaoshi_brief.get("content") or "")[:1500])
            L.append("```")
        else:
            L.append("- 小石晨报未拉到（接口失败或非交易时段）")
        L.append("")

        # 小石快讯
        L.append(f"## ⚡ 小石快讯（昨日 15:00 后 {len(xiaoshi_items or [])} 条）")
        L.append("")
        if xiaoshi_items:
            L.append("```")
            L.append(fmt_xiaoshi(xiaoshi_items[:25]))
            L.append("```")
        else:
            L.append("- 未拉到（接口失败或非交易时段）")
        L.append("")
    else:
        # 2 财联社重点电报
        L.append(f"## 📰 财联社重点电报（昨日 15:00 后 A/B 级 {len(cls_items)} 条）")
        L.append("")
        if cls_items:
            L.append("```")
            # 2026-08-24 P1修复: format_telegraph 此前未导入（仅 fmt_cls 内局部 import），
            # 小石失败回退财联社路径必然 NameError → 早报草稿不落盘。改走 fmt_cls。
            L.append(fmt_cls(cls_items[:25]))
            L.append("```")
        else:
            L.append("- 未拉到（接口失败或非交易时段）")
        L.append("")

    # 3 昨日复盘要点
    L.append("## 📋 昨日复盘要点")
    L.append("")
    if not rev:
        L.append("- 未读取到昨日复盘（DAILY_REVIEW/daily_review 缺失）")
    else:
        L.append(f"- 市场环境：{rev.get('market_env', '（无）')}")
        ms = rev.get("main_sectors") or []
        if ms:
            L.append("- 主线方向：")
            for s in ms[:6]:
                L.append(f"  - {s}")
        rw = rev.get("risk_warnings") or []
        if rw:
            L.append("- 风险警告：")
            for s in rw[:6]:
                L.append(f"  - {s}")
    L.append("")

    # 4 宏观环境
    L.append("## 🧭 宏观环境")
    L.append("")
    if not macro:
        L.append("- 未读取到宏观闸门记录")
    elif "raw" in macro:
        L.append(f"- {macro['raw']}")
    else:
        L.append(f"- REGIME={macro.get('regime')} ｜ SCORE={macro.get('score')} ｜ "
                 f"仓位建议={macro.get('position')} ｜ 风格={macro.get('style')} ｜ 记录时间 {macro.get('time')}")
    L.append("")

    # 5-6 预留段（知微补全）
    L.append("## 📅 今日财经日历（待知微 MCP 补全）")
    L.append("- [ ] 经济数据/会议/解禁（jin10 日历或百度财经日历）")
    L.append("")
    L.append("## ⚡ 实时快讯（待知微 MCP 补全）")
    L.append("- [ ] 金十快讯/东财7x24/华尔街见闻摘选")
    L.append("")

    # 7 风险提示（铁律）
    L.append("## ⚠️ 风险提示")
    L.append("- 本报告为信息聚合，非交易指令；任何买入须过 pre_trade_check_v2 全闸门。")
    L.append("- 隔夜美股映射板块仅参考，A 股开盘后以实际走势为准。")
    L.append("")
    return "\n".join(L)


def position_gate_line() -> str:
    """晨报顶部 A1 组合仓位闸门状态行(20261021 开工令: 仓位闸门状态 开启/关闭)。

    阈值唯一真源 = trade_executor._POSITION_GATE_THRESHOLD(该模块有 __main__ 守卫, import 无副作用);
    westock 取数失败 → N/A 不阻断晨报。本行纯呈现, A1 执行面在 trade_executor.do_buy 内 fail-closed。
    """
    try:
        from westock_client import WeStockClient
        import trade_executor as te
        ws = WeStockClient()
        pf = (ws.portfolio().get("data") or {}).get("portfolio") or {}
        ta = float(pf.get("totalAssets") or 0)
        mv = float(pf.get("marketValue") or 0)
        ratio = (mv / ta) if ta > 0 else None
        thr = float(getattr(te, "_POSITION_GATE_THRESHOLD", 0) or 0)
        if ratio is None or thr <= 0:
            return "仓位闸门状态(A1)：N/A(仓位或阈值不可计算; 执行面仍 fail-closed)"
        state = "🟢 关闭(允许新增)" if ratio <= thr else "🔴 开启(仓位超标, 新增买入仅记录不执行)"
        return f"仓位闸门状态(A1)：{state} ｜ 当前仓位 {ratio:.1%} / 阈值 {thr:.0%}"
    except Exception as e:
        return f"仓位闸门状态(A1)：N/A({type(e).__name__}: {e})"


def main() -> int:
    ap = argparse.ArgumentParser(description="盘前财经早报生成器")
    ap.add_argument("--date", default=_today())
    ap.add_argument("--source", choices=["cls", "xiaoshi"], default="xiaoshi",
                    help="新闻数据源: xiaoshi=小石晨报+快讯(默认), cls=财联社")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args()

    date_str = a.date
    last = last_trade_date()
    since = datetime.strptime(
        (datetime.strptime(last, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d"), "%Y%m%d")
    since_ts = int(since.replace(hour=15, minute=0, second=0).timestamp())
    cls_items = fetch_cls_important(since_ts)
    xiaoshi_brief, xiaoshi_items = {}, []
    if a.source == "xiaoshi":
        xiaoshi_brief, xiaoshi_items = fetch_xiaoshi_brief(since_ts)
        # 小石失败 -> CLS 兜底
        if not xiaoshi_brief and not xiaoshi_items:
            a.source = "cls"
    sig = read_us_signal(date_str)
    rev = read_yesterday_review(last)
    macro = read_macro_regime()

    if a.json:
        print(json.dumps({
            "date": date_str, "last_trade_date": last,
            "source": a.source,
            "cls_important_count": len(cls_items),
            "xiaoshi_brief_title": (xiaoshi_brief or {}).get("title"),
            "xiaoshi_flash_count": len(xiaoshi_items),
            "us_signal_sectors": list(sig.get("sectors", {}).keys()),
            "us_signal_strengths": {k: v.get("strength") for k, v in sig.get("sectors", {}).items()},
            "macro": macro,
            "review_found": bool(rev),
        }, ensure_ascii=False, indent=2))
        return 0

    md = build_md(date_str, last, cls_items, sig, rev, macro,
                  source=a.source, xiaoshi_brief=xiaoshi_brief, xiaoshi_items=xiaoshi_items)
    # A1 仓位闸门状态行(20261021 开工令): 晨报顶部第一行, 取数失败 N/A 不阻断
    md = position_gate_line() + "\n\n" + md
    os.makedirs(REPORT_DIR, exist_ok=True)
    draft = os.path.join(REPORT_DIR, f"morning_brief_{date_str}_draft.md")
    with open(draft, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"✅ 早报草稿已生成：{draft}")
    src_desc = {"xiaoshi": f"小石晨报+快讯 {len(xiaoshi_items)}条", "cls": f"财联社重点 {len(cls_items)}条"}[a.source]
    print(f"   数据源 {a.source}（{src_desc}）｜ 美股映射板块 {len(sig.get('sectors', {}))} 个 ｜ "
          f"宏观 REGIME={macro.get('regime', 'N/A')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())