# -*- coding: utf-8 -*-
"""大导弹三笔已证买入 × 屠龙区域引擎对照验证:
他的"底部首板"入场, 在我们 G/H/B 区框架里落在哪? pos60 是否低位? 后验如何?
另: 对每笔做 fwd 路径(模拟 T20/S5 退出)看他赚的是哪段。
"""
import os
import struct

VIPDOC = r"D:\anzhuang\tdx\vipdoc"


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


TRADES = [
    ("002037", "保利联合", "sz", 20220902, 15.86),
    ("601068", "中铝国际", "sh", 20221103, 4.22),
    ("600095", "湘财股份", "sh", 20221114, 7.64),
]

print("=== 大导弹三笔入场 × 屠龙区域引擎对照 ===\n")
for code, name, mkt, d, buy_px in TRADES:
    rows = read_day(code, mkt)
    idx = next((i for i, r in enumerate(rows) if r[0] == d), None)
    if idx is None or idx < 60:
        print(f"{name} {d}: 数据不足")
        continue
    win = rows[idx-59:idx+1]
    hi = max(r[2] for r in win)
    lo = min(r[3] for r in win)
    c = rows[idx][4]
    pos60 = (c - lo) / (hi - lo) * 100 if hi > lo else 50
    # 前一日涨停? (首板确认)
    prev_chg = (c / rows[idx-1][4] - 1) * 100
    # 均线
    ma20 = sum(r[4] for r in rows[idx-19:idx+1]) / 20
    ma60 = sum(r[4] for r in rows[idx-59:idx+1]) / 60
    bias20 = (c / ma20 - 1) * 100
    # fwd
    f5 = rows[idx+5][4] / buy_px - 1 if idx + 5 < len(rows) else None
    f10 = rows[idx+10][4] / buy_px - 1 if idx + 10 < len(rows) else None
    f20 = rows[idx+20][4] / buy_px - 1 if idx + 20 < len(rows) else None
    # 区间最高(f20内)
    hi20 = max(r[2] for r in rows[idx:idx+20]) if idx + 20 <= len(rows) else None
    hi20_ret = (hi20 / buy_px - 1) * 100 if hi20 else None
    zone = "G区(低位)" if pos60 <= 40 else ("中部" if pos60 <= 70 else ("H追高区" if pos60 >= 80 else "中高位"))
    print(f"{name}({code}) 买入日{d} 买价{buy_px}")
    print(f"  收盘{c:.2f} 当日涨幅{prev_chg:+.1f}% pos60={pos60:.0f}% bias20={bias20:+.1f}%")
    print(f"  屠龙区域判定: {zone} | 均线: 收盘{'>' if c > ma20 else '<'}MA20, 收盘{'>' if c > ma60 else '<'}MA60")
    print(f"  买价后验: f5={(f5 or 0)*100:+.1f}% f10={(f10 or 0)*100:+.1f}% f20={(f20 or 0)*100:+.1f}% 20日内最高{hi20_ret:+.1f}%")
    # S-5 路径: 从买价起, 是否触发-8%
    stop_hit = None
    for k in range(1, 21):
        if idx + k >= len(rows):
            break
        if rows[idx+k][4] <= buy_px * 0.92:
            stop_hit = (k, rows[idx+k][0], (rows[idx+k][4]/buy_px-1)*100)
            break
    if stop_hit:
        print(f"  S-5(收盘-8%): 第{stop_hit[0]}日触 发({stop_hit[2]:+.1f}%)")
    else:
        print(f"  S-5(收盘-8%): 20日内未触发")
    print()

# 汇总: 他的入场是否符合G区
print("=== 汇总 ===")
g_count = 0
for code, name, mkt, d, buy_px in TRADES:
    rows = read_day(code, mkt)
    idx = next((i for i, r in enumerate(rows) if r[0] == d), None)
    if idx is None or idx < 60:
        continue
    win = rows[idx-59:idx+1]
    hi = max(r[2] for r in win)
    lo = min(r[3] for r in win)
    c = rows[idx][4]
    pos60 = (c - lo) / (hi - lo) * 100 if hi > lo else 50
    in_g = pos60 <= 40
    g_count += in_g
    print(f"{name}: pos60={pos60:.0f}% → {'✅G区' if in_g else '❌非G区'}")
print(f"G区符合率: {g_count}/3")
