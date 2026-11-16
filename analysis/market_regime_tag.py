# -*- coding: utf-8 -*-
"""市场状态分层器(主人模拟盘裁判框架·环境分层 20261027) — 零交易权。
规则: 上证指数 60日均线方向 + ADX14(Wilder)
  趋势市up   = ADX>25 且 均线向上(收盘>MA60 且 MA60五日斜率>0)
  下跌市down = 均线向下(收盘<MA60 且 斜率<0) 且 ADX>25
  震荡市range= ADX<20
  过渡trans  = 其余(ADX 20~25 未达趋势定义; 主人三桶定义的未覆盖带, 判定分层时单列)
数据源: tdx vipdoc sh000001 日线(tdx_reader 单一真源)。
输出: data/market_regime_latest.json; CLI --date YYYYMMDD 可查历史日。
"""
import json
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, r"D:\zcodesj")
from tdx_reader import read_day


def _adx14(high, low, close, period=14):
    n = len(close)
    if n < period * 2 + 1:
        return None
    tr = np.maximum(high[1:] - low[1:], np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])))
    plus_dm = np.zeros(n - 1)
    minus_dm = np.zeros(n - 1)
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move[(up_move > down_move) & (up_move > 0)]
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move[(down_move > up_move) & (down_move > 0)]
    atr = np.copy(tr[:period]).sum() / period
    pdm = plus_dm[:period].sum() / period
    mdm = minus_dm[:period].sum() / period
    adx_seq = []
    for i in range(period, n - 1):
        atr = (atr * (period - 1) + tr[i]) / period
        pdm = (pdm * (period - 1) + plus_dm[i]) / period
        mdm = (mdm * (period - 1) + minus_dm[i]) / period
        pdi = 100 * pdm / atr if atr > 0 else 0
        mdi = 100 * mdm / atr if atr > 0 else 0
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0
        adx_seq.append(dx)
    if len(adx_seq) < period:
        return None
    adx = np.mean(adx_seq[:period])
    for dx in adx_seq[period:]:
        adx = (adx * (period - 1) + dx) / period
    return float(adx)


def regime_on(date_int=None):
    rows = read_day(market="sh", code="000001")
    dates = np.array([r[0] for r in rows])
    close = np.array([r[4] for r in rows], float)
    high = np.array([r[2] for r in rows], float)
    low = np.array([r[3] for r in rows], float)
    if date_int is None:
        idx = len(rows) - 1
    else:
        hit = np.where(dates == date_int)[0]
        if len(hit) == 0:
            return {"date": date_int, "regime": "N/A", "reason": "非交易日或无数据"}
        idx = int(hit[0])
    if idx < 65:
        return {"date": int(dates[idx]), "regime": "N/A", "reason": "历史不足"}
    win = close[idx - 59:idx + 1]
    ma60 = float(win.mean())
    ma60_prev = float(close[max(0, idx - 64):idx - 4].mean())
    slope_up = ma60 > ma60_prev
    above = close[idx] > ma60
    adx = _adx14(high[:idx + 1], low[:idx + 1], close[:idx + 1])
    if adx is None:
        return {"date": int(dates[idx]), "regime": "N/A", "reason": "ADX历史不足"}
    if adx > 25 and above and slope_up:
        regime = "趋势市up"
    elif adx > 25 and (not above) and (not slope_up):
        regime = "下跌市down"
    elif adx < 20:
        regime = "震荡市range"
    else:
        regime = "过渡trans"
    return {"date": int(dates[idx]), "close": round(float(close[idx]), 2), "ma60": round(ma60, 2),
            "ma60_slope_up": bool(slope_up), "close_above_ma60": bool(above),
            "adx14": round(adx, 1), "regime": regime}


if __name__ == "__main__":
    date_int = None
    if "--date" in sys.argv:
        date_int = int(sys.argv[sys.argv.index("--date") + 1])
    out = regime_on(date_int)
    out["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if date_int is None:
        json.dump(out, open("data/market_regime_latest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))
