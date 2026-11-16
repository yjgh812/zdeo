# -*- coding: utf-8 -*-
"""特征归因(20261021) — seed0 pred 的 fwd20 结构藏在哪些 Alpha158 因子里。

方法: master_feat.parquet(修复后数据, 158因子+63市场+1标签) test期(2026-01→max) 与 seed0 pred 对齐,
     另拼 qfq 真 fwd20。三层:
  ① 全因子画像: 每因子 日均|秩相关(因子,pred)| → Top15"模型在看的因子";
  ② 全因子预测力: 每因子 日均秩IC(因子,f20) → Top15"本身能预测的因子";
  ③ 正交阶梯: pred 逐日截面回归剔除 Top-N pred相关因子(N=5/10/20/30) 后, 残差对 f20 的日均秩IC
     → IC 随 N 衰减到≈0 = 信息可归因于已知因子组合; 维持高位 = 深度交互/非简单线性组合。
"""
import pickle
import sqlite3
import sys

import numpy as np
import pandas as pd

FEAT = r"D:\zcodesj\strategy_libs\MASTER\data\tulong\master_feat.parquet"
PKL = r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_20261022_seed0.pkl"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
TOPN = 15


def main():
    feat = pd.read_parquet(FEAT)
    feat_cols = [c for c in feat.columns if c != "LABEL0"][:158]
    t = feat[feat.index.get_level_values("datetime") >= "2026-01-01"].copy()
    t["code"] = [i[-6:] for i in t.index.get_level_values("instrument")]
    t["d"] = [x.strftime("%Y%m%d") for x in t.index.get_level_values("datetime")]

    s = pickle.load(open(PKL, "rb")).rename("pred").reset_index()
    s["code"] = s["instrument"].str[-6:]
    s["d"] = s["datetime"].astype(str).str[:10].str.replace("-", "")
    t = t.reset_index().merge(s[["code", "d", "pred"]], on=["code", "d"], how="inner")

    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    codes = ",".join(f"'{c}'" for c in t["code"].unique())
    q = pd.read_sql_query(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({codes}) AND date>='20260101' ORDER BY code,date",
        con)
    con.close()
    cal = sorted(q["date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    q["i"] = q["date"].map(pos)
    closes = q.pivot(index="i", columns="code", values="close").sort_index()
    f20 = (closes.shift(-20) / closes - 1).stack().swaplevel().rename_axis(["code", "i"]).rename("f20")
    f20d = f20.reset_index()
    f20d["d"] = f20d["i"].map(lambda i: cal[int(i)])
    t = t.merge(f20d[["code", "d", "f20"]], on=["code", "d"], how="left").dropna(subset=["f20", "pred"])
    print(f"[align] n={len(t)} 股票{t['code'].nunique()} 天数{t['d'].nunique()}")

    def dailyc(col_a, col_b, absflag=False):
        g = t.groupby("d").apply(
            lambda x: x[col_a].corr(x[col_b], method="spearman"), include_groups=False)
        g = g.replace([np.inf, -np.inf], np.nan).dropna()
        return (g.abs().mean() if absflag else g.mean()), (g.abs().mean() if absflag else (g > 0).mean())

    prof, icf = [], []
    for c in feat_cols:
        if t[c].nunique() < 5:
            continue
        mp, pp = dailyc(c, "pred", absflag=True)
        mi, pi = dailyc(c, "f20")
        prof.append((c, mp)); icf.append((c, mi, pi))
    prof.sort(key=lambda x: -x[1]); icf.sort(key=lambda x: -abs(x[1]))
    print(f"\n== ① 模型在看的因子(Top{TOPN} 日均|秩相关| vs pred) ==")
    for c, v in prof[:TOPN]:
        print(f"  {c:<14}{v:.4f}")
    print(f"\n== ② 本身能预测的因子(Top{TOPN} 日均|秩IC| vs f20) ==")
    for c, v, pos_r in icf[:TOPN]:
        print(f"  {c:<14}{v:.4f} (方向{'+' if pos_r > 0.5 else '-'}{pos_r:.0%})")

    watch = {c for c, _ in prof[:TOPN]}
    pred_top = [c for c, _ in prof[:30]]
    print("\n== ③ 正交阶梯(残差IC vs f20) ==")
    base, _ = dailyc("pred", "f20")
    print(f"  原始 pred→f20 IC: {base:+.4f}")
    for n in (5, 10, 20, 30):
        cols = pred_top[:n]
        res_ic = []
        for d, g in t.groupby("d"):
            if len(g) < 30:
                continue
            xm = np.column_stack([np.ones(len(g))] + [g[c].values for c in cols])
            y = g["pred"].values
            try:
                beta, *_ = np.linalg.lstsq(xm, y, rcond=None)
                res = y - xm @ beta
                ic = pd.Series(res).corr(g["f20"], method="spearman")
                if ic == ic:
                    res_ic.append(ic)
            except Exception:
                continue
        ric = np.array(res_ic)
        print(f"  剔除Top{n:2d}: 残差IC={ric.mean():+.4f} ±{ric.std():.4f} ({(ric > 0).mean():.0%}日正)")


if __name__ == "__main__":
    sys.exit(main())
