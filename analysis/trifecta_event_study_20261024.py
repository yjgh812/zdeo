# -*- coding: utf-8 -*-
"""三重共振筛选条件历史事件研究(20261024) — 依据 prereg_trifecta_screen_20261024.md 执行。

规则(prereg冻结): 信号 = 趋势T(MACD零轴上多头V1/任意金叉V2) ∧ 突破B(创20日新高) ∧ 放量V(量>=1.5x前5日均量且涨)。
入场=信号次日开盘(next-bar); 出场=信号日后5/10/20交易日收盘; 冷却10交易日去重为主口径。
基准=同信号日全宇宙等权同期限前向收益。判定阈值见prereg §6。

只读两库(qfq价格真源 + st_history ST标记), 零写入生产数据, 零交易权。
输出: analysis/trifecta_event_study_20261024_result.json + stdout摘要。
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STDB = r"D:\zcodesj\data\st_history.db"
OUT_JSON = r"D:\zcodesj\analysis\trifecta_event_study_20261024_result.json"

LOOKBACK_START = "20190101"   # EMA 预热
EVAL_START = "20200104"       # prereg §4 信号评估窗口
EVAL_END = "20261023"
COOLDOWN = 10                 # prereg 主口径冷却期
ST_DB_START = 20160104        # st_daily 覆盖起点

# 通过/证伪阈值 (prereg §6, V1 冷却口径)
PASS_MIN_EVENTS = 2000
PASS_MIN_WINRATE_20D = 0.52
PASS_MIN_EXCESS_20D = 0.005
PASS_MEDIAN_20D_GT0 = True


def norm_code(raw: str) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def in_universe(code6: str) -> bool:
    if code6.startswith(("688", "689", "4", "8", "92")):
        return False
    return len(code6) == 6 and code6.isdigit()


def load_st_dates_by_code() -> dict:
    con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", con)
    con.close()
    out = {}
    for c, d in zip(df["code"].map(norm_code), df["date"].astype("int64")):
        out.setdefault(c, set()).add(int(d))
    return out


def cooldown_mask(sig: np.ndarray, cooldown: int) -> np.ndarray:
    """主口径: 触发后 cooldown 个交易日内不再计新事件。"""
    out = np.zeros(len(sig), dtype=bool)
    last = -10**9
    for i in range(len(sig)):
        if sig[i] and (i - last) > cooldown:
            out[i] = True
            last = i
    return out


def forward_stats(close, open_, high, dates, st_ok):
    """per-code numpy 前向收益与20日窗口内最低回撤(相对次日开盘入场)。"""
    n = len(close)
    nxt_open = np.full(n, np.nan)
    if n > 1:
        nxt_open[:-1] = open_[1:]
    r = {}
    for k in (5, 10, 20):
        ex = np.full(n, np.nan)
        if n > k:
            ex[:n - k] = close[k:]
        r[k] = np.where(nxt_open > 0, ex / nxt_open - 1.0, np.nan)
    hh = pd.Series(high).rolling(20, min_periods=20).min().shift(-19).to_numpy()
    dd20 = np.where(nxt_open > 0, hh / nxt_open - 1.0, np.nan)
    valid = (~np.isnan(nxt_open)) & (nxt_open > 0) & st_ok & (dates >= int(EVAL_START)) & (dates <= int(EVAL_END))
    return r, dd20, valid, nxt_open


def main():
    t0 = time.time()
    st_by_code = load_st_dates_by_code()
    print(f"[1/4] ST标记加载完成 is_st=1 股日数={sum(len(v) for v in st_by_code.values())}  ({time.time()-t0:.1f}s)")

    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,open,high,close,volume,change_pct FROM stock_daily_meta_qfq "
        f"WHERE date>='{LOOKBACK_START}' ORDER BY code,date", con)
    con.close()
    df["code6"] = df["code"].map(norm_code)
    df = df[in_universe_batch(df["code6"])].copy()
    df["d"] = df["date"].astype("int64")
    df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
    print(f"[2/4] qfq加载 行数={len(df)} 股票数={df['code6'].nunique()}  ({time.time()-t0:.1f}s)")

    st_dates_by_code = st_by_code  # code6 -> set(st dates)

    events = {"V1": [], "V2": []}
    raw_counts = {"V1": 0, "V2": 0}
    base_rows = []

    grouped = list(df.groupby("code6", sort=False))
    for idx, (code, g) in enumerate(grouped):
        g = g.sort_values("d")
        close = g["close"].to_numpy(dtype=float)
        open_ = g["open"].to_numpy(dtype=float)
        high = g["high"].to_numpy(dtype=float)
        vol = g["volume"].to_numpy(dtype=float)
        chg = g["change_pct"].to_numpy(dtype=float)
        dates = g["d"].to_numpy(dtype="int64")
        st_d = st_dates_by_code.get(code)
        if st_d:
            st_ok = ~np.isin(dates, np.fromiter(st_d, dtype="int64", count=len(st_d)))
        else:
            st_ok = np.ones(len(dates), dtype=bool)

        s = pd.Series(close)
        ema12 = s.ewm(span=12, adjust=False).mean()
        ema26 = s.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        dif = dif.to_numpy(); dea = dea.to_numpy()

        brk = close >= pd.Series(close).rolling(20, min_periods=20).max().to_numpy()
        vol_ok = vol >= 1.5 * pd.Series(vol).rolling(5, min_periods=5).mean().shift(1).to_numpy()
        up = chg > 0

        sig = {
            "V1": (dif > dea) & (dif > 0) & brk & vol_ok & up,
            "V2": (dif > dea) & brk & vol_ok & up,
        }

        r, dd20, valid, nxt_open = forward_stats(close, open_, high, dates, st_ok)

        # 基准累计: 评估窗内全部有效股日的前向收益按日期聚合
        vmask = valid & ~np.isnan(r[20])
        if vmask.any():
            dts = dates[vmask]
            base_rows.append(pd.DataFrame({
                "date": dts, "one": 1.0,
                "r5": r[5][vmask], "r10": r[10][vmask], "r20": r[20][vmask],
                "w20": (r[20][vmask] > 0).astype(float),
            }))

        for v, arr in sig.items():
            raw_counts[v] += int(arr[valid].sum())
            ev = cooldown_mask(arr, COOLDOWN) & valid & ~np.isnan(r[20])
            for i in np.nonzero(ev)[0]:
                events[v].append({
                    "code": code, "date": int(dates[i]),
                    "r5": float(r[5][i]), "r10": float(r[10][i]), "r20": float(r[20][i]),
                    "dd20": float(dd20[i]) if not np.isnan(dd20[i]) else np.nan,
                })
        if (idx + 1) % 1000 == 0:
            print(f"    ... {idx+1}/{len(grouped)}  ({time.time()-t0:.1f}s)")

    print(f"[3/4] 信号计算完成  ({time.time()-t0:.1f}s)")
    base = pd.concat(base_rows, ignore_index=True)
    base_g = base.groupby("date").agg(n=("one", "sum"), m5=("r5", "mean"),
                                      m10=("r10", "mean"), m20=("r20", "mean"),
                                      w20=("w20", "mean"))

    result = {"generated_at": "2026-10-24", "window": [EVAL_START, EVAL_END],
              "cooldown": COOLDOWN, "variants": {}}

    for v in ("V1", "V2"):
        ev = pd.DataFrame(events[v])
        n_ev = len(ev)
        ev["year"] = (ev["date"] // 10000).astype(int)
        b20 = ev["date"].map(base_g["m20"])
        b5 = ev["date"].map(base_g["m5"])
        excess20 = ev["r20"] - b20
        stat = {
            "events": n_ev,
            "raw_events_no_cooldown": int(raw_counts[v]),
            "win5": float((ev["r5"] > 0).mean()),
            "win10": float((ev["r10"] > 0).mean()),
            "win20": float((ev["r20"] > 0).mean()),
            "mean5": float(ev["r5"].mean()),
            "mean10": float(ev["r10"].mean()),
            "mean20": float(ev["r20"].mean()),
            "median20": float(ev["r20"].median()),
            "baseline_mean20_matched": float(b20.mean()),
            "baseline_win20_matched": float(ev["date"].map(base_g["w20"]).mean()),
            "excess20_vs_matched": float(excess20.mean()),
            "median_excess20": float(excess20.median()),
            "dd20_p50": float(ev["dd20"].median()),
            "dd20_p95": float(ev["dd20"].quantile(0.05)),
            "by_year": {
                int(y): {"events": int((ev["year"] == y).sum()),
                         "win20": float(ev.loc[ev["year"] == y, "r20"].gt(0).mean()) if (ev["year"] == y).any() else None,
                         "mean20": float(ev.loc[ev["year"] == y, "r20"].mean()) if (ev["year"] == y).any() else None,
                         "excess20": float(excess20[ev["year"] == y].mean()) if (ev["year"] == y).any() else None}
                for y in sorted(ev["year"].unique())
            },
        }
        passed = (n_ev >= PASS_MIN_EVENTS and stat["win20"] >= PASS_MIN_WINRATE_20D
                  and stat["excess20_vs_matched"] >= PASS_MIN_EXCESS_20D
                  and (stat["median20"] > 0 if PASS_MEDIAN_20D_GT0 else True))
        stat["verdict"] = "PASS_CANDIDATE" if passed else "FALSIFIED"
        result["variants"][v] = stat

    result["baseline_overall"] = {
        "n_stockdays": int(base_g["n"].sum()),
        "mean20": float(base["r20"].mean()),
        "win20": float((base["r20"] > 0).mean()),
        "mean5": float(base["r5"].mean()),
        "mean10": float(base["r10"].mean()),
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[4/4] 完成  ({time.time()-t0:.1f}s)  → {OUT_JSON}")
    for v in ("V1", "V2"):
        s = result["variants"][v]
        print(f"\n===== {v} ({'严格:零轴上多头' if v == 'V1' else '宽松:任意金叉'}) =====")
        print(f"事件数(冷却/原始): {s['events']} / {s['raw_events_no_cooldown']}")
        print(f"胜率 5/10/20日: {s['win5']:.3%} / {s['win10']:.3%} / {s['win20']:.3%}")
        print(f"均值 5/10/20日: {s['mean5']:+.3%} / {s['mean10']:+.3%} / {s['mean20']:+.3%}")
        print(f"20日中位数: {s['median20']:+.3%}  匹配基准20日均值: {s['baseline_mean20_matched']:+.3%}"
              f"  超额: {s['excess20_vs_matched']:+.3%}")
        print(f"持有期回撤 dd20 中位/p95: {s['dd20_p50']:.2%} / {s['dd20_p95']:.2%}")
        print("逐年:", {y: (d['events'], f"{d['win20']:.1%}" if d['win20'] is not None else None,
                          f"{d['excess20']:+.2%}" if d['excess20'] is not None else None)
                       for y, d in s["by_year"].items()})
        print(f"判定: {s['verdict']}  (阈值: 事件>=2000, 20日胜率>=52%, 超额>=+0.5pp, 中位数>0)")
    b = result["baseline_overall"]
    print(f"\n无条件基准 全样本股日={b['n_stockdays']}  胜率20日={b['win20']:.3%}  均值20日={b['mean20']:+.3%}")


def in_universe_batch(codes: pd.Series) -> np.ndarray:
    return codes.map(in_universe).to_numpy(dtype=bool)


if __name__ == "__main__":
    sys.exit(main())
