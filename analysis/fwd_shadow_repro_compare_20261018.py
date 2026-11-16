# -*- coding: utf-8 -*-
"""复现测试比对：影子 runner（win2024 锚）vs canonical 引擎产物逐笔对齐。
预期 = 影子逐笔 == canonical 逐笔（剔除窗口末日 end_of_sample 行，影子语义抑制项）。
PASS → exit 0；FAIL → exit 3 并打印前若干处差异。
"""
import json
import sys

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CANON_DIR = r"D:/zcodesj/reports/backtest_full_win2024_20261016_eod"
SHADOW_LEDGER = r"D:/zcodesj/tmp/fwd_shadow_repro_20261018/main_combo_fwd_shadow_repro2024_ledger.jsonl"

ARM_MAP = {
    "bp+ep_core": "bp_ep_core_trades_full.csv",
    "ml_signal": "ml_signal_trades_full.csv",
}
KEY_COLS = ["买入日期", "卖出日期", "股票代码", "买入价格", "卖出价格", "持股数量", "卖出触发理由"]
SOFT_COLS = ["盈亏金额", "盈亏比例%", "交易成本", "持仓天数"]


def load_canonical(arm):
    df = pd.read_csv(f"{CANON_DIR}/{ARM_MAP[arm]}", encoding="utf-8-sig")
    df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
    before = len(df)
    df = df[df["卖出触发理由"] != "end_of_sample"].copy()
    dropped_eos = before - len(df)
    return df, dropped_eos


def load_shadow(arm):
    rows = []
    with open(SHADOW_LEDGER, "r", encoding="utf-8") as f:
        for ln in f:
            r = json.loads(ln)
            if r.get("type") == "trade" and r.get("arm") == arm:
                rows.append(r)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.rename(columns={
        "entry_date": "买入日期", "exit_date": "卖出日期", "code": "股票代码",
        "entry_price": "买入价格", "exit_price": "卖出价格", "shares": "持股数量",
        "exit_reason": "卖出触发理由", "pnl": "盈亏金额", "pnl_pct": "盈亏比例%",
        "cost": "交易成本", "holding_bars": "持仓天数", "entry_reason": "买入决策理由",
    })
    return df


def main():
    total_diff = 0
    for arm in ARM_MAP:
        canon, dropped_eos = load_canonical(arm)
        shadow = load_shadow(arm)
        sort_keys = ["买入日期", "股票代码", "卖出日期"]
        canon = canon.sort_values(sort_keys).reset_index(drop=True)
        shadow_s = shadow.sort_values(sort_keys).reset_index(drop=True) if not shadow.empty else shadow
        print(f"== {arm}: canonical(剔EOS {dropped_eos} 行)={len(canon)} vs shadow={len(shadow_s)}")
        if len(canon) != len(shadow_s):
            print(f"   [FAIL] 行数不一致")
            total_diff += 1
            continue
        mismatch = 0
        reason_mismatch = 0
        for i in range(len(canon)):
            c, s = canon.iloc[i], shadow_s.iloc[i]
            for col in KEY_COLS:
                cv, sv = c[col], s[col]
                if col in ("买入价格", "卖出价格"):
                    if abs(float(cv) - float(sv)) > 1e-6:
                        mismatch += 1
                        if mismatch <= 5:
                            print(f"   [DIFF] 行{i} {col}: canon={cv} vs shadow={sv}")
                elif col == "股票代码":
                    if str(cv).zfill(6) != str(sv).zfill(6):
                        mismatch += 1
                        if mismatch <= 5:
                            print(f"   [DIFF] 行{i} {col}: canon={cv} vs shadow={sv}")
                elif col == "持股数量":
                    if int(cv) != int(sv):
                        mismatch += 1
                        if mismatch <= 5:
                            print(f"   [DIFF] 行{i} {col}: canon={cv} vs shadow={sv}")
                else:
                    if str(cv)[:10] != str(sv)[:10]:
                        mismatch += 1
                        if mismatch <= 5:
                            print(f"   [DIFF] 行{i} {col}: canon={cv} vs shadow={sv}")
            for col in SOFT_COLS:
                try:
                    if abs(float(c[col]) - float(s[col])) > max(1e-6, abs(float(c[col])) * 1e-9):
                        mismatch += 1
                        if mismatch <= 5:
                            print(f"   [DIFF] 行{i} {col}: canon={c[col]} vs shadow={s[col]}")
                except (ValueError, TypeError):
                    pass
            if str(c.get("买入决策理由", "")) != str(s.get("买入决策理由", "")):
                reason_mismatch += 1
        print(f"   硬字段差={mismatch}｜买入理由差={reason_mismatch}（理由仅统计不判死）")
        total_diff += mismatch
    if total_diff == 0:
        print("REPRO PASS: 影子 runner 与 canonical 引擎逐笔一致（EOS 抑制项豁免）")
        sys.exit(0)
    print(f"REPRO FAIL: 总差异数={total_diff}")
    sys.exit(3)


if __name__ == "__main__":
    main()
