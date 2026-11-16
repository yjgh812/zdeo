# 直接从 qlib bin 造 daily_pv h5（绕开 qlib D.features 的多进程调度，43 分钟不完的实测替代）
# schema 对齐 qlib D.features: MultiIndex(datetime, instrument) + [$open,$close,$high,$low,$volume,$factor]
# 域: 副本 csi300.txt 静态快照前 300 行 | bin 格式: float32 数组, [0]=start_index
# 注意1: 生产 qlib_bin 无 factor.day.bin (qfq 已复权), 缺失时 factor 置 1.0
# 注意2: 产物仅作 rdagent 因子实现源数据样例 (LLM 调试样本), 回测本体走 conf.yaml 的池
# 原始位置: D:\zcodesj\.rdagent_ws\make_h5_direct.py (B7, 2026-09-07)
import struct
from pathlib import Path

import numpy as np
import pandas as pd

BIN = Path(r"D:\zcodesj\.rdagent_ws\qlib_data\qlib_bin")


def read_bin(p: Path, n_days: int, offset: int):
    """读 qlib bin: [0]=start_index(相对 calendar 偏移), 之后是各日值。返回对齐全日历的 series。"""
    raw = np.fromfile(p, dtype=np.float32)
    start = int(raw[0])
    vals = raw[1:]
    s = np.full(n_days, np.nan, dtype=np.float64)
    # start 相对全日历; 我们只取 [offset:] 段
    lo = max(start, offset)
    hi = min(start + len(vals), offset + n_days)
    if hi > lo:
        s[lo - offset : hi - offset] = vals[lo - start : hi - start]
    return s


# 1) 日历 (qlib dump 标准格式 YYYY-MM-DD)
cal = [x.strip() for x in (BIN / "calendars" / "day.txt").read_text().splitlines() if x.strip()]
cal_dt = pd.to_datetime(cal)
offset = 0
n_days = len(cal)

# 2) csi300 成分(静态快照)
inst_lines = (BIN / "instruments" / "csi300.txt").read_text().splitlines()
codes = []
for ln in inst_lines:
    parts = ln.split("\t")
    if parts and parts[0].strip():
        codes.append(parts[0].strip().upper())
codes = sorted(set(codes))[:300]
print("codes:", len(codes), codes[:3])

# 3) 逐股读 bin 拼长表
frames = []
for code in codes:
    d = BIN / "features" / code
    if not d.exists():
        continue
    cols = {}
    ok = True
    for f in ["open", "close", "high", "low", "volume", "factor"]:
        p = d / f"{f}.day.bin"
        if not p.exists():
            if f == "factor":
                # 生产 qlib_bin 由 qfq 库生成(已复权), 无 factor 文件, 语义恒 1
                cols["$factor"] = np.ones(n_days, dtype=np.float64)
                continue
            ok = False
            break
        cols["$" + f] = read_bin(p, n_days, offset)
    if not ok:
        continue
    df = pd.DataFrame(cols, index=cal_dt)
    df["instrument"] = code
    frames.append(df.reset_index().rename(columns={"index": "datetime"}))
    if len(frames) >= 300:
        break

big = pd.concat(frames, ignore_index=True)
big = big.set_index(["datetime", "instrument"]).sort_index()
big = big[["$open", "$close", "$high", "$low", "$volume", "$factor"]]
big = big.dropna(how="all")
print("all h5 shape:", big.shape)

out_dir = Path(__file__).parent
big.to_hdf(out_dir / "daily_pv_all.h5", key="data")

# debug: 2018-2019 × 前 100 股
dbg = big.loc[(big.index.get_level_values("datetime") >= "2018-01-01") & (big.index.get_level_values("datetime") <= "2019-12-31")]
c100 = dbg.index.get_level_values("instrument").unique()[:100]
dbg = dbg[dbg.index.get_level_values("instrument").isin(c100)]
dbg.to_hdf(out_dir / "daily_pv_debug.h5", key="data")
print("debug h5 shape:", dbg.shape)
print("DIRECT H5 DONE")
