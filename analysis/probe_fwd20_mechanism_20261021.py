# -*- coding: utf-8 -*-
"""机制研究(20261021): seed0(干净重训) pred 的 fwd20 结构 = 未知信息 or 已知因子伪装?

假设: pred 可能是「超卖/低波动」倾斜的伪装(2026 年 V 型市里超卖倾斜两头占优:
     崩盘月跌得少 IC 为正、反弹月弹得多 IC 也为正——与实测"下跌月IC更强"吻合)。
检验: 对齐 (pred, f20, vol20, mom20), 三步定谳:
  ① pred 与 vol20/mom20 的横截面相关(它是什么的代理);
  ② vol20 / mom20 各自对 f20 的日均秩IC(已知因子本身的预测力);
  ③ pred ⊥ (vol20, mom20) 正交化残差对 f20 的日均秩IC(残差IC≈0 → 纯伪装; 显著>0 → 独立信息)。
"""
import pickle
import sqlite3
import sys

import numpy as np
import pandas as pd

PKL = r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_20261022_seed0.pkl"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def main():
    s = pickle.load(open(PKL, "rb")).rename("pred").reset_index()
    s["dt"] = s["datetime"].astype(str).str[:10].str.replace("-", "")
    s["code"] = s["instrument"].str[-6:]
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    codes = ",".join(f"'{c}'" for c in s["code"].unique())
    q = pd.read_sql_query(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({codes}) AND date>='20251201' ORDER BY code,date",
        con)
    con.close()
    cal = sorted(q["date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    q["i"] = q["date"].map(pos)
    closes = q.pivot(index="i", columns="code", values="close").sort_index()
    ret = closes / closes.shift(1) - 1
    vol20 = ret.rolling(20).std() * np.sqrt(20) * 100          # 20日年化波动%
    mom20 = (closes / closes.shift(20) - 1) * 100               # 过去20日动量%

    m = s.copy()
    m["i"] = m["dt"].map(pos)
    m = m.dropna(subset=["i"]); m["i"] = m["i"].astype(int)
    m = m.set_index(["code", "i"])
    f20 = (closes.shift(-20) / closes - 1).stack().swaplevel().rename_axis(["code", "i"]).rename("f20")
    v20 = vol20.stack().swaplevel().rename_axis(["code", "i"]).rename("vol20")
    mm20 = mom20.stack().swaplevel().rename_axis(["code", "i"]).rename("mom20")
    j = pd.concat([m["pred"], f20, v20, mm20], axis=1, join="inner").dropna()
    j = j.reset_index()
    print(f"对齐样本 n={len(j)}")

    print("\n① pred 的横截面画像(日均秩相关):")
    for col in ("vol20", "mom20"):
        ic = j.groupby("i").apply(lambda g: g["pred"].corr(g[col], method="spearman"),
                                  include_groups=False).replace([np.inf, -np.inf], np.nan).dropna()
        print(f"  pred vs {col}: {ic.mean():+.4f} ({(ic > 0).mean():.0%}日为正)")

    print("\n② 已知因子自身对 f20 的日均秩IC:")
    for col in ("vol20", "mom20"):
        ic = j.groupby("i").apply(lambda g: g[col].corr(g["f20"], method="spearman"),
                                  include_groups=False).replace([np.inf, -np.inf], np.nan).dropna()
        print(f"  {col} vs f20: {ic.mean():+.4f}")

    print("\n③ 正交化残差检验(逐日截面回归 pred ~ vol20 + mom20, 取残差):")
    res_ic = []
    for _, g in j.groupby("i"):
        if len(g) < 30:
            continue
        x = g[["vol20", "mom20"]].values
        y = g["pred"].values
        xm = np.column_stack([np.ones(len(x)), x])
        try:
            beta, *_ = np.linalg.lstsq(xm, y, rcond=None)
            res = y - xm @ beta
            ic = pd.Series(res).corr(g["f20"].reset_index(drop=True), method="spearman")
            if ic == ic:
                res_ic.append(ic)
        except Exception:
            continue
    ric = np.array(res_ic)
    print(f"  残差日均秩IC = {ric.mean():+.4f} ±{ric.std():.4f} ({len(ric)}日, {(ric>0).mean():.0%}为正)")
    print("\n判定: 残差IC≈0 → pred=已知因子(超卖/低波)伪装, fwd20结构无新信息;")
    print("      残差IC显著>0 → pred含独立信息, 20日视野研究立项成立。")


if __name__ == "__main__":
    sys.exit(main())
