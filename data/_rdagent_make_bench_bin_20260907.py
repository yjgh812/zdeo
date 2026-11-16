# 为 rdagent 副本 qlib_bin 造 SH000300 基准指数 bin (来源=tdx vipdoc sh000300.day)
# 格式对齐生产 bin: float32 数组, [0]=start_index(相对 calendar 的起始偏移)。
# 副本=rdagent 工作区数据集(生产 qlib_bin 生成物未触碰)。
# 原始位置: D:\zcodesj\.rdagent_ws\make_bench_bin.py (B7, 2026-09-07)
import struct

import numpy as np

TDX = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000300.day"
CAL = r"D:\zcodesj\.rdagent_ws\qlib_data\qlib_bin\calendars\day.txt"
OUT = r"D:\zcodesj\.rdagent_ws\qlib_data\qlib_bin\features\sh000300"

# 1) tdx 指数日线: 每记录 32B = date,open,high,low,close(int,元*100),amount(float),vol(uint),保留
recs = []
with open(TDX, "rb") as f:
    data = f.read()
for i in range(len(data) // 32):
    d, o, h, l, c, amt, vol, _ = struct.unpack_from("<IIIIIfII", data, i * 32)
    recs.append((d, o / 100.0, h / 100.0, l / 100.0, c / 100.0, float(amt), float(vol)))
tdx = {str(d): (o, h, l, c, amt, vol) for d, o, h, l, c, amt, vol in recs}

# 2) 对齐 qlib calendar (副本, 2016-01-04 起)
with open(CAL, encoding="utf-8") as f:
    cal = [line.strip().replace("-", "") for line in f if line.strip()]
print("calendar:", cal[0], "->", cal[-1], "days:", len(cal))

n = len(cal)
fields = {k: np.full(n, np.nan, dtype=np.float32) for k in ["open", "high", "low", "close", "amount", "volume"]}
hit = 0
for i, d in enumerate(cal):
    if d in tdx:
        o, h, l, c, amt, vol = tdx[d]
        fields["open"][i] = o
        fields["high"][i] = h
        fields["low"][i] = l
        fields["close"][i] = c
        fields["amount"][i] = amt
        fields["volume"][i] = vol
        hit += 1
print(f"aligned: {hit}/{n} ({hit/n*100:.1f}%)")

# 3) 写 bin: [start_index=0] + values
import os
os.makedirs(OUT, exist_ok=True)
for name, arr in fields.items():
    valid = np.where(~np.isnan(arr))[0]
    if len(valid) == 0:
        continue
    start = int(valid[0])
    vals = arr[start:].astype(np.float32)
    (np.concatenate([[np.float32(start)], vals])).tofile(f"{OUT}\\{name}.day.bin")
# factor 全 1 (指数无复权)
fac = np.ones(n, dtype=np.float32)
np.concatenate([[np.float32(0)], fac]).tofile(f"{OUT}\\factor.day.bin")
print("written:", sorted(os.listdir(OUT)))

# 4) instruments 追加 (副本)
for inst_name in ["all.txt", "csi300.txt"]:
    p = rf"D:\zcodesj\.rdagent_ws\qlib_data\qlib_bin\instruments\{inst_name}"
    with open(p, encoding="utf-8") as f:
        lines = [x.rstrip("\n") for x in f]
    if not any(x.startswith("SH000300") for x in lines):
        first_d = f"{cal[0][:4]}-{cal[0][4:6]}-{cal[0][6:]}"
        last_d = f"{cal[-1][:4]}-{cal[-1][4:6]}-{cal[-1][6:]}"
        lines.append(f"SH000300\t{first_d}\t{last_d}")
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"{inst_name}: SH000300 appended ({len(lines)} rows)")
    else:
        print(f"{inst_name}: already present")
print("=== BENCH BIN DONE ===")
