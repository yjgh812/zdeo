r"""slow_bull_playbook_backtest.py — 慢牛完整打法事件式实测（PREREG_SLOWBULL_PLAYBOOK_V1，指纹2f59d351e2f14918）。
P1=慢牛七判据选形+缩量回踩-启动两日买点+三选一退出(有效破30日线/回撤10%/120交易日到期);
P2=同P1删回踩缩量条件(检验缩量边际); P3=同P1退出仅留120日到期(+数据窗末强平, 检验退出纪律边际)。
事件式回测(每股载入全史bar, 按全局交易日索引切片, 逐bar状态机IDLE->WATCH->HOLD), 零交易权。
用法: python -I slow_bull_playbook_backtest.py [run2]
判定逻辑严格按预注册冻结定义实现, 禁止事后调参。
"""
import bisect
import hashlib
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from itertools import groupby

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ST_DB = r"D:\zcodesj\data\st_history.db"
PREREG = r"D:\zcodesj\data\PREREG_SLOWBULL_PLAYBOOK_V1_20261116.json"
START_DATA, SIG_END = "20100101", "20261113"
SPLIT = 20160000
COST = 0.003
MINBARS = 130
EXCL = ("4", "8", "9", "68")
ARMS = ("P1", "P2", "P3")
RB_LO, RB_HI = 2, 20        # 回踩日rb全局交易日偏移窗(D日=第0日)
RE_WIN = 5                  # 介入日re在rb后1~5个全局交易日
PEAK_UP, PEAK_DN = 0.97, 0.93   # 回踩幅度带: 距D以来峰高3%~7%
VOL_SHRINK = 0.8            # 缩量阈值(P2删除该条件)
HOLD_EXIT = 119             # 到期: 买入日全局偏移+119(买入日=第1个交易日)
DD_EXIT = 0.10              # 回撤退出阈值
BRK_SOFT = 0.97             # 有效破位倍数(收盘<MA30×0.97)
REC_WIN = 2                 # 破位收复检查全局交易日窗
OUT1 = r"D:\zcodesj\reports\slow_bull_playbook_bt_20100101_20261113.json"
OUT2 = r"D:\zcodesj\reports\slow_bull_playbook_bt_20100101_20261113_run2.json"


def st_loader():
    con = sqlite3.connect("file:%s?mode=ro" % ST_DB, uri=True)
    cache = {}

    def state(code, date):
        if code not in cache:
            pfx = "sh" if code.startswith("6") else "sz"
            rows = con.execute("SELECT date,is_st FROM st_daily WHERE code=? ORDER BY date",
                               (pfx + "." + code,)).fetchall()
            cache[code] = rows
        rows = cache[code]
        lo, hi = 0, len(rows)
        while lo < hi:
            mid = (lo + hi) // 2
            if rows[mid][0] <= date:
                lo = mid + 1
            else:
                hi = mid
        return rows[lo - 1][1] if lo > 0 else 0
    return state


def linreg(ys):
    n = len(ys)
    mt = (n - 1) / 2.0
    my = sum(ys) / n
    sxy = sxx = syy = 0.0
    for t, y in zip(range(n), ys):
        dx = t - mt
        dy = y - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    slope = sxy / sxx if sxx > 0 else 0.0
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    return slope, r2


def slow_bull7(c, c121, vals61, vals70, chgs):
    """慢牛七判据, 与slow_bull_backtest.py(149冻结口径)逐字同源.
    输入: 信号日收盘c, c[-121]收盘, close[-61:]共61值, close[-70:]共70值, 近60根bar的change_pct."""
    ma20 = sum(vals61[-20:]) / 20.0
    ma60 = sum(vals61[-60:]) / 60.0
    if not (c > ma20 > ma60):
        return False
    if not (ma60 > sum(vals70[:-10]) / 60.0):
        return False
    if not (c / c121 - 1.0 > 0.0):
        return False
    r60 = c / vals61[0] - 1.0
    if not (0.10 <= r60 <= 0.60):
        return False
    peak = vals61[0]
    dd = 0.0
    for v in vals61:
        if v > peak:
            peak = v
        d0 = 1.0 - v / peak
        if d0 > dd:
            dd = d0
    if not (dd < 0.12):
        return False
    for x in chgs:
        if x is None or x > 9.5 or x < -6.0:
            return False
    if sum(1 for x in chgs if abs(x) > 5.0) > 3:
        return False
    if c / max(vals61) < 0.97:
        return False
    slope, r2 = linreg([math.log(v) for v in vals61])
    if r2 < 0.75:
        return False
    dpct = (math.exp(slope) - 1.0) * 100.0
    ang = math.degrees(math.atan(dpct))
    if not (18.0 <= ang <= 45.0):
        return False
    return True


def run_arm(arm, code, bars, is_st, bcum, bd, out):
    """单臂事件式状态机. bars=[(date_int, gd, o, c, v, chg)], gd=全局交易日索引."""
    n = len(bars)
    if n < MINBARS:
        return
    shrink = (arm != "P2")     # P2 删缩量条件
    use_exit = (arm != "P3")   # P3 删破位/回撤退出, 仅留到期+强平
    dates = [b[0] for b in bars]
    gds = [b[1] for b in bars]
    closes = [b[3] for b in bars]
    opens = [b[2] for b in bars]
    vols = [b[4] for b in bars]
    chgs = [b[5] for b in bars]

    def ma30_at(i):
        w = closes[i - 29:i + 1]
        if any(x is None or x <= 0 for x in w):
            return None
        return sum(w) / 30.0

    def do_sell(si, sc, reason):
        """si=卖出bar序, sc=卖出收盘价. 基准=同买卖日窗全池等权曲线同扣双边费."""
        sp = sc * (1.0 - COST)
        bp = bars_buyc * (1.0 + COST)
        net = sp / bp - 1.0
        k_s = bisect.bisect_right(bd, dates[si]) - 1
        k_b = bisect.bisect_right(bd, dates[bars_buyi]) - 1
        bench_raw = bcum[k_s] / bcum[k_b] - 1.0 if k_s >= 0 and k_b >= 0 else 0.0
        net_bench = (1.0 + bench_raw) * (1.0 - COST) / (1.0 + COST) - 1.0
        out.append({"code": code, "buy": dates[bars_buyi], "sell": dates[si], "reason": reason,
                    "net": round(net * 100.0, 4), "bench": round(net_bench * 100.0, 4),
                    "ex": round((net - net_bench) * 100.0, 4),
                    "hold": gds[si] - gds[bars_buyi]})

    i = MINBARS - 1
    state = "I"
    D_i = peakD = rb_i = rb_gd = rb_vol = None
    bars_buyi = bars_buygd = bars_buyc = peak_h = brk_i = brk_gd = None
    while i < n:
        gd = gds[i]
        c = closes[i]
        if state == "I":
            # setup判定: 七判据 + 硬过滤(价格带/PIT ST/bars>=130)
            if c is not None and c > 0 and 2.0 <= c <= 30.0:
                if dates[i] >= SPLIT and is_st(code, dates[i]):
                    i += 1
                    continue
                ok = True
                for j in (i - 120,):
                    if closes[j] is None or closes[j] <= 0:
                        ok = False
                        break
                if ok:
                    vals61 = closes[i - 60:i + 1]
                    if any(v is None or v <= 0 for v in vals61):
                        ok = False
                if ok:
                    vals70 = closes[i - 69:i + 1]
                    if any(v is None or v <= 0 for v in vals70):
                        ok = False
                if ok:
                    chgs60 = chgs[i - 59:i + 1]
                    if slow_bull7(c, closes[i - 120], vals61, vals70, chgs60):
                        state = "W"
                        D_i = i
                        peakD = c
                        rb_i = None
                        i += 1
                        continue
            i += 1
        elif state == "W":
            if c is None or c <= 0:
                i += 1
                continue
            if rb_i is None:
                if gd - gds[D_i] > RB_HI:
                    state = "I"           # 观察窗超限作废, 次bar恢复扫描
                    i += 1
                    continue
                is_rb = False
                if peakD is not None and c <= peakD * PEAK_UP and c >= peakD * PEAK_DN:
                    m30 = ma30_at(i)          # 回踩日关键位不破(MA30), P1/P2均保留
                    if m30 is not None and c > m30:
                        if not shrink:
                            is_rb = True      # P2: 删量能条件
                        else:
                            vv = vols[i - 5:i]
                            if all(x is not None for x in vv) and vols[i] is not None:
                                if vols[i] < VOL_SHRINK * (sum(vv) / 5.0):
                                    is_rb = True
                if is_rb:
                    rb_i = i
                    rb_gd = gd
                    rb_vol = vols[i]
                peakD = max(peakD, c)     # 判定用旧峰, 判定后更新
                i += 1
            else:
                if gd - gds[D_i] > RB_HI or gd - rb_gd > RE_WIN:
                    state = "I"           # re未现, 作废
                    i += 1
                    continue
                o, v = opens[i], vols[i]
                pc = closes[i - 1]
                if o is not None and c is not None and v is not None and pc is not None and c > 0:
                    if c > o and c > pc and v > rb_vol:
                        state = "H"
                        bars_buyi = i
                        bars_buygd = gd
                        bars_buyc = c
                        peak_h = c
                        brk_i = None
                        brk_gd = None
                i += 1
        else:  # state == "H"
            if c is None or c <= 0:
                # 无有效bar: 到期顺延; 破位观察跨窗(触发+2全局日内无bar)以触发日收盘卖
                if brk_i is not None and gd > brk_gd + REC_WIN:
                    do_sell(brk_i, closes[brk_i], "break30_timeout")
                    state = "I"
                    brk_i = None
                i += 1
                continue
            # ③到期(优先)
            if gd - bars_buygd >= HOLD_EXIT:
                do_sell(i, c, "expire")
                state = "I"
                i += 1
                continue
            # ①有效破30日线
            if use_exit:
                m30 = ma30_at(i)
                if m30 is not None:
                    if brk_i is None:
                        if c < m30 * BRK_SOFT:
                            do_sell(i, c, "break30_hard")
                            state = "I"
                            i += 1
                            continue
                        if c < m30:
                            brk_i = i
                            brk_gd = gd
                    else:
                        if c >= m30:
                            brk_i = None          # 收复取消
                        elif c < m30 * BRK_SOFT:
                            do_sell(i, c, "break30_hard")
                            state = "I"
                            brk_i = None
                            i += 1
                            continue
                        elif gd - brk_gd >= REC_WIN:
                            do_sell(brk_i, closes[brk_i], "break30_timeout")
                            state = "I"
                            brk_i = None
                            i += 1
                            continue
            # ②持有期回撤>=10%
            if use_exit:
                if c / peak_h - 1.0 <= -DD_EXIT:
                    do_sell(i, c, "drawdown10")
                    state = "I"
                    i += 1
                    continue
            peak_h = max(peak_h, c)
            i += 1
    # ④数据窗末强平
    if state == "H":
        for j in range(n - 1, bars_buyi, -1):
            if closes[j] is not None and closes[j] > 0:
                do_sell(j, closes[j], "force_end")
                break


def seg_stats(trs, lo, hi, judge):
    xs = [t for t in trs if lo <= t["buy"] < hi]
    n = len(xs)
    nets = [t["net"] for t in xs]
    exs = [t["ex"] for t in xs]
    if n == 0:
        return {"n_trades": 0, "verdict": "无交易"}
    mu = sum(nets) / n
    sd = (sum((x - mu) ** 2 for x in nets) / n) ** 0.5
    t = mu / (sd / n ** 0.5) if sd > 0 else None
    mu_ex = sum(exs) / n
    win = sum(1 for x in nets if x > 0) / n
    avg_hold = sum(x["hold"] for x in xs) / n
    max_loss = min(nets)
    reasons = defaultdict(int)
    for x in xs:
        reasons[x["reason"]] += 1
    if not judge:
        vd = "仅报告(样本不足)" if n < 30 else "仅报告"
    elif n < 100:
        vd = "交易数<100判据不满足=观察不采信"
    elif mu > 0 and t is not None and t >= 2.0 and mu_ex > 0:
        vd = "PASS_candidate"
    elif mu < 0 and t is not None and t <= -2.0:
        vd = "FAIL"
    else:
        vd = "观察不采信"
    return {"n_trades": n, "mean_net": round(mu, 4),
            "t_net": None if t is None else round(t, 2),
            "win_rate": round(win, 4), "avg_hold_days": round(avg_hold, 1),
            "max_loss": round(max_loss, 4), "mean_ex": round(mu_ex, 4),
            "exit_reasons": dict(reasons), "verdict": vd}


def year_stats(trs, lo, hi):
    ys = defaultdict(list)
    for t in trs:
        if lo <= t["buy"] < hi:
            ys[str(t["buy"])[:4]].append(t)
    return [{"year": y, "n": len(v),
             "mean_net": round(sum(x["net"] for x in v) / len(v), 4),
             "mean_ex": round(sum(x["ex"] for x in v) / len(v), 4)}
            for y, v in sorted(ys.items())]


def main():
    t0 = time.time()
    raw = open(PREREG, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace(('  "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b'  "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    fp_ok = fp == prereg["fingerprint"]
    print("PREREG指纹校验: %s 匹配=%s" % (fp, fp_ok))

    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    all_dates = [int(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? AND date<=? ORDER BY date",
        (START_DATA, SIG_END))]
    gidx = {d: i for i, d in enumerate(all_dates)}
    is_st = st_loader()

    # pass1 基准: 全池(剔前缀, 按日剔ST)等权日收益累乘 (同FIP模板)
    bench = defaultdict(lambda: [0.0, 0])
    cur = con.execute("SELECT code,date,close FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        if code.startswith(EXCL):
            continue
        prev = None
        for _, d, c in grp:
            d = int(d)
            if c is None or prev is None or prev <= 0:
                prev = c if c is not None else prev
                continue
            if not is_st(code, d):
                bench[d][0] += c / prev - 1.0
                bench[d][1] += 1
            prev = c
    bd = sorted(bench)
    bcum, cc = [], 1.0
    for d in bd:
        s, nb = bench[d]
        cc *= (1 + s / nb if nb else 0.0)
        bcum.append(cc)
    del bench
    print("pass1完成 %.0fs 基准日%d 全局交易日%d" % (time.time() - t0, len(bd), len(all_dates)))

    # pass2 事件式扫描: 每股一次载入全史, 三臂各自状态机
    trades = {a: [] for a in ARMS}
    cur = con.execute("SELECT code,date,open,close,volume,change_pct FROM stock_daily_meta_qfq "
                      "WHERE date>=? AND date<=? ORDER BY code,date", (START_DATA, SIG_END))
    nstock = 0
    for code, grp in ((k, list(g)) for k, g in groupby(cur, key=lambda r: r[0])):
        if code.startswith(EXCL):
            continue
        bars = []
        for _, d, o, c, v, chg in grp:
            gd = gidx.get(int(d))
            if gd is None:
                continue
            bars.append((int(d), gd, o, c, v, chg))
        nstock += 1
        for arm in ARMS:
            run_arm(arm, code, bars, is_st, bcum, bd, trades[arm])
    con.close()
    print("pass2完成 %.0fs 扫描股票%d" % (time.time() - t0, nstock))

    results = {}
    for arm in ARMS:
        p = seg_stats(trades[arm], 20160000, 20990000, arm == "P1")  # 仅P1有PASS/FAIL判据, P2/P3边际检验仅报告
        h = seg_stats(trades[arm], 20100000, 20160000, False)
        results[arm] = {"primary": p, "holdout": h,
                        "years_primary": year_stats(trades[arm], 20160000, 20990000),
                        "years_holdout": year_stats(trades[arm], 20100000, 20160000)}
        print("%s: 主窗 n=%s 笔均净=%s%% t=%s 胜率=%s 平均持有=%s日 笔均超额=%s%% | 留出窗 n=%s 笔均净=%s%% | 判定=%s" % (
            arm, p["n_trades"], p["mean_net"], p["t_net"], p["win_rate"],
            p["avg_hold_days"], p["mean_ex"], h["n_trades"], h["mean_net"], p["verdict"]))
    print("机械判定(P1主窗): %s" % results["P1"]["primary"]["verdict"])
    print("P2边际(主窗判定仅报告): %s | P3边际(主窗判定仅报告): %s" % (
        results["P2"]["primary"]["verdict"], results["P3"]["primary"]["verdict"]))
    out = {"prereg_fp": fp, "fp_ok": fp_ok, "window": [START_DATA, SIG_END],
           "split": SPLIT, "cost": COST, "min_bars": MINBARS,
           "n_scanned_stocks": nstock, "arms": results}
    path = OUT2 if len(sys.argv) > 1 and sys.argv[1] == "run2" else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写入", path, " %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
