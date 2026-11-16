# -*- coding: utf-8 -*-
"""
verified_risk_flags.py — 已验证下跌风险特征计算模块（2026-08-18）

来源：verify_downdrop_features_20260818.py 全市场验证（2026-01~08 约79万股票日）
四个特征均为「提示性参考」，不构成硬性卖出规则（铁律：硬规则须主人明示）。

已验证有效性（vs 基准 次日均+0.002%/跌占比49.8%）：
  broken_board      炸板(触板未封)      次日均-0.45%, 63%低开, 31.7%跌≥3%  ← 最强
  guillotine        断头铡刀(破3线)     次日均-0.39%, 22.4%跌≥3%
  upper_shadow      长上影阳线(≥4%)     60%低开, 28.5%跌≥3%
  high_pos_heavy    高位放量下跌(pos60≥0.9,量≥2x,跌≥2%)  T+5跌占比63.2% ← 中期信号

证伪项（不纳入）：缩量阴跌、高位放量滞涨、低位放量下跌 — 数据无信号。

用法:
    from verified_risk_flags import compute_risk_flags
    flags = compute_risk_flags("002582")  # 最近交易日
    flags = compute_risk_flags("002582", days_back=1)  # 指定往前第N个交易日
"""
import sqlite3
import pandas as pd

QFQ_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"

# 涨停判定阈值（按板块，与验证脚本同口径）
def _limit_th(code6: str) -> float:
    if code6.startswith(("300", "301", "688")):
        return 19.5
    if code6.startswith(("8", "4", "92")):
        return 29.5
    return 9.5


def compute_risk_flags(code6: str, days_back: int = 0, qfq_path: str = QFQ_DB) -> dict:
    """计算个股最近交易日的已验证风险特征。

    days_back: 0=该股最新交易日, 1=前一交易日…
    返回 dict: {date, broken_board, guillotine, upper_shadow, high_pos_heavy,
                detail:{...}, error}
    数据不足/出错时 flags 全 False + error 字段说明（优雅降级，不抛异常）。
    """
    out = {"date": None, "broken_board": False, "guillotine": False,
           "upper_shadow": False, "high_pos_heavy": False, "detail": {}, "error": None}
    try:
        con = sqlite3.connect(f"file:{qfq_path}?mode=ro", uri=True)
        df = pd.read_sql(
            "SELECT date, open, high, low, close, volume, change_pct "
            "FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 90",
            con, params=(code6,))
        con.close()
    except Exception as e:
        out["error"] = f"qfq读取失败: {type(e).__name__} {e}"
        return out
    if df is None or len(df) < 25:
        out["error"] = f"数据不足({0 if df is None else len(df)}行<25)"
        return out
    df = df.iloc[::-1].reset_index(drop=True)  # 时间正序
    df["date"] = pd.to_numeric(df["date"], errors="coerce")

    # 均线/均量（向量化）
    close, high, open_ = df["close"], df["high"], df["open"]
    for n in (5, 10, 20, 60):
        df[f"ma{n}"] = close.rolling(n).mean()
    df["vol_ma5"] = df["volume"].rolling(5).mean()
    df["high60"] = high.rolling(60).max()
    df["pos60"] = close / df["high60"]

    idx = len(df) - 1 - days_back
    if idx < 60:
        out["error"] = f"回看{days_back}日后样本不足(idx={idx})"
        return out
    r = df.iloc[idx]
    p = df.iloc[idx - 1]
    th = _limit_th(code6)
    out["date"] = int(r["date"])

    # ① 炸板: 盘中触涨停未封住
    prev_close = r["close"] / (1 + r["change_pct"] / 100)
    high_pct = (r["high"] / prev_close - 1) * 100
    out["broken_board"] = bool((high_pct >= th) and (r["change_pct"] < th))
    if out["broken_board"]:
        out["detail"]["high_pct"] = round(high_pct, 2)

    # ② 断头铡刀: 跌≥3% 且今收<三线最低 且昨收>昨三线最高
    mas_now = min(r["ma5"], r["ma10"], r["ma20"])
    mas_prev = max(p["ma5"], p["ma10"], p["ma20"])
    out["guillotine"] = bool((r["change_pct"] <= -3) and (r["close"] < mas_now) and (p["close"] > mas_prev))

    # ③ 长上影阳线: 上影≥4% 且收涨
    upper = (r["high"] - max(r["open"], r["close"])) / r["close"]
    out["upper_shadow"] = bool((upper >= 0.04) and (r["change_pct"] > 0))
    if out["upper_shadow"]:
        out["detail"]["upper_shadow_pct"] = round(upper * 100, 2)

    # ④ 高位放量下跌: 量≥2×5日均量 跌≥2% pos60≥0.9（中期T+5信号）
    out["high_pos_heavy"] = bool(
        (r["volume"] >= 2 * r["vol_ma5"]) and (r["change_pct"] <= -2)
        and (r["pos60"] >= 0.9))
    if out["high_pos_heavy"]:
        out["detail"]["pos60"] = round(r["pos60"], 3)

    out["detail"]["chg_pct"] = round(r["change_pct"], 2)
    return out


HINT_TEXT = {
    "broken_board": "R6炸板⚠️(已验证:次日均-0.45%/63%低开/31.7%跌≥3%,最强负面信号)",
    "guillotine": "R6断头铡刀⚠️(已验证:次日均-0.39%/22.4%跌≥3%)",
    "upper_shadow": "R6长上影⚠️(已验证:60%低开/28.5%跌≥3%)",
    "high_pos_heavy": "R6高位放量下跌⚠️(已验证:T+5跌占比63.2%,中期信号)",
}


def flags_to_hints(flags: dict) -> list:
    """把 compute_risk_flags 结果转为提示文案列表"""
    hints = []
    for k, txt in HINT_TEXT.items():
        if flags.get(k):
            hints.append(txt)
    return hints


if __name__ == "__main__":
    import sys, json
    code = sys.argv[1] if len(sys.argv) > 1 else "002582"
    back = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    print(json.dumps(compute_risk_flags(code, back), ensure_ascii=False, indent=2))