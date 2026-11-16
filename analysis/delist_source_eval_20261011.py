# -*- coding: utf-8 -*-
"""呈请4阶段一(只读评估): 退市股历史行情备源覆盖率 + 本地qfq幸存者偏差下界 (2026-10-11)

呈请来源: rightside_spec_coverage §三呈请4 (观察级; 本件只评估不回填, 回填/重建动态池须主人裁决)。
v2(20261011): tushare stock_basic 本账户限频1次/小时不可用, 全程改baostock:
  - 退市保留性: query_stock_basic(outDate/status) + 全区间K末bar vs outDate (6只已知退市样本)
  - tushare daily: 仅测逐股K线可得性(官方退市日以baostock outDate为对照)
  - 腾讯ifzq: 同样本探测(预期不保留退市股)
  - qfq下界: qfq码集 - baostock当日上市宇宙 = 库内退市股名单(逐股query_stock_basic枚举outDate)
    × 4,313事件交叠 = 已入事件但现已退市的样本统计; 完全缺席qfq的退市股=不可计缺失如实标注。
口径: 备源一律不复权原始价(backup_daily_probe.py为口径owner); 本件只读评估零交易权。
输出: stdout + reports/delist_source_eval_20261011/delist_source_eval.json
"""
import os, sys, json, time
import requests
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
OUT_DIR = r"D:\zcodesj\reports\delist_source_eval_20261011"
TESTS = [("sz", "300104", "乐视网"), ("sz", "002450", "康得新"), ("sz", "300028", "金亚科技"),
         ("sh", "601558", "退市锐电"), ("sz", "300216", "千山药机"), ("sz", "300156", "神雾环保")]
STK_PREFIX = ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605")
os.makedirs(OUT_DIR, exist_ok=True)


def bs_login():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login fail {lg.error_msg}")
    return bs


def probe_baostock(bs):
    out = {}
    for mkt, code, name in TESTS:
        full = f"{mkt}.{code}"
        b = bs.query_stock_basic(code=full)
        meta = {}
        while b.error_code == "0" and b.next():
            meta = dict(zip(b.fields, b.get_row_data()))
        rs = bs.query_history_k_data_plus(full, "date,close", start_date="2010-01-01",
                                          end_date="", frequency="d", adjustflag="3")
        dates = []
        while rs.error_code == "0" and rs.next():
            dates.append(rs.get_row_data()[0].replace("-", ""))
        out[full] = {"status": meta.get("status"), "ipo": meta.get("ipoDate"),
                     "outDate": meta.get("outDate"),
                     "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                     "bars": len(dates),
                     "retained_full": bool(dates and meta.get("outDate") and dates[-1] == meta["outDate"].replace("-", ""))}
    return out


def probe_tushare_daily(bs):
    """tushare逐股daily可得性; 官方退市日=baostock outDate"""
    import tushare as ts
    tok = os.environ.get("TUSHARE_TOKEN", "").strip() or open(r"D:\zcodesj\config\tushare_token.txt", encoding="utf-8").read().strip()
    pro = ts.pro_api(tok)
    out = {}
    for mkt, code, name in TESTS:
        tc = f"{code}.{mkt.upper()}"
        k = pro.daily(ts_code=tc, start_date="20100101", end_date="").sort_values("trade_date")
        out[tc] = {"first": str(k.trade_date.iloc[0]), "last": str(k.trade_date.iloc[-1]), "bars": int(len(k))}
        time.sleep(0.35)
    return out


def probe_tencent():
    out = {}
    for mkt, code, name in TESTS:
        try:
            r = requests.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                             params={"param": f"{mkt}{code},day,,,9000,"},
                             headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"}, timeout=15)
            d = (r.json().get("data") or {}).get(f"{mkt}{code}") or {}
            rows = d.get("day") or d.get("qfqday") or []
            out[f"{mkt}{code}"] = {"bars": len(rows),
                                   "last": rows[-1][0].replace("-", "") if rows else None}
        except Exception as e:
            out[f"{mkt}{code}"] = {"error": str(e)[:80]}
        time.sleep(0.3)
    return out


def today_universe(bs):
    rs = bs.query_all_stock(day="2026-10-09")  # 空参返回空集, 须显式最近交易日
    codes = set()
    while rs.error_code == "0" and rs.next():
        c = rs.get_row_data()[0]
        tail = c.split(".")[-1]
        if c.split(".")[0] in ("sz", "sh") and tail.startswith(STK_PREFIX):
            codes.add(tail)
    return codes


def main():
    out = {"meta": {"date": "2026-10-11", "spec": "呈请4阶段一只读评估(v2 baostock全程)",
                    "tests": [t[2] for t in TESTS]}}
    partial = os.path.join(OUT_DIR, "delist_source_eval.json")
    bs = bs_login()
    try:
        print("[probe] baostock 退市保留性 ...")
        out["baostock"] = probe_baostock(bs)
        json.dump(out, open(partial, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for k, v in out["baostock"].items():
            print(f"  {k}: status={v['status']} out={v['outDate']} last={v['last']} bars={v['bars']} retained_full={v['retained_full']}")
        print("[probe] tushare daily 逐股可得性 ...")
        out["tushare_daily"] = probe_tushare_daily(bs)
        json.dump(out, open(partial, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for k, v in out["tushare_daily"].items():
            print(f"  {k}: first={v['first']} last={v['last']} bars={v['bars']}")
        print("[probe] 腾讯ifzq ...")
        out["tencent"] = probe_tencent()
        json.dump(out, open(partial, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for k, v in out["tencent"].items():
            print(f"  {k}: {v}")
        print("[universe] baostock当日上市宇宙 ...")
        today = today_universe(bs)
        print(f"  当日股票宇宙: {len(today)}")
        print("[roster] qfq库内退市股枚举(逐股query_stock_basic) ...")
        cq = __import__("sqlite3").connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
        qcodes = sorted({r[0] for r in cq.execute("select distinct code from stock_daily_meta_qfq").fetchall()})
        cq.close()
        gone = [c for c in qcodes if c not in today]
        roster = {}
        suspended_unknown = []
        for c in gone:
            mkt = "sh" if c.startswith("6") else "sz"
            b = bs.query_stock_basic(code=f"{mkt}.{c}")
            meta = {}
            while b.error_code == "0" and b.next():
                meta = dict(zip(b.fields, b.get_row_data()))
            od = meta.get("outDate")
            if od:  # 真退市(有退市日); 无outDate=停牌/数据暂停等非退市缺席
                roster[c] = {"name": meta.get("code_name"), "outDate": od,
                             "status": meta.get("status")}
            else:
                suspended_unknown.append(c)
        out["qfq_absent_not_delisted"] = len(suspended_unknown)
        out["qfq_delisted_by_year"] = {}
        for c, m in roster.items():
            y = (m.get("outDate") or "????")[:4]
            out["qfq_delisted_by_year"][y] = out["qfq_delisted_by_year"].get(y, 0) + 1
        out["qfq_delisted_roster_sample"] = dict(sorted(roster.items())[:400])
        print(f"  qfq库内真退市股: {len(roster)} 只 (另有停牌/数据暂停缺席 {len(suspended_unknown)} 只非退市)  退市年分布: {out['qfq_delisted_by_year']}")
    finally:
        bs.logout()
    # 幸存者偏差下界(事件交叠, 仅真退市码)
    em = pd.read_csv(r"D:\zcodesj\reports\rightside_startup_20261011\events_main.csv", dtype={"code": str})
    m = em.code.isin(set(roster.keys()))
    sub = em[m]
    out["qfq_bound"] = {"events_total": int(len(em)),
                        "delisted_events": int(m.sum()),
                        "delisted_event_codes": int(sub.code.nunique()),
                        "delisted_events_h60": ({"n": int(sub.f60_net.notna().sum()),
                                                 "mean": round(float(sub.f60_net.mean()), 3),
                                                 "win": round(float((sub.f60_net > 0).mean() * 100), 1)}
                                                if m.sum() else None),
                        "all_events_h60_mean": round(float(em.f60_net.mean()), 3),
                        "note": "完全缺席qfq的退市股(未上市即退/早期退市未入库)不可计, 真实偏差≥此下界"}
    json.dump(out, open(partial, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[bound] {out['qfq_bound']}")
    print(f"[out] {partial}")


if __name__ == "__main__":
    main()
