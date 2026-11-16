# -*- coding: utf-8 -*-
"""
holding_risk_score.py — 持仓风险预警引擎（2026-08-10 知微）

基于经验库28笔真实亏损+经验库提炼的5维度风险评分：
  R1 阴跌趋势（均线空头）：5日收益<0 且 close<MA5<MA10<MA20  → 0.25
  R2 相对弱势（跑输大盘）：个股20日收益 < 指数20日收益 - 5%  → 0.25
  R3 近期破位（新低信号）：close跌破近20日最低点  → 0.20
  R4 波动骤增（震荡加剧）：近5日ATR/60日ATR > 1.5  → 0.15
  R5 成交萎缩（资金撤退）：近5日均量/20日均量 < 0.7  → 0.15

评分范围 0-1，>0.6 触发预警。
诚实标注：
  - 权重来自经验库定性提炼（非统计优化），后续根据全市场回测调整
  - R2 需要指数数据（上证指数 sh000001），传入 index_ret20 参数
  - 全部用日线数据，无外部依赖
"""
import numpy as np
import pandas as pd


def compute_holding_risk(df: pd.DataFrame, index_ret20: float = 0.0) -> dict:
    """计算单只持仓的风险评分。

    参数:
        df: 单只股票日线（index=DatetimeIndex, 含 open/high/low/close/volume），至少60行
        index_ret20: 同期大盘指数20日收益率（如上证指数），用于 R2 相对弱势

    返回:
        dict: {score: 0-1, level: 预警级别, R1-R5: 各维度命中, detail: 各指标值}
    """
    if df is None or len(df) < 60:
        return {"score": 0, "level": "数据不足", "error": "日线<60行"}

    d = df.copy()
    close = d["close"].iloc[-1]
    n = len(d)

    # R1: 阴跌趋势（均线空头排列）
    ma5 = d["close"].rolling(5).mean().iloc[-1]
    ma10 = d["close"].rolling(10).mean().iloc[-1]
    ma20 = d["close"].rolling(20).mean().iloc[-1]
    ret5 = close / d["close"].iloc[-6] - 1 if n >= 6 else 0
    r1 = (ret5 < 0) and (close < ma5) and (ma5 < ma10) and (ma10 < ma20)

    # R2: 相对弱势（20日收益显著跑输大盘）
    stock_ret20 = close / d["close"].iloc[-21] - 1 if n >= 21 else 0
    r2 = stock_ret20 < (index_ret20 - 0.05)  # 跑输大盘>5pp

    # R3: 近期破位（跌破近20日最低，不含当日）
    if n >= 21:
        low_20 = d["low"].iloc[-21:-1].min()  # 前20日最低（不含今日）
        r3 = close < low_20
    else:
        r3 = False

    # R4: 波动骤增（近5日ATR / 60日ATR > 1.5）
    if n >= 65:
        tr = pd.concat([
            d["high"] - d["low"],
            (d["high"] - d["close"].shift(1)).abs(),
            (d["low"] - d["close"].shift(1)).abs()
        ], axis=1).max(axis=1)
        atr5 = tr.iloc[-5:].mean()
        atr60 = tr.iloc[-60:].mean()
        vol_ratio = atr5 / atr60 if atr60 > 0 else 1.0
        r4 = vol_ratio > 1.5
    else:
        vol_ratio = 1.0
        r4 = False

    # R5: 成交萎缩（近5日均量/20日均量 < 0.7）
    if n >= 25:
        vol_ma5 = d["volume"].iloc[-5:].mean()
        vol_ma20 = d["volume"].iloc[-20:].mean()
        vol_shrink = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
        r5 = vol_shrink < 0.7
    else:
        vol_shrink = 1.0
        r5 = False

    # 加权评分
    weights = {"R1": 0.25, "R2": 0.25, "R3": 0.20, "R4": 0.15, "R5": 0.15}
    hits = {"R1": bool(r1), "R2": bool(r2), "R3": bool(r3), "R4": bool(r4), "R5": bool(r5)}
    score = sum(weights[k] for k, v in hits.items() if v)

    if score >= 0.6:
        level = "🔴 高风险（建议减仓）"
    elif score >= 0.4:
        level = "🟡 中风险（密切关注）"
    elif score >= 0.2:
        level = "🟢 低风险"
    else:
        level = "✅ 安全"

    return {
        "score": round(score, 3),
        "level": level,
        "R1_yindie": hits["R1"],      # 阴跌趋势
        "R2_weakness": hits["R2"],    # 相对弱势
        "R3_breakdown": hits["R3"],   # 近期破位
        "R4_volatility": hits["R4"],  # 波动骤增
        "R5_volume": hits["R5"],      # 成交萎缩
        "detail": {
            "close": round(close, 2),
            "ret5": round(ret5, 4),
            "ret20": round(stock_ret20, 4),
            "index_ret20": round(index_ret20, 4),
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
            "vol_ratio_atr": round(vol_ratio, 3),
            "vol_shrink": round(vol_shrink, 3),
        }
    }


if __name__ == "__main__":
    # 自测：构造一个阴跌+跑输+破位+缩量 的"粤传媒样"高风险股
    np.random.seed(42)
    n = 80
    # 模拟：从 10 元阴跌到 8 元，均线空头，缩量
    closes = np.linspace(10, 8, n) + np.random.randn(n) * 0.1
    highs = closes + 0.15
    lows = closes - 0.15
    vols = np.linspace(5_000_000, 2_000_000, n)  # 缩量
    idx = pd.bdate_range("2026-01-01", periods=n)
    test_df = pd.DataFrame({"open": closes, "high": highs, "low": lows,
                            "close": closes, "volume": vols}, index=idx)

    # 大盘同期涨5%（个股跑输13%+）
    result = compute_holding_risk(test_df, index_ret20=0.05)
    print("=== 粤传媒样阴跌股测试 ===")
    print(f"评分: {result['score']}, 级别: {result['level']}")
    print(f"R1阴跌={result['R1_yindie']} R2弱势={result['R2_weakness']} "
          f"R3破位={result['R3_breakdown']} R4波动={result['R4_volatility']} R5缩量={result['R5_volume']}")
    print(f"明细: {result['detail']}")
    assert result["score"] >= 0.4, f"阴跌股应>=中风险, 实际{result['score']}"

    # 自测2：构造一个健康上涨股（低风险）
    closes2 = np.linspace(10, 13, n) + np.random.randn(n) * 0.1
    highs2 = closes2 + 0.2
    lows2 = closes2 - 0.1
    vols2 = np.full(n, 5_000_000.0)
    test_df2 = pd.DataFrame({"open": closes2, "high": highs2, "low": lows2,
                             "close": closes2, "volume": vols2}, index=idx)
    result2 = compute_holding_risk(test_df2, index_ret20=0.05)
    print(f"\n=== 健康上涨股测试 ===")
    print(f"评分: {result2['score']}, 级别: {result2['level']}")
    assert result2["score"] < 0.4, f"健康股应低风险, 实际{result2['score']}"

    # 自测3：数据不足
    result3 = compute_holding_risk(test_df.iloc[:30])
    assert "error" in result3, f"数据不足应报错: {result3}"

    print("\n✅ holding_risk_score 自测全绿")
