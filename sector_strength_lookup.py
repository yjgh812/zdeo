#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sector_strength_lookup.py — 个股所属申万L1板块强度查询（C6/O13 数据源，2026-08-28 落地）

背景（E64/E65 收口）：C6「板块强势白名单」自 2026-08-23 物化起无调用方传 --sector-pct
（死参数）；14:30 链 R5 卖出"板块未退潮只减半仓"无判定数据可依据。本脚本提供
个股 → 申万L1 → 板块强度 的唯一查询入口（脚本侧，供 do_sell / 14:30 链 / 人工调用）。

数据链（全部本地只读，无网络）：
  ① code → 申万L1：dragon_quant_meta.db stock_sector_sw1（sh/sz 前缀码, 5449只）
  ② L1 → 强度：sw_industry.db industry_kline_sw(level='L1') 最近 6 根日线
     → pct_1d（最新一根 vs 前一根）/ pct_5d（最新一根 vs 第 6 根）

判定口径（显式文档化，T-1 近似：最新一根=最近已收盘交易日，不含当日盘中）：
  强势  : pct_1d >= +3.0（对齐 C6 原始阈值）或 pct_5d >= +8.0
  退潮  : pct_1d <= -3.0 且 pct_5d <= -5.0
  中性  : 其余
  无数据: 映射/指数缺失 → fail-open（不阻断任何卖出，仅标注）

用法：
  python sector_strength_lookup.py --code sh600984
  python sector_strength_lookup.py --code 600984 --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
SW_DB = r"D:\workbuddyspace\databases\sw_industry.db"

STRONG_1D = 3.0
STRONG_5D = 8.0
WEAK_1D = -3.0
WEAK_5D = -5.0


def norm_prefixed(code: str) -> str:
    """归一为 sh/sz/bj 前缀码（与 stock_sector_sw1.code 口径一致）。"""
    c = str(code or "").strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c[:2] + "".join(ch for ch in c[2:] if ch.isdigit()).zfill(6)[-6:]
    digits = "".join(ch for ch in c if ch.isdigit()).zfill(6)[-6:]
    if digits.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + digits
    if digits.startswith(("8", "920", "430", "4")):
        return "bj" + digits
    return "sz" + digits


def lookup(code: str) -> dict:
    """查询个股所属申万L1板块强度。任何数据缺失均 fail-open 返回 c6_verdict=无数据。"""
    pcode = norm_prefixed(code)
    out = {"code": pcode, "found": False, "sector": None, "latest_date": None,
           "pct_1d": None, "pct_5d": None, "c6_verdict": "无数据",
           "note": ""}
    try:
        m = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=5)
        row = m.execute("SELECT sector_name FROM stock_sector_sw1 WHERE code=?",
                        (pcode,)).fetchone()
        m.close()
    except Exception as e:
        out["note"] = f"sw1映射库读取失败(fail-open): {type(e).__name__} {e}"
        return out
    if not row or not row[0]:
        out["note"] = "stock_sector_sw1 无该股申万L1映射(fail-open)"
        return out
    sector = row[0]
    out["sector"] = sector
    try:
        s = sqlite3.connect(f"file:{SW_DB}?mode=ro", uri=True, timeout=5)
        rows = s.execute(
            "SELECT trade_date, close FROM industry_kline_sw "
            "WHERE level='L1' AND name=? ORDER BY trade_date DESC LIMIT 6",
            (sector,)).fetchall()
        s.close()
    except Exception as e:
        out["note"] = f"sw指数库读取失败(fail-open): {type(e).__name__} {e}"
        return out
    if len(rows) < 2:
        out["note"] = f"申万L1[{sector}]指数日线不足2根(fail-open)"
        return out
    closes = [float(r[1]) for r in rows if r[1]]
    if len(closes) < 2 or closes[-1] <= 0 or closes[1] <= 0:
        out["note"] = f"申万L1[{sector}]收盘价异常(fail-open)"
        return out
    pct_1d = (closes[0] / closes[1] - 1) * 100
    pct_5d = ((closes[0] / closes[5] - 1) * 100
              if len(closes) >= 6 and closes[5] > 0 else None)
    out.update({"found": True, "latest_date": str(rows[0][0]),
                "pct_1d": round(pct_1d, 2),
                "pct_5d": round(pct_5d, 2) if pct_5d is not None else None})
    if pct_1d >= STRONG_1D or (pct_5d is not None and pct_5d >= STRONG_5D):
        out["c6_verdict"] = "强势"
    elif pct_1d <= WEAK_1D and pct_5d is not None and pct_5d <= WEAK_5D:
        out["c6_verdict"] = "退潮"
    else:
        out["c6_verdict"] = "中性"
    out["note"] = (f"T-1口径(最新={out['latest_date']}, 不含当日盘中); "
                   f"阈值: 强势=1d>={STRONG_1D}%或5d>={STRONG_5D}%, "
                   f"退潮=1d<={WEAK_1D}%且5d<={WEAK_5D}%")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="个股所属申万L1板块强度查询（C6/O13 数据源）")
    p.add_argument("--code", required=True, help="股票代码（sh/sz 前缀或裸6位）")
    p.add_argument("--json", action="store_true", help="仅输出 JSON（默认即JSON）")
    a = p.parse_args()
    r = lookup(a.code)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["found"] else 2


if __name__ == "__main__":
    sys.exit(main())
