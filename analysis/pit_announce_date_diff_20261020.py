# -*- coding: utf-8 -*-
"""1-B 公告日 PIT 对拍(只读零风险, 呈请1主件, 2026-10-20).

命题(基于 1-A 与主表结构勘正后重写):
  PIT 主表 stock_factors_fundamental.pit_avail_date = 法定披露截止日(纯日历推导:
  Q1→当年0430 / H1→当年0831 / Q3→当年1031 / 年报→次年0430), 无公告日数据上游。
  故安全性质 = BaoStock 实际公告日 pubDate <= pit_avail_date(违反即前视窗口)。
对拍:
  T1 安全性质: 抽样(code, report_date) 的 baostock pubDate vs pit_avail_date,
     统计违反率 + 保守度(pit_avail - pubDate 天数)分布 + 全局映射一致性。
  T2 浅表公告日直接对拍: fina_indicator / express / forecast 的 ann_date
     vs baostock 对应接口 pubDate(动态字段发现), 一致率与偏差天数。
  T3 数值抽查: sff.roe(pit_avail 当日快照) vs query_dupont_data.roeAvg, 容差0.5pp。
产出: 同名 .json + .md(另写)。断点续跑: 结果逐键写 jsonl 缓存。
用法: python analysis/pit_announce_date_diff_20261020.py
"""
import json
import os
import random
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import baostock as bs

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
OUT_JSON = r"D:\zcodesj\analysis\pit_announce_date_diff_result_20261020.json"
CACHE = r"D:\zcodesj\analysis\pit_announce_date_diff_cache_20261020.jsonl"

STAT_TO_AVAIL = {}  # 全局法定映射(以主表实际值为准, 抽验一致性)
REPORT_DATES = ["20241231", "20250331", "20250630", "20250930",
                "20260331", "20260630", "20181231", "20211231"]

main_con = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True, timeout=120)
fund_con = sqlite3.connect("file:%s?mode=ro" % FUND_DB, uri=True, timeout=60)


def bs_stat(report_date):
    return "%s-%s-%s" % (report_date[:4], report_date[4:6], report_date[6:])


def bs_code(code6):
    return ("sh." if code6.startswith("6") else "sz.") + code6


def rows_of(rs):
    out = []
    while (rs.error_code == "0") and rs.next():
        out.append(rs.get_row_data())
    return out


def pick_pub_field(fields):
    for f in fields:
        if "pubdate" in f.lower():
            return f
    return None


def get_profit_pub(code6, rd):
    rs = bs.query_profit_data(code=bs_code(code6), year=int(rd[:4]), quarter={"03": 1, "06": 2, "09": 3, "12": 4}[rd[4:6]])
    rr = rows_of(rs)
    if not rr:
        return None
    return dict(zip(rs.fields, rr[0])).get("pubDate", "")


def get_dupont_roe(code6, rd):
    """baostock 杜邦 ROE 字段=dupontROE, 实测原值即小数(工行2024Q4=0.0947≈9.5%与实况吻合), 直接返回不缩放。"""
    rs = bs.query_dupont_data(code=bs_code(code6), year=int(rd[:4]), quarter={"03": 1, "06": 2, "09": 3, "12": 4}[rd[4:6]])
    rr = rows_of(rs)
    if not rr:
        return ""
    v = dict(zip(rs.fields, rr[0])).get("dupontROE", "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return ""


def load_cache():
    done = {}
    if os.path.exists(CACHE):
        for line in open(CACHE, encoding="utf-8"):
            try:
                r = json.loads(line)
                # 历史缓存兼容: SQLite INTEGER 列曾以 int 落盘, 统一归一为 8 位字符串
                for k in ("avail", "ann"):
                    if r.get(k) is not None:
                        r[k] = str(r[k])
                done[r["key"]] = r
            except Exception:
                pass
    return done


def append_cache(rec):
    with open(CACHE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def build_samples():
    """市值分层在市15只 + 退市3只; 返回 (listed_codes, delisted_codes)"""
    cols = [r[1] for r in fund_con.execute("PRAGMA table_info(daily_basic)")]
    listed, delisted = [], []
    if "total_mv" in cols and "ts_code" in cols:
        td = fund_con.execute("SELECT MAX(trade_date) FROM daily_basic").fetchone()[0]
        rows = fund_con.execute(
            "SELECT ts_code, total_mv FROM daily_basic WHERE trade_date=? "
            "AND (ts_code LIKE '6%.SH' OR ts_code LIKE '0%.SZ') AND total_mv IS NOT NULL "
            "ORDER BY total_mv DESC", (td,)).fetchall()
        n = len(rows)
        top = [r[0].split(".")[0] for r in rows[:5]]
        mid = [r[0].split(".")[0] for r in rows[n // 2 - 2:n // 2 + 3]]
        small = [r[0].split(".")[0] for r in rows[-8:-3]]
        listed = list(dict.fromkeys(top + mid + small))
    else:
        rows = main_con.execute(
            "SELECT DISTINCT code FROM stock_factors_fundamental WHERE report_date='20251231' "
            "AND code LIKE '6%' LIMIT 15").fetchall()
        listed = [r[0] for r in rows]
    d2 = sqlite3.connect(r"file:D:/zcodesj/data/delisted_segments_baostock.db?mode=ro", uri=True)
    raw = [r[0] for r in d2.execute("SELECT DISTINCT code FROM stock_daily_delisted_raw LIMIT 60")]
    for c in ["sh.600005", "sh.600068"]:
        if c in raw:
            raw.remove(c)
        raw.insert(0, c)
    delisted = [c.split(".")[1] for c in raw[:3]]
    return listed, delisted


def main():
    random.seed(20261020)
    lg = bs.login()
    print("login:", lg.error_code)
    cache = load_cache()
    result = {"probe_time": time.strftime("%Y-%m-%d %H:%M:%S"),
              "t1_safety": {"keys": 0, "missing_bs": 0, "missing_pit": 0,
                            "violations": [], "gap_days": [], "global_map_mismatch": []},
              "t1_delisted": {"keys": 0, "in_sff": 0, "violations": []},
              "t2_tables": {}, "t3_roe": {"keys": 0, "missing": 0, "diffs": []},
              "names": {}}

    listed, delisted = build_samples()
    print("listed sample:", listed)
    print("delisted sample:", delisted)
    for c in listed + delisted:
        rs = bs.query_stock_basic(code=bs_code(c))
        rr = rows_of(rs)
        result["names"][c] = dict(zip(rs.fields, rr[0])).get("code_name", "?") if rr else "?"

    def pit_avail(code6, rd):
        row = main_con.execute(
            "SELECT pit_avail_date FROM stock_factors_fundamental "
            "WHERE code=? AND report_date=? LIMIT 1", (code6, rd)).fetchone()
        return row[0] if row else None

    def run_key(code6, rd, want_roe=False):
        key = "T1:%s:%s" % (code6, rd)
        if key in cache:
            return cache[key]
        pub = get_profit_pub(code6, rd)
        avail = pit_avail(code6, rd)
        rec = {"key": key, "pub": pub,
               "avail": str(avail) if avail is not None else None}
        if want_roe:
            row = main_con.execute(
                "SELECT roe FROM stock_factors_fundamental WHERE code=? AND report_date=? "
                "AND date<=? ORDER BY date DESC LIMIT 1", (code6, rd, avail or "20991231")).fetchone()
            rec["roe_sff"] = row[0] if row else None
            rec["roe_bs"] = get_dupont_roe(code6, rd)
        append_cache(rec)
        return rec

    # ---- T1 在市: 15只 × 8报告期(roe 只抽前6只)
    for i, code6 in enumerate(listed):
        w = (i < 6)
        for rd in REPORT_DATES:
            rec = run_key(code6, rd, want_roe=w)
            t1 = result["t1_safety"]
            t1["keys"] += 1
            if rec["pub"] in (None, ""):
                t1["missing_bs"] += 1
            if rec["avail"] is None:
                t1["missing_pit"] += 1
            if rec["pub"] and rec["avail"]:
                # 全局映射一致性: avail 应等于纯日历法定截止
                y = rd[:4]
                legal = {"03": y + "0430", "06": y + "0831",
                         "09": y + "1031", "12": str(int(y) + 1) + "0430"}[rd[4:6]]
                if rec["avail"] != legal:
                    t1["global_map_mismatch"].append({"key": rec["key"], "avail": rec["avail"], "legal": legal})
                pd_days = (time.strptime(rec["pub"], "%Y-%m-%d"),
                           time.strptime(rec["avail"], "%Y%m%d"))
                gap = (time.mktime(pd_days[1]) - time.mktime(pd_days[0])) / 86400.0
                t1["gap_days"].append(gap)
                if rec["pub"].replace("-", "") > rec["avail"]:
                    t1["violations"].append({"key": rec["key"], "pub": rec["pub"], "avail": rec["avail"]})
            if w and rec.get("roe_sff") is not None and rec.get("roe_bs"):
                t3 = result["t3_roe"]
                t3["keys"] += 1
                try:
                    d = abs(float(rec["roe_sff"]) - float(rec["roe_bs"]))
                    t3["diffs"].append(round(d, 4))
                except (TypeError, ValueError):
                    t3["missing"] += 1
            time.sleep(0.03)

    # ---- T1 退市: 2只 × 2报告期
    for code6 in delisted[:2]:
        for rd in ["20181231", "20211231"]:
            rec = run_key(code6, rd)
            t1d = result["t1_delisted"]
            t1d["keys"] += 1
            if rec["avail"] is not None:
                t1d["in_sff"] += 1
            if rec["pub"] and rec["avail"] and rec["pub"].replace("-", "") > rec["avail"]:
                t1d["violations"].append({"key": rec["key"], "pub": rec["pub"], "avail": rec["avail"]})

    # ---- T2 浅表直接对拍
    def t2_diff(table, sql_rows, bs_fetch, tag):
        out = {"keys": 0, "missing_bs": 0, "match": 0, "gap": [], "field": None, "rows": []}
        for code6, rd, ann in sql_rows:
            key = "T2:%s:%s:%s" % (tag, code6, rd)
            if key in cache:
                rec = cache[key]
            else:
                pub = bs_fetch(code6, rd)
                rec = {"key": key, "pub": pub, "ann": str(ann) if ann is not None else None}
                append_cache(rec)
            out["keys"] += 1
            if not rec["pub"]:
                out["missing_bs"] += 1
                continue
            g = (time.mktime(time.strptime(rec["ann"], "%Y%m%d"))
                 - time.mktime(time.strptime(rec["pub"], "%Y-%m-%d"))) / 86400.0
            out["gap"].append(g)
            if abs(g) <= 1:
                out["match"] += 1
            if len(out["rows"]) < 6:
                out["rows"].append({"code": code6, "rd": rd, "ann": rec["ann"], "pub": rec["pub"]})
        result["t2_tables"][tag] = out

    r1 = fund_con.execute(
        "SELECT ts_code, end_date, ann_date FROM fina_indicator "
        "WHERE ts_code LIKE '6%.SH' OR ts_code LIKE '0%.SZ' ORDER BY RANDOM() LIMIT 10").fetchall()
    t2_diff("fina_indicator", [(c.split(".")[0], e, a) for c, e, a in r1],
            lambda c, rd: get_profit_pub(c, rd), "fina_indicator")

    r2 = fund_con.execute(
        "SELECT ts_code, end_date, ann_date FROM express "
        "WHERE (ts_code LIKE '6%.SH' OR ts_code LIKE '0%.SZ') AND ann_date IS NOT NULL "
        "ORDER BY RANDOM() LIMIT 8").fetchall()

    def bs_express(code6, rd):
        # 该接口按公告日区间过滤, 快报公告在报告期末之后: 区间放宽至次年7月末
        rs = bs.query_performance_express_report(code=bs_code(code6),
                                                 start_date=bs_stat(rd),
                                                 end_date="%s-07-31" % (int(rd[:4]) + 1))
        rr = rows_of(rs)
        if not rr:
            return ""
        pf = pick_pub_field(rs.fields)
        return dict(zip(rs.fields, rr[0])).get(pf, "") if pf else ""

    t2_diff("express", [(c.split(".")[0], e, a) for c, e, a in r2], bs_express, "express")

    r3 = fund_con.execute(
        "SELECT ts_code, end_date, ann_date FROM forecast "
        "WHERE (ts_code LIKE '6%.SH' OR ts_code LIKE '0%.SZ') AND end_date>='20230101' "
        "ORDER BY RANDOM() LIMIT 8").fetchall()

    def bs_forecast(code6, rd):
        rs = bs.query_forecast_report(code=bs_code(code6), start_date=bs_stat(rd), end_date=bs_stat(rd))
        rr = rows_of(rs)
        if not rr:
            return ""
        pf = pick_pub_field(rs.fields)
        return dict(zip(rs.fields, rr[0])).get(pf, "") if pf else ""

    t2_diff("forecast", [(c.split(".")[0], e, a) for c, e, a in r3], bs_forecast, "forecast")

    bs.logout()
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    # 控制台摘要
    t1 = result["t1_safety"]
    gaps = sorted(t1["gap_days"])
    med = gaps[len(gaps) // 2] if gaps else None
    print("T1 keys=%d missing_bs=%d missing_pit=%d violations=%d gap_med=%s min=%s max=%s mapmismatch=%d"
          % (t1["keys"], t1["missing_bs"], t1["missing_pit"], len(t1["violations"]),
             med, gaps[0] if gaps else None, gaps[-1] if gaps else None, len(t1["global_map_mismatch"])))
    print("T1 delisted keys=%d in_sff=%d violations=%d"
          % (result["t1_delisted"]["keys"], result["t1_delisted"]["in_sff"], len(result["t1_delisted"]["violations"])))
    for tag, o in result["t2_tables"].items():
        print("T2 %s keys=%d missing_bs=%d match(±1d)=%d med_gap=%s"
              % (tag, o["keys"], o["missing_bs"], o["match"],
                 sorted(o["gap"])[len(o["gap"]) // 2] if o["gap"] else None))
    t3 = result["t3_roe"]
    dd = sorted(t3["diffs"])
    print("T3 roe keys=%d over0.5pp=%d med_diff=%s"
          % (t3["keys"], sum(1 for d in dd if d > 0.005), dd[len(dd) // 2] if dd else None))
    print("saved:", OUT_JSON)


if __name__ == "__main__":
    main()
