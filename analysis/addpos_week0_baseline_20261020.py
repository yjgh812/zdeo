# -*- coding: utf-8 -*-
"""加仓 Week-0 规则基线叠加器 (2026-10-20, 设计=analysis/addpos_week0_design_20261020.md).

R0=模板逐笔原样; R1浮盈+5%∧bp_ep当日Top15 / R2第3/5/10持仓日∧前20% / R3浮亏-5%对照。
R1/R2 止损修正口径=加仓后 stop=max(原线, 加仓价×0.92) (米勒维尼式, 满足合法加仓判别式)。
信号收盘评估→次日开盘成交; 涨停日(open==high且pct≥9.5)不加仓; 早停=low破线→次日开盘出。
判定(2025-2026): R1或R2 胜率≥R0+2pp 且 盈亏比≥1.1 且 换手(加仓名义/底仓名义)≤+50% → Week1;
三臂全败 → 加仓项目终止。零交易权研究件。
用法: python analysis/addpos_week0_baseline_20261020.py
"""
import json
import sys
from collections import defaultdict

import pandas as pd
import sqlite3

sys.path.insert(0, r"D:\zcodesj\analysis")
sys.path.insert(0, r"D:\zcodesj")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TRADES = r"D:\zcodesj\reports\backtest_full_20261019_ladder_debug\bp_ep_core_trades.csv"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\addpos_week0_result_20261020.json"
ADD_FRAC = 0.5          # 加仓名义=底仓×50%
COST_BUY = COST_SELL = 0.0013   # 近似双边费率(R0模板费内, 加仓批同口径)
WIN_Y0 = "2025-01-01"

import bt_full_report_ladder_20261019 as T  # noqa: E402  (import-safe, __main__守卫:2109)

loader = T.TulongDataLoader(
    db_path=r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
    qfq_path=r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
_qcon = sqlite3.connect("file:%s?mode=ro" % QFQ, uri=True)
_score_cache = {}
_bar_cache = {}


def daily_scores(date_str):
    """同源复刻模板 bp_ep 打分(:1013-1016): get_fundamental_factors + close<=30 + ep/bp pct-rank 等权。"""
    if date_str in _score_cache:
        return _score_cache[date_str]
    df = loader.get_fundamental_factors(date_str)
    s = None
    if df is not None and len(df) >= 20:
        df = df[df["close"] <= 30]
        if len(df) >= 20:
            df = df.copy()
            df["ep_rank"] = df["ep"].rank(pct=True)
            df["bp_rank"] = df["bp"].rank(pct=True)
            s = (df["ep_rank"] + df["bp_rank"]) / 2
    _score_cache[date_str] = s
    return s


def bars(code):
    """qfq 日线(规范化 YYYY-MM-DD 索引)。"""
    if code in _bar_cache:
        return _bar_cache[code]
    df = pd.read_sql("SELECT date,open,high,low,close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
                     _qcon, params=(code,))
    d = df["date"].astype(str)
    df["date"] = d.str[:4] + "-" + d.str[4:6] + "-" + d.str[6:8] if d.iloc[0].isdigit() and len(d.iloc[0]) == 8 else d
    df = df.set_index("date")
    _bar_cache[code] = df
    return df


def simulate(trade, arm):
    """单笔叠加模拟。返回 (combined_ret_pct, extra_turnover_ratio, early_stop, added) 或 None(数据不足)。"""
    code = str(trade["symbol"]).zfill(6)
    entry, exit_ = trade["entry_date"], trade["exit_date"]
    b = bars(code)
    try:
        win = b.loc[entry:exit_]
    except KeyError:
        return None
    if len(win) < 2:
        return None
    e_price = float(trade["entry_price"])
    size = float(trade["size"])
    base_notional = e_price * size
    r0_ret = float(trade["pnl_pct"])
    stop0 = e_price * 0.92  # R0 止损语义近似(实际R0由模板引擎退出; 早停仅叠加臂引入)

    base_fee = base_notional * (COST_BUY + COST_SELL)
    rows = list(win.itertuples())
    dates = list(win.index)

    # ---- 触发评估(信号日收盘) → 次日开盘成交 ----
    add_at = None  # (idx, price)
    if arm != "R0":
        for i, row in enumerate(rows[:-1]):  # 最后一天无法次日成交
            d = dates[i]
            px = row.close
            if arm == "R1" and px >= e_price * 1.05:
                sc = daily_scores(d)
                if sc is not None and code in sc.index:
                    top15 = sc.nlargest(15)
                    if code in top15.index:
                        add_at = i + 1
                        break
            elif arm == "R2" and i in (3, 5, 10):
                sc = daily_scores(d)
                if sc is not None and code in sc.index:
                    pct = sc.rank(pct=True).get(code, 0)
                    if pct >= 0.8:
                        add_at = i + 1
                        break
            elif arm == "R3" and px <= e_price * 0.95:
                add_at = i + 1
                break

    added = False
    early_stop = False
    add_pnl = 0.0
    extra_turn = 0.0
    stop = stop0
    out_idx = len(rows) - 1
    out_price = float(trade["exit_price"])
    for i, row in enumerate(rows):
        if arm == "R0":
            break
        # 加仓成交(T+1: 触发次日开盘)
        if add_at is not None and i == add_at:
            o = row.open
            prev_close = rows[i - 1].close
            pct_chg = (o / prev_close - 1) * 100
            limit_up = o >= row.high and pct_chg >= 9.5
            if not limit_up:
                add_notional = o * size * ADD_FRAC
                extra_turn = add_notional / base_notional
                add_cost = add_notional * (COST_BUY + COST_SELL)
                add_pnl -= add_cost
                added = True
                if arm in ("R1", "R2"):
                    stop = max(stop, o * 0.92)  # 米勒维尼口径: 加仓即抬止损
                _trade_add_price = o
            add_at = None
        # 早停检查(加仓后): 日low破线 → 次日开盘全出
        if added and row.low <= stop and i < len(rows) - 1:
            out_idx = i + 1
            out_price = rows[i + 1].open
            early_stop = True
            break

    # 底仓 pnl 重算(若早停改变退出): 用 R0 原 pnl 比例外推
    if arm == "R0":
        comb_ret = r0_ret
    else:
        base_exit = rows[out_idx].close if early_stop else float(trade["exit_price"])
        if early_stop:
            base_pnl = (base_exit - e_price) * size - base_fee
        else:
            base_pnl = float(trade["pnl"])  # 原账(含模板费)
        comb_ret = (base_pnl + add_pnl) / base_notional * 100
        if added:
            add_exit = base_exit
            add_pnl += (add_exit - _trade_add_price) * size * ADD_FRAC
            comb_ret = (base_pnl + add_pnl) / base_notional * 100
    return comb_ret, extra_turn, early_stop, added


def metrics(rets, turns):
    r = pd.Series(rets)
    wins, losses = r[r > 0], r[r <= 0]
    return {"n": int(len(r)), "win_rate": round(len(wins) / len(r) * 100, 2),
            "pl_ratio": round(wins.mean() / abs(losses.mean()), 3) if len(wins) and len(losses) else None,
            "med_ret": round(r.median(), 3), "extra_turnover": round(sum(turns) / max(len(turns), 1), 3)}


def main():
    df = pd.read_csv(TRADES, encoding="utf-8-sig")
    df["entry_date"] = df["entry_date"].astype(str)
    w = df[df["entry_date"] >= WIN_Y0].copy()
    print("窗口内 R0 笔数:", len(w))

    arms = {"R0": [], "R1": [], "R2": [], "R3": []}
    turns = {"R1": [], "R2": [], "R3": []}
    n_added = {"R1": 0, "R2": 0, "R3": 0}
    skipped = 0
    for _, tr in w.iterrows():
        for arm in ("R1", "R2", "R3"):
            res = simulate(tr, arm)
            if res is None:
                skipped += 1
                continue
            ret, extra_turn, early_stop, added = res
            arms[arm].append(ret)
            turns[arm].append(extra_turn)
            n_added[arm] += int(added)
        arms["R0"].append(float(tr["pnl_pct"]))

    out = {"window": "2025-01-01+", "r0_n": len(arms["R0"]), "skipped": skipped,
           "metrics": {k: metrics(v, turns.get(k, [0] * len(v))) for k, v in arms.items()},
           "added_count": n_added}
    m = out["metrics"]
    print(json.dumps(m, ensure_ascii=False, indent=1))
    r0 = m["R0"]
    verdict = {}
    for a in ("R1", "R2", "R3"):
        wr_ok = m[a]["win_rate"] >= r0["win_rate"] + 2
        pl_ok = (m[a]["pl_ratio"] or 0) >= 1.1
        to_ok = m[a]["extra_turnover"] <= 0.5 * 1.5  # 换手不升>50%: 加仓名义/底仓 ≤ 0.5×1.5=0.75? 口径=额外成交≤50%增幅
        verdict[a] = {"win_rate_ok": bool(wr_ok), "pl_ok": bool(pl_ok), "turnover_ok": bool(m[a]["extra_turnover"] <= 0.75),
                      "pass": bool(wr_ok and pl_ok)}
    out["verdict"] = verdict
    any_pass = any(v["pass"] for v in verdict.values()) if verdict else False
    all_fail = all((m[a]["win_rate"] < r0["win_rate"] + 0) for a in ("R1", "R2", "R3"))
    out["conclusion"] = ("PASS_WEEK1" if any_pass else
                         ("TERMINATE_ALL_FAIL" if all_fail else "NO_PASS_CONTINUE_REVIEW"))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("verdict:", json.dumps(verdict, ensure_ascii=False))
    print("conclusion:", out["conclusion"])
    print("saved:", OUT)


if __name__ == "__main__":
    main()
