#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小石市场快照桥接层 (xiaoshi_market_bridge)
=============================================
屠龙 monitor_engine 实时行情兜底: 将小石 market-snapshot 全市场快照
映射为 monitor_engine.fetch_quotes 的消费形状。

映射目标形状 ({code: dict}):
  name, price, pct, vol_hand(手), amount_wan(万元), data_ts, limit_up,
  limit_touched, broken, near_limit

单位约定(以 xiaoshi-data schema 数据字典为准):
  - 小石 volume 单位 = 股(share), 与历史 manifest cn-daily volume_unit=share 一致
  - 小石 amount 单位 = 元(CNY)
  - 换算: vol_hand = volume/100; amount_wan = amount/10000
  - 涨跌停价按屠龙 limit_price(prev, price_limit_pct) 本地计算(不动屠龙逻辑)

约束:
  - 只读; 单请求全市场(market=CN limit=6000), 不逐只轮询
  - 腾讯正常时不调用; 仅在腾讯批量失败时旁挂兜底
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import xiaoshi_client as xc
except ImportError:
    xc = None

# 复用屠龙本地涨跌停判定(与 monitor_engine 一致), 失败则本地兜底实现
try:
    sys.path.insert(0, r"D:\zcodesj")
    from monitor_engine import limit_price, price_limit_pct
except Exception:
    def price_limit_pct(code: str, _d: date) -> float:
        if code.startswith(("30", "68")):
            return 20.0
        if code.startswith(("8", "4", "92")):
            return 30.0
        return 10.0

    def limit_price(prev: float, pct: float) -> float:
        return round(prev * (1 + pct / 100), 2)


def available() -> bool:
    return xc is not None


def fetch_market_snapshot(market: str = "CN", limit: int = 6000) -> dict:
    """拉取小石全市场快照。返回 {code: item}。失败抛异常由调用方兜底。"""
    if xc is None:
        raise RuntimeError("xiaoshi_client 不可用")
    snap = xc.market_snapshot(market=market, offset=0, limit=limit)
    items = snap.get("items") or []
    return {
        "updated_at": snap.get("updated_at"),
        "freshness_sec": snap.get("freshness_sec"),
        "count": snap.get("count"),
        "coverage": snap.get("coverage"),
        "partial": snap.get("partial"),
        "items": items,
    }


def to_monitor_shape(snap: dict, codes: list[str] | None = None) -> dict[str, dict]:
    """将小石快照映射为 monitor_engine 消费形状。codes 为 None 时返回全部。"""
    out: dict[str, dict] = {}
    today = date.today()
    for it in snap.get("items", []):
        code = str(it.get("code") or "")
        if not code:
            continue
        if codes is not None and code not in codes:
            continue
        try:
            price = float(it.get("price") or 0)
            prev = float(it.get("last_close") or 0)
            high = float(it.get("high") or 0)
            pct = float(it.get("change_pct") if it.get("change_pct") is not None
                        else it.get("pct") or 0)
            volume = float(it.get("volume") or 0)  # 股
            amount = float(it.get("amount") or 0)  # 元
            qtime = it.get("quote_time") or ""
            lp = limit_price(prev, price_limit_pct(code, today)) if prev > 0 else 0.0
            sealed = bool(lp and price >= lp - 0.005)
            touched = bool(lp and high >= lp - 0.005)
            out[code] = {
                "name": it.get("name") or "",
                "price": price,
                "pct": pct,
                "vol_hand": volume / 100.0,   # 股 -> 手
                "amount_wan": amount / 1e4,   # 元 -> 万元
                "data_ts": qtime[:10].replace("-", "") if qtime else "",
                "limit_up": sealed,
                "limit_touched": touched,
                "broken": touched and not sealed,
                "near_limit": (lp / price - 1) * 100 if (lp and 0 < price < lp - 0.005) else 99.0,
                "_source": "xiaoshi",
            }
        except (ValueError, TypeError):
            continue
    return out


def fetch_quotes_fallback(codes: list[str]) -> dict[str, dict]:
    """兜底入口: 拉小石全市场快照并映射为指定 codes 的行情。失败抛异常。"""
    snap = fetch_market_snapshot()
    return to_monitor_shape(snap, codes=codes)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="小石市场快照桥接层自测")
    ap.add_argument("--codes", default="600519,000001,300750", help="逗号分隔代码")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not available():
        print("xiaoshi_client 不可用", file=sys.stderr)
        sys.exit(1)
    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    res = fetch_quotes_fallback(codes)
    if a.json:
        import json
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"小石兜底行情 {len(res)}/{len(codes)} 只:")
        for c in codes:
            if c in res:
                m = res[c]
                print(f"  {c} {m['name']}: price={m['price']} pct={m['pct']}% "
                      f"vol_hand={m['vol_hand']:.0f} amount_wan={m['amount_wan']:.0f} "
                      f"limit_up={m['limit_up']}")
            else:
                print(f"  {c}: 缺失")
