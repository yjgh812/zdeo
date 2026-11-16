# -*- coding: utf-8 -*-
"""选股alpha验证: 她的每笔买入 vs 同期上证指数超额 + 同题材A/B对照(模塑vs星期六)"""
import os
import struct

VIPDOC = r"D:\anzhuang\tdx\vipdoc"


def read_day(code, market):
    path = os.path.join(VIPDOC, market, "lday", market + code + ".day")
    rows = []
    with open(path, "rb") as f:
        while True:
            buf = f.read(32)
            if len(buf) < 32:
                break
            d, o, h, l, c = struct.unpack("<IIIII", buf[:20])
            rows.append((d, o / 100, h / 100, l / 100, c / 100))
    return rows


IDX = {r[0]: r[4] for r in read_day("000001", "sh")}  # 上证指数收盘


def fwd(code, market, d, n=20):
    rows = read_day(code, market)
    idx = next((i for i, r in enumerate(rows) if r[0] == d), None)
    if idx is None or idx + n >= len(rows):
        return None, None
    base = rows[idx][4]
    stock = rows[idx + n][4] / base - 1
    d0, d1 = rows[idx][0], rows[idx + n][0]
    ikeys = [k for k in IDX if d0 <= k <= d1]
    if len(ikeys) < 2:
        return stock, None
    i_base = IDX[ikeys[0]]
    ind = IDX[ikeys[-1]] / i_base - 1
    return stock, stock - ind


print("=== 她的买入 vs 同期上证(fwd20) ===")
buys = [
    ("600816", "sh", 20190301, "建元信托(3.1卖)"),
    ("002526", "sz", 20190301, "山东矿机"),
    ("603956", "sh", 20190404, "威派格"),
    ("300421", "sz", 20190509, "力星股份"),
    ("000712", "sz", 20200731, "锦龙股份"),
    ("002421", "sz", 20260608, "达实智能"),
    ("601990", "sh", 20191218, "南京证券"),
    ("600233", "sh", 20200207, "圆通速递"),
    ("000700", "sz", 20200110, "模塑科技"),
]
ex_list = []
for code, mkt, d, name in buys:
    s, ex = fwd(code, mkt, d, 20)
    if s is None:
        print(name, "NO_DATA")
        continue
    ex_list.append(ex)
    print(f"{name}: fwd20={s*100:+.1f}%  超额={ex*100:+.1f}pp")
valid = [e for e in ex_list if e is not None]
import statistics as st
print(f"超额中位={st.median(valid)*100:+.1f}pp 均值={st.mean(valid)*100:+.1f}pp 正超额{sum(1 for e in valid if e>0)}/{len(valid)}")

print()
print("=== 同题材A/B: 模塑科技(她的选择) vs 星期六(同期货), 基准2020-01-10 ===")
for code, mkt, name in [("000700", "sz", "模塑科技"), ("002291", "sz", "星期六")]:
    s, ex = fwd(code, mkt, 20200110, 20)
    print(f"{name}: fwd20={s*100:+.1f}%")

print()
print("=== 同题材A/B: 君正集团 vs 锦龙股份(同日2020-07-31,她卖前买后) ===")
for code, mkt, name, d in [("601216", "sh", "君正集团(她持有至7/31)", 20200715),
                           ("000712", "sz", "锦龙股份(她7/31买入)", 20200731)]:
    s, ex = fwd(code, mkt, d, 15)
    print(f"{name}: fwd15={s*100:+.1f}%")
