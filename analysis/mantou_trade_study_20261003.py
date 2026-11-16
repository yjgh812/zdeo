# -*- coding: utf-8 -*-
"""两个小馒头交易事件数据集: 楼层/交割单已证实的交易, 用vipdoc不复权K线逐笔定性"""
import csv
import os
import struct

VIPDOC = r"D:\anzhuang\tdx\vipdoc"
OUT_CSV = r"D:\zcodesj\reports\mantou_trade_events_20261003.csv"


def read_day(code, market):
    path = os.path.join(VIPDOC, market, "lday", market + code + ".day")
    rows = []
    with open(path, "rb") as f:
        while True:
            buf = f.read(32)
            if len(buf) < 32:
                break
            d, o, h, l, c = struct.unpack("<IIIII", buf[:20])
            amount, vol = struct.unpack("<fI", buf[20:28])
            rows.append({"date": d, "o": o / 100, "h": h / 100, "l": l / 100,
                         "c": c / 100, "vol": vol})
    return rows


# 事件表: code, name, market, date, action, price(0=用当日收盘), 来源等级
EVENTS = [
    ("600816", "建元信托", "sh", 20190227, "buy", 8.94, "交割单"),
    ("600816", "建元信托", "sh", 20190301, "sell", 6.80, "交割单"),
    ("601106", "中国一重", "sh", 20190227, "sell", 4.00, "交割单"),
    ("002526", "山东矿机", "sz", 20190301, "buy", 2.82, "交割单"),
    ("000599", "青岛双星", "sz", 20190404, "sell", 6.27, "交割单"),
    ("603956", "威派格", "sh", 20190404, "buy", 35.11, "交割单"),
    ("300421", "力星股份", "sz", 20190509, "buy", 11.70, "截图反解"),
    ("000712", "锦龙股份", "sz", 20200731, "buy", 18.62, "截图反解"),
    ("601216", "君正集团", "sh", 20200731, "sell", 7.47, "截图反解"),
    ("002421", "达实智能", "sz", 20260608, "buy", 5.61, "截图反解"),
    ("603390", "通达电气", "sh", 20191220, "sell", 20.37, "截图S点"),
    ("601990", "南京证券", "sh", 20191218, "buy", 0, "楼层自述"),
    ("002927", "泰永长征", "sz", 20181224, "hold", 0, "楼层自报资产"),
    ("603220", "贝通信", "sh", 20181227, "hold", 0, "楼层自报资产"),
    ("002243", "力合科创", "sz", 20181219, "hold", 0, "楼层(时名通产丽星)"),
    ("600233", "圆通速递", "sh", 20200207, "buy", 0, "楼层(刚好新高)"),
    ("000700", "模塑科技", "sz", 20200110, "buy", 0, "楼层(半仓)"),
    ("600365", "ST通葡", "sh", 20230926, "sell", 4.58, "截图(-37.24万)"),
]


def limit_ratio(code, name):
    if "ST" in name:
        return 0.048
    return 0.095


rows_out = []
stats = {"buy_zt": 0, "buy_n": 0, "sell_strength": 0, "sell_n": 0}
for code, name, mkt, d, action, price, src in EVENTS:
    rows = read_day(code, mkt)
    idx = next((i for i, r in enumerate(rows) if r["date"] == d), None)
    if idx is None:
        rows_out.append([code, name, d, action, price, src, "NO_DATA"] + [""] * 9)
        continue
    r = rows[idx]
    prev = rows[idx - 1] if idx else None
    chg = (r["c"] / prev["c"] - 1) if prev else 0
    is_zt = chg >= limit_ratio(code, name)
    vol5 = sum(x["vol"] for x in rows[max(0, idx - 5):idx]) / 5 if idx else 0
    volr = r["vol"] / vol5 if vol5 else 0
    hi60 = max(x["h"] for x in rows[max(0, idx - 60):idx + 1])
    lo60 = min(x["l"] for x in rows[max(0, idx - 60):idx])
    pos60 = (r["c"] - lo60) / (hi60 - lo60) * 100 if hi60 > lo60 else 50
    f1 = rows[idx + 1]["c"] / r["c"] - 1 if idx + 1 < len(rows) else None
    f5 = rows[idx + 5]["c"] / r["c"] - 1 if idx + 5 < len(rows) else None
    f20 = rows[idx + 20]["c"] / r["c"] - 1 if idx + 20 < len(rows) else None
    use_px = price if price else r["c"]
    tag = ""
    if action == "buy":
        stats["buy_n"] += 1
        if is_zt:
            stats["buy_zt"] += 1
            tag = "涨停日买"
        elif volr >= 1.5:
            tag = "放量非板买"
        else:
            tag = "普通买"
    elif action == "sell":
        stats["sell_n"] += 1
        if chg >= 0.03 or is_zt:
            stats["sell_strength"] += 1
            tag = "强中卖"
        elif chg <= -0.048:
            tag = "止损卖"
        else:
            tag = "普通卖"
    else:
        tag = "持有"
    rows_out.append([
        code, name, d, action, use_px, src, tag,
        f"{chg*100:.2f}", f"{volr:.2f}", f"{pos60:.0f}",
        f"{f1*100:.2f}" if f1 is not None else "", f"{f5*100:.2f}" if f5 is not None else "",
        f"{f20*100:.2f}" if f20 is not None else "",
    ])

with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["code", "name", "date", "action", "price", "source", "tag",
                "chg%", "volr_vs5d", "pos60", "fwd1%", "fwd5%", "fwd20%"])
    w.writerows(rows_out)

print("=== 统计 ===")
print(stats)
print("=== 明细 ===")
for r in rows_out:
    print(r)
print("saved", OUT_CSV)
