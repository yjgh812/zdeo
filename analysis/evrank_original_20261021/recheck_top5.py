# -*- coding: utf-8 -*-
"""复验脚本(只读): 对 TOP5 用独立实现路径(pure python, 不用 pandas)重算上轮输出, 断言一致.
同库=qfq真源, 但计算路径独立 -> 验证计算实现正确性. 跨源验证另由 baostock 完成."""
import sqlite3

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
CODES = ["601225", "600901", "000719", "601098", "002292"]
# 上轮输出(报告值)
EXPECT = {
    "601225": dict(win5=0.583, ret20=0.074, ret60=0.115, dd250=-0.025, bias20=0.048),
    "600901": dict(win5=0.548, ret20=0.061, ret60=0.105, dd250=-0.015, bias20=0.043),
    "000719": dict(win5=0.470, ret20=0.054, ret60=0.066, dd250=-0.062, bias20=0.036),
    "601098": dict(win5=0.513, ret20=0.075, ret60=0.060, dd250=-0.080, bias20=0.027),
    "002292": dict(win5=0.513, ret20=0.106, ret60=0.222, dd250=-0.146, bias20=0.051),
}
TOL = 0.005  # 报告为3位小数舍入值, 容差取半位

con = sqlite3.connect(QFQ, uri=True)
all_ok = True
for code in CODES:
    rows = con.execute(
        "SELECT date, close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
        (code,)).fetchall()
    closes = [r[1] for r in rows]
    vols = [r[2] for r in rows]
    n = len(closes)
    i = n - 1

    def ma(period):  # 独立实现: 简单均值
        return sum(closes[i - period + 1:i + 1]) / period

    ma5, ma10, ma20, ma60, ma120 = ma(5), ma(10), ma(20), ma(60), ma(120)
    close = closes[i]
    ret20 = close / closes[i - 20] - 1
    ret60 = close / closes[i - 60] - 1
    # 距250日高点回撤(用high)
    highs = [r[0] for r in con.execute(
        "SELECT high FROM stock_daily_meta_qfq WHERE code=? ORDER BY date", (code,))]
    hi250 = max(highs[max(0, i - 249):i + 1])
    dd250 = close / hi250 - 1
    bias20 = close / ma20 - 1
    # win5: 近120日买入持5日盈利占比
    wins = 0
    tot = 0
    for t in range(max(0, i - 119), i + 1):
        if t + 5 <= i:
            tot += 1
            if closes[t + 5] / closes[t] - 1 > 0:
                wins += 1
    win5 = wins / tot
    bull = close > ma20 > ma60 and ma60 > ma120 and ma5 > ma10 > ma20
    vr = sum(vols[i - 4:i + 1]) / 5 / (sum(vols[i - 59:i + 1]) / 60)

    e = EXPECT[code]
    checks = {
        "bull": (bull, True),
        "win5": (round(win5, 3), e["win5"]),
        "ret20": (round(ret20, 3), e["ret20"]),
        "ret60": (round(ret60, 3), e["ret60"]),
        "dd250": (round(dd250, 3), e["dd250"]),
        "bias20": (round(bias20, 3), e["bias20"]),
    }
    bad = []
    for k, (got, exp) in checks.items():
        if isinstance(exp, bool):
            if got != exp:
                bad.append(f"{k}: got={got} exp={exp}")
        elif abs(got - exp) > TOL:
            bad.append(f"{k}: got={got} exp={exp}")
    status = "PASS" if not bad else "FAIL " + "; ".join(bad)
    if bad:
        all_ok = False
    print(f"{code}: bull={bull} win5={win5:.3f} ret20={ret20:.3f} ret60={ret60:.3f} "
          f"dd250={dd250:.3f} bias20={bias20:.3f} vr={vr:.2f} n={n} last={rows[-1][0]} -> {status}")
con.close()
print("\nRECHECK RESULT:", "ALL PASS" if all_ok else "HAS FAILURES")
