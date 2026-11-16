# -*- coding: utf-8 -*-
"""Kronos v2官方口径sweep — 对齐论文投资模拟协议 (2026-08-18凌晨预备)
官方: lookback90 / H=10 / T=0.6 / top_p0.9 / N=5 / 信号=mean(全horizon预测收盘)-现价 与 last-现价
评估窗: 2025-07~2026-06 (12个月定向, v2训练期止2023-12=全部OOS)
"""
import argparse
import os
import time

import numpy as np
import pandas as pd

from kronos_factor_sweep import QFQ_DB, get_universe, load_qfq, month_end_dates

LOOKBACK = 90          # 官方日频投资模拟回看
PRED_LEN = 10          # 官方H=10
T = 0.6                # 官方推理温度
TOP_P = 0.9
SAMPLES = 5            # 官方N=5
START_MONTH, END_MONTH = "202507", "202606"


def main(ft_dir: str, out_path: str, start_month: str, end_month: str):
    codes, names = get_universe()
    print(f"[universe] 沪深300 {len(codes)} 只")
    df = load_qfq(codes)
    dates = month_end_dates(df)
    dates = [d for d in dates if start_month <= d[:6] <= end_month]
    print(f"[dates] {len(dates)} 个月末: {dates[0]}..{dates[-1]}")

    device = "cuda:0" if __import__("torch").cuda.is_available() else "cpu"
    print(f"[device] {device} | T={T} N={SAMPLES} H={PRED_LEN} lookback={LOOKBACK}")
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        print("[keep-awake] 已阻止系统睡眠")
    except Exception:
        pass

    import glob as _glob
    import sys
    sys.path.append(r"D:\zcodesj\Kronos")
    tok_p = _glob.glob(os.path.join(ft_dir, "tokenizer", "best_model"))[0]
    mdl_p = _glob.glob(os.path.join(ft_dir, "basemodel", "best_model"))[0]
    from model import Kronos, KronosTokenizer, KronosPredictor
    tokenizer = KronosTokenizer.from_pretrained(tok_p)
    model = Kronos.from_pretrained(mdl_p)
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)
    print("[model] v2微调加载完成")

    done = set()
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        old = pd.read_csv(out_path).dropna(subset=["date", "code"])
        done = set(zip(old["date"].astype(str), old["code"].astype(str).str.zfill(6)))
        print(f"[resume] 跳过 {len(done)} 行")

    rows, n, t0 = [], 0, time.time()
    total = len(dates) * len(codes)
    by_code = {c: g for c, g in df.groupby("code")}
    for d in dates:
        for code in codes:
            if (d, code) in done:
                continue
            g = by_code.get(code)
            if g is None:
                continue
            sub = g[g["date"] <= d]
            if len(sub) == 0 or sub["date"].iloc[-1] != d:
                continue
            hist = sub.tail(LOOKBACK).reset_index(drop=True)
            if len(hist) < LOOKBACK:
                continue
            x = hist[["open", "high", "low", "close", "volume", "amount"]]
            x_ts = hist["dt"]
            y_ts = pd.bdate_range(hist["dt"].iloc[-1] + pd.Timedelta(days=1),
                                  periods=PRED_LEN)
            try:
                arr = predictor.predict(
                    df=x, x_timestamp=x_ts, y_timestamp=pd.Series(y_ts),
                    pred_len=PRED_LEN, T=T, top_p=TOP_P, sample_count=SAMPLES,
                    verbose=False, return_samples=True)   # (S, H, 6)
            except Exception as e:
                print(f"  [warn] {d} {code}: {e}")
                continue
            last = float(hist["close"].iloc[-1])
            closes = arr[:, :, 3]                        # (S, H)
            rows.append({
                "date": d, "code": code, "name": names.get(code, ""),
                "last_close": last,
                # 无量纲比率版(官方在z空间算diff≈log比率; 原始价格diff会偏向高价股)
                "sig_horizon_mean": float(closes.mean() / last - 1),
                "sig_last": float(closes[:, -1].mean() / last - 1),
                "mean_ret": float(closes[:, -1].mean() / last - 1),
                "p_up": float((closes[:, -1] > last).mean()),
            })
            n += 1
            if n % 50 == 0:
                pd.DataFrame(rows).to_csv(out_path, mode="a", header=not os.path.exists(out_path), index=False)
                rows = []
                eta = (time.time() - t0) / n * (total - n) / 3600
                print(f"  [{time.strftime('%H:%M:%S')}] [{n}/{total}] ETA={eta:.1f}h", flush=True)
    if rows:
        pd.DataFrame(rows).to_csv(out_path, mode="a", header=not os.path.exists(out_path), index=False)
    print(f"[done] +{n} 行 -> {out_path}  耗时{(time.time()-t0)/60:.0f}min")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", required=True, help="v2微调目录 ckpt_ashare_v2")
    ap.add_argument("--out", default=None)
    ap.add_argument("--start-month", default=START_MONTH)
    ap.add_argument("--end-month", default=END_MONTH)
    a = ap.parse_args()
    out = a.out or (a.ckpt.rstrip("\\/") + "_sweep_v2.csv")
    main(a.ckpt, out, a.start_month, a.end_month)
