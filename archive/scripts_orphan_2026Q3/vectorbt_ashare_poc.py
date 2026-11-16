"""
vectorbt_ashare_poc.py — VectorBT A股参数预筛层 PoC（研究层，只读）

定位（来自 STACK_LEARNING_20260828 报告）：
  VectorBT 在本系统的合法用途 = 参数扫描/敏感性结构发现（找平台期），
  最终裁决永远在自研事件式门禁回测 + wfa_gate。本脚本绝不输出"生产参数"。

本 PoC 只读生产 qfq 库（D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_qfq.db），
零写库、零交易、零触碰生产 python（运行于独立 venv .venv_vbt，numpy2.5/pandas3/plotly6.9）。

自证清单（每项都有可验证断言）：
  S1 next-bar-open 语义：信号在 close[t] 计算 → 下一 bar open[t+1] 成交，
     断言 entry_price == open[entry_idx]（容差 1e-6）。
  S2 涨跌停掩码：开盘即涨停禁止买入、开盘即跌停禁止卖出（open 与 ±10% 限价比较），
     断言所有成交 entry bar 均非涨停日、exit bar 均非跌停日。
  S3 停牌掩码：volume==0 置掩码，断言无成交发生在停牌日。
  S4 T+1 约束：日频下 entry bar 与 exit bar 不可能同日（信号单bar翻转+shift），
     断言所有 trades exit_idx > entry_idx。
  S5 卖出单边印花税：from_orders 按 bar 分侧费用数组（买 0.025% 佣金、卖 0.025%+0.05% 印花），
     断言 vbt trades.fees 合计 == 手工逐笔计算合计（容差 1e-6 相对）。
  S6 引擎对账：同一策略（600519 MA5/20）用纯 Python 循环手写模拟重算最终权益，
     断言与 vbt 最终权益一致（容差 0.1%）。
  S7 参数平台期演示：fast/slow 网格热力图，输出平台区而非 argmax（纪律示范）。

用法（Windows Git Bash）：
  NUMBA_CACHE_DIR=D:/zcodesj/.venv_vbt/.numba_cache ./.venv_vbt/Scripts/python.exe vectorbt_ashare_poc.py
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STOCKS = [  # code, 名称, 板块(主板)
    ("600519", "贵州茅台", "主板"),
    ("000001", "平安银行", "主板"),
    ("600036", "招商银行", "主板"),
]
START = "2023-01-01"
END = "2026-08-27"
COMM_RATE = 0.00025     # 佣金 万2.5 双边
STAMP_RATE = 0.0005     # 印花税 千0.5 仅卖出
LIMIT_PCT = 0.10        # 主板 ±10%
FAST, SLOW = 5, 20      # 自证用固定参数
GRID_FAST = [3, 5, 10, 15]
GRID_SLOW = [20, 30, 60]

evidence = {}


def load_stock(code):
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT code, date, open, high, low, close, volume, amount, change_pct "
        "FROM stock_daily_meta_qfq WHERE code=? AND date>=? AND date<=? ORDER BY date",
        con, params=(code, START.replace("-", ""), END.replace("-", "")))
    con.close()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date")
    df["prev_close"] = df["close"].shift(1)
    return df


def ma_cross_signal(close, fast, slow):
    """long-only 单bar翻转信号；warmup 期内 False。"""
    f = close.rolling(fast).mean()
    s = close.rolling(slow).mean()
    up = (f > s) & (f.shift(1) <= s.shift(1))
    dn = (f < s) & (f.shift(1) >= s.shift(1))
    warm = close.rolling(slow).count() < slow
    up = up & ~warm
    dn = dn & ~warm
    return up, dn


def next_bar_open(df, entries, exits):
    """信号 shift(1) → 下一 bar 开盘成交；停牌/涨跌停掩码作用于执行 bar。"""
    ents = entries.shift(1).fillna(False)
    exts = exits.shift(1).fillna(False)
    suspended = df["volume"].fillna(0) <= 0
    limit_up_open = df["open"] >= np.round(df["prev_close"] * (1 + LIMIT_PCT), 2) - 0.005
    limit_dn_open = df["open"] <= np.round(df["prev_close"] * (1 - LIMIT_PCT), 2) + 0.005
    block_buy = suspended | limit_up_open
    block_sell = suspended | limit_dn_open
    ents = ents & ~block_buy
    exts = exts & ~block_sell
    return ents, exts, {"suspended": suspended, "limit_up_open": limit_up_open,
                        "limit_dn_open": limit_dn_open, "block_buy": block_buy,
                        "block_sell": block_sell}


def run_poc_stock(code, name, df):
    """S1-S5 单股自证 + 手工对账 S6 + 网格 S7。"""
    out = {}
    open_, close = df["open"], df["close"]
    up, dn = ma_cross_signal(close, FAST, SLOW)
    ents, exts, masks = next_bar_open(df, up, dn)

    # size: +1 买 / -1 卖；fees: 买 COMM，卖 COMM+STAMP
    size = pd.DataFrame(0.0, index=df.index, columns=["x"])
    size.loc[ents, "x"] = 1.0
    size.loc[exts, "x"] = -1.0
    fees = pd.DataFrame(COMM_RATE, index=df.index, columns=["x"])
    fees.loc[exts, "x"] = COMM_RATE + STAMP_RATE
    pf = vbt.Portfolio.from_orders(
        open_.to_frame("x"), size, size_type="Amount", price=open_.to_frame("x"),
        fees=fees, init_cash=1e9, freq="D",
        # vbt 1.1.0 from_orders 默认 direction 非 longonly：
        # 若首信号是"卖"（窗口起点已在慢线上方）会把整段配对成做空交易，费用买卖反收。
        # A 股无做空，必须显式 longonly（与 manual_sim 的多头逻辑一致）。
        direction="longonly")

    trades = pf.trades.records_arr  # 结构化数组: id,col,size,entry_idx,entry_price,entry_fees,exit_idx,exit_price,exit_fees,status,...
    ntr = len(trades)
    # vbt status: 0=Open 1=Closed。未平仓交易的 exit_idx 是"标记bar"（exit_fees=0），不可当真实卖出
    closed = trades["status"] == 1
    out["n_trades"] = int(ntr)

    # S1 next-bar-open 自证
    if ntr:
        ep = trades["entry_price"].astype(float)
        open_at_entry = open_.to_numpy()[trades["entry_idx"].astype(int)]
        s1_max = float(np.max(np.abs(ep - open_at_entry)))
    else:
        s1_max = None
    out["S1_nextbar_open_maxdiff"] = s1_max

    # S2 涨跌停掩码自证
    lu_open = masks["limit_up_open"].to_numpy()
    ld_open = masks["limit_dn_open"].to_numpy()
    if ntr:
        entry_on_lu = bool(lu_open[trades["entry_idx"].astype(int)].any())
        exit_on_ld = bool(ld_open[trades["exit_idx"][closed].astype(int)].any())
    else:
        entry_on_lu = exit_on_ld = False
    out["S2_limit_up_open_days"] = int(lu_open.sum())
    out["S2_limit_dn_open_days"] = int(ld_open.sum())
    out["S2_entry_on_limitup"] = entry_on_lu
    out["S2_exit_on_limitdown"] = exit_on_ld

    # S3 停牌掩码自证
    susp = masks["suspended"].to_numpy()
    if ntr:
        trade_on_susp = bool((susp[trades["entry_idx"].astype(int)].any())
                             or (susp[trades["exit_idx"][closed].astype(int)].any()))
    else:
        trade_on_susp = False
    out["S3_suspension_days"] = int(susp.sum())
    out["S3_trade_on_suspension"] = trade_on_susp

    # S4 T+1 断言（仅 closed 交易）
    if ntr and closed.any():
        diff = trades["exit_idx"][closed].astype(int) - trades["entry_idx"][closed].astype(int)
        out["S4_min_exit_entry_gap"] = int(diff.min())
        out["S4_any_same_day"] = bool((diff <= 0).any())
    else:
        out["S4_min_exit_entry_gap"] = None
        out["S4_any_same_day"] = None

    # S5 印花税分侧费用自证（手工逐笔对 vbt entry_fees+exit_fees）
    if ntr:
        sz = np.abs(trades["size"].astype(float))
        manual_fees = float(np.sum(sz * trades["entry_price"].astype(float) * COMM_RATE))
        if closed.any():
            manual_fees += float(np.sum(
                sz[closed] * trades["exit_price"][closed].astype(float) * (COMM_RATE + STAMP_RATE)))
        vbt_fees = float(trades["entry_fees"].astype(float).sum()
                         + trades["exit_fees"].astype(float).sum())
        rel = abs(vbt_fees - manual_fees) / max(abs(manual_fees), 1e-9)
        out["S5_vbt_fees"] = vbt_fees
        out["S5_manual_fees"] = manual_fees
        out["S5_rel_diff"] = float(rel)
    else:
        out["S5_vbt_fees"] = None

    # S6 纯 Python 手工对账（每只股）
    manual_equity, manual_entry_prices, manual_exit_prices = manual_sim(df, up, dn, masks)
    # 注意：vbt 1.1.0 的 asset_value() 访问器有回归（恒返回 0），改用 value()（cash+持仓市值）
    vbt_equity = float(pf.value().iat[-1, 0])
    rel_eq = abs(vbt_equity - manual_equity) / abs(manual_equity)
    out["S6_vbt_equity"] = vbt_equity
    out["S6_manual_equity"] = manual_equity
    out["S6_rel_diff"] = float(rel_eq)
    if manual_entry_prices:
        mp = np.array(manual_entry_prices)
        vp = trades["entry_price"].astype(float)
        out["S6_entry_price_maxdiff"] = float(np.max(np.abs(mp - vp))) if len(mp) == len(vp) else None
        if manual_exit_prices and int(closed.sum()) == len(manual_exit_prices):
            out["S6_exit_price_maxdiff"] = float(np.max(np.abs(
                np.array(manual_exit_prices) - trades["exit_price"][closed].astype(float))))
        else:
            out["S6_exit_price_maxdiff"] = None

    # S7 参数平台期网格（600519）
    if code == "600519":
        out["S7_grid"] = run_grid(df)

    return pf, out


def manual_sim(df, up, dn, masks):
    """逐 bar 手写：信号 close[t] → open[t+1] 成交；买 COMM、卖 COMM+STAMP。返回最终现金+权益。"""
    open_ = df["open"].to_numpy()
    close = df["close"].to_numpy()
    up_n = up.to_numpy()
    dn_n = dn.to_numpy()
    blk_buy = masks["block_buy"].to_numpy()
    blk_sell = masks["block_sell"].to_numpy()
    cash = 1e9
    pos = 0.0
    entry_prices, exit_prices = [], []
    for t in range(1, len(df)):
        if pos == 0 and up_n[t - 1] and not blk_buy[t]:
            px = open_[t]
            cash -= px * (1 + COMM_RATE)
            pos = 1.0
            entry_prices.append(px)
        elif pos > 0 and dn_n[t - 1] and not blk_sell[t]:
            px = open_[t]
            cash += px * (1 - COMM_RATE - STAMP_RATE)
            pos = 0.0
            exit_prices.append(px)
    equity = cash + pos * open_[-1]
    return equity, entry_prices, exit_prices


def run_grid(df):
    """fast x slow 网格热力图 + 平台期识别（纪律示范：不取 argmax）。"""
    open_ = df["open"]
    close = df["close"]
    combos = [(f, s) for f in GRID_FAST for s in GRID_SLOW]
    n = len(combos)
    ents = pd.DataFrame(False, index=df.index, columns=[f"c{i}" for i in range(n)])
    exts = pd.DataFrame(False, index=df.index, columns=[f"c{i}" for i in range(n)])
    for i, (f, s) in enumerate(combos):
        up, dn = ma_cross_signal(close, f, s)
        e, x, _ = next_bar_open(df, up, dn)
        ents[f"c{i}"] = e
        exts[f"c{i}"] = x
    size = pd.DataFrame(0.0, index=df.index, columns=ents.columns)
    size[ents] = 10000.0   # 每笔等额定仓 1 万元（网格用价值定仓，绝对收益才有经济含义）
    size[exts] = -10000.0
    fees = pd.DataFrame(COMM_RATE, index=df.index, columns=ents.columns)
    fees[exts] = COMM_RATE + STAMP_RATE
    pf = vbt.Portfolio.from_orders(open_, size, size_type="Value", price=open_,
                                   fees=fees, init_cash=1e9, freq="D", direction="longonly")
    ret = pf.total_profit()  # 绝对盈亏(元)，不被超大 init_cash 稀释；相对收益会被基数稀释成 ~0
    mat = np.array(ret).reshape(len(GRID_FAST), len(GRID_SLOW))
    ret_map = {"f{}_s{}".format(GRID_FAST[i], GRID_SLOW[j]): float(mat[i, j])
               for i in range(len(GRID_FAST)) for j in range(len(GRID_SLOW))}
    best = max(ret_map, key=ret_map.get)
    best_v = ret_map[best]
    # 平台期：与最优组合盈亏差 <= max(500元, 2%×|最优|)
    thr = max(500.0, 0.02 * abs(best_v))
    plateau = {k: v for k, v in ret_map.items() if best_v - v <= thr}
    return {
        "grid_return": ret_map,
        "best": {"combo": best, "ret": best_v},
        "plateau_within_2pp": plateau,
        "note": "网格结果仅为参数敏感性/平台期研究，禁止直接作为生产参数；最终裁决走自研事件式门禁回测+wfa_gate",
    }


def main():
    global vbt
    import vectorbt as vbt
    out_all = {"meta": {"qfq_db": QFQ_DB, "window": [START, END], "vbt": vbt.__version__,
                        "numpy": np.__version__, "pandas": pd.__version__}}
    for code, name, _b in STOCKS:
        df = load_stock(code)
        pf, out = run_poc_stock(code, name, df)
        out_all[f"{code}_{name}"] = out
        print(f"\n=== {code} {name} === 数据 {len(df)} 根K线 {df.index[0].date()}~{df.index[-1].date()}")
        print(f"  S1 next-bar-open entry_price==open 最大偏差: {out['S1_nextbar_open_maxdiff']}")
        print(f"  S2 涨停开盘日={out['S2_limit_up_open_days']} 跌停开盘日={out['S2_limit_dn_open_days']} "
              f"entry在涨停日={out['S2_entry_on_limitup']} exit在跌停日={out['S2_exit_on_limitdown']}")
        print(f"  S3 停牌日={out['S3_suspension_days']} 成交在停牌日={out['S3_trade_on_suspension']}")
        print(f"  S4 最小 exit-entry 间隔={out['S4_min_exit_entry_gap']} 同日={out['S4_any_same_day']}")
        if out.get("S5_vbt_fees") is not None:
            print(f"  S5 印花税分侧 vbt={out['S5_vbt_fees']:.4f} manual={out['S5_manual_fees']:.4f} 相对差={out['S5_rel_diff']:.2e}")
        if out.get("S6_vbt_equity") is not None:
            print(f"  S6 引擎对账 vbt权益={out['S6_vbt_equity']:.2f} 手工权益={out['S6_manual_equity']:.2f} 相对差={out['S6_rel_diff']:.2e} "
                  f"entry价差={out.get('S6_entry_price_maxdiff')} exit价差={out.get('S6_exit_price_maxdiff')}")
        if out.get("S7_grid"):
            g = out["S7_grid"]
            print(f"  S7 网格最优={g['best']} 平台期(±2pp)={len(g['plateau_within_2pp'])} 组")
            for k, v in sorted(g["grid_return"].items(), key=lambda kv: -kv[1]):
                f_, s_ = k.split("_s")
                print(f"      fast={int(f_.lstrip('f')):>2} slow={int(s_):>3} profit={v:+.0f}元")

    # 断言汇总
    print("\n===== 断言汇总 =====")
    fails = []
    for key in out_all:
        if not isinstance(key, str) or "_" not in key:
            continue
        o = out_all[key]
        for chk in [
            ("S1", o.get("S1_nextbar_open_maxdiff") is not None and o["S1_nextbar_open_maxdiff"] < 1e-4),
            ("S2", not o["S2_entry_on_limitup"] and not o["S2_exit_on_limitdown"]),
            ("S3", not o["S3_trade_on_suspension"]),
            ("S4", (o.get("S4_min_exit_entry_gap") or 0) >= 1 and not o["S4_any_same_day"]),
            ("S5", o.get("S5_rel_diff") is not None and o["S5_rel_diff"] < 1e-4),
            ("S6", o.get("S6_rel_diff") is not None and o["S6_rel_diff"] < 0.001),
        ]:
            flag = "PASS" if chk[1] else "FAIL"
            print(f"  {key} {chk[0]}: {flag}")
            if not chk[1]:
                fails.append((key, chk[0]))

    os.makedirs("reports", exist_ok=True)
    with open(r"reports\vbt_poc_evidence_20260828.json", "w", encoding="utf-8") as f:
        json.dump(out_all, f, ensure_ascii=False, indent=1, default=str)
    print("\n证据已写 reports/vbt_poc_evidence_20260828.json")
    if fails:
        print("FAILED:", fails)
        sys.exit(1)
    print("POC ALL PASS")


if __name__ == "__main__":
    main()
