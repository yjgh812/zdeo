# -*- coding: utf-8 -*-
"""log_rotate.py — 晚间链日志轮转(2026-11-07 建)
用法: python log_rotate.py <logfile> [max_mb=10] [keep_mb=2]
超限则截尾保留最后 keep_mb, 防每日追加型日志无限增长。ASCII-only 兼容。
"""
import os
import sys


def main():
    if len(sys.argv) < 2:
        print("usage: log_rotate.py <logfile> [max_mb] [keep_mb]")
        return 1
    p = sys.argv[1]
    max_mb = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    keep_mb = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    if not os.path.exists(p):
        print("no log:", p)
        return 0
    sz = os.path.getsize(p)
    if sz <= max_mb * 1024 * 1024:
        print("log ok: %.1fMB" % (sz / 1048576.0))
        return 0
    with open(p, "rb") as f:
        f.seek(-int(keep_mb * 1024 * 1024), 2)
        tail = f.read()
    tmp = p + ".rotating"
    with open(tmp, "wb") as f:
        f.write(b"[log rotated %s, kept tail]\n" % str(sz).encode())
        f.write(tail)
    os.replace(tmp, p)
    print("rotated: %.1fMB -> %.1fMB tail" % (sz / 1048576.0, len(tail) / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
