# -*- coding: utf-8 -*-
"""证伪复核批次4 · 独立重实现三件

J) TEXTBOOK-BS-POINTS-20261003 三子断言(定义依原档 tmp/framework_crosscheck_tests.py):
   T-A 周线MACD金叉(DIF上穿DEA)+5周均线上穿10周, 信号=次周首交易日(PIT), 原档 n=19,171 win42.8% vs 基线49.0%
   T-B1 支撑位(|c/ma60-1|<=3%)缩量(v<ma5v)长下影(下影>=2倍实体且>=50%振幅): 原档 n=20,004 win47.2%
   T-B2 低位(250日close低位+35%以内)阳包阴: 原档 n=41,468 win50.3%=基线零alpha
   口径: 事件日次开入场, fwd20=信号日后第20根收盘/次开-1, 5日去重; 原档跑在已失考的本地副本上(谱系发现)

K) BREAKOUT-MIDPOS 三带突破全景(定义依原档 attribution/breakout_midpos_20260911.py):
   pos250=(c-min250)/(max250-min250); 突破=c>前60日max 且 vol>=1.2*vol_ma20(shift1);
   带=low<0.35/mid 0.35-0.70/high>0.70; 宇宙 c<=30, 次开入场(gap<=10自然日), 成本0.2%;
   原档: 三带事件日聚簇t=-5.85/-8.86/-6.16; mid win44.0%/mean+0.27%/同带增量-0.235pp

L) EMOTION-PHASE-V2-NEXTDAY (台账 data/emotion_phase_v2_ledger.jsonl 419日):
   相位(ebbing/healing)对次日上证方向 MW-U; 对次日炸板率(台账br列);
   原档: ebbing n=112 vs healing n=209, +0.01% vs +0.12%, p=0.91; 炸板率31.13% vs 29.63% p=0.056

数据: wb 单一真源只读 + zt/emotion 台账只读
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
PHASE = r"D:\zcodesj\data\emotion_phase_v2_ledger.jsonl"
PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")


def load_panel(start, cols):
    con = sqlite3.connect("file:%s?mode=ro" % QFQ, uri=True)
    ph = ",".join("?" * len(PREFIXES))
    df = pd.read_sql(
        "SELECT %s FROM stock_daily_meta_qfq WHERE date>=? AND substr(code,1,3) IN (%s)" % (cols, ph),
        con, params=(start,) + PREFIXES)
    con.close()
    df["date"] = df["date"].astype(str)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def jdump(obj, name):
    p = os.path.join(HERE, name)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", p)


def part_j():
    df = load_panel("20140101", "code,date,open,high,low,close,volume")
    out = {"ta": [], "hammer": [], "engulf": []}
    base_f20 = []
    for code, g in df.groupby("code", sort=False):
        n = len(g)
        if n < 300:
            continue
        d = g["date"].to_numpy()
        o = g["open"].to_numpy(float); h = g["high"].to_numpy(float)
        l = g["low"].to_numpy(float); c = g["close"].to_numpy(float)
        v = g["volume"].to_numpy(float)

        def ma(x, w):
            cs = np.cumsum(np.insert(x, 0, 0.0))
            out = np.full(len(x), np.nan)
            out[w - 1:] = (cs[w:] - cs[:-w]) / w
            return out

        def ema(x, w):
            a = 2.0 / (w + 1); out = np.full(len(x), np.nan); e = None
            for i, xv in enumerate(x):
                e = xv if e is None else a * xv + (1 - a) * e
                out[i] = e
            return out

        ma5v = ma(v, 5); ma60 = ma(c, 60)
        prev_c = np.concatenate([[np.nan], c[:-1]]); prev_o = np.concatenate([[np.nan], o[:-1]])
        # 周线合成(ISO周)
        wk_close = []; wk_end = []; cur = None; last = None
        for i, ds in enumerate(d):
            wk = datetime.strptime(ds, "%Y%m%d").isocalendar()[:2]
            if wk != cur:
                if cur is not None:
                    wk_close.append(last); wk_end.append(i - 1)
                cur = wk
            last = c[i]
        wk_close.append(last); wk_end.append(n - 1)
        wc = np.asarray(wk_close, float)
        if len(wc) >= 40:
            dif = ema(wc, 12) - ema(wc, 26); dea = ema(dif, 9)
            w5 = ma(wc, 5); w10 = ma(wc, 10)
            last_ev = -99
            for wi in range(1, len(wc) - 1):
                if (dif[wi - 1] <= dea[wi - 1] and dif[wi] > dea[wi]
                        and w5[wi] > w10[wi] and w5[wi - 1] <= w10[wi - 1]):
                    si = wk_end[wi] + 1
                    if si >= n or d[si] < "20160101":
                        continue
                    if si - last_ev < 5:
                        continue
                    last_ev = si
                    out["ta"].append((code, si))
        last_ev2 = -99
        sw_min = pd.Series(c).rolling(250, min_periods=250).min().to_numpy()
        for i in range(260, n - 21):
            if d[i] < "20160101":
                continue
            body = abs(c[i] - o[i]); rng = h[i] - l[i]
            lower = min(o[i], c[i]) - l[i]
            at_sup = (not np.isnan(ma60[i])) and abs(c[i] / ma60[i] - 1) <= 0.03
            shrink = (not np.isnan(ma5v[i])) and v[i] < ma5v[i]
            if at_sup and shrink and rng > 0 and lower >= 2 * body and lower / rng >= 0.5:
                if i - last_ev2 >= 5:
                    last_ev2 = i
                    out["hammer"].append((code, i))
            eng = (not np.isnan(prev_c[i - 1])) and prev_c[i - 1] < prev_o[i - 1] \
                and c[i] > o[i - 1] and o[i] < c[i - 1] and c[i] > o[i]
            if eng and not np.isnan(sw_min[i]) and c[i] / sw_min[i] - 1 <= 0.35:
                if i - last_ev2 >= 5:
                    last_ev2 = i
                    out["engulf"].append((code, i))

    def settle(evs):
        f = []
        for code, i in evs:
            sub = dmap[code]
            if i + 21 >= len(sub[0]):
                continue
            oo = sub[1][i + 1]
            if not oo or oo <= 0:
                continue
            f.append(sub[4][i + 20] / oo - 1.0)
        return np.asarray(f)

    dmap = {}
    for code, g in df.groupby("code", sort=False):
        dmap[code] = (g["date"].to_numpy(), g["open"].to_numpy(float), g["high"].to_numpy(float),
                      g["low"].to_numpy(float), g["close"].to_numpy(float), g["volume"].to_numpy(float))
    res = {"id": "TEXTBOOK-BS-POINTS-20261003", "mode": "independent_reimpl"}
    for key, lab, orig in [("ta", "weekly_macd_cross", "n=19171 win42.8%"),
                           ("hammer", "support_shrink_hammer", "n=20004 win47.2%"),
                           ("engulf", "low_engulfing", "n=41468 win50.3%")]:
        f = settle(out[key])
        # 基线: 同结算口径的信号日次开->20日全样本(win) 用抽样(每stock每40日)近似
        res[lab] = {"n": int(len(f)), "win20": round(float((f > 0).mean()), 4) if len(f) else None,
                    "med20_pct": round(float(np.median(f)) * 100, 3) if len(f) else None,
                    "orig": orig}
    bl = []
    for code, sub in dmap.items():
        nn = len(sub[0])
        idx = np.arange(260, nn - 21, 40)
        for i in idx:
            oo = sub[1][i + 1]
            if oo and oo > 0:
                bl.append(sub[4][i + 20] / oo - 1.0)
    bl = np.asarray(bl)
    res["baseline_sampled"] = {"n": int(len(bl)), "win20": round(float((bl > 0).mean()), 4)}
    res["caveats"] = ["ST未剔(原名服务未接, 影响约2-3%股票); 原档跑在已失考本地副本(谱系发现F9); 5日去重=模式内独立计数"]
    jdump(res, "reaudit_textbook_bs_result.json")
    print("J)", json.dumps({k2: res[k2] for k2 in
                            ["weekly_macd_cross", "support_shrink_hammer", "low_engulfing", "baseline_sampled"]},
                           ensure_ascii=False))
    return res


def part_k():
    df = load_panel("20150101", "code,date,open,close,volume")
    df = df.dropna(subset=["open", "close"])
    st_cache = r"D:\zcodesj\attribution\stock_info_cache.json"
    st_codes = set()
    if os.path.exists(st_cache):
        st = json.load(open(st_cache, encoding="utf-8"))["stocks"]
        st_codes = {k for k, v in st.items() if v.get("is_st")}
    ev_rows = []   # (date, band, ret20, kind)
    ctl_rows = []
    dn0 = pd.Timestamp("2000-01-01")
    for code, g in df.groupby("code", sort=False):
        if code[:3] not in PREFIXES or code in st_codes:
            continue
        n = len(g)
        if n < 261:
            continue
        o = g["open"].to_numpy(float); c = g["close"].to_numpy(float); v = g["volume"].to_numpy(float)
        d = g["date"].to_numpy()
        dn = (pd.to_datetime(pd.Series(d), format="%Y%m%d") - dn0).dt.days.to_numpy()
        s = pd.Series(c)
        mn250 = s.rolling(250, min_periods=250).min().to_numpy()
        mx250 = s.rolling(250, min_periods=250).max().to_numpy()
        box = s.shift(1).rolling(60, min_periods=60).max().to_numpy()
        vma20 = pd.Series(v).shift(1).rolling(20, min_periods=20).mean().to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            pos = (c - mn250) / np.where(mx250 > mn250, mx250 - mn250, np.nan)
            brk = (c > box) & (v >= 1.2 * vma20)
        brk &= ~np.isnan(pos) & ~np.isnan(box) & ~np.isnan(vma20)
        elig = np.where((c <= 30.0) & ~np.isnan(pos))[0]
        f20v = np.full(n, np.nan); f5v = np.full(n, np.nan)
        for i in range(n - 21):
            j = i + 1
            if j >= n or dn[j] - dn[i] > 10:
                continue
            if not np.isfinite(o[j]) or o[j] <= 0:
                continue
            f20v[i] = c[i + 20] / o[j] - 1.0 - 0.002
            f5v[i] = c[i + 5] / o[j] - 1.0 - 0.002
        band = np.where(pos < 0.35, "low", np.where(pos <= 0.70, "mid", "high"))
        for i in elig:
            if not ("20160904" <= d[i] <= "20260910"):
                continue
            if not np.isfinite(f20v[i]):
                continue
            if brk[i]:
                ev_rows.append((d[i], band[i], f20v[i], f5v[i]))
            elif i % 3 == 0:
                ctl_rows.append((d[i], band[i], f20v[i], f5v[i]))
    ev = pd.DataFrame(ev_rows, columns=["date", "band", "f20", "f5"])
    ctl = pd.DataFrame(ctl_rows, columns=["date", "band", "f20", "f5"])

    def cluster_t(sub, col="f20"):
        gg = sub.groupby("date")[col].mean()
        return float(gg.mean() / (gg.std(ddof=1) / np.sqrt(len(gg)))) if len(gg) > 2 else float("nan")

    res = {"id": "BREAKOUT-MIDPOS", "mode": "independent_reimpl", "bands": {}}
    for b in ["low", "mid", "high"]:
        e = ev[ev["band"] == b]
        cband = ctl[ctl["band"] == b]
        # 同带增量: 按日配对(有双方数据的日)
        em = e.groupby("date")["f20"].mean(); cm = cband.groupby("date")["f20"].mean()
        pair = (em - cm).dropna()
        pt = float(pair.mean() / (pair.std(ddof=1) / np.sqrt(len(pair)))) if len(pair) > 2 else float("nan")
        res["bands"][b] = {
            "n": int(len(e)), "win": round(float((e["f20"] > 0).mean()), 4),
            "mean_pct": round(float(e["f20"].mean()) * 100, 3),
            "cluster_t": round(cluster_t(e), 2),
            "ctl_n": int(len(cband)),
            "same_band_increment_pp": round(float(pair.mean()) * 100, 3),
            "paired_t": round(pt, 2),
        }
    res["caveats"] = ["对照日=同带非突破日1/3抽样; ST用stock_info_cache剔除; 成本0.2%含在收益内"]
    jdump(res, "reaudit_breakout_midpos_result.json")
    print("K)", json.dumps(res["bands"], ensure_ascii=False))
    return res


def part_l():
    rows = [json.loads(l) for l in open(PHASE, encoding="utf-8")]
    ph = pd.DataFrame(rows)
    ph = ph.sort_values("phase_date").reset_index(drop=True)
    # 次日上证: dragon_quant_meta index_daily_meta
    con = sqlite3.connect("file:%s?mode=ro" % META, uri=True)
    cols = [r[1] for r in con.execute("PRAGMA table_info(index_daily_meta)")]
    codecol = "ts_code" if "ts_code" in cols else ("code" if "code" in cols else cols[0])
    datecol = "date" if "date" in cols else ("trade_date" if "trade_date" in cols else cols[1])
    closecol = "close" if "close" in cols else cols[-1]
    cand = ["000001.SH", "000001", "sh000001", "1A0001"]
    idx = None
    for cd in cand:
        try:
            t = pd.read_sql(
                "SELECT %s AS d,%s AS c FROM index_daily_meta WHERE %s=? AND d>='20241201' ORDER BY d"
                % (datecol, closecol, codecol), con, params=(cd,))
        except Exception:
            continue
        if len(t) > 100:
            idx = t
            print("L) index source=%s rows=%d" % (cd, len(t)))
            break
    con.close()
    assert idx is not None, "FAIL-CLOSED: 上证指数序列未找到"
    idx["d"] = idx["d"].astype(str)
    idx["ret1"] = idx["c"].pct_change()
    idxm = idx.set_index("d")["ret1"]
    ph["next_date"] = ph["phase_date"].shift(-1)
    ph["next_ret"] = ph["next_date"].map(idxm)
    ph["next_br"] = ph["br"].shift(-1)
    sub = ph[ph["phase"].isin(["ebbing", "healing"])].dropna(subset=["next_ret"])
    e = sub[sub["phase"] == "ebbing"]["next_ret"]
    h = sub[sub["phase"] == "healing"]["next_ret"]
    mw = stats.mannwhitneyu(e, h, alternative="two-sided")
    br = sub.dropna(subset=["next_br"])
    be = br[br["phase"] == "ebbing"]["next_br"] * 100
    bh = br[br["phase"] == "healing"]["next_br"] * 100
    mwb = stats.mannwhitneyu(be, bh, alternative="two-sided")
    res = {
        "id": "EMOTION-PHASE-V2-NEXTDAY", "mode": "independent_reimpl",
        "n_ebbing": int(len(e)), "n_healing": int(len(h)),
        "next_sh_ret": {"ebbing_mean_pct": round(float(e.mean()) * 100, 3),
                        "healing_mean_pct": round(float(h.mean()) * 100, 3),
                        "mwu_p_two_sided": round(float(mw.pvalue), 4)},
        "next_zha_rate": {"ebbing_pct": round(float(be.mean()), 2),
                          "healing_pct": round(float(bh.mean()), 2),
                          "mwu_p_two_sided": round(float(mwb.pvalue), 4)},
        "caveats": ["台账419日; 次日上证=dragon_quant_meta index_daily_meta; MW-U双侧(原档单侧p=0.91)"],
    }
    jdump(res, "reaudit_emotion_phase_result.json")
    print("L)", json.dumps({k2: res[k2] for k2 in ["n_ebbing", "n_healing", "next_sh_ret", "next_zha_rate"]},
                           ensure_ascii=False))
    return res


if __name__ == "__main__":
    part_l()
    part_k()
    part_j()
    print("done")
