# -*- coding: utf-8 -*-
"""交易登记表v2: 全部已证实交易(交割单/截图/楼层) K线定性 — 2019全年统计+全期汇总"""
import csv
import os
import sqlite3
import struct

VIPDOC = r"D:\anzhuang\tdx\vipdoc"
OUT = r"D:\zcodesj\reports\mantou_trade_events_v2_20261003.csv"


def read_day(code, market):
    path = os.path.join(VIPDOC, market, "lday", market + code + ".day")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "rb") as f:
        while True:
            buf = f.read(32)
            if len(buf) < 32:
                break
            d, o, h, l, c = struct.unpack("<IIIII", buf[:20])
            rows.append((d, o / 100, h / 100, l / 100, c / 100))
    return rows


EVENTS = [
    # (code, name, market, date, action, price, source)
    ("002941", "新疆交建", "sz", 20190104, "buy", 25.19, "截图成本(1/4涨停日)"),
    ("000595", "宝塔实业", "sz", 20190110, "sell", 3.94, "截图(-10.05%止损)"),
    ("002941", "新疆交建", "sz", 20190110, "sell", 36.51, "截图(现价)"),
    ("002437", "誉衡药业", "sz", 20190122, "hold", 4.09, "截图(时名芭田股份,副账户)"),
    ("600816", "建元信托", "sh", 20190213, "hold", 8.39, "截图(时名安信信托,-6.3%扛单)"),
    ("600816", "建元信托", "sh", 20190227, "buy", 8.94, "交割单"),
    ("600816", "建元信托", "sh", 20190301, "sell", 6.80, "交割单(-11.8万)"),
    ("600624", "复旦复华", "sh", 20190315, "hold", 10.99, "截图(涨停)"),
    ("600268", "国电南自", "sh", 20190315, "sell", 7.17, "截图(-10.04%止损)"),
    ("002177", "御银股份", "sz", 20190403, "hold", 7.43, "截图(+34.9%满仓)"),
    ("603956", "威派格", "sh", 20190404, "buy", 35.11, "交割单"),
    ("600758", "辽宁能源", "sh", 20190408, "buy", 6.62, "截图(时名红阳能源)"),
    ("002547", "*ST春兴", "sz", 20190408, "buy", 11.65, "截图(时名春兴精工)"),
    ("300766", "每日互动", "sz", 20190408, "sell", 0, "截图(清仓-15.9万)"),
    ("300421", "力星股份", "sz", 20190509, "buy", 11.70, "截图反解"),
    ("603390", "通达电气", "sh", 20191220, "sell", 20.37, "截图S点"),
    ("000712", "锦龙股份", "sz", 20200731, "buy", 18.62, "截图反解"),
    ("601216", "君正集团", "sh", 20200731, "sell", 7.47, "截图反解"),
    ("002421", "达实智能", "sz", 20260608, "buy", 5.61, "截图反解"),
]


def limit_ratio(code, name):
    return 0.048 if "ST" in name else 0.095


rows_out = []
stats = {"buy_zt": 0, "buy_n": 0, "sell_strong": 0, "sell_n": 0}
seen = {}
for code, name, mkt, d, action, price, src in EVENTS:
    rows = read_day(code, mkt)
    idx = next((i for i, r in enumerate(rows) if r[0] == d), None)
    if idx is None or idx == 0:
        rows_out.append([code, name, d, action, price, src, "NO_DATA"] + [""] * 8)
        continue
    c = rows[idx][4]
    prev = rows[idx - 1][4]
    chg = c / prev - 1
    is_zt = chg >= limit_ratio(code, name)
    hi60 = max(r[2] for r in rows[max(0, idx - 59):idx + 1])
    lo60 = min(r[3] for r in rows[max(0, idx - 59):idx + 1])
    pos60 = (c - lo60) / (hi60 - lo60) * 100 if hi60 > lo60 else 50
    f5 = rows[idx + 5][4] / c - 1 if idx + 5 < len(rows) else None
    f20 = rows[idx + 20][4] / c - 1 if idx + 20 < len(rows) else None
    tag = action
    if action == "buy":
        stats["buy_n"] += 1
        tag = "涨停日买" if is_zt else "非板买"
        if is_zt:
            stats["buy_zt"] += 1
    elif action == "sell":
        stats["sell_n"] += 1
        if chg >= 0.03 or is_zt:
            stats["sell_strong"] += 1
            tag = "强中卖"
        elif chg <= -0.048:
            tag = "止损卖"
        else:
            tag = "普通卖"
    rows_out.append([
        code, name, d, action, price, src, tag,
        f"{chg*100:.2f}", f"{pos60:.0f}",
        f"{f5*100:.2f}" if f5 is not None else "", f"{f20*100:.2f}" if f20 is not None else "",
    ])

with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["code", "name", "date", "action", "price", "source", "tag",
                "chg%", "pos60", "fwd5%", "fwd20%"])
    w.writerows(rows_out)

print(stats)
b20 = [float(r[10]) for r in rows_out if r[3] == "buy" and r[10] != ""]
s20 = [float(r[10]) for r in rows_out if r[3] == "sell" and r[10] != ""]
import statistics as st
if b20:
    print(f"买入fwd20: n={len(b20)} mean={st.mean(b20):+.1f}% median={st.median(b20):+.1f}%")
if s20:
    print(f"卖出后fwd20: n={len(s20)} mean={st.mean(s20):+.1f}% median={st.median(s20):+.1f}% (负=卖得好)")
print("saved", OUT)
