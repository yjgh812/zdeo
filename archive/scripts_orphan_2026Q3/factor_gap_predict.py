# -*- coding: utf-8 -*-
"""
factor_gap_predict.py — L4跳空缺口预测因子模块

来源：reports/TULONG_MULTI_MODEL_BACKTEST_2016_2026.md 第3.2节
问题：ml大亏130笔，名义-8%止损实际成交-14.1%，滑点6.1pp占损失43%
根因：止损按次日开盘价成交，跳空低开致实际止损位远超名义线

设计思路（基于S1否决教训）：
  - S1 Gap保护（过滤路线）已否决：命中率低，大亏主因是持仓后下跌非跳空买入
  - L4走"预测+仓位再平衡"路线：前日特征→预测次日跳空风险→对高gap_risk标的降仓

因子定义（前日尾盘可获取，无未来函数）：
  1. body_pct: 前日实体大小 = |close-open|/close
  2. upper_shadow_pct: 前日上影 = (high-max(open,close))/close
  3. lower_shadow_pct: 前日下影 = (min(open,close)-low)/close
  4. vol_ratio: 前日量比 = volume/MA5(volume)
  5. dist_to_high20: 前日收盘距20日高点 = close/high20-1（负值，越接近0越靠近高点）
  6. amplitude20: 前20日振幅 = max(high20)/min(low20)-1
  7. mom5: 前5日涨幅
  8. mom20: 前20日涨幅

gap_risk_score: 基于实证统计的规则因子（非ML模型，避免过拟合）
  - 高风险条件（命中任一即风险+1）：
    a) 前日放量下跌（vol_ratio≥1.5 + change_pct≤-2）→ 次日跳空低开概率高
    b) 前日长上影（upper_shadow≥3% + change_pct>0）→ 盘中抛压
    c) 前日接近20日高点后回落（dist_to_high20>-2% + body_pct>2% + 收阴）→ 获利盘抛压
    d) 高波动+负动量（amplitude20>15% + mom5<-3%）→ 趋势下行中波动大

性质：提示性参考（与 verified_risk_flags/backtest_failure_filter 一致）
铁律：硬规则须主人明示，本模块仅打标并返回 gap_risk_score 供调仓层参考

用法:
    from factor_gap_predict import compute_gap_risk
    risk = compute_gap_risk("600016")  # 最近交易日
"""
import sqlite3
import pandas as pd
import os

_QFQ_CANDIDATES = [
    # 2026-08-29 优化专项修复: 根目录从来不是qfq真库位置(daily_chain_update.py:45为真源),
    # 旧候选链会静默落到backups 8/23陈旧快照; 真库提为首位, backups保留兜底。
    r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db",
    r"D:/zcodesj/backups/dragon_quant_qfq/weekly/dragon_quant_qfq_20260823.db",
]


def _resolve_qfq_path() -> str:
    for p in _QFQ_CANDIDATES:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return _QFQ_CANDIDATES[0]


QFQ_DB = _resolve_qfq_path()


def compute_gap_risk(code6: str, days_back: int = 0, qfq_path: str | None = None) -> dict:
    """计算个股最近交易日的跳空缺口风险评分。

    参数:
        code6: 6位股票代码
        days_back: 0=最新交易日, 1=前一交易日…
        qfq_path: 自定义QFQ库路径

    返回:
        dict: {
            date, gap_risk_score (0-4),
            risk_factors: {放量大跌, 长上影, 高点回落, 高波动下杀},
            detail: {body_pct, upper_shadow, lower_shadow, vol_ratio, dist_to_high20, ...},
            error
        }
        数据不足/出错时 score=0 + error 字段说明（优雅降级，不抛异常）。
    """
    path = qfq_path or QFQ_DB
    out = {
        "date": None,
        "gap_risk_score": 0,
        "risk_factors": {},
        "detail": {},
        "error": None,
    }

    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        df = pd.read_sql(
            "SELECT date, open, high, low, close, volume, change_pct "
            "FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 90",
            con, params=(code6,),
        )
        con.close()
    except Exception as e:
        out["error"] = f"qfq读取失败: {type(e).__name__} {e}"
        return out

    if df is None or len(df) < 25:
        out["error"] = f"数据不足({0 if df is None else len(df)}行<25)"
        return out

    df = df.iloc[::-1].reset_index(drop=True)  # 时间正序
    df["date"] = pd.to_numeric(df["date"], errors="coerce")

    idx = len(df) - 1 - days_back
    if idx < 20:
        out["error"] = f"回看{days_back}日后样本不足(idx={idx}<20)"
        return out

    r = df.iloc[idx]
    out["date"] = int(r["date"]) if pd.notna(r["date"]) else None

    close = float(r["close"])
    open_ = float(r["open"])
    high = float(r["high"])
    low = float(r["low"])
    volume = float(r["volume"])
    change_pct = float(r["change_pct"])

    # 5日均量
    vol_ma5 = df["volume"].astype(float).iloc[max(0, idx - 4): idx + 1].mean()
    vol_ratio = volume / vol_ma5 if vol_ma5 > 0 else 1.0

    # 20日高低点
    high20 = df["high"].astype(float).iloc[max(0, idx - 19): idx + 1].max()
    low20 = df["low"].astype(float).iloc[max(0, idx - 19): idx + 1].min()
    dist_to_high20 = (close / high20 - 1) * 100  # 负值
    amplitude20 = (high20 / low20 - 1) * 100 if low20 > 0 else 0.0

    # 动量
    close_series = df["close"].astype(float)
    mom5 = (close / close_series.iloc[max(0, idx - 5)] - 1) * 100 if idx >= 5 else 0.0
    mom20 = (close / close_series.iloc[max(0, idx - 20)] - 1) * 100 if idx >= 20 else 0.0

    # K线形态
    body_pct = abs(close - open_) / close * 100 if close > 0 else 0.0
    upper_shadow = (high - max(open_, close)) / close * 100 if close > 0 else 0.0
    lower_shadow = (min(open_, close) - low) / close * 100 if close > 0 else 0.0
    is_bearish = close < open_

    out["detail"] = {
        "close": round(close, 2),
        "change_pct": round(change_pct, 2),
        "body_pct": round(body_pct, 2),
        "upper_shadow": round(upper_shadow, 2),
        "lower_shadow": round(lower_shadow, 2),
        "vol_ratio": round(vol_ratio, 2),
        "dist_to_high20": round(dist_to_high20, 2),
        "amplitude20": round(amplitude20, 2),
        "mom5": round(mom5, 2),
        "mom20": round(mom20, 2),
        "is_bearish": is_bearish,
    }

    risk_factors = {}

    # a) 前日放量下跌：量比≥1.5 + 跌≥2% → 次日跳空低开概率高
    if vol_ratio >= 1.5 and change_pct <= -2:
        risk_factors["放量大跌"] = f"量比{vol_ratio:.1f}x+跌{change_pct:.1f}%"

    # b) 前日长上影：上影≥3% + 收涨 → 盘中抛压
    if upper_shadow >= 3.0 and change_pct > 0:
        risk_factors["长上影"] = f"上影{upper_shadow:.1f}%+涨{change_pct:.1f}%"

    # c) 高点回落：接近20日高点(>-2%) + 大实体(>2%) + 收阴 → 获利盘抛压
    if dist_to_high20 > -2.0 and body_pct > 2.0 and is_bearish:
        risk_factors["高点回落"] = f"距高点{dist_to_high20:.1f}%+实体{body_pct:.1f}%+收阴"

    # d) 高波动下杀：振幅>15% + 5日跌>3% → 趋势下行中波动大
    if amplitude20 > 15.0 and mom5 < -3.0:
        risk_factors["高波动下杀"] = f"振幅{amplitude20:.1f}%+5日跌{mom5:.1f}%"

    out["risk_factors"] = risk_factors
    out["gap_risk_score"] = len(risk_factors)
    return out


GAP_RISK_HINT = (
    "⚠️跳空缺口风险(L4): 前日特征预示次日跳空低开概率较高，"
    "建议对该标的降仓（止损滑点6.1pp占大亏损失43%，BT-FAIL-004）"
)


def flags_to_hints(risk: dict) -> list:
    """把 compute_gap_risk 结果转为提示文案列表。"""
    hints = []
    if risk.get("gap_risk_score", 0) > 0:
        hints.append(GAP_RISK_HINT)
        for factor, detail in risk["risk_factors"].items():
            hints.append(f"  → {factor}: {detail}")
    return hints


if __name__ == "__main__":
    # 测试：民生银行 + 九洲集团 + 茅台
    test_cases = [
        ("600016", "民生银行"),
        ("300040", "九洲集团"),
        ("600519", "贵州茅台"),
        ("601818", "光大银行"),
    ]
    print(f"QFQ库: {QFQ_DB}")
    print()
    for code, name in test_cases:
        risk = compute_gap_risk(code)
        print(f"=== {code} {name} ===")
        if risk["error"]:
            print(f"  ❌ {risk['error']}")
        else:
            print(f"  date={risk['date']}  gap_risk_score={risk['gap_risk_score']}/4")
            print(f"  detail: {risk['detail']}")
            if risk["risk_factors"]:
                for factor, detail in risk["risk_factors"].items():
                    print(f"  ⚠️ {factor}: {detail}")
            else:
                print("  ✅ 无跳空风险因子命中")
        print()
