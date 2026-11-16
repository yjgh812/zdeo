# -*- coding: utf-8 -*-
"""持仓侧逃生门预警 v1 (20261007, PD-036④主人批准·提请制·零交易权)
两通道(判据源自十三案例卖出侧五件套, 全市场大样本验证):
  S1 断板即卖预警: 持仓股 T-1 处连板(连续涨停>=2板) ∧ 最新交易日未涨停 → 提请
     (事件研究 n≈10.9万: 4板断后 fwd20 中位 -15.28%/胜率23.3%, 高度单调)
  S2 反抽逃生门预警: 持仓股 20日内回撤>=15% ∧ 现价较20日低点反弹>=8% ∧ 仍低于20日高 → 提请
     (反抽后中位再跌 -5.1~-7.5%/胜率仅35% = 反抽位是卖点)
边界: 提请制弹清单待主人确认, 不自动卖, 不碰 trade_executor; 台账 data/exit_gate_ledger.jsonl
(date+code+type 幂等); 持仓=data/holdings_codes_latest.json 快照(真值以westock实时为准);
非交易日自动跳过; 无命中静默。
用法: python exit_gate_watch.py [--force]
"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"D:\zcodesj")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
LEDGER = ROOT / "data" / "exit_gate_ledger.jsonl"
HOLDINGS = ROOT / "data" / "holdings_codes_latest.json"
ZT = 9.5
S1_MIN_BOARD = 2      # 连板>=2才预警(首板断板噪声大)
S2_DRAWDOWN = 0.15    # 20日内回撤>=15%
S2_BOUNCE = 0.08      # 较20日低点反弹>=8%


def load_holdings() -> list[dict]:
    d = json.loads(HOLDINGS.read_text(encoding="utf-8"))
    return d.get("positions", [])


def check_code(con, code6: str) -> list[dict]:
    hits = []
    df = pd.read_sql_query(
        "SELECT date,close,change_pct FROM stock_daily_meta_qfq "
        "WHERE code=? AND date>='20260601' ORDER BY date", con, params=(code6,))
    if len(df) < 25:
        return hits
    c = df["close"].astype(float).to_numpy()
    chg = df["change_pct"].astype(float).to_numpy()
    dates = df["date"].astype(int).tolist()
    # S1 断板: T-1连板>=2 ∧ 最新日未涨停
    zt = chg >= ZT
    if len(zt) >= 2 and zt[-1] is False and zt[-2]:
        board = 0
        i = len(zt) - 2
        while i >= 0 and zt[i]:
            board += 1
            i -= 1
        if board >= S1_MIN_BOARD:
            hits.append({"type": "S1_断板", "board": board,
                         "note": f"{board}连板后断板(最新日{chg[-1]:+.1f}%) — 断板后20日中位-15.3%/胜率23%(n=10.9万)"})
    # S2 反抽逃生: 20日窗回撤>=15% ∧ 较低点反弹>=8% ∧ 低于20日高
    w = c[-20:]
    if len(w) >= 15:
        hi, lo = float(w.max()), float(w.min())
        dd = 1 - c[-1] / hi if hi > 0 else 0
        bounce = c[-1] / lo - 1 if lo > 0 else 0
        if dd >= S2_DRAWDOWN and bounce >= S2_BOUNCE and c[-1] < hi:
            hits.append({"type": "S2_反抽逃生", "dd": round(dd * 100, 1), "bounce": round(bounce * 100, 1),
                         "note": f"20日回撤{dd*100:.0f}%后反弹{bounce*100:.0f}% — 反抽位中位再跌5~7.5%/胜率35%"})
    for h in hits:
        h["date"] = dates[-1]
    return hits


def main() -> int:
    force = "--force" in sys.argv
    today = __import__("datetime").datetime.now().strftime("%Y%m%d")
    if not force:
        from trade_day_guard import is_trading_day
        if is_trading_day(today) is False:
            print("[exit-gate] 休市日 → 跳过")
            return 0
    pos = load_holdings()
    if not pos:
        print("[exit-gate] 持仓快照空 → 跳过(真值以westock实时为准)")
        return 0
    con = sqlite3.connect(QFQ, uri=True)
    rows = []
    for p in pos:
        code6 = str(p.get("code", "")).replace("sh", "").replace("sz", "").replace("bj", "")
        nm = p.get("name", code6)
        for h in check_code(con, code6):
            rows.append({"date": today, "code": code6, "name": nm, **h})
    con.close()

    old = []
    if LEDGER.exists():
        old = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    keys_old = {(r["date"], r["code"], r["type"]) for r in old}
    new = [r for r in rows if (r["date"], r["code"], r["type"]) not in keys_old]
    if new:
        with LEDGER.open("a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    total = len(rows)
    if total == 0:
        print("[exit-gate] 持仓无断板/反抽逃生命中")
        return 0
    print(f"[exit-gate] 命中{total}笔(新增{len(new)}):")
    for r in rows:
        print(f"  {r['code']} {r['name']} [{r['type']}] {r['note']}")
    print("(提请制: 待主人裁决, 不自动卖; 台账exit_gate_ledger)")
    try:
        sys.path.insert(0, str(ROOT))
        from notify import send
        send(title="逃生门预警", content="; ".join(f"{r['name']}{r['type']}" for r in rows[:5]), level="warn")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
