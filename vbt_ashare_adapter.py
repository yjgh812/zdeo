"""
vbt_ashare_adapter.py — 屠龙 VectorBT A股规则适配器（研究层预筛，单一真源）

定位：VectorBT 在屠龙 = 参数预筛层（STACK_LEARNING_20260828）。本适配器把所有 A 股
成交规则集中在一处，使预筛扫描的"相对参数排序/平台期结构"可信；最终裁决仍在自研
事件式门禁回测 + wfa_gate。适配器绝不产出"生产参数"。

规则（全部引擎内生效，manual_sim 实现完全一致规则用于对账）：
  R1 next-bar-open：原始信号 close[t] → 引擎在 open[t+1] 成交（适配器内部 shift）
  R2 T+1：卖出最早为买入次一 bar（信号单bar翻转 + shift 构造保证；适配器断言不变量）
  R3 涨跌停：开盘即涨停禁买、开盘即跌停禁卖（open 与 ±limit_pct×prev_close 比较）
  R4 停牌：volume<=0 禁交易
  R5 最低佣金 5 元/笔（买、卖佣金部分均适用；印花税无最低）
  R6 卖出印花税 0.05%（单边）
  R7 整手约束：size_granularity=lot（主板 100 股）
  R8 滑点：可选（默认 0，与事件式回测口径一致；>0 时买卖按 ±slippage 成交）

实现机制：from_orders + 逐bar有效费率（费率 = max(comm, min_comm/(size×open)) [+stamp]）。
已在合成数据实证：引擎费用 == 手工逐笔（买 5.0 / 卖 5.575 精确命中），并在 PoC 实证
引擎权益与纯 Python 模拟分毫不差。from_order_func（引擎内拒单/记录 rejection）作为
未来 T+1 引擎级强制的备选路径记录于报告，本适配器采用更简单且已验证的路径。

运行自证：./.venv_vbt/Scripts/python.exe vbt_ashare_adapter.py  （全断言 PASS 才 exit 0）
"""
import dataclasses
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STOCKS = [
    ("600519", "贵州茅台"),
    ("000001", "平安银行"),
    ("600036", "招商银行"),
    ("300059", "东方财富"),  # 创业板 ±20%，验证板块感知涨跌停
]
START, END = "2023-01-01", "2026-08-27"


@dataclasses.dataclass
class AshareRules:
    comm: float = 0.00025      # 佣金率 万2.5
    stamp: float = 0.0005      # 印花税 千0.5 卖出单边
    min_comm: float = 5.0      # 最低佣金 元/笔
    lot: int = 100             # 整手 100 股
    slippage: float = 0.0      # 滑点率（默认 0 = 事件式回测口径）


def board_limit_pct(code: str) -> float:
    """按股票代码推断涨跌停幅度（屠龙宇宙：沪深主板+创业板，剔 ST/科创/北交所/次新）。
    主板(60x/000/001/003)=±10%；创业板(300/301)=±20%（2020-08-24 注册制起，本 PoC 窗口 2023+ 恒 20%）；
    科创板(688)=±20%；北交所(8/920)=±30%。ST 为 ±5% 但屠龙已剔除，不在此建模。"""
    c = str(code)
    if c.startswith(("300", "301", "688")):
        return 0.20
    if c.startswith(("8", "920", "4")):  # 北交所旧码 8/4 开头、新码 920 开头
        return 0.30
    return 0.10


def load_stock(code):
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume,amount,change_pct "
        "FROM stock_daily_meta_qfq WHERE code=? AND date>=? AND date<=? ORDER BY date",
        con, params=(code, START.replace("-", ""), END.replace("-", "")))
    con.close()
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date")
    df["prev_close"] = df["close"].shift(1)
    return df


def _order_masks(df, limit_pct):
    """停牌 + 涨跌停开盘掩码（limit_pct 按板块感知，见 board_limit_pct）。"""
    suspended = df["volume"].fillna(0) <= 0
    limit_up_open = df["open"] >= np.round(df["prev_close"] * (1 + limit_pct), 2) - 0.005
    limit_dn_open = df["open"] <= np.round(df["prev_close"] * (1 - limit_pct), 2) + 0.005
    return suspended, limit_up_open, limit_dn_open


def _next_bar(df, entries_raw, exits_raw, limit_pct):
    """R1+R3+R4：信号 shift(1) 到 next-bar-open；执行 bar 加停牌/涨跌停掩码；
    再加单仓位状态机过滤（买入仅空仓时、卖出仅持仓时）——防涨跌停拦卖后重复买入累积。"""
    ents = entries_raw.shift(1).fillna(False)
    exts = exits_raw.shift(1).fillna(False)
    suspended, lu, ld = _order_masks(df, limit_pct)
    block_buy = suspended | lu
    block_sell = suspended | ld
    ents = ents & ~block_buy
    exts = exts & ~block_sell
    # 单仓位状态机：vbt 对重复买入会累积加仓，与"单仓位 long-only"语义不符，必须过滤
    e = ents.to_numpy()
    x = exts.to_numpy()
    fe = np.zeros(len(e), dtype=bool)
    fx = np.zeros(len(x), dtype=bool)
    holding = False
    for i in range(len(e)):
        if (not holding) and e[i]:
            fe[i] = True
            holding = True
        elif holding and x[i]:
            fx[i] = True
            holding = False
    ents = pd.Series(fe, index=ents.index)
    exts = pd.Series(fx, index=exts.index)
    return ents, exts, {"suspended": suspended, "limit_up_open": lu, "limit_dn_open": ld}


def _effective_fee_rate(value, is_sell, rules):
    """R5+R6：含最低佣金的逐bar有效费率。value = |size×open|。"""
    rate = max(rules.comm, rules.min_comm / value) if value > 0 else 0.0
    if is_sell:
        rate += rules.stamp
    return rate


def run_ashare(df, entries_raw, exits_raw, size=100, init_cash=1e9, rules=None, code=None):
    """构建并运行 A 股规则化 from_orders 组合。entries_raw/exits_raw 为原始（未shift）信号。
    code 用于板块感知涨跌停幅度（缺省按主板 10%）。"""
    rules = rules or AshareRules()
    limit_pct = board_limit_pct(code) if code else 0.10
    ents, exts, masks = _next_bar(df, entries_raw, exits_raw, limit_pct)
    overlap = (ents & exts).sum()
    if overlap:
        raise ValueError(f"同bar买卖冲突 {int(overlap)} 处：策略信号需先保证单bar翻转（T+1 前置）")

    sz = pd.DataFrame(0.0, index=df.index, columns=["x"])
    sz.loc[ents, "x"] = float(size)
    sz.loc[exts, "x"] = -float(size)
    # 逐bar有效费率：买入 max(comm, min_comm/value)；卖出另加印花税
    fees = pd.DataFrame(rules.comm, index=df.index, columns=["x"])
    open_n = df["open"].to_numpy()
    for bar in np.where(ents.to_numpy())[0]:
        fees.iloc[bar, 0] = _effective_fee_rate(size * open_n[bar], False, rules)
    for bar in np.where(exts.to_numpy())[0]:
        fees.iloc[bar, 0] = _effective_fee_rate(size * open_n[bar], True, rules)

    import vectorbt as vbt
    pf = vbt.Portfolio.from_orders(
        df["open"].to_frame("x"), sz, size_type="Amount", size_granularity=rules.lot,
        price=df["open"].to_frame("x"), fees=fees, slippage=rules.slippage,
        init_cash=init_cash, freq="D", direction="longonly")
    return pf, ents, exts, masks


def manual_sim_ashare(df, entries_raw, exits_raw, size=100, init_cash=1e9, rules=None, code=None):
    """事件式手工模拟，实现与 run_ashare 完全一致的规则（用于对账）。返回(权益,总费用,买入价[],卖出价[])。"""
    rules = rules or AshareRules()
    limit_pct = board_limit_pct(code) if code else 0.10
    ents, exts, masks = _next_bar(df, entries_raw, exits_raw, limit_pct)
    open_n = df["open"].to_numpy()
    ent_n, ext_n = ents.to_numpy(), exts.to_numpy()
    cash = float(init_cash)
    pos = 0.0
    total_fee = 0.0
    entry_px, exit_px = [], []
    for t in range(1, len(df)):
        px = open_n[t] * (1 + rules.slippage if ent_n[t] else 1)  # 滑点仅影响成交价口径
        if pos == 0 and ent_n[t]:
            fill = open_n[t] * (1 + rules.slippage)
            fee = max(rules.comm * size * fill, rules.min_comm)
            cash -= size * fill + fee
            total_fee += fee
            pos = float(size)
            entry_px.append(fill)
        elif pos > 0 and ext_n[t]:
            fill = open_n[t] * (1 - rules.slippage)
            fee = max(rules.comm * size * fill, rules.min_comm) + rules.stamp * size * fill
            cash += size * fill - fee
            total_fee += fee
            pos = 0.0
            exit_px.append(fill)
    equity = cash + pos * open_n[-1]
    return equity, total_fee, entry_px, exit_px


def verify():
    import vectorbt as vbt
    rules = AshareRules()
    fails = []
    for code, name in STOCKS:
        df = load_stock(code)
        close = df["close"]
        fast, slow = close.rolling(5).mean(), close.rolling(20).mean()
        up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        dn = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        warm = close.rolling(20).count() < 20
        up, dn = up & ~warm, dn & ~warm

        pf, ents, exts, masks = run_ashare(df, up, dn, size=100, rules=rules, code=code)
        tr = pf.trades.records_arr
        closed = tr["status"] == 1
        open_n = df["open"].to_numpy()

        print(f"\n=== {code} {name} ===")
        # S1 next-bar-open
        s1 = float(np.max(np.abs(tr["entry_price"].astype(float) - open_n[tr["entry_idx"].astype(int)]))) if len(tr) else 0.0
        # S2/S3 掩码
        lu, ld, susp = masks["limit_up_open"].to_numpy(), masks["limit_dn_open"].to_numpy(), masks["suspended"].to_numpy()
        s2 = bool(lu[tr["entry_idx"].astype(int)].any() or ld[tr["exit_idx"][closed].astype(int)].any())
        s3 = bool(susp[tr["entry_idx"].astype(int)].any() or susp[tr["exit_idx"][closed].astype(int)].any())
        # S4 T+1
        diff = tr["exit_idx"][closed].astype(int) - tr["entry_idx"][closed].astype(int)
        s4_min = int(diff.min()) if closed.any() else None
        s4 = (s4_min or 0) >= 1
        # S5 费用（含最低佣金）
        man_equity, man_fee, man_ep, man_xp = manual_sim_ashare(df, up, dn, size=100, rules=rules, code=code)
        vbt_fee = float(tr["entry_fees"].astype(float).sum() + tr["exit_fees"].astype(float).sum())
        s5 = abs(vbt_fee - man_fee) / max(man_fee, 1e-9) < 1e-4
        # S6 权益对账
        vbt_eq = float(pf.value().iat[-1, 0])
        s6 = abs(vbt_eq - man_equity) / max(man_equity, 1e-9) < 1e-4
        # S7 成交价对账（含滑点路径：再跑一次 slippage=0.0005）
        ep_match = len(man_ep) == len(tr) and float(np.max(np.abs(np.array(man_ep) - tr["entry_price"].astype(float)))) < 1e-6
        rules2 = dataclasses.replace(rules, slippage=0.0005)
        pf2, *_ = run_ashare(df, up, dn, size=100, rules=rules2, code=code)
        man2_equity, man2_fee, *_ = manual_sim_ashare(df, up, dn, size=100, rules=rules2, code=code)
        s7_slip = abs(float(pf2.value().iat[-1, 0]) - man2_equity) / max(man2_equity, 1e-9) < 1e-4

        for k, ok, desc in [
            ("S1 next-bar-open", s1 < 1e-4, f"entry==open 最大偏差 {s1:.2e}"),
            ("S2 涨跌停掩码", not s2, "无涨跌停开盘成交"),
            ("S3 停牌掩码", not s3, "无停牌日成交"),
            ("S4 T+1", s4, f"最小 exit-entry 间隔 {s4_min}"),
            ("S5 费用含最低佣金", s5, f"vbt={vbt_fee:.4f} manual={man_fee:.4f}"),
            ("S6 权益对账", s6, f"vbt={vbt_eq:.2f} manual={man_equity:.2f}"),
            ("S7 成交价+滑点0.0005", ep_match and s7_slip, f"ep_match={ep_match} slip_eq={s7_slip}"),
        ]:
            print(f"  {k}: {'PASS' if ok else 'FAIL'}  {desc}")
            if not ok:
                fails.append((code, k))

    print("\n===== 适配器自证汇总 =====")
    if fails:
        print("FAILED:", fails)
        sys.exit(1)
    print("ADAPTER ALL PASS")


if __name__ == "__main__":
    verify()
