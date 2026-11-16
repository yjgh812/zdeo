# -*- coding: utf-8 -*-
"""屠龙 V5 — 样本外(OOS)验证：IS 2016-2023 / OOS 2024-2026
验证组合策略(60/20/20) + V375优化 + bp+ep 参数冻结下的稳定性
"""
import json, os, sqlite3, time, logging
import pandas as pd
import numpy as np

INITIAL_CAPITAL = 100000
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\reports\tulong_backtest_20260813"
os.makedirs(OUT, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(f"{OUT}/run_v5_oos.log", encoding="utf-8")])
log = logging.getLogger("T5")

def _norm(d):
    s = str(d).replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else str(d)

# ====== 数据加载（全区间，供分段时间窗回测复用） ======
log.info("加载数据...")
t0 = time.time()
conn = sqlite3.connect(DB)

cal = pd.read_sql("SELECT trade_date FROM trading_calendar ORDER BY trade_date", conn)
all_dates = sorted([_norm(d) for d in cal.iloc[:, 0]])

si = pd.read_sql("SELECT code, is_st FROM stock_info", conn)
ok_codes = set(si[pd.to_numeric(si["is_st"], errors="coerce").fillna(0) == 0]["code"])
ok_codes = {c for c in ok_codes if not c.startswith(("688", "689", "8", "4", "920"))}

idx_df = pd.read_sql("SELECT date, close FROM index_daily WHERE index_code='000001' ORDER BY date", conn)
idx_df["date"] = idx_df["date"].apply(_norm)
idx_df["ma250"] = idx_df["close"].rolling(250, min_periods=100).mean()
idx_map = {r["date"]: {"close": r["close"], "ma250": r["ma250"]} for _, r in idx_df.iterrows()}

v375_all = pd.read_sql("SELECT code, date, v375_prob FROM stock_signals_v375", conn)
v375_all["date"] = v375_all["date"].apply(_norm)
v375_by_date = {d: grp.set_index("code")["v375_prob"] for d, grp in v375_all.groupby("date")}

p2_all = pd.read_sql("SELECT code, date, p2_prob FROM stock_signals_p2", conn)
p2_all["date"] = p2_all["date"].apply(_norm)
p2_by_date = {d: grp.set_index("code")["p2_prob"] for d, grp in p2_all.groupby("date")}

fund_all = pd.read_sql("SELECT code, date, close, ep, bp FROM stock_factors_fundamental", conn)
fund_all["date"] = fund_all["date"].apply(_norm)
fund_by_date = {d: grp.set_index("code")[["close", "ep", "bp"]] for d, grp in fund_all.groupby("date")}
conn.close()

qconn = sqlite3.connect(QFQ)
q_all = pd.read_sql("SELECT code, date, open, high, low, close FROM stock_daily_meta_qfq", qconn)
q_all["date"] = q_all["date"].apply(_norm)
q_all = q_all[q_all["code"].isin(ok_codes)]
bar_by_date = {d: grp.set_index("code") for d, grp in q_all.groupby("date")}
qconn.close()
log.info(f"数据加载完成: {time.time()-t0:.0f}s")

# ====== 引擎（参数完全冻结，与 V4 一致） ======
class Engine:
    def __init__(self, name, freq="daily", min_hold=5):
        self.name = name
        self.freq = freq
        self.min_hold = min_hold
        self.cash = INITIAL_CAPITAL
        self.pos = {}
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
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / (p["cost"] * p["shares"]) * 100, 2),
            "reason": reason
        })
        del self.pos[code]

    def nav(self, prices):
        pos_val = sum(prices.get(c, 0) * v["shares"] for c, v in self.pos.items())
        return self.cash + pos_val

    def run(self, signal_fn, dates):
        if self.freq == "quarterly":
            rebal_dates = set()
            prev_m = 0
            for d in dates:
                m = int(d[5:7])
                if m in (1, 4, 7, 10) and m != prev_m:
                    rebal_dates.add(d)
                    prev_m = m
        else:
            rebal_dates = None

        n = len(dates)
        for i, date in enumerate(dates):
            bars = bar_by_date.get(date)
            if bars is None or len(bars) == 0:
                self.ecu.append({"date": date, "equity": round(self.cash, 2)})
                continue
            prices = bars["close"].to_dict()
            opens = bars["open"].to_dict()

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

            is_rebal = (rebal_dates is None) or (date in rebal_dates)
            if is_rebal:
                weights = signal_fn(date, prices, i)
                if weights:
                    for code in list(self.pos.keys()):
                        if code not in weights:
                            if (i - self.pos[code]["buy_i"]) >= self.min_hold:
                                self._sell(code, prices.get(code, 0), date, "rebalance")
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

        df_ec = pd.DataFrame(self.ecu)
        total_ret = (df_ec["equity"].iloc[-1] / INITIAL_CAPITAL - 1) * 100
        years = max(1, len(dates) / 245)
        cagr = ((1 + total_ret / 100) ** (1 / years) - 1) * 100
        wins = sum(1 for t in self.trades if t["pnl"] > 0)
        win_rate = wins / len(self.trades) * 100 if self.trades else 0
        peak = df_ec["equity"].cummax()
        dd = ((df_ec["equity"] - peak) / peak * 100).min()
        win_p = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        loss_p = [t["pnl"] for t in self.trades if t["pnl"] <= 0]
        pl_ratio = (sum(win_p)/len(win_p) / abs(sum(loss_p)/len(loss_p))) if win_p and loss_p else 0
        return {
            "name": self.name, "total_return_pct": round(total_ret, 2),
            "cagr_pct": round(cagr, 2), "max_drawdown_pct": round(dd, 2),
            "total_trades": len(self.trades), "win_rate_pct": round(win_rate, 1),
            "pl_ratio": round(pl_ratio, 2), "final_equity": round(df_ec["equity"].iloc[-1], 2),
            "trades": self.trades, "equity_curve": df_ec,
        }

# ====== 信号（参数冻结：与 V4 完全一致） ======
def bp_ep_signal(date, prices, i, top_n=20, weight=1.0):
    df = fund_by_date.get(date)
    if df is None or len(df) < 20: return {}
    df = df.copy().dropna(subset=["close", "ep", "bp"])
    df = df[df["close"] <= 30]
    if len(df) < 20: return {}
    df["score"] = (df["ep"].rank(pct=True) + df["bp"].rank(pct=True)) / 2
    top = df.nlargest(top_n, "score")
    w = weight / len(top)
    return {code: w for code in top.index}

def v375_opt_signal(date, prices, i):
    s = v375_by_date.get(date)
    if s is None or len(s) == 0: return {}
    s = s.dropna()
    cand = s[s >= 0.80]
    if len(cand) == 0: return {}
    top = cand.nlargest(min(2, len(cand)))
    w = 0.20 / len(top)
    return {code: w for code in top.index}

def p2_opt_signal(date, prices, i):
    s = p2_by_date.get(date)
    if s is None or len(s) == 0: return {}
    s = s.dropna()
    idxd = idx_map.get(date)
    bullish = True
    if idxd and not pd.isna(idxd["ma250"]):
        bullish = idxd["close"] > idxd["ma250"]
    thr = 0.70 if bullish else 0.72
    cand = s[s >= thr]
    if len(cand) == 0: return {}
    top = cand.nlargest(min(3, len(cand)))
    w = 0.30 / len(top)
    return {code: w for code in top.index}

def combo_signal(date, prices, i):
    w = {}
    for sub in [bp_ep_signal(date, prices, i, top_n=20, weight=0.60),
                v375_opt_signal(date, prices, i),
                p2_opt_signal(date, prices, i)]:
        for c, wt in sub.items():
            w[c] = w.get(c, 0) + wt
    return w

# ====== 时间段 ======
IS_START, IS_END = "2016-06-30", "2023-12-31"
OOS_START, OOS_END = "2024-01-01", "2026-08-13"

def run_window(name, signal_fn, freq, dates):
    log.info(f"运行 {name} [{dates[0]}~{dates[-1]}] 共{len(dates)}日")
    eng = Engine(name, freq, min_hold=5)
    return eng.run(signal_fn, dates)

if __name__ == "__main__":
    t0 = time.time()
    log.info("=" * 60)
    log.info("V5 样本外验证启动（IS 2016-2023 / OOS 2024-2026）")
    log.info("=" * 60)

    is_dates = [d for d in all_dates if IS_START <= d <= IS_END]
    oos_dates = [d for d in all_dates if OOS_START <= d <= OOS_END]

    strategies = [
        ("bp+ep", lambda d, p, i: bp_ep_signal(d, p, i, top_n=20, weight=1.0), "quarterly"),
        ("v375_opt", v375_opt_signal, "daily"),
        ("combo_60_20_20", combo_signal, "daily"),
    ]

    results = {}
    for name, fn, freq in strategies:
        r_is = run_window(f"IS_{name}", fn, freq, is_dates)
        r_oos = run_window(f"OOS_{name}", fn, freq, oos_dates)
        results[name] = {"IS": r_is, "OOS": r_oos}
        # 输出
        for phase, r in [("IS", r_is), ("OOS", r_oos)]:
            pfx = f"{phase}_{name}"
            r["equity_curve"].to_csv(f"{OUT}/{pfx}_equity.csv", index=False, encoding="utf-8-sig")
            with open(f"{OUT}/{pfx}_trades.json", "w", encoding="utf-8") as f:
                json.dump(r["trades"], f, ensure_ascii=False, default=str)
            summary = {k: float(v) if isinstance(v, (np.integer, np.floating)) else v
                       for k, v in r.items() if k in ["name", "total_return_pct", "cagr_pct",
                       "max_drawdown_pct", "total_trades", "win_rate_pct", "pl_ratio", "final_equity"]}
            with open(f"{OUT}/{pfx}_summary.json", "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)

    # ====== OOS 稳定性判定 ======
    log.info("\n" + "=" * 60)
    log.info("样本外稳定性分析")
    log.info("=" * 60)
    print(f"\n{'策略':<16}{'阶段':<6}{'总收益':>9}{'年化':>9}{'回撤':>9}{'胜率':>8}{'盈亏比':>8}{'交易':>6}")
    verdicts = []
    for name, phases in results.items():
        for phase in ["IS", "OOS"]:
            r = phases[phase]
            print(f"{name:<16}{phase:<6}{r['total_return_pct']:>8.2f}%{r['cagr_pct']:>8.2f}%"
                  f"{r['max_drawdown_pct']:>8.2f}%{r['win_rate_pct']:>7.1f}%{r['pl_ratio']:>8.2f}"
                  f"{r['total_trades']:>6}")
        r_is, r_oos = phases["IS"], phases["OOS"]
        # 判定：OOS 年化 vs IS 年化
        if r_oos["cagr_pct"] > 0:
            ratio = r_oos["cagr_pct"] / max(r_is["cagr_pct"], 0.1)
            verdict = "✅ OOS 稳定盈利" if ratio >= 0.5 else "🟡 OOS 衰减但盈利"
        else:
            verdict = "🔴 OOS 失效"
        verdicts.append((name, verdict, r_is["cagr_pct"], r_oos["cagr_pct"]))
        log.info(f"{name}: {verdict} (IS CAGR={r_is['cagr_pct']:.2f}% → OOS CAGR={r_oos['cagr_pct']:.2f}%)")

    # 保存判定
    with open(f"{OUT}/v5_oos_verdict.json", "w", encoding="utf-8") as f:
        json.dump([{"strategy": n, "verdict": v, "is_cagr": is_, "oos_cagr": oos}
                   for n, v, is_, oos in verdicts], f, ensure_ascii=False, indent=2)

    elapsed = time.time() - t0
    log.info(f"\n完成! 耗时 {elapsed:.0f}s ({elapsed/60:.1f}min)")
