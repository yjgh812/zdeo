r"""portrait_dtw_d1_20261116.py — 画像系统·DTW阶段检索标定 E-DTW-1（PREREG_PORTRAIT_DTW_D1_V1，指纹2bc89a1f42bddac1）。

两目标(上证最近30日/600519最近30日)对全市场历史30日窗口库(约733万)检索：粗筛Euclidean Top-500
→ 精配自实现DTW(30x30全带宽)Top-50 → 后续20日收益分布+era分布。纯描述零预测声称, 零交易权。
用法: python -I portrait_dtw_d1_20261116.py [run2]
"""
import hashlib
import heapq
import itertools
import json
import math
import sqlite3
import sys
import time

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC_DB = r"D:\zcodesj\data\universe_portrait_fullseries.db"
PREREG = r"D:\zcodesj\data\PREREG_PORTRAIT_DTW_D1_V1_20261116.json"
OUT1 = r"D:\zcodesj\reports\PORTRAIT_DTW_D1_20261116.json"
OUT2 = OUT1.replace(".json", "_run2.json")
WIN = 30
COARSE_K = 500
TOP_K = 50
FWD = 20


def dtw_dist(a, b):
    """自实现DTW, 30x30全带宽DP。a,b=等长float序列。"""
    n = len(a)
    D = np.full((n + 1, n + 1), np.inf)
    D[0, 0] = 0.0
    for i in range(1, n + 1):
        ai = a[i - 1]
        for j in range(1, n + 1):
            c = abs(ai - b[j - 1])
            D[i, j] = c + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    return D[n, n]


def norm01(x):
    x = np.asarray(x, dtype=np.float64)
    lo = x.min()
    hi = x.max()
    rng = hi - lo
    if rng <= 0:
        return None
    return (x - lo) / rng


def build_windows(closes):
    """closes=行序close列表。返回[(row_idx, vec)]，30行连续且close>0且归一化有效。"""
    out = []
    n = len(closes)
    for i in range(WIN - 1, n):
        w = closes[i - WIN + 1:i + 1]
        if any(c is None or c <= 0 for c in w):
            continue
        v = norm01(w)
        if v is None:
            continue
        out.append((i, v))
    return out


def coarse_scan(target_vec, con, leak_code=None, leak_date=None):
    """分块扫全库窗口, Euclidean粗筛Top-500。返回[(dist, code, end_row, end_date)]。"""
    heap = []
    nwin = 0
    nflat = 0
    cur = con.execute("SELECT code,date,close FROM stock_daily ORDER BY code,date")
    import itertools
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        rows = list(grp)
        dates = [int(r[1]) for r in rows]
        closes = [r[2] for r in rows]
        wins = build_windows(closes)
        if not wins:
            nflat += 0
            continue
        nwin += len(wins)
        for (i, v) in wins:
            d_end = dates[i]
            if leak_code is not None and code == leak_code and d_end >= leak_date:
                continue
            d = float(np.sqrt(((v - target_vec) ** 2).sum()))
            item = (d, code, i, d_end)
            if len(heap) < COARSE_K:
                heapq.heappush(heap, (-d, code, i, d_end))
            elif -d > heap[0][0]:
                heapq.heapreplace(heap, (-d, code, i, d_end))
    cand = sorted([(-nd[0], nd[1], nd[2], nd[3]) for nd in heap])
    return cand, nwin, nflat


def run_target(tname, t_vec, con, leak_code=None, leak_date=None):
    t0 = time.time()
    cand, nwin, nflat = coarse_scan(t_vec, con, leak_code=leak_code, leak_date=leak_date)
    print("[%s] 库窗口=%d 粗筛Top%d→DTW精配Top%d" % (tname, nwin, COARSE_K, TOP_K))
    codes = sorted(set(c[1] for c in cand))
    qmarks = ",".join("?" * len(codes))
    rows = con.execute("SELECT code,date,close FROM stock_daily WHERE code IN (%s) ORDER BY code,date" % qmarks, codes).fetchall()
    by_code = {}
    for code, grp in itertools.groupby(rows, key=lambda r: r[0]):
        by_code[code] = list(grp)
    refined = []
    for (d_e, code, i_end, d_end) in cand:
        cls = [r[2] for r in by_code[code]]
        w = cls[i_end - WIN + 1:i_end + 1]
        v = norm01(w)
        if v is None:
            continue
        dw = dtw_dist(t_vec, v)
        refined.append((dw, d_e, code, i_end, d_end))
    refined.sort()
    top = refined[:TOP_K]
    result = {"n_lib_windows": nwin, "coarse_k": COARSE_K, "top_k": TOP_K,
              "top": [], "stats": {}, "era": {}}
    f20s = []
    for (dw, d_e, code, i_end, d_end) in top:
        cls = [r[2] for r in by_code[code]]
        j = i_end + FWD
        trunc = False
        if j >= len(cls):
            j = len(cls) - 1
            trunc = True
        while j > i_end and (cls[j] is None or cls[j] <= 0):
            j -= 1
        if j <= i_end:
            f20 = None
        else:
            f20 = cls[j] / cls[i_end] - 1.0
        f20s.append(f20)
        result["top"].append({"code": code, "end_date": d_end,
                              "dtw": round(dw, 4),
                              "fwd20_pct": None if f20 is None else round(f20 * 100.0, 4),
                              "truncated": trunc})
    vals = [x for x in f20s if x is not None]
    n_v = len(vals)
    if n_v:
        s = sorted(vals)
        result["stats"] = {"n_valid": n_v,
                           "mean_pct": round(sum(vals) / n_v * 100.0, 4),
                           "median_pct": round(pctile(s, 50) * 100.0, 4),
                           "p25_pct": round(pctile(s, 25) * 100.0, 4),
                           "p75_pct": round(pctile(s, 75) * 100.0, 4),
                           "share_gt0_pct": round(sum(1 for x in vals if x > 0) / n_v * 100.0, 2),
                           "share_ge10_pct": round(sum(1 for x in vals if x >= 0.10) / n_v * 100.0, 2),
                           "share_le_n10_pct": round(sum(1 for x in vals if x <= -0.10) / n_v * 100.0, 2)}
        for item in result["top"]:
            if item["fwd20_pct"] is not None:
                yr = str(item["end_date"])[:4]
                result["era"][yr] = result["era"].get(yr, 0) + 1
    print("[%s] Top%d后续20日: %s" % (tname, len(top), result["stats"]))
    print("[%s] era分布: %s" % (tname, result["era"]))
    print("[%s] 耗时%.0fs" % (tname, time.time() - t0))
    return result


def pctile(xs, q):
    n = len(xs)
    if n == 0:
        return None
    if n == 1:
        return xs[0]
    pos = (n - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    f = pos - lo
    return xs[lo] + (xs[hi] - xs[lo]) * f


def main():
    t0 = time.time()
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace((' "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b' "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = (fp == prereg["fingerprint"])
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))
    con = sqlite3.connect("file:%s?mode=ro" % SRC_DB, uri=True)
    # T1 = 上证指数最后30个交易日
    rows = con.execute("SELECT date, idx_sh_c FROM market_daily WHERE idx_sh_c IS NOT NULL "
                       "ORDER BY date DESC LIMIT 30").fetchall()
    rows.reverse()
    t1 = norm01([float(r[1]) for r in rows])
    t1_end = int(rows[-1][0])
    # T2 = 600519 最后30行close>0
    rows2 = con.execute("SELECT date, close FROM stock_daily WHERE code=600519 AND close>0 "
                        "ORDER BY date DESC LIMIT 30").fetchall()
    rows2.reverse()
    t2 = norm01([float(r[1]) for r in rows2])
    t2_end = int(rows2[-1][0])
    print("目标: T1=上证至%s / T2=600519至%s" % (t1_end, t2_end))
    r1 = run_target("T1_上证", t1, con)
    r2 = run_target("T2_600519", t2, con, leak_code=600519, leak_date=t2_end)
    con.close()
    n_ok = sum(1 for r in (r1, r2) if r["stats"].get("n_valid", 0) >= 40)
    verdict = "RETRIEVAL_OK" if n_ok == 2 else "DEGENERATE"
    out = {"prereg_fp": fp, "fp_ok": fp_ok,
           "targets": {"T1_sh": r1, "T2_600519": r2},
           "verdict": verdict}
    path = OUT2 if (len(sys.argv) > 1 and sys.argv[1] == "run2") else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("判定=%s 已写入 %s %.0fs" % (verdict, path, time.time() - t0))


if __name__ == "__main__":
    main()
