# -*- coding: utf-8 -*-
"""屠龙系统多模型全域回测 V6 — 完整版（4 模型 + 年度逐笔明细 + 6 类图表 + 深度诊断）"""
import json, os, sqlite3, time, logging, base64
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft Sans Serif", "FangSong", "KaiTi"]
plt.rcParams["axes.unicode_minus"] = False

INITIAL_CAPITAL = 100000
START, END = "2016-06-30", "2026-08-13"
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\reports\tulong_backtest_v7"
os.makedirs(OUT, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(f"{OUT}/run.log", encoding="utf-8")])
log = logging.getLogger("T6")

def _norm(d):
    s = str(d).replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else str(d)

# ====== 数据加载 ======
log.info("加载数据...")
t0 = time.time()
conn = sqlite3.connect(DB)
cal = pd.read_sql("SELECT trade_date FROM trading_calendar ORDER BY trade_date", conn)
all_dates = sorted([_norm(d) for d in cal.iloc[:, 0]])
trade_dates = [d for d in all_dates if START <= d <= END]
si = pd.read_sql("SELECT code, name, is_st FROM stock_info", conn)
names = si.set_index("code")["name"].to_dict()
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
q_all = pd.read_sql("SELECT code, date, open, close FROM stock_daily_meta_qfq", qconn)
q_all["date"] = q_all["date"].apply(_norm)
q_all = q_all[q_all["code"].isin(ok_codes)]
bar_by_date = {d: grp.set_index("code") for d, grp in q_all.groupby("date")}
qconn.close()
log.info(f"数据加载完成: {time.time()-t0:.0f}s")

# ====== 引擎（含交易理由） ======
class Engine:
    def __init__(self, name, freq="daily", min_hold=8):
        self.name, self.freq, self.min_hold = name, freq, min_hold
        self.cash = INITIAL_CAPITAL
        self.pos = {}
        self.trades = []
        self.ecu = []

    def _buy(self, code, price, shares, date, reason):
        cost = price * shares
        comm = max(cost * 0.00025, 5.0)
        if cost + comm > self.cash:
            return
        self.cash -= cost + comm
        if code in self.pos:
            p = self.pos[code]
            ts = p["shares"] + shares
            p["cost"] = (p["cost"] * p["shares"] + cost) / ts
            p["shares"] = ts
        else:
            self.pos[code] = {"shares": shares, "cost": price, "buy_date": date, "reason": reason}

    def _sell(self, code, price, date, reason):
        p = self.pos.get(code)
        if not p:
            return
        proceeds = price * p["shares"]
        comm = max(proceeds * 0.00025, 5.0)
        stamp = proceeds * 0.0005
        net = proceeds - comm - stamp
        pnl = net - p["cost"] * p["shares"]
        self.cash += net
        self.trades.append({
            "code": code, "name": names.get(code, ""), "date": date,
            "buy_date": p["buy_date"], "buy_price": round(p["cost"], 2),
            "sell_price": round(price, 2), "shares": p["shares"],
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / (p["cost"] * p["shares"]) * 100, 2),
            "buy_reason": p["reason"], "sell_reason": reason
        })
        del self.pos[code]

    def nav(self, prices):
        return self.cash + sum(prices.get(c, 0) * v["shares"] for c, v in self.pos.items())

    def run(self, signal_fn, dates):
        if self.freq == "quarterly":
            rebal = set()
            pm = 0
            for d in dates:
                m = int(d[5:7])
                if m in (1, 4, 7, 10) and m != pm:
                    rebal.add(d); pm = m
        else:
            rebal = None
        for i, date in enumerate(dates):
            bars = bar_by_date.get(date)
            if bars is None or len(bars) == 0:
                self.ecu.append({"date": date, "equity": round(self.cash, 2)})
                continue
            prices = bars["close"].to_dict()
            opens = bars["open"].to_dict()
            for code in list(self.pos.keys()):
                cp = prices.get(code)
                if cp is None: continue
                cost = self.pos[code]["cost"]
                pnl_pct = (cp - cost) / cost
                held = i - next((j for j, d in enumerate(dates) if d == self.pos[code]["buy_date"]), 0)
                if pnl_pct <= -0.08:
                    self._sell(code, cp, date, "-8%止损")
                    continue
                if pnl_pct >= 0.10 and held >= self.min_hold:
                    trail = 0.06 if pnl_pct >= 0.30 else 0.10
                    if (max(cp, cost) - cp) / max(cp, cost) >= trail:
                        self._sell(code, cp, date, "阶梯止盈")
                        continue
            is_rebal = (rebal is None) or (date in rebal)
            if is_rebal:
                weights = signal_fn(date, prices, i)
                if weights:
                    for code in list(self.pos.keys()):
                        if code not in weights:
                            held = i - next((j for j, d in enumerate(dates) if d == self.pos[code]["buy_date"]), 0)
                            if held >= self.min_hold:
                                self._sell(code, prices.get(code, 0), date, "调仓换股")
                    trade_codes = [c for c in weights if not str(c).startswith("_reason")]
                    for code in sorted(trade_codes, key=lambda c: -weights[c]):
                        if code in self.pos: continue
                        price = opens.get(code, prices.get(code))
                        if price is None or price <= 0 or price > 30: continue
                        target = self.cash * weights[code]
                        if target < 1000: continue
                        shares = int(target / price / 100) * 100
                        if shares >= 100:
                            self._buy(code, price * 1.0003, shares, date, weights.get(f"_reason_{code}", weights.get("_reason", "信号")))
            self.ecu.append({"date": date, "equity": round(self.nav(prices), 2)})
            if (i + 1) % 600 == 0:
                log.info(f"[{self.name}] {i+1}/{len(dates)} 净值={self.nav(prices):.0f} 交易={len(self.trades)}")
        df_ec = pd.DataFrame(self.ecu)
        return {"name": self.name, "trades": self.trades, "equity_curve": df_ec}

# ====== 信号（含理由） ======
def bp_ep_signal(date, prices, i):
    df = fund_by_date.get(date)
    if df is None or len(df) < 20: return {}
    df = df.copy().dropna(subset=["close", "ep", "bp"])
    df = df[df["close"] <= 30]
    if len(df) < 20: return {}
    df["score"] = (df["ep"].rank(pct=True) + df["bp"].rank(pct=True)) / 2
    top = df.nlargest(20, "score")
    w = {code: 1.0 / 20 for code in top.index}
    w["_reason"] = "bp+ep季度调仓Top20"
    return w

def v375_signal(date, prices, i):
    s = v375_by_date.get(date)
    if s is None or len(s) == 0: return {}
    s = s.dropna()
    cand = s[s >= 0.75]
    if len(cand) == 0: return {}
    top = cand.nlargest(min(3, len(cand)))
    w = {code: 0.25 / len(top) for code in top.index}
    w["_reason"] = "V375重训>=0.75"
    return w

def p2_signal(date, prices, i):
    s = p2_by_date.get(date)
    if s is None or len(s) == 0: return {}
    s = s.dropna()
    idxd = idx_map.get(date)
    bullish = idxd["close"] > idxd["ma250"] if idxd and not pd.isna(idxd["ma250"]) else True
    thr = 0.70 if bullish else 0.72
    cand = s[s >= thr]
    if len(cand) == 0: return {}
    top = cand.nlargest(min(3, len(cand)))
    w = {code: 0.30 / len(top) for code in top.index}
    w["_reason"] = f"P2{'牛' if bullish else '熊'}>={thr}"
    return w

def _is_bear(date):
    idxd = idx_map.get(date)
    if idxd and not pd.isna(idxd["ma250"]):
        return idxd["close"] < idxd["ma250"]
    return False

def combo_signal(date, prices, i):
    w = {}
    reasons = {}
    bear = _is_bear(date)
    # 熊市：V375/P2 权重降0（仅 bp+ep 保留）
    subs = [(bp_ep_signal(date, prices, i), "bp+ep")]
    if not bear:
        subs += [(v375_signal(date, prices, i), "V375"), (p2_signal(date, prices, i), "P2")]
    for sub, subr in subs:
        r = sub.pop("_reason", None)
        for c, wt in sub.items():
            w[c] = w.get(c, 0) + wt
            reasons[c] = r or subr
    for c in list(w.keys()):
        w[f"_reason_{c}"] = (reasons.get(c, "组合") + ("(熊市仅bp+ep)" if bear else ""))
    return w

# ====== 执行 ======
if __name__ == "__main__":
    log.info("=" * 60)
    strategies = [
        ("bp+ep", bp_ep_signal, "quarterly"),
        ("v375_retrained", v375_signal, "daily"),
        ("p2", p2_signal, "daily"),
        ("combo_60_20_20", combo_signal, "daily"),
    ]
    results = {}
    for name, fn, freq in strategies:
        log.info(f"运行 {name} (频率={freq})")
        t1 = time.time()
        eng = Engine(name, freq, min_hold=8)
        results[name] = eng.run(fn, trade_dates)
        log.info(f"{name}: {len(results[name]['trades'])} 笔交易, 耗时 {time.time()-t1:.0f}s")
        # 保存
        results[name]["equity_curve"].to_csv(f"{OUT}/{name}_equity.csv", index=False, encoding="utf-8-sig")
        with open(f"{OUT}/{name}_trades.json", "w", encoding="utf-8") as f:
            json.dump(results[name]["trades"], f, ensure_ascii=False, indent=2, default=str)
    with open(f"{OUT}/all_results.json", "w", encoding="utf-8") as f:
        json.dump({k: {"n_trades": len(v["trades"]), "final_equity": v["equity_curve"]["equity"].iloc[-1]} for k, v in results.items()}, f, ensure_ascii=False, indent=2)
    log.info("回测完成，开始生成报告...")
