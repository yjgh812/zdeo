# -*- coding: utf-8 -*-
"""
round6 买入决策理由溯源回填
===========================
对每一笔已成交交易，按(股票代码, 买入日期)从对应信号表取当日真实信号值，
生成可追溯的"买入决策理由"，写回 trades_full.csv 及各年度 trades CSV。
只使用买入当日可获取的信号数据（无未来函数）。
"""
import sqlite3, re
from pathlib import Path
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
ROOT = Path(r"D:\zcodesj\reports\tulong_backtest_multi_round6_20260819")

MODEL_SQL = {
    "bp_ep_core": "SELECT code, date, ep, bp FROM stock_factors_fundamental",
    "ml_signal": "SELECT code, date, ml_prob FROM stock_ml_scores_v22",
    "v375_signal": "SELECT code, date, v375_prob FROM stock_signals_v375",
    "p2_signal": "SELECT code, date, p2_prob FROM stock_signals_p2",
    "composite_signal": "SELECT code, date, composite_score, ai_prob_5d FROM stock_signals_v4",
}


def norm_date(d):
    s = str(d)[:10]
    return s.replace("-", "")


def build_reason(model, row, sig):
    code = str(row["股票代码"]).zfill(6)
    vals = sig.get((code, norm_date(row["买入日期"])))
    if model == "bp_ep_core":
        if vals and pd.notna(vals[0]):
            return f"bp+ep因子选股(ep={vals[0]:.4g},bp={vals[1]:.4g}) 季度Top20"
        return "bp+ep因子选股(当日信号缺失)"
    if model == "ml_signal":
        if vals and pd.notna(vals[0]):
            return f"ML因子信号 ml_prob={vals[0]:.4f} 月度Top20"
        return "ML因子信号(当日信号缺失)"
    if model == "v375_signal":
        if vals and pd.notna(vals[0]):
            return f"V375信号 v375_prob={vals[0]:.3f}"
        return "V375信号(当日信号缺失)"
    if model == "p2_signal":
        if vals and pd.notna(vals[0]):
            return f"P2信号 p2_prob={vals[0]:.3f}"
        return "P2信号(当日信号缺失)"
    if model == "composite_signal":
        if vals and pd.notna(vals[0]):
            return f"综合信号 composite={vals[0]:.3f},ai_5d={vals[1]:.3f}"
        return "综合信号(当日信号缺失)"
    return ""


def main():
    conn = sqlite3.connect(DB)
    for model, sql in MODEL_SQL.items():
        trades = ROOT / f"{model}_trades_full.csv"
        if not trades.exists():
            continue
        df = pd.read_csv(trades)
        df.columns = [c.strip("\ufeff").strip() for c in df.columns]
        if df.empty:
            continue
        codes = df["股票代码"].astype(str).str.zfill(6).unique()
        in_clause = ",".join(f"'{c}'" for c in codes)
        raw = pd.read_sql(f"{sql} WHERE code IN ({in_clause})", conn)
        raw["code"] = raw["code"].astype(str).str.zfill(6)
        raw["date"] = raw["date"].astype(str).str[:8]
        # 生成交易日历最近信号（信号表可能按实际交易日存储，直接匹配当日）
        sig = {(r.code, r.date): tuple(r[2:].values) for _, r in raw.iterrows()}
        # 也允许日期带横杠
        sig2 = {(r.code, r.date[:4] + "-" + r.date[4:6] + "-" + r.date[6:]): tuple(r[2:].values) for _, r in raw.iterrows()}
        sig.update(sig2)

        filled = 0
        for idx, row in df.iterrows():
            reason = build_reason(model, row, sig)
            df.at[idx, "买入决策理由"] = reason
            if reason and "缺失" not in reason:
                filled += 1
        df.to_csv(trades, index=False, encoding="utf-8-sig")
        # 各年度 trades CSV 同步
        for yf in ROOT.glob(f"{model}_trades_*.csv"):
            if "full" in yf.name:
                continue
            try:
                ydf = pd.read_csv(yf)
                ydf.columns = [c.strip("\ufeff").strip() for c in ydf.columns]
                key = ["买入日期", "股票代码"]
                if "买入决策理由" not in ydf.columns:
                    ydf["买入决策理由"] = ""
                m = df.merge(ydf.drop(columns=["买入决策理由"]) if "买入决策理由" in ydf.columns else ydf,
                             on=key, suffixes=("", "_y"), how="right")
                # 简化处理：直接用 full 表的映射按(买入日期,代码)回填
                lookup = df.set_index([df["买入日期"].astype(str), df["股票代码"].astype(str)])["买入决策理由"]
                ydf["买入决策理由"] = ydf.apply(
                    lambda r: lookup.get((str(r["买入日期"])[:10], str(r["股票代码"]).zfill(6)), ""), axis=1)
                ydf.to_csv(yf, index=False, encoding="utf-8-sig")
            except Exception as e:
                print(f"  !! {yf.name} 回填失败: {e}")
        print(f"{model}: 总{len(df)}笔, 理由已回填 {filled} 笔 ({(filled/len(df))*100:.1f}%)")
    conn.close()


if __name__ == "__main__":
    main()