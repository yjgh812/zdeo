# -*- coding: utf-8 -*-
"""pit_v2_rescan_compare_20261028.py — v2 量纲列谱系重扫对比器（ICIR含SUE / 118异象 升级版 vs 冻结版）

背景: v2 量纲族(SOP一百八十五⑪)落地后, 两个 reconstructed 方法的因子池可从「降级替代」
升级到「真量纲原谱系」。本脚本与冻结构建(analysis/revival_wave23_scanner_20261028.py)
同源同宇宙(T=20261027, 剔持仓∪Wave1), 隔离「因子池升级」单一变量; 今日冻结名单不动,
输出为对比证据 + 后续批次(20日滚动)的候选输入。

升级口径(还原升级 v3, 差异显式):
  icir_sue v3: 因子池 4→8 = [ep,bp,roe,sue] + [sp_v2,ato_v2,pm_v2,lev_v2](v2真量纲列);
               权重=带符号 rank|ICIR|(正向取rank、负向取1−rank), 替代旧 max(icir,0) 丢负向做法。
  anom118 v3: 七因子 = [id_vol(−),Amihud(+),ret20反转(−),ep(+),bp(+),roe(+),ato_v2(+)],
               仅把降级替代「20日均振幅」换回真资产周转 ato_v2, 其余六支不动。
对比方法: 冻结版候选(直接取自 data/revival_wave23_picks_20261028.json 方法 cands 无法回放,
故用冻结版同名函数在皮重算——scanner 模块函数复用, 同参数重放)。
"""
from __future__ import annotations
import json, sys, os, statistics, math
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analysis"))
import sqlite3
import numpy as np
import importlib.util

spec = importlib.util.spec_from_file_location(
    "w23", os.path.join(ROOT, "analysis", "revival_wave23_scanner_20261028.py"))
w23 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w23)

MAIN = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
PIT_DB = w23.PIT_DB
OUT = os.path.join(ROOT, "data", "pit_v2_rescan_compare_20261028.json")


def pit_latest_v2(T):
    """T日截面: ep/bp/roe + v2 四列(每票 pit_avail<=T 最新一期)"""
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    rows = con.execute(
        "select code,ep,bp,roe,sp_v2,lev_v2,ato_v2,pm_v2 from stock_factors_fundamental "
        "where pit_avail_date>=? and pit_avail_date<=? order by code,date",
        (int(T) - 800, int(T))).fetchall()
    con.close()
    latest = {}
    for code, ep, bp, roe, sp2, lv2, at2, pm2 in rows:
        latest[code] = (ep, bp, roe, sp2, lv2, at2, pm2)
    return latest


def pit_by_me_v2(dates):
    """24个月末快照(v2列) — 与 scanner.build_pit_by_me_fast 同构"""
    month_last = {}
    for d in dates:
        month_last[d // 100] = d
    targets = sorted(set(list(month_last.values())[-25:-1]))
    by_me = {me: {} for me in targets}
    con = sqlite3.connect(f"file:{MAIN}?mode=ro", uri=True)
    rows = con.execute(
        "select code,pit_avail_date,ep,bp,roe,sp_v2,lev_v2,ato_v2,pm_v2 "
        "from stock_factors_fundamental where pit_avail_date>=? order by pit_avail_date,date",
        (min(dates),)).fetchall()
    con.close()
    cur = {}
    ti = 0
    for code, avail, ep, bp, roe, sp2, lv2, at2, pm2 in rows:
        while ti < len(targets) and int(avail) > int(targets[ti]):
            by_me[targets[ti]] = dict(cur)
            ti += 1
        cur[code] = (ep, bp, roe, sp2, lv2, at2, pm2)
    while ti < len(targets):
        by_me[targets[ti]] = dict(cur)
        ti += 1
    return by_me


def icir_weights_v3(series, uni, pit_by_me, sue_by_me, dates):
    """带符号 rank-ICIR 权重(8因子池)"""
    fns = {
        "ep": lambda f, su: f[0], "bp": lambda f, su: f[1], "roe": lambda f, su: f[2],
        "sp_v2": lambda f, su: f[3], "lev_v2": lambda f, su: f[4],
        "ato_v2": lambda f, su: f[5], "pm_v2": lambda f, su: f[6],
        "sue": lambda f, su: su,
    }
    month_last = {}
    for d in dates:
        month_last[d // 100] = d
    mes = sorted(set(list(month_last.values())[-25:-1]))
    ics = defaultdict(list)
    for me in mes:
        fwd = {}
        for code in uni:
            s = series[code]
            i = w23.idx_of(s, me)
            if i is None:
                continue
            c = s["close"]
            if len(c) > i + 21 and c[i] and c[i] > 0:
                fwd[code] = c[i + 21] / c[i] - 1
        if len(fwd) < 500:
            continue
        fr = w23.pct_rank(fwd)
        pit_m = pit_by_me.get(me, {})
        sue_m = sue_by_me.get(me, {})
        for name, fn in fns.items():
            vals = {}
            for code in uni:
                f = pit_m.get(code)
                if f is None:
                    continue
                v = fn(f, sue_m.get(code))
                if v is not None and not (isinstance(v, float) and math.isnan(v)):
                    vals[code] = v
            if len(vals) < 300:
                continue
            rk = w23.pct_rank(vals)
            pairs = [(rk[k], fr[k]) for k in rk if k in fr]
            if len(pairs) < 300:
                continue
            x = np.array([p[0] for p in pairs]); y = np.array([p[1] for p in pairs])
            ic = float(np.corrcoef(x, y)[0, 1])
            if not math.isnan(ic):
                ics[name].append(ic)
    w = {}
    sign = {}
    for name, sic in ics.items():
        if len(sic) >= 12:
            icir = statistics.fmean(sic) / (statistics.pstdev(sic) + 1e-9)
            w[name] = abs(icir)
            sign[name] = 1 if icir > 0 else -1
    return w, sign, {k: len(v) for k, v in ics.items()}


def score_pool(series, uni, pit, sue_T, w, sign, fns):
    comps = {}
    for name, fn in fns.items():
        if w.get(name, 0) <= 0:
            continue
        vals = {}
        for c in uni:
            f = pit.get(c)
            if f is None:
                continue
            v = fn(f, sue_T.get(c))
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                vals[c] = v
        if len(vals) < 100:
            continue
        rk = w23.pct_rank(vals)
        s = sign.get(name, 1)
        for c, r in rk.items():
            comps[c] = comps.get(c, 0.0) + w[name] * (r if s > 0 else 1 - r)
    return sorted(comps.items(), key=lambda x: -x[1])


def main():
    series, dates = w23.load_qfq()
    T = dates[-1]
    wave1_codes = set()
    try:
        w1 = json.load(open(os.path.join(ROOT, "data", "revival_picks_20261028.json"), encoding="utf-8"))
        for m in w1["methods"].values():
            for p in m["picks"]:
                wave1_codes.add(p["code"])
    except Exception:
        pass
    uni, held = w23.base_universe(series, wave1_codes)
    print(f"[同源] T={T} 宇宙={len(uni)}")

    pit = pit_latest_v2(T)
    sue_T = w23.eps_sue(T)
    sue_me = w23.sue_by_me(dates)
    pit_me = pit_by_me_v2(dates)
    mkt = w23.mkt_ew_ret(series, list(uni.keys()))

    w, sign, ic_n = icir_weights_v3(series, uni, pit_me, sue_me, dates)
    print(f"[ICIR v3] 权重: { {k: (round(v,3), sign[k]) for k,v in w.items()} } 月数: {ic_n}")

    out = {"signal_date": T, "frozen_picks_md5": "4cc8093082e82e00f7cbe5345ca1bce1(不动)",
           "note": "对比证据+后续批次候选输入; 非执行名单(今日09:37计划已冻结)",
           "icir_sue_v3": {}, "anom118_v3": {}}

    # icir_sue v3
    fns = {"ep": lambda f, su: f[0], "bp": lambda f, su: f[1], "roe": lambda f, su: f[2],
           "sp_v2": lambda f, su: f[3], "lev_v2": lambda f, su: f[4],
           "ato_v2": lambda f, su: f[5], "pm_v2": lambda f, su: f[6], "sue": lambda f, su: su}
    ranked = score_pool(series, uni, pit, sue_T, w, sign, fns)
    picks = []
    for c, v in ranked[:12]:
        price = float(uni[c]["close"][-1])
        if price * 100 <= w23.BUDGET_CAP:
            picks.append({"code": c, "name": w23.get_name(c) or "?", "score": round(v, 4), "price": price})
    old = json.load(open(os.path.join(ROOT, "data", "revival_wave23_picks_20261028.json"), encoding="utf-8"))
    old5 = [p["code"] for p in old["methods"]["revival_icir_sue"]["picks"]]
    new5 = [p["code"] for p in picks[:5]]
    out["icir_sue_v3"] = {"weights": {k: round(v, 3) for k, v in w.items()},
                          "sign": sign, "picks": picks[:5],
                          "frozen5": old5, "overlap": len(set(old5) & set(new5))}
    print(f"[icir_sue v3] 新5只: {new5} | 与冻结版重合 {len(set(old5) & set(new5))}/5")

    # anom118 v3 (7因子: 仅替换降级替代支 20日均振幅→ato_v2)
    idv, amh = {}, {}
    for c in uni:
        v = w23.id_vol_120(series[c], mkt)
        if v is not None:
            idv[c] = v
        v2_ = w23.amihud_20(series[c])
        if v2_ is not None:
            amh[c] = v2_
    r20 = {c: w23.ret_n(series[c], 20) for c in uni}
    r20 = {c: v for c, v in r20.items() if v is not None}
    rk_idv = w23.pct_rank(idv); rk_amh = w23.pct_rank(amh); rk_r20 = w23.pct_rank(r20)
    ep = {c: pit[c][0] for c in uni if pit.get(c)}; bp = {c: pit[c][1] for c in uni if pit.get(c)}
    roe = {c: pit[c][2] for c in uni if pit.get(c)}; at2 = {c: pit[c][5] for c in uni if pit.get(c)}
    rk_ep = w23.pct_rank({c: v for c, v in ep.items() if v})
    rk_bp = w23.pct_rank({c: v for c, v in bp.items() if v})
    rk_roe = w23.pct_rank({c: v for c, v in roe.items() if v is not None})
    rk_at2 = w23.pct_rank({c: v for c, v in at2.items() if v})
    comps = defaultdict(float); ncov = defaultdict(int)
    for c in uni:
        tot = 0.0; n = 0
        if c in rk_idv: tot += (1 - rk_idv[c]); n += 1
        if c in rk_amh: tot += rk_amh[c]; n += 1
        if c in rk_r20: tot += (1 - rk_r20[c]); n += 1
        if c in rk_ep: tot += rk_ep[c]; n += 1
        if c in rk_bp: tot += rk_bp[c]; n += 1
        if c in rk_roe: tot += rk_roe[c]; n += 1
        if c in rk_at2: tot += rk_at2[c]; n += 1
        if n >= 6:
            comps[c] = tot / n; ncov[c] = n
    out_an = sorted(((v, c) for c, v in comps.items()), reverse=True)[:12]
    an_picks = [{"code": c, "name": w23.get_name(c) or "?", "score": round(v, 4),
                 "price": float(uni[c]["close"][-1])} for v, c in out_an]
    old5a = [p["code"] for p in old["methods"]["revival_anom118"]["picks"]]
    new5a = [p["code"] for p in an_picks[:5]]
    out["anom118_v3"] = {"picks": an_picks[:5], "frozen5": old5a,
                         "overlap": len(set(old5a) & set(new5a))}
    print(f"[anom118 v3] 新5只: {new5a} | 与冻结版重合 {len(set(old5a) & set(new5a))}/5")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"产物: {OUT}")


if __name__ == "__main__":
    main()
