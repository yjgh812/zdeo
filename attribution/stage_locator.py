r"""阶段定位器（零交易权，只读 qfq）。
方法来源：主人 2026-11-10 令「搞清楚选的股票在吸筹/洗盘/震仓/拉升哪一阶段 + 介入/撤离时点」。
规则真源（全部引用既有验证档案，不新增交易语义）：
  涨停锚=吸筹确认(生产扫描器S1)；缩量回踩不破锚=洗盘(S3/S4)；
  震仓判别=PREREG_SHOCK_RECOVERY_20261110(569样本PASS_CANDIDATE, 未接线)；
  拉升中段禁入=G2急涨否决线(锚上+10%)；破锚=结构死(S4)；-8%止损=宪法S-5；
  吸筹特征/派发预警=描述性规则，显式标注 UNVERIFIED，仅观察标记。
用法：python attribution/stage_locator.py 600570,600584,600754,600779
"""
import json
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT_DIR = r"D:\zcodesj\reports"

G2_OVERHEAT_PCT = 10.0      # 锚上+10% = 拉升中段(G2急涨否决线)
ENTRY_ZONE = 1.03           # [anchor, anchor*1.03] 洗盘入场区(扫描器S4)
SHOCK_CHG = -3.0            # 震仓事件: 当日跌幅<=-3%
SHOCK_LOW = 0.96            # 盘中最深<=前收*0.96
STOP_LOSS = -0.08           # 宪法S-5硬止损(仅对持仓票用成本价算)


def turnover_band(fconn, code, date_str):
    """近3日换手率均值分带 (主人框架2026-11-10第二份实战资料的量化增量, UNVERIFIED仅观察标记)。
    带: <3%死水 / 3-8%温和 / 8-15%起爆 / 15-20%高热 / >20%派发预警。"""
    ts = str(code) + (".SH" if str(code).startswith("6") else ".SZ")
    rows = fconn.execute("SELECT turnover_rate FROM daily_basic WHERE ts_code=? AND trade_date<=? "
                         "ORDER BY trade_date DESC LIMIT 3", (ts, date_str)).fetchall()
    vals = [r[0] for r in rows if r[0] is not None]
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    if avg < 3:
        band = "T0_死水(<3%)"
    elif avg < 8:
        band = "T1_温和(3-8%)"
    elif avg <= 15:
        band = "T2_起爆(8-15%)"
    elif avg <= 20:
        band = "T3_高热(15-20%)"
    else:
        band = "T4_异常(>20%派发预警)"
    return {"avg3d_pct": round(avg, 2), "band": band, "unverified": True}


def load_bars(conn, code, ndays=90):
    rows = conn.execute("SELECT date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
                        "WHERE code=? ORDER BY date DESC LIMIT ?", (code, ndays)).fetchall()
    bars = [r for r in reversed(rows) if (r[5] is not None and r[5] > 0 and r[2] and r[2] > 0)]
    return bars


def find_anchor(bars, code):
    """近30交易日最近一次涨停锚, 返回bar索引或None。"""
    n = len(bars)
    if n < 20:
        return None
    thr = 19.8 if str(code).startswith(("300", "301", "302")) else 9.8
    for bi in range(n - 2, max(n - 32, -1), -1):
        d, o, h, l, c, v, chg = bars[bi]
        if chg is None or v is None or v <= 0 or c is None or h is None:
            continue
        if chg >= thr and c >= h * 0.999:
            return bi
    return None


def locate(code, cost=None, fconn=None, asof=None):
    """asof=YYYYMMDD 可选回放截面(仅截断bars至该日, 判定逻辑零变化; 供监视线回归验证)"""
    conn = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    bars = load_bars(conn, code)
    conn.close()
    if asof is not None:
        bars = [b for b in bars if str(b[0]) <= str(asof)]
    s = str(code)
    if len(bars) < 20:
        return {"code": code, "stage": "NODATA", "detail": "bars<20"}
    T = bars[-1]
    close = T[4]
    anchor_i = find_anchor(bars, code)
    out = {"code": code, "date": T[0], "close": close}

    def pct_vs(a, b):
        return round((close / b - 1.0) * 100, 2) if b else None

    # 破锚 / 位置区间
    if anchor_i is None:
        # 阶段0/1: 吸筹特征(UNVERIFIED描述性)
        seg = bars[-20:]
        hi = max(b[2] for b in seg)
        lo = min(b[3] for b in seg)
        med = sorted(b[4] for b in seg)[10]
        rng = (hi - lo) / med if med else 1
        v5 = sum(b[5] for b in bars[-5:]) / 5.0
        v60 = sum(b[5] for b in bars[-60:]) / max(len(bars[-60:]), 1)
        absorb = (rng < 0.12) and (v60 > 0 and v5 / v60 < 0.7)
        out["stage"] = "S1_吸筹观察" if absorb else "S0_无结构"
        out["absorb_unverified"] = True
        out["rng20"] = round(rng * 100, 1)
        out["vol5_vs_vol60"] = round(v5 / v60, 2) if v60 else None
        if fconn is not None:
            out["turnover"] = turnover_band(fconn, code, T[0])
        return out
    ad, ao, ah, al, ac, av, acg = bars[anchor_i]
    out["anchor_date"] = ad
    out["anchor"] = ac
    out["vs_anchor_pct"] = pct_vs(close, ac)
    # 锚日放量倍数(主人框架2026-11-10第四份: 突破日量>=5日均量1.5-2倍为合格突破, UNVERIFIED标记)
    seg5 = bars[max(anchor_i - 5, 0):anchor_i]
    v5b = sum(b[5] for b in seg5) / len(seg5) if seg5 else None
    out["anchor_vol_x"] = round(av / v5b, 2) if (v5b and v5b > 0) else None
    # 撤离判据优先: 破锚
    if close < ac:
        out["stage"] = "S5x_破锚走坏"
        out["action"] = "EXIT(结构死, 卖出须问主人)"
        return out
    # G2超热
    out["chg5d"] = pct_vs(close, bars[-6][4]) if len(bars) >= 6 else None
    out["overheat_g2"] = (out["chg5d"] is not None and out["chg5d"] > G2_OVERHEAT_PCT)
    # 震仓事件检测(锚后至T)
    shock_i = None
    for k in range(anchor_i + 1, len(bars)):
        if len(bars) - 1 - k > 3:  # 时效窗: 只判距今<=3交易日的急跌事件(旧事件已过判别窗口)
            continue
        w = bars[k]
        if w[6] is None:
            continue
        if w[6] <= SHOCK_CHG and w[3] <= bars[k - 1][4] * SHOCK_LOW and w[4] >= ac:
            shock_i = k
            break
    # 派发预警(UNVERIFIED): 放量阴线或放量滞涨
    vol_ma5 = sum(b[5] for b in bars[-6:-1]) / 5.0
    dist_warning = (T[5] > vol_ma5 * 1.5) and ((T[4] < T[1]) or abs(T[6] or 0) < 1.0)
    if shock_i is not None:
        E = bars[shock_i]
        out["shock_date"] = E[0]
        out["shock_chg"] = E[6]
        out["stop_ref"] = round(E[3], 2)  # 框架口径: 止损设震仓低点下方(收盘跌破=撤离评估)
        if shock_i == len(bars) - 1:
            out["stage"] = "S3_震仓当日(等次日判别)"
            out["action"] = "WATCH(急跌未破锚, 次日收复判别: 收复=持有, 未收复=离场判别)"
        elif shock_i < len(bars) - 1:
            e1 = bars[shock_i + 1]
            rec = (e1[4] > e1[1]) and (e1[4] > E[3])
            out["stage"] = "S3a_震仓已收复" if rec else "S3b_震仓未收复"
            out["action"] = ("HOLD(收复=假摔, 持有纪律)" if rec
                             else "EXIT_CANDIDATE(未收复组十年均值-4.08%/胜率34%, 撤离评估, 卖出须问主人)")
    if shock_i is None:
        # 位置区间(仅在无震仓事件时判定); G2超热优先于位置
        if out.get("overheat_g2"):
            out["stage"] = "S4b_拉升中段(G2超热)"
            out["action"] = "NO_ENTRY(追高否决) 持有者=派发监控"
        elif close <= ac * ENTRY_ZONE:
            out["stage"] = "S2_洗盘入场区"
            out["action"] = "WATCH(入场区, 等阳线确认[收阳+不创新低]再介入)"
            out["stop_ref"] = ac  # 框架口径: 收盘跌破锚=结构死(S5x同口径)
        elif close > ac * (1 + G2_OVERHEAT_PCT / 100.0):
            out["stage"] = "S4b_拉升中段(锚上超10%)"
            out["action"] = "NO_ENTRY(追高否决) 持有者=派发监控"
        else:
            out["stage"] = "S4a_拉升启动段"
            out["action"] = "HOLD(启动段) 新仓介入需主人特批(追高区)"
            out["stop_ref"] = round(ac, 2)  # 锚下=结构死; 框架另建议5日线(未接, 差异声明)
    # 止损/派发标记
    out["dist_warning"] = bool(dist_warning)
    if cost:
        out["pnl_pct"] = round((close / cost - 1) * 100, 2)
        out["stop_hit"] = out["pnl_pct"] <= STOP_LOSS * 100
    out["price_over_30"] = close > 30.0
    if fconn is not None:
        out["turnover"] = turnover_band(fconn, code, T[0])
    return out


if __name__ == "__main__":
    args = sys.argv[1] if len(sys.argv) > 1 else ""
    cost_map = {}
    if len(sys.argv) > 2 and os.path.exists(sys.argv[2]):
        with open(sys.argv[2], encoding="utf-8") as f:
            cost_map = json.load(f)
    codes = [c.strip()[-6:] for c in args.split(",") if c.strip()]
    results = []
    fconn = None
    try:
        fconn = sqlite3.connect("file:%s?mode=ro" % FUND_DB, uri=True)
    except sqlite3.Error:
        fconn = None
    for c in codes:
        try:
            results.append(locate(c, cost=cost_map.get(str(c)[-6:]), fconn=fconn))
        except Exception as e:
            results.append({"code": c, "stage": "ERROR", "detail": repr(e)})
    if fconn is not None:
        fconn.close()
    with open(os.path.join(OUT_DIR, "stage_locate_%s.json" % time.strftime("%Y%m%d")), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    print("定位完成 %d 只 -> reports/stage_locate_%s.json" % (len(results), time.strftime("%Y%m%d")))
