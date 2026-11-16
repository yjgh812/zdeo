# -*- coding: utf-8 -*-
"""诊断③ 分 regime 胜率×盈亏比矩阵 + 2026 负年归因 (外部方案审计B第二档·序5-2, 主人 b 令)

输入=既有回测逐笔(Top15 新基线 + Top20 历史档), 输出:
  a) 分年表: n/双轨胜率/均盈/均亏/盈亏比/盈亏和/止损占比
  b) L2情绪倍数分带(理由字段内嵌, 免费 regime 维度)
  c) 2026 负年归因: Top15 vs Top20, 胜率因子 vs 盈亏比因子分解(近似分解)
双门四格历史切片: 缺 2016-2026 逐日门状态缓存(gate 影子仅 2026-10 起 3 行),
结构化 N/A, 不伪造。
"""
import json
import re

import pandas as pd

DATASETS = {
    "bp_ep_top15_20261018": r"D:\zcodesj\reports\backtest_full_20261018\bp_ep_core_trades_full.csv",
    "bp_ep_top20_20261016": r"D:\zcodesj\reports\backtest_full_20261016_eod\bp_ep_core_trades_full.csv",
    "ml_top20_20261018": r"D:\zcodesj\reports\backtest_full_20261018\ml_signal_trades_full.csv",
}
OUT_JSON = r"D:\zcodesj\analysis\regime_payoff_matrix_20261018_out.json"
OUT_MD = r"D:\zcodesj\analysis\regime_payoff_matrix_20261018_out.md"
SENT_RE = re.compile(r"L2情绪([\d.]+)x")


def yearly(df: pd.DataFrame):
    df = df.copy()
    df["year"] = df["买入日期"].astype(str).str[:4].astype(int)
    ret = df["盈亏比例%"].astype(float)
    pnl = df["盈亏金额"].astype(float)
    df["_win_r"] = ret > 0
    df["_win_a"] = pnl > 0
    rows = []
    for y, g in df.groupby("year"):
        w = g[g["_win_a"]]
        l = g[~g["_win_a"]]
        aw = float(w["盈亏金额"].mean()) if len(w) else 0.0
        al = float(l["盈亏金额"].mean()) if len(l) else 0.0
        exits = g["卖出触发理由"].astype(str).value_counts().to_dict()
        rows.append(dict(
            year=int(y), n=int(len(g)),
            win_r=round(float(g["_win_r"].mean()) * 100, 2),
            win_a=round(float(g["_win_a"].mean()) * 100, 2),
            avg_win=round(aw, 0), avg_loss=round(al, 0),
            pl_ratio=round(abs(aw / al), 3) if al else None,
            pnl_sum=round(float(g["盈亏金额"].sum()), 0),
            stop_share=round(exits.get("stop_loss", 0) / len(g) * 100, 1),
            reb_share=round(exits.get("rebalance", 0) / len(g) * 100, 1),
            risk_ctrl=exits.get("risk_control", 0)))
    return rows


def sent_bands(df: pd.DataFrame):
    mults = df["买入决策理由"].astype(str).str.extract(SENT_RE)[0].astype(float)
    vals = mults.dropna()
    if len(vals) == 0 or vals.nunique() <= 1:
        v = float(vals.iloc[0]) if len(vals) else None
        return {"verdict": "结构性无判别力(全部同一倍数, 回测内 L2情绪=常量)",
                "constant": v, "n": int(len(df))}
    df = df.copy()
    df["_m"] = mults
    banded = df.dropna(subset=["_m"])
    out = {"n_no_tag": int(df["_m"].isna().sum())}
    for name, m in (("m<1.0", banded["_m"] < 1.0), ("m=1.0", banded["_m"] == 1.0),
                    ("m>1.0", banded["_m"] > 1.0)):
        g = banded[m]
        if len(g):
            out[name] = dict(n=int(len(g)),
                             win_r=round(float((g["盈亏比例%"] > 0).mean()) * 100, 2),
                             med_ret=round(float(g["盈亏比例%"].median()), 3),
                             pnl_sum=round(float(g["盈亏金额"].sum()), 0))
        else:
            out[name] = dict(n=0)
    return out


def attr2026(t15: pd.DataFrame, t20: pd.DataFrame):
    """2026 段(按买入年) Top15 vs Top20: 总盈亏差的胜率/均盈/均亏/笔数四因子近似分解。"""
    def seg(df):
        g = df[df["买入日期"].astype(str).str[:4] == "2026"]
        pnl = g["盈亏金额"].astype(float)
        w, l = pnl[pnl > 0], pnl[pnl <= 0]
        return dict(n=int(len(g)), pnl=float(pnl.sum()),
                    p_win=float((pnl > 0).mean()),
                    avg_win=float(w.mean()) if len(w) else 0.0,
                    avg_loss=float(l.mean()) if len(l) else 0.0)

    a, b = seg(t15), seg(t20)
    # 总盈亏 ≈ n * [p*W + (1-p)*L]; 以 Top20 为基线逐因子替换(近似, 交互项并入末项)
    def tot(x):
        return x["n"] * (x["p_win"] * x["avg_win"] + (1 - x["p_win"]) * x["avg_loss"])

    base = tot(b)
    step1 = {**b, "n": a["n"]}                      # 笔数效应
    step2 = {**step1, "p_win": a["p_win"]}          # 胜率效应
    step3 = {**step2, "avg_win": a["avg_win"]}      # 均盈效应
    eff_n = tot(step1) - base
    eff_p = tot(step2) - tot(step1)
    eff_w = tot(step3) - tot(step2)
    eff_l = tot(a) - tot(step3)                     # 均亏效应+交互残差
    return dict(top15=a, top20=b, effect=dict(n=round(eff_n, 0), win_rate=round(eff_p, 0),
                                              avg_win=round(eff_w, 0), avg_loss=round(eff_l, 0)))


def main():
    dfs = {}
    for name, path in DATASETS.items():
        try:
            dfs[name] = pd.read_csv(path, encoding="utf-8-sig")
        except Exception as e:
            dfs[name] = None
            print(f"READ FAIL {name}: {e}")
    out = {k: {} for k in DATASETS}
    for name, df in dfs.items():
        if df is None:
            continue
        out[name] = {"yearly": yearly(df), "sentiment_bands": sent_bands(df)}
    if dfs.get("bp_ep_top15_20261018") is not None and dfs.get("bp_ep_top20_20261016") is not None:
        out["attr_2026_top15_vs_top20"] = attr2026(dfs["bp_ep_top15_20261018"],
                                                   dfs["bp_ep_top20_20261016"])
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    lines = ["# 诊断③ 分 regime 胜率×盈亏比矩阵 (2026-10-18)", "",
             "> 双门四格历史切片=结构化 N/A(缺 2016-2026 逐日门状态, gate 影子仅 2026-10 起);",
             "> regime 维度=年份 + L2情绪倍数(理由字段内嵌) + 止损占比。", ""]
    for name in DATASETS:
        if not out.get(name):
            continue
        lines.append(f"## {name}")
        lines.append("| 年 | n | 胜率%(价) | 胜率%(额) | 均盈 | 均亏 | 盈亏比 | 盈亏和 | 止损% | 调仓% | 风控 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in out[name]["yearly"]:
            lines.append(f"| {r['year']} | {r['n']} | {r['win_r']} | {r['win_a']} | {r['avg_win']} | "
                         f"{r['avg_loss']} | {r['pl_ratio']} | {r['pnl_sum']} | {r['stop_share']} | "
                         f"{r['reb_share']} | {r['risk_ctrl']} |")
        lines.append("")
        lines.append(f"L2情绪分带: {json.dumps(out[name]['sentiment_bands'], ensure_ascii=False)}")
        lines.append("")
    a = out.get("attr_2026_top15_vs_top20")
    if a:
        lines.append("## 2026 负年归因 (bp_ep, Top15 新基线 vs Top20 历史档, 买入年=2026)")
        lines.append(f"- Top15: {json.dumps(a['top15'], ensure_ascii=False)}")
        lines.append(f"- Top20: {json.dumps(a['top20'], ensure_ascii=False)}")
        lines.append(f"- 效应分解(元, 近似逐因子替换, 交互残差并入均亏项): "
                     f"{json.dumps(a['effect'], ensure_ascii=False)}")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print("OUT:", OUT_MD)


if __name__ == "__main__":
    main()
