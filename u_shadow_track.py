#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""u_shadow_track.py — 「2-4元启动确认」prereg影子跟踪器（2026-09-24 主人批准立项）

prereg = data/prereg_u24_lowprice_20260924.json (指纹 9673c4451702b076, 跑前冻结)
口径: universe_startup_scan 漏斗 + 命中日qfq收盘价∈[2,4) ; 前向结算=T+1开盘买→T+61收盘卖,
净收益含0.9%费, 超额=净-全市场等权同期。影子=零交易权, 只记录不买(直跑制+U24Shadow_2355调度双保险)。

用法:
  python u_shadow_track.py --daily            # 回填模式: 从台账最后日期+1 到 库内最新bar 逐日补记(漏跑自愈)
  python u_shadow_track.py --daily 20260925   # 只跑指定单日
  python u_shadow_track.py --settle           # 结算已满61个交易日的条目
  python u_shadow_track.py --status           # 影子状态
台账: data/u24_shadow_hits.jsonl / data/u24_shadow_settled.jsonl
缓存: data侧npz过期自动检测(库MAX date>缓存末bar→删缓存重建, 内存守卫FreeVirtual>=8GB否则退出待下轮)
验收(prereg冻结): 影子期60交易日累积n>=150 且 日聚簇NW-t>=2 且 事件加权ex>0 → 方可提请主人复议入场。
"""
import argparse, json, os, sys, time, subprocess
import numpy as np
import pandas as pd

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import universe_startup_backtest_20260924 as U
from stock_name_lookup import get_name

HITS = os.path.join(ROOT, "data", "u24_shadow_hits.jsonl")
SETTLED = os.path.join(ROOT, "data", "u24_shadow_settled.jsonl")
PREREG = os.path.join(ROOT, "data", "prereg_u24_lowprice_20260924.json")
CACHE = os.path.join(U.OUT, "_pivot_cache.npz")
CACHE_META = os.path.join(U.OUT, "_pivot_cache_meta.json")


def qfq_max_date():
    import sqlite3
    db = sqlite3.connect(U.QFQ_DB, uri=True)
    mx = db.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    db.close()
    return int(mx)


def free_virtual_mb():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[math]::Round((Get-CimInstance Win32_OperatingSystem).FreeVirtualMemory/1024)"],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return int(out)
    except Exception:
        return -1


def load_data():
    """缓存过期自动重建: 库MAX date > 缓存末bar → 删npz+meta → U.load()重建(守卫内存)。
    盘中污染防御: 缓存末bar==今天 且 build时间<当日16:00 → 视为过期(半日bar不可信, qfq正常晚间更新,
    但存在盘中被触碰先例)。"""
    mx = qfq_max_date()
    today = int(time.strftime("%Y%m%d"))
    cutoff = time.mktime(time.strptime(f"{today}", "%Y%m%d")) + 16 * 3600
    # 影子=前向跟踪必须见库内最新bar: 覆盖U模块END_DATE常量(10/1定谳: 常量20260923封顶致重建后回填仍0天)
    U.END_DATE = mx
    cache_ok = False
    meta = {}
    if os.path.exists(CACHE) and os.path.exists(CACHE_META):
        # with块必须先关NpzFile句柄: 句柄未关时下方os.remove在Windows必撞WinError 32(10/1定谳的9/24-9/30断粮根因)
        with np.load(CACHE) as z:
            last_bar = int(z["dates"][-1])
        meta = json.load(open(CACHE_META, encoding="utf-8"))
        built = float(meta.get("built_epoch", 0))
        if last_bar >= mx and not (last_bar >= today and built < cutoff):
            cache_ok = True
    if not cache_ok and os.path.exists(CACHE):
        fv = free_virtual_mb()
        if fv > 0 and fv < 8000:
            print(f"[SKIP] 缓存需重建但空闲提交仅{fv}MB(<8000), 待下轮(静默窗口重建)")
            sys.exit(3)
        for p in (CACHE, CACHE_META):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except PermissionError as e:
                    print(f"[FAIL] 缓存删除被外部进程占用拒绝: {e} → 本轮rc=4退出待下轮重试")
                    sys.exit(4)
        print(f"[CACHE] 缓存过期(库MAX={mx}), 已删除待重建...")
    CMp, VMp, OMp, dates, cidx, codes = U.load()
    m = json.load(open(CACHE_META, encoding="utf-8"))
    m["built_epoch"] = time.time()
    json.dump(m, open(CACHE_META, "w", encoding="utf-8"))
    return CMp, VMp, OMp, dates, cidx


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def append_jsonl(path, rows):
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def scan_day(CMp, VMp, dates, cidx, dtarget, have):
    rows = []
    t0 = time.time()
    for code, j in cidx.items():
        col = CMp[:, j]
        mask = np.isfinite(col)
        if mask.sum() < 400:
            continue
        c = col[mask]; v = VMp[mask, j]; dd = dates[mask]
        ti = int(np.searchsorted(dd, dtarget, side="right")) - 1
        if ti < 399 or int(dd[ti]) != dtarget:
            continue
        cond, score = U.funnel_stock(c[:ti + 1], v[:ti + 1], 0.95)
        if cond is None or not cond[-1]:
            continue
        px = float(c[ti])
        if not (2 <= px < 4):
            continue
        if (dtarget, code) in have:
            continue
        rows.append(dict(date=dtarget, code=code, name=get_name(code), px=px,
                         score=round(float(score[-1]), 4), ts=time.strftime("%F %T")))
    print(f"  [SCAN] {dtarget}: 新增 {len(rows)} 只 ({time.time()-t0:.0f}s)")
    for r in rows:
        print("  ", r["code"], r["name"], r["px"], "score", r["score"])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", nargs="?", const="backfill", default=None)
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    pre = json.load(open(PREREG, encoding="utf-8"))

    if a.status:
        hits = load_jsonl(HITS)
        settled = load_jsonl(SETTLED)
        print(f"[STATUS] prereg指纹={pre.get('fingerprint', '?')} 影子命中={len(hits)} "
              f"已结算={len(settled)} 进行中={len(hits)-len(settled)}")
        if settled:
            df = pd.DataFrame(settled)
            g = df.groupby("date")["ex"].mean()
            mu = g.mean(); se = g.std(ddof=1) / np.sqrt(len(g)) if len(g) > 2 else np.nan
            print(f"[STATUS] 已结算: n={len(df)} 事件ex均={df.ex.mean():+.2f}% 日等权={mu:+.2f}% "
                  f"聚簇t={mu/se if se and se>0 else 0:+.2f} 净胜率={(df.net>0).mean()*100:.1f}%")
        if hits:
            print(f"[STATUS] 台账覆盖 {hits[0]['date']} ~ {hits[-1]['date']}")
        return

    if a.daily is not None:
        today = int(time.strftime("%Y%m%d"))
        cutoff = time.mktime(time.strptime(f"{today}", "%Y%m%d")) + 16 * 3600
        CMp, VMp, OMp, dates, cidx = load_data()
        have = {(h["date"], h["code"]) for h in load_jsonl(HITS)}
        last_bar = int(dates[-1])
        if a.daily == "backfill":
            hit_days = sorted({h["date"] for h in load_jsonl(HITS)})
            start = hit_days[-1] if hit_days else None
            if start is None:
                print("[DAILY] 空台账, 请指定起始日: --daily YYYYMMDD")
                return
            targets = [int(d) for d in dates if start < int(d) <= last_bar]
        else:
            d1 = int(a.daily)
            targets = [d1] if d1 <= last_bar else []
            if d1 > last_bar:
                print(f"[SKIP] {d1} 当日bar未生成(库内最新={last_bar}), 归下次运行")
        # 收盘防御: 只对"已收盘"日期记账(目标日==今天且<16:00 → 半日bar不可信, 留待晚间)
        if targets and targets[-1] >= today and time.time() < cutoff:
            dropped = [d for d in targets if d >= today]
            targets = [d for d in targets if d < today]
            print(f"[GUARD] 推迟未收盘日期记账: {dropped} (现距收盘确认16:00未到)")
        total = 0
        for dtg in targets:
            rows = scan_day(CMp, VMp, dates, cidx, dtg, have)
            append_jsonl(HITS, rows)
            have.update((r["date"], r["code"]) for r in rows)
            total += len(rows)
        print(f"[DAILY] 回填完成: {len(targets)}个交易日, 新增命中 {total} 只 → {HITS}")
        return

    if a.settle:
        CMp, VMp, OMp, dates, cidx = load_data()
        hits = load_jsonl(HITS)
        done = {(s["date"], s["code"]) for s in load_jsonl(SETTLED)}
        mkt_s = pd.Series(pd.DataFrame(CMp).mean(axis=1).to_numpy(), index=dates)
        di = {int(d): i for i, d in enumerate(dates)}
        rows = []
        for h in hits:
            if (h["date"], h["code"]) in done:
                continue
            i0 = di.get(h["date"]); j = cidx.get(h["code"])
            if i0 is None or j is None or i0 + 61 >= len(dates):
                continue
            bp = OMp[i0 + 1, j]; sp = CMp[i0 + 61, j]
            if not (np.isfinite(bp) and bp > 0 and np.isfinite(sp) and sp > 0):
                continue
            net = (sp / bp - 1) * 100 - 0.9
            base = (mkt_s.iloc[i0 + 61] / mkt_s.iloc[i0 + 1] - 1) * 100
            rows.append(dict(date=h["date"], code=h["code"], name=h.get("name", ""), net=net,
                             ex=net - base, exit_date=int(dates[i0 + 61])))
        append_jsonl(SETTLED, rows)
        print(f"[SETTLE] 新结算 {len(rows)} 条 → {SETTLED}")
        return


if __name__ == "__main__":
    main()
