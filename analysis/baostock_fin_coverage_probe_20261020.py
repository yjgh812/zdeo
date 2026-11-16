# -*- coding: utf-8 -*-
"""1-A BaoStock 季频财务接口覆盖探测(只读零风险, 呈请1前置, 2026-10-20).

目的: 确定六接口覆盖起点下界与退市股财务可得性, 不预设年份。
- 六接口 × 浦发银行(sh.600000, 1999上市) 自 2005 年逐年回探首个非空 pubDate 季度。
- 退市样本 sh.600005(武钢股份,2017退市) / sh.600068(葛洲坝,2022换股终止上市):
  探测退市前年度报告期 pubDate 可得性。
产出: 同名 .json 结果 + .md 报告(另写)。
用法: python analysis/baostock_fin_coverage_probe_20261020.py
"""
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import baostock as bs

OUT_JSON = r"D:\zcodesj\analysis\baostock_fin_coverage_probe_result_20261020.json"

INTERFACES = [
    ("query_profit_data", lambda c, y, q: bs.query_profit_data(code=c, year=y, quarter=q)),
    ("query_balance_data", lambda c, y, q: bs.query_balance_data(code=c, year=y, quarter=q)),
    ("query_cash_flow_data", lambda c, y, q: bs.query_cash_flow_data(code=c, year=y, quarter=q)),
    ("query_growth_data", lambda c, y, q: bs.query_growth_data(code=c, year=y, quarter=q)),
    ("query_operation_data", lambda c, y, q: bs.query_operation_data(code=c, year=y, quarter=q)),
    ("query_dupont_data", lambda c, y, q: bs.query_dupont_data(code=c, year=y, quarter=q)),
]


def rows_of(rs):
    out = []
    while (rs.error_code == "0") and rs.next():
        out.append(rs.get_row_data())
    return out


def probe_quarter(code, fn, y, q):
    """返回 (has_row, pubDate, statDate)"""
    rs = fn(code, y, q)
    rr = rows_of(rs)
    if not rr:
        return (False, "", "")
    d = dict(zip(rs.fields, rr[0]))
    return (True, d.get("pubDate", ""), d.get("statDate", ""))


def first_cover(code, fn, y_lo=2005, y_hi=2022):
    """先探每年Q4, 命中年后再找该年首个非空季度。返回 dict 或 None。"""
    hit_year = None
    for y in range(y_lo, y_hi + 1):
        ok, _, _ = probe_quarter(code, fn, y, 4)
        if ok:
            hit_year = y
            break
    if hit_year is None:
        return None
    for q in range(1, 5):
        ok, pub, stat = probe_quarter(code, fn, hit_year, q)
        if ok:
            return {"first_year": hit_year, "first_quarter": q,
                    "pubDate": pub, "statDate": stat}
    return {"first_year": hit_year, "first_quarter": 4, "pubDate": "", "statDate": ""}


def main():
    t0 = time.time()
    lg = bs.login()
    print("login:", lg.error_code, lg.error_msg)
    result = {"probe_time": time.strftime("%Y-%m-%d %H:%M:%S"),
              "listed_probe": {}, "delisted_probe": {}, "names": {}}

    # 名称真源: baostock 自身
    for code in ["sh.600000", "sh.600005", "sh.600068"]:
        rs = bs.query_stock_basic(code=code)
        rr = rows_of(rs)
        result["names"][code] = dict(zip(rs.fields, rr[0])).get("code_name", "?") if rr else "?"

    # 在市样本: 六接口覆盖起点
    for name, fn in INTERFACES:
        r = first_cover("sh.600000", fn)
        result["listed_probe"][name] = r
        print(f"[600000] {name}: {r}")

    # 退市样本: 退市前若干报告期 pubDate 可得性
    delisted_cases = [
        ("sh.600005", [(2016, 4), (2015, 4), (2013, 4)]),
        ("sh.600068", [(2021, 4), (2019, 4), (2016, 4)]),
    ]
    for code, cases in delisted_cases:
        per = {}
        for name, fn in INTERFACES:
            sub = {}
            for (y, q) in cases:
                ok, pub, stat = probe_quarter(code, fn, y, q)
                sub[f"{y}Q{q}"] = {"ok": ok, "pubDate": pub}
            per[name] = sub
        result["delisted_probe"][code] = per
        print(f"[delisted {code}] profit 2016Q4:", per["query_profit_data"])

    bs.logout()
    result["elapsed_sec"] = round(time.time() - t0, 1)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("saved:", OUT_JSON, "elapsed", result["elapsed_sec"])


if __name__ == "__main__":
    main()
