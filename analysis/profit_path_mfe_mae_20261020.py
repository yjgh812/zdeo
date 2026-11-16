# -*- coding: utf-8 -*-
"""A2 加仓反事实观测层（学习计划阶段A2, 只读零交易风险）
对 bp_ep/ml 双引擎全部历史交易(1778+1339笔)逐笔计算:
  MFE/MAE(持有窗内最大浮盈/最大浮亏, 以high/low对P0)、+3/5/8%首触时点占持仓期比例、
  事后最优加仓点(argmax close)与"完美时机上界"(在argmax收盘加一档持至原卖出的收益)、
  可行触发对照(R1p X=5配对增量, 引自#109/#110)→量化"完美时机与可行时机的差距"。
产出: analysis/profit_path_mfe_mae_20261020.json + md 表格直出
"""
import json
import sqlite3
import statistics

import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
TRADES = {
    "bp_ep": r"D:\zcodesj\reports\backtest_full_20261019_ladder_L0b\bp_ep_core_trades_full.csv",
    "ml": r"D:\zcodesj\reports\backtest_full_20261016_eod\ml_signal_trades_full.csv",
}
OUT = r"D:\zcodesj\analysis\profit_path_mfe_mae_20261020.json"


def _dnorm(d):
    d = str(d)
    return d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:]}"


def load_bars(codes):
    db = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    bars = {}
    for c in codes:
        rows = [(_dnorm(d), h, l, c) for d, h, l, c in db.execute(
            "select date,high,low,close from stock_daily_meta_qfq where code=? order by date", (c,)
        )]
        bars[c] = rows
    db.close()
    return bars


def pct(vals, q):
    s = sorted(vals)
    if not s:
        return None
    return round(s[min(len(s) - 1, int(q * len(s)))], 4)


def main():
    all_res = {}
    for engine, path in TRADES.items():
        t = pd.read_csv(path, dtype={"股票代码": str})
        t["股票代码"] = t["股票代码"].str.zfill(6)
        bars = load_bars(sorted(t["股票代码"].unique()))
        recs = []
        for _, r in t.iterrows():
            code = r["股票代码"]
            rows = bars.get(code) or []
            dates = [x[0] for x in rows]
            try:
                i0 = next(i for i, d in enumerate(dates) if d >= r["买入日期"])
            except StopIteration:
                continue
            iD = None
            for j in range(i0, len(dates)):
                if dates[j] >= r["卖出日期"]:
                    iD = j
                    break
            if iD is None or iD <= i0:
                continue
            p0 = float(r["买入价格"])
            win = rows[i0:iD + 1]
            highs = [x[1] for x in win]
            lows = [x[2] for x in win]
            closes = [x[3] for x in win]
            mfe = max(highs) / p0 - 1
            mae = min(lows) / p0 - 1
            k_best = max(range(len(closes)), key=lambda j: closes[j])
            # 完美时机上界: 在事后最优收盘日加一档, 持至原卖出
            ub = float(r["卖出价格"]) / closes[k_best] - 1 - 0.002
            # +5% 首触时点(占持仓期比例)
            touch5 = None
            for j, c in enumerate(closes):
                if c >= p0 * 1.05:
                    touch5 = j / (len(closes) - 1)
                    break
            recs.append({"mfe": mfe, "mae": mae, "k": k_best / (len(closes) - 1),
                         "ub": ub, "touch5": touch5,
                         "r0": float(r["卖出价格"]) / p0 - 1})
        mfe_l = [x["mfe"] for x in recs]
        mae_l = [x["mae"] for x in recs]
        ub_l = [x["ub"] for x in recs]
        kb = [x["k"] for x in recs]
        t5 = [x["touch5"] for x in recs if x["touch5"] is not None]
        res = {
            "n": len(recs),
            "mfe_med": pct(mfe_l, 0.5), "mfe_p25": pct(mfe_l, 0.25), "mfe_p75": pct(mfe_l, 0.75),
            "mae_med": pct(mae_l, 0.5), "mae_p25": pct(mae_l, 0.25), "mae_p75": pct(mae_l, 0.75),
            "mfe_ge_5pct_share": round(sum(1 for x in mfe_l if x >= 0.05) / len(mfe_l), 4),
            "mfe_ge_8pct_share": round(sum(1 for x in mfe_l if x >= 0.08) / len(mfe_l), 4),
            "best_add_point_med": pct(kb, 0.5),
            "best_add_point_p25": pct(kb, 0.25), "best_add_point_p75": pct(kb, 0.75),
            "perfect_ub_med": pct(ub_l, 0.5),
            "touch5_share": round(len(t5) / len(recs), 4),
            "touch5_pos_med": pct(t5, 0.5) if t5 else None,
        }
        all_res[engine] = res
        print(engine, json.dumps(res, ensure_ascii=False), flush=True)

    out = {"engines": all_res,
           "note": "perfect_ub=事后最优加仓点上界(不可实现); 可行触发对照=R1p X5 配对增量(bp_ep -0.31%/ml -0.16%, 见#109/#110)。差距=时机价值的真实量级。"}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[done] →", OUT, flush=True)


if __name__ == "__main__":
    main()
