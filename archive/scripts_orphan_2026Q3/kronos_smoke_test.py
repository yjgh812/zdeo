# -*- coding: utf-8 -*-
"""Kronos 金融K线基础模型 冒烟测试
数据: dragon_quant_meta.db stock_daily_meta (本地A股日K)
模型: NeoQuasar/Kronos-base (1.02亿参数) + Kronos-Tokenizer-base (本地缓存 D:\\zcodesj\\hf_cache)
运行: HF_HOME=D:\\zcodesj\\hf_cache python kronos_smoke_test.py
"""
import sys
import time
import sqlite3

import pandas as pd

sys.path.append(r"D:\zcodesj\Kronos")
from model import Kronos, KronosTokenizer, KronosPredictor

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"


def load_daily(code: str, n: int = 600) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume, amount FROM stock_daily_meta "
        f"WHERE code='{code}' ORDER BY date DESC LIMIT {n}",
        con,
    )
    con.close()
    df = df.iloc[::-1].reset_index(drop=True)
    df["timestamps"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    return df[["timestamps", "open", "high", "low", "close", "volume", "amount"]]


def main() -> None:
    code, name = "000001", "平安银行"
    df = load_daily(code)
    last = float(df["close"].iloc[-1])
    print(f"[数据] {name}({code}) {len(df)}根日K "
          f"{df['timestamps'].iloc[0].date()}~{df['timestamps'].iloc[-1].date()} 最新收盘{last:.2f}")

    t0 = time.time()
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-base")
    predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=512)
    print(f"[模型] Kronos-base 加载完成 {time.time() - t0:.1f}s (CPU)")

    lookback, pred_len = 400, 20
    x_df = df.iloc[-lookback:][["open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True)
    x_ts = df.iloc[-lookback:]["timestamps"].reset_index(drop=True)
    future = pd.bdate_range(df["timestamps"].iloc[-1] + pd.Timedelta(days=1), periods=pred_len)

    t0 = time.time()
    pred = predictor.predict(
        df=x_df,
        x_timestamp=x_ts,
        y_timestamp=pd.Series(future),
        pred_len=pred_len,
        T=1.0,
        top_p=0.9,
        sample_count=1,
        verbose=True,
    )
    print(f"[推理] 预测耗时 {time.time() - t0:.1f}s")

    print(f"[结果] 真实最新收盘 {last:.2f} | 预测期末 {pred['close'].iloc[-1]:.2f} "
          f"({(pred['close'].iloc[-1] / last - 1) * 100:+.2f}%) | "
          f"区间 {pred['close'].min():.2f}~{pred['close'].max():.2f}")
    print(pred.head())
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
