# -*- coding: utf-8 -*-
"""fracDiff 可行性探针 (D3, 为画像 v1.2 十月评审备料; 2026-09-07)

de Prado AFML Ch5: 整数差分(d=1)平稳但抹掉全部记忆; 分数阶差分找最小 d* 使序列平稳
(ADF p<0.01) 同时保留记忆(与原对数价格的相关性)。
本探针只做可行性层: d* 分布 + 记忆保留曲线; IC 增量留 v1.2 评审。
对象: ①自建全市场等权指数(2016+ 日 log-ret 均值累加); ②随机 30 只个股(2016 起 >=2000 行)。
方法: fixed-width window fracdiff (FFLL, tol=1e-5)。只读研究。
产物: data/_fracdiff_probe_20260907.json
"""
import json
import time

import numpy as np
import pandas as pd
import sqlite3
from statsmodels.tsa.stattools import adfuller

QFQ_DB = "D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
OUT_JSON = "data/_fracdiff_probe_20260907.json"
D_GRID = [round(x, 2) for x in np.arange(0.1, 1.01, 0.1)]
TOL = 1e-5
N_STOCKS = 30
SEED = 7

t0 = time.time()


def log(m):
    print(f"[{time.time()-t0:6.1f}s] {m}", flush=True)


def fracdiff_ffll(series, d, tol=TOL):
    """Fixed-width window fractional differencing (AFML ch5). 返回对齐尾部后长度 = len-w+1。"""
    w = [1.0]
    for k in range(1, 10000):
        wk = -w[-1] * (d - k + 1) / k
        if abs(wk) < tol:
            break
        w.append(wk)
    w = np.array(w[::-1])  # 最旧权重在前
    width = len(w)
    if len(series) < width + 10:
        return None, width
    # 滑窗点积
    s = np.asarray(series, dtype=np.float64)
    # out[i] = sum_j w[j]*s[i+j], j=0..width-1 (w[0]=最旧)
    out = np.convolve(s, w, mode="valid")
    return out, width


def adf_p(x):
    try:
        return adfuller(x, autolag="AIC")[1]
    except Exception:
        return 1.0


def analyze(name, logp):
    """logp: 对数价格序列(升序时间)。返回 d 网格上的 (adf_p, memory_corr) 与 d*。"""
    rows = []
    for d in D_GRID:
        fd, width = fracdiff_ffll(logp, d)
        if fd is None or len(fd) < 100:
            continue
        p = adf_p(fd)
        # 记忆保留: 与对应窗口的原 log 价格相关
        mem_corr = np.corrcoef(fd, logp[width - 1:])[0, 1]
        rows.append({"d": d, "adf_p": round(float(p), 5),
                     "memory_corr": round(float(mem_corr), 4), "window": width})
    d_star = None
    for r in rows:
        if r["adf_p"] < 0.01:
            d_star = r["d"]
            break
    return {"series": name, "n": len(logp), "d_star_stationary": d_star,
            "curve": rows}


def main():
    # 1) 读全市场 close(只 code,date,close), 构建等权指数 + 抽样个股
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    chunks = []
    for ch in pd.read_sql("SELECT code,date,close FROM stock_daily_meta_qfq",
                          con, chunksize=3_000_000):
        ch["date"] = ch["date"].astype(str).str[:8].astype(np.int64)
        chunks.append(ch)
    con.close()
    px = pd.concat(chunks, ignore_index=True)
    px["code"] = px["code"].astype(str).str.zfill(6)
    px = px.sort_values(["code", "date"])
    log(f"rows: {len(px):,}")

    # 等权指数: 每日全市场 log-ret 均值累加
    px["logret"] = px.groupby("code")["close"].transform(lambda s: np.log(s).diff())
    daily = px.dropna(subset=["logret"]).groupby("date")["logret"].mean().sort_index()
    idx_logp = np.cumsum(daily.to_numpy())
    res_idx = analyze("ew_index_2016plus", idx_logp)
    log(f"ew_index d* = {res_idx['d_star_stationary']}")

    # 个股抽样: 2016 起 >=2000 行
    cnt = px.groupby("code").size()
    elig = cnt[cnt >= 2000].index.to_numpy()
    rng = np.random.default_rng(SEED)
    picks = rng.choice(elig, size=min(N_STOCKS, len(elig)), replace=False)
    stock_res = []
    for code in picks:
        s = px[px["code"] == code].sort_values("date")
        lp = np.log(s["close"].to_numpy())
        lp = lp[np.isfinite(lp)]
        if len(lp) < 1000:
            continue
        stock_res.append(analyze(f"stock_{code}", lp))
    d_stars = [r["d_star_stationary"] for r in stock_res if r["d_star_stationary"]]
    log(f"stocks: {len(stock_res)}, d* dist: {np.percentile(d_stars, [25, 50, 75]).tolist() if d_stars else 'NA'}")

    out = {
        "generated": "2026-09-07", "method": "FFLL fracdiff tol=1e-5, ADF p<0.01 为平稳判据",
        "d_grid": D_GRID,
        "ew_index": res_idx,
        "stocks": stock_res,
        "summary": {
            "n_stocks": len(stock_res),
            "d_star_pct25_50_75": (np.percentile(d_stars, [25, 50, 75]).round(2).tolist()
                                    if d_stars else None),
            "d_star_mean": round(float(np.mean(d_stars)), 3) if d_stars else None,
            "note": "d*=1 即普通差分; d* 明显<1 ⇒ fracDiff 有记忆保留空间",
        },
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log(f"saved -> {OUT_JSON}")
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    print("ew_index curve:", [(r['d'], r['adf_p'], r['memory_corr']) for r in res_idx["curve"]])


if __name__ == "__main__":
    main()
