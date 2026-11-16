# -*- coding: utf-8 -*-
"""extreme_window_gateoff_20261019.py — 呈请③c: 极端窗口关闸 vs 开闸重放（2026-10-19「继续」令）

问题(外部材料第二轮缺口2): 全史回测的最大回撤可能因风控闸门在极端窗口"幸运"起效/失效而误读;
需在 2016-01熔断 / 2018熊市 / 2020-02疫情 / 2024-01小盘危机 四个极端窗口对比
  开闸组 = dose_n15 (生产口径: 逐票止损0.08+组合熔断10%+无持有帽)
  关闸组 = ladder L0b (止损禁用1.0+熔断禁用100+固定20根持有 — 10/19判别树产物, 零新回测复用)
口径声明: L0b 与生产的差异=止损/熔断关闭+持有帽20根 两项合并(判别树原设计), 非单旋钮;
  2015-06 不在回测窗(引擎主口径2016起), 以2016-01熔断窗代之, 如实声明。
只读研究件·零交易权·零生产改动·零新回测。
"""
import json
import sqlite3
import sys

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

ROOTS = {
    "开闸(生产口径)": r"D:\zcodesj\reports\backtest_full_20261018_dose_n15",
    "关闸(L0b:无止损无熔断+20根帽)": r"D:\zcodesj\reports\backtest_full_20261019_ladder_L0b",
}
WINDOWS = [
    ("2016-01熔断", 20160101, 20160129),
    ("2018熊市全年", 20180101, 20181231),
    ("2020-02疫情", 20200101, 20200331),
    ("2024-01小盘危机", 20240101, 20240229),
]
OUT_JSON = r"D:\zcodesj\analysis\extreme_window_gateoff_20261019_out.json"
OUT_MD = r"D:\zcodesj\reports\extreme_window_gateoff_20261019.md"


def load_equity(root, fname):
    df = pd.read_csv(rf"{root}\{fname}", encoding="utf-8-sig")
    df.columns = [c.strip().lower() for c in df.columns]
    df["d"] = pd.to_numeric(df["date"].str.replace("-", ""), errors="coerce").astype("int64")
    df["v"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["d", "v"]).sort_values("d")


def window_stats(df, d0, d1):
    s = df[(df["d"] >= d0) & (df["d"] <= d1)]
    if len(s) < 2:
        return None
    v = s["v"].to_numpy()
    peak = v[0]
    mdd = 0.0
    for x in v:
        peak = max(peak, x)
        mdd = max(mdd, (peak - x) / peak)
    return {"ret_pct": round(float(v[-1] / v[0] - 1) * 100, 2),
            "mdd_in_win_pct": round(mdd * 100, 2), "days": int(len(v))}


def main():
    out = {}
    lines = ["# 极端窗口关闸 vs 开闸重放（呈请③c · 2026-10-19）\n",
             "> 只读研究件·零新回测（复用 10/18 剂量 fork 与 10/19 判别树产物净值序列）。开闸=dose_n15 生产口径（止损0.08+熔断10%+无持有帽）；关闸=L0b（止损禁用+熔断禁用+固定20根持有，差异两项合并如实声明）。2015-06 不在引擎主口径窗（2016 起），以 2016-01 熔断窗代之。\n"]
    for arm, fname in [("bp_ep", "bp_ep_core_equity.csv")]:  # 梯子回放仅bp臂(ladder旋钮为bp专用), ml无关闸态产物, 如实声明
        out[arm] = {}
        lines.append(f"\n## {arm}\n")
        lines.append("| 窗口 | 组 | 窗内收益% | 窗内MDD% | 交易日 | 开闸−关闸收益差(pp) |")
        lines.append("|---|---|---|---|---|---|")
        eqs = {k: load_equity(root, fname) for k, root in ROOTS.items()}
        for wname, d0, d1 in WINDOWS:
            stats = {k: window_stats(eqs[k], d0, d1) for k in ROOTS}
            diff = None
            if stats["开闸(生产口径)"] and stats["关闸(L0b:无止损无熔断+20根帽)"]:
                diff = round(stats["开闸(生产口径)"]["ret_pct"] - stats["关闸(L0b:无止损无熔断+20根帽)"]["ret_pct"], 2)
            out[arm][wname] = stats | {"diff_open_minus_off_pp": diff}
            for k in ROOTS:
                st = stats[k]
                if st:
                    lines.append(f"| {wname} | {k.split('(')[0]} | {st['ret_pct']} | {st['mdd_in_win_pct']} | {st['days']} | {diff if k==list(ROOTS)[1] else ''} |")
                else:
                    lines.append(f"| {wname} | {k.split('(')[0]} | 数据不足 | | | |")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    lines.append("\n- 判读口径: 正收益差=闸门在极端窗贡献正收益(保护或止损再入场效应)；负=关闸更优。结合 MDD 对比读「闸门是否极端窗承重」。")
    lines.append("- 边界: L0b 含 20 根持有帽（与生产无帽不同），收益差含持有期效应成分；单旋钮拆解须另设 fork（本件不跑，声明边界）。")
    lines.append("\n*脚本直出零转抄: analysis/extreme_window_gateoff_20261019.py。*")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
