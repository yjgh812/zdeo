# -*- coding: utf-8 -*-
"""给 RD-Agent 研究线数据副本补 SH000300 基准指数 bin(从通达信 vipdoc 解析)。

背景: qlib 组合回测需要基准指数日线, 屠龙 qlib_bin 是纯股票宇宙无指数。
数据: D:\\anzhuang\\tdx\\vipdoc\\sh\\lday\\sh000300.day (通达信 .day 32字节/记录:
      date,u32 open,u32 high,u32 low,u32 close(价*100),f32 amount(元),u32 vol(股),u32 reserved)
写入: <cn_data>/features/sh000300/{open,high,low,close,volume,factor}.day.bin
      qlib bin 格式 = float32[起始日历下标, 逐日值...]; factor=1.0。
不写入 instruments/all.txt(避免污染股票宇宙; qlib 基准按显式代码取数)。

用法: python build_index_bin.py <cn_data_dir> <vipdoc_day_file>
退出码: 0=成功, 2=异常。
"""
import struct
import sys
from pathlib import Path


def parse_tdx_day(path: Path) -> list[tuple[str, float, float, float, float, float, float]]:
    raw = path.read_bytes()
    n = len(raw) // 32
    out = []
    for i in range(n):
        d, o, h, l, c, amount, vol, _res = struct.unpack_from("<IIIIIfII", raw, i * 32)
        date = f"{d:08d}"
        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        out.append((date, o / 100.0, h / 100.0, l / 100.0, c / 100.0, amount, float(vol)))
    return out


def make_bin(close_bin_sibling_len_bytes: int, start_idx: int) -> bytes:
    n_days = (close_bin_sibling_len_bytes - 4) // 4
    return struct.pack("<f", float(start_idx)) + b"\x00\x00\x80\x3f" * n_days


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python build_index_bin.py <cn_data_dir> <vipdoc_sh000300_day>")
        return 2
    cn = Path(sys.argv[1])
    src = Path(sys.argv[2])
    cal = [x.strip() for x in (cn / "calendars" / "day.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
    cal_idx = {d: i for i, d in enumerate(cal)}

    rows = parse_tdx_day(src)
    aligned = [(cal_idx[d], o, h, l, c, vol) for d, o, h, l, c, amount, vol in rows if d in cal_idx]
    if not aligned:
        print("FAIL: no overlapping dates between vipdoc and calendar")
        return 2
    aligned.sort()
    start = aligned[0][0]
    n = len(aligned)
    fields = {
        "open": [r[1] for r in aligned],
        "high": [r[2] for r in aligned],
        "low": [r[3] for r in aligned],
        "close": [r[4] for r in aligned],
        "volume": [r[5] for r in aligned],
    }
    out_dir = cn / "features" / "sh000300"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload_head = struct.pack("<f", float(start))
    for name, vals in fields.items():
        (out_dir / f"{name}.day.bin").write_bytes(payload_head + struct.pack(f"<{n}f", *vals))
    (out_dir / "factor.day.bin").write_bytes(make_factor(n, start))
    print(f"OK: sh000300 bins written, days={n}, cal_span={cal[start]}..{cal[-1]}, "
          f"last_close={fields['close'][-1]:.2f}")
    return 0


def make_factor(n: int, start: int) -> bytes:
    return struct.pack("<f", float(start)) + b"\x00\x00\x80\x3f" * n


if __name__ == "__main__":
    sys.exit(main())
