# -*- coding: utf-8 -*-
"""
S2 组合版验证：每天选 ret20 最深(跌最多)前 K 只等权，构造月度组合收益序列
输入：smc_s2_trades.csv（严格交易化每笔：code/buy_date/sell_date/ret/ret20）
说明：近似组合（当月选中交易等权平均），用于判断"信号+选股"组合层价值
"""
import pandas as pd
import numpy as np

T = pd.read_csv("smc_s2_trades.csv", dtype={"code": str, "buy_date": str, "sell_date": str})
print(f"总交易 {len(T)} 笔")

for K in [1, 3, 5, 10]:
    # 每天选 ret20 最深(升序)前 K 只
    sel = T.sort_values(["buy_date", "ret20"]).groupby("buy_date").head(K)
    sel = sel.sort_values("buy_date")
    sel["ym"] = sel["buy_date"].str[:6]
    m = sel.groupby("ym")["ret"].mean()
    sharpe = m.mean() / m.std() * np.sqrt(12) if m.std() > 0 else 0
    print(f"\n== 每天选最深 {K} 只 == 交易{len(sel)}笔 月均{m.mean()*100:.2f}% 月胜率{(m>0).mean()*100:.0f}% "
          f"年化{m.mean()*12*100:.1f}% Sharpe{sharpe:.2f} 最大月亏{m.min()*100:.1f}%")
    wins = (sel["ret"] > 0).groupby(sel["buy_date"].str[:4]).mean() * 100
    print("  分年胜率:", " ".join(f"{y}:{v:.0f}%" for y, v in wins.items()))
    yrs = sel.groupby(sel["buy_date"].str[:4])["ret"].mean() * 100
    print("  分年均值:", " ".join(f"{y}:{v:.1f}%" for y, v in yrs.items()))
    if K == 5:
        m.to_csv("smc_s2_combo_monthly_K5.csv")
        print("  K=5 月度序列已存")
