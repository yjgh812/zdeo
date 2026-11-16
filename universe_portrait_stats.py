#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""universe_portrait_stats.py — A股宇宙深度画像统计引擎 v1（侦察级·零交易权）
================================================================================
主人令 2026-10-01 深夜:「A股宇宙深度研究画像系统,提高胜率,寻找买卖点,买卖区域。并全对A股宇宙的股票做统计」

边界(不可豁免):
- 画像 v1 特征面已判死(falsified#49)、画像A/B已CLOSED(10/2)——本件不复活,维度全部取自
  2026-10-01 八轮验证过的资产(fresh新鲜度/量能vr120/位置pos250/价格带/趋势排列)。
- 侦察级:产物仅供主人研判与后续PD裁决,不进 trade_executor/pre_trade_check 任何闸门。
- 唯一价格源=qfq库(宪法§7);剔科创/北交所/ST近似(名称门禁在下游);价>30元仍统计但单独成带。

三层结构:
  A层 --snapshot  全宇宙逐股画像快照(~4900只×12维) → data/universe_portrait_latest.json
  B层            横截面分布(市场结构画像:各维度分带占比+分位) → 同上
  C层 --history  画像格子×fwd20 历史统计(2023-01~2026-09月末截面抽样,
                  趋势×位置×量能×价格带主交叉) → data/universe_portrait_grid_history.json

用法:
  python universe_portrait_stats.py --snapshot   # 当日截面(默认主库最新交易日)
  python universe_portrait_stats.py --history    # 历史格子(约2-3分钟)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np

from portrait_manifest import write_manifest

ROOT = r"D:\zcodesj"
QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(zc副本停更10/09已废, SOP一百三十二)
OUT_JSON = os.path.join(ROOT, "data", "universe_portrait_latest.json")
OUT_GRID = os.path.join(ROOT, "data", "universe_portrait_grid_history.json")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_panel(fetch_from="20200601"):
    con = sqlite3.connect(QFQ_DB, uri=True)
    rows = con.execute(
        "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date",
        (fetch_from,)).fetchall()
    con.close()
    panel = {}
    for code, d, o, h, low, c, v in rows:
        if code[:3] in ("688", "689") or code[:1] in ("4", "8"):
            continue
        p = panel.setdefault(code, ([], [], [], [], [], []))
        p[0].append(d); p[1].append(o); p[2].append(h); p[3].append(low); p[4].append(c); p[5].append(v)
    return panel


def ma(x, win):
    out = np.full(len(x), np.nan)
    if len(x) >= win:
        cs = np.cumsum(np.insert(np.asarray(x, float), 0, 0.0))
        out[win - 1:] = (cs[win:] - cs[:-win]) / win
    return out


def portrait_at(code, dates, opens, highs, lows, closes, vols, t):
    """t 日(含)收盘可知的画像向量。返回 None=数据不足。"""
    n = len(dates)
    if t < 260 or t >= n:
        return None
    c = np.asarray(closes[: t + 1], float)
    v = np.asarray(vols[: t + 1], float)
    px = float(closes[t])
    if not px or px <= 0:
        return None
    ma5, ma10, ma20, ma60 = ma(c, 5)[-1], ma(c, 10)[-1], ma(c, 20)[-1], ma(c, 60)[-1]
    vma5, vma120 = ma(v, 5)[-1], ma(v, 120)[-1]
    if np.isnan(ma60) or np.isnan(vma120):
        return None
    w250 = c[-250:]
    lo250, hi250 = float(w250.min()), float(w250.max())
    pos250 = (px - lo250) / (hi250 - lo250) * 100 if hi250 > lo250 else 50.0
    vr120 = (vma5 / vma120) if vma120 else None
    # 趋势态
    if ma5 > ma10 > ma20 > ma60:
        trend = "多头排列"
    elif ma5 < ma10 < ma20 < ma60:
        trend = "空头排列"
    else:
        trend = "震荡"
    ma20_up = ma(c, 20)[-1] > ma(c, 20)[-6] if len(c) >= 26 else None
    # 波动(20日收益率std年化近似)
    rets = c[-21:] / c[-22:-1] - 1.0 if len(c) >= 22 else None
    vol20 = float(np.std(rets)) * 100 if rets is not None and len(rets) >= 10 else None
    # fresh(距最近启动≤5日; 启动=放量大阳∧前60日跌≥25%∧启动前缩量≤0.95, 八轮同式)
    chg = c[1:] / c[:-1] - 1.0
    vma20r = ma(v, 20)[1:]
    v250r = ma(v, 250)[1:]
    big = (vma20r > 0) & (v[1:] >= 2.0 * vma20r) & (chg >= 0.03)
    dd60 = np.full(len(chg), np.nan)
    if len(c) >= 61:
        sw = np.lib.stride_tricks.sliding_window_view(c, 60)
        dd = (sw.max(axis=1) - sw.min(axis=1)) / sw.max(axis=1)
        dd60[59:] = dd[: len(dd60) - 59] if len(dd) >= len(dd60) - 59 else dd60[59: len(dd) + 59]
    pre_sh = np.full(len(chg), np.nan)
    ok = v250r > 0
    pre_sh[ok] = vma20r[ok] / v250r[ok]
    startup = big & (dd60 >= 0.25) & (pre_sh <= 0.95)
    idx_startup = np.where(startup)[0]
    fresh = bool(len(idx_startup) > 0 and (len(chg) - 1 - idx_startup[-1]) <= 5)
    # 爆量末段(四轮D同式)
    ret20 = px / float(c[-21]) - 1.0 if len(c) >= 21 and c[-21] else None
    blowoff = bool(vr120 and vr120 >= 1.5 and ret20 is not None and ret20 >= 0.20)
    bias20 = px / float(ma20) - 1.0 if ma20 else None  # 乖离MA20 (2026-10-03 R闸候选PREREG_BIAS20_BAN)
    # 价格带
    pb = ("≤5元" if px <= 5 else "5-10元" if px <= 10 else "10-20元" if px <= 20 else "20-30元" if px <= 30 else ">30元")
    return dict(
        px=round(px, 2), trend=trend, ma20_up=bool(ma20_up),
        pos250=round(pos250, 1), off250_lo=round((px / lo250 - 1) * 100, 1) if lo250 else None,
        vr120=round(float(vr120), 2) if vr120 else None,
        vol20_pct=round(vol20, 2) if vol20 is not None else None,
        fresh=fresh, blowoff=blowoff, price_band=pb,
        ret20_pct=round(ret20 * 100, 1) if ret20 is not None else None,
        bias20_pct=round(bias20 * 100, 1) if bias20 is not None else None,
    )


def band_pos(p):
    return ("深底<20" if p < 20 else "低位20-40" if p < 40 else "中位40-60" if p < 60 else "高位60-80" if p < 80 else "顶区≥80")


def band_vr(x):
    if x is None:
        return "NA"
    return ("极缩<0.7" if x < 0.7 else "缩0.7-1" if x < 1.0 else "常1-1.5" if x < 1.5 else "爆≥1.5")


def cmd_snapshot():
    panel = load_panel()
    rows, skipped = [], 0
    latest_dates = max(dates[-1] for dates, *_ in panel.values())
    for code, (dates, opens, highs, lows, closes, vols) in panel.items():
        t = len(dates) - 1
        if dates[t] != latest_dates:
            skipped += 1
            continue
        p = portrait_at(code, dates, opens, highs, lows, closes, vols, t)
        if p is None:
            skipped += 1
            continue
        p["code"] = code
        rows.append(p)
    # B层: 分布
    def dist(key, bands_fn):
        g = defaultdict(int)
        for r in rows:
            g[bands_fn(r)] += 1
        total = len(rows)
        return {k: dict(n=v, pct=round(v / total * 100, 1)) for k, v in sorted(g.items())}

    out = dict(
        # 批1' 元数据(2026-10-11): 零交易权决策位为静态政策恒写入; qfq_max=asof(截面引擎同一panel)
        schema_version="universe.portrait.v1.1", owner="universe_portrait",
        built_at=datetime.now().isoformat(timespec="seconds"),
        qfq_max_date=latest_dates,
        permissions={"display": True, "research_shadow": True, "ranking": False,
                     "filtering": False, "pre_trade_gate": False, "trade_execution": False},
        version="universe_portrait_stats_v1", asof=latest_dates, recon_only=True,
        n_stocks=len(rows), n_skipped=skipped,
        dist=dict(
            trend=dist("trend", lambda r: r["trend"]),
            pos250=dist("pos250", lambda r: band_pos(r["pos250"])),
            vr120=dist("vr120", lambda r: band_vr(r["vr120"])),
            price_band=dist("price_band", lambda r: r["price_band"]),
            fresh=dist("fresh", lambda r: "fresh" if r["fresh"] else "aged/无"),
            blowoff=dist("blowoff", lambda r: "爆量末段" if r["blowoff"] else "正常"),
        ),
        # 关键交叉: 趋势×位置
        cross_trend_pos=defaultdict(dict),
        rows=rows,
    )
    g = defaultdict(int)
    for r in rows:
        g[(r["trend"], band_pos(r["pos250"]))] += 1
    out["cross_trend_pos"] = {f"{k[0]}|{k[1]}": v for k, v in sorted(g.items())}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    write_manifest("universe_portrait", {"data/universe_portrait_latest.json": OUT_JSON})
    print(f"[PORTRAIT] asof={latest_dates} stocks={len(rows)} skipped={skipped} -> {OUT_JSON}")
    print("  分布:", json.dumps(out["dist"], ensure_ascii=False)[:400])
    return 0


def cmd_history():
    """C层: 月末截面×画像格子×fwd20(次开起, 含-8%止损口径与毛口径)。2023-01~2026-09。"""
    panel = load_panel()
    # 全部交易日 → 月末(每月最后交易日)
    all_dates = sorted({dates[-1] for dates, *_ in
                        ((v[0],) for v in [list(panel.values())[0]])})  # placeholder
    # 直接从任一票取日历(全市场共享交易日)
    some = next(iter(panel.values()))
    cal = some[0]
    month_last = {}
    for d in cal:
        if d >= "20230101":
            month_last[d[:6]] = d
    month_ends = sorted(set(month_last.values()))
    print(f"[PORTRAIT-HIST] 月末截面 {len(month_ends)} 个 ({month_ends[0]}~{month_ends[-1]})")
    grid = defaultdict(list)   # (trend,pos,vol_band,price_band) -> [fwd20_net]
    grid2 = defaultdict(list)  # trend×fresh
    n_sample = 0
    for code, (dates, opens, highs, lows, closes, vols) in panel.items():
        d2i = {d: i for i, d in enumerate(dates)}
        for me in month_ends:
            t = d2i.get(me)
            if t is None or t + 21 >= len(dates):
                continue
            p = portrait_at(code, dates, opens, highs, lows, closes, vols, t)
            if p is None:
                continue
            entry = float(opens[t + 1])
            if not entry or entry <= 0:
                continue
            # 毛口径 fwd20
            fwd = float(closes[t + 20]) / entry - 1.0
            # 含-8%止损口径(收盘判定)
            ex = None
            for j in range(t + 1, t + 21):
                if closes[j] <= entry * 0.92:
                    ex = float(closes[j])
                    break
            ex = ex if ex is not None else float(closes[t + 20])
            t20e = ex / entry - 1.0
            n_sample += 1
            key = (p["trend"], band_pos(p["pos250"]), band_vr(p["vr120"]), p["price_band"])
            grid[key].append((t20e, fwd))
            grid2[(p["trend"], "fresh" if p["fresh"] else "aged")].append((t20e, fwd))
    # 汇总
    def stat(vals_t, vals_f):
        a = np.asarray(vals_t)
        b = np.asarray(vals_f)
        return dict(n=len(a), t20e_mean=round(float(a.mean()) * 100, 2), t20e_med=round(float(np.median(a)) * 100, 2),
                    t20e_win=round(float((a > 0).mean()) * 100, 1),
                    fwd20_mean=round(float(b.mean()) * 100, 2))
    out_grid = {}
    for k, v in sorted(grid.items(), key=lambda x: -len(x[1])):
        if len(v) < 300:
            continue
        out_grid["|".join(k)] = stat([x[0] for x in v], [x[1] for x in v])
    out_grid2 = {f"{k[0]}|{k[1]}": stat([x[0] for x in v], [x[1] for x in v])
                 for k, v in sorted(grid2.items()) if len(v) >= 300}
    out = dict(version="universe_portrait_grid_v1", n_month_ends=len(month_ends),
               n_samples=n_sample, recon_only=True,
               grid_trend_pos_vol_price=out_grid, grid_trend_fresh=out_grid2,
               note="月末截面抽样2023-01~2026-09; fwd=次开起; t20e含-8%收盘止损; ≥300样本入表")
    with open(OUT_GRID, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[PORTRAIT-HIST] 样本={n_sample} 格子={len(out_grid)} -> {OUT_GRID}")
    # 控制台: top10 / bottom5
    ranked = sorted(out_grid.items(), key=lambda x: -x[1]["t20e_mean"])
    print("\nTOP10 正期望画像格(t20e):")
    for k, s in ranked[:10]:
        print(f"  {k:44s} n={s['n']:6d} t20e={s['t20e_mean']:+6.2f}% med={s['t20e_med']:+6.2f}% win={s['t20e_win']:4.1f}%")
    print("BOTTOM5:")
    for k, s in ranked[-5:]:
        print(f"  {k:44s} n={s['n']:6d} t20e={s['t20e_mean']:+6.2f}% med={s['t20e_med']:+6.2f}% win={s['t20e_win']:4.1f}%")
    print("\n趋势×fresh:")
    for k, s in out_grid2.items():
        print(f"  {k:20s} n={s['n']:6d} t20e={s['t20e_mean']:+6.2f}% win={s['t20e_win']:4.1f}%")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args()
    if a.snapshot:
        sys.exit(cmd_snapshot())
    if a.history:
        sys.exit(cmd_history())
    ap.print_help()
