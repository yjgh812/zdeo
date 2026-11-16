# -*- coding: utf-8 -*-
"""右侧启动买点全史事件研究 (2026-10-11, 主人令: A股全宇宙2012至今+画像特征+波段结构统计)

信号真源 = universe_startup_scan.py 段起漏斗(主人9/23四维法: 低位+启动确认+深跌+缩量+排雷),
本件=该买点的全史事件级统计+波段结构, 非组合回放(U系列已做)。

prereg口径(冻结, 无前视):
  段起日s = 250日窗最低点之后首个放量大阳(量>=2×含当日20日均量 且 涨>=3%),
            l0..s-1 无更早段起(首段起复刻scanner倒序break), 前60根(>=40根才判)跌>=25%,
            前20根均量/250根均量 <= 0.95(LOW_VOL历史态不可得, 用实弹历史回放臂同款0.95固定)。
  介入日D = s..s+40 内首个合格扫描日: 多头排列MA20>MA60, pos250(D)<50, 价格2-30(qfq),
            off_start=close_D/close_s-1 ∈[-3%,+17%], pos3(D,正向750窗)<55, off_lo3<=80%,
            排雷(量位vrank<80, 乖离bias20<=3; S5/S6与低位语义互斥自动失效)。
  入场 = D+1开盘(next-bar); 出场 = 收盘锚f5/f10/f20/f60/f120 + 净口径-0.45%费
        (佣金0.025%双边+印花0.05%卖+滑点0.2%双边, 与生产引擎config对齐)。
  主样本 = 每段起首个合格D; lag样本 = 每段起全部合格D(介入时机分层)。
波段结构(主人令第二问):
  入场后逐日追踪250交易日, 峰值=入场后收盘最高; 走坏确认多口径并列(机械,非提请制):
    brk20d=收盘跌破MA20且MA20较3日前向下(主口径, 贴"趋势走坏"语义)
    brk20=收盘跌破MA20 / brk60=收盘跌破MA60 / dd15/dd20=收盘自峰值回撤15%/20% / stop8=收盘≤0.92×入场
  每口径记录: 确认日/相对入场涨幅/自入场天数/峰值→确认回吐。
宇宙: 60/00/30开头(剔688/689/8/4/9科创北交所B股), 现名剔ST/退(历史ST态不可得=近似,如实标注);
      含退市股(幸存者偏差修正), zt_gene: 2020+用涨停池, 2012-2019日线近似(分列标注)。
画像: pos60/posband 按 analysis/portrait_pit_dims_backfill.py 逐位同源重算(全史),
      与 data/portrait_pit_dims_daily.db 抽样对账; 人气/主线历史不可得=N/A。
对账: ①2016+段与U系列事件级(n=17118,+5.64% h60)量级对照 ②与9/23以来实弹名单交叉 ③抽样20事件人工复核。
用法: python analysis/rightside_startup_event_study_20261011.py   (全量~5-10分钟, 输出reports/)
"""
import os, sys, json, time, random
import numpy as np

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from stock_name_lookup import get_name

DB_QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DB_PIT = r"file:D:/zcodesj/data/portrait_pit_dims_daily.db?mode=ro"
DB_ZT = r"file:D:/zcodesj/data/zt_pool_history.db?mode=ro"
OUTDIR = os.path.join(ROOT, "reports", "rightside_startup_20261011")
os.makedirs(OUTDIR, exist_ok=True)
WARM_FROM, EV_FROM = "20100101", 2012  # EV_FROM=事件起始年(dt//10000=YYYY)
FEE_NET = 0.0045          # 单回合合计费率
STOP8 = 0.92
TRACK_BARS = 250          # 波段追踪窗
EXCLUDE_PREFIX = ("688", "689", "8", "4", "9")


def log(*a):
    print(*a, flush=True)


def band_pos60(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    if v <= 25:
        return "深位"
    if v <= 40:
        return "灰带"
    if v <= 80:
        return "中带"
    return "高区"


# ================================================= 涨停池近10日命中 (2020+)
def load_zt_set():
    import sqlite3
    db = sqlite3.connect(DB_ZT, uri=True)
    rows = db.execute("SELECT date, code FROM zt_pool").fetchall()
    db.close()
    return {(d, c) for d, c in rows}


# 画像对账: 正查(事件2020+段 → pit_dims逐条比对)
def pit_check(events):
    import sqlite3, random
    ev20 = [e for e in events if e["d_date"] >= 20200103 and e["pos60"] is not None]
    random.seed(42)
    sample = random.sample(ev20, min(1500, len(ev20)))
    db = sqlite3.connect(DB_PIT, uri=True)
    hit, diffs, miss = 0, [], 0
    for ev in sample:
        r = db.execute("SELECT pos60 FROM pit_dims WHERE code=? AND date=?",
                       (ev["code"], ev["d_date"])).fetchone()
        if r is None:
            miss += 1
            continue
        hit += 1
        diffs.append(abs(r[0] - ev["pos60"]))
    db.close()
    return hit, miss, diffs


# ================================================= 主扫描
def main():
    import sqlite3
    t0 = time.time()
    zt_set = load_zt_set()
    log(f"[1] 涨停池加载 {len(zt_set):,} 条 {time.time()-t0:.0f}s")

    # 宇宙: 全库代码 - 科创北交所B股 - 现名ST/退
    db = sqlite3.connect(DB_QFQ, uri=True)
    codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq ORDER BY code")]
    universe = []
    for c in codes:
        if c[:3] in EXCLUDE_PREFIX or (c[:1] in ("4", "8", "9") and not c[:3] == "300"):
            continue
        nm = get_name(c) or ""
        if "ST" in nm.upper() or "退" in nm:
            continue
        universe.append((c, nm))
    log(f"[2] 宇宙 {len(universe):,} / 全库 {len(codes):,} 只 {time.time()-t0:.0f}s")

    # 逐股流式(全史, 不截断2010 — 三年窗750在老票2010起index<749段需全史语义, 全量复核错15/pos3失配根因)
    cur = db.execute(
        "SELECT code,date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq "
        "ORDER BY code,date")
    events, lag_rows = [], []
    cur_code, buf = None, []
    n_stock_done = 0

    def flush(code, nm, buf):
        n = len(buf)
        if n < 400:
            return
        # buf元组=(date,open,high,low,close,volume,amount) 7项[0..6] — TRAPS['qfq列索引']
        dt = np.array([int(r[0]) for r in buf])
        o = np.array([float(r[1] or 0) for r in buf])
        c = np.array([float(r[4] or 0) for r in buf])
        v = np.array([float(r[5] or 0) for r in buf])
        a = np.array([float(r[6] or 0) for r in buf])
        if (c <= 0).any():
            ok = c > 0
            # 早期脏数据(0价)截断到首段有效区
            first = int(np.argmax(ok))
            if not ok[-1]:
                return
            dt, o, c, v, a = (x[first:] for x in (dt, o, c, v, a))
            n = len(c)
            if n < 400:
                return
        pct = np.zeros(n)
        pct[1:] = c[1:] / c[:-1] - 1.0

        cs = np.concatenate([[0.0], np.cumsum(c)])
        vs = np.concatenate([[0.0], np.cumsum(v)])

        def sma(arr, w, pad):
            d = (arr[w:] - arr[:-w]) / w
            return np.concatenate([np.full(pad, np.nan), d])

        ma20, ma60 = sma(cs, 20, 19), sma(cs, 60, 59)
        v20 = sma(vs, 20, 19)            # 含当日20根均量
        v250 = sma(vs, 250, 249)         # 含当日250根均量
        W = np.lib.stride_tricks.sliding_window_view(c, 250)
        lo250 = np.concatenate([np.full(249, np.nan), W.min(1)])
        hi250 = np.concatenate([np.full(249, np.nan), W.max(1)])
        rng = np.where(hi250 - lo250 > 0, hi250 - lo250, np.nan)
        pos250 = 100.0 * (c - lo250) / rng
        lo_rel = W.argmin(1)     # 行内偏移(从窗头数, ties取靠前=scanner原版list.index语义)
        lo_day = np.concatenate([np.full(249, -1), np.arange(249, n) - 249 + lo_rel])
        W750 = np.lib.stride_tricks.sliding_window_view(c, 750) if n >= 750 else None
        fa, fb = np.maximum.accumulate(c), np.minimum.accumulate(c)  # 正向累积=无前视
        if W750 is not None:
            hi3 = np.concatenate([np.full(749, np.nan), W750.max(1)])
            lo3 = np.concatenate([np.full(749, np.nan), W750.min(1)])
            # 不足750根段回填全部历史累积(scanner"序列不足用全部"语义; 漏回填=全量复核揪出的1853误杀根因)
            hi3 = np.where(np.isnan(hi3), fa, hi3)
            lo3 = np.where(np.isnan(lo3), fb, lo3)
        else:
            hi3, lo3 = fa, fb
        rng3 = np.where(hi3 - lo3 > 0, hi3 - lo3, np.nan)
        pos3 = 100.0 * (c - lo3) / rng3
        off_lo3 = (c / np.where(lo3 > 0, lo3, np.nan) - 1) * 100

        cand = (pct >= 0.03) & (v >= 2 * v20) & (v20 > 0)
        cand &= ~np.isnan(pos250)
        cand_basic = (pct >= 0.03) & (v >= 2 * v20) & (v20 > 0)  # earlier首段起检查用(scanner原版无pos250条件;
        #  全量复核错160根因: 上市初期<250根内的更早段起pos250=NaN, 若过滤会使窗内最低点落在初期时误判首段起)
        S = np.where(cand)[0]
        if len(S) == 0:
            return
        board300 = code[:3] in ("300", "301")
        zt_thr = 0.198 if board300 else 0.098

        # 介入日预掩码
        with np.errstate(invalid="ignore"):
            vrank = np.full(n, np.nan)
            V = np.lib.stride_tricks.sliding_window_view(v, 250)
            vrank[249:] = 100.0 * (V <= V[:, -1:]).mean(1)
            bias20 = (c / ma20 - 1) * 100
        elig = np.zeros(n, bool)
        el = ((np.arange(n) >= 249) & (pos250 < 50) & (c >= 2) & (c <= 30)
              & (bias20 <= 3) & (vrank < 80) & (pos3 < 55) & (off_lo3 <= 80)
              & (ma20 > ma60) & ~np.isnan(vrank))
        elig[:] = el
        # 波段追踪用指标
        ma20_d = ma20

        for s in S:
            if dt[s] // 10000 < EV_FROM:
                continue
            # 首段起复刻: 250窗最低点到s-1之间无更早段起(cand_basic, scanner原版语义)
            l0 = lo_day[s]
            if l0 < 0:
                continue
            if cand_basic[l0:s].any():
                continue
            # 深跌+缩量门(前60根>=40才判, 否则豁免=scanner语义)
            pre60 = c[max(0, s - 60):s]
            if len(pre60) >= 40:
                if pre60.min() / pre60.max() - 1 > -0.25:
                    continue
                pre20 = v[max(0, s - 20):s].mean()
                v250s = v250[s] if not np.isnan(v250[s]) else v.mean()
                if v250s <= 0 or pre20 / v250s > 0.95:
                    continue
            pre_dd_v = (pre60.min() / pre60.max() - 1) * 100 if len(pre60) >= 40 else 0.0
            pre_sh_v = (v[max(0, s - 20):s].mean() / (v250[s] if not np.isnan(v250[s]) and v250[s] > 0 else v.mean())) if len(pre60) >= 40 else 1.0
            vr_s = v[s] / v20[s] if v20[s] > 0 else np.nan
            pct_s = pct[s] * 100

            # 介入日
            hit = np.where(elig[s:s + 41])[0]
            if len(hit) == 0:
                continue
            d_events = []
            for d0 in hit:
                d = s + int(d0)
                if d + 1 >= n or o[d + 1] <= 0:
                    continue
                off_start = (c[d] / c[s] - 1) * 100
                if off_start > 17 or off_start < -3:   # scanner实弹同款距段起窗
                    continue
                d_events.append((d, off_start))
            if not d_events:
                continue

            zt10 = 0
            for i in range(max(0, d_events[0][0] - 9), d_events[0][0] + 1):
                if pct[i] >= zt_thr:
                    zt10 += 1
            consec = 0
            for i in range(d_events[0][0], 0, -1):
                if pct[i] > 0:
                    consec += 1
                else:
                    break

            for k, (d, off_start) in enumerate(d_events):
                e = o[d + 1]
                dtp = int(dt[d])
                pos60_v = pos250_d = None
                W60lo = c[max(0, d - 59):d + 1]
                lo60, hi60 = W60lo.min(), W60lo.max()
                if len(W60lo) >= 20 and hi60 > lo60:
                    pos60_v = 100.0 * (c[d] - lo60) / (hi60 - lo60)
                # 收益(收盘锚)
                fwd = {}
                for kk in (5, 10, 20, 60, 120):
                    j = d + kk
                    fwd[kk] = (c[j] / e - 1) * 100 if j < n else None
                # 波段追踪(仅主样本k==0, 250交易日)
                wave = None
                if k == 0:
                    end = min(d + 1 + TRACK_BARS, n)
                    ent_i = d + 1
                    seg_c = c[ent_i:end]
                    seg_o = o[ent_i:end]
                    m20 = ma20_d[ent_i:end]
                    m60 = ma60[ent_i:end]
                    peak = -1.0
                    peak_i = -1
                    res = {}
                    for name in ("brk20d", "brk20", "brk60", "dd15", "dd20", "stop8"):
                        res[name] = None
                    for i in range(len(seg_c)):
                        ci = seg_c[i]
                        if ci > peak:
                            peak, peak_i = ci, i
                        gi = ent_i + i
                        if res["stop8"] is None and ci <= STOP8 * e:
                            res["stop8"] = (gi, (ci / e - 1) * 100)
                        if res["dd20"] is None and peak > 0 and ci <= 0.80 * peak:
                            res["dd20"] = (gi, (ci / e - 1) * 100)
                        if res["dd15"] is None and peak > 0 and ci <= 0.85 * peak:
                            res["dd15"] = (gi, (ci / e - 1) * 100)
                        if res["brk20"] is None and not np.isnan(m20[i]) and ci < m20[i]:
                            res["brk20"] = (gi, (ci / e - 1) * 100)
                        if res["brk20d"] is None and not np.isnan(m20[i]) and ci < m20[i] \
                                and i >= 3 and m20[i] < m20[i - 3]:
                            res["brk20d"] = (gi, (ci / e - 1) * 100)
                        if res["brk60"] is None and not np.isnan(m60[i]) and ci < m60[i]:
                            res["brk60"] = (gi, (ci / e - 1) * 100)
                    wave = {
                        # 注: 不放entry键 — base合并循环对float统一round2, 会把entry精度压到2位
                        "max_gain": (peak / e - 1) * 100,
                        "peak_day": peak_i,   # 相对入场第几交易日(0=入场当日收盘)
                        "wave_len": len(seg_c) - 1,
                    }
                    for name, r in res.items():
                        if r is None:
                            wave[name + "_day"] = wave[name + "_ret"] = None
                        else:
                            gi, ret = r
                            wave[name + "_day"] = gi - ent_i
                            wave[name + "_ret"] = ret
                            wave[name + "_dd_from_peak"] = (c[gi] / peak - 1) * 100 if peak > 0 else None
                    # 峰值后首次破MA20向下(区分启动初期洗盘破位与波段顶后走坏)
                    if peak_i >= 0:
                        found = None
                        for i in range(peak_i, len(seg_c)):
                            gi = ent_i + i
                            if not np.isnan(m20[i]) and seg_c[i] < m20[i] and i >= 3 and m20[i] < m20[i - 3]:
                                found = (gi - ent_i, (seg_c[i] / e - 1) * 100, (seg_c[i] / peak - 1) * 100)
                                break
                        wave["pk_brk20d_day"] = found[0] if found else None
                        wave["pk_brk20d_ret"] = round(found[1], 2) if found else None
                        wave["pk_brk20d_dd"] = round(found[2], 2) if found else None
                    else:
                        wave["pk_brk20d_day"] = wave["pk_brk20d_ret"] = wave["pk_brk20d_dd"] = None
                base = {
                    "code": code, "name": nm, "d_date": dtp, "s_date": int(dt[s]),
                    "days": d - s, "entry": round(e, 3),
                    "px_D": round(c[d], 3), "off_start": round(off_start, 1),
                    "vr_s": round(vr_s, 2), "pct_s": round(pct_s, 2),
                    "pre_dd": round(pre_dd_v, 1), "pre_shrink": round(pre_sh_v, 2),
                    "pos250": round(pos250[d], 1), "pos3": round(pos3[d], 1),
                    "off_lo3": round(off_lo3[d], 1),
                    "pos60": None if pos60_v is None else round(pos60_v, 1),
                    "posband": band_pos60(pos60_v),
                    "bias20": round(bias20[d], 2), "bias60": round((c[d] / ma60[d] - 1) * 100, 2),
                    "vrank": round(vrank[d], 1), "amount_wy": round(a[d]),
                    "zt10": zt10, "consec_up": consec, "pct_D": round(pct[d] * 100, 2),
                    "amount_mv": round(a[d] / 1e8, 3),  # 成交额(亿元, 流动性代理, 无换手)
                }
                for kk in (5, 10, 20, 60, 120):
                    base[f"f{kk}"] = None if fwd[kk] is None else round(fwd[kk], 2)
                    base[f"f{kk}_net"] = None if fwd[kk] is None else round(fwd[kk] - FEE_NET * 100, 2)
                if wave:
                    for kk2, vv in wave.items():
                        if isinstance(vv, float):
                            base[kk2] = round(vv, 2)
                        else:
                            base[kk2] = vv
                if k == 0:
                    events.append(base)
                else:
                    lag_rows.append(base)

    import itertools
    LIMIT = int(os.environ.get("SMOKE_LIMIT", "0") or 0)  # 冒烟: 只处理前N股
    while True:
        chunk = cur.fetchmany(400_000)
        if not chunk:
            break
        for code, date_, o_, h_, l_, cl_, v_, a_ in chunk:
            if code != cur_code:
                if cur_code is not None:
                    idx = next((i for i, (cc, _) in enumerate(universe) if cc == cur_code), None)
                    if idx is not None:
                        flush(cur_code, universe[idx][1], buf)
                        n_stock_done += 1
                        if LIMIT and n_stock_done >= LIMIT:
                            cur_code = None
                            break
                    if n_stock_done % 800 == 0:
                        log(f"  ... {n_stock_done:,} 股, 事件 {len(events):,}, lag {len(lag_rows):,} {time.time()-t0:.0f}s")
                cur_code, buf = code, []
            buf.append((date_, o_, h_, l_, cl_, v_, a_))
        if LIMIT and n_stock_done >= LIMIT:
            break
    if cur_code is not None:
        idx = next((i for i, (cc, _) in enumerate(universe) if cc == cur_code), None)
        if idx is not None:
            flush(cur_code, universe[idx][1], buf)

    log(f"[3] 扫描完成: 主事件 {len(events):,}, lag样本 {len(lag_rows):,} 只耗时 {time.time()-t0:.0f}s")

    # 画像对账(正查)
    hit, miss, diffs = pit_check(events)
    log(f"[4] 画像对账: 命中 {hit}/{hit+miss}(缺失{miss}), pos60最大差 {max(diffs) if diffs else 'N/A'}")

    # 输出
    import csv
    cols_main = None
    with open(os.path.join(OUTDIR, "events_main.csv"), "w", newline="", encoding="utf-8-sig") as f:
        if events:
            cols_main = list(events[0].keys())
            w = csv.DictWriter(f, fieldnames=cols_main)
            w.writeheader()
            w.writerows(events)
    with open(os.path.join(OUTDIR, "events_lag.csv"), "w", newline="", encoding="utf-8-sig") as f:
        if lag_rows:
            w = csv.DictWriter(f, fieldnames=list(lag_rows[0].keys()))
            w.writeheader()
            w.writerows(lag_rows)
    json.dump({"n_main": len(events), "n_lag": len(lag_rows),
               "pit_check": {"hit": hit, "miss": miss,
                             "max_diff": max(diffs) if diffs else None},
               "elapsed": round(time.time() - t0)}, open(os.path.join(OUTDIR, "run_meta.json"), "w"))
    log(f"[5] 输出完成 {OUTDIR} 总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
