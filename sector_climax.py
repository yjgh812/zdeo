#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R24 板块高潮快照生成器（2026-08-21 交付，模拟盘改进方案v2 M2 高潮禁追的数据源）。

判定规则（主人批准版，2026-08-21）：
  申万L1行业指数 单日涨幅 ≥ R24_PCT_THRESHOLD(默认5%) → 该板块「当日+次一交易日」禁买。
  实证依据：8/18农林牧渔(+11.83%/16只涨停)追入批次5/5止损；8/20医药生物(30+涨停)次日接力山河药辅-6.3%。

数据源：industry_kline_sw（本地 sw_industry.db，离线/幂等，L1每日增量由 update_sw_index_daily.py 维护）。
产出：reports/sector_block_snapshot_{YYYYMMDD}.json —— pre_trade_check_v2 的 R24 段读取（只读，无网络）。

fail-open 设计（对齐 R19/R22 模式）：
  - sw库不可用 / 数据过期>STALE_DAYS个交易日 → 写空 blocked_sectors + note 显式标注，
    闸门端 R24 对缺失/过期快照不拦截（新增规则不是安全闸，缺数据不误伤买入）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

BASE = r"D:\zcodesj"
SW_DB = r"D:\workbuddyspace\databases\sw_industry.db"
SNAP_DIR = os.path.join(BASE, "reports")
UPDATE_SCRIPT = os.path.join(BASE, "update_sw_index_daily.py")

R24_PCT_THRESHOLD = 5.0          # 申万L1单日涨幅阈值（%），主人批准默认5
STALE_DAYS = 4                    # 快照数据超过4个交易日视为过期 → fail-open
STALE_CALENDAR_DAYS = 10          # 交易日滞后的日历日近似上界（含节假日缓冲）


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def refresh_sw_index() -> bool:
    """尝试刷新申万L1日线（update_sw_index_daily.py，幂等~40s）。失败不影响快照，仅记note。"""
    try:
        r = subprocess.run([sys.executable, UPDATE_SCRIPT],
                           capture_output=True, text=True, timeout=240)
        return r.returncode == 0
    except Exception:
        return False


def load_blocked(date_str: str, threshold: float = R24_PCT_THRESHOLD) -> dict:
    """读 industry_kline_sw，返回最新交易日达到高潮阈值的申万L1板块清单。

    2026-08-24 P1修复: ①threshold 参数化——此前 --pct 显式覆盖是死代码(pass)，快照
    内容仍按默认5%算但元数据记调用值；②STALE_DAYS 落地——sw库停更时数天前的板块
    高潮仍以 ok=True 供 R24 消费（文档承诺的过期保护从未实现）。
    """
    try:
        con = sqlite3.connect(f"file:{SW_DB}?mode=ro", uri=True)
        cur = con.execute(
            "SELECT MAX(trade_date) FROM industry_kline_sw WHERE level='L1'")
        latest = cur.fetchone()[0]
        if not latest:
            con.close()
            return {"ok": False, "note": "sw库无L1数据", "latest": None}
        # 过期保护: latest 距快照日超过近似上界(4个交易日≈10日历日) → 不再当有效快照
        try:
            _lag = (datetime.strptime(str(date_str)[:8], "%Y%m%d")
                    - datetime.strptime(str(latest)[:8], "%Y%m%d")).days
        except ValueError:
            _lag = 0
        if _lag > STALE_CALENDAR_DAYS:
            con.close()
            return {"ok": False,
                    "note": f"sw库L1最新{str(latest)[:8]}距快照日{str(date_str)[:8]}"
                            f"滞后{_lag}天(>{STALE_CALENDAR_DAYS}) → 过期fail-open",
                    "latest": latest}
        # 取最新一日 + 其前一交易日（同板块同一 code 最近两根）
        cur = con.execute(
            "SELECT ts_code, name, close, trade_date FROM industry_kline_sw "
            "WHERE level='L1' AND trade_date IN ("
            "  SELECT DISTINCT trade_date FROM industry_kline_sw WHERE level='L1' "
            "  ORDER BY trade_date DESC LIMIT 2) ORDER BY trade_date")
        rows = cur.fetchall()
        con.close()
    except Exception as e:
        return {"ok": False, "note": f"sw库不可用({type(e).__name__}) → fail-open", "latest": None}

    by_code: dict[str, dict] = {}
    dates = sorted({r[3] for r in rows})
    if len(dates) < 2:
        return {"ok": False, "note": f"L1数据不足两日({dates}) → fail-open", "latest": dates[0] if dates else None}
    prev, last = dates[0], dates[1]
    for code, name, close, td in rows:
        by_code.setdefault(code, {"name": name})
        by_code[code][td] = close

    blocked = []
    for code, d in by_code.items():
        if prev not in d or last not in d or not d[prev]:
            continue
        pct = (d[last] - d[prev]) / d[prev] * 100.0
        if pct >= threshold:
            blocked.append({"sw_code": code, "name": d["name"],
                            "pct": round(pct, 2), "trade_date": last})
    return {"ok": True, "note": "", "latest": last,
            "blocked_sectors": blocked, "pct_threshold": threshold}


def main() -> int:
    ap = argparse.ArgumentParser(description="R24板块高潮快照生成器")
    ap.add_argument("--date", default=_today(), help="快照日期 YYYYMMDD（默认今日）")
    ap.add_argument("--pct", type=float, default=R24_PCT_THRESHOLD,
                    help="板块单日涨幅阈值%%（默认5）")
    ap.add_argument("--no-refresh", action="store_true", help="跳过申万L1增量刷新")
    a = ap.parse_args()

    refreshed = False
    if not a.no_refresh:
        refreshed = refresh_sw_index()

    res = load_blocked(a.date, a.pct)

    blocked = res.get("blocked_sectors", [])
    snapshot = {
        "asof_date": a.date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sw_latest_trade_date": res.get("latest"),
        "pct_threshold": a.pct,
        "sw_index_refreshed": refreshed,
        "blocked_sectors": blocked,
        "ok": res.get("ok", False),
        "note": res.get("note", ""),
    }
    os.makedirs(SNAP_DIR, exist_ok=True)
    out = os.path.join(SNAP_DIR, f"sector_block_snapshot_{a.date}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    if blocked:
        print(f"🔴 R24 高潮禁买板块 {len(blocked)} 个（数据日 {res.get('latest')}）:")
        for b in blocked:
            print(f"  - {b['name']}({b['sw_code']}) 单日+{b['pct']}%")
    else:
        print(f"R24 快照已生成：无 ≥{a.pct}% 高潮板块（数据日 {res.get('latest')}）")
    if res.get("note"):
        print(f"[note] {res['note']}")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
