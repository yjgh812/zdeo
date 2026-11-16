# -*- coding: utf-8 -*-
"""delisted_segment_backfill_20261018.py — 退市段回填件·方案A (2026-10-18, DeepSeek外部审计V4/P1呈请立项)

事实链: wb qfq 主库零退市段(生产源结构性活股化, 幸存者偏差回归)——回填=修复敞口。
方案A(整合呈请件§二, 主人「继续」按推荐立项): **独立退市段研究库, 零触 wb 主表/生产读路径**。

口径铁律(不可违):
  1) baostock 用涨跌幅复权算法, 与新浪口径不同——本库存 **raw 原始价 + baostock 复权因子** 两表,
     退市段整段自洽使用(fwd 收益用 baostock 因子域内自算), **禁止与 sina 段跨源拼接**;
  2) 数据源=baostock(10/11 评估 6/6 退市股完整保留实测, 唯一可行批量源);
  3) 宇宙=宪法定义域(沪深主板+创业板 60/00/30 前缀, 剔 688/689 科创与北交), 2015-01-01 起取数;
  4) 幂等(INSERT OR REPLACE), 复跑安全; 库=data/delisted_segments_baostock.db(研究侧)。

退市名单: baostock query_stock_basic 全量 → type=1(股票)∧status=0(退市)∧前缀过滤。
输出: 库内三表 + stdout 汇总(码数/行数/失败清单 fail-closed)。
"""
import os
import sqlite3
import sys
from datetime import datetime

import baostock as bs
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
OUT_DB = r"D:\zcodesj\data\delisted_segments_baostock.db"
START = "2015-01-01"
END = datetime.now().strftime("%Y-%m-%d")  # 未来日期会被baostock静默返回空(10/18首跑实证), END=当天
KEEP_PREFIX = ("60", "00", "30")   # 沪深主板+创业板(宪法定义域)
DROP_PREFIX = ("688", "689")       # 科创板(宪法剔除)


def init_db():
    con = sqlite3.connect(OUT_DB)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS stock_daily_delisted_raw (
            code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
            volume INTEGER, amount REAL, pctchg REAL,
            PRIMARY KEY (code, date));
        CREATE TABLE IF NOT EXISTS adjust_factor (
            code TEXT, date TEXT, foreAdjustFactor REAL, backAdjustFactor REAL,
            PRIMARY KEY (code, date));
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY, value TEXT);
        """
    )
    con.close()


def main():
    lg = bs.login()
    if lg.error_code != "0":
        print(f"[FAIL] baostock 登录失败: {lg.error_msg}")
        return 1
    try:
        init_db()
        # 1) 退市名单: 全量基础表 → type=1股票 ∧ status=0退市 ∧ 宪法定义域前缀
        rs = bs.query_stock_basic()
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        basic = pd.DataFrame(rows, columns=rs.fields)
        n_all = len(basic)
        basic = basic[(basic["type"] == "1") & (basic["status"] == "0")]
        basic = basic[basic["code"].str[3:5].isin(list(KEEP_PREFIX)) |
                      basic["code"].str[3:6].isin(list(KEEP_PREFIX))]
        basic = basic[~basic["code"].str.contains("688|689")]
        if "outDate" in basic.columns:
            basic = basic[basic["outDate"] >= START]  # 2015前已退市的码取不到窗内bar, 剔除省调用
        codes = sorted(basic["code"].unique())
        print(f"[名单] 全量{n_all} → 退市股票{len(basic)} → 宪法域去重 {len(codes)} 码")
        if not codes:
            print("[FAIL] 退市名单为空 — 名单查询口径异常, 结果作废")
            return 1

        con = sqlite3.connect(OUT_DB)
        ok, failed, n_bars, n_fac = 0, [], 0, 0
        outdate_map = dict(zip(basic["code"], basic["outDate"])) if "outDate" in basic.columns else {}

        def fv(x):
            """停牌日空串守卫: ''/None→0.0"""
            return float(x) if x not in ("", None) else 0.0

        for i, code in enumerate(codes, 1):
            # baostock 对 end 越过退市日的区间静默返回空(10/18实证)——逐码钳到 outDate
            # turnover 字段本机版本报 10004012 参数错误(10/18实证)——弃用, 量额已足研究用
            code_end = min(END, outdate_map.get(code, END)) if outdate_map.get(code) else END
            try:
                rs = bs.query_history_k_data_plus(
                    code, "date,open,high,low,close,volume,amount,pctChg",
                    start_date=START, end_date=code_end, frequency="d", adjustflag="3")  # 3=不复权
                if rs.error_code != "0":
                    failed.append(f"{code}: k-data err {rs.error_code} {rs.error_msg[:60]}")
                else:
                    bars = []
                    while rs.error_code == "0" and rs.next():
                        bars.append(rs.get_row_data())
                    if bars:
                        bdf = pd.DataFrame(bars, columns=rs.fields)
                        bdf["vol_n"] = pd.to_numeric(bdf["volume"], errors="coerce")
                        bdf = bdf[bdf["vol_n"] > 0]
                        if len(bdf):
                            con.executemany(
                                "INSERT OR REPLACE INTO stock_daily_delisted_raw VALUES (?,?,?,?,?,?,?,?,?)",
                                [(code, r["date"], fv(r["open"]), fv(r["high"]), fv(r["low"]),
                                  fv(r["close"]), int(fv(r["volume"])), fv(r["amount"]), fv(r["pctChg"]))
                                 for _, r in bdf.iterrows()])
                            n_bars += len(bdf)
            except Exception as e:
                failed.append(f"{code}: bars {e}")
            try:
                rf = bs.query_adjust_factor(code, start_date=START, end_date=code_end)
                facs = []
                while rf.error_code == "0" and rf.next():
                    facs.append(rf.get_row_data())
                if facs:
                    fdf = pd.DataFrame(facs, columns=rf.fields)
                    con.executemany(
                        "INSERT OR REPLACE INTO adjust_factor VALUES (?,?,?,?)",
                        [(code, r["dividOperateDate"], fv(r["foreAdjustFactor"]),
                          fv(r["backAdjustFactor"])) for _, r in fdf.iterrows()])
                    n_fac += len(fdf)
            except Exception as e:
                failed.append(f"{code}: factor {e}")
            if i % 20 == 0 or i == len(codes):
                print(f"[{i}/{len(codes)}] ok={len(codes)-len(failed)} fail={len(failed)} bars={n_bars} factors={n_fac}", flush=True)
        ok = len(codes) - len(failed)
        con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
            ("source", "baostock(涨跌幅复权口径; 禁与sina段跨源拼接)"),
            ("fetched_at", datetime.now().isoformat(timespec="seconds")),
            ("window", f"{START}~{END}"),
            ("codes_ok", str(ok)), ("codes_failed", ";".join(failed)[:500]),
            ("volume_unit", "股"), ("raw_flag", "adjustflag=3 不复权原始价"),
        ])
        con.commit()
        con.close()
        print(f"[DONE] ok={ok}/{len(codes)} bars={n_bars} factors={n_fac} failed={len(failed)}")
        if failed:
            for f in failed[:10]:
                print("  [FAIL-ITEM]", f)
            return 1 if ok == 0 else 0
        return 0
    finally:
        bs.logout()


if __name__ == "__main__":
    sys.exit(main())
