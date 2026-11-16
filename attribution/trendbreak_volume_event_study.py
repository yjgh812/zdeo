r"""trendbreak_volume_event_study.py — 走坏判定量能层事件研究（PREREG_TRENDBREAK_VOLUME_V1，指纹83c84e2bced24ae5）。

问题: trend_checker v2.2 走坏卖出时点上, 破位首日量比能否区分卖飞。
层: L1缩量<1.0 / L2正常[1.0,1.5) / L3放量≥1.5。卖出口径照搬系统A/B分流(A组c+1开盘卖,
B组c+1收盘收回则不卖, 否则c+2开盘卖)。ret20=卖出后第20个交易日收盘/执行价-1。
事件研究不计费用。零交易权。
用法: python -I trendbreak_volume_event_study.py [run2]
判定逻辑严格按预注册冻结定义实现, 禁止事后调参。
"""
import hashlib
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict, deque
from itertools import groupby

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ST_DB = r"D:\zcodesj\data\st_history.db"
PREREG = r"D:\zcodesj\data\PREREG_TRENDBREAK_VOLUME_V1_20261116.json"
START_DATA, SIG_END = "20100101", "20261113"
SPLIT = 20160000
EXCL = ("4", "8", "9", "68")
DEPTH = 0.01       # 破位深度阈值
GAP_A = -0.03      # A组距离阈值
SLOPE_A = -0.05    # A组MA20五日斜率阈值
VOL_LO, VOL_HI = 1.0, 1.5   # 层边界
FWD = 20           # 卖出后第20个交易日(索引X+20)
MIN_BARS = 60      # 确认日前须已有bar数
CROSS_WIN = 10     # 破位首日回看穿越窗
OUT1 = r"D:\zcodesj\reports\TRENDBREAK_VOLUME_EVENT_STUDY_20261116.json"
OUT2 = OUT1.replace(".json", "_run2.json")
LAYERS = ("L1", "L2", "L3")


def st_loader():
    con = sqlite3.connect("file:%s?mode=ro" % ST_DB, uri=True)
    cache = {}

    def state(code, date):
        if code not in cache:
            pfx = "sh" if code.startswith("6") else "sz"
            rows = con.execute("SELECT date,is_st FROM st_daily WHERE code=? ORDER BY date",
                               (pfx + "." + code,)).fetchall()
            cache[code] = rows
        rows = cache[code]
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] <= date:
                lo = mid + 1
            else:
                hi = mid
        return rows[lo - 1][1] if lo > 0 else 0
    return state


def ma_series(closes, k):
    """滚动均值, 窗口内任一收盘缺失/非正→None。O(n)滑窗实现。"""
    out = [None] * len(closes)
    buf = deque()
    s = 0.0
    nbad = 0
    for i, c in enumerate(closes):
        bad = c is None or c <= 0
        buf.append(bad)
        if bad:
            nbad += 1
        else:
            s += c
        if len(buf) > k:
            if buf.popleft():
                nbad -= 1
            else:
                s -= closes[i - k]
        if i >= k - 1 and nbad == 0:
            out[i] = s / k
    return out


def run_stock(code, bars, is_st, evs):
    """单股事件扫描, 返回剔除/分支计数。"""
    n = len(bars)
    cnt = {"n": 0, "recovered": 0, "vol_missing": 0, "exec_missing": 0,
           "trunc_no_fwd": 0, "st_excluded": 0, "truncated": 0}
    if n < MIN_BARS:
        return cnt
    dates = [b[0] for b in bars]
    opens = [b[2] for b in bars]
    closes = [b[3] for b in bars]
    vols = [b[4] for b in bars]
    ma5 = ma_series(closes, 5)
    ma10 = ma_series(closes, 10)
    ma20 = ma_series(closes, 20)
    bear = [False] * n
    for i in range(n):
        c = closes[i]
        m5, m10, m20v = ma5[i], ma10[i], ma20[i]
        if c is None or c <= 0 or m20v is None or m5 is None or m10 is None:
            continue
        depth = (m20v - c) / m20v
        if c < m20v and m5 < m10 < m20v and depth >= DEPTH:
            bear[i] = True
    suppressed = False
    events_local = []
    for i in range(1, n):
        if suppressed:
            m_r = ma20[i]
            c_r = closes[i]
            if m_r is not None and c_r is not None and c_r > 0 and c_r >= m_r:
                suppressed = False
            continue
        if i >= MIN_BARS and bear[i] and bear[i - 1]:
            events_local.append(i)
            suppressed = True
    for c in events_local:
        if is_st(code, dates[c]):
            cnt["st_excluded"] += 1
            continue
        cnt["n"] += 1
        t0 = c - 1
        for t in range(c, max(1, c - CROSS_WIN + 1) - 1, -1):
            mt = ma20[t]
            mp = ma20[t - 1]
            if (mt is not None and mp is not None
                    and closes[t] is not None and closes[t] > 0
                    and closes[t - 1] is not None and closes[t - 1] > 0
                    and closes[t - 1] >= mp and closes[t] < mt):
                t0 = t
                break
        v0 = vols[t0]
        vwin = vols[t0 - 5:t0]
        if (v0 is None or v0 <= 0 or any(x is None for x in vwin)
                or sum(vwin) <= 0):
            cnt["vol_missing"] += 1
            continue
        vr = v0 / (sum(vwin) / 5.0)
        if vr < VOL_LO:
            layer = "L1"
        elif vr < VOL_HI:
            layer = "L2"
        else:
            layer = "L3"
        m20c = ma20[c]
        gap = closes[c] / m20c - 1.0
        m20s = ma20[c - 5]
        slope5 = None if m20s is None else (m20c - m20s) / m20s
        is_a = (gap <= GAP_A) or (slope5 is not None and slope5 <= SLOPE_A)
        grp = "A" if is_a else "B"
        if is_a:
            X = c + 1
        else:
            if c + 1 >= n:
                cnt["exec_missing"] += 1
                continue
            m_n = ma20[c + 1]
            c_n = closes[c + 1]
            if m_n is not None and c_n is not None and c_n > 0 and c_n >= m_n:
                cnt["recovered"] += 1
                continue
            X = c + 2
        if X >= n:
            cnt["exec_missing"] += 1
            continue
        px = None
        o_x = opens[X]
        c_x = closes[X]
        if o_x is not None and o_x > 0:
            px = o_x
        elif c_x is not None and c_x > 0:
            px = c_x
        else:
            cnt["exec_missing"] += 1
            continue
        j = X + FWD
        if j >= n:
            j = n - 1
        truncated = (j != X + FWD) or (closes[j] is None) or (closes[j] <= 0)
        while j > X and (closes[j] is None or closes[j] <= 0):
            j -= 1
        if j <= X:
            cnt["trunc_no_fwd"] += 1
            continue
        ret = closes[j] / px - 1.0
        evs.append({
            "code": code, "confirm": dates[c], "break_day": dates[t0],
            "vol_ratio": round(vr, 4), "layer": layer, "group": grp,
            "exec_date": dates[X], "exec_price": round(px, 4),
            "ret20_pct": round(ret * 100.0, 4), "truncated": truncated,
        })
        if truncated:
            cnt["truncated"] += 1
    return cnt


def layer_stats(evs, layer, lo, hi):
    xs = [e for e in evs if e["layer"] == layer and lo <= e["confirm"] < hi]
    n = len(xs)
    if n == 0:
        return {"n": 0}
    rets = [e["ret20_pct"] for e in xs]
    mu = sum(rets) / n
    sd = (sum((x - mu) ** 2 for x in rets) / n) ** 0.5
    fly = sum(1 for x in rets if x > 5.0) / n
    return {"n": n, "mean_ret20_pct": round(mu, 4), "sd": round(sd, 4),
            "fly_rate_gt5pct": round(fly, 4),
            "truncated_n": sum(1 for e in xs if e["truncated"])}


def welch(m1, s1, n1, m3, s3, n3):
    v = s1 * s1 / n1 + s3 * s3 / n3
    if v <= 0:
        return None
    return (m1 - m3) / math.sqrt(v)


def main():
    t0t = time.time()
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace((' "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b' "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = (fp == prereg["fingerprint"])
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))

    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    is_st = st_loader()
    evs = []
    totals = {"n": 0, "recovered": 0, "vol_missing": 0, "exec_missing": 0,
              "trunc_no_fwd": 0, "st_excluded": 0, "truncated": 0}
    nstock = 0
    cur = con.execute("SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date",
                      (START_DATA, SIG_END))
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        if code.startswith(EXCL):
            continue
        bars = []
        for _, d, o, c, v in grp:
            bars.append((int(d), None, o, c, v))
        nstock += 1
        r = run_stock(code, bars, is_st, evs)
        for k in totals:
            totals[k] += r.get(k, 0)
    con.close()
    print("扫描完成 %.0fs 股票%d 事件%d" % (time.time() - t0t, nstock, totals["n"]))
    l1p = layer_stats(evs, "L1", SPLIT, 20999999)
    l2p = layer_stats(evs, "L2", SPLIT, 20999999)
    l3p = layer_stats(evs, "L3", SPLIT, 20999999)
    h1 = layer_stats(evs, "L1", 20100000, SPLIT)
    h2 = layer_stats(evs, "L2", 20100000, SPLIT)
    h3 = layer_stats(evs, "L3", 20100000, SPLIT)
    if l1p["n"] == 0 or l3p["n"] == 0:
        w_t = None
        w_diff = None
    else:
        w_diff = round(l1p["mean_ret20_pct"] - l3p["mean_ret20_pct"], 4)
        w_t = round(welch(l1p["mean_ret20_pct"], l1p["sd"], l1p["n"],
                          l3p["mean_ret20_pct"], l3p["sd"], l3p["n"]), 2)
    total = l1p["n"] + l3p["n"]
    if total < 2000:
        verdict = "观察不采信(样本不足)"
    elif w_t is None:
        verdict = "观察不采信"
    elif w_t >= 2.0 and w_diff > 0:
        verdict = "PASS_candidate"
    elif w_t <= -2.0:
        verdict = "FAIL"
    else:
        verdict = "观察不采信"
    print("主窗 L1(缩量) n=%s 均值=%s%% 卖飞率=%s | L3(放量) n=%s 均值=%s%% 卖飞率=%s" % (
        l1p["n"], l1p["mean_ret20_pct"], l1p.get("fly_rate_gt5pct"),
        l3p["n"], l3p["mean_ret20_pct"], l3p.get("fly_rate_gt5pct")))
    print("主窗 L2(正常) n=%s 均值=%s%%" % (l2p["n"], l2p.get("mean_ret20_pct")))
    print("Welch(缩量-放量): diff=%s%% t=%s 总样本=%s 判定=%s" % (w_diff, w_t, total, verdict))
    ys = defaultdict(int)
    for e in evs:
        if e["confirm"] >= SPLIT:
            ys[str(e["confirm"])[:4]] += 1
    samples = {}
    for lay in LAYERS:
        samples[lay] = [e for e in evs if e["layer"] == lay and e["confirm"] >= SPLIT][:3]
    out = {"prereg_fp": fp, "fp_ok": fp_ok,
           "window": [START_DATA, SIG_END], "split": SPLIT,
           "counts": dict(totals, nstock=nstock),
           "layers_primary": {"L1": l1p, "L2": l2p, "L3": l3p},
           "welch_L1_minus_L3": {"diff_pct": w_diff, "t": w_t,
                                 "n_L1": l1p["n"], "n_L3": l3p["n"]},
           "verdict_primary": verdict,
           "layers_holdout": {"L1": h1, "L2": h2, "L3": h3},
           "years_primary": dict(sorted(ys.items())),
           "samples": samples}
    path = OUT2 if (len(sys.argv) > 1 and sys.argv[1] == "run2") else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写入", path, "%.0fs" % (time.time() - t0t))


if __name__ == "__main__":
    main()
