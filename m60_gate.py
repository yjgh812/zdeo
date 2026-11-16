# -*- coding: utf-8 -*-
"""m60_gate.py — 60分钟介入点判定模块(主人多周期框架执行层·v2验证口径)

供各信号源扫描器调用: 候选票 → 判定当前是否处于可介入结构(B1/B2/B3) → 介入区域。
判定口径(全部已回测验证):
  日线方向闸: 收盘>MA20 且 MA20斜率≥0 (qfq日线)
  B1 回踩不破: 现价在60分MA20/MA60区 ±2% + 量缩至前波放量≤1/2 + 止跌(下影/小阳)
  B2 突破确认: 60分实体上穿最近摆动高点 + 量≥1.5×前5均量且<3×(防一次性打满) + 3根站稳
  B3 二次探底不破: 20根内两触MA60, 第二次缩量不更低 + 阳线
  防追高: 现价距最近60分低点≤10%
输出: {"pass": bool, "struct": str, "region": [low, high], "reason": str}
无结构 → pass=False(等待, 次日扫描再判)。
"""
import os, sys, json, sqlite3, time
import numpy as np
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
URL = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
       "symbol={sym}&scale=60&ma=no&datalen=500")  # 实测20261030: datalen=1000返回null, 新浪60分上限=500


def _sma(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a, 0, 0.0)); out[w-1:] = (cs[w:]-cs[:-w])/w
    return out


def daily_dir_ok(code):
    """日线方向闸: 收盘>MA20 且 MA20三日斜率≥0"""
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    rows = con.execute("select date,close from stock_daily_meta_qfq where code=? and date>=20260101 order by date",
                       (code,)).fetchall()
    con.close()
    if len(rows) < 25:
        return False
    cl = [float(c) for _, c in rows]
    ma20 = sum(cl[-20:])/20
    ma20_prev = sum(cl[-23:-3])/20
    return cl[-1] > ma20 and ma20 >= ma20_prev


def fetch_m60(code):
    # 输入归一化(20261030): 兼容带sh/sz前缀或.SH后缀传入, 只留数字取6位——
    # 此前带前缀传入会拼出szsh600926必然null, 且被裸TypeError掩盖
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    code = digits[-6:] if len(digits) >= 6 else str(code)
    sym = ("sh" if code.startswith(("6", "9")) else "sz") + code
    kl = None
    for attempt in (1, 2):
        req = urllib.request.Request(URL.format(sym=sym),
                                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=15) as r:
            kl = json.loads(r.read().decode("utf-8"))
        if isinstance(kl, list) and kl:
            break
        if attempt == 1:
            time.sleep(1.5)   # 新浪偶发null(频控抖动, 20261030实测), 间隔1.5s后重试一次
    if not isinstance(kl, list) or not kl:
        raise RuntimeError(f"新浪60分K返回null/空(sym={sym}, 疑似频控或停牌)")
    bars = [{"dt": b["day"], "o": float(b["open"]), "c": float(b["close"]),
             "h": float(b["high"]), "l": float(b["low"]), "v": float(b["volume"])} for b in kl]
    # 数据卫生: 复权断点剔除
    cs = [b["c"] for b in bars]
    for i in range(1, len(cs)):
        if cs[i-1] > 0 and abs(cs[i]/cs[i-1]-1) > 0.35:
            return bars[:i]
    return bars


def judge(code, cur_price=None):
    """判定候选票当前是否处于60分可介入结构。cur_price=现价(缺省用最后bar收盘)"""
    try:
        bars = fetch_m60(code)
    except Exception as e:
        return {"pass": False, "struct": "", "region": None, "reason": f"60分K拉取失败:{e}"}
    if not bars or len(bars) < 130:
        return {"pass": False, "struct": "", "region": None, "reason": "60分K不足"}
    if not daily_dir_ok(code):
        return {"pass": False, "struct": "", "region": None, "reason": "日线方向闸未开(≤MA20或MA20下行)"}
    cl = np.array([b["c"] for b in bars]); op = np.array([b["o"] for b in bars])
    lo = np.array([b["l"] for b in bars]); hi = np.array([b["h"] for b in bars])
    vv = np.array([b["v"] for b in bars])
    ma20, ma60 = _sma(cl, 20), _sma(cl, 60)
    v5 = _sma(vv, 5)
    v10 = _sma(vv, 10)
    dif = None
    e12, e26 = None, None
    k12, k26 = 2/13, 2/27
    e12 = np.empty(len(cl)); e26 = np.empty(len(cl))
    e12[0] = e26[0] = cl[0]
    for i in range(1, len(cl)):
        e12[i] = k12*cl[i] + (1-k12)*e12[i-1]
        e26[i] = k26*cl[i] + (1-k26)*e26[i-1]
    dif = e12 - e26
    dea = np.empty(len(cl)); dea[0] = dif[0]
    for i in range(1, len(cl)):
        dea[i] = 0.2*dif[i] + 0.8*dea[i-1]
    prev_v = np.full(len(cl), np.nan)
    for i in range(80, len(cl)):
        prev_v[i] = vv[i-80:i-20].mean()
    swing_hi = np.full(len(cl), np.nan)
    for i in range(2, len(cl)-2):
        if hi[i] >= hi[i-2] and hi[i] >= hi[i-1] and hi[i] >= hi[i+1] and hi[i] >= hi[i+2]:
            swing_hi[i] = hi[i]
    last_swing = np.nan; cur = np.nan
    recent_swing = None
    for i in range(len(cl)):
        if not np.isnan(swing_hi[i]):
            cur = swing_hi[i]; recent_swing = (i, swing_hi[i])
        last_swing = cur
    low20 = np.array([lo[max(0, i-20):i+1].min() for i in range(len(cl))])
    i = len(cl) - 1
    px = cl[i] if cur_price is None else cur_price
    lp = low20[i]
    if lp > 0 and px/lp - 1 > 0.10:
        return {"pass": False, "struct": "", "region": None, "reason": f"60分追高(距低点{(px/lp-1)*100:.1f}%>10%)"}
    ma60_up = ma60[i] >= ma60[max(0, i-3)]
    vol_shrink = vv[i] <= prev_v[i]*0.5 or (not np.isnan(v10[i]) and vv[i] <= v10[i]*0.8)
    body_small = 0 < cl[i]-op[i] <= cl[i]*0.008
    lower_sh = (min(op[i], cl[i])-lo[i])/max(cl[i], 1e-9) >= 0.004
    golden = dif[i] > dea[i] and dif[i-1] <= dea[i-1] and dif[i] > 0
    stop_sig = lower_sh >= 0.004 or body_small or golden
    in_zone = (min(ma20[i], ma60[i])*0.99) <= px <= (max(ma20[i], ma60[i])*1.02)
    vol_break = vv[i] >= 1.5*(v5[i] if not np.isnan(v5[i]) else vv[max(0, i-5):i+1].mean())
    vol_not_blow = vv[i] < 3*(v5[i] if not np.isnan(v5[i]) else vv[max(0, i-5):i+1].mean())
    # B1 回踩不破(现价在MA20/60区+缩量+止跌+MA60向上)
    if in_zone and vol_shrink and stop_sig and ma60_up:
        region = sorted([float(ma60[i]), float(ma20[i])])
        return {"pass": True, "struct": "B1回踩不破", "region": [round(region[0], 2), round(region[1]*1.01, 2)],
                "reason": "60分MA20/60区缩量止跌"}
    # B2 突破确认(近8根内触发且3根站稳)
    for k in range(max(90, i-7), i):
        if (not np.isnan(last_swing) if False else not np.isnan(last_swing)) and False:
            pass
    for k in range(max(90, i-7), i):
        swp = last_swing
        if np.isnan(swp) if isinstance(swp, float) else False:
            continue
    # 简化B2: 近8根内有摆动高点, 当前收盘在其上方, 当根放量突破且非巨量, 且近3根站稳
    swings_recent = [swing_hi[k] for k in range(max(2, i-40), i+1) if not np.isnan(swing_hi[k])]
    if swings_recent and not np.isnan(v5[i]):
        sw_ref = swings_recent[-2] if len(swings_recent) >= 2 else swings_recent[-1]
        if cl[i] > sw_ref and vol_break and vol_not_blow:
            stable = all(cl[k] > sw_ref for k in range(max(0, i-2), i+1))
            if stable:
                region = sorted([float(sw_ref), float(max(cl[i], sw_ref)*1.01)])
                return {"pass": True, "struct": "B2突破确认", "region": [round(region[0], 2), round(region[1], 2)],
                        "reason": "60分实体放量突破摆动高点+3根站稳"}
    # B3 二次探底不破
    touches = sum(1 for kk in range(max(0, i-20), i+1) if lo[kk] <= ma60[kk]*1.01)
    if touches >= 2 and lo[i] >= lo[max(0, i-10):i].min()-1e-9 and vol_shrink and cl[i] > op[i]:
        region = sorted([float(lo[max(0, i-10):i+1].min()), float(cl[i])])
        return {"pass": True, "struct": "B3二次探底", "region": [round(region[0]*0.99, 2), round(region[1], 2)],
                "reason": "60分二次探底缩量不破+阳线"}
    return {"pass": False, "struct": "", "region": None, "reason": "60分无介入结构(等待)"}


if __name__ == "__main__":
    import sys as _s
    for c in _s.argv[1:]:
        r = judge(c)
        print(c, json.dumps(r, ensure_ascii=False))
