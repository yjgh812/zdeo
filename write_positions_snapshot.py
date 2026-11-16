#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""write_positions_snapshot.py — westock 实时持仓 → data/positions_latest.json（R30b 数据源）

背景（2026-08-28 E79/R30b 收口）：tulong_buy_whitelist 的 R30b 组合级行业占比守卫依赖
data/positions_latest.json，但此前无任何生产者 → 守卫静默失效。本脚本供 09:10 盘前链
在取 westock 持仓后调用，把实时持仓写入快照（westock=持仓唯一真值源）。

格式与白名单 _load_positions_snapshot() 完全对齐：
  {"positions": [{"code": "600928", "quantity": 1600, "costPrice": 3.59}, ...],
   "snapshot_at": ISO, "source": "westock_live"}

用法：python write_positions_snapshot.py
退出码：0=写入成功 / 3=westock 真值源拉取失败(fail-closed，拒绝假快照)
"""
from __future__ import annotations

import json
import os
import sys
import datetime

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace")

OUT = r"D:\zcodesj\data\positions_latest.json"


def main() -> int:
    try:
        from westock_client import WeStockClient
        ws = WeStockClient()
        data = ws.positions().get("data", {})
        positions = data.get("positions", [])
    except Exception as e:
        print(f"[FAIL-CLOSED] westock 持仓拉取失败({type(e).__name__}: {e})——拒绝写假快照",
              file=sys.stderr)
        return 3
    if not positions:
        print("[WARN] westock 返回空持仓（现金账户也可能正常）——仍写空快照")
    doc = {
        "positions": positions,
        "snapshot_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": "westock_live",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    print(f"[OK] 持仓快照写入 {OUT}（{len(positions)} 只）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
