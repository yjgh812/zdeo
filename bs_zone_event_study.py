# -*- coding: utf-8 -*-
"""买卖区域事件研究 v1.1（侦察级，2026-09-29，SOP四十九-V/W）
问题：在选股宇宙（低位强池）上，哪类「买入区域」有真实前向胜率？何时有效（市况分层）？
      「卖出区域」（60日前高压力带）的本地实证数字是什么？
population = 低位强池：pos250<=50 ∧ close>MA20 ∧ MA20>MA60 ∧ 距250日收盘低<=35% ∧ 近60日内存在放量大阳(量>=2×20日均量 ∧ 涨>=3%)
  近似复刻 universe_startup_scan 启动口径（不含启动前缩量/前60日跌25%细筛）→ 样本更宽，偏差如实标注。
买入 zones（entry=next open，杜绝未来函数）：
  Z1 确认窗⭐  off250=close/low250close-1 ∈ [5%,10%]   （9/19 段起+5~10% 最优介入窗复验）
  Z2 回踩MA10  low<=MA10 ∧ close>=MA10 ∧ MA20>MA20[5日前]
  Z3 回踩MA20  low<=MA20 ∧ close>=MA20 ∧ MA20>MA20[5日前]
  Z4 追高负对照 ret5>=15%                              （与9/18「涨3-6%最差/追涨禁令」互证）
卖出 zones（60日前高压力带，v1.1新增）：
  S1 冲高回落  high>=res60_prev ∧ close<res60_prev     （贴前高冲击失败→卖出区域候选）
  S2 放量创高  close>=res60_prev ∧ vr120>=1.2          （放量突破→持有证据）
  S3 贴前高滞涨  close>=res60_prev×0.98 ∧ high<res60_prev（贴压力未冲→观察）
metrics：fwd5/fwd10/fwd20 = open[D+1+k]/open[D+1]-1；t20e = 收盘-8%止损∧20日持有（未触损=T+20收盘）
市况分层（v1.1）：上证收盘>MA20=BULL段，否则BEAR段（vipdoc sh000001，事件日对齐前一交易日）。
去重：同code同类事件间隔>=5交易日。
唯一价格源 = qfq库（宪法§7）。ST/一字板近似未滤（如实标注）。
输出：data/bs_zone_event_study_<date>.json + 控制台汇总表。零交易权，仅供呈报与PD裁决。
"""
import json
import sqlite3
import sys
from collections import defaultdict

import numpy as np

from tdx_reader import read_day

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
FETCH_FROM = "20200601"          # 预热窗口（250日滚动统计）
EVENT_FROM = "20230101"          # 事件统计起点（保证 fwd20 与 t20e 完整）
ZONES = ("Z1_confirm_window", "Z2_pullback_ma10", "Z3_pullback_ma20", "Z4_chase_negative")
SELL_ZONES = ("S1_fail_retest", "S2_breakout_vol", "S3_stall_near_high")
ZONE_CN = {
    "Z1_confirm_window": "Z1 确认窗⭐(距年低+5~10%)",
    "Z2_pullback_ma10": "Z2 回踩MA10企稳带",
    "Z3_pullback_ma20": "Z3 深回踩MA20企稳带",
    "Z4_chase_negative": "Z4 追高负对照(ret5>=15%)",
    "S1_fail_retest": "S1 冲高回落(卖出区域候选)",
    "S2_breakout_vol": "S2 放量创60日新高(持有证据)",
    "S3_stall_near_high": "S3 贴前高滞涨(观察)",
    "Z2_shrink_vol": "Z2s 回踩MA10·缩量企稳",
    "Z2_norm_vol": "Z2n 回踩MA10·常量企稳",
    "Z2_heavy_vol": "Z2h 回踩MA10·放量下探",
}


def load_regime():
    """上证 close>MA20 = BULL。返回升序 [(date, bull)] 与日期->最近bull 的查找函数。"""
    rows = read_day("sh", "000001")
    closes = [r[4] for r in rows]
    dates = [r[0] for r in rows]
    seq = []
    ma20 = []
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= 20:
            s -= closes[i - 20]
            ma20.append(s / 20)
            seq.append((dates[i], c >= ma20[-1]))
    d2bull = {d: b for d, b in seq}
    dlist = [d for d, _ in seq]
    import bisect

    def regime_at(date_int):
        i = bisect.bisect_right(dlist, int(date_int)) - 1
        return seq[i][1] if i >= 0 else None
    return d2bull, regime_at


def load_panel():
    con = sqlite3.connect(QFQ_DB, uri=True)
    sql = ("SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
           "WHERE date>=? AND (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date")
    cur = con.execute(sql, (FETCH_FROM,))
    rows = cur.fetchall()
    con.close()
    panel = defaultdict(lambda: ([], [], [], [], [], []))
    for code, date, o, h, low, c, v in rows:
        if code[:3] in ("688", "689") or code[:1] in ("4", "8"):
            continue  # 科创板/北交所剔除（宪法§2）
        p = panel[code]
        p[0].append(date)
        p[1].append(o)
        p[2].append(h)
        p[3].append(low)
        p[4].append(c)
        p[5].append(v)
    return panel


def rolling_pos(x, win=250):
    if len(x) < win:
        return np.full(len(x), np.nan)
    sw = np.lib.stride_tricks.sliding_window_view(x, win)
    pos = (sw[:, :-1] < sw[:, -1:]).mean(axis=1)
    return np.concatenate([np.full(win - 1, np.nan), pos])


def rolling_min(x, win):
    out = np.full(len(x), np.nan)
    if len(x) >= win:
        out[win - 1:] = np.lib.stride_tricks.sliding_window_view(x, win).min(axis=1)
    return out


def ma(x, win):
    out = np.full(len(x), np.nan)
    if len(x) >= win:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[win - 1:] = (c[win:] - c[:-win]) / win
    return out


def collect_events():
    panel = load_panel()
    events = []        # 买入区域 (zone, code, date, t)
    sell_events = []   # 压力带 (zone, code, date, t)
    strict_events = []  # v1.3 严格启动口径（扫描器细筛近似）上的头部买点带
    for code, (dates, opens, highs, lows, closes, vols) in panel.items():
        n = len(dates)
        if n < 260:
            continue
        o = np.asarray(opens, dtype=np.float64)
        h = np.asarray(highs, dtype=np.float64)
        low = np.asarray(lows, dtype=np.float64)
        c = np.asarray(closes, dtype=np.float64)
        v = np.asarray(vols, dtype=np.float64)
        prev_c = np.concatenate([[np.nan], c[:-1]])
        chg = c / prev_c - 1.0
        ret5 = np.full(n, np.nan)
        ret5[5:] = c[5:] / c[:-5] - 1.0
        ma10, ma20, ma60 = ma(c, 10), ma(c, 20), ma(c, 60)
        ma20_prev5 = np.full(n, np.nan)
        ma20_prev5[5:] = ma20[:-5]
        vma5, vma20, vma120 = ma(v, 5), ma(v, 20), ma(v, 120)
        pos250 = rolling_pos(c, 250)
        low250 = rolling_min(c, 250)
        big = (vma20 > 0) & (v >= 2.0 * vma20) & (chg >= 0.03)
        big60 = np.convolve(big.astype(np.int8), np.ones(60, dtype=np.int8), mode="full")[:n] > 0
        pop = ((pos250 <= 0.50) & (c > ma20) & (ma20 > ma60)
               & (c / low250 - 1.0 <= 0.35) & big60 & (c <= 30.0))
        off250 = c / low250 - 1.0
        # v1.3 严格启动口径（近似复刻 universe_startup_scan L78-114：放量大阳+前60日跌≥25%+启动前缩量；
        # 三年分位/LOW_VOL cap 细分略去，近似如实标注）
        dd60 = np.full(n, np.nan)
        pre_shrink = np.full(n, np.nan)
        v250 = ma(v, 250)
        if n >= 60:
            sw60 = np.lib.stride_tricks.sliding_window_view(c, 60)
            dd60[59:] = (sw60.max(axis=1) - sw60.min(axis=1)) / sw60.max(axis=1)
        okv = v250 > 0
        pre_shrink[okv] = vma20[okv] / v250[okv]
        startup = big & (dd60 >= 0.25) & (pre_shrink <= 0.95)
        startup40 = np.convolve(startup.astype(np.int8), np.ones(40, dtype=np.int8), mode="full")[:n] > 0
        pop_strict = pop & startup40
        # 60日前高（截至昨日，不含当日）
        res_prev = np.full(n, np.nan)
        if n > 60:
            res_prev[60:] = np.lib.stride_tricks.sliding_window_view(c, 60).max(axis=1)[:-1]
        vr120 = np.full(n, np.nan)
        ok = (vma120 > 0) & ~np.isnan(vma5)
        vr120[ok] = vma5[ok] / vma120[ok]
        cand = defaultdict(list)
        strict_cand = defaultdict(list)
        sell_cand = defaultdict(list)
        for t in range(260, n):
            if dates[t] < EVENT_FROM:
                continue
            ma20_up = ma20[t] > ma20_prev5[t]
            if pop[t]:
                if 0.05 <= off250[t] <= 0.10:
                    cand["Z1_confirm_window"].append(t)
                    if pop_strict[t]:
                        strict_cand["Z1_confirm_window"].append(t)
                if (not np.isnan(ma10[t]) and low[t] <= ma10[t] <= c[t] and ma20_up):
                    cand["Z2_pullback_ma10"].append(t)
                    # v1.2 企稳触发质量切分：缩量回踩 vs 常量 vs 放量下探（互斥，shrink/heavy优先）
                    if not np.isnan(vma5[t]) and v[t] < vma5[t]:
                        cand["Z2_shrink_vol"].append(t)
                        if pop_strict[t]:
                            strict_cand["Z2_shrink_vol"].append(t)
                    elif not np.isnan(vma5[t]) and v[t] >= 1.5 * vma5[t]:
                        cand["Z2_heavy_vol"].append(t)
                    else:
                        cand["Z2_norm_vol"].append(t)
                if (not np.isnan(ma20[t]) and low[t] <= ma20[t] <= c[t] and ma20_up):
                    cand["Z3_pullback_ma20"].append(t)
                if ret5[t] >= 0.15:
                    cand["Z4_chase_negative"].append(t)
            # 压力带事件不要求 pop（任何强池外个股的前高行为同样成立；口径=全市场剔科创/北交/价<=30）
            if not np.isnan(res_prev[t]) and c[t] <= 30.0:
                if h[t] >= res_prev[t] and c[t] < res_prev[t]:
                    sell_cand["S1_fail_retest"].append(t)
                if c[t] >= res_prev[t] and not np.isnan(vr120[t]) and vr120[t] >= 1.2:
                    sell_cand["S2_breakout_vol"].append(t)
                if c[t] >= res_prev[t] * 0.98 and h[t] < res_prev[t]:
                    sell_cand["S3_stall_near_high"].append(t)
        for zone, ts in cand.items():
            last = -99
            for t in ts:
                if t - last >= 5:
                    events.append((zone, code, dates[t], t))
                    last = t
        for zone, ts in strict_cand.items():
            last = -99
            for t in ts:
                if t - last >= 5:
                    strict_events.append((zone, code, dates[t], t))
                    last = t
        for zone, ts in sell_cand.items():
            last = -99
            for t in ts:
                if t - last >= 5:
                    sell_events.append((zone, code, dates[t], t))
                    last = t
    return panel, events, sell_events, strict_events


def forward_metrics(panel, events):
    recs = defaultdict(list)
    for zone, code, date, t in events:
        dates, opens, _, _, closes, _ = panel[code]
        n = len(dates)
        if t + 1 >= n:
            continue
        entry = opens[t + 1]
        if not entry or entry <= 0:
            continue
        fwd = {}
        for k in (5, 10, 20):
            if t + 1 + k < n and opens[t + 1 + k] > 0:
                fwd[k] = opens[t + 1 + k] / entry - 1.0
        t20e = None
        if t + 21 < n:
            exit_px = None
            for j in range(t + 1, t + 21):
                if closes[j] <= entry * 0.92:
                    exit_px = closes[j]
                    break
            if exit_px is None:
                exit_px = closes[t + 20]
            t20e = exit_px / entry - 1.0
        recs[zone].append({"date": date, "year": date[:4], "fwd5": fwd.get(5), "fwd10": fwd.get(10),
                           "fwd20": fwd.get(20), "t20e": t20e})
    return recs


def summarize(vals):
    vals = [x for x in vals if x is not None]
    if not vals:
        return {"n": 0}
    a = np.asarray(vals, dtype=np.float64)
    return {"n": len(a), "mean": round(float(a.mean()) * 100, 3), "med": round(float(np.median(a)) * 100, 3),
            "win": round(float((a > 0).mean()) * 100, 1)}


def main():
    panel, events, sell_events, strict_events = collect_events()
    d2bull, regime_at = load_regime()
    recs = forward_metrics(panel, events)
    sell_recs = forward_metrics(panel, sell_events)
    strict_recs = forward_metrics(panel, strict_events)
    out = {"version": "bs_zone_event_study_v1.1", "asof": "20260928(qfq库最新)", "event_from": EVENT_FROM,
           "population": "低位强池(pos250<=50,close>MA20>MA60,距年低<=35%,60日内放量大阳,价<=30,剔科创/北交)",
           "resistance_population": "全市场(剔科创/北交,价<=30),60日前高压力带",
           "regime_def": "上证收盘>MA20=BULL,否则BEAR(vipdoc sh000001,事件日取前一交易日)",
           "stop_rule": "t20e=收盘较入场-8%止损否则T+20收盘",
           "caveats": ["ST未滤", "一字板近似未滤", "启动口径为扫描器近似(无缩量/跌25%细筛)", "压力带事件含涨跌停摩擦近似"],
           "zones": {}, "sell_zones": {}, "regime_split": {},
           "strict_population_check": {
               z: {"broad": {m: summarize([r[m] for r in recs.get(z, [])]) for m in ("fwd5", "fwd20", "t20e")},
                   "strict": {m: summarize([r[m] for r in strict_recs.get(z, [])]) for m in ("fwd5", "fwd20", "t20e")},
                   "strict_by_year": {y: {m: summarize([r[m] for r in strict_recs.get(z, []) if r["year"] == y])
                                          for m in ("fwd5", "fwd20", "t20e")}
                                      for y in sorted({r["year"] for r in strict_recs.get(z, [])})}}
               for z in ("Z1_confirm_window", "Z2_shrink_vol")},
           "z2s_strict_events": [{"code": e[1], "date": e[2]} for e in strict_events if e[0] == "Z2_shrink_vol"],
           "strict_population_note": "严格口径=泛强池∧近40日有启动事件(放量大阳∧前60日跌≥25%∧启动前缩量≤0.95)，近似复刻扫描器细筛(三年分位/LOW_VOL cap略去)"}
    for zone in ZONES + ("Z2_shrink_vol", "Z2_norm_vol", "Z2_heavy_vol"):
        rs = recs.get(zone, [])
        z = {"total_n": len(rs),
             "all": {k: summarize([r[k] for r in rs]) for k in ("fwd5", "fwd10", "fwd20", "t20e")},
             "by_year": {}}
        for y in sorted({r["year"] for r in rs}):
            ry = [r for r in rs if r["year"] == y]
            z["by_year"][y] = {k: summarize([r[k] for r in ry]) for k in ("fwd5", "fwd20", "t20e")}
        # v1.1 市况分层
        split = {"BULL": [], "BEAR": []}
        for r in rs:
            b = regime_at(r["date"])
            if b is not None:
                split["BULL" if b else "BEAR"].append(r)
        for k, rr in split.items():
            z.setdefault("regime", {})[k] = {m: summarize([r[m] for r in rr]) for m in ("fwd5", "fwd20", "t20e")}
        out["zones"][zone] = z
        out["regime_split"][zone] = z["regime"]
    for zone in SELL_ZONES:
        rs = sell_recs.get(zone, [])
        out["sell_zones"][zone] = {"total_n": len(rs),
                                   "all": {k: summarize([r[k] for r in rs]) for k in ("fwd5", "fwd10", "fwd20", "t20e")}}
    path = r"D:/zcodesj/data/bs_zone_event_study_20260929.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[BSZONE-STUDY] buy_events={len(events)} sell_events={len(sell_events)} strict_events={len(strict_events)} -> {path}")
    sp = out["strict_population_check"]
    for z in ("Z1_confirm_window", "Z2_shrink_vol"):
        b, s = sp[z]["broad"]["t20e"], sp[z]["strict"]["t20e"]
        print(f"[STRICT-CHECK] {z}: broad n={b.get('n')} t20e={b.get('mean')}%(win{b.get('win')},med{b.get('med')})"
              f" | strict n={s.get('n')} t20e={s.get('mean')}%(win{s.get('win')},med{s.get('med')})")
    for zone in ZONES + ("Z2_shrink_vol", "Z2_norm_vol", "Z2_heavy_vol"):
        z = out["zones"][zone]
        a = z["all"]
        print(f"{ZONE_CN[zone]:<26} n={z['total_n']:>6} fwd5={a['fwd5'].get('mean')}%(win{a['fwd5'].get('win')})"
              f" fwd20={a['fwd20'].get('mean')}% t20e={a['t20e'].get('mean')}%(win{a['t20e'].get('win')},med{a['t20e'].get('med')})")
        for rk, rv in z["regime"].items():
            print(f"    {rk}: n={rv['t20e'].get('n')} t20e={rv['t20e'].get('mean')}%(win{rv['t20e'].get('win')},med{rv['t20e'].get('med')}) fwd5={rv['fwd5'].get('mean')}%")
    for zone in SELL_ZONES:
        z = out["sell_zones"][zone]
        a = z["all"]
        print(f"{ZONE_CN[zone]:<26} n={z['total_n']:>6} fwd5={a['fwd5'].get('mean')}%(win{a['fwd5'].get('win')})"
              f" fwd20={a['fwd20'].get('mean')}%(win{a['fwd20'].get('win')}) t20e={a['t20e'].get('mean')}%(med{a['t20e'].get('med')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
