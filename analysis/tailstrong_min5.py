#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tailstrong_min5.py — 尾盘强势信号5分钟级检验（预注册 tailstrong_prereg.json）。零交易权。

TDX lc5 全市场扫描（2026-01-12~2026-11-06约198交易日）。
信号=14:45收盘>9:35-14:25日内最高 且 14:30-14:45量≥前均量2倍 且 涨幅∈[+1%,涨停-1%]。
入场=14:45 bar收盘价；退出=次日开盘/次日收盘/次2日收盘；对照=全A同窗基线。
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAW_DB = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
          + "?mode=ro")
VIP = r"D:\anzhuang\tdx\vipdoc"
OUT = r"D:\zcodesj\data\fulldomain\tailstrong_events.json"


def decode_bars(path):
    """lc5全文件解码。返回 [(dayint, tmin, open, high, low, close, vol)]。"""
    n = os.path.getsize(path) // 32
    bars = []
    with open(path, "rb") as f:
        for _ in range(n):
            b = f.read(32)
            if len(b) < 32:
                break
            tv = struct.unpack("<I", b[0:4])[0]
            tmin = tv >> 16
            dd = tv & 0xFFFF
            y = dd // 2048 + 2004
            md = dd % 2048
            mo, dy = md // 100, md % 100
            if not (2004 <= y <= 2030 and 1 <= mo <= 12
                    and 1 <= dy <= 31 and md >= 101):
                continue
            o, h, l, c = struct.unpack("<ffff", b[4:20])
            if c <= 0 or o <= 0:
                continue
            vol = struct.unpack("<I", b[24:28])[0]
            bars.append((y * 10000 + md, tmin, o, h, l, c, vol))
    return bars


def run():
    # 昨收+次日映射（raw日频, 2026-01-12起）
    conn = sqlite3.connect(RAW_DB, uri=True, check_same_thread=False)
    prev_of, nxt = {}, {}
    cur_code, arr = None, []
    cur = conn.execute(
        "SELECT code, date, open, close FROM stock_daily_raw "
        "WHERE date >= 20260101 ORDER BY code, date")
    for code, d, o, c in cur:
        k = str(code)[-6:]
        di = int(d)
        if k != cur_code:
            for j in range(len(arr) - 1):
                d0 = arr[j][0]
                nxt[(cur_code, d0)] = (arr[j + 1][0], arr[j + 1][1],
                                       arr[j + 1][2])
                prev_of[(cur_code, arr[j + 1][0])] = arr[j][2]
            cur_code, arr = k, []
        arr.append((di, float(o), float(c)))
    kk = cur_code
    for j in range(len(arr) - 1):
        nxt[(kk, arr[j][0])] = (arr[j + 1][0], arr[j + 1][1], arr[j + 1][2])
        prev_of[(kk, arr[j + 1][0])] = arr[j][2]
    conn.close()
    print(f"[TAIL] 昨收{len(prev_of)} 次日{len(nxt)}映射", flush=True)

    # 全市场lc5扫描
    events = []
    n_files = 0
    for mkt in ("sh", "sz"):
        fd = os.path.join(VIP, mkt, "fzline")
        for fn in os.listdir(fd):
            if not fn.endswith(".lc5"):
                continue
            code = fn[2:8]
            if code.startswith(("68", "40", "43", "83", "87", "92")):
                continue
            n_files += 1
            bars = decode_bars(os.path.join(fd, fn))
            byday = defaultdict(list)
            for b in bars:
                byday[b[0]].append(b)
            for day, bs in byday.items():
                bs.sort(key=lambda x: x[1])
                bs = [x for x in bs if 575 <= x[1] <= 900]
                if len(bs) < 45 or 870 not in [x[1] for x in bs]:
                    continue
                p0 = prev_of.get((code, day))
                if not p0 or p0 <= 0:
                    continue
                am = [x for x in bs if x[1] <= 865]
                if len(am) < 40:
                    continue
                hi_am = max(x[3] for x in am)
                vols_am = [x[6] for x in am]
                vma = sum(vols_am) / len(vols_am)
                pm = [x for x in bs if 870 <= x[1] <= 885]
                if len(pm) < 4:
                    continue
                c145 = pm[-1][5]
                v_pm = sum(x[6] for x in pm)
                vr = v_pm / vma if vma > 0 else 0.0
                thr = (0.198 if code.startswith("30")
                       and day >= 20200824 else 0.098)
                pct = c145 / p0 - 1.0
                if c145 > hi_am and vr >= 2.0 and 0.01 <= pct < thr - 0.01:
                    nx = nxt.get((code, day))
                    if not nx:
                        continue
                    d1, o1, c1 = nx
                    events.append({"code": code, "d": day,
                                   "entry": round(c145, 3),
                                   "no": round((o1 / c145 - 1.0) * 100, 2),
                                   "nc": round((c1 / c145 - 1.0) * 100, 2)})
    print(f"[TAIL] 扫描{n_files}只 信号{len(events)}", flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False)
    print(f"[TAIL] -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
