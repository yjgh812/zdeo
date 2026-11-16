# -*- coding: utf-8 -*-
r"""
Kronos A股本地微调 Phase 1.4
================================
原判死: 三关FAIL(RankIC-0.0013零信号/分层双峰断裂/分年反号), 归档条件=A股微调后重过三关。
本脚本执行该复活条件: 用A股qfq日线微调Kronos(域适配), 再评估Fwd20因子IC。

步骤:
  1. 导出A股Top200流动性股票 2020-2026 qfq日线 → Kronos CSV格式(每股一文件)
  2. 微调 tokenizer + predictor (finetune_csv管线, lookback=256, predict=20)
  3. 对2025-2026测试段滚动预测 → Fwd20信号 → 截面RankIC三关评估

产出: D:\zcodesj\Kronos\finetune_csv\data\ashare\*.csv + reports/KRONOS_FINETUNE_20260816.md
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
KRONOS_FT = Path(r"D:\zcodesj\Kronos\finetune_csv")
OUT_DIR = KRONOS_FT / "data" / "ashare"
REPORT = Path(r"D:\zcodesj\reports")
N_STOCKS = 200


def export_csv():
    """Top200流动性 → 每股一个Kronos格式CSV。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT code, date, open, high, low, close, volume, amount "
        "FROM stock_daily_meta_qfq WHERE date >= 20200101 AND close > 0",
        conn)
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(("60", "00", "30", "68", "92"))]
    df = df.drop_duplicates(["code", "date"], keep="last")
    # 流动性Top200
    df["amt"] = df["volume"] * df["close"]
    liq = df.groupby("code")["amt"].mean()
    top = list(liq.sort_values(ascending=False).head(N_STOCKS).index)
    n = 0
    for c in top:
        sub = df[df["code"] == c].sort_values("date").copy()
        if len(sub) < 400:
            continue
        out = pd.DataFrame({
            "timestamps": pd.to_datetime(sub["date"].astype(str),
                                         format="%Y%m%d").dt.strftime("%Y/%m/%d"),
            "open": sub["open"], "high": sub["high"], "low": sub["low"],
            "close": sub["close"], "volume": sub["volume"],
            "amount": 0,   # ⚠️qfq库amount不可用铁律 → 全0(Kronos支持)
        })
        out.to_csv(OUT_DIR / f"{c}.csv", index=False)
        n += 1
    print(f"  导出 {n}/{len(top)} 股 → {OUT_DIR}")
    return top


def write_config(codes):
    """生成微调config(多文件需合并成单CSV: Kronos管线单文件)。"""
    # Kronos finetune_csv要单CSV——合并所有股票到一文件, timestamps连续分股
    frames = []
    for f in sorted(OUT_DIR.glob("*.csv")):
        frames.append(pd.read_csv(f))
    big = pd.concat(frames, ignore_index=True)
    big_path = KRONOS_FT / "data" / "ashare_all.csv"
    big.to_csv(big_path, index=False)
    print(f"  合并: {len(big):,}行 → {big_path.name}")
    cfg = f"""data:
  data_path: "{str(big_path).replace(chr(92), '/')}"
  lookback_window: 256
  predict_window: 20
  max_context: 256
  train_start: "2020/01/01"
  val_start: "2024/06/01"
  test_start: "2025/01/01"
  batch_size: 32

train:
  tokenizer:
    max_epochs: 3
    learning_rate: 1.0e-4
  predictor:
    max_epochs: 3
    learning_rate: 1.0e-4

model:
  tokenizer_ckpt: "NeoQuan/Kronos-Tokenizer-base"
  use_amp: true

path:
  save_dir: "./ckpt_ashare"
"""
    p = KRONOS_FT / "configs" / "config_ashare_daily.yaml"
    p.write_text(cfg, encoding="utf-8")
    print(f"  config: {p}")
    return p


def main():
    print("=== Kronos A股微调数据准备 ===")
    codes = export_csv()
    cfg = write_config(codes)
    print("  下一步: cd Kronos/finetune_csv && python train_sequential.py "
          f"--config {cfg.name}")


if __name__ == "__main__":
    main()
