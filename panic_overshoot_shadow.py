#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""panic_overshoot_shadow.py — PD-034 恐慌超跌出击影子台账件（PREREG_PANIC_OVERSHOOT_20261006, 2026-10-06 批准）

观测层零交易权: 只记账不产生任何买卖信号, 不接闸门不接 executor。
触发 LOCKED(prereg, 禁调参): A=上证日跌<=-2.0% 且 close/MA5-1<=-3.0% × B=全市场(qfq库,n>800)普跌家数占比>=80%
质量分层: erp_pct_5y>=60 记 quality=high(分层字段非触发条件); 相位仅叙述字段。
台账: data/panic_overshoot_shadow.jsonl 按 event_date 幂等 UPSERT; in_sample=回测期参考层(2015~20261006), 前向事件 out_of_sample。
验收(LOCKED): 日历12个月 或 累计 out_of_sample n>=3 先到; pass=n>=3 且 fwd20中位>=+2% 且 win>=60%; kill_switch=连续3个前向事件 fwd20 全负。

数据源: 上证=tdx vipdoc sh000001.day 本地直读(主) / baostock(回填与兜底); 普跌占比=dragon_quant_qfq.db(与验证同源)。
退出码: 0 正常 / 2 数据缺陷。
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import struct
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
LEDGER = os.path.join(ROOT, "data", "panic_overshoot_shadow.jsonl")
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
ERP_DB = "file:D:/zcodesj/erp_history.db?mode=ro"
PHASE_LEDGER = os.path.join(ROOT, "data", "emotion_phase_v2_ledger.jsonl")
VIPDOC_DAY = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000001.day"
IN_SAMPLE_CUTOFF = "20261006"
PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")


def read_vipdoc_sh000001():
    """直读通达信 .day: 每条32字节 = date,u_open,u_high,u_low,u_close,amount(float),u_vol,保留。价格×100 整数。"""
    if not os.path.exists(VIPDOC_DAY):
        return []
    out = []
    with open(VIPDOC_DAY, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            d, o, h, l, c, amt, vol, _r = struct.unpack("<IIIIIfII", b)
            out.append((str(d), c / 100.0))
    return out


def fetch_sh_index_baostock():
    import baostock as bs
    bs.login()
    rows, rs = [], bs.query_history_k_data_plus(
        "sh.000001", "date,close", start_date="2015-01-01", end_date="2026-12-31",
        frequency="d", adjustflag="3")
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    bs.logout()
    return [(r[0].replace("-", ""), float(r[1])) for r in rows]


def sh_index_series():
    px = read_vipdoc_sh000001()
    if px:
        return px, "vipdoc"
    return fetch_sh_index_baostock(), "baostock"


BREADTH_CACHE = os.path.join(ROOT, "data", "panic_breadth_cache.json")


def breadth_down_ratio(con):
    """全市场逐日普跌占比(qfq, 与验证同源)。
    2026-10-19 a1提速(夜链瓶颈审计呈请): 逐日期聚合缓存(data/panic_breadth_cache.json,
    原子覆盖), 夜间仅对缓存尾日期之后做 idx_qfq_date 区间扫; 缓存缺失/损坏→全量重算
    (=原行为)。计数按日期确定性, 产出与全量重算逐位一致(md5对拍为验收)。"""
    cache_ok = False
    dates, tots, dns = [], [], []
    try:
        if os.path.exists(BREADTH_CACHE):
            ca = json.loads(open(BREADTH_CACHE, encoding="utf-8").read())
            if (ca.get("v") == 1 and isinstance(ca.get("dates"), list) and ca["dates"]
                    and len(ca["dates"]) == len(ca.get("tot", [])) == len(ca.get("dn", []))):
                dates, tots, dns = list(ca["dates"]), list(ca["tot"]), list(ca["dn"])
                cache_ok = True
    except Exception:
        cache_ok = False
    sql = ("SELECT date, COUNT(*), SUM(CASE WHEN change_pct<0 THEN 1 ELSE 0 END) "
           "FROM stock_daily_meta_qfq WHERE change_pct IS NOT NULL "
           "AND substr(code,1,3) IN (%s)" % ",".join("'%s'" % p for p in PREFIX))
    args = []
    if cache_ok:
        sql += " AND date > ?"
        args.append(dates[-1])
    else:
        sql += " AND date>='20150101'"
    sql += " GROUP BY date"
    for d, tot, dn in con.execute(sql, args):
        if tot > 800:
            dates.append(str(d))
            tots.append(tot)
            dns.append(dn)
    tmp = BREADTH_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"v": 1, "dates": dates, "tot": tots, "dn": dns}, f)
    os.replace(tmp, BREADTH_CACHE)
    out = {d: round(dn * 100.0 / tot, 1) for d, tot, dn in zip(dates, tots, dns) if tot > 800}
    return out


def erp_map():
    con = sqlite3.connect(ERP_DB, uri=True, timeout=30)
    m = {int(d): p for d, p in con.execute(
        "SELECT date, erp_pct_5y FROM erp_daily WHERE date>=20150101").fetchall() if p is not None}
    con.close()
    return m


def phase_map():
    if not os.path.exists(PHASE_LEDGER):
        return {}
    out = {}
    for line in open(PHASE_LEDGER, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                r = json.loads(line)
                out[r["phase_date"]] = r["phase"]
            except Exception:
                pass
    return out


def detect_events(px, br):
    """A×B 逐日检测。返回 [(date, ret1, dev_ma5, down_ratio)]。"""
    ev, ma5 = [], {}
    for i in range(4, len(px)):
        d, c = px[i]
        pc = px[i - 1][1]
        ret1 = (c / pc - 1) * 100 if pc else None
        ma5[d] = sum(x[1] for x in px[i - 4:i + 1]) / 5
        dev = (c / ma5[d] - 1) * 100
        if ret1 is not None and ret1 <= -2.0 and dev <= -3.0 and br.get(d, 0) >= 80:
            ev.append((d, round(ret1, 2), round(dev, 2), br[d]))
    return ev


def fwd_ret(px, d, n):
    idx = {x[0]: i for i, x in enumerate(px)}.get(d)
    if idx is None or idx + n >= len(px):
        return None
    return round((px[idx + n][1] / px[idx][1] - 1) * 100, 2)


def load_ledger():
    rows = []
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def upsert_all(recs):
    rows = {r["event_date"]: r for r in load_ledger()}
    for r in recs:
        old = rows.get(r["event_date"], {})
        merged = dict(old)
        merged.update({k: v for k, v in r.items() if v is not None})
        rows[r["event_date"]] = merged
    out = sorted(rows.values(), key=lambda r: r["event_date"])
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)
    return len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="全史回填(含in_sample参考层)+前向事件补齐")
    a = ap.parse_args()
    try:
        px, src = sh_index_series()
        con = sqlite3.connect(QFQ_DB, uri=True, timeout=30)
        br = breadth_down_ratio(con)
        con.close()
    except Exception as e:
        print("[FAIL] 数据源不可达: %s" % e, file=sys.stderr)
        return 2
    if len(px) < 100 or not br:
        print("[FAIL] 指数%d条/广度%d日不足" % (len(px), len(br)), file=sys.stderr)
        return 2
    erp, ph = erp_map(), phase_map()
    print("[SRC] 上证=%s %s~%s %d条 | 广度%d日" % (src, px[0][0], px[-1][0], len(px), len(br)))

    existing = {r["event_date"]: r for r in load_ledger()}
    events = detect_events(px, br)
    recs = []
    for d, ret1, dev, dr in events:
        e = int(erp.get(int(d)))
        rec = {
            "event_date": d,
            "ret1_pct": ret1, "dev_ma5_pct": dev, "down_ratio_pct": dr,
            "erp_pct_5y": e, "quality": "high" if e >= 60 else "normal",
            "phase": ph.get(d, "N/A"),
            "sample": "in_sample" if d <= IN_SAMPLE_CUTOFF else "out_of_sample",
            "fwd5_pct": fwd_ret(px, d, 5), "fwd20_pct": fwd_ret(px, d, 20),
        }
        recs.append(rec)
    total = upsert_all(recs)
    rows = load_ledger()
    oos = [r for r in rows if r.get("sample") == "out_of_sample"]
    ins = [r for r in rows if r.get("sample") == "in_sample"]
    print("[BACKFILL]" if a.backfill else "[SCAN]",
          "检测%d事件 -> 台账%d行 (in_sample=%d out_of_sample=%d)" % (len(events), total, len(ins), len(oos)))

    filled = sum(1 for r in oos if r.get("fwd20_pct") is not None)
    print("[FWD] 前向事件fwd20已回填%d/%d" % (filled, len(oos)))
    if filled >= 3:
        f20 = [r["fwd20_pct"] for r in oos if r.get("fwd20_pct") is not None]
        win = sum(1 for x in f20 if x > 0) * 100.0 / len(f20)
        med = sorted(f20)[len(f20) // 2]
        print("[PROGRESS] n=%d fwd20中位%+.2f%% win%.0f%% | 判据: med>=+2%%且win>=60%%" % (len(f20), med, win))
        neg_tail = all((r.get("fwd20_pct") is not None and r["fwd20_pct"] < 0)
                       for r in oos[-3:]) if len(oos) >= 3 else False
        if neg_tail:
            print("[KILL_SWITCH] 连续3个前向事件fwd20全负 -> 提请废案(LOCKED)")
    last = rows[-1]["event_date"] if rows else "-"
    print("[DONE] 台账%s | 最新事件%s" % (os.path.basename(LEDGER), last))
    return 0


if __name__ == "__main__":
    sys.exit(main())
