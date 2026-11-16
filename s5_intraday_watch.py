#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S-5盘中预警档(呈请5, 2026-10-14主人「继续」令) — 纯提示层toast, 零交易权
背景: S-5执行面=stop_loss_executor 09:35/14:45两采样点, 点间盘中深跌不可见
(10/9克来机电盘中-9.9%无人见; 10/13早班调度缺位当日全盲)。本件调度每15分钟拉
westock实时持仓, 任一持仓profitRate<=-8%(S-5同口径硬线)即toast预警, 每日每码至多一次。
边界: 禁止自动卖出禁触trade_executor禁改S-5执行链节奏; 是否提前处置由主人裁决
(立即走trade_executor止损链 或 等14:45 Sentinel自动采样执行); MCP失败exit2 fail-loud。
用法: python s5_intraday_watch.py            # 调度常态(15分钟, 09:35-14:55)
      python s5_intraday_watch.py --force    # 忽略休市日守卫(测试)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\zcodesj")
sys.path.insert(0, str(ROOT))

LEDGER = ROOT / "data" / "s5_intraday_watch_ledger.jsonl"
RUNLOG = ROOT / "data" / "s5_intraday_watch_run.log"
LINE = -8.0  # S-5同口径硬线(%), 不加缓冲带


def pick_breaches(positions: list[dict]) -> list[dict]:
    """S-5线筛选: profitRate<=-8%的持仓。字段缺失/None不误报。"""
    out = []
    for p in positions:
        v = p.get("float_pnl_pct")
        if isinstance(v, (int, float)) and v <= LINE:
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略休市日守卫(测试)")
    args = ap.parse_args()
    today = datetime.now().strftime("%Y%m%d")
    if not args.force:
        from trade_day_guard import is_trading_day
        if is_trading_day(today) is False:
            print("[s5-intraday] 休市日 → 跳过")
            return 0
    from stop_loss_executor import _fetch_positions_via_mcp
    positions = _fetch_positions_via_mcp()
    if positions is None:
        RUNLOG.open("a", encoding="utf-8").write(
            f"{datetime.now():%Y-%m-%d %H:%M:%S} MCP_FAIL exit2\n")
        print("[s5-intraday] westock MCP 取数失败 → exit 2 (fail-loud)")
        return 2
    done = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if r.get("date") == today:
                    done.add(r["code"])
            except Exception:
                pass
    breaches = [p for p in pick_breaches(positions) if p["code"] not in done]
    new_rows = [{"date": today, "code": p["code"], "name": p["name"],
                 "pnl_pct": p["float_pnl_pct"], "price": p["current_price"],
                 "qty": p["quantity"], "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                for p in breaches]
    if new_rows:
        with LEDGER.open("a", encoding="utf-8") as fp:
            for r in new_rows:
                fp.write(json.dumps(r, ensure_ascii=False) + "\n")
        content = "\n".join(
            f"{p['name']}({p['code']}) 现价{p['current_price']:.2f} 成本{p['cost_price']:.2f} "
            f"盈亏{p['float_pnl_pct']:.2f}% — S-5线已破"
            for p in breaches)
        title = f"S-5盘中预警: {len(breaches)}只破-8%线(提请制)"
        try:
            import subprocess
            r = subprocess.run(
                [sys.executable, str(ROOT / "notify.py"), "send",
                 "--title", title, "--content",
                 content + "\n  14:45 Sentinel将自动采样执行; 提前处置走trade_executor止损链, 数量裁决归主人",
                 "--level", "warn", "--channel", "toast"],
                capture_output=True, text=True, timeout=60, cwd=str(ROOT))
            toast_rc = r.returncode
        except Exception as e:
            toast_rc = f"fail:{e}"
    else:
        toast_rc = "-"
    line = (f"{datetime.now():%Y-%m-%d %H:%M:%S} pos={len(positions)} "
            f"breach_new={len(new_rows)} toast={toast_rc}")
    RUNLOG.open("a", encoding="utf-8").write(line + "\n")
    print("[s5-intraday]", line)
    for p in breaches:
        print(f"  {p['name']}({p['code']}) {p['float_pnl_pct']:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
