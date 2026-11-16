# -*- coding: utf-8 -*-
"""复验(只读): 主线/次线5只, 纯Python独立实现重算全部指标并断言与筛选输出一致."""
import sqlite3

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
CODES = ["300199", "002376", "601098", "000719", "600566"]
EXPECT = {
    "300199": dict(win5=0.539, ret20=0.127, ret60=0.220, dd250=-0.043, dd20=-0.078, bias20=0.096, rsi=64.972, vr=1.381),
    "002376": dict(win5=0.461, ret20=0.133, ret60=0.220, dd250=-0.099, dd20=-0.074, bias20=0.093, rsi=65.625, vr=3.072),
    "601098": dict(win5=0.513, ret20=0.075, ret60=0.060, dd250=-0.080, dd20=-0.060, bias20=0.027, rsi=56.723, vr=1.483),
    "000719": dict(win5=0.470, ret20=0.054, ret60=0.066, dd250=-0.062, dd20=-0.049, bias20=0.036, rsi=59.783, vr=1.688),
    "600566": dict(win5=0.461, ret20=0.051, ret60=-0.004, dd250=-0.058, dd20=-0.061, bias20=0.035, rsi=65.705, vr=1.396),
}
TOL = 0.006

con = sqlite3.connect(QFQ, uri=True)
all_ok = True
for code in CODES:
    rows = con.execute(
        "SELECT date, close, volume, high, low FROM stock_daily_meta_qfq "
        "WHERE code=? ORDER BY date", (code,)).fetchall()
    closes = [r[1] for r in rows]; vols = [r[2] for r in rows]
    highs = [r[3] for r in rows]; lows = [r[4] for r in rows]
    n = len(closes); i = n - 1
    diffs14 = [closes[t] - closes[t - 1] for t in range(i - 13, i + 1)]

    def ma(p): return sum(closes[i - p + 1:i + 1]) / p
    ma5, ma10, ma20, ma60, ma120 = ma(5), ma(10), ma(20), ma(60), ma(120)
    close = closes[i]
    ret20 = close / closes[i - 20] - 1
    ret60 = close / closes[i - 60] - 1
    hi250 = max(highs[max(0, i - 249):i + 1])
    dd250 = close / hi250 - 1
    # 与主筛选同语义: 近19天逐日"收盘相对其20日滚动收盘高点"回撤的最小值(滚动回撤极值)
    worst20 = min((lambda hh: closes[t] / hh - 1)(max(closes[t - 19:t + 1]))
                  for t in range(i - 19, i + 1))
    gains = [max(diffs14[t], 0) for t in range(14)]
    losses = [abs(min(diffs14[t], 0)) for t in range(14)]
    ag, al = sum(gains) / 14, sum(losses) / 14            # Wilder SMA-RSI: 除以完整窗口14
    rsi = 100 * ag / (ag + al)
    vr = sum(vols[i - 4:i + 1]) / 5 / (sum(vols[i - 59:i + 1]) / 60)
    wins = sum(1 for t in range(max(0, i - 119), i + 1) if t + 5 <= i and closes[t + 5] / closes[t] - 1 > 0)
    tot = sum(1 for t in range(max(0, i - 119), i + 1) if t + 5 <= i)
    win5 = wins / tot
    up20 = sum(1 for t in range(i - 19, i + 1) if rows[t][0]) and None  # up20需change序列
    ups = 0
    for t in range(i - 19, i + 1):
        chg = (closes[t] / closes[t - 1] - 1) if t > 0 else 0
        ups += 1 if chg > 0 else 0
    up20 = ups / 20
    bias20 = close / ma20 - 1
    tier = ("A" if (ma5 > ma10 > ma20 and close > ma20 > ma60 and ma60 > ma120)
            else ("B" if (ma5 > ma10 > ma20 and close > ma20) else "X"))

    e = EXPECT[code]
    checks = {
        "tier": (tier, "A"),
        "win5": (round(win5, 3), e["win5"]),
        "ret20": (round(ret20, 3), e["ret20"]),
        "ret60": (round(ret60, 3), e["ret60"]),
        "dd250": (round(dd250, 3), e["dd250"]),
        "dd20": (round(worst20, 3), e["dd20"]),
        "bias20": (round(bias20, 3), e["bias20"]),
        "rsi14": (round(rsi, 3), e["rsi"]),
        "vr": (round(vr, 2), e["vr"]),
    }
    bad = [f"{k}:{g}!={x}" for k, (g, x) in checks.items()
           if (g != x if isinstance(x, str) else abs(g - x) > TOL)]
    if bad:
        all_ok = False
    print(f"{code}: tier={tier} win5={win5:.3f} ret20={ret20:.3f} ret60={ret60:.3f} "
          f"dd250={dd250:.3f} dd20={worst20:.3f} rsi={rsi:.1f} vr={vr:.2f} "
          f"n={n} last={rows[-1][0]} -> {'PASS' if not bad else 'FAIL ' + '; '.join(bad)}")
con.close()
print("\nRECHECK:", "ALL PASS" if all_ok else "HAS FAILURES")
