# -*- coding: utf-8 -*-
"""突破主线实验·资格赛一: 信号复现检验(prereg PREREG_BREAKOUT_MAINLINE_20261026, 主人裁判资格框架 20261027) — 零交易权。
双实现 60 交易日重放对照:
  Impl A = scanner-equivalent: 逐日复算 breakout_mainline_scanner.py 的实现口径
  Impl B = rule-text-direct:   直接从 prereg 冻结规则文本独立向量化实现
通过判据(主人框架): 逐日候选集重合率>=95%; 同数据源下价格偏差0 by construction,
实盘成交价偏差由 westock 成交回报对账另行承担(资格赛二观察面)。
通过判据不通过 → 数据层/工程层问题, 模拟盘收益数字无效, 规律尚未被检验。
用法: python analysis/breakout_qualification.py [--days 60]
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
IMAP = "data/industry_group_map_snapshot.db"


def norm6(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


def load_universe(days_needed):
    con = sqlite3.connect(QFQ, uri=True)
    latest = int(con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
    win_start = (datetime.strptime(str(latest), "%Y%m%d") - timedelta(days=days_needed)).strftime("%Y%m%d")
    df = pd.read_sql_query(
        f"SELECT code,date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq "
        f"WHERE date>='{win_start}' ORDER BY code,date", con)
    con.close()
    df["code6"] = df["code"].map(norm6)
    df["d"] = df["date"].astype("int64")
    df = df[(df["close"] > 0) & (df["volume"] > 0)]
    icon = sqlite3.connect(IMAP)
    imap = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", icon)
    icon.close()
    imap["code6"] = imap["code"].astype(str).str[-6:]
    l1_map = dict(zip(imap["code6"], imap["l1"]))
    scon = sqlite3.connect("file:data/st_history.db?mode=ro", uri=True)
    st_latest = scon.execute(
        "SELECT code,is_st FROM st_daily s1 WHERE date=(SELECT MAX(date) FROM st_daily s2 WHERE s2.code=s1.code)").fetchall()
    scon.close()
    st_codes = {norm6(c) for c, s in st_latest if s == 1}
    return latest, df, l1_map, st_codes


def impl_a_scanner_equiv(df, l1_map, st_codes, trade_days, asof, cutoff60):
    """Impl A: 与 breakout_mainline_scanner.py 逐条同口径的逐日复算(预分组实现, 避免逐票全表扫描)。"""
    out = {}
    sub = df[df["code6"].str.match(r"^[603]") & ~df["code6"].str.startswith("688")].copy()
    sub = sub[~sub["code6"].isin(st_codes)]
    sub["l1"] = sub["code6"].map(l1_map)
    ind_c = sub.groupby(["l1", "d"])["close"].mean().reset_index()
    piv = ind_c.pivot(index="d", columns="l1", values="close").sort_index()
    mkt_all = sub.groupby("d")["close"].mean().sort_index()
    groups = {c: g.sort_values("d") for c, g in sub.groupby("code6", sort=False)}
    for d in trade_days:
        if d not in mkt_all.index:
            continue
        mi = mkt_all.index.get_loc(d)
        if mi == 0:
            continue
        prev_d = mkt_all.index[mi - 1]
        mkt_ret = float(mkt_all.loc[d] / mkt_all.loc[prev_d] - 1)
        if d in piv.index and prev_d in piv.index:
            ind_ret = (piv.loc[d] / piv.loc[prev_d] - 1).to_dict()
        else:
            ind_ret = {}
        day_codes = sub.loc[sub["d"] == d, "code6"]
        cands = []
        for code6 in day_codes:
            hist = groups.get(code6)
            if hist is None:
                continue
            arr_d = hist["d"].to_numpy("int64")
            lo = int(np.searchsorted(arr_d, d, side="right"))  # 截断到<=d(重放语义: 历史日只能看当时可见的K线)
            if lo < 25 or arr_d[lo - 1] != d:
                continue
            if arr_d[0] > cutoff60:
                continue
            c = hist["close"].to_numpy(float)[:lo]
            v = hist["volume"].to_numpy(float)[:lo]
            if c[-1] > 30 or c[-1] < 2:
                continue
            vol5 = np.mean(v[-6:-1])
            if c[-1] >= max(c[-21:-1]) and vol5 > 0 and v[-1] >= 1.5 * vol5 and c[-1] > c[-2]:
                l1 = l1_map.get(code6, "")
                ir = ind_ret.get(l1, -99)
                if ir > mkt_ret:
                    cands.append(code6)
        out[d] = set(cands)
    return out


def impl_b_rule_direct(df, l1_map, st_codes, trade_days, asof, cutoff60):
    """Impl B: 直接从 prereg 冻结规则文本的独立向量化实现(不同代码路径)。"""
    out = {}
    uni = df[df["code6"].str.match(r"^[603]")].copy()
    uni = uni[~uni["code6"].str.startswith("688")]
    uni = uni[~uni["code6"].isin(st_codes)]
    uni["l1"] = uni["code6"].map(l1_map)
    uni = uni[uni["l1"].notna() & (uni["l1"] != "")]
    wc = uni.pivot_table(index="d", columns="code6", values="close").sort_index()
    wv = uni.pivot_table(index="d", columns="code6", values="volume").sort_index()
    ind_mean = uni.groupby(["l1", "d"])["close"].mean().reset_index()
    ind_piv = ind_mean.pivot(index="d", columns="l1", values="close").sort_index()
    mkt_ser = uni.groupby("d")["close"].mean().sort_index()
    td_idx = list(trade_days)
    for d in td_idx:
        if d not in wc.index:
            continue
        j = wc.index.get_loc(d)  # 用矩阵自身行位(修复: replay窗pos与全史矩阵iloc错位的bug)
        if j < 21:
            continue
        prev_d = wc.index[j - 1]
        win20 = wc.iloc[j - 20:j]
        c_today = wc.loc[d]
        c_prev1 = wc.iloc[j - 1]
        v_today = wv.loc[d]
        vol5 = wv.iloc[j - 5:j].mean()
        hi20 = win20.max()
        is_break = c_today >= hi20
        is_vol = v_today >= 1.5 * vol5
        is_up = c_today > c_prev1
        last_price_ok = (c_today <= 30) & (c_today >= 2)
        cond = is_break & is_vol & is_up & last_price_ok
        codes = set(cond.index[cond.fillna(False)])
        codes = {c for c in codes if c not in st_codes}
        first_seen = {c: wc[c].first_valid_index() for c in codes}
        codes = {c for c in codes if first_seen.get(c) is not None and first_seen[c] <= cutoff60}
        mkt_ret = float(mkt_ser.loc[d] / mkt_ser.loc[prev_d] - 1)
        if d in ind_piv.index and prev_d in ind_piv.index:
            ind_ret = (ind_piv.loc[d] / ind_piv.loc[prev_d] - 1).to_dict()
        else:
            ind_ret = {}
        cands = set()
        l1_of = uni[uni["d"] == d].set_index("code6")["l1"].to_dict()
        for c in codes:
            l1 = l1_of.get(c, "")
            ir = ind_ret.get(l1, -99)
            if ir > mkt_ret:
                cands.add(c)
        out[d] = cands
    return out


def main():
    days = 60
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    latest, df, l1_map, st_codes = load_universe(days_needed=days * 2 + 130)
    td = sorted(df["d"].unique().tolist())
    replay_days = [d for d in td if d <= latest][-days:]
    cutoff60 = int((datetime.strptime(str(latest), "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d"))
    print(f"资格赛一 | 基准={latest} | 重放{len(replay_days)}个交易日({replay_days[0]}~{replay_days[-1]})")
    A = impl_a_scanner_equiv(df, l1_map, st_codes, replay_days, latest, cutoff60)
    B = impl_b_rule_direct(df, l1_map, st_codes, replay_days, latest, cutoff60)
    rows, worst = [], 1.0
    for d in replay_days:
        a, b = A.get(d, set()), B.get(d, set())
        union = a | b
        j = (len(a & b) / len(union)) if union else 1.0
        rows.append((d, len(a), len(b), len(a & b), round(j * 100, 1)))
        if union:
            worst = min(worst, j)
    df_out = pd.DataFrame(rows, columns=["date", "implA", "implB", "both", "jaccard_pct"])
    bad = df_out[df_out["jaccard_pct"] < 95]
    print(df_out.to_string(index=False))
    total_union = len(set().union(*A.values())) if A else 0
    print(f"\n逐日Jaccard最低={min(r[4] for r in rows):.1f}% | 全窗口并集={total_union}只 | 不达标日数={len(bad)}")
    verdict = "PASS" if len(bad) == 0 else "FAIL"
    print(f"资格赛一判定: {verdict} (判据=逐日候选集重合率>=95%)")
    result = {"qual": "资格赛一_信号复现", "asof": latest, "days": len(replay_days),
              "verdict": verdict, "worst_jaccard_pct": round(min(r[4] for r in rows), 1),
              "bad_days": bad["date"].tolist() if len(bad) else [],
              "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(result, open("data/breakout_qualification_latest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
