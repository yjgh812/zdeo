# -*- coding: utf-8 -*-
"""给 RD-Agent 研究线的 qlib 数据副本补 $factor 字段(factor=1.0)。

背景: rdagent 的因子沙盒数据生成器(factor_data_template/generate.py)引用 $factor,
而生产 qlib_bin(qfq 基准)没有该 bin。副本价格本身已是 qfq(前复权)口径,
factor=1.0 常数在语义上自洽(不再二次复权)。仅操作 D:\\zcodesj 研究线副本, 不碰生产 qlib_bin。

qlib bin 格式: float32 数组, 首元素=该 Instrument 在日历中的起始下标, 其后为逐日数值。

用法: python gen_factor_bins.py <cn_data_dir>
退出码: 0=全部补齐, 2=异常。
"""
import struct
import sys
from pathlib import Path


def make_factor_bin(close_bin: Path, factor_bin: Path) -> None:
    raw = close_bin.read_bytes()
    start_idx = struct.unpack("<f", raw[:4])[0]
    n_days = (len(raw) - 4) // 4
    payload = struct.pack("<f", start_idx) + b"\x00\x00\x80\x3f" * n_days  # float32 1.0
    factor_bin.write_bytes(payload)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python gen_factor_bins.py <cn_data_dir>")
        return 2
    feat_root = Path(sys.argv[1]) / "features"
    if not feat_root.is_dir():
        print(f"FAIL: {feat_root} not found")
        return 2
    made = skipped = 0
    for inst_dir in sorted(feat_root.iterdir()):
        close_bin = inst_dir / "close.day.bin"
        factor_bin = inst_dir / "factor.day.bin"
        if not close_bin.exists():
            continue
        if factor_bin.exists() and factor_bin.stat().st_size == close_bin.stat().st_size:
            skipped += 1
            continue
        make_factor_bin(close_bin, factor_bin)
        made += 1
    print(f"OK: factor bins made={made} skipped={skipped} (root={feat_root})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
