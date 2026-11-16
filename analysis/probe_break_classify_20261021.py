# -*- coding: utf-8 -*-
"""只读分类(20261021): 814 断点全量 raw 交叉判别(修复工程阶段1, 零写入)。

判别规则(每断点取 tdx 原始未复权价):
  A 因子伪影      : |原始隔夜变动| <= 板限+1pp(原始价平滑, qfq 跳变纯系因子错)
  B 真实无限制日   : 原始跳超板限 且 当日量能 >= 5× 前20日均量(恢复上市/新股复牌等, 保留不修)
  C 除权漏调      : 原始跳超板限 但 量能正常(除权事件因子漏调, 修因子)
  NO_RAW          : 北交所等 vipdoc 无原始数据(需另源处理)
输入: analysis/qfq_break_census_v2_20261021_list.csv (814点)
输出: analysis/qfq_break_classified_20261021.csv + 统计
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from build_qfq_meta_tdx_20260811 import read_tdx_day, TDX_DIR  # noqa: E402

IN_CSV = r"D:\zcodesj\analysis\qfq_break_census_v2_20261021_list.csv"
OUT_CSV = r"D:\zcodesj\analysis\qfq_break_classified_20261021.csv"


def main():
    hd = pd.read_csv(IN_CSV, dtype={"code": str, "date": str})
    print(f"[in] 断点 {len(hd)} 个 / {hd['code'].nunique()} 只")
    raw_cache = {}
    out_rows = []
    for code, grp in hd.groupby("code"):
        if code not in raw_cache:
            raw = read_tdx_day(code)
            if raw is not None and len(raw):
                raw = raw.sort_values("date").reset_index(drop=True)
                raw["vol20"] = raw["volume"].rolling(20, min_periods=5).mean().shift(1)
            raw_cache[code] = raw
        raw = raw_cache[code]
        for r in grp.itertuples():
            if raw is None or not len(raw):
                out_rows.append((r.code, r.date, r.chg, r.board, "NO_RAW", np.nan, np.nan, np.nan))
                continue
            arr = raw[raw.index <= int(r.date)]
            if len(arr) < 2:
                out_rows.append((r.code, r.date, r.chg, r.board, "NO_RAW", np.nan, np.nan, np.nan))
                continue
            c_now = float(arr.iloc[-1]["close"])
            c_prev = float(arr.iloc[-2]["close"])
            raw_chg = c_now / c_prev - 1
            limit = r.threshold - 0.04  # 板限+1pp
            if abs(raw_chg) <= limit:
                cls = "A_因子伪影"
                vol_ratio = float(arr.iloc[-1]["volume"] / arr.iloc[-1]["vol20"]) if arr.iloc[-1]["vol20"] else np.nan
            else:
                v = arr.iloc[-1]["volume"]
                v20 = arr.iloc[-1]["vol20"]
                vol_ratio = float(v / v20) if v20 else np.inf
                cls = "B_真实无限制日" if vol_ratio >= 5 else "C_除权漏调"
            out_rows.append((r.code, r.date, r.chg, r.board, cls, round(raw_chg, 4), round(vol_ratio, 2) if vol_ratio == vol_ratio else np.nan, c_now))
    o = pd.DataFrame(out_rows, columns=["code", "date", "qfq_chg", "board", "class", "raw_chg", "vol_ratio", "raw_close"])
    o.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"[out] {OUT_CSV}\n")
    vc = o["class"].value_counts()
    print("分类结果:")
    for k, v in vc.items():
        print(f"  {k}: {v} ({v/len(o):.1%})")
    print("\n按板×类:")
    print(pd.crosstab(o["board"], o["class"]).to_string())
    print("\nA类Top10(按|qfq_chg|):")
    a = o[o["class"] == "A_因子伪影"].reindex(o[o["class"] == "A_因子伪影"]["qfq_chg"].abs().sort_values(ascending=False).index)
    for r in a.head(10).itertuples():
        print(f"  {r.code} @ {r.date}  qfq={r.qfq_chg:+.1%} raw={r.raw_chg:+.1%}")


if __name__ == "__main__":
    sys.exit(main())
