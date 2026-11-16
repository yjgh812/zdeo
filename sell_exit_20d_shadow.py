# -*- coding: utf-8 -*-
"""卖出侧 20 日到期检查点影子件（PREREG_SELL_EXIT_20D_20260920，2026-09-20 落地）

来源: 第三期全维度自省 M3 + prereg data/prereg_sell_exit_20d_20260920.json（指纹 sellexit20d-a3f7c2e1）
证据: reports/exit_v2_20260919.md（n=3209：20 日到期=最优可操作出场，MA20 系全灭）
定位: 只读影子零交易权。不动三元裁决（S-5 自动/趋势走坏提请/主人指令）；不产生任何卖出指令。
语义: 持仓满 20 交易日的标的，登记「第 20 交易日假想出场 vs 继续持有至评估日」收益差；
      S-5 域标的（期内低点破-8%）单独标记不入主判定。
数据: westock 实时持仓（唯一真值）+ attribution trade_records 首笔 BUY 锚 + qfq 日线（workbuddyspace 主库）
台账: data/sell_exit_20d_shadow.jsonl 按 date UPSERT 幂等。
用法: python sell_exit_20d_shadow.py [--eval-date YYYYMMDD]
退出码: 0 成功 / 2 数据通道失败
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
WS_PATH = r"D:\workbuddyspace"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = os.path.join(ROOT, "data", "sell_exit_20d_shadow.jsonl")
HOLD_DAYS = 20

sys.path.insert(0, ROOT)
sys.path.insert(0, WS_PATH)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_positions() -> list[dict]:
    from westock_client import WeStockClient
    resp = WeStockClient().call("portfolio_paper_positions", {})
    pos = (resp.get("data") or {}).get("positions") or []
    if not pos:
        raise RuntimeError("westock 持仓返回空")
    return [{"code": p["code"][-6:], "name": p["name"],
             "cost": float(p.get("costPrice") or 0), "price": float(p.get("currentPrice") or 0)}
            for p in pos]


def entry_dates() -> dict[str, str]:
    """首笔买入锚: executor_orders.jsonl 首笔 BUY(主, 覆盖41/42) -> trade_records BUY(兜底);
    两源皆无(App端绕通道等)不入=NA, 不伪造。"""
    out: dict[str, str] = {}
    eo_path = os.path.join(ROOT, "data", "executor_orders.jsonl")
    try:
        firsts: dict[str, str] = {}
        for line in open(eo_path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("action") != "BUY":
                continue
            code = str(d.get("code", ""))[-6:]
            td = str(d.get("trade_date", "")).replace("-", "")[:8]
            if code and td and (code not in firsts or td < firsts[code]):
                firsts[code] = td
        out.update(firsts)
    except Exception as e:
        print(f"[EXIT20-WARN] executor_orders 读取失败: {e}")
    try:
        con = sqlite3.connect("file:" + os.path.join(ROOT, "attribution", "attribution.db").replace("\\", "/") + "?mode=ro", uri=True)
        for code, td in con.execute(
                "SELECT code, MIN(trade_date) FROM trade_records WHERE action='BUY' GROUP BY code"):
            code = str(code)[-6:]
            td = str(td).replace("-", "")[:8]
            if code not in out or td < out[code]:
                out[code] = td
        con.close()
    except Exception as e:
        print(f"[EXIT20-WARN] trade_records 读取失败: {e}")
    return out


def qfq_series(con, code: str) -> list[tuple]:
    """[(date, close, low)] 升序; qfq 主库"""
    return [(str(r[0]), float(r[1]), float(r[2])) for r in con.execute(
        "SELECT date, close, low FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (code,))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    today = a.eval_date

    try:
        positions = load_positions()
    except Exception as e:
        print(f"[EXIT20-FAIL] 持仓通道失败: {e}")
        return 2
    ent = entry_dates()
    con = sqlite3.connect("file:" + QFQ_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=60)

    rows, na_entry, young, s5, mature = [], [], [], [], []
    for p in positions:
        code = p["code"]
        e0 = ent.get(code)
        if not e0:
            na_entry.append(f"{code}{p['name']}")
            continue
        series = [x for x in qfq_series(con, code) if x[0] <= today]
        idx0 = next((i for i, x in enumerate(series) if x[0] >= e0), None)
        if idx0 is None or len(series) - idx0 < HOLD_DAYS:
            young.append((code, p["name"], len(series) - idx0 if idx0 is not None else 0))
            continue
        cost_q = series[idx0][1]  # 首笔日收盘近似锚(qfq 口径, 影子内部一致口径)
        t20_close = series[idx0 + HOLD_DAYS - 1][1]
        cur = series[-1][1]
        min_low = min(x[2] for x in series[idx0:])
        row = {
            "code": code, "name": p["name"], "entry": e0,
            "held_days": len(series) - idx0,
            "t20_close_qfq": round(t20_close, 3), "cur_qfq": round(cur, 3),
            "diff_t20_vs_hold": round(t20_close / cur - 1, 4),  # >0=20日出场优于继续持有
            "cost_actual": p["cost"], "price_actual": p["price"],
            "s5_hit": bool(min_low / cost_q <= 0.92),
        }
        rows.append(row)
        (s5 if row["s5_hit"] else mature).append(row)
    con.close()

    valid = mature  # 主判定样本=S-5 域外
    diffs = sorted(r["diff_t20_vs_hold"] for r in valid)
    med = diffs[len(diffs) // 2] if diffs else None
    win = sum(1 for d in diffs if d > 0)
    summary = {
        "date": today, "positions_total": len(positions),
        "valid_n": len(valid), "s5_hit_n": len(s5), "young_n": len(young), "na_entry_n": len(na_entry),
        "diff_median": med, "diff_win_rate": round(win / len(valid), 3) if valid else None,
        "prereg": "PREREG_SELL_EXIT_20D_20260920(sellexit20d-a3f7c2e1)",
        "note": "影子期禁改判据; 判定需 valid_n>=30; S-5域外样本才算; diff>0=20日出场优于持有",
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rows": rows,
    }
    # UPSERT by date
    old = []
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            line = line.strip()
            if line:
                d = json.loads(line)
                if d.get("date") != today:
                    old.append(d)
    old.append(summary)
    with open(LEDGER, "w", encoding="utf-8") as fh:
        for d in old:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")

    print(f"[EXIT20-OK] 持仓{len(positions)} 有效{len(valid)} s5域{len(s5)} 未满20日{len(young)} 无买入锚{len(na_entry)}"
          f" | diff中位={med} 胜率={summary['diff_win_rate']}")
    if na_entry:
        print(f"[EXIT20-NA] 无买入锚(不入判定): {'、'.join(na_entry[:8])}{'…' if len(na_entry) > 8 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
