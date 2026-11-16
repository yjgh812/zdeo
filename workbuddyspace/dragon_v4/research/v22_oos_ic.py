# -*- coding: utf-8 -*-
"""
v22_oos_ic.py — V22 生产模型 ml_prob 真实 OOS IC 评估
========================================================
直接从 stock_ml_scores_v22 取 ml_prob，对齐 stock_daily_hsj 的
T+1 前向收益 (next_close / close - 1)，计算 2025-2026 OOS RankIC。

同时计算 IS（V22训练窗内）的 IC 做对照 → 量化样本内污染幅度。

产出：`reports/v22_oos_ic.json`
"""
import json, sqlite3, time
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
REPORTS = Path(r"D:\workbuddyspace\dragon_v4\reports")
V22_TRAIN_END = 20241231   # V22 训练窗截止
OOS_START = 20250101

def conn():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)

def main():
    t0 = time.time()
    db = conn()
    try:
        # ml_prob
        print("[1/3] 加载 ml_prob ...", flush=True)
        ml = pd.read_sql_query("""
            SELECT code, date, ml_prob FROM stock_ml_scores_v22
            WHERE date BETWEEN 20190101 AND 20260807
        """, db)
        ml["code"] = ml["code"].astype(str).str.zfill(6)
        ml["date"] = ml["date"].astype(int)

        # stock_daily_hsj: close + next_close
        print("[2/3] 加载日线 + 对齐 T+1 前向收益 ...", flush=True)
        hsj = pd.read_sql_query("""
            SELECT code, date, close FROM stock_daily_hsj
            WHERE date BETWEEN 20190101 AND 20260807 AND close>0
        """, db)
        hsj["code"] = hsj["code"].astype(str).str.zfill(6)
        hsj["date"] = hsj["date"].astype(int)
        hsj = hsj.sort_values(["code","date"])
        hsj["next_close"] = hsj.groupby("code")["close"].shift(-1)
        hsj["fwd_ret"] = hsj["next_close"] / hsj["close"] - 1
        hsj = hsj.dropna(subset=["fwd_ret"])
    finally:
        db.close()

    print("[3/3] 计算每日截面 RankIC ...", flush=True)
    merged = ml.merge(hsj[["code","date","fwd_ret"]], on=["code","date"], how="inner")
    print(f"  合并: {len(merged)} 行")

    def daily_ic(sub):
        if len(sub) < 30: return np.nan
        return spearmanr(sub["ml_prob"], sub["fwd_ret"])[0]

    grp = merged.groupby("date")
    ics = grp.apply(daily_ic).dropna()
    dates = pd.Series(ics.index, index=ics.index)

    is_ics = ics[ics.index <= V22_TRAIN_END]
    oos_ics = ics[ics.index >= OOS_START]

    result = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "label": "T+1 forward return (next_close/close-1)",
        "v22_train_end": V22_TRAIN_END,
        "total": {
            "n_days": len(ics), "ic_mean": round(float(ics.mean()), 6),
            "icir": round(float(ics.mean()/ics.std()), 4) if ics.std()>0 else 0,
            "ic_win": round(float((ics>0).mean()), 4),
        },
        "oos": {
            "period": f"{OOS_START}~20260807",
            "n_days": len(oos_ics),
            "ic_mean": round(float(oos_ics.mean()), 6) if len(oos_ics)>0 else None,
            "icir": round(float(oos_ics.mean()/oos_ics.std()),4) if len(oos_ics)>0 and oos_ics.std()>0 else 0,
            "ic_win": round(float((oos_ics>0).mean()),4) if len(oos_ics)>0 else None,
        },
        "is": {
            "period": "2019~20241231",
            "n_days": len(is_ics),
            "ic_mean": round(float(is_ics.mean()), 6) if len(is_ics)>0 else None,
            "icir": round(float(is_ics.mean()/is_ics.std()),4) if len(is_ics)>0 and is_ics.std()>0 else 0,
            "ic_win": round(float((is_ics>0).mean()),4) if len(is_ics)>0 else None,
        },
        "contamination": {
            "is_oos_ratio": round(float(is_ics.mean()/oos_ics.mean()),2) if len(is_ics)>0 and len(oos_ics)>0 and oos_ics.mean()!=0 else 0,
            "note": "IS IC / OOS IC > 1.0 表示样本内高估",
        },
        "time_s": round(time.time()-t0, 1),
    }

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "v22_oos_ic.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    print(f"\nV22 ml_prob 真实 OOS IC:")
    print(f"  OOS ({result['oos']['period']}): IC={result['oos']['ic_mean']:+.4f} "
          f"IR={result['oos']['icir']:+.3f} win={result['oos']['ic_win']:.0%}")
    print(f"  IS  ({result['is']['period']}): IC={result['is']['ic_mean']:+.4f} "
          f"IR={result['is']['icir']:+.3f} win={result['is']['ic_win']:.0%}")
    print(f"  污染比 IS/OOS={result['contamination']['is_oos_ratio']:.1f}x")
    print(f"  -> {out}")

if __name__ == "__main__":
    main()
