# -*- coding: utf-8 -*-
"""
round9 优化栈 WFA 门禁验证 — PIT v2 口径重跑 (2026-08-26)
==========================================================
背景: 8/25 WFA(5折参数选择型)基于 v1 前视口径 equity; v1 前视发现后 A 项已经
      v2 重核稳健成立, 本脚本闭合最后一个悬置面: WFA 结论在 v2 口径下是否保持。
协议: 严格复刻 8/25 — train 24m / test 12m 滚动 5 折(OOS=2018/2020/2022/2024/2026),
      每折 IS 段独立选参数(bp15|bp20, m20|m45, dyn(0.4/0.7)|static60/40) -> OOS 执行,
      一次协议 N=1 无 OOS 后调参(一票制)。
输入: 全部已落盘 equity, 纯后处理无回测自由度:
      bp15_v2 = pitv2_recheck | bp20_v2 = ma250_activated对照(ml/bp不读regime, v2口径)
      m20 = pitv2_recheck(与v1全同, ml不读PIT) | m45 = round9主跑(ml不受PIT影响)
"""
import sqlite3, json, itertools, os
import pandas as pd
import numpy as np

OUT = r"D:\zcodesj\reports\round9_wfa_v2_recheck_20260826"
os.makedirs(OUT, exist_ok=True)


def load_eq(p, col):
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")[col]


EQ = {
    "bp15": load_eq(r"D:\zcodesj\reports\round9_pitv2_recheck_20260826\bp_ep_top15_pitv2_equity.csv", "equity"),
    "bp20": load_eq(r"D:\zcodesj\reports\round9_ma250_activated_20260825\bp_ep_core_nochange_equity.csv", "equity"),
    "m20":  load_eq(r"D:\zcodesj\reports\round9_pitv2_recheck_20260826\ml_hold20_pitv2_equity.csv", "equity"),
    "m45":  load_eq(r"D:\zcodesj\reports\tulong_backtest_round9_20260825\ml_signal_equity.csv", "value"),
}

# T-1 MA250 状态 (2014起全程有值; 8/25版为2016起前100日无状态, 复算证实仅窗口边界差异)
conn = sqlite3.connect(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db")
idx = pd.read_sql("SELECT date, close FROM index_daily WHERE index_code='000001' "
                  "AND date BETWEEN 20140101 AND 20260824 ORDER BY date", conn)
conn.close()
idx["date"] = pd.to_datetime(idx["date"].astype(str), format="%Y%m%d")
idx["ma250"] = idx["close"].rolling(250, min_periods=100).mean()
BULL = (idx["close"] > idx["ma250"]).shift(1)  # T-1 状态
BULL.index = idx["date"]

RET = {k: v.pct_change() for k, v in EQ.items()}
DATES = sorted(set(EQ["bp15"].index) & set(EQ["bp20"].index) &
               set(EQ["m20"].index) & set(EQ["m45"].index))
IDX = pd.DatetimeIndex(DATES)


def seg_return(bp_key, m_key, wmode, mask):
    r_bp = RET[bp_key].reindex(IDX)[mask]
    r_ml = RET[m_key].reindex(IDX)[mask]
    if wmode == "static":
        r = 0.6 * r_bp + 0.4 * r_ml
    else:  # dyn: 多头bp0.4 / 空头bp0.7
        bull = BULL.reindex(IDX)[mask]
        w = np.where(bull, 0.4, 0.7)
        r = pd.Series(w * r_bp + (1 - w) * r_ml, index=r_bp.index)
    return float((1 + r.fillna(0)).prod() - 1)


FOLDS = [  # (IS起, IS止, OOS起, OOS止)
    ("2016-01-01", "2017-12-31", "2018-01-01", "2018-12-31"),
    ("2018-01-01", "2019-12-31", "2020-01-01", "2020-12-31"),
    ("2020-01-01", "2021-12-31", "2022-01-01", "2022-12-31"),
    ("2022-01-01", "2023-12-31", "2024-01-01", "2024-12-31"),
    ("2024-01-01", "2025-12-31", "2026-01-01", "2026-08-24"),
]
CANDIDATES = list(itertools.product(["bp15", "bp20"], ["m20", "m45"], ["static", "dyn"]))

rows, oos_stack, oos_base = [], [], []
for is_s, is_e, os_s, os_e in FOLDS:
    is_mask = (IDX >= is_s) & (IDX <= is_e)
    os_mask = (IDX >= os_s) & (IDX <= os_e)
    best = max(CANDIDATES, key=lambda c: seg_return(*c, is_mask))
    oos_r = seg_return(*best, os_mask)
    base_r = seg_return("bp20", "m45", "static", os_mask)
    oos_stack.append(1 + oos_r); oos_base.append(1 + base_r)
    rows.append({"oos": os_s[:4], "is_pick": "/".join(best), "oos_stack": round(oos_r*100, 1),
                 "oos_base": round(base_r*100, 1), "diff_pp": round((oos_r-base_r)*100, 1)})

concat_stack = float(np.prod(oos_stack) - 1) * 100
concat_base = float(np.prod(oos_base) - 1) * 100
df = pd.DataFrame(rows)
print(df.to_string(index=False))
print(f"\n拼接OOS(v2口径): 优化栈 {concat_stack:+.1f}% vs 基线 {concat_base:+.1f}% = {concat_stack-concat_base:+.1f}pp")
worse = sum(1 for r in rows if r["diff_pp"] < 0)
print(f"OOS 劣于基线折数: {worse}/5")
for k in ["bp15", "bp20", "m20", "m45", "static", "dyn"]:
    n = sum(1 for r in rows if k in r["is_pick"].split("/"))
    print(f"IS 选择 {k}: {n}/5 折")

json.dump({"folds": rows, "concat_stack_pct": concat_stack, "concat_base_pct": concat_base,
           "folds_worse_than_base": worse,
           "v1对照": {"concat_stack": 16.4, "concat_base": 2.5, "verdicts": "S1 PASS/S2 PASS/S3 WARN"}},
          open(os.path.join(OUT, "wfa_v2_result.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
df.to_csv(os.path.join(OUT, "wfa_v2_folds.csv"), index=False, encoding="utf-8-sig")
print("WFA v2 重跑完成")
