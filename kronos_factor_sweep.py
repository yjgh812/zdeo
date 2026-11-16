# -*- coding: utf-8 -*-
"""Kronos Fwd20上涨概率因子扫描 — 沪深300月度 2024-01..2026-06
输入: dragon_quant_qfq.db stock_daily_meta_qfq (前复权, 系统唯一价格源)
信号: Kronos-base 预测未来20根日K, p_up=样本路径中期末价>当前收盘的占比
输出: D:\\zcodesj\\kronos_factor_sweep.csv (增量写入, 断点续跑)
用法: python kronos_factor_sweep.py [--sample-count N] [--max-dates N]
"""
import argparse
import os
import sqlite3
import sys
import time

import pandas as pd

sys.path.append(r"D:\zcodesj\Kronos")
from model import Kronos, KronosTokenizer, KronosPredictor

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_DEFAULT = r"D:\zcodesj\kronos_factor_sweep.csv"
LOOKBACK = 400
PRED_LEN = 20
START_MONTH, END_MONTH = "202401", "202606"  # 含首尾


def get_universe():
    """沪深300当前成分股(csindex官方源), 返回 (codes, name_map)"""
    import akshare as ak
    df = ak.index_stock_cons_csindex(symbol="000300")
    return list(df["成分券代码"]), dict(zip(df["成分券代码"], df["成分券名称"]))


def load_qfq(codes):
    con = sqlite3.connect(QFQ_DB)
    q = ",".join(f"'{c}'" for c in codes)
    df = pd.read_sql_query(
        f"SELECT code, date, open, high, low, close, volume, amount "
        f"FROM stock_daily_meta_qfq WHERE code IN ({q}) ORDER BY code, date", con)
    con.close()
    df["dt"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df


def month_end_dates(df):
    """202401..202606 每个月最后一个交易日"""
    s = df[df["date"].between(START_MONTH, END_MONTH + "31")]["date"].astype(str)
    out = []
    for m in sorted(set(x[:6] for x in s)):
        out.append(max(x for x in s if x.startswith(m)))
    return out


def run_sweep(sample_count: int = 10, max_dates: int | None = None, ft_dir: str = None):
    codes, names = get_universe()
    print(f"[universe] 沪深300成分股 {len(codes)} 只, 快照2026-08-14 (存续偏差备注)")
    df = load_qfq(codes)
    print(f"[data] qfq日K {len(df)} 行, 新鲜至 {df['date'].max()}")

    dates = month_end_dates(df)
    if max_dates:
        dates = dates[:max_dates]
    print(f"[dates] {len(dates)} 个月末交易日: {dates[0]} .. {dates[-1]}")

    device = "cuda:0" if __import__("torch").cuda.is_available() else "cpu"
    print(f"[device] {device} | sample_count={sample_count}")
    try:  # 进程存活期间阻止系统睡眠(笔记本睡眠会杀掉扫描, 8/15 23:32实证)
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        print("[keep-awake] 已阻止系统睡眠")
    except Exception:
        pass
    t0 = time.time()
    # --ckpt 指向微调目录(默认预训练); finetuned结构=tokenizer/best_model + basemodel/best_model
    import glob as _glob
    out_path = OUT_DEFAULT if not ft_dir else str(ft_dir) + "_sweep.csv"
    if ft_dir:
        tok_p = _glob.glob(os.path.join(ft_dir, "tokenizer", "best_model"))[0]
        mdl_p = _glob.glob(os.path.join(ft_dir, "basemodel", "best_model"))[0]
        tokenizer = KronosTokenizer.from_pretrained(tok_p)
        model = Kronos.from_pretrained(mdl_p)
    else:
        tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        model = Kronos.from_pretrained("NeoQuasar/Kronos-base")
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)
    print(f"[model] 加载完成 {time.time() - t0:.1f}s")

    # 断点续跑: 清洗历史CSV(截断行), 重建done; 之后一律追加写, 防覆盖丢数据
    def flush(new_rows):
        if new_rows:
            pd.DataFrame(new_rows).to_csv(out_path, mode="a", header=False, index=False, encoding="utf-8")

    done = set()
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        try:
            old = pd.read_csv(out_path).dropna(subset=["date", "code", "p_up"])
            old.to_csv(out_path, index=False, encoding="utf-8-sig")
            done = set(zip(old["date"].astype(str), old["code"].astype(str).str.zfill(6)))
            print(f"[resume] 清洗后 {len(old)} 行, 跳过 {len(done)} 个(date,code)")
        except Exception as e:
            print(f"[resume] CSV损坏, 重置: {e}")
            pd.DataFrame(columns=["date", "code", "name", "last_close", "p_up", "mean_ret"]).to_csv(
                out_path, index=False, encoding="utf-8-sig")

    rows = []
    by_code = {c: g for c, g in df.groupby("code")}
    total = len(dates) * len(codes)
    n, t_start = 0, time.time()
    for d in dates:
        for code in codes:
            if (d, code) in done:
                continue
            g = by_code.get(code)
            if g is None:
                continue
            sub = g[g["date"] <= d]
            if len(sub) == 0 or sub["date"].iloc[-1] != d:   # 当日停牌/无数据
                continue
            hist = sub.tail(LOOKBACK).reset_index(drop=True)
            if len(hist) < 256:                   # 历史不足(新股)
                continue
            x = hist[["open", "high", "low", "close", "volume", "amount"]]
            x_ts = hist["dt"]
            y_ts = pd.bdate_range(hist["dt"].iloc[-1] + pd.Timedelta(days=1), periods=PRED_LEN)
            try:
                arr = predictor.predict(
                    df=x, x_timestamp=x_ts, y_timestamp=pd.Series(y_ts),
                    pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=sample_count,
                    verbose=False, return_samples=True)
            except Exception as e:
                print(f"  [warn] {d} {code} predict失败: {e}")
                continue
            last = float(hist["close"].iloc[-1])
            paths_close = arr[:, -1, 3]               # 每样本期末close (close=idx3)
            p_up = float((paths_close > last).mean())
            mean_ret = float(paths_close.mean() / last - 1)
            rows.append({"date": d, "code": code, "name": names.get(code, ""),
                         "last_close": last, "p_up": p_up, "mean_ret": mean_ret})
            n += 1
            if n % 50 == 0:
                flush(rows)
                rows = []
                eta = (time.time() - t_start) / n * (total - n) / 3600
                print(f"  [{time.strftime('%H:%M:%S')}] [{n}/{total}] {d} {code} p_up={p_up:.2f} ETA={eta:.1f}h", flush=True)
    flush(rows)
    print(f"[done] 本次新增 {n} 条预测, 总耗时 {(time.time() - t_start) / 60:.1f}min -> {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-count", type=int, default=10)
    ap.add_argument("--max-dates", type=int, default=None)
    ap.add_argument("--ckpt", default=None, help="微调目录(ckpt_ashare), 默认预训练")
    a = ap.parse_args()
    run_sweep(a.sample_count, a.max_dates, a.ckpt)
