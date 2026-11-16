# -*- coding: utf-8 -*-
"""选股程序走前锋回测(20261025) — "选的对不对"的程序级实证。

程序定义(冻结): 每10个交易日一个调仓日T:
  候选池 = T日处于A格状态(close≤30, 非ST)的全部股票;
  每股滚动条件胜率 = 该股截至T已完成(dpos+20≤dpos_T)的A格进入事件t20e胜率, 样本≥5才可排序;
  选top5(胜率降序,n为次序) 与 bottom5; 次日开盘买入、T+20收盘卖出(停牌缺行则顺延剔除);
  基线 = 同日全部候选股同等持有均值。
判读: top5超额>0 且 top5>bottom5 → 排名有前瞻内容; 否则程序判死。
全程无前视: 排序只用T日前完结事件。只读, 零交易权。
输出: analysis/top5_walkforward_20261025_result.json
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
OUT = r"D:\zcodesj\analysis\top5_walkforward_20261025_result.json"

LOAD_START = "20190101"
EVAL_START = 20220101
STEP = 10
COOLDOWN = 5
HOLD = 20
STOP = 0.92
MIN_TRAIL = 5
TOPN = 5


def t20e_outcome(close: np.ndarray, ev_idx: np.ndarray):
    """事件t20e: (win bool, 事件日dpos)。要求完整前向。"""
    n = len(close)
    ev = ev_idx[(ev_idx + HOLD < n) & (ev_idx >= 0)]
    if len(ev) == 0:
        return np.array([], dtype=bool)
    win20 = np.lib.stride_tricks.sliding_window_view(close, HOLD)[ev + 1]
    stop = close[ev, None] * STOP
    broke = win20 <= stop
    has_break = broke.any(axis=1)
    first = np.argmax(broke, axis=1)
    exit_px = np.where(has_break, win20[np.arange(len(ev)), first], close[ev + HOLD])
    return exit_px > close[ev]


def cooldown_filter(idxs: np.ndarray, cd: int):
    keep, last = [], -10**9
    for i in idxs:
        if i - last > cd:
            keep.append(i)
            last = i
    return np.array(keep, dtype=int)


def main():
    t0 = time.time()
    st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
    st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
    st_con.close()
    st_by_code = {}
    for c, d in zip(st_rows["code"].str[-6:], st_rows["date"].astype("int64")):
        st_by_code.setdefault(c, set()).add(int(d))

    db = sqlite3.connect(QFQ, uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,open,close,volume FROM stock_daily_meta_qfq WHERE date>='{LOAD_START}' "
        "ORDER BY code,date", db)
    db.close()
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    df["d"] = df["date"].astype("int64")
    g = df.groupby("code")["close"]
    df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
    mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
    mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
    df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)
    print(f"[1/4] 状态矩阵 行={len(df)}  ({time.time()-t0:.1f}s)")

    d_g = df["d"].to_numpy(dtype="int64")
    o_g = df["open"].to_numpy(dtype=float)
    c_g = df["close"].to_numpy(dtype=float)
    code_g = df["code"].to_numpy()
    is_A_g = df["is_A"].fillna(False).to_numpy()
    prevA_g = df.groupby("code")["is_A"].shift(1).fillna(False).to_numpy()
    st_ok_g = np.array([(d in st_by_code.get(c, ())) is False for c, d in zip(code_g, d_g)])
    entry_mask = is_A_g & ~prevA_g & st_ok_g & (c_g <= 30.0)

    uniq_d = np.unique(d_g)
    dpos_map = {int(d): i for i, d in enumerate(uniq_d)}
    dpos_g = np.array([dpos_map[int(d)] for d in d_g], dtype="int64")

    # 每股历史事件(完结即有效): dpos事件序 + win
    ev_by_code = {}
    order = np.argsort(np.nonzero(entry_mask)[0], kind="stable")
    ep = np.nonzero(entry_mask)[0][order]
    lc = code_g[ep]
    change = np.nonzero(lc[1:] != lc[:-1])[0] + 1
    s_b = np.concatenate([[0], change])
    e_b = np.concatenate([change, [len(ep)]])
    for sb, eb in zip(s_b, e_b):
        block = ep[sb:eb]
        code = code_g[block[0]]
        g0, g1 = int(block[0]), int(block[-1])
        block = block[block + HOLD <= g1]
        if len(block) == 0:
            continue
        wins = t20e_outcome(c_g[g0:g1 + 1], block - g0)
        ev_by_code[code] = (dpos_g[block], wins)
    # 每日候选集合: is_A状态日
    cand_by_date = {}
    m = np.nonzero(is_A_g & st_ok_g & (c_g <= 30.0))[0]
    for i in m:
        cand_by_date.setdefault(dpos_g[i], set()).add(code_g[i])
    print(f"[2/4] 事件库与候选集完成 事件股={len(ev_by_code)}  ({time.time()-t0:.1f}s)")

    # 股票块索引(供前向行查找)
    code_starts = np.append([0], np.nonzero(code_g[1:] != code_g[:-1])[0] + 1)
    code_ends = np.append(code_starts[1:], len(code_g))
    block_of = {}
    for bi in range(len(code_starts)):
        block_of[code_g[code_starts[bi]]] = bi

    def fwd_ret(code, j):
        """T=j: 次日开盘买入, j+20收盘卖出; 缺行返回None。"""
        bi = block_of.get(code)
        if bi is None:
            return None
        s, e = int(code_starts[bi]), int(code_ends[bi])
        dd = d_g[s:e]
        i1 = np.searchsorted(dd, uniq_d[j + 1])
        i20 = np.searchsorted(dd, uniq_d[j + HOLD])
        if i1 >= len(dd) or dd[i1] != uniq_d[j + 1]:
            return None
        if i20 >= len(dd) or dd[i20] != uniq_d[j + HOLD]:
            return None
        op, cl = o_g[s + i1], c_g[s + i20]
        if not (op > 0) or not (cl > 0):
            return None
        return cl / op - 1.0

    def trailing(code, j):
        evd, wins = ev_by_code.get(code, (np.array([], dtype=int), np.array([], dtype=bool)))
        k = np.searchsorted(evd, j - HOLD, side="right")
        if k < MIN_TRAIL:
            return None
        return float(wins[:k].mean())

    nd = len(uniq_d)
    picks_rec, base_rec, bot_rec = [], [], []
    j = np.searchsorted(uniq_d, EVAL_START)
    n_rebal = 0
    while j + HOLD < nd:
        T_date = int(uniq_d[j])
        cand = cand_by_date.get(j, set())
        scored = []
        for code in cand:
            tw = trailing(code, j)
            if tw is not None:
                scored.append((tw, len(ev_by_code[code][1]), code))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        # 前向有效过滤
        def valid(code):
            return fwd_ret(code, j) is not None
        top5 = [c for _, _, c in scored if valid(c)][:TOPN]
        bot5 = [c for _, _, c in reversed(scored) if valid(c)][:TOPN]
        if len(top5) < TOPN:
            j += STEP
            continue
        rets_top = [fwd_ret(c, j) for c in top5]
        rets_bot = [fwd_ret(c, j) for c in bot5]
        rets_all = [fwd_ret(c, j) for c in cand]
        rets_all = [r for r in rets_all if r is not None]
        picks_rec.extend(rets_top)
        bot_rec.extend(rets_bot)
        base_rec.extend(rets_all)
        n_rebal += 1
        j += STEP
    print(f"[3/4] 走前锋完成 调仓次数={n_rebal} 选样={len(picks_rec)} 基线样本={len(base_rec)}  ({time.time()-t0:.1f}s)")

    pr = np.array(picks_rec); br = np.array(base_rec); bo = np.array(bot_rec)
    by_year = {}
    results = {
        "rebalances": n_rebal, "picks": int(len(pr)),
        "top5_win": round(float((pr > 0).mean()) * 100, 2),
        "top5_mean": round(float(pr.mean()) * 100, 2),
        "baseline_win": round(float((br > 0).mean()) * 100, 2),
        "baseline_mean": round(float(br.mean()) * 100, 2),
        "top5_excess_pp": round((float(pr.mean()) - float(br.mean())) * 100, 2),
        "bottom5_win": round(float((bo > 0).mean()) * 100, 2),
        "bottom5_mean": round(float(bo.mean()) * 100, 2),
        "top_minus_bottom_pp": round((float(pr.mean()) - float(bo.mean())) * 100, 2),
        "by_year": by_year,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[4/4] 完成 ({time.time()-t0:.1f}s) → {OUT}")
    print(f"\n== 选股程序走前锋(每10日调仓, top5按滚动胜率) ==")
    print(f"top5:  胜率={results['top5_win']}%  均值={results['top5_mean']:+.2f}%")
    print(f"基线:  胜率={results['baseline_win']}%  均值={results['baseline_mean']:+.2f}%")
    print(f"top5超额: {results['top5_excess_pp']:+.2f}pp   bottom5均值: {results['bottom5_mean']:+.2f}%   top−bottom: {results['top_minus_bottom_pp']:+.2f}pp")
    verdict = "有前瞻内容" if (results["top5_excess_pp"] > 0 and results["top_minus_bottom_pp"] > 0) else "程序判死(无前瞻内容)"
    print(f"判定: {verdict}")


if __name__ == "__main__":
    sys.exit(main())
