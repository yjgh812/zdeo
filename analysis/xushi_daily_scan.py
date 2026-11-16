# -*- coding: utf-8 -*-
"""xushi_daily_scan.py — 蓄势满弦/蓄势双优 每日信号扫描器(2026-11-03 立项)

方法权威 = analysis/xushi_backtest_20261029.py(逐字复现其 twelve_dims/quality_score/选票规则)。
断根合同: 产出计划禁止携带主人批准键; 下单授权 = 暂行方法准入闸门 + CLI显式旗标/manual_confirmed。
fidelity 锚: as-of 20261028 满弦TOP5=振华重工/有友食品/广州发展/上海石化/中储股份;
  as-of 20261029 双优TOP5=长沙银行/浙能电力/东航物流/杭州银行/江苏银行 (tests/test_xushi_daily_scan_fidelity.py)
"""
import argparse
import json
import os
import sys

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name  # noqa: E402

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
FORBIDDEN_PLAN_KEYS = ("owner_approve_ddgate", "owner_approve_p2",
                       "owner_approve_r31", "owner_override_session")


def roll_mean(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w - 1:] = sliding_window_view(a, w).mean(axis=1)
    return out


def roll_max(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w - 1:] = sliding_window_view(a, w).max(axis=1)
    return out


def roll_min(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w - 1:] = sliding_window_view(a, w).min(axis=1)
    return out


def roll_std(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w - 1:] = sliding_window_view(a, w).std(axis=1)
    return out


def ema_arr(a, n):
    k = 2.0 / (n + 1)
    e = a[0]
    out = np.empty(len(a))
    out[0] = e
    for i in range(1, len(a)):
        e = k * a[i] + (1 - k) * e
        out[i] = e
    return out


def twelve_dims(cl, vol):
    """(12维布尔矩阵[n,12], res_cnt[n], pos250[n]) — 口径与 fulltest/backtest 一致(P3=expanding min)"""
    n = len(cl)
    ma5, ma10, ma20, ma60 = roll_mean(cl, 5), roll_mean(cl, 10), roll_mean(cl, 20), roll_mean(cl, 60)
    v5, v60 = roll_mean(vol, 5), roll_mean(vol, 60)
    v3a = roll_mean(vol, 3)
    v3b = np.full(n, np.nan)
    v3b[3:] = v3a[:-3]
    hi60, lo60 = roll_max(cl, 60), roll_min(cl, 60)
    hi10, lo10 = roll_max(cl, 10), roll_min(cl, 10)
    lo20 = roll_min(cl, 20)
    lo250, hi250 = roll_min(cl, 250), roll_max(cl, 250)
    expmin = np.minimum.accumulate(cl)
    std20 = roll_std(cl, 20)
    std100 = roll_std(cl, 100)
    std_ref = np.full(n, np.nan)
    std_ref[20:] = std100[:-20]
    d1 = np.diff(cl, prepend=cl[0])
    su, sd = roll_mean(np.where(d1 > 0, d1, 0.0), 60), roll_mean(np.where(d1 < 0, -d1, 0.0), 60)
    rsi = 100 * su / np.maximum(su + sd, 1e-12)
    dif = ema_arr(cl, 12) - ema_arr(cl, 26)
    a9 = 0.2
    dea = np.empty(n)
    dea[0] = dif[0]
    for i in range(1, n):
        dea[i] = 0.2 * dif[i] + 0.8 * dea[i]
    dif_n = dif / np.maximum(cl, 1e-9)
    r1d = cl / np.concatenate([[cl[0]], cl[:-1]]) - 1
    with np.errstate(invalid="ignore"):
        pos250 = (cl - lo250) / np.maximum(hi250 - lo250, 1e-9) * 100
        g4 = (pos250 >= 10) & (pos250 <= 50)
        mas_min = np.nanmin(np.stack([ma5, ma10, ma20, ma60]), axis=0)
        mas_max = np.nanmax(np.stack([ma5, ma10, ma20, ma60]), axis=0)
        g6 = (~np.isnan(mas_min)) & ((mas_max - mas_min) / np.maximum(mas_min, 1e-9) <= 0.03)
        ma20_5a = np.full(n, np.nan)
        ma20_5a[5:] = ma20[:-5]
        g7 = (~np.isnan(ma20_5a)) & (ma20 >= ma20_5a * 0.998)
    m20_ = roll_mean(cl, 20)
    mref_ = roll_mean(cl, 100)
    g10 = (~np.isnan(std_ref)) & (std20 / np.maximum(m20_, 1e-9) <= (std_ref / np.maximum(mref_, 1e-9)) * 0.7)
    G = np.stack([
        (v5 / np.maximum(v60, 1e-9)) <= 0.5,
        v3a > v3b,
        (~np.isnan(lo60)) & (lo60 > 0) & ((hi60 - lo60) / np.maximum(lo60, 1e-9) <= 0.25),
        (~np.isnan(lo10)) & (lo10 > 0) & ((hi10 - lo10) / np.maximum(lo10, 1e-9) <= 0.08),
        (~np.isnan(lo250)) & (lo20 > expmin * 1.005),
        g4,
        g6,
        g7,
        np.abs(dif_n) <= 0.005,
        (rsi >= 45) & (rsi <= 55),
        g10,
        r1d > -0.07,
    ], axis=1)
    res_cnt = np.nansum(G, axis=1)
    return G, res_cnt, pos250


def quality_score(cl, vol):
    """六项质量数值(压缩60/窄幅10/粘合/pos250/量比/收口) — 池内排名用, 与backtest逐字一致"""
    amp60 = (cl[-60:].max() - cl[-60:].min()) / max(cl[-60:].min(), 1e-9) * 100
    amp10 = (cl[-10:].max() - cl[-10:].min()) / max(cl[-10:].min(), 1e-9) * 100
    mas = [cl[-w:].mean() for w in (5, 10, 20, 60)]
    glue = (max(mas) - min(mas)) / max(min(mas), 1e-9) * 100
    mn, mx = cl[-250:].min(), cl[-250:].max()
    pos = (cl[-1] - mn) / max(mx - mn, 1e-9) * 100
    vr = vol[-5:].mean() / max(vol[-60:].mean(), 1e-9)
    sq = (cl[-20:].std() / max(cl[-20:].mean(), 1e-9)) / max(cl[-120:-20].std() / max(cl[-120:-20].mean(), 1e-9), 1e-9)
    return np.array([amp60, amp10, glue, pos, vr, sq])


def load_universe(asof: str):
    """加载 qfq 数据(date<=asof), 返回 (dates_all, series)。PIT: 严格只看 asof 及之前。
    窗口=1000交易日(与原版 fulltest 一致——expanding-min 锚点依赖窗口长度)。"""
    import sqlite3
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    all_dates = sorted({int(r[0]) for r in con.execute(
        "select distinct date from stock_daily_meta_qfq where date<=? order by date desc limit 1000", (asof,))})
    dmin = str(all_dates[0])
    rows = con.execute(
        "select code,date,close,volume from stock_daily_meta_qfq "
        "where date>=? and date<=? order by code,date", (dmin, asof)).fetchall()
    con.close()
    series = {}
    for code, d, c, v in rows:
        s = series.setdefault(code, {"date": [], "close": [], "vol": []})
        s["date"].append(int(d))
        s["close"].append(float(c))
        s["vol"].append(float(v or 0))
    date_idx = {d: i for i, d in enumerate(all_dates)}
    for code, s in series.items():
        s["gidx"] = [date_idx[d] for d in s["date"]]
    return all_dates, date_idx, series


def held_codes() -> set:
    with open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8") as f:
        doc = json.load(f)
    held = set()
    for p in doc.get("positions", []):
        cc = str(p.get("code", "")).lower()
        for pref in ("sh", "sz", "bj"):
            if cc.startswith(pref):
                cc = cc[len(pref):]
        if cc:
            held.add(cc.zfill(6))
    return held


def scan(asof: str, ignore_held: bool = False, winrate_overlap: bool = False) -> dict:
    """对 as-of 日截面跑 满弦/双优 双路线, 返回 {"date", picks, pools, prices}。
    winrate_overlap: 双优胜率样本语义(True=逐bar重叠=原10/29批口径; 缺省False=非重叠=backtest PIT权威口径)。"""
    all_dates, date_idx, series = load_universe(asof)
    if all_dates and all_dates[-1] != int(asof):
        raise SystemExit(f"[xushi-daily] asof={asof} 非qfq库最新交易日(最新={all_dates[-1] if all_dates else '空'})——拒绝在陈旧数据上出信号")
    t0 = date_idx[int(asof)]
    held = set() if ignore_held else held_codes()
    manxian_pool = []
    shuangyou_pool = []
    for code, s in series.items():
        loc = s["gidx"].index(t0) if t0 in s["gidx"] else -1
        if loc < 260:
            continue
        cl = np.array(s["close"][:loc + 1])
        vol = np.array(s["vol"][:loc + 1])
        if len(cl) < 300:
            continue
        Tc = cl[-1]
        if not (0.5 < Tc <= 30):
            continue
        if code in held:
            continue
        nm = get_name(code) or ""
        if "ST" in nm.upper() or "退" in nm:
            continue
        if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
            continue
        _, res_cnt, _ = twelve_dims(cl, vol)
        res = int(res_cnt[-1])
        if res >= 6:
            manxian_pool.append((code, res, loc))
        if res >= 9:
            # PIT胜率样本: overlap=False=非重叠(backtest权威口径) / True=逐bar重叠(原10/29批口径)
            hits = []
            last = -999
            for t in np.where((np.arange(len(cl)) >= 259) & (res_cnt >= 6))[0]:
                if not winrate_overlap and t < last + 20:
                    continue
                if t + 20 <= len(cl) - 1:
                    hits.append((int(t), cl[t + 20] / cl[t] - 1))
                    last = int(t)
            done = [f for (t_, f) in hits if t_ + 21 < loc]
            if len(done) >= 3:
                fa = np.array(done)
                shuangyou_pool.append({"code": code, "win": float((fa > 0).mean()) * 100,
                                       "n": len(done), "res": res})
    picks = {"manxian": [], "shuangyou": []}
    if manxian_pool:
        maxres = max(r for _, r, _ in manxian_pool)
        top_pool = [(c, l) for c, r, l in manxian_pool if r >= max(maxres, 11)] or \
                   [(c, l) for c, r, l in manxian_pool if r == maxres]
        qs = np.array([quality_score(np.array(series[c]["close"][:l + 1]),
                                     np.array(series[c]["vol"][:l + 1])) for c, l in top_pool])
        rk = np.zeros(len(top_pool))
        for j in range(6):
            order = np.argsort(qs[:, j])
            tmp = np.empty(len(top_pool))
            tmp[order] = np.arange(len(top_pool)) / max(len(top_pool) - 1, 1)
            rk += 1 - tmp
        picks["manxian"] = [top_pool[i][0] for i in np.argsort(-rk)[:5]]
    shuangyou_pool.sort(key=lambda x: (-x["win"], -x["n"]))
    picks["shuangyou"] = [x["code"] for x in shuangyou_pool[:5]]
    prices = {}
    for code in picks["manxian"] + picks["shuangyou"]:
        prices[code] = round(float(series[code]["close"][-1]), 2)
    return {"date": asof, "manxian": picks["manxian"], "shuangyou": picks["shuangyou"],
            "prices": prices,
            "pools": {"manxian_pool": len(manxian_pool), "shuangyou_pool": len(shuangyou_pool)}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="信号日YYYYMMDD, 缺省=qfq最新交易日")
    ap.add_argument("--out", default="", help="计划输出路径")
    ap.add_argument("--ignore-held", action="store_true", help="不剔持仓(fidelity校验用)")
    ap.add_argument("--winrate-overlap", action="store_true", help="双优胜率样本逐bar重叠(原10/29批口径)")
    args = ap.parse_args()
    date = args.date or ""
    if not date:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
        row = con.execute("select max(date) from stock_daily_meta_qfq").fetchone()
        con.close()
        date = str(row[0])
    result = scan(date, ignore_held=args.ignore_held, winrate_overlap=args.winrate_overlap)
    out_path = args.out or os.path.join(ROOT, "data", f"xushi_scan_{date}_plan.json")
    entries = []
    for src_key, src_tag in (("manxian", "xushi_manxian"), ("shuangyou", "xushi_shuangyou")):
        for code in result[src_key]:
            nm = get_name(code) or code
            entries.append({
                "code": code, "name": nm, "source": src_tag,
                "price": result["prices"].get(code), "volume": 100, "sector": "",
                "note": f"蓄势每日扫描{src_key}路线 信号日{date} (暂行方法准入七组之一)",
                "execute": True,
            })
    for e in entries:
        bad = [k for k in FORBIDDEN_PLAN_KEYS if k in e]
        if bad:
            raise SystemExit(f"断根检查失败: 计划条目含禁止键 {bad}")
    # 执行器合同: 顶层必须为 JSON 数组(与 manxian5_plan 原批同构)
    json.dump(entries, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[xushi-daily] 信号日{date} | 满弦池{result['pools']['manxian_pool']} "
          f"选{len(result['manxian'])}只={result['manxian']} | "
          f"双优池{result['pools']['shuangyou_pool']} 选{len(result['shuangyou'])}只={result['shuangyou']}")
    print(f"[xushi-daily] 计划落盘: {out_path} ({len(entries)}条)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
