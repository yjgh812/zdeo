# -*- coding: utf-8 -*-
"""实弹scanner名单全量回验 (2026-10-11, 主人令"继续": 上轮警报闭环, 只读零改动)
对9/23以来全部实弹名单(universe_startup_scan_*.json)逐条(扫描日,code)复刻八门:
  逐门: 多头排列/pos250<50/价格2-30/off_start窗/pos3<55/off_lo3<=80/量位vrank<80/乖离bias20<=3
  缩量cap自适应: 先0.95, 不过再试1.56并标注LOW_VOL归因(实弹动态cap, 历史态不可得)
  段起锚: 实弹扫描日视角的250窗最低点后首段起(=scanner每日重扫语义)
输出分类:
  A 复刻全过(0.95)   = 复刻一致
  B cap归因(1.56过)  = LOW_VOL口径差
  C pos250>=50       = 🔴高位段起混入(上轮警报定量版)
  D 其他门分歧       = 逐门列出
输出: reports/rightside_startup_20261011/livetask_recheck.md
"""
import os, sys, json, glob, sqlite3
import numpy as np

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from stock_name_lookup import get_name

D = r"D:\zcodesj\reports\rightside_startup_20261011"
OUT = open(os.path.join(D, "livetask_recheck.md"), "w", encoding="utf-8")
db = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)


def w(*a):
    print(*a)
    print(*a, file=OUT, flush=True)


_cache = {}


def bars(code):
    if code not in _cache:
        _cache[code] = db.execute(
            "SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
            (code,)).fetchall()
    return _cache[code]


def check_hit(code, scan_date):
    """复刻实弹扫描日命中: 返回(分类, 明细)"""
    rows = bars(code)
    dates = [int(x[0]) for x in rows]
    if scan_date not in dates:
        return ("E无bar", f"scan_date {scan_date} 不在序列")
    t = dates.index(scan_date)
    if t < 249:
        return ("E暖机不足", f"t={t}<249")
    c = [float(x[4] or 0) for x in rows]
    v = [float(x[5] or 0) for x in rows]
    n = len(c)
    cs = [0.0]
    vsv = [0.0]
    for i in range(n):
        cs.append(cs[-1] + c[i])
        vsv.append(vsv[-1] + v[i])

    def wma(S, i, w):
        s0 = max(0, i - w + 1)
        return (S[i + 1] - S[s0]) / (i + 1 - s0)

    px = c[t]
    ma20 = wma(cs, t, 20)
    ma60 = wma(cs, t, 60)
    win = c[t - 249:t + 1]
    lo = min(win)
    hi = max(win)
    pos250 = (px - lo) / (hi - lo) * 100 if hi > lo else 50.0
    # 段起锚(扫描日视角)
    lo_i = t - 249 + win.index(lo)
    v20arr = [wma(vsv, i, 20) for i in range(n)]
    start_i = None
    for i in range(lo_i, t + 1):
        if i < 19:
            break
        if v20arr[i] > 0 and v[i] >= 2 * v20arr[i] and c[i] / c[i - 1] - 1 >= 0.03:
            start_i = i
            break
    gates = {}
    gates["多头排列"] = ma20 > ma60
    gates["pos250<50"] = pos250 < 50
    gates["价格2-30"] = 2 <= px <= 30
    if start_i is None:
        return ("E无段起", f"250窗内无放量大阳(lo={lo:.2f}@{dates[lo_i]})")
    pre60 = c[max(0, start_i - 60):start_i]
    dd_ok = True
    shrink = None
    if len(pre60) >= 40:
        dd_ok = min(pre60) / max(pre60) - 1 <= -0.25
        pre20 = wma(vsv, start_i - 1, 20)
        v250 = wma(vsv, start_i, 250)
        shrink = pre20 / v250 if v250 > 0 else 9
    off_start = (px / c[start_i] - 1) * 100
    gates["深跌门"] = dd_ok
    gates["off_start窗"] = -3 <= off_start <= 17
    w3 = c[max(0, t - 749):t + 1]
    lo3 = min(w3)
    hi3 = max(w3)
    pos3v = (px - lo3) / (hi3 - lo3) * 100 if hi3 > lo3 else 50
    gates["pos3<55"] = pos3v < 55
    gates["距三年低<=80"] = (px / lo3 - 1) * 100 <= 80 if lo3 > 0 else True
    v250w = v[t - 249:t + 1]
    vrank = 100 * sum(1 for x in v250w if x <= v[t]) / 250
    gates["量位<80"] = vrank < 80
    gates["乖离<=3"] = (px / ma20 - 1) * 100 <= 3
    fails = [k for k, ok in gates.items() if not ok]
    if fails:
        if "pos250<50" in fails:
            cat = "C高位混入"
        else:
            cat = "D其他门分歧"
        return (cat, f"未过门={fails} pos250={pos250:.1f} pos3={pos3v:.1f} vrank={vrank:.0f} bias={(px/ma20-1)*100:.1f} off_start={off_start:.1f}")
    # 八门全过→缩量cap
    if shrink is None:
        return ("A复刻全过", "段起前<40根豁免缩量门")
    if shrink <= 0.95:
        return ("A复刻全过", f"缩量{shrink:.2f}<=0.95")
    if shrink <= 1.56:
        return ("B LOW_VOL cap归因", f"缩量{shrink:.2f}∈(0.95,1.56]")
    return ("D其他门分歧", f"缩量{shrink:.2f}>1.56 两口径都应拒")


# ---- 载入全部实弹名单 ----
scans = sorted(glob.glob(r"D:\zcodesj\reports\universe_startup_scan_2026*.json"))
w("# 实弹scanner名单全量回验 (9/23以来, 只读零改动)\n")
w(f"名单文件数: {len(scans)}")
cat_cnt = {}
detail_rows = []
seen = set()
for p in scans:
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    import re
    md = re.search(r"(\d{8})", os.path.basename(p))
    sd = int(md.group(1)) if md else 0
    for r0 in d.get("rows") or d.get("hits") or []:
        code = str(r0.get("code", "")).zfill(6)[-6:]
        key = (sd, code)
        if key in seen:
            continue
        seen.add(key)
        cat, detail = check_hit(code, sd)
        cat_cnt[cat] = cat_cnt.get(cat, 0) + 1
        detail_rows.append((sd, code, get_name(code) or "", cat, detail))

w(f"去重后(扫描日,code)命中条数: {len(seen)}\n")
w("| 分类 | 条数 | 占比 |")
w("|---|---|---|")
for k in sorted(cat_cnt):
    w(f"| {k} | {cat_cnt[k]} | {cat_cnt[k]/len(seen)*100:.1f}% |")
w("")
w("## C 高位混入明细 (pos250>=50 仍被实弹命中)\n")
w("| 扫描日 | 代码 | 名称 | 明细 |")
w("|---|---|---|---|")
for sd, code, nm, cat, detail in detail_rows:
    if cat == "C高位混入":
        w(f"| {sd} | {code} | {nm} | {detail} |")
w("")
w("## D 其他门分歧明细\n")
w("| 扫描日 | 代码 | 名称 | 明细 |")
w("|---|---|---|---|")
for sd, code, nm, cat, detail in detail_rows:
    if cat.startswith("D"):
        w(f"| {sd} | {code} | {nm} | {detail} |")
w("")
w("## B LOW_VOL cap归因抽样(前10)\n")
n_b = 0
for sd, code, nm, cat, detail in detail_rows:
    if cat.startswith("B") and n_b < 10:
        w(f"- {sd} {code} {nm}: {detail}")
        n_b += 1
OUT.close()
print("[输出] livetask_recheck.md")
