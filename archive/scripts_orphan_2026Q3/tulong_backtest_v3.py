# -*- coding: utf-8 -*-
"""屠龙系统多模型全域回测 — V3 独立引擎"""
import json, os, sqlite3, time, logging
from datetime import datetime

import pandas as pd
import numpy as np

# ====== 硬参数 ======
INITIAL_CAPITAL = 100000
START = "2016-06-30"
END = "2026-08-13"
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\reports\tulong_backtest_20260813"
os.makedirs(OUT, exist_ok=True)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(f"{OUT}/run_v3.log", encoding="utf-8")])
log = logging.getLogger("T3")

def _norm(d):
    """统一日期格式 YYYY-MM-DD"""
    s = str(d).replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else str(d)

# ====== 数据加载 ======
log.info("=" * 60)
log.info("加载数据...")
t0 = time.time()
conn = sqlite3.connect(DB)

# 交易日
cal = pd.read_sql("SELECT trade_date FROM trading_calendar ORDER BY trade_date", conn)
trade_dates = sorted([_norm(d) for d in cal.iloc[:, 0]])
trade_dates = [d for d in trade_dates if START <= d <= END]
log.info(f"交易日: {len(trade_dates)}")

# 股票信息（过滤）
si = pd.read_sql("SELECT code, name, is_st, market FROM stock_info", conn)
# 剔除ST / 科创板 / 北交所
# is_st 为字符串 '0'/'1'
ok_codes = set(si[pd.to_numeric(si["is_st"], errors="coerce").fillna(0) == 0]["code"])
# 按代码前缀过滤
ok_codes = {c for c in ok_codes if not c.startswith(("688", "689", "8", "4", "920"))}
log.info(f"可交易标的: {len(ok_codes)}")

# 上证指数（MA250 牛熊判定）
idx_df = pd.read_sql("SELECT date, close FROM index_daily WHERE index_code='000001' ORDER BY date", conn)
idx_df["date"] = idx_df["date"].apply(_norm)
idx_df["ma250"] = idx_df["close"].rolling(250, min_periods=100).mean()
idx_map = {r["date"]: {"close": r["close"], "ma250": r["ma250"]} for _, r in idx_df.iterrows()}
log.info(f"上证指数: {len(idx_map)} 日")

# 信号表
log.info("加载信号...")

# V375
v375_all = pd.read_sql("SELECT code, date, v375_prob FROM stock_signals_v375", conn)
v375_all["date"] = v375_all["date"].apply(_norm)
v375_by_date = {d: grp.set_index("code")["v375_prob"] for d, grp in v375_all.groupby("date")}
log.info(f"V375: {len(v375_by_date)} 日")

# P2
p2_all = pd.read_sql("SELECT code, date, p2_prob FROM stock_signals_p2", conn)
p2_all["date"] = p2_all["date"].apply(_norm)
p2_by_date = {d: grp.set_index("code")["p2_prob"] for d, grp in p2_all.groupby("date")}
log.info(f"P2: {len(p2_by_date)} 日")

# 基本面因子 (bp+ep)
fund_all = pd.read_sql("SELECT code, date, close, ep, bp FROM stock_factors_fundamental", conn)
fund_all["date"] = fund_all["date"].apply(_norm)
fund_by_date = {d: grp.set_index("code")[["close", "ep", "bp"]] for d, grp in fund_all.groupby("date")}
log.info(f"基本面: {len(fund_by_date)} 日")

conn.close()

# QFQ 日线
log.info("加载 QFQ 日线...")
qconn = sqlite3.connect(QFQ)
q_all = pd.read_sql("SELECT code, date, open, high, low, close, volume, amount FROM stock_daily_meta_qfq", qconn)
q_all["date"] = q_all["date"].apply(_norm)
q_all = q_all[q_all["code"].isin(ok_codes)]
bar_by_date = {d: grp.set_index("code") for d, grp in q_all.groupby("date")}
qconn.close()
log.info(f"QFQ 行情: {len(bar_by_date)} 日, {len(q_all)} 行")

log.info(f"数据加载完成: {time.time()-t0:.0f}s")

# ====== 简易回测引擎 ======
class Engine:
    def __init__(self, name, freq="quarterly"):
        self.name = name
        self.freq = freq  # "quarterly", "daily"
        self.cash = INITIAL_CAPITAL
        self.pos = {}  # code -> {"shares": int, "cost": float, "buy_date": str}
        self.trades = []
        self.ecu = []
        self.fees = 0.0

    def _buy(self, code, price, shares, date):
        cost = price * shares
        comm = max(cost * 0.00025, 5.0)
        total = cost + comm
        if total > self.cash:
            return False
        self.cash -= total
        self.fees += comm
        if code in self.pos:
            p = self.pos[code]
            total_shares = p["shares"] + shares
            p["cost"] = (p["cost"] * p["shares"] + cost) / total_shares
            p["shares"] = total_shares
        else:
            self.pos[code] = {"shares": shares, "cost": price, "buy_date": date}
        return True

    def _sell(self, code, price, date, reason=""):
        p = self.pos.get(code)
        if not p:
            return
        proceeds = price * p["shares"]
        comm = max(proceeds * 0.00025, 5.0)
        stamp_tax = proceeds * 0.0005
        net = proceeds - comm - stamp_tax
        pnl = net - p["cost"] * p["shares"]
        self.cash += net
        self.fees += comm + stamp_tax
        self.trades.append({
            "code": code, "date": date, "buy_date": p["buy_date"],
            "buy_price": round(p["cost"], 2), "sell_price": round(price, 2),
            "shares": p["shares"], "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / (p["cost"] * p["shares"]) * 100, 2),
            "reason": reason
        })
        del self.pos[code]

    def nav(self, prices):
        pos_val = sum(prices.get(c, 0) * v["shares"] for c, v in self.pos.items())
        return self.cash + pos_val

    def get_bars(self, date):
        return bar_by_date.get(date)

    def run(self, signal_fn):
        # 季度调仓日
        if self.freq == "quarterly":
            rebal_dates = set()
            prev_m = 0
            for d in trade_dates:
                m = int(d[5:7])
                if m in (1, 4, 7, 10) and m != prev_m:
                    rebal_dates.add(d)
                    prev_m = m
        else:
            rebal_dates = None

        n = len(trade_dates)
        for i, date in enumerate(trade_dates):
            bars = self.get_bars(date)
            if bars is None or len(bars) == 0:
                self.ecu.append({"date": date, "equity": self.cash})
                continue

            prices = bars["close"].to_dict()
            opens = bars["open"].to_dict()

            # --- 卖出检查 ---
            for code in list(self.pos.keys()):
                cp = prices.get(code)
                if cp is None:
                    continue
                cost = self.pos[code]["cost"]
                pnl_pct = (cp - cost) / cost

                if pnl_pct <= -0.08:
                    self._sell(code, cp, date, "stop_loss")
                    continue

                if pnl_pct >= 0.10:
                    trail = 0.06 if pnl_pct >= 0.30 else 0.10
                    high_since = max(cp, cost)
                    dd_from_peak = (high_since - cp) / high_since
                    if dd_from_peak >= trail:
                        self._sell(code, cp, date, "trailing_stop")
                        continue

            # --- 信号（按频率触发） ---
            is_rebal_day = (rebal_dates is None) or (date in rebal_dates)
            if is_rebal_day:
                weights = signal_fn(date, prices)
                # --- 调仓 ---
                if weights:
                    for code in list(self.pos.keys()):
                        if code not in weights:
                            self._sell(code, prices.get(code, 0), date, "rebalance")
                    sorted_codes = sorted(weights.keys(), key=lambda c: -weights[c])
                    for code in sorted_codes:
                        if code in self.pos:
                            continue
                        price = opens.get(code, prices.get(code))
                        if price is None or price <= 0 or price > 30:
                            continue
                        target_val = self.cash * weights[code]
                        if target_val < 1000:
                            continue
                        shares = int(target_val / price / 100) * 100
                        if shares >= 100:
                            self._buy(code, price * 1.0003, shares, date)

            nv = self.nav(prices)
            self.ecu.append({"date": date, "equity": nv})

            if (i + 1) % 300 == 0:
                log.info(f"[{self.name}] {i + 1}/{n} 净值={nv:.0f} 持仓={len(self.pos)} 交易={len(self.trades)}")

        # end
        final_nav = self.nav({})
        df_ec = pd.DataFrame(self.ecu)
        total_ret = (df_ec["equity"].iloc[-1] / INITIAL_CAPITAL - 1) * 100
        years = max(1, len(trade_dates) / 245)
        cagr = ((1 + total_ret / 100) ** (1 / years) - 1) * 100
        wins = sum(1 for t in self.trades if t["pnl"] > 0)
        win_rate = wins / len(self.trades) * 100 if self.trades else 0
        peak = df_ec["equity"].cummax()
        dd = ((df_ec["equity"] - peak) / peak * 100).min()

        log.info(f"[{self.name}] 完成! 收益={total_ret:.2f}% CAGR={cagr:.2f}% 回撤={dd:.2f}% "
                 f"交易={len(self.trades)} 胜率={win_rate:.1f}%")

        return {
            "name": self.name, "freq": self.freq,
            "total_return_pct": round(total_ret, 2),
            "cagr_pct": round(cagr, 2),
            "max_drawdown_pct": round(dd, 2),
            "total_trades": len(self.trades),
            "win_rate_pct": round(win_rate, 1),
            "final_equity": round(df_ec["equity"].iloc[-1], 2),
            "trades": self.trades,
            "equity_curve": df_ec,
        }


# ====== 信号函数 ======

def signal_bp_ep(date, prices):
    """bp+ep 季度调仓：等权合成，<=30元域内 Top20"""
    df = fund_by_date.get(date)
    if df is None or len(df) < 20:
        return {}
    df = df.copy()
    df = df.dropna(subset=["close", "ep", "bp"])
    df = df[df["close"] <= 30]
    if len(df) < 20:
        return {}
    df["ep_r"] = df["ep"].rank(pct=True)
    df["bp_r"] = df["bp"].rank(pct=True)
    df["score"] = (df["ep_r"] + df["bp_r"]) / 2
    top = df.nlargest(20, "score")
    w = 1.0 / 20
    return {code: w for code in top.index}


def signal_v375(date, prices):
    s = v375_by_date.get(date)
    if s is None or len(s) == 0:
        return {}
    s = s.dropna()
    cand = s[s >= 0.65]
    if len(cand) == 0:
        return {}
    top = cand.nlargest(min(3, len(cand)))
    w = 1.0 / len(top)
    return {code: w for code in top.index}


def signal_p2(date, prices):
    s = p2_by_date.get(date)
    if s is None or len(s) == 0:
        return {}
    s = s.dropna()
    idx_data = idx_map.get(date)
    bullish = True
    if idx_data and not pd.isna(idx_data["ma250"]):
        bullish = idx_data["close"] > idx_data["ma250"]
    thr = 0.70 if bullish else 0.74
    cand = s[s >= thr]
    if len(cand) == 0:
        return {}
    top = cand.nlargest(min(5, len(cand)))
    w = 1.0 / len(top)
    return {code: w for code in top.index}


def signal_composite(date, prices):
    # 用 V375 作为综合（覆盖时间最长）
    return signal_v375(date, prices)


# ====== 执行 ======

def run_one(name, freq, signal_fn):
    eng = Engine(name, freq)
    return eng.run(signal_fn)


def export_result(res, prefix):
    df_ec = res["equity_curve"]
    df_ec.to_csv(f"{OUT}/{prefix}_equity.csv", index=False)
    with open(f"{OUT}/{prefix}_trades.json", "w", encoding="utf-8") as f:
        json.dump(res["trades"], f, ensure_ascii=False, indent=2, default=str)
    summary = {k: v for k, v in res.items() if k in ["name", "freq", "total_return_pct",
        "cagr_pct", "max_drawdown_pct", "total_trades", "win_rate_pct", "final_equity"]}
    with open(f"{OUT}/{prefix}_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info(f"  导出 {prefix}: 收益={res['total_return_pct']}% 交易={res['total_trades']}")


if __name__ == "__main__":
    t0 = time.time()
    configs = [
        ("bp+ep_core", "quarterly", signal_bp_ep),
        ("v375_signal", "daily", signal_v375),
        ("p2_signal", "daily", signal_p2),
        ("composite_signal", "daily", signal_composite),
    ]
    results = {}
    for name, freq, fn in configs:
        log.info(f"\n{"=" * 50}\n开始: {name} (频率={freq})")
        r = run_one(name, freq, fn)
        results[name] = r
        export_result(r, name.replace("+", "_"))

    # 组合
    log.info(f"\n{"=" * 50}\n运行组合策略...")
    ec0 = results["bp+ep_core"]["equity_curve"]
    ec1 = results["v375_signal"]["equity_curve"]
    ec2 = results["p2_signal"]["equity_curve"]
    # 对齐日期
    dates = ec0["date"].values
    ec1_d = {r["date"]: r["equity"] for _, r in ec1.iterrows()}
    ec2_d = {r["date"]: r["equity"] for _, r in ec2.iterrows()}

    combo_data = []
    for _, r in ec0.iterrows():
        d = r["date"]
        v0 = r["equity"]
        v1 = ec1_d.get(d, INITIAL_CAPITAL)
        v2 = ec2_d.get(d, INITIAL_CAPITAL)
        # 组合净值 = 各部分收益加权
        w0, w1, w2 = 0.40, 0.20, 0.40
        r0 = (v0 / INITIAL_CAPITAL - 1) * w0
        r1 = (v1 / INITIAL_CAPITAL - 1) * w1
        r2 = (v2 / INITIAL_CAPITAL - 1) * w2
        combo_nav = INITIAL_CAPITAL * (1 + r0 + r1 + r2)
        combo_data.append({"date": d, "equity": round(combo_nav, 2)})

    df_combo = pd.DataFrame(combo_data)
    combo_ret = (df_combo["equity"].iloc[-1] / INITIAL_CAPITAL - 1) * 100
    combo_peak = df_combo["equity"].cummax()
    combo_dd = ((df_combo["equity"] - combo_peak) / combo_peak * 100).min()
    log.info(f"组合: 收益={combo_ret:.2f}% 回撤={combo_dd:.2f}%")
    df_combo.to_csv(f"{OUT}/portfolio_combo_equity.csv", index=False)
    with open(f"{OUT}/portfolio_combo_summary.json", "w", encoding="utf-8") as f:
        json.dump({"total_return_pct": round(combo_ret, 2),
                   "max_drawdown_pct": round(combo_dd, 2), "final_equity": round(df_combo["equity"].iloc[-1], 2)},
                  f, indent=2)

    elapsed = time.time() - t0
    log.info(f"\n{"=" * 50}\n全部完成! 耗时: {elapsed:.0f}s ({elapsed / 60:.1f}min)\n输出: {OUT}")
    for f in sorted(os.listdir(OUT)):
        print(f"  {f}")
