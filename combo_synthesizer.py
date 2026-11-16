# -*- coding: utf-8 -*-
"""
共享层: 生产组合 60/40 净值合成器 (唯一实现)
====================================================
背景: 8/26 strategy.py 改动使 equity_curve.date 变 datetime64, 字符串键失配致
多处 combo_60_40 静默归零; 8/27 固化红线 "自研聚合器必须先过权威产物回归校验再采信"。
本模块为组合合成的唯一 owner, 供 tulong_backtest_full_report / driftcheck /
round10_adopted_combo 复用, 杜绝再写第三个副本。公式=每日 60/40 加权复合, 与
历史权威实现逐位一致(回归校验锚 299,276.84)。
"""
import pandas as pd

AUTHORITATIVE_BASELINE = 299276.84  # round9_pitv2_recheck_20260826 权威组合终值
BASELINE_LEGS = (
    r"D:\zcodesj\reports\round9_pitv2_recheck_20260826\bp_ep_top15_pitv2_equity.csv",
    r"D:\zcodesj\reports\round9_pitv2_recheck_20260826\ml_hold20_pitv2_equity.csv",
)


def _load_leg(ec):
    if isinstance(ec, str):
        ec = pd.read_csv(ec)
    col = "value" if "value" in ec.columns else "equity"
    return ec.assign(_d=ec["date"].astype(str).str[:10], _v=ec[col])[["_d", "_v"]]


def synth_combo_60_40(ec_a, ec_b, init=100000.0, wa=0.50, wb=0.50) -> pd.DataFrame:
    """merge 版合成: 两端日期归一化 'YYYY-MM-DD' 后按日对齐, 每日按权重加权复合。
    ⚠️ 2026-08-29 v1.4 起生产组合已切纯 bp_ep 单腿(ml 腿退役, 见 tulong_production_config.json);
    本函数降级为研究层历史复现工具(round9/round10 50/50、60/40 锚 299,276.84 由显式传参复现)。
    ec_a/ec_b 可为 DataFrame(date, equity|value) 或 CSV 路径。
    返回 DataFrame(date, equity); 首日权益=init, 与原 combo_60_40 同构。"""
    a, b = _load_leg(ec_a), _load_leg(ec_b)
    m = a.merge(b, on="_d", how="inner").sort_values("_d").reset_index(drop=True)
    combined = [init]
    for i in range(1, len(m)):
        a0, a1 = m.loc[i - 1, "_v_x"], m.loc[i, "_v_x"]
        b0, b1 = m.loc[i - 1, "_v_y"], m.loc[i, "_v_y"]
        r = 0.0
        if a0 and a0 > 0:
            r += wa * (a1 - a0) / a0
        if b0 and b0 > 0:
            r += wb * (b1 - b0) / b0
        combined.append(combined[-1] * (1 + r))
    return pd.DataFrame({"date": m["_d"].values, "equity": combined[:len(m)]})


def regression_self_test() -> bool:
    """权威基线回归校验: 用 8/26 两腿 CSV + 显式 60/40 重算, 必须复现 299,276.84 分毫不差。
    (默认权重已随 C1 采纳改为 50/50; 基线锚必须显式 60/40 复现)"""
    ec = synth_combo_60_40(*BASELINE_LEGS, wa=0.60, wb=0.40)
    tv = float(ec["equity"].iloc[-1])
    ok = abs(tv - AUTHORITATIVE_BASELINE) < 0.01
    print(f"[combo_synthesizer] 回归校验: 基线重算终值={tv:.2f} "
          f"(权威={AUTHORITATIVE_BASELINE}) -> {'PASS' if ok else 'FAIL'}")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if regression_self_test() else 1)
