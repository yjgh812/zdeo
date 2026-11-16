# -*- coding: utf-8 -*-
"""厄尔尼诺映射效率实验·盘后对账(一次性, 2026-09-07 15:30后手动运行)

用法: python data/elnino_exp_verify_20260907.py
产出: data/elnino_exp_day1_20260907.json (五股成交与首日快照)
对照实验台账: data/EXPERIMENT_ELNINO_MAP_EFF_20260907.json
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
sys.path.insert(0, str(ROOT))
from westock_client import WeStockClient  # noqa: E402

TARGETS = {
    "000911": "*ST广糖",
    "600737": "中粮糖业",
    "601118": "海南橡胶",
    "002637": "赞宇科技",
    "002011": "盾安环境",
}
OUT = ROOT / "data" / "elnino_exp_day1_20260907.json"


def main():
    ws = WeStockClient()
    positions = ws.positions().get("data", {}).get("positions", [])
    today = date.today().strftime("%Y%m%d")
    rows, missing = [], []
    for code, name in TARGETS.items():
        hit = [p for p in positions if str(p.get("code", "")).lstrip("szsh") == code]
        if not hit:
            missing.append(f"{code} {name}")
            continue
        p = hit[0]
        qty = float(p.get("quantity", 0))
        cost = float(p.get("costPrice", 0))
        cur = float(p.get("currentPrice", 0))
        rows.append({
            "code": code, "name": name, "quantity": qty, "cost_price": cost,
            "current_price": cur, "market_value": round(qty * cur, 2),
            "pnl_pct": round((cur / cost - 1) * 100, 2) if cost else None,
        })
    # executor_orders 今日成交对照
    orders = []
    oj = ROOT / "data" / "executor_orders.jsonl"
    if oj.exists():
        for line in oj.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if str(rec.get("trade_date", "")).replace("-", "") == today and rec.get("action") == "BUY":
                orders.append({k: rec.get(k) for k in ("code", "name", "volume", "price", "status", "order_id") if k in rec})
    snapshot = {
        "verify_date": today, "positions": rows, "missing": missing,
        "orders_today": orders,
        "note": "映射效率对照: 000911(1:1样本) vs 600737/601118(效率0对照); 评判终点见台账",
    }
    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(snapshot, ensure_ascii=False, indent=1))
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()
