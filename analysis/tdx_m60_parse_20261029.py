# -*- coding: utf-8 -*-
"""tdx_m60_parse_20261029.py — TDX vipdoc 5分K→60分K 聚合全量解析

TDX .lc5 二进制格式:
  Header: 12 bytes (0x1C = 28? 需实测)
  Record: 32 bytes each
    [0:4]   date (unsigned int, TDX custom encoding)
    [4:8]   time (unsigned int)
    [8:12]  open (float, price*100? 需实测)
    [12:16] high (float)
    [16:20] low  (float)
    [20:24] close(float)
    [24:28] amount (float, 元)
    [28:32] volume (int, 手)

输出: data/m60_tdx_full/ 每票一个JSON(60分K)
"""
import os, sys, json, struct, time
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
VIPDOC = r"D:\anzhuang\tdx\vipdoc"
OUT = os.path.join(ROOT, "data", "m60_tdx_full")
os.makedirs(OUT, exist_ok=True)

def parse_lc5(filepath):
    """解析TDX .lc5 5分钟K线, 返回bars列表"""
    size = os.path.getsize(filepath)
    if size < 44:
        return []
    # TDX .lc5 格式: 无header, 每记录32字节直接开始
    # [0:4] date as u32: 0xXXXXXXXX packed date
    # 实测格式: bytes 0-1 = year offset, bytes 2-3 = month/day
    n = size // 32
    bars_raw = []
    with open(filepath, "rb") as f:
        for i in range(n):
            buf = f.read(32)
            if len(buf) < 32:
                break
            # TDX日期编码: 前2字节=年份-2004偏移相关, 后2字节=月日
            raw_date = struct.unpack("<I", buf[0:4])[0]
            # 尝试标准TDX .day格式解码
            year = (raw_date >> 16) & 0xFFFF
            month = (raw_date >> 8) & 0xFF
            day = raw_date & 0xFF
            if year < 2000 or year > 2030:
                # 尝试另一种编码
                year = 2004 + (raw_date >> 20)
                month = (raw_date >> 12) & 0xFF
                day = (raw_date >> 4) & 0xFF
            o = struct.unpack("<f", buf[4:8])[0]
            h = struct.unpack("<f", buf[8:12])[0]
            l = struct.unpack("<f", buf[12:16])[0]
            c = struct.unpack("<f", buf[16:20])[0]
            vol = struct.unpack("<I", buf[20:24])[0]
            amt = struct.unpack("<f", buf[24:28])[0]
            if c <= 0 or o <= 0:
                continue
            bars_raw.append((year, month, day, o, h, l, c, vol, amt))
    return bars_raw


# 先解析一只票看看格式对不对
test_file = os.path.join(VIPDOC, "sh", "fzline", "sh600519.lc5")
raw = parse_lc5(test_file)
print(f"测试: sh600519 {len(raw)}条5分K")
if raw:
    print(f"  首条: year={raw[0][0]} month={raw[0][1]} day={raw[0][2]} close={raw[0][6]}")
    print(f"  末条: year={raw[-1][0]} month={raw[-1][1]} day={raw[-1][2]} close={raw[-1][6]}")
    # 如果日期解码不对, 试读原始bytes
    with open(test_file, "rb") as f:
        f.seek(0)
        header = f.read(32)
        print(f"  头32字节(hex): {header.hex()}")
        f.seek(32)
        rec1 = f.read(32)
        print(f"  第一记录(hex): {rec1.hex()}")
        # 试直接解4字节date
        d_raw = struct.unpack("<I", rec1[0:4])[0]
        print(f"  date_raw: {d_raw} (0x{d_raw:08X})")
        # 试: TDX 5分K的date可能是打包的 (year-2004)*2048 + month*100 + day? 
        # 或按.day文件格式: 每个byte有含义
        b = rec1[0:4]
        print(f"  bytes: {b[0]:02X} {b[1]:02X} {b[2]:02X} {b[3]:02X}")
        # .lc5的日期格式: u32 packed date = (year-2004)*2048 + month*100 + day (不完全确定)
        # 试直接按字节解析
        b0, b1, b2, b3 = b[0], b[1], b[2], b[3]
        print(f"  b0={b0} b1={b1} b2={b2} b3={b3}")
        # 如果date按.lc5格式: u16 date + u16 time
        d_u16 = struct.unpack("<H", rec1[0:2])[0]
        t_u16 = struct.unpack("<H", rec1[2:4])[0]
        print(f"  u16_date={d_u16} u16_time={t_u16}")
        # u16_date: bit0-5=day, bit6-10=month, bit11-15=year-2020?
        yy = (d_u16 >> 11) & 0x1F
        mm = (d_u16 >> 6) & 0x1F
        dd = d_u16 & 0x3F
        hh = (t_u16 >> 8) & 0xFF
        mi = t_u16 & 0xFF
        print(f"  yy+2020={yy+2020} mm={mm} dd={dd} hh={hh} mi={mi}")
else:
    print("无数据")
