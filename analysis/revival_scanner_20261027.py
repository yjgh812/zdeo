# -*- coding: utf-8 -*-
"""revival_scanner_20261027.py — 全员复活模拟盘验证 Wave1 选股扫描器（主人令 2026-10-27）

合同: analysis/revival_paper_test_PREREG_20261027.md
每方法 5 只; 宇宙=非ST/非科创北交/窗内>=60交易日/收盘<=30元/剔当前持仓/方法间去重;
单方法预算帽 7500 元(超帽在 top15 顺位内换 cheaper); 输出 data/revival_picks_20261028.json。
"""
from __future__ import annotations
import json, sys, os, hashlib
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
import sqlite3
import statistics

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
OUT = os.path.join(ROOT, "data", "revival_picks_20261028.json")
PREREG = os.path.join(ROOT, "analysis", "revival_paper_test_PREREG_20261027.md")
BUDGET_CAP = 7500.0
TOP_SUB = 15  # 预算帽换候选时的顺位深度
LOOKBACK_ROWS = 75  # 交易日窗

from stock_name_lookup import get_name  # 唯一名称接口


def load_kline():
    con = sqlite3.connect(QFQ)
    dates = [r[0] for r in con.execute(
        "select distinct date from stock_daily_meta_qfq order by date desc limit ?", (LOOKBACK_ROWS,))]
    dmin = dates[-1]
    rows = con.execute(
        "select code,date,open,high,low,close,volume,amount,change_pct from stock_daily_meta_qfq "
        "where date>=? order by code,date", (dmin,)).fetchall()
    con.close()
    series = defaultdict(list)  # code -> list[(date, o,h,l,c,vol,amt,pct)]
    for code, d, o, h, l, c, v, a, p in rows:
        series[code].append((d, o, h, l, c, v, a, p))
    for code in series:
        series[code].sort(key=lambda x: x[0])
    return series, dates


def base_universe(series):
    """(code, T行) 合法宇宙: >=60行/非科创北交/收盘<=30/非ST/非已持仓"""
    held = set()
    try:
        doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
        for p in doc["positions"]:
            c = str(p["code"]).lower()
            for pref in ("sh", "sz", "bj"):
                if c.startswith(pref):
                    c = c[len(pref):]
            held.add(c.zfill(6))
    except Exception:
        pass
    out = {}
    for code, rows in series.items():
        if len(rows) < 60 or code in held:
            continue
        if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
            continue
        t = rows[-1]
        if not t[4] or t[4] > 30 or t[4] <= 0.5:
            continue
        name = get_name(code) or ""
        if "ST" in name.upper() or "退" in name:
            continue
        out[code] = t
    return out, held


def limit_th(code):
    return 19.9 if code.startswith("30") else 9.9


def m_gapup(series, uni):
    cands = []
    for code, t in uni.items():
        prev = series[code][-2]
        gap = (t[1] / prev[4] - 1) * 100
        if gap >= 3.0:
            cands.append((gap, code, f"跳空{gap:.1f}%"))
    cands.sort(reverse=True)
    return [(c, r) for _, c, r in cands]


def m_dazhan(series, uni):
    cands = []
    for code in uni:
        rows = series[code]
        if len(rows) < 2:
            continue
        p1 = rows[-2]  # T-1
        if p1[7] is not None and p1[7] >= limit_th(code):
            amt = p1[6] or 0
            cands.append((amt, code, f"T-1涨停{p1[7]:.1f}%"))
    cands.sort(reverse=True)
    return [(c, r) for _, c, r in cands]


def m_t1seal(series, uni):
    cands = []
    for code in uni:
        rows = series[code]
        if len(rows) < 3:
            continue
        p1, p2 = rows[-2], rows[-3]
        if p1[7] is not None and p1[7] >= limit_th(code) and (p2[7] is None or p2[7] < limit_th(code)):
            cands.append((p1[6] or 0, code, "T-1首板封板"))
    cands.sort(reverse=True)
    return [(c, r) for _, c, r in cands]


def m_lianban(series, uni):
    cands = []
    for code in uni:
        rows = series[code]
        if len(rows) < 6:
            continue
        n = 0
        for r in reversed(rows[:-1]):
            if r[7] is not None and r[7] >= limit_th(code):
                n += 1
            else:
                break
        if n >= 2:
            cands.append((n, rows[-2][6] or 0, code, f"{n}连板"))
    cands.sort(key=lambda x: (-x[0], -x[1]))
    return [(c, r) for _, _, c, r in cands]


def m_dikai(series, uni):
    cands = []
    for code, t in uni.items():
        prev = series[code][-2]
        if t[1] <= prev[4] * 0.98 and t[4] > t[1] and t[4] >= prev[4]:
            cands.append((t[6] or 0, code, "低开承接收复"))
    cands.sort(reverse=True)
    return [(c, r) for _, c, r in cands]


def m_tandi(series, uni):
    cands = []
    for code in uni:
        rows = series[code]
        if len(rows) < 21:
            continue
        ret20 = (rows[-1][4] / rows[-21][4] - 1) * 100
        if ret20 > -15:
            continue
        t = rows[-1]
        if not (t[4] > t[1]):
            continue
        v20 = statistics.fmean([r[5] or 0 for r in rows[-20:]])
        if v20 > 0 and (t[5] or 0) < 0.9 * v20:
            cands.append((ret20, code, f"20日{ret20:.1f}%缩量企稳"))
    cands.sort()
    return [(c, r) for _, c, r in cands]


def m_dibo(series, uni):
    rows_by = {}
    for code in uni:
        rows = series[code]
        if len(rows) < 61:
            continue
        rets = []
        for i in range(len(rows) - 60, len(rows)):
            if i == 0 or not rows[i - 1][4]:
                break
            rets.append(rows[i][4] / rows[i - 1][4] - 1)
        if len(rets) < 55:
            continue
        vol60 = statistics.fmean(rets) and statistics.pstdev(rets) * (250 ** 0.5)
        rows_by[code] = (vol60, (rows[-1][4] / rows[-21][4] - 1) * 100)
    if not rows_by:
        return []
    vs = sorted(v[0] for v in rows_by.values())
    cut = vs[len(vs) // 3]
    cands = [(m20, code, f"低波{v60:.1f}动量{m20:.1f}%") for code, (v60, m20) in rows_by.items()
             if v60 <= cut]
    cands.sort(reverse=True)
    return [(c, r) for _, c, r in cands]


def m_hydl(series, uni):
    con = sqlite3.connect(os.path.join(ROOT, "data", "industry_group_map_snapshot.db"))
    ind = dict(con.execute("select code,l1 from l1_member"))
    con.close()
    ind_ret = defaultdict(list)
    for code in uni:
        rows = series[code]
        if len(rows) < 21 or code not in ind:
            continue
        ind_ret[ind[code]].append((rows[-1][4] / rows[-21][4] - 1) * 100)
    top3 = sorted(((statistics.fmean(v), k) for k, v in ind_ret.items()), reverse=True)[:3]
    names3 = {k for _, k in top3}
    per = defaultdict(list)
    for code in uni:
        if code not in ind or ind[code] not in names3:
            continue
        rows = series[code]
        per[ind[code]].append(((rows[-1][4] / rows[-21][4] - 1) * 100, code))
    picks, cap = [], defaultdict(int)
    for ind_name in sorted(names3, key=lambda k: -dict(top3)[k] if False else 0):
        pass
    flat = []
    for k in names3:
        for r20, code in sorted(per[k], reverse=True):
            flat.append((k, r20, code))
    for k, r20, code in sorted(flat, key=lambda x: -x[1]):
        if cap[k] >= 2 or len(picks) >= 5:
            continue
        cap[k] += 1
        picks.append((code, f"行业动量{k} r20={r20:.1f}%"))
    return picks


def m_master_hist(uni):
    path = os.path.join(ROOT, "reports", "daily_signals", "signal_20261027.csv")
    if not os.path.exists(path):
        return []
    picks = []
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        for line in f:
            d = dict(zip(header, line.strip().split(",")))
            code = d.get("code", "")[-6:].zfill(6)  # SH601988 -> 601988
            if code in uni:
                try:
                    ar = float(d.get("avg_rank", "99"))
                except ValueError:
                    continue
                picks.append((ar, code, f"MH avg_rank={ar}"))
    picks.sort()
    return [(c, r) for _, c, r in picks]


def m_shendu(series, uni):
    side = os.path.join(ROOT, "data", "revival_shendu_cands_20261027.json")
    doc = json.load(open(side, encoding="utf-8"))
    cands = []
    for code, pe, pb in doc["cands"]:
        if code in uni:
            score = 1.0 / pe + 1.0 / pb
            cands.append((score, code, f"E/P+1/PB={score:.3f}"))
    cands.sort(reverse=True)
    return [(c, r) for _, c, r in cands]


def main():
    series, dates = load_kline()
    t_date = dates[0]
    uni, held = base_universe(series)
    print(f"信号日={t_date} 宇宙={len(uni)} 已持仓剔除={len(held)}")
    methods = [
        ("revival_gapup", m_gapup), ("revival_dazhan", m_dazhan),
        ("revival_t1seal", m_t1seal), ("revival_lianban", m_lianban),
        ("revival_dikai", m_dikai), ("revival_tandi", m_tandi),
        ("revival_dibo", m_dibo), ("revival_shendu", m_shendu), ("revival_hydl", m_hydl),
        ("revival_master_hist", lambda s, u: m_master_hist(u)),
    ]
    used = set()
    out = {"signal_date": t_date, "generated": __import__("datetime").datetime.now().isoformat(),
           "prereg": PREREG, "methods": {}}
    for src, fn in methods:
        cands = fn(series, uni)
        picks, cost = [], 0.0
        deep = cands[: max(TOP_SUB, 5)]
        for code, reason in deep:
            if code in used or len(picks) >= 5:
                continue
            price = uni[code][4]
            if cost + price * 100 > BUDGET_CAP:
                continue
            picks.append({"code": code, "name": get_name(code) or "?", "price": round(price, 2),
                          "volume": 100, "source": src, "rule": reason})
            cost += price * 100
        for p in picks:
            used.add(p["code"])
        out["methods"][src] = {"picks": picks, "cost": round(cost, 2),
                               "cand_total": len(cands), "short": len(picks) < 5}
        print(f"{src:<22} 候选{len(cands):>5} 入选{len(picks)} 成本{cost:>8.0f} "
              + ("⚠️不足5只" if len(picks) < 5 else ""))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    md5 = hashlib.md5(open(OUT, "rb").read()).hexdigest()
    n_all = sum(len(m["picks"]) for m in out["methods"].values())
    cost_all = sum(m["cost"] for m in out["methods"].values())
    print(f"\n合计 {n_all} 只 / 预算 {cost_all:,.0f} 元 | picks md5={md5}")
    print(f"产物: {OUT}")


if __name__ == "__main__":
    main()
