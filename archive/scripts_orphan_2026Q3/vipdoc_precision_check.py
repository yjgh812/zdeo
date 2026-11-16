# -*- coding: utf-8 -*-
"""vipdoc 本地数据精度验证：读取通达信本地 .day/.lc1，与 pytdx 在线行情交叉比对"""
import struct
import datetime

VIPDOC = r"D:\anzhuang\tdx\vipdoc"

def read_day(code_market, n=3):
    """读日线最后n条: 返回[(date,open,high,low,close,volume股,amount元)]"""
    path = f"{VIPDOC}\\{code_market}\\lday\\{code_market}.day"
    recs = []
    with open(path, "rb") as f:
        buf = f.read()
    for i in range((len(buf)//32 - n), len(buf)//32):
        d, o, h, l, c, amt, vol, _ = struct.unpack_from("<IIIIIfII", buf, i*32)
        recs.append((d, o/100, h/100, l/100, c/100, vol, round(amt, 2)))
    return recs

def read_lc1(code_market, n=5):
    """读1分钟线最后n条: 返回[(datetime,open,high,low,close,volume股,amount元)]"""
    path = f"{VIPDOC}\\{code_market}\\minline\\{code_market}.lc1"
    recs = []
    with open(path, "rb") as f:
        buf = f.read()
    for i in range((len(buf)//32 - n), len(buf)//32):
        d, t, o, h, l, c, amt, vol, _ = struct.unpack_from("<HHfffffII", buf, i*32)
        y = (d >> 11) + 2004; rem = d & 2047; m = rem // 100; day = rem % 100
        hh = t // 60; mm = t % 60
        recs.append((f"{y:04d}-{m:02d}-{day:02d} {hh:02d}:{mm:02d}",
                     round(o,2), round(h,2), round(l,2), round(c,2), vol, round(amt,2)))
    return recs

# ---------- 本地读取 ----------
targets = [("sh600519", "贵州茅台"), ("sz000650", "仁和药业"), ("sh688981", "中芯国际")]
print("=" * 70)
print("【通达信 vipdoc 本地数据】(volume单位=股, amount单位=元)")
local = {}
for cm, name in targets:
    day = read_day(cm, 2)
    lc1 = read_lc1(cm, 3)
    local[cm] = {"day": day, "lc1": lc1}
    print(f"\n{cm} {name}")
    for r in day:
        print(f"  日线 {r[0]} O:{r[1]:.2f} H:{r[2]:.2f} L:{r[3]:.2f} C:{r[4]:.2f} Vol:{r[5]:,}股 Amt:{r[6]:,.0f}元")
    for r in lc1:
        print(f"  1分钟 {r[0]} O:{r[1]:.2f} C:{r[2+2]:.2f} Vol:{r[5]:,}股 Amt:{r[6]:,.0f}元")

# ---------- pytdx 在线交叉验证 ----------
print("\n" + "=" * 70)
print("【pytdx 在线行情服务器交叉验证】")
from pytdx.hq import TdxHq_API

servers = [("119.147.212.81", 7709), ("115.238.56.198", 7709), ("218.108.98.244", 7709)]
api = TdxHq_API()
connected = False
for host, port in servers:
    try:
        api.connect(host, port, time_out=5)
        connected = True
        print(f"已连接: {host}:{port}")
        break
    except Exception as e:
        print(f"连接失败 {host}: {e}")

if connected:
    for cm, name in targets:
        market = 0 if cm.startswith("sh") else 1
        code = cm[2:]
        bars = api.get_security_bars(9, market, code, 0, 2)  # 9=日线
        bars.sort(key=lambda x: x["datetime"])
        print(f"\n{cm} {name} 在线日线:")
        for b in bars:
            dt = b["datetime"]
            online_date = int(dt.replace("-", "")[:8])
            loc = local[cm]["day"]
            match = [x for x in loc if x[0] == online_date]
            if match:
                L = match[0]
                dC = abs(L[4] - b["close"])
                dV = abs(L[5] - b["vol"]*100)  # 在线日线vol=手
                print(f"  {dt[:10]} C在线:{b['close']:.2f} C本地:{L[4]:.2f} 价差:{dC:.2f} | Vol在线:{b['vol']:,}手 本地:{L[5]:,}股 差:{dV:,}股 ({dV/max(L[5],1)*100:.3f}%)")
            else:
                print(f"  {dt[:10]} C在线:{b['close']:.2f} (本地无同日记录)")
    api.disconnect()
else:
    print("所有服务器连接失败")
print("\n验证完成")
