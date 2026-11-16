# -*- coding: utf-8 -*-
"""sens_compare_20261019.py — 呈请①B 三参数敏感性对比表（2026-10-19）

基线 = backtest_full_20261018_dose_n15（N15 生产等价, bp 181.17%/1612笔/51.92%）;
情景 = cost15(费用×1.5) / slip10(滑点+10bp) / gap1(买入侧缺口+1%)。
判定(材料口径): 若敏感性下胜率/盈亏比塌方 → 结论对成本假设脆弱; 若稳健 → 回测结论可信度加固。
只读研究件·零交易权。
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

BASE = r"D:\zcodesj\reports\backtest_full_20261018_dose_n15"
SCENS = [
    ("基线(N15生产等价)", BASE),
    ("费用×1.5", r"D:\zcodesj\reports\backtest_full_20261019_sens_cost15"),
    ("滑点+10bp", r"D:\zcodesj\reports\backtest_full_20261019_sens_slip10"),
    ("买入缺口+1%", r"D:\zcodesj\reports\backtest_full_20261019_sens_gap1"),
]
OUT_MD = r"D:\zcodesj\reports\sens_compare_20261019.md"
OUT_JSON = r"D:\zcodesj\analysis\sens_compare_20261019_out.json"
ARMS = ["bp_ep_core", "ml_signal"]


def read_summary(root, arm):
    with open(rf"{root}\{arm}_summary.json", encoding="utf-8") as f:
        d = json.load(f)
    s = d.get("summary", {})
    m = d.get("meta", {})
    return {
        "ret": s.get("total_return_pct"), "win": s.get("trade_win_rate_pct"),
        "pl": s.get("profit_loss_ratio"), "mdd": s.get("max_drawdown_pct"),
        "n": m.get("n_trades"),
    }


def main():
    data = {}
    rows = []
    for label, root in SCENS:
        for arm in ARMS:
            try:
                v = read_summary(root, arm)
            except Exception as e:
                v = {"error": str(e)}
            data.setdefault(arm, {})[label] = v
    for arm in ARMS:
        base = data[arm].get(SCENS[0][0], {})
        for label, _ in SCENS:
            v = data[arm].get(label, {})
            if "error" in v:
                rows.append(f"| {arm} | {label} | ERR | | | | |")
                continue
            d_ret = None
            if base.get("ret") is not None and v.get("ret") is not None:
                d_ret = round(v["ret"] - base["ret"], 2)
            rows.append(f"| {arm} | {label} | {v.get('ret')} | {v.get('win')} | {v.get('pl')} | {v.get('mdd')} | {d_ret} |")

    L = []
    L.append("# 三参数敏感性对比（呈请①B · 2026-10-19）\n")
    L.append("> 只读研究件。fork=analysis/bt_full_report_sens_20261019.py（dose N15 克隆，三环境旋钮默认=基线零变化）；窗口 2016-01-01~2026-10-16 全史主口径；双臂 TULONG_ARMS=bp+ep_core,ml_signal。\n")
    L.append("| 臂 | 情景 | 总收益% | 胜率% | 盈亏比 | MDD% | 收益差 vs 基线(pp) |")
    L.append("|---|---|---|---|---|---|---|")
    L.extend(rows)
    L.append("")
    L.append("- 判读口径: 材料判定=敏感性下结论塌方则先修成本假设；本表为收益/胜率/盈亏比/MDD 四维逐情景对照。")
    L.append("- 注: gap1 为买入侧单边+1%（SlippageConfig.calculate 买入分支实现），严格对齐材料「开盘缺口+1%」语义。")
    L.append("*脚本直出零转抄: analysis/sens_compare_20261019.py。*")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for arm in ARMS:
        print(arm, json.dumps(data[arm], ensure_ascii=False))


if __name__ == "__main__":
    main()
