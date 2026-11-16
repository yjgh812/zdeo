# -*- coding: utf-8 -*-
"""屠龙系统多模型回测 V4 — 优化迭代版
修复：V375高频交易/最低持有期/组合真实撮合(有胜率)/bp+ep Top15 vs Top20
"""
import json, os, sqlite3, time, logging
import pandas as pd
import numpy as np

INITIAL_CAPITAL = 100000
START = "2016-06-30"
END = "2026-08-13"
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\reports\tulong_backtest_20260813"
os.makedirs(OUT, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(f"{OUT}/run_v4.log", encoding="utf-8")])
log = logging.getLogger("T4")

def _norm(d):
    s = str(d).replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else str(d)

# ====== 数据加载 ======
log.info("加载数据...")
t0 = time.time()
conn = sqlite3.connect(DB)

cal = pd.read_sql("SELECT trade_date FROM trading_calendar ORDER BY trade_date", conn)
trade_dates = sorted([_norm(d) for d in cal.iloc[:, 0]])
trade_dates = [d for d in trade_dates if START <= d <= END]
log.info(f"交易日: {len(trade_dates)}")

si = pd.read_sql("SELECT code, name, is_st, market FROM stock_info", conn)
ok_codes = set(si[pd.to_numeric(si["is_st"], errors="coerce").fillna(0) == 0]["code"])
ok_codes = {c for c in ok_codes if not c.startswith(("688", "689", "8", "4", "920"))}
log.info(f"可交易标的: {len(ok_codes)}")

idx_df = pd.read_sql("SELECT date, close FROM index_daily WHERE index_code='000001' ORDER BY date", conn)
idx_df["date"] = idx_df["date"].apply(_norm)
idx_df["ma250"] = idx_df["close"].rolling(250, min_periods=100).mean()
idx_map = {r["date"]: {"close": r["close"], "ma250": r["ma250"]} for _, r in idx_df.iterrows()}

# 信号表
v375_all = pd.read_sql("SELECT code, date, v375_prob FROM stock_signals_v375", conn)
v375_all["date"] = v375_all["date"].apply(_norm)
v375_by_date = {d: grp.set_index("code")["v375_prob"] for d, grp in v375_all.groupby("date")}

p2_all = pd.read_sql("SELECT code, date, p2_prob FROM stock_signals_p2", conn)
p2_all["date"] = p2_all["date"].apply(_norm)
p2_by_date = {d: grp.set_index("code")["p2_prob"] for d, grp in p2_all.groupby("date")}

# 综合信号：stock_signals 的 composite_score + ai_prob_5d（真正实现）
comp_all = pd.read_sql("SELECT code, date, composite_score, ai_prob_5d FROM stock_signals", conn)
comp_all["date"] = comp_all["date"].apply(_norm)
comp_by_date = {d: grp.set_index("code")[["composite_score", "ai_prob_5d"]]
                for d, grp in comp_all.groupby("date")}

fund_all = pd.read_sql("SELECT code, date, close, ep, bp FROM stock_factors_fundamental", conn)
fund_all["date"] = fund_all["date"].apply(_norm)
fund_by_date = {d: grp.set_index("code")[["close", "ep", "bp"]] for d, grp in fund_all.groupby("date")}
conn.close()

qconn = sqlite3.connect(QFQ)
q_all = pd.read_sql("SELECT code, date, open, high, low, close, volume, amount FROM stock_daily_meta_qfq", qconn)
q_all["date"] = q_all["date"].apply(_norm)
q_all = q_all[q_all["code"].isin(ok_codes)]
bar_by_date = {d: grp.set_index("code") for d, grp in q_all.groupby("date")}
qconn.close()
log.info(f"数据加载完成: {time.time()-t0:.0f}s (QFQ {len(bar_by_date)}日)")

# ====== 引擎（含最低持有期） ======
class Engine:
    def __init__(self, name, freq="daily", min_hold=0, top_n=None):
        self.name = name
        self.freq = freq          # daily / quarterly
        self.min_hold = min_hold  # 最低持有交易日
        self.top_n = top_n        # bp+ep 持仓数
        self.cash = INITIAL_CAPITAL
        self.pos = {}             # code -> {shares, cost, buy_date, buy_i}
        self.trades = []
        self.ecu = []
        self.fees = 0.0

    def _buy(self, code, price, shares, date, i):
        cost = price * shares
        comm = max(cost * 0.00025, 5.0)
        total = cost + comm
        if total > self.cash:
            return
        self.cash -= total
        self.fees += comm
        if code in self.pos:
            p = self.pos[code]
            ts = p["shares"] + shares
            p["cost"] = (p["cost"] * p["shares"] + cost) / ts
            p["shares"] = ts
        else:
            self.pos[code] = {"shares": shares, "cost": price, "buy_date": date, "buy_i": i}

    def _sell(self, code, price, date, reason=""):
        p = self.pos.get(code)
        if not p:
            return
        proceeds = price * p["shares"]
        comm = max(proceeds * 0.00025, 5.0)
        stamp = proceeds * 0.0005
        net = proceeds - comm - stamp
        pnl = net - p["cost"] * p["shares"]
        self.cash += net
        self.fees += comm + stamp
        self.trades.append({
            "code": code, "date": date, "buy_date": p["buy_date"],
            "buy_price": round(p["cost"], 2), "sell_price": round(price, 2),
            "shares": p["shares"], "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / (p["cost"] * p["shares"]) * 100, 2),
            "hold_days": None, "reason": reason
        })
        del self.pos[code]

    def nav(self, prices):
        pos_val = sum(prices.get(c, 0) * v["shares"] for c, v in self.pos.items())
        return self.cash + pos_val

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
            bars = bar_by_date.get(date)
            if bars is None or len(bars) == 0:
                self.ecu.append({"date": date, "equity": self.cash})
                continue

            prices = bars["close"].to_dict()
            opens = bars["open"].to_dict()

            # 卖出检查（止损无持有期限制；止盈需过最低持有期）
            for code in list(self.pos.keys()):
                cp = prices.get(code)
                if cp is None:
                    continue
                cost = self.pos[code]["cost"]
                pnl_pct = (cp - cost) / cost
                held = i - self.pos[code]["buy_i"]
                if pnl_pct <= -0.08:
                    self._sell(code, cp, date, "stop_loss")
                    continue
                if pnl_pct >= 0.10 and held >= self.min_hold:
                    trail = 0.06 if pnl_pct >= 0.30 else 0.10
                    high_since = max(cp, cost)
                    if (high_since - cp) / high_since >= trail:
                        self._sell(code, cp, date, "trailing_stop")
                        continue

            # 信号
            is_rebal = (rebal_dates is None) or (date in rebal_dates)
            if is_rebal:
                weights = signal_fn(date, prices, i)
                if weights:
                    # 调出不在新权重中的（需过持有期，止损除外）
                    for code in list(self.pos.keys()):
                        if code not in weights:
                            held = i - self.pos[code]["buy_i"]
                            if held >= self.min_hold:
                                self._sell(code, prices.get(code, 0), date, "rebalance")
                    # 买入
                    for code in sorted(weights, key=lambda c: -weights[c]):
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
                            self._buy(code, price * 1.0003, shares, date, i)

            self.ecu.append({"date": date, "equity": round(self.nav(prices), 2)})
            if (i + 1) % 500 == 0:
                log.info(f"[{self.name}] {i+1}/{n} 净值={self.nav(prices):.0f} 持仓={len(self.pos)} 交易={len(self.trades)}")

        # 补持有天数
        for t in self.trades:
            try:
                bi = next((i for i, d in enumerate(trade_dates) if d == t["buy_date"]), None)
                si = next((i for i, d in enumerate(trade_dates) if d == t["date"]), None)
                t["hold_days"] = (si - bi) if bi is not None and si is not None else None
            except Exception:
                pass

        df_ec = pd.DataFrame(self.ecu)
        total_ret = (df_ec["equity"].iloc[-1] / INITIAL_CAPITAL - 1) * 100
        years = max(1, len(trade_dates) / 245)
        cagr = ((1 + total_ret / 100) ** (1 / years) - 1) * 100
        wins = sum(1 for t in self.trades if t["pnl"] > 0)
        win_rate = wins / len(self.trades) * 100 if self.trades else 0
        peak = df_ec["equity"].cummax()
        dd = ((df_ec["equity"] - peak) / peak * 100).min()
        # 盈亏比
        win_p = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        loss_p = [t["pnl"] for t in self.trades if t["pnl"] <= 0]
        pl_ratio = (sum(win_p)/len(win_p) / abs(sum(loss_p)/len(loss_p))) if win_p and loss_p else 0

        log.info(f"[{self.name}] 完成! 收益={total_ret:.2f}% CAGR={cagr:.2f}% 回撤={dd:.2f}% "
                 f"交易={len(self.trades)} 胜率={win_rate:.1f}% 盈亏比={pl_ratio:.2f}")
        return {
            "name": self.name, "freq": self.freq,
            "total_return_pct": round(total_ret, 2), "cagr_pct": round(cagr, 2),
            "max_drawdown_pct": round(dd, 2), "total_trades": len(self.trades),
            "win_rate_pct": round(win_rate, 1), "pl_ratio": round(pl_ratio, 2),
            "final_equity": round(df_ec["equity"].iloc[-1], 2),
            "trades": self.trades, "equity_curve": df_ec,
        }


# ====== 信号函数 ======

def bp_ep_signal(date, prices, i, top_n=20, weight=1.0):
    """bp+ep：<=30元 TopN，等权 weight 仓位"""
    df = fund_by_date.get(date)
    if df is None or len(df) < 20:
        return {}
    df = df.copy().dropna(subset=["close", "ep", "bp"])
    df = df[df["close"] <= 30]
    if len(df) < 20:
        return {}
    df["score"] = (df["ep"].rank(pct=True) + df["bp"].rank(pct=True)) / 2
    top = df.nlargest(top_n, "score")
    w = weight / len(top)
    return {code: w for code in top.index}


def v375_opt_signal(date, prices, i):
    """V375 优化：阈值0.80，每日≤2只，等权20%"""
    s = v375_by_date.get(date)
    if s is None or len(s) == 0:
        return {}
    s = s.dropna()
    cand = s[s >= 0.80]
    if len(cand) == 0:
        return {}
    top = cand.nlargest(min(2, len(cand)))
    w = 0.20 / len(top)
    return {code: w for code in top.index}


def p2_opt_signal(date, prices, i):
    """P2 优化：牛0.70/熊0.72（P2 prob 最大值仅0.775，99分位0.699），每日≤3只，等权30%"""
    s = p2_by_date.get(date)
    if s is None or len(s) == 0:
        return {}
    s = s.dropna()
    idxd = idx_map.get(date)
    bullish = True
    if idxd and not pd.isna(idxd["ma250"]):
        bullish = idxd["close"] > idxd["ma250"]
    thr = 0.70 if bullish else 0.72
    cand = s[s >= thr]
    if len(cand) == 0:
        return {}
    top = cand.nlargest(min(3, len(cand)))
    w = 0.30 / len(top)
    return {code: w for code in top.index}


def composite_opt_signal(date, prices, i):
    """综合信号：composite_score≥0.50（99分位）+ ai_prob_5d≥0.55，每日≤3只，等权30%"""
    df = comp_by_date.get(date)
    if df is None or len(df) == 0:
        return {}
    df = df.copy().dropna()
    df = df[(df["composite_score"] >= 0.50) & (df["ai_prob_5d"] >= 0.55)]
    if len(df) == 0:
        return {}
    top = df.nlargest(min(3, len(df)), "composite_score")
    w = 0.30 / len(top)
    return {code: w for code in top.index}


# ====== 组合（真实统一账户撮合，有胜率） ======
def combo_signal(date, prices, i):
    """核心-卫星组合：bp+ep 60% + V375 20% + P2 20%（统一账户）"""
    w = {}
    for sub in [bp_ep_signal(date, prices, i, top_n=20, weight=0.60),
                v375_opt_signal(date, prices, i),
                p2_opt_signal(date, prices, i)]:
        for c, wt in sub.items():
            w[c] = w.get(c, 0) + wt
    return w


def run_one(name, freq, fn, min_hold=0, top_n=None):
    eng = Engine(name, freq, min_hold, top_n)
    return eng.run(fn)


def export_result(res, prefix):
    res["equity_curve"].to_csv(f"{OUT}/{prefix}_equity.csv", index=False, encoding="utf-8-sig")
    with open(f"{OUT}/{prefix}_trades.json", "w", encoding="utf-8") as f:
        json.dump(res["trades"], f, ensure_ascii=False, indent=2, default=str)
    summary = {k: float(v) if isinstance(v, (np.integer, np.floating)) else v
               for k, v in res.items() if k in ["name", "freq", "total_return_pct",
        "cagr_pct", "max_drawdown_pct", "total_trades", "win_rate_pct", "pl_ratio", "final_equity"]}
    with open(f"{OUT}/{prefix}_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info(f"  导出 {prefix}: 收益={res['total_return_pct']}% 交易={res['total_trades']}")


if __name__ == "__main__":
    t0 = time.time()
    log.info("=" * 60)
    log.info("V4 优化迭代回测启动（含最低持有期）")
    log.info("=" * 60)

    configs = [
        ("bp+ep_top20", "quarterly", lambda d, p, i: bp_ep_signal(d, p, i, top_n=20, weight=1.0), 5),
        ("bp+ep_top15", "quarterly", lambda d, p, i: bp_ep_signal(d, p, i, top_n=15, weight=1.0), 5),
        ("v375_opt", "daily", v375_opt_signal, 5),
        ("p2_opt", "daily", p2_opt_signal, 5),
        ("composite_opt", "daily", composite_opt_signal, 5),
        ("combo_core_sat", "daily", combo_signal, 5),
    ]
    results = {}
    for name, freq, fn, min_hold in configs:
        log.info(f"\n{'=' * 50}\n开始: {name} (频率={freq} 最低持有={min_hold}天)")
        r = run_one(name, freq, fn, min_hold)
        results[name] = r
        export_result(r, name)

    # ====== 对比总表 ======
    log.info("\n" + "=" * 60)
    log.info("多模型 KPI 对比")
    log.info("=" * 60)
    print(f"\n{'策略':<18}{'总收益':>10}{'年化':>10}{'回撤':>10}{'胜率':>8}{'盈亏比':>8}{'交易':>6}{'终值':>10}")
    for name, r in results.items():
        print(f"{name:<18}{r['total_return_pct']:>9.2f}%{r['cagr_pct']:>9.2f}%"
              f"{r['max_drawdown_pct']:>9.2f}%{r['win_rate_pct']:>7.1f}%{r['pl_ratio']:>8.2f}"
              f"{r['total_trades']:>6}{r['final_equity']:>10,.0f}")

    # 组合汇总 JSON
    combo_data = {k: {kk: vv for kk, vv in v.items() if kk != "trades" and kk != "equity_curve"}
                  for k, v in results.items()}
    with open(f"{OUT}/v4_comparison.json", "w", encoding="utf-8") as f:
        json.dump(combo_data, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - t0
    log.info(f"\n全部完成! 耗时 {elapsed:.0f}s ({elapsed/60:.1f}min)")
