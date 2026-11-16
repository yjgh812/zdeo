# -*- coding: utf-8 -*-
"""关键工件日期快照器(证伪复核F2/F9加固件, 2026-10-20)。手动运行, 不挂调度。

背景: chan_signals_v2.pkl 被夜链滚动重建冲掉9/9原件(F2)、本地qfq副本删除致教材条目快照失考(F9)。
用法: python analysis/cache_snapshot_20261020.py [--into DIR]
产出: data/cache_snapshots/<YYYYMMDD>/<basename>  (存在即跳过, 幂等)
"""
import argparse
import datetime
import os
import shutil

TARGETS = [
    r"D:\zcodesj\portrait_agent\cache\chan_signals_v2.pkl",
    r"D:\zcodesj\attribution\stock_info_cache.json",
    r"D:\zcodesj\data\falsified_rules.json",
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--into", default=r"D:\zcodesj\data\cache_snapshots")
    args = ap.parse_args()
    day = datetime.date.today().strftime("%Y%m%d")
    dst_dir = os.path.join(args.into, day)
    os.makedirs(dst_dir, exist_ok=True)
    for src in TARGETS:
        if not os.path.exists(src):
            print("MISS", src)
            continue
        dst = os.path.join(dst_dir, os.path.basename(src))
        if os.path.exists(dst):
            print("SKIP(exists)", dst)
            continue
        shutil.copy2(src, dst)
        print("SNAP", src, "->", dst)

if __name__ == "__main__":
    main()
