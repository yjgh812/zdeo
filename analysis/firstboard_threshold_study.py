#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""firstboard_threshold_study.py — 首板晋级阈值本地验证（呈现层，零交易权）。
2026-11-07 主人「继续」令。指南外部数字（封单≥3亿→58%、组合→66%等）不可直接采信，
用本地 limit_up_patterns(首板3179笔, 2026-08-10~11-06 约45个交易日) 验证：

  分桶维度：①封板时段(开盘早盘/盘中/尾盘) ②封单额(<1亿/1-3亿/3-10亿/≥10亿,可解析子集)
  ③封单额/成交额比(<0.1/0.1-0.3/≥0.3) ④组合=封单≥3亿&开盘早盘
  结果=次日晋级率(次日涨幅≥板块涨停阈-0.2pp, 板块阈=market_rules_config分段规则)

本地缺失维度(如实N/A)：换手率(无流通股本)、竞价涨幅(r8表auction_gap全空)。
退出码：0正常/3数据源不可用。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")
import market_rules_config as mrc  # noqa: E402

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
PRICE_DB = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
            + "?mode=ro")
REPORT_DIR = r"D:\zcodesj\reports"


def _code6(c):
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0]


def _parse_seal(ss):
    """从 seal_status 文本解析 (封单额亿, 炸板次数)，解析不到返回 (None, None)。"""
    ss = ss or ""
    m = re.search(r"封板([\d.]+)亿", ss)
    amt = float(m.group(1)) if m else None
    m2 = re.search(r"(\d+)炸", ss)
    bombs = int(m2.group(1)) if m2 else None
    return amt, bombs


def _time_bucket(flt, ss):
    t = int(str(flt)) if flt and str(flt).isdigit() else None
    if t:
        if t <= 100000:
            return "开盘早盘"
        return "盘中" if t < 140000 else "尾盘"
    return {"开盘秒板": "开盘早盘", "盘中封": "盘中", "尾盘封": "尾盘"}.get(ss or "", "未知")


def _load(conn, conn2):
    rows = conn.execute(
        "SELECT code,name,trade_date,first_limit_time,seal_status,turnover,feature_tags "
        "FROM limit_up_patterns WHERE limit_count=1 ORDER BY trade_date").fetchall()
    out, st_like = [], 0
    for code, name, td, flt, ss, amt, tags in rows:
        code = _code6(code)
        seal_amt, bombs = _parse_seal(ss)
        ratio = None
        try:
            ratio = json.loads(tags or "{}").get("seal_amt_ratio")
        except Exception:
            ratio = None
        td_i = int(str(td)[:8])
        cur = conn2.execute(
            "SELECT close FROM stock_daily_raw WHERE code=? AND date=?",
            (code, td_i)).fetchone()
        nxt = conn2.execute(
            "SELECT close FROM stock_daily_raw WHERE code=? AND date>? ORDER BY date LIMIT 1",
            (code, td_i)).fetchone()
        prv = conn2.execute(
            "SELECT close FROM stock_daily_raw WHERE code=? AND date<? ORDER BY date DESC LIMIT 1",
            (code, td_i)).fetchone()
        if not cur or not nxt or not prv or cur[0] <= 0:
            out.append({"tod": _time_bucket(flt, ss), "seal": seal_amt,
                        "ratio": ratio, "ok": False})
            continue
        thr = mrc.limit_pct(code, td_i) * 100 - 0.2
        day_pct = (cur[0] / prv[0] - 1) * 100
        if day_pct < thr - 0.3:      # 当日涨幅达不到板阈-0.5pp → 疑似ST口径,剔除
            st_like += 1
            continue
        nxt_pct = (nxt[0] / cur[0] - 1) * 100
        out.append({"td": td_i, "tod": _time_bucket(flt, ss),
                    "seal": seal_amt, "ratio": ratio, "ok": True,
                    "promoted": nxt_pct >= thr,
                    "nxt_pct": nxt_pct})
    return out, st_like


def _bstat(items):
    n = len(items)
    if not n:
        return "n=0"
    p = sum(1 for r in items if r.get("promoted"))
    return f"n={n} 晋级率={p / n * 100:.1f}%"


def run():
    try:
        conn = sqlite3.connect(ATTR_DB)
        conn2 = sqlite3.connect(PRICE_DB, uri=True)
        data, st_like = _load(conn, conn2)
        conn.close()
        conn2.close()
    except Exception as e:  # noqa: _BLE001
        print(f"[FB-STUDY] [FAIL-CLOSED] 数据源不可用({type(e).__name__}: {e})")
        return 3
    ok = [r for r in data if r["ok"]]
    no = len(data) - len(ok)
    L = [f"# 首板晋级阈值本地验证 {datetime.now():%Y-%m-%d %H:%M}", "",
         f"- 样本：首板 {len(ok)} 笔（limit_up_patterns, 2026-08-10~11-06 约45个交易日；"
         f"价格缺失{no}笔、疑似ST口径剔除{st_like}笔）",
         "- 晋级=次日涨幅≥板块涨停阈-0.2pp；封单额维度仅可解析子集；",
         "- 本地缺失维度：换手率(无流通股本)、竞价涨幅(r8表全空)——如实N/A。",
         "", "## 分桶结果", ""]
    L.append(f"- 总体首板次日晋级率：{_bstat(ok)}")
    for label, key in (("封板时段", "tod"), ("封单额(亿)", "seal"), ("封单额/成交额比", "ratio")):
        L.append(f"### {label}")
        buckets = {}
        for r in ok:
            v = r[key]
            if key == "seal":
                v = ("<1亿" if v is not None and v < 1 else
                     "1-3亿" if v is not None and v < 3 else
                     "3-10亿" if v is not None and v < 10 else
                     "≥10亿" if v is not None else "未解析")
            elif key == "ratio":
                v = ("<0.1" if v is not None and v < 0.1 else
                     "0.1-0.3" if v is not None and v < 0.3 else
                     "≥0.3" if v is not None else "未解析")
            buckets.setdefault(v, []).append(r)
        for b in sorted(buckets):
            L.append(f"- {b}：{_bstat(buckets[b])}")
    combo = [r for r in ok if (r["seal"] is not None and r["seal"] >= 3
                               and r["tod"] == "开盘早盘")]
    L += ["### 组合(封单≥3亿 & 开盘早盘)", f"- {_bstat(combo)}（指南参考: 封单≥3亿&竞价≥9.5%→58%~66%）"]
    dss = sorted(r["td"] for r in ok)
    mid = dss[len(dss) // 2]
    L += ["### 时段稳健性（窗口对半切，封单/成交额比因子）", ""]
    for label_, hh_ in (("前半", [r for r in ok if r["td"] <= mid]),
                        ("后半", [r for r in ok if r["td"] > mid])):
        hi_ = [r for r in hh_ if r["ratio"] is not None and r["ratio"] >= 0.3]
        lo_ = [r for r in hh_ if r["ratio"] is not None and r["ratio"] < 0.1]
        L.append(f"- {label_}(截至{mid})：≥0.3档 {_bstat(hi_)} | <0.1档 {_bstat(lo_)}")
    path = os.path.join(REPORT_DIR,
                        f"firstboard_threshold_study_{datetime.now():%Y%m%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[FB-STUDY] 样本{len(ok)} 剔ST{st_like} 价格缺失{no} -> {path}")
    print(f"[FB-STUDY] 总体: {_bstat(ok)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
