#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买入溢价收盘核对器（胜率计划阶段2第4条，2026-08-24）。

背景：44 笔历史成交核对证实 8/13/8/19 型"早盘冲高买贵"中位 +1.8%，
是"买了就跌"最直接的微观机制。本工具每日收盘后跑一次（5 秒），
当日溢价中位 >1.5% 即 WARN —— 让买贵当天被发现，而不是三周后复盘才发现。

输出三件事：
  1. 当日每笔买入：成交价 vs 白名单参考价 vs qfq 当日收盘 的溢价
  2. 当日指标：名单内买入占比 / 买价溢价中位（8/31 一周复盘三指标之二）
  3. 未在白名单内的买入逐笔列出（名单制 R26 合规监控）

数据源：westock_client.WeStockClient（唯一入口，token 自动管理；不复制直连
HTTP 兜底——westock_client 已是稳定 owner，复制执行链违反宪法 §5）。

用法: python tulong_buy_premium_check.py [--date 20260824]
退出码: 0=正常/无买入 1=溢价超标或名单外买入 2=数据源失败
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
WARN_PREMIUM = 1.5  # 溢价警戒线(%)：44笔历史中位+1.8%的一半


def fetch_today_buys() -> list[dict] | None:
    """westock 当日成交买单；失败返回 None（fail-closed，由调用方退 2）。"""
    try:
        sys.path.insert(0, r"D:/workbuddyspace")
        from westock_client import WeStockClient
        ws = WeStockClient()
        r = ws.call("portfolio_paper_history", {"range": "today", "limit": 100})
        recs = (r.get("data") or {}).get("records") or []
        return [x for x in recs if x.get("direction") == "buy" and x.get("status") == "已成交"]
    except Exception as e:
        print(f"[ERROR] WeStockClient 失败({type(e).__name__}: {e}) → 结构化退出", file=sys.stderr)
        return None


def load_whitelist(date_str: str, wl_path: str = "") -> dict[str, dict]:
    """{code: row_dict}；wl_path 优先（当日执行基准名单，防重跑覆盖失真）。"""
    path = wl_path or os.path.join(BASE, "reports", f"buy_whitelist_{date_str}.csv")
    out: dict[str, dict] = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = (row.get("code") or "").strip()
            if code:
                out[code] = row
    return out


def qfq_close(code: str, date_str: str) -> float | None:
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=5)
        r = conn.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                         (code, date_str)).fetchone()
        conn.close()
        return float(r[0]) if r and r[0] else None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--whitelist", default="",
                    help="执行基准白名单CSV路径（默认当日最新版；当日重跑过白名单时须传执行时版本）")
    a = ap.parse_args()
    date_str = a.date

    buys = fetch_today_buys()
    if buys is None:
        return 2
    wl = load_whitelist(date_str, a.whitelist)
    print(f"=== 买入溢价核对 {date_str}（胜率计划阶段2）===")
    print(f"当日成交买单: {len(buys)} 笔 | 白名单: {len(wl)} 只"
          f"{'（缺失）' if not wl else ''}")

    if not buys:
        print("无买入，跳过")
        return 0

    premiums, in_list, out_list = [], [], []
    for b in buys:
        code6 = (b.get("code") or "")[-6:]
        name = b.get("name", "")
        px = float(b.get("price") or 0)
        if px <= 0:
            continue
        row = wl.get(code6)
        close = qfq_close(code6, date_str)
        prem_close = (px / close - 1) * 100 if close else None
        if row:
            ref = float(row.get("price") or 0)
            prem_ref = (px / ref - 1) * 100 if ref > 0 else None
            in_list.append(code6)
            premiums.append(prem_close if prem_close is not None else 0.0)
            flag = "🔴" if (prem_close or 0) > WARN_PREMIUM else "✅"
            print(f" {flag} {code6} {name} 成交{px} vs 名单参考{ref or '-'}"
                  f"({prem_ref and f'{prem_ref:+.1f}%'}) "
                  f"vs 收盘{close or '-'}({prem_close and f'{prem_close:+.1f}%'}) "
                  f"[{row.get('source')}/{row.get('tier')}]")
        else:
            out_list.append(f"{code6}{name}@{px}")
            premiums.append(prem_close if prem_close is not None else 0.0)
            print(f" ⛔ {code6} {name} 成交{px} **不在白名单** "
                  f"(vs 收盘{prem_close and f'{prem_close:+.1f}%'})")

    if premiums:
        s = sorted(premiums)
        med = s[len(s) // 2]
        print(f"\n[指标] 名单内买入占比: {len(in_list)}/{len(buys)}"
              f" ({len(in_list) / len(buys) * 100:.0f}%)"
              f" | 成交价vs当日收盘溢价中位: {med:+.2f}%")
        verdict = []
        if out_list:
            verdict.append(f"⛔名单外买入{len(out_list)}笔: {', '.join(out_list)}")
        if med > WARN_PREMIUM:
            verdict.append(f"🔴溢价中位{med:+.2f}%>{WARN_PREMIUM}%（买贵警戒）")
        if verdict:
            print("\n".join(verdict))
            return 1
        print("✅ 全部合规")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
