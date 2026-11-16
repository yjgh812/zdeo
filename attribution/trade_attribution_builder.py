#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""trade_attribution_builder.py — 盈利归因底表构建器（PREREG_WINNER_ATTR_V11 前置件）。

零交易权研究管线：只读 westock 成交真值 → FIFO 配对闭合交易 → qfq 买点特征快照
→ 写独立研究库 attribution/trade_attribution_base.db（不触碰 attribution.db / trade_records，
后者 owner=naked_trade_audit 合规域）。

数据源（唯一真值）:
  - 成交: westock_client(portfolio_paper_history, range=recent, 3个月窗)
  - 价格: D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db
          stock_daily_meta_qfq(code, date TEXT 'YYYYMMDD', OHLC, volume, amount) 前复权
标签 v2 待接: 集群标签(涨停池历史)、环境档(R37)、RITC 拥挤代理 —— 底表 V1 不做, 预注册声明。

快照特征(买点锚=首笔买入成交日 T):
  ret_5d_before  = close[T]/close[T-5交易日] - 1
  dist_20d_high  = close[T]/max(high[T-19..T]) - 1   (≤0, 越接近0越贴高点)
  vol_20d        = std(近20交易日日收益率, ddof=1)
  amt_20d        = mean(近20交易日成交额)
  fwd_ret_5d/10d = close[T+N]/close[T] - 1 (未来收益, IC 用; 不足窗=空)
  realized_r     = pair pnl_pct / 8.0  (S-5 -8% = 1R 口径)
  cost_anomaly   = westock costPrice<=0 → 1 (百亚型异常标记)

幂等: 全量重建(研究库小, 每次全刷); 失败 fail-loud 结构化退出。
用法: python attribution/trade_attribution_builder.py
"""
import json
import os
import sqlite3
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.append(r"D:\workbuddyspace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_DB = r"D:\zcodesj\attribution\trade_attribution_base.db"
R_UNIT = 8.0  # S-5 -8% = 1R

def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def fail(msg):
    print("[FATAL] %s" % msg, flush=True)
    sys.exit(2)

def norm6(code):
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[2:]
    return c.split(".")[0].zfill(6)

def next_day(d):
    import datetime
    dt = datetime.date(d // 10000, d // 100 % 100, d % 100) + datetime.timedelta(days=1)
    return dt.year * 10000 + dt.month * 100 + dt.day

def main():
    t0 = time.time()
    from westock_client import WeStockClient
    cli = WeStockClient()

    # ---------- 1. 拉全部成交(已成交 only) ----------
    fills, offset, PAGE = [], 0, 200
    while True:
        resp = cli.call("portfolio_paper_history",
                        {"range": "recent", "limit": PAGE, "offset": offset})
        if not resp.get("ok"):
            fail("westock 返回失败: %r" % resp)
        data = resp.get("data") or {}
        recs = data.get("records") or []
        fills.extend(r for r in recs if str(r.get("status")) == "已成交"
                     and str(r.get("direction")) in ("buy", "sell"))
        total = int(data.get("total") or 0)
        offset += PAGE
        if not recs or offset >= total:
            filled_n = len(fills)
            log("成交拉取完成: offset=%d total=%d 已成交=%d" % (offset, total, filled_n))
            break
    if not fills:
        fail("零成交记录, 中止(禁止静默空库)")

    # ---------- 2. FIFO 配对 ----------
    by_code = {}
    for r in fills:
        c = norm6(r["code"])
        by_code.setdefault(c, {"name": r.get("name", ""), "fills": []})
        d = int(str(r["filledAt"])[:10].replace("-", ""))
        hhmmss = str(r["filledAt"])[11:19].replace(":", "")
        by_code[c]["fills"].append({
            "date": d, "ts": str(r["filledAt"]), "dir": r["direction"],
            "qty": float(r["quantity"]), "price": float(r["price"])})

    pairs, open_lots, unmatched_sell_qty = [], [], 0
    for c, v in sorted(by_code.items()):
        lots, sells = [], []
        for f in sorted(v["fills"], key=lambda x: (x["date"], x["ts"])):
            if f["dir"] == "buy":
                lots.append(f)
            else:
                sells.append(f)
        # FIFO: 卖出消耗最早买入批
        si = 0
        li = 0
        while si < len(sells) and li < len(lots):
            lot = lots[li]
            sell = sells[si]
            take = min(lot["qty"], sell["qty"])
            if take > 0:
                pnl_pct = (sell["price"] / lot["price"] - 1) * 100
                pairs.append({
                    "code": c, "name": v["name"],
                    "buy_date": lot["date"], "buy_price": lot["price"],
                    "sell_date": sell["date"], "sell_price": sell["price"],
                    "qty": take, "pnl_pct": pnl_pct,
                    "realized_r": pnl_pct / R_UNIT,
                    "hold_days": sell["date"] - lot["date"] if sell["date"] >= lot["date"] else 0,
                })
                lot["qty"] -= take
                sell["qty"] -= take
            if lot["qty"] <= 0:
                li += 1
            if sell["qty"] <= 0:
                si += 1
        if si < len(sells) and sells[si]["qty"] > 0:
            unmatched_sell_qty += sells[si]["qty"]
        open_lots.extend(lots[li:] if lots[li:] else lots[li:])
    if unmatched_sell_qty > 0:
        log("警告: 窗口前建仓的卖出 %d 股无法配对(仅计数不造数)" % unmatched_sell_qty)

    # ---------- 3. qfq 买点快照 ----------
    qconn = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    def bars(code, before_d):
        rows = qconn.execute(
            "SELECT date, close, high, amount FROM stock_daily_meta_qfq "
            "WHERE code=? AND date<=? ORDER BY date DESC LIMIT 40",
            (code, str(before_d))).fetchall()
        rows.reverse()
        return rows  # [(date,close,high,amount)] 升序, 含T
    def snapshot(code, anchor):
        rows = bars(code, anchor)
        if len(rows) < 6:
            return {"ret_5d_before": None, "dist_20d_high": None, "vol_20d": None,
                    "amt_20d": None, "fwd_ret_5d": None, "fwd_ret_10d": None,
                    "snap_n": len(rows)}
        closes = [r[1] for r in rows]
        highs = [r[2] for r in rows]
        amts = [r[3] for r in rows]
        n = len(closes)
        ret5 = closes[-1] / closes[-6] - 1 if n >= 6 else None
        hi20 = max(highs[-20:]) if n >= 20 else max(highs)
        d20 = closes[-1] / hi20 - 1
        rets = [closes[i] / closes[i-1] - 1 for i in range(max(1, n-20), n)]
        v20 = statistics.stdev(rets) if len(rets) >= 2 else None
        a20 = sum(amts[-20:]) / min(n, 20)
        # 未来收益: T 之后到最新
        frows = qconn.execute(
            "SELECT date, close FROM stock_daily_meta_qfq "
            "WHERE code=? AND date>? ORDER BY date ASC LIMIT 12",
            (code, str(anchor))).fetchall()
        f5 = frows[4][1] / closes[-1] - 1 if len(frows) >= 5 else None
        f10 = frows[9][1] / closes[-1] - 1 if len(frows) >= 10 else None
        return {"ret_5d_before": ret5, "dist_20d_high": d20, "vol_20d": v20,
                "amt_20d": a20, "fwd_ret_5d": f5, "fwd_ret_10d": f10, "snap_n": n}

    # ---------- 4. 组装行 ----------
    def attach(snap, row):
        row.update(snap)
        return row

    rows_out = []
    for p in pairs:
        p["anchor"] = p["buy_date"]
        p.update(snapshot(p["code"], p["anchor"]))
        p["row_type"] = "closed"
        rows_out.append(p)
    for c, v in sorted(by_code.items()):
        # 重放FIFO得残余 lots(与配对循环同口径)
        lots, sells = [], []
        for f in v["fills"]:
            if f["dir"] == "buy":
                lots.append(dict(f))
            else:
                sells.append(dict(f))
        li, si = 0, 0
        while si < len(sells) and li < len(lots):
            take = min(lots[li]["qty"], sells[si]["qty"])
            lots[li]["qty"] -= take
            sells[si]["qty"] -= take
            if lots[li]["qty"] <= 0:
                li += 1
            if sells[si]["qty"] <= 0:
                si += 1
        for lot in lots[li:]:
            if lot["qty"] <= 0:
                continue
            row = {"code": c, "name": v["name"], "buy_date": lot["date"],
                   "buy_price": lot["price"], "qty": lot["qty"],
                   "sell_date": None, "sell_price": None, "pnl_pct": None,
                   "realized_r": None, "hold_days": None,
                   "row_type": "open", "anchor": lot["date"]}
            row.update(snapshot(c, lot["date"]))
            rows_out.append(row)
    if not rows_out:
        fail("零底表行, 中止")

    # ---------- 5. 写库(全量重建, 参数绑定) ----------
    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)
    oconn = sqlite3.connect(OUT_DB)
    oconn.execute("""CREATE TABLE trade_base (
        code TEXT, name TEXT, row_type TEXT, anchor INTEGER,
        buy_date INTEGER, buy_price REAL, sell_date INTEGER, sell_price REAL,
        qty REAL, pnl_pct REAL, realized_r REAL, hold_days INTEGER,
        ret_5d_before REAL, dist_20d_high REAL, vol_20d REAL, amt_20d REAL,
        fwd_ret_5d REAL, fwd_ret_10d REAL, snap_n INTEGER,
        PRIMARY KEY (code, row_type, buy_date, sell_date, buy_price))""")
    oconn.executemany(
        "INSERT OR REPLACE INTO trade_base VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["code"], r["name"], r["row_type"], r["anchor"], r["buy_date"],
          r["buy_price"], r["sell_date"], r["sell_price"], r["qty"],
          r["pnl_pct"], r["realized_r"], r["hold_days"],
          r["ret_5d_before"], r["dist_20d_high"], r["vol_20d"],
          r["amt_20d"], r["fwd_ret_5d"], r["fwd_ret_10d"], r["snap_n"])
         for r in rows_out])
    oconn.commit()
    n = oconn.execute("SELECT COUNT(*) FROM trade_base").fetchone()[0]
    oconn.close()
    log("底表写入完成: %d 行 -> %s" % (n, OUT_DB))
    log("汇总: closed=%d open=%d 耗时%.0fs" % (sum(1 for r in rows_out if r["row_type"]=="closed"),
        sum(1 for r in rows_out if r["row_type"]=="open"), time.time()-t0))


if __name__ == "__main__":
    main()
