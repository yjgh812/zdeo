r"""portrait_hmm_e1_calibrate_20261116.py — 画像系统E层·HMM指数标定（PREREG_PORTRAIT_HMM_E1_V1，指纹134dd326edd3c5f2）。

E1=沪深300/上证四状态HMM标定：观测=[日对数收益, 20日滚动波动]标准化, GaussianHMM(4/full/200iter)
5种子取最大似然, Viterbi解码, 按状态收益/波动均值映射涨跌宽窄语义。结构判据: 涨态年化波<跌态年化波
且四态各≥5%天数且双指数通过→CALIBRATED。零交易权。
用法: python -I portrait_hmm_e1_calibrate_20261116.py [run2]
"""
import hashlib
import json
import math
import sqlite3
import sys
import time

import numpy as np
from hmmlearn.hmm import GaussianHMM
from sklearn.preprocessing import StandardScaler

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC_DB = r"D:\zcodesj\data\universe_portrait_fullseries.db"
PREREG = r"D:\zcodesj\data\PREREG_PORTRAIT_HMM_E1_V1_20261116.json"
OUT1 = r"D:\zcodesj\reports\PORTRAIT_HMM_E1_20261116.json"
OUT2 = OUT1.replace(".json", "_run2.json")
SEEDS = [42, 1, 7, 2024, 99]
ANN_D = 250.0
ANN_V = 250.0 ** 0.5


def calibrate(closes):
    """单指数四状态标定。closes=升序收盘列表。返回dict。"""
    c = np.asarray(closes, dtype=float)
    logret = np.diff(np.log(c)) * 100.0
    ret = logret[19:]
    n = len(ret)
    std20 = np.array([ret[max(0, i - 19):i + 1].std(ddof=0) for i in range(n)])
    X_raw = np.column_stack([ret, std20])
    X = StandardScaler().fit_transform(X_raw)
    best = None
    best_ll = None
    for seed in SEEDS:
        m = GaussianHMM(n_components=4, covariance_type="full", n_iter=200,
                        random_state=seed)
        m.fit(X)
        ll = m.score(X)
        if best_ll is None or ll > best_ll:
            best_ll = ll
            best = m
    states = best.predict(X)
    raw_means = best.means_[:, 0]
    order = np.argsort(raw_means)
    sem = {order[0]: "跌", order[3]: "涨"}
    rest = [order[1], order[2]]
    v0 = best.means_[rest[0], 1]
    v1 = best.means_[rest[1], 1]
    lo, hi = (rest[0], rest[1]) if v0 <= v1 else (rest[1], rest[0])
    sem[lo] = "窄幅震荡"
    sem[hi] = "宽幅震荡"
    return ret, std20, states, sem, best_ll


def state_stats(labels, ret, std20):
    """按语义标签聚合天数/占比/年化收益/年化波动/游程/自转移。"""
    out = {}
    runs = []
    cur = labels[0]
    ln = 1
    for i in range(1, len(labels)):
        if labels[i] == cur:
            ln += 1
        else:
            runs.append((cur, ln))
            cur = labels[i]
            ln = 1
    runs.append((cur, ln))
    for lab in ("涨", "跌", "宽幅震荡", "窄幅震荡"):
        mask = np.array([1 if x == lab else 0 for x in labels], dtype=bool)
        days = int(mask.sum())
        if days == 0:
            out[lab] = {"days": 0}
            continue
        sel_r = ret[mask]
        sel_v = std20[mask]
        run_lens = [L for (lb, L) in runs if lb == lab]
        same = 0
        for i in range(len(labels) - 1):
            if labels[i] == lab and labels[i + 1] == lab:
                same += 1
        out[lab] = {"days": days,
                    "share_pct": round(days / len(labels) * 100.0, 2),
                    "ann_ret_pct": round(float(sel_r.mean()) * ANN_D, 2),
                    "ann_vol_pct": round(float(sel_v.mean()) * ANN_V, 2),
                    "avg_run": round(sum(run_lens) / len(run_lens), 1),
                    "max_run": max(run_lens),
                    "self_trans": round(same / (days - 1), 4) if days > 1 else None}
    return out, runs


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
    idx_map = {"sh": "idx_sh_c", "hs300": "idx_hs300_c"}
    results = {}
    for key, col in idx_map.items():
        rows = con.execute("SELECT date, %s FROM market_daily WHERE %s IS NOT NULL "
                           "ORDER BY date" % (col, col)).fetchall()
        dates = [int(r[0]) for r in rows]
        closes = [float(r[1]) for r in rows]
        ret, std20, states, sem, best_ll = calibrate(closes)
        labels = [sem[s] for s in states]
        stats, runs = state_stats(labels, ret, std20)
        results[key] = {"n_days": len(ret), "dates": [dates[19], dates[-1]],
                        "loglik": round(best_ll, 2), "states": stats}
        print("[%s] %d bar(%d-%d) 对数似然=%.1f" % (key, len(ret), dates[19], dates[-1], best_ll))
        for lab in ("涨", "跌", "宽幅震荡", "窄幅震荡"):
            s = stats[lab]
            print("  %s: %d天(%.1f%%) 年化收益=%s%% 年化波=%s%% 均游程=%s天 自转移=%s" % (
                lab, s["days"], s.get("share_pct", 0), s.get("ann_ret_pct"),
                s.get("ann_vol_pct"), s.get("avg_run"), s.get("self_trans")))
    con.close()
    verdicts = {}
    for key in idx_map:
        st = results[key]["states"]
        need = ("涨", "跌", "宽幅震荡", "窄幅震荡")
        ok_min = all(st[lab]["days"] > 0 and st[lab]["share_pct"] >= 5.0 for lab in need)
        vol_ok = (st["涨"]["days"] > 0 and st["跌"]["days"] > 0
                  and st["涨"]["ann_vol_pct"] < st["跌"]["ann_vol_pct"])
        verdicts[key] = bool(ok_min and vol_ok)
    overall = "CALIBRATED" if all(verdicts.values()) else "FAIL/观察"
    print("结构判据: 涨态波<跌态波+四态各≥5%% 双指数=%s → %s" % (verdicts, overall))
    out = {"prereg_fp": fp, "fp_ok": fp_ok,
           "window": results["sh"]["dates"],
           "ref_external": {"up_days": 32, "up_vol": 9.6, "dn_days": 37, "dn_vol": 28},
           "indexes": results, "verdicts": verdicts, "verdict": overall}
    path = OUT2 if (len(sys.argv) > 1 and sys.argv[1] == "run2") else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写入", path, "%.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
