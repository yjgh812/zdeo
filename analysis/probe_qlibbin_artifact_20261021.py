# -*- coding: utf-8 -*-
"""只读探针(20261021): 今日 qlib_bin 中两类伪影是否仍存在。

背景: parquet(9/28构建)的 LABEL0 出现 2026-07-09 五只高价股 -31~-36% 假收益(无对应公司行为);
     qfq库 000630 2018-06-15 +399% 断层(除权仅0.02元现金分红, 无送转)。
判定: 直接读 qlib_bin 今日状态 →
  - 若 7/9 五股假跌仍在: qlib_bin 持续带毒 → 未来 parquet 重建仍会中毒(修复必须先修 qlib_bin 链);
  - 若已消失: 9/28 构建时点的 qfq 数据后来自愈 → 但 parquet/pred/生产模型仍是脏时代产物(需重建重训)。
  - 000630 断层在 qlib_bin 的状态同理(该点已确认今日 qfq 库仍在)。
"""
import sys

import numpy as np
import pandas as pd

CODES = ["SH688008", "SZ301308", "SH688396", "SH600460", "SH688082", "SZ000630"]


def main():
    import qlib
    from qlib.constant import REG_CN
    qlib.init(provider_uri=r"D:\workbuddyspace\qlib_data\qlib_bin", region=REG_CN, kernels=2)
    from qlib.data import D

    df = D.features(CODES, ["$close"], start_time="2026-06-25", end_time="2026-07-20")
    print("== 类2: 2026-07-09 前后 qlib_bin 收盘(今日状态) ==")
    piv = df["$close"].unstack(level="instrument")
    print(piv.round(2).to_string())
    print("\n7/8→7/9 单日变动:")
    r = piv.loc["2026-07-09"] / piv.loc["2026-07-08"] - 1
    for k, v in r.items():
        print(f"  {k}: {v:+.2%}  {'⚠️越限伪影' if abs(v) > 0.25 else 'OK'}")

    df2 = D.features(["SZ000630"], ["$close"], start_time="2018-06-01", end_time="2018-07-10")
    piv2 = df2["$close"].unstack(level="instrument")
    print("\n== 类1: 000630 @2018-06 断层(今日 qlib_bin) ==")
    print(piv2.round(3).to_string())
    r2 = piv2.loc["2018-06-15"] / piv2.loc["2018-06-14"] - 1
    print(f"6/14→6/15: {float(r2.iloc[0]):+.2%}  {'⚠️断层仍在' if abs(float(r2.iloc[0])) > 0.25 else '已消除'}")


if __name__ == "__main__":
    sys.exit(main())
