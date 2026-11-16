# -*- coding: utf-8 -*-
"""300116/300117 全序列重建(20261021) — 多层叠加损坏的终结解。

语义判定(人工逐条):
  300116: 20130614(10转10)/20140509(10派0.1转5)/20150702(10派0.18转5)/20170418(10派0.1转10)
          = 股东权益型, 调; 20200401 重整转增8.5 = 非股东权益型(重整投资人认购), 不调;
  300117: 9事件全为正常分红送转, 全调。
方法: 腾讯真值raw(不复权) × 事件推导累计因子(自最新日向后走), 全序列重锚:
  new_OHLC[t] = raw_OHLC[t] × cumfactor[t]; 整体替换 qfq 库该股 OHLC+change_pct;
  同步重写该股 qlib_bin OHLC; 复检全序列 |隔夜|<=板限。
默认DRY, --execute 写库。
"""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
QLIB = Path(r"D:\workbuddyspace\qlib_data\qlib_bin")
BACKUP = Path(r"D:\zcodesj\backups\qlib_bin_pre_rebuild_20261021")
LEDGER = r"D:\zcodesj\analysis\qfq_rebuild_2stocks_ledger_20261021.jsonl"
FIELDS = ["open", "high", "low", "close"]

EVENTS = {
    "300116": [("20130614", 0.00, 1.0), ("20140509", 0.01, 0.5),
               ("20150702", 0.018, 0.5), ("20170418", 0.01, 1.0)],
    "300117": [("20130529", 0.05, 0.0), ("20140513", 0.035, 0.5),
               ("20150720", 0.02, 0.0), ("20160426", 0.02, 1.2),
               ("20170721", 0.01, 0.0), ("20180626", 0.01, 0.0),
               ("20190821", 0.008, 0.0), ("20200720", 0.003, 0.0),
               ("20210820", 0.013, 0.0)],
    "300208": [("20130402", 0.20, 1.0), ("20150924", 0.06, 1.5),
               ("20190712", 0.03, 0.0)],
    "300495": [("20160427", 0.58, 2.0), ("20170427", 0.15, 1.5),
               ("20180502", 0.05, 0.0), ("20190712", 0.10, 0.0),
               ("20200717", 0.053, 0.0)],
}
LIMIT = 0.21  # GEM板限+1pp


def tencent_raw(code: str, start: str, end: str):
    sym = ("sh" if code.startswith("6") else "sz") + code
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={sym},day,{start},{end},800,")
    import time as _t
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                j = json.loads(r.read().decode("utf-8"))
            break
        except Exception as e:
            last = e
            _t.sleep(2 * (attempt + 1))
    else:
        raise last
    node = (j["data"][0] if isinstance(j["data"], list) else j["data"]).get(sym, {})
    out = {}
    for row in (node.get("day") or []):
        out[row[0].replace("-", "")] = (float(row[1]), float(row[3]), float(row[4]), float(row[2]), float(row[5]))
    return out


def main():
    execute = "--execute" in sys.argv
    chunks = [(f"{y}-01-01", f"{y}-12-31") for y in range(2012, 2027)]
    con = sqlite3.connect(QFQ)
    con.execute("PRAGMA journal_mode=WAL")
    ledger = open(LEDGER, "a", encoding="utf-8")
    for code in ("300116", "300117", "300208", "300495"):
        raw = {}
        for s, e in chunks:
            raw.update(tencent_raw(code, s, e))
        dates = sorted(raw)
        print(f"[{code}] tencent真值raw {len(dates)}日 {dates[0]}→{dates[-1]}")
        evs = sorted(EVENTS[code], reverse=True)
        factor = {d: 1.0 for d in dates}
        # 逐事件向后回溯(事件日之前全段×k)
        for (ed, dv, bv) in evs:
            prevs = [x for x in dates if x < ed]
            if not prevs:
                print(f"  [warn] {code}@{ed}: raw无前日, 跳过该事件")
                continue
            p_prev = raw[prevs[-1]][3]  # 前收(close)
            k = ((p_prev - dv) / (1 + bv)) / p_prev
            for d in dates:
                if d < ed:
                    factor[d] *= k
        # 连续性预检(含违规明细: 日期/调整后变动/当日量能vs前20日均量)
        chgs = []
        vols = {d: raw[d][4] for d in dates}
        for a, b in zip(dates[:-1], dates[1:]):
            chgs.append(raw[b][3] * factor[b] / (raw[a][3] * factor[a]) - 1)
        n_bad = 0
        for (a, b), cg in zip(zip(dates[:-1], dates[1:]), chgs):
            if abs(cg) > LIMIT:
                n_bad += 1
                v20 = np.mean([vols[x] for x in dates[max(0, dates.index(a) - 20):dates.index(a)]]) if dates.index(a) > 0 else np.nan
                print(f"  [violation] {code} {b}: 调整后={cg:+.1%} 量能={vols[b]:.0f} vs 前20均={v20:.0f} "
                      f"({'量能异常→疑似真实无限制日' if v20 and vols[b] > 5 * v20 else '量能正常→疑似仍存因子错'})")
        print(f"[{code}] 重建序列自检: 事件{len(evs)}个, 越限残留{n_bad} "
              f"(0=干净) | 因子范围 {min(factor.values()):.4f}~{max(factor.values()):.4f}")
        if n_bad > 0:
            print(f"  [ABORT] {code} 重建后仍有越限, 不写库")
            continue
        if not execute:
            print(f"  [DRY] {code}: 将整股替换 {len(dates)} 日")
            continue
        cur = con.cursor()
        try:
            cur.execute("BEGIN")
            upd = []
            for d in dates:
                o, h, l, c = raw[d]
                f = factor[d]
                upd.append((o * f, h * f, l * f, c * f, d, code))
            cur.executemany(
                "UPDATE stock_daily_meta_qfq SET open=?,high=?,low=?,close=? WHERE date=? AND code=?", upd)
            q2 = pd.read_sql_query("SELECT date,close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
                                   con, params=(code,))
            cp = (q2["close"] / q2["close"].shift(1) - 1) * 100
            cur.executemany("UPDATE stock_daily_meta_qfq SET change_pct=? WHERE date=? AND code=?",
                            [(float(v) if pd.notna(v) else None, d, code) for d, v in zip(q2["date"], cp)])
            con.commit()
        except Exception as e:
            con.rollback()
            print(f"  [FAIL] {code} DB: {type(e).__name__}: {e}")
            continue
        # qlib_bin 同步重写(整股OHLC, DB为准)
        inst = ("sh" if code.startswith("6") else "sz") + code
        idir = QLIB / "features" / inst
        bdir = BACKUP / inst
        bdir.mkdir(parents=True, exist_ok=True)
        cal = [x.strip().replace("-", "") for x in open(QLIB / "calendars" / "day.txt", encoding="utf-8") if x.strip()]
        dbmap = {d: (o, h, l, c) for o, h, l, c, d, _c in upd}
        for fld in FIELDS:
            fi = FIELDS.index(fld)
            arr = np.fromfile(idir / f"{fld}.day.bin", dtype="<f4")
            hdr, vals, tail = int(arr[0]), arr[1:-1].copy(), arr[-1]
            for p in range(len(vals)):
                ci = hdr + p
                if ci < len(cal) and cal[ci] in dbmap:
                    vals[p] = dbmap[cal[ci]][fi]
            (bdir / f"{fld}.day.bin.bak").write_bytes((idir / f"{fld}.day.bin").read_bytes())
            out = np.concatenate([np.array([hdr], dtype="<f4"), vals.astype("<f4"), np.array([tail], dtype="<f4")])
            tmp = idir / f"{fld}.day.bin.tmp"
            out.tofile(tmp)
            tmp.replace(idir / f"{fld}.day.bin")
        ledger.write(json.dumps({"code": code, "events": len(evs), "rows": len(dates),
                                 "repair": "20261021_full_rebuild"}, ensure_ascii=False) + "\n")
        ledger.flush()
        print(f"  [REBUILT] {code}: 整股{len(dates)}日重锚 + bin同步重写")
    ledger.close()
    con.close()
    print(f"\n[summary] 完成 ({'EXECUTE' if execute else 'DRY'})")


if __name__ == "__main__":
    sys.exit(main())
