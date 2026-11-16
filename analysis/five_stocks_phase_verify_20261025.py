# -*- coding: utf-8 -*-
"""5只候选·相位条件复验与上涨概率排名 (20261025晚, 只读零交易权)。

对象: 亚星锚链601890/中光学002189/展鹏科技603488/四创电子600990/航新科技300424。
口径(与生产复验完全一致): 事件=A格状态进入(pos250<30且|chg5|<3)+冷却5+close<=30+非ST日,
  t20e=收盘入场×0.92止损/T+20收盘; 增补T+5/T+10纯持有与t5e/t10e止损口径。
相位: 等权指数(全宇宙chg均值累乘)收盘 vs MA60 → bull/bear; 当前相位标注。
排名口径(跑前冻结): **bear相位win20e**(当前状态的条件胜率)为主序; 全相位win20e与2026年事件为辅序。
输出: analysis/five_stocks_phase_verify_20261025_result.json
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
OUT = r"D:\zcodesj\analysis\five_stocks_phase_verify_20261025_result.json"

STOCKS = {
    "601890": "亚星锚链", "002189": "中光学", "603488": "展鹏科技",
    "600990": "四创电子", "300424": "航新科技",
}
LOAD_START = "20190101"
COOLDOWN = 5
STOP = 0.92


def tke_sim(close, ev, hold):
    n = len(close)
    ev = ev[(ev + hold < n)]
    if len(ev) == 0:
        return np.array([], dtype=bool), np.array([], dtype=float)
    win = np.lib.stride_tricks.sliding_window_view(close, hold)[ev + 1]
    stop = close[ev, None] * STOP
    broke = win <= stop
    has = broke.any(axis=1)
    first = np.argmax(broke, axis=1)
    xp = np.where(has, win[np.arange(len(ev)), first], close[ev + hold])
    ret = xp / close[ev] - 1.0
    return ret > 0, ret


def main():
    st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
    st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
    st_con.close()
    st_by = {}
    for c, d in zip(st_rows["code"].str[-6:], st_rows["date"].astype("int64")):
        st_by.setdefault(c, set()).add(int(d))

    con = sqlite3.connect(QFQ, uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,close,change_pct FROM stock_daily_meta_qfq WHERE date>='{LOAD_START}' "
        "ORDER BY code,date", con)
    con.close()
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    df["d"] = df["date"].astype("int64")

    # 相位(全宇宙等权)
    mkt = df.groupby("d")["change_pct"].mean().reset_index().sort_values("d")
    lvl = (1.0 + mkt["change_pct"].fillna(0) / 100.0).cumprod()
    ma60 = lvl.rolling(60, min_periods=60).mean()
    bull = (lvl > ma60).to_numpy()
    bull_by_d = dict(zip(mkt["d"].to_numpy(dtype="int64"), bull))
    cur_d = int(mkt["d"].iloc[-1])
    cur_bull = bool(bull[-1])
    print(f"[phase] 最新日{cur_d} 相位={'牛市(指数>MA60)' if cur_bull else '弱势(指数<MA60)'}")

    g = df.groupby("code")["close"]
    df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
    mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
    mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
    df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)

    d_g = df["d"].to_numpy(dtype="int64")
    close_g = df["close"].to_numpy(dtype=float)
    is_A_g = df["is_A"].fillna(False).to_numpy()

    results = {}
    for code, name in STOCKS.items():
        gi = np.sort(df.groupby("code", sort=False).indices[code])
        c = close_g[gi]
        d_loc = d_g[gi]
        st_d = st_by.get(code, ())
        ok = ~np.isin(d_loc, np.fromiter(st_d, dtype="int64", count=len(st_d))) if st_d else np.ones(len(gi), bool)
        isA = is_A_g[gi]
        prevA = np.append([False], isA[:-1])
        local = np.nonzero(isA & ~prevA & ok & (c <= 30.0))[0]
        keep, last = [], -10**9
        for i in local:
            if i - last > COOLDOWN:
                keep.append(i)
                last = i
        ev = np.array(keep, dtype=int)

        rec = {"name": name, "n_total": int(len(ev))}
        for hold in (5, 10, 20):
            ev_h = ev[ev + hold < len(c)]
            w, r = tke_sim(c, ev, hold)
            phases = np.array([bull_by_d[int(d_loc[i])] for i in ev_h], dtype=bool)
            years = d_loc[ev_h] // 10000
            rec[f"t{hold}"] = {
                "all_win": round(float(w.mean()) * 100, 1),
                "all_mean": round(float(r.mean()) * 100, 2),
                "bear_n": int((~phases).sum()),
                "bear_win": round(float(w[~phases].mean()) * 100, 1) if (~phases).any() else None,
                "bear_mean": round(float(r[~phases].mean()) * 100, 2) if (~phases).any() else None,
                "bull_win": round(float(w[phases].mean()) * 100, 1) if phases.any() else None,
            }
            if hold == 20:
                rec["by_year"] = {int(y): round(float(w[years == y].mean()) * 100, 1)
                                  for y in np.unique(years) if (years == y).sum() >= 5}
                rec["y2026"] = {"n": int((years == 2026).sum()),
                                "win": round(float(w[years == 2026].mean()) * 100, 1) if (years == 2026).sum() else None,
                                "mean": round(float(r[years == 2026].mean()) * 100, 2) if (years == 2026).sum() else None}
                # 2026事件明细
                ev26 = [(int(d_loc[i]), round(float(xp), 3)) for i, xp in zip(ev_h[years == 2026], r[years == 2026])]
                rec["y2026_events"] = [{"d": d, "ret": rr} for d, rr in ev26]
        results[code] = rec

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"current_phase": "bear" if not cur_bull else "bull",
                   "bear_pool_baseline_pct": 45.2, "stocks": results}, f, ensure_ascii=False, indent=1)

    print(f"\n{'代码':<8}{'名称':<7}{'n':>4} | {'全win20':>7} {'弱势win20':>9} {'弱势均值':>8} | {'T5win':>6} {'T10win':>7} | {'2026n':>5} {'2026win':>7}")
    for code, r in sorted(results.items(), key=lambda kv: -(kv[1]["t20"]["bear_win"] or -1)):
        t20, t5, t10 = r["t20"], r["t5"], r["t10"]
        print(f"{code:<8}{r['name']:<7}{r['n_total']:>4} | {t20['all_win']:>6.1f}% {t20['bear_win']:>8.1f}% "
              f"{t20['bear_mean']:>7.2f}% | {t5['all_win']:>5.1f}% {t10['all_win']:>6.1f}% | "
              f"{r['y2026']['n']:>5} {str(r['y2026']['win'])+'%' if r['y2026']['win'] is not None else '—':>7}")
        print(f"        分年win20: {r['by_year']}  2026事件: {r['y2026_events']}")


if __name__ == "__main__":
    sys.exit(main())
