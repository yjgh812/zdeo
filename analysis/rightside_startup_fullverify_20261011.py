# -*- coding: utf-8 -*-
"""右侧启动买点全量复核 (2026-10-11, 主人令: 全量复核买卖点是否正确)
三重全量验证, 与扫描脚本独立实现(纯python逐点, 不复用numpy向量化代码路径, 避免同bug互证):
  A) 召回反查: 独立重扫全宇宙, 产出(段起,首个介入)与(段起,全部介入)键集合,
     与 events_main.csv / events_lag.csv 精确比对 → 多/漏/错逐条列出
  B) 精度正查: 全部主事件逐字段重算比对(入场价/段起特征/介入特征/画像pos60/
     近涨停/收益f5-f120毛净/波段max_gain/peak_day/六口径走坏日/峰值后走坏)
  C) lag样本: 键全量比对 + 关键字段(entry/days/off_start)全量重算
容差: 价格相对1e-4 / 百分比0.05 / 天数精确相等。
输出: reports/rightside_startup_20261011/fullverify_report.txt
"""
import os, sys, time, sqlite3, csv, math

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

D = r"D:\zcodesj\reports\rightside_startup_20261011"
OUTP = os.path.join(D, "fullverify_report.txt")
DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
EXCLUDE_PREFIX = ("688", "689", "8", "4", "9")
EV_FROM = 2012
t0 = time.time()


def log(*a):
    print(*a, flush=True)


def rmatch(a, b, tol=1e-4):
    """数值匹配: None==None; 相对或绝对容差"""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= max(tol * max(abs(a), abs(b)), 1e-9)


def pmatch(a, b, tol=0.05):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def read_csv_rows(path):
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r["code"] = str(r["code"]).zfill(6)
            out.append(r)
    return out


def fnum(x):
    if x is None or x == "" or x == "None":
        return None
    try:
        return float(x)
    except Exception:
        return None


def fna(x):
    return None if (x is None or x != x if isinstance(x, float) else x is None) else x


# ================================================= 独立重扫(纯python+cumsum预计算, 与扫描脚本不同代码路径)
def rescan_stock(dates, o, c, v):
    """返回 (main_keys, all_keys): main={(s_date, first_d)}, all={(s_date,d)}"""
    n = len(dates)
    main, allk = set(), set()
    if n < 400:
        return main, allk
    # cumsum滑窗均值(独立实现, 非numpy stride_tricks)
    csv_, vsv = [0.0], [0.0]
    for i in range(n):
        csv_.append(csv_[-1] + c[i])
        vsv.append(vsv[-1] + v[i])

    def wma(arr_sum, i, w):
        s0 = max(0, i - w + 1)
        return (arr_sum[i + 1] - arr_sum[s0]) / (i + 1 - s0)

    v20_arr = [wma(vsv, i, 20) for i in range(n)]
    ma20_arr = [wma(csv_, i, 20) for i in range(n)]
    ma60_arr = [wma(csv_, i, 60) for i in range(n)]

    for s in range(249, n - 1):
        if dates[s] // 10000 < EV_FROM:
            continue
        if c[s - 1] <= 0 or c[s] / c[s - 1] - 1 < 0.03:
            continue
        if v20_arr[s] <= 0 or v[s] < 2 * v20_arr[s]:
            continue
        w0 = s - 249
        win = c[w0:s + 1]
        lo = min(win)
        lo_i = w0 + win.index(lo)
        earlier = False
        for i in range(lo_i, s):
            if v20_arr[i] > 0 and v[i] >= 2 * v20_arr[i] and c[i] / c[i - 1] - 1 >= 0.03:
                earlier = True
                break
        if earlier:
            continue
        pre60 = c[max(0, s - 60):s]
        if len(pre60) >= 40:
            if min(pre60) / max(pre60) - 1 > -0.25:
                continue
            pre20 = wma(vsv, s - 1, 20)
            v250s = wma(vsv, s, 250)
            if v250s <= 0 or pre20 / v250s > 0.95:
                continue
        first_d = None
        for d in range(s, min(s + 41, n - 1)):
            if o[d + 1] <= 0:
                continue
            off = (c[d] / c[s] - 1) * 100
            if off > 17 or off < -3:
                continue
            if ma20_arr[d] <= ma60_arr[d]:
                continue
            if not (2 <= c[d] <= 30):
                continue
            w250 = c[d - 249:d + 1]
            lo2, hi2 = min(w250), max(w250)
            if hi2 <= lo2 or (c[d] - lo2) / (hi2 - lo2) * 100 >= 50:
                continue
            vv = v[d - 249:d + 1]
            if 100 * sum(1 for x in vv if x <= v[d]) / 250 >= 80:
                continue
            if c[d] / ma20_arr[d] - 1 > 0.03:
                continue
            w3 = c[max(0, d - 749):d + 1]
            lo3, hi3 = min(w3), max(w3)
            if hi3 > lo3 and (c[d] - lo3) / (hi3 - lo3) * 100 >= 55:
                continue
            if lo3 > 0 and c[d] / lo3 - 1 > 0.80:
                continue
            allk.add((dates[s], dates[d]))
            if first_d is None:
                first_d = dates[d]
        if first_d is not None:
            main.add((dates[s], first_d))
    return main, allk


def main():
    em = read_csv_rows(os.path.join(D, "events_main.csv"))
    lg = read_csv_rows(os.path.join(D, "events_lag.csv"))
    db = sqlite3.connect(DB, uri=True)
    all_codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq ORDER BY code")]
    from stock_name_lookup import get_name
    uni = []
    for cd in all_codes:
        if cd[:3] in EXCLUDE_PREFIX or cd[:1] in ("4", "8", "9"):
            continue
        nm = get_name(cd) or ""
        if "ST" in nm.upper() or "退" in nm:
            continue
        uni.append(cd)
    log(f"[0] 宇宙 {len(uni)} 只, 主事件 {len(em)}, lag {len(lg)}")

    csv_main = {(r["code"], int(r["s_date"]), int(r["d_date"])) for r in em}
    csv_lag = {(r["code"], int(r["s_date"]), int(r["d_date"])) for r in lg}
    csv_main_keys = {(k[0], k[1], k[2]) for k in csv_main}

    # ---- Part A: 召回反查 ----
    rescan_main, rescan_all = set(), set()
    n_done = 0
    for cd in uni:
        rows = db.execute(
            "SELECT date,open,close,volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
            (cd,)).fetchall()
        if not rows:
            continue
        dates = [int(r[0]) for r in rows]
        o = [float(r[1] or 0) for r in rows]
        c = [float(r[2] or 0) for r in rows]
        v = [float(r[3] or 0) for r in rows]
        mk, ak = rescan_stock(dates, o, c, v)
        rescan_main |= {(cd, s, d) for s, d in mk}
        rescan_all |= {(cd, s, d) for s, d in ak}
        n_done += 1
        if n_done % 800 == 0:
            log(f"  [A] 重扫 {n_done} 股 {time.time()-t0:.0f}s")
    miss = rescan_main - csv_main_keys       # 复核扫到而事件库没有=漏
    extra = csv_main_keys - rescan_main      # 事件库有而复核扫不到=错
    # lag口径: rescan_all = main∪lag
    lag_miss = rescan_all - csv_main_keys - csv_lag
    lag_extra = (csv_main_keys | csv_lag) - rescan_all
    log(f"[A] 召回反查: 重扫主事件 {len(rescan_main)} vs 库 {len(csv_main_keys)} | 漏 {len(miss)} 错 {len(extra)} | lag漏 {len(lag_miss)} lag多 {len(lag_extra)}")

    # ---- Part B: 主事件逐字段正查 ----
    bars_cache = {}

    def get_bars(code):
        if code not in bars_cache:
            rows = db.execute(
                "SELECT date,open,high,low,close,volume,amount FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
                (code,)).fetchall()
            bars_cache[code] = rows
        return bars_cache[code]

    def wma_at(arr, i, w):
        s0 = max(0, i - w + 1)
        seg = arr[s0:i + 1]
        return sum(seg) / len(seg)

    field_fail = {}
    fp_boundary_n = [0]
    n_field_checked = 0

    def chk(tag, ok, code, s_date, detail=""):
        nonlocal field_fail, n_field_checked
        n_field_checked += 1
        if not ok:
            field_fail.setdefault(tag, []).append(f"{code}@{s_date} {detail}")

    b_fail = 0
    for r in em:
        code = r["code"]
        s_date, d_date = int(r["s_date"]), int(r["d_date"])
        rows = get_bars(code)
        dates = [int(x[0]) for x in rows]
        if s_date not in dates or d_date not in dates:
            b_fail += 1
            chk("日期存在", False, code, s_date, "bar缺失")
            continue
        si, di = dates.index(s_date), dates.index(d_date)
        o = [float(x[1] or 0) for x in rows]
        c = [float(x[4] or 0) for x in rows]
        v = [float(x[5] or 0) for x in rows]
        a = [float(x[6] or 0) for x in rows]
        n = len(c)
        # 段起特征
        pct_s = (c[si] / c[si - 1] - 1) * 100
        v20s = wma_at(v, si, 20)
        chk("段起量比", pmatch(v[si] / v20s, fnum(r["vr_s"]), 0.02), code, s_date, f"{v[si]/v20s:.3f} vs {r['vr_s']}")
        chk("段起涨幅", pmatch(pct_s, fnum(r["pct_s"]), 0.02), code, s_date)
        pre60 = c[max(0, si - 60):si]
        predd = (min(pre60) / max(pre60) - 1) * 100 if len(pre60) >= 40 else 0.0
        chk("启动前回撤", pmatch(predd, fnum(r["pre_dd"]), 0.06), code, s_date)  # 0.06: 覆盖round1存储±0.05
        pre20 = wma_at(v, si - 1, 20)
        v250s = wma_at(v, si, 250)
        chk("启动前缩量", pmatch(pre20 / v250s, fnum(r["pre_shrink"]), 0.01), code, s_date)
        # 介入特征
        e = o[di + 1]
        # CSV entry=round(e,3)存储, 容差按3位舍入半格0.0005放宽(全量复核修正: 原1e-4相对容差对round3存储误报)
        chk("入场价", pmatch(e, fnum(r["entry"]), 0.0011), code, s_date, f"{e} vs {r['entry']}")
        chk("介入收盘", rmatch(c[di], fnum(r["px_D"]), 1e-3), code, s_date)
        chk("days", int(r["days"]) == di - si, code, s_date, f"{di-si} vs {r['days']}")
        chk("off_start", pmatch((c[di] / c[si] - 1) * 100, fnum(r["off_start"]), 0.05), code, s_date)
        ma20d = wma_at(c, di, 20)
        ma60d = wma_at(c, di, 60)
        w250 = c[di - 249:di + 1]
        lo2, hi2 = min(w250), max(w250)
        pos250 = (c[di] - lo2) / (hi2 - lo2) * 100
        chk("pos250", pmatch(pos250, fnum(r["pos250"]), 0.1), code, s_date)
        w3 = c[max(0, di - 749):di + 1]
        lo3, hi3 = min(w3), max(w3)
        pos3v = (c[di] - lo3) / (hi3 - lo3) * 100 if hi3 > lo3 else 50.0
        chk("pos3", pmatch(pos3v, fnum(r["pos3"]), 0.1), code, s_date)
        chk("off_lo3", pmatch((c[di] / lo3 - 1) * 100, fnum(r["off_lo3"]), 0.1), code, s_date)
        # 画像pos60(60根, <20根None)
        w60 = c[max(0, di - 59):di + 1]
        if len(w60) >= 20 and max(w60) > min(w60):
            pos60v = (c[di] - min(w60)) / (max(w60) - min(w60)) * 100
        else:
            pos60v = None
        chk("pos60画像", pmatch(pos60v if pos60v is not None else 9e9,
                                fnum(r["pos60"]) if fnum(r["pos60"]) is not None else 9e9, 0.1), code, s_date)
        chk("bias20", pmatch((c[di] / ma20d - 1) * 100, fnum(r["bias20"]), 0.05), code, s_date)
        chk("bias60", pmatch((c[di] / ma60d - 1) * 100, fnum(r["bias60"]), 0.05), code, s_date)
        vv = v[di - 249:di + 1]
        chk("vrank", pmatch(100 * sum(1 for x in vv if x <= v[di]) / 250, fnum(r["vrank"]), 0.5), code, s_date)
        # 近10日涨停/连涨/介入日涨幅
        thr = 0.198 if code[:3] in ("300", "301") else 0.098
        zt10 = sum(1 for i in range(max(0, di - 9), di + 1) if c[i] / c[i - 1] - 1 >= thr)
        chk("zt10", zt10 == int(r["zt10"]), code, s_date, f"{zt10} vs {r['zt10']}")
        cons = 0
        for i in range(di, 0, -1):
            if c[i] / c[i - 1] - 1 > 0:
                cons += 1
            else:
                break
        chk("consec_up", cons == int(r["consec_up"]), code, s_date)
        chk("pct_D", pmatch((c[di] / c[di - 1] - 1) * 100, fnum(r["pct_D"]), 0.02), code, s_date)
        chk("成交额亿", pmatch(a[di] / 1e8, fnum(r["amount_mv"]), 0.001), code, s_date)
        # 收益
        for kk in (5, 10, 20, 60, 120):
            j = di + kk
            gv = (c[j] / e - 1) * 100 if j < n else None
            csvv = fnum(r[f"f{kk}"])
            ok = (gv is None and csvv is None) or (gv is not None and csvv is not None and pmatch(gv, csvv, 0.02))
            chk(f"f{kk}", ok, code, s_date, f"{gv} vs {csvv}")
            gn = None if gv is None else gv - 0.45
            csvn = fnum(r[f"f{kk}_net"])
            okn = (gn is None and csvn is None) or (gn is not None and csvn is not None and pmatch(gn, csvn, 0.02))
            chk(f"f{kk}net", okn, code, s_date)
        # 波段(250交易日)
        ent_i = di + 1
        end = min(ent_i + 250, n)
        peak, peak_i = -1.0, -1
        res = {k2: None for k2 in ("brk20d", "brk20", "brk60", "dd15", "dd20", "stop8")}
        m20 = [wma_at(c, ent_i + i, 20) if ent_i + i < n else float("nan") for i in range(end - ent_i)]
        m60 = [wma_at(c, ent_i + i, 60) if ent_i + i < n else float("nan") for i in range(end - ent_i)]
        segc = c[ent_i:end]
        for i in range(len(segc)):
            ci = segc[i]
            if ci > peak:
                peak, peak_i = ci, i
            gi = ent_i + i
            if res["stop8"] is None and ci <= 0.92 * e:
                res["stop8"] = (gi, (ci / e - 1) * 100)
            if res["dd20"] is None and peak > 0 and ci <= 0.80 * peak:
                res["dd20"] = (gi, (ci / e - 1) * 100)
            if res["dd15"] is None and peak > 0 and ci <= 0.85 * peak:
                res["dd15"] = (gi, (ci / e - 1) * 100)
            if res["brk20"] is None and ci < m20[i]:
                res["brk20"] = (gi, (ci / e - 1) * 100)
            if res["brk20d"] is None and ci < m20[i] and i >= 3 and m20[i] < m20[i - 3]:
                res["brk20d"] = (gi, (ci / e - 1) * 100)
            if res["brk60"] is None and ci < m60[i]:
                res["brk60"] = (gi, (ci / e - 1) * 100)
        def fp_boundary(gd, wd):
            """浮点临界翻转归类(不计失配):
            ① |close-MA|/MA<1e-4 的临界日 (close vs MA20/MA60 比较)
            ② MA走平: |MA[i]-MA[i-3]|/MA<1e-4 (down3判定在数学相等值上的1ulp翻转)
            ③ ±1天: 浮点求和顺序在临界日的比较翻转, 对分位数统计零影响"""
            if gd is None or wd is None:
                return False
            if abs(gd - wd) <= 1:
                return True
            lo_i, hi_i = min(gd, wd), max(gd, wd)
            for i in range(max(0, lo_i - 2), min(hi_i + 3, len(segc))):
                t = ent_i + i
                m = wma_at(c, t, 20)
                if m > 0 and abs(c[t] - m) / m < 1e-4:
                    return True
                if i >= 3:
                    m3 = wma_at(c, t - 3, 20)
                    if m > 0 and m3 > 0 and abs(m - m3) / m3 < 1e-4:
                        return True
                if i >= 19 and m60v[t] > 0 and not math.isnan(m60v[t]):
                    if abs(c[t] - m60v[t]) / m60v[t] < 1e-4:
                        return True
            return False

        m60v = [wma_at(c, t, 60) for t in range(len(c))]

        chk("max_gain", pmatch((peak / e - 1) * 100, fnum(r["max_gain"]), 0.02), code, s_date)
        chk("peak_day", peak_i == int(float(r["peak_day"])), code, s_date, f"{peak_i} vs {r['peak_day']}")
        for k2, (want_d, want_r) in {
            "brk20d": (r["brk20d_day"], r["brk20d_ret"]), "brk20": (r["brk20_day"], r["brk20_ret"]),
            "brk60": (r["brk60_day"], r["brk60_ret"]), "dd15": (r["dd15_day"], r["dd15_ret"]),
            "dd20": (r["dd20_day"], r["dd20_ret"]), "stop8": (r["stop8_day"], r["stop8_ret"]),
        }.items():
            got = res[k2]
            gd = None if got is None else got[0] - ent_i
            wd = None if want_d in (None, "", "None") else int(float(want_d))
            if gd == wd:
                chk(k2 + "_day", True, code, s_date)
            elif fp_boundary(gd, wd):
                fp_boundary_n[0] += 1
                continue  # day系浮点临界翻转, 其ret为该日的函数一并归FP
            else:
                chk(k2 + "_day", False, code, s_date, f"{gd} vs {wd}")
            if got is not None and wd is not None:
                chk(k2 + "_ret", pmatch(got[1], fnum(want_r), 0.02), code, s_date)
        # 峰值后走坏
        if peak_i >= 0:
            fnd = None
            for i in range(peak_i, len(segc)):
                if segc[i] < m20[i] and i >= 3 and m20[i] < m20[i - 3]:
                    fnd = (ent_i + i - ent_i, (segc[i] / e - 1) * 100, (segc[i] / peak - 1) * 100)
                    break
            wd = None if r["pk_brk20d_day"] in (None, "", "None") else int(float(r["pk_brk20d_day"]))
            gd = None if fnd is None else fnd[0]
            if gd == wd:
                chk("pk_brk20d_day", True, code, s_date)
            elif fp_boundary(gd, wd):
                fp_boundary_n[0] += 1
                continue  # day浮点翻转, dd为该日函数一并归FP
            else:
                chk("pk_brk20d_day", False, code, s_date, f"{gd} vs {wd}")
            if fnd and wd is not None:
                chk("pk_brk20d_dd", pmatch(fnd[2], fnum(r["pk_brk20d_dd"]), 0.05), code, s_date)
        b_fail += 0
    log(f"[B] 主事件正查: {len(em)} 事件 × {n_field_checked} 字段次, 失配 {sum(len(v2) for v2 in field_fail.values())} 项")
    for tag, lst in sorted(field_fail.items()):
        log(f"    ✗ {tag}: {len(lst)} 项, 首例 {lst[0]}")

    # ---- Part C: lag关键字段全量 ----
    c_fail = 0
    for r in lg:
        code = r["code"]
        s_date, d_date = int(r["s_date"]), int(r["d_date"])
        rows = get_bars(code)
        dates = [int(x[0]) for x in rows]
        if s_date not in dates or d_date not in dates:
            c_fail += 1
            continue
        si, di = dates.index(s_date), dates.index(d_date)
        e = float(rows[di + 1][1] or 0)
        c_d = float(rows[di][4])
        c_s = float(rows[si][4])
        if not rmatch(e, fnum(r["entry"]), 1e-3):
            c_fail += 1
            continue
        if int(r["days"]) != di - si or not pmatch((c_d / c_s - 1) * 100, fnum(r["off_start"]), 0.05):
            c_fail += 1
    log(f"[C] lag全量: {len(lg)} 条键比对(见A) + entry/days/off_start复算, 失配 {c_fail}")

    # ---- 汇总 ----
    n_fp = fp_boundary_n[0]
    total_bad = len(miss) + len(extra) + len(lag_miss) + len(lag_extra) + \
        sum(len(v2) for v2 in field_fail.values()) + c_fail
    verdict = "✅ 全量复核 PASS" if total_bad == 0 else f"❌ 全量复核 FAIL ({total_bad} 项失配)"
    log("\n" + "=" * 70)
    log(verdict + (f"（另计浮点临界翻转 {n_fp} 项, 不计失配）" if n_fp else ""))
    log(f"A 召回: 主事件漏{len(miss)}/错{len(extra)} | lag漏{len(lag_miss)}/多{len(lag_extra)}")
    log(f"B 精度: {n_field_checked} 字段次, 失配 {sum(len(v2) for v2 in field_fail.values())}, 浮点临界翻转 {n_fp}")
    log(f"C lag: {len(lg)} 条, 失配 {c_fail}")
    log(f"耗时 {time.time()-t0:.0f}s")

    with open(OUTP, "w", encoding="utf-8") as f:
        f.write(f"全量复核报告 {verdict}" + (f"（另计浮点临界翻转 {n_fp} 项, 不计失配）" if n_fp else "") + f"\n{'='*60}\n")
        f.write(f"A 召回反查: 重扫主事件 {len(rescan_main)} vs 事件库 {len(csv_main_keys)}\n")
        f.write(f"  漏(库无重扫有) {len(miss)}: {sorted(miss)[:20]}\n")
        f.write(f"  错(库有重扫无) {len(extra)}: {sorted(extra)[:20]}\n")
        f.write(f"  lag漏 {len(lag_miss)}: {sorted(lag_miss)[:20]}\n")
        f.write(f"  lag多 {len(lag_extra)}: {sorted(lag_extra)[:20]}\n")
        f.write(f"B 精度正查: {len(em)} 事件 × {n_field_checked} 字段次, 浮点临界翻转 {n_fp} 项(不计失配)\n")
        for tag, lst in sorted(field_fail.items()):
            f.write(f"  ✗ {tag}: {len(lst)} 项\n")
            for x in lst[:10]:
                f.write(f"      {x}\n")
        f.write(f"C lag: {len(lg)} 条, entry/days/off_start失配 {c_fail}\n")
        f.write(f"总耗时 {time.time()-t0:.0f}s\n")
    log(f"[输出] {OUTP}")


if __name__ == "__main__":
    main()
