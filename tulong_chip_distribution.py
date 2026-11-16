#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tulong_chip_distribution.py — 屠龙版筹码分布工具（移植自 myhhub/stock, 8/16验证）
================================================================================
来源: stock_myhhub/instock/core/kline/cyq.py (标准三角形近似法, 宣称与东财一致)
移植要点:
  - 算法等价移植; 防除零/防负换手/精度修正
  - 价格用 stock_daily_meta 原始(未复权)close——筹码分布必须不复权(复权扭曲历史成本)
  - 换手率: daily_basic.turnover_rate 优先; 缺失日=volume/流通股本估算
    (流通股本=circ_mv/当日close, 快照间用最近快照前向填充)
输出(指定日期): 获利比例/平均成本/90%·70%成本区间/集中度/筹码重心
用法: python tulong_chip_distribution.py 600148 [--date 20260814] [--days 210] [--verbose]
"""
import argparse
import sqlite3
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from stock_name_lookup import require_name  # noqa: E402  名称铁律

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"


def load_kdata(code: str, end_date: int, days: int) -> pd.DataFrame:
    """K线+换手率: [date,open,close,high,low,volume,amount,turnover] 原始价"""
    end_dt = datetime.strptime(str(end_date), "%Y%m%d")
    start_dt = pd.Timestamp(end_dt) - pd.Timedelta(days=int(days * 2.2) + 90)
    start = int(start_dt.strftime("%Y%m%d"))
    con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT code, date, open, high, low, close, volume, amount FROM stock_daily_meta "
            "WHERE code=? AND date BETWEEN ? AND ? AND close > 0 ORDER BY date",
            con, params=[code, start, end_date])
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")

    # 换手率: daily_basic 优先(2024+日频), 缺失日估算=volume/流通股本
    con2 = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    try:
        db = pd.read_sql_query(
            "SELECT trade_date, turnover_rate, circ_mv FROM daily_basic "
            "WHERE ts_code LIKE ? AND trade_date BETWEEN ? AND ?",
            con2, params=[f"{code}.%", start, end_date])
    finally:
        con2.close()
    if len(db):
        db["date"] = pd.to_datetime(db["trade_date"].astype(str), format="mixed")
        db = db.drop_duplicates("date", keep="last").set_index("date")
        db["turnover_rate"] = pd.to_numeric(db["turnover_rate"], errors="coerce")
        db["circ_mv"] = pd.to_numeric(db["circ_mv"], errors="coerce")
        db["float_shares"] = db["circ_mv"] / df.set_index("date")["close"].reindex(db.index) * 1e4
        db["float_shares"] = db["float_shares"].ffill()   # 快照间前向填充
        db = db.dropna(subset=["float_shares"])
        db["est_turnover"] = df.set_index("date")["volume"] / db["float_shares"] * 100
        df = df.merge(db[["turnover_rate", "est_turnover"]], left_on="date", right_index=True,
                      how="left")
        df["turnover"] = df["turnover_rate"].fillna(df["est_turnover"]).fillna(0.0)
        df["turnover"] = df["turnover"].clip(lower=0.0, upper=100.0)
    else:
        df["turnover"] = 0.0
    df = df.tail(days + 20).reset_index(drop=True)
    return df[["date", "open", "close", "high", "low", "volume", "amount", "turnover"]]


class CYQCalculator:
    """三角形近似筹码分布（算法等价 myhhub/stock cyq.py, 防除零+负值修正）"""

    def __init__(self, kdata: pd.DataFrame, accuracy_factor=150, crange=120, cyq_days=210):
        self.klinedata = kdata
        self.fator = max(30, int(accuracy_factor))
        self.range = crange
        self.tradingdays = cyq_days

    def calc(self, index: int) -> dict:
        kd = self.klinedata
        # 窗口=以index为终点的最近tradingdays根（修正myhhub原版"index-119"错位语义）
        kdata = kd.iloc[max(0, index - self.tradingdays + 1): index + 1]
        if len(kdata) < 20:
            raise ValueError(f"K线不足(仅{len(kdata)}根), 无法计算筹码分布")
        factor = self.fator
        maxprice, minprice = float(kdata["high"].max()), float(kdata["low"].min())
        if maxprice <= minprice:
            maxprice, minprice = maxprice + 0.01, minprice - 0.01
        accuracy = max(0.01, (maxprice - minprice) / (factor - 1))
        currentprice = float(kdata.iloc[-1]["close"])
        yrange = [minprice + accuracy * i for i in range(factor)]
        xdata = [0.0] * factor

        for _, r in kdata.iterrows():
            o, c, h, l = float(r["open"]), float(r["close"]), float(r["high"]), float(r["low"])
            tr = min(1.0, max(0.0, float(r["turnover"]) / 100))
            avg = (o + c + h + l) / 4
            H = int((h - minprice) / accuracy)
            L = int((l - minprice) / accuracy + 0.99)
            H = min(H, factor - 1)
            L = max(L, 0)
            g0 = 2.0 / (h - l) if h > l else factor - 1
            g1 = int((avg - minprice) / accuracy)
            g1 = min(max(g1, 0), factor - 1)
            for n in range(factor):
                xdata[n] *= (1 - tr)
            if h == l:
                xdata[g1] += g0 * tr / 2
            else:
                for j in range(L, H + 1):
                    curprice = minprice + accuracy * j
                    if curprice <= avg:
                        if abs(avg - l) < 1e-8:
                            xdata[j] += g0 * tr
                        else:
                            xdata[j] += (curprice - l) / (avg - l) * g0 * tr
                    else:
                        if abs(h - avg) < 1e-8:
                            xdata[j] += g0 * tr
                        else:
                            xdata[j] += (h - curprice) / (h - avg) * g0 * tr

        total = sum(xdata)

        def cost_at(chip: float) -> float:
            acc = 0.0
            for i in range(factor):
                if acc + xdata[i] > chip:
                    return minprice + i * accuracy
                acc += xdata[i]
            return maxprice

        def percent_range(percent: float) -> dict:
            ps = [(1 - percent) / 2, (1 + percent) / 2]
            p0, p1 = cost_at(total * ps[0]), cost_at(total * ps[1])
            return {"price_range": [round(p0, 2), round(p1, 2)],
                    "concentration": 0 if p0 + p1 == 0 else round((p1 - p0) / (p0 + p1), 4)}

        benefit = sum(x for x, p in zip(xdata, yrange) if p <= currentprice) / total if total else 0
        return {"date": kdata.iloc[-1]["date"].strftime("%Y%m%d"), "trading_days": len(kdata),
                "benefit_part": round(benefit, 4), "avg_cost": round(cost_at(total * 0.5), 2),
                "current_price": round(currentprice, 2),
                "percent_90": percent_range(0.9), "percent_70": percent_range(0.7),
                "price_low": round(minprice, 2), "price_high": round(maxprice, 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description="屠龙筹码分布(原始价+换手率, 三角形近似)")
    ap.add_argument("code", help="股票代码, 如600148")
    ap.add_argument("--date", type=int, default=0, help="截止日期YYYYMMDD, 默认最新交易日")
    ap.add_argument("--days", type=int, default=210)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    code = a.code.strip()

    if not a.date:
        con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=30)
        a.date = con.execute(
            "SELECT MAX(date) FROM stock_daily_meta WHERE code=?", (code,)).fetchone()[0]
        con.close()
    if not a.date:
        print(f"[拒绝] {code} 无行情数据")
        return 1

    kd = load_kdata(code, a.date, a.days)
    if len(kd) < 30:
        print(f"[拒绝] {code} K线不足({len(kd)}根)")
        return 1
    cal = CYQCalculator(kd, cyq_days=a.days)
    r = cal.calc(len(kd) - 1)
    name = require_name(code)
    print(f"\n=== 筹码分布 {code} {name} @ {r['date']} ===")
    print(f"现价 {r['current_price']} | 平均成本 {r['avg_cost']} | 获利比例 {r['benefit_part']:.1%}")
    print(f"90%成本区间 [{r['percent_90']['price_range'][0]}, {r['percent_90']['price_range'][1]}] "
          f"集中度 {r['percent_90']['concentration']}")
    print(f"70%成本区间 [{r['percent_70']['price_range'][0]}, {r['percent_70']['price_range'][1]}] "
          f"集中度 {r['percent_70']['concentration']}")
    print(f"区间 [{r['price_low']}, {r['price_high']}] | 计算天数 {r['trading_days']}")
    pos = ("获利盘为主" if r["benefit_part"] > 0.6 else
           "套牢盘为主" if r["benefit_part"] < 0.4 else "多空均衡")
    print(f"解读: {pos} | 现价 vs 平均成本: "
          f"{'上方' if r['current_price'] > r['avg_cost'] else '下方'} "
          f"{abs(r['current_price'] - r['avg_cost']) / r['avg_cost']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
