#!/usr/bin/env python3
"""
宏观环境闸门 (Macro Regime Gate)
===============================
接入屠龙系统决策流程，根据最新宏观数据输出市场环境等级，
影响：仓位上限、闸门阈值、投资风格偏好

等级定义：
  BULLISH    → 强复苏/过热 → 满仓进攻，成长风格
  CAUTIOUS   → 温和复苏   → 正常仓位，均衡风格  
  DEFENSIVE  → 弱复苏/通缩 → 半仓防御，红利/价值风格
  RISK_OFF   → 衰退/危机   → 轻仓/空仓，现金为王

用法:
  python macro_regime_gate.py              → 输出当前环境等级
  python macro_regime_gate.py --json       → JSON格式
  python macro_regime_gate.py --cron       → 输出单行摘要(适合cron记录)
"""

import akshare as ak
import pandas as pd
import json
import sys
from datetime import datetime


def _latest_valid(df: pd.DataFrame) -> pd.DataFrame:
    """过滤掉今值为None的行（未来日期待公布），取最新有效行"""
    if "今值" in df.columns:
        valid = df[df["今值"].notna()]
        return valid.tail(1) if not valid.empty else df.tail(1)
    return df.tail(1)


def get_macro_data() -> dict:
    """获取最新宏观数据"""
    result = {}

    # GDP (滞后指标，季度更新)
    try:
        df = ak.macro_china_gdp_yearly()
        l = _latest_valid(df)
        result["gdp"] = {
            "value": float(l["今值"].values[0]) if pd.notna(l["今值"].values[0]) else None,
            "previous": float(l["前值"].values[0]) if pd.notna(l["前值"].values[0]) else None,
            "date": l["日期"].values[0],
        }
    except Exception as e:
        result["gdp"] = {"error": str(e)}

    # CPI (月度更新)
    try:
        df = ak.macro_china_cpi_yearly()
        l = _latest_valid(df)
        result["cpi"] = {
            "value": float(l["今值"].values[0]) if pd.notna(l["今值"].values[0]) else None,
            "previous": float(l["前值"].values[0]) if pd.notna(l["前值"].values[0]) else None,
            "date": l["日期"].values[0],
        }
    except Exception as e:
        result["cpi"] = {"error": str(e)}

    # PPI (月度更新)
    try:
        df = ak.macro_china_ppi_yearly()
        l = _latest_valid(df)
        result["ppi"] = {
            "value": float(l["今值"].values[0]) if pd.notna(l["今值"].values[0]) else None,
            "previous": float(l["前值"].values[0]) if pd.notna(l["前值"].values[0]) else None,
            "date": l["日期"].values[0],
        }
    except Exception as e:
        result["ppi"] = {"error": str(e)}

    # PMI (月度更新) — macro_china_pmi是旧接口数据过期，改用财新PMI接口兜底
    try:
        df = ak.macro_china_pmi()
        l = df.tail(1)
        month = str(l["月份"].values[0])
        pmi_val = float(l["制造业-指数"].values[0])
        # 旧接口数据停留在2008年，视为无效，尝试财新PMI
        if "2008" in month or "2009" in month:
            try:
                df2 = ak.macro_china_cx_pmi_yearly()
                l2 = _latest_valid(df2)
                pmi_val = float(l2["今值"].values[0]) if pd.notna(l2["今值"].values[0]) else None
                month = str(l2["日期"].values[0])
            except Exception:
                pmi_val = None
                month = "数据过期"
        result["pmi"] = {
            "manufacturing": pmi_val,
            "month": month,
        }
    except Exception as e:
        result["pmi"] = {"error": str(e)}

    # M2 (月度更新)
    try:
        df = ak.macro_china_m2_yearly()
        l = _latest_valid(df)
        result["m2"] = {
            "value": float(l["今值"].values[0]) if pd.notna(l["今值"].values[0]) else None,
            "previous": float(l["前值"].values[0]) if pd.notna(l["前值"].values[0]) else None,
            "date": l["日期"].values[0],
        }
    except Exception as e:
        result["m2"] = {"error": str(e)}
    
    # LPR
    try:
        df = ak.macro_china_lpr()
        l = df.tail(1)
        result["lpr"] = {
            "lpr1y": float(l["LPR1Y"].values[0]),
            "lpr5y": float(l["LPR5Y"].values[0]),
            "date": l["TRADE_DATE"].values[0],
        }
    except Exception as e:
        result["lpr"] = {"error": str(e)}
    
    # Shibor (日更新)
    try:
        df = ak.macro_china_shibor_all()
        l = df.tail(1)
        result["shibor_on"] = {
            "value": float(l["O/N-定价"].values[0]),
            "change": float(l["O/N-涨跌幅"].values[0]),
            "date": l["日期"].values[0],
        }
    except Exception as e:
        result["shibor_on"] = {"error": str(e)}
    
    return result


def assess_regime(macro: dict) -> dict:
    """
    根据宏观数据判定市场环境等级
    
    评分维度 (各10分, 总分60):
    1. GDP趋势: >=5% → 加分, <4.5% → 减分
    2. CPI: 1-3%正常, <0%通缩减分, >5%通胀减分
    3. PPI: >0 企业利润改善, <0 通缩压力
    4. PMI: >50扩张, <50收缩
    5. M2: >8%宽货币加分, <6%紧货币减分
    6. Shibor: 低→流动性充裕, 高→流动性紧张
    """
    
    score = 30  # 中性基准分
    signals = []
    
    # 1. GDP (权重10)
    gdp = macro.get("gdp", {})
    if gdp.get("value") is not None:
        v = gdp["value"]
        if v >= 5.5:
            score += 5
            signals.append("GDP强增长(+5)")
        elif v >= 5.0:
            score += 2
            signals.append("GDP温和增长(+2)")
        elif v >= 4.5:
            score += 0
            signals.append("GDP增速放缓(0)")
        else:
            score -= 5
            signals.append(f"GDP偏弱({v}%, -5)")
    else:
        signals.append("GDP数据待更新")
    
    # 2. CPI (权重10)
    cpi = macro.get("cpi", {})
    if cpi.get("value") is not None:
        v = cpi["value"]
        if v < 0:
            score -= 5
            signals.append(f"通缩CPI={v}%(-5)")
        elif v < 1:
            score -= 2
            signals.append(f"低通胀CPI={v}%(-2)")
        elif 1 <= v <= 3:
            score += 3
            signals.append(f"温和通胀CPI={v}%(+3)")
        elif v > 5:
            score -= 5
            signals.append(f"高通胀CPI={v}%(-5)")
        else:
            score -= 2
            signals.append(f"偏高CPI={v}%(-2)")
    else:
        signals.append("CPI数据待更新")
    
    # 3. PPI (权重10)
    ppi = macro.get("ppi", {})
    if ppi.get("value") is not None:
        v = ppi["value"]
        if v > 0:
            score += 3
            signals.append(f"PPI转正({v}%, +3)")
        elif v > -2:
            score += 0
            signals.append(f"PPI温和通缩({v}%, 0)")
        else:
            score -= 5
            signals.append(f"PPI深度通缩({v}%, -5)")
    else:
        signals.append("PPI数据待更新")
    
    # 4. PMI (权重10)
    pmi = macro.get("pmi", {})
    if pmi.get("manufacturing") is not None:
        v = pmi["manufacturing"]
        if v > 52:
            score += 5
            signals.append(f"PMI强扩张({v}, +5)")
        elif v > 50:
            score += 2
            signals.append(f"PMI扩张({v}, +2)")
        elif v > 48:
            score += 0
            signals.append(f"PMI临界({v}, 0)")
        else:
            score -= 5
            signals.append(f"PMI收缩({v}, -5)")
    
    # 5. M2 (权重10)
    m2 = macro.get("m2", {})
    if m2.get("value") is not None:
        v = m2["value"]
        if v > 10:
            score += 5
            signals.append(f"M2高增长({v}%, +5)")
        elif v > 8:
            score += 2
            signals.append(f"M2稳健({v}%, +2)")
        elif v > 6:
            score += 0
            signals.append(f"M2偏低({v}%, 0)")
        else:
            score -= 3
            signals.append(f"M2紧缩({v}%, -3)")
    else:
        signals.append("M2数据待更新")
    
    # 6. Shibor (权重10)
    shibor = macro.get("shibor_on", {})
    if shibor.get("value") is not None:
        v = shibor["value"]
        if v < 1.2:
            score += 3
            signals.append(f"流动性宽裕(Shibor={v}%, +3)")
        elif v < 1.8:
            score += 1
            signals.append(f"流动性正常(Shibor={v}%, +1)")
        elif v < 2.5:
            score -= 2
            signals.append(f"流动性偏紧(Shibor={v}%, -2)")
        else:
            score -= 5
            signals.append(f"流动性紧张(Shibor={v}%, -5)")
    
    # 判定等级
    if score >= 50:
        regime = "BULLISH"
        label = "🟢 激进 · 强复苏"
        position_advice = "满仓(100%)"
        style_advice = "成长进攻 · 科技/小盘"
    elif score >= 40:
        regime = "CAUTIOUS"
        label = "🟡 谨慎 · 温和复苏"
        position_advice = "正常仓位(80%)"
        style_advice = "均衡 · 成长+价值"
    elif score >= 30:
        regime = "DEFENSIVE"
        label = "🟠 防御 · 弱复苏/通缩"
        position_advice = "半仓(50%)"
        style_advice = "防御 · 红利/价值/公用事业"
    else:
        regime = "RISK_OFF"
        label = "🔴 避险 · 衰退/危机"
        position_advice = "轻仓(≤30%)或空仓"
        style_advice = "现金为王 · 债券/黄金"

    return {
        "regime": regime,
        "label": label,
        "score": score,
        "max_score": 60,
        "position_advice": position_advice,
        "style_advice": style_advice,
        "signals": signals,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def main():
    # 获取宏观数据
    macro = get_macro_data()
    
    # 评估环境
    result = assess_regime(macro)
    
    # 组装完整输出
    output = {
        "宏观环境等级": result["label"],
        "得分": f"{result['score']}/{result['max_score']}",
        "仓位建议": result["position_advice"],
        "风格建议": result["style_advice"],
        "判定信号": result["signals"],
        "宏观原始数据": {k: v for k, v in macro.items() if "error" not in v},
        "timestamp": result["timestamp"],
    }
    
    # 输出格式
    if "--json" in sys.argv:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    elif "--cron" in sys.argv:
        # 单行日志用（2026-08-20改为脚本自写文件,不再依赖cron会话手动追加）
        line = (f"[{result['timestamp']}] REGIME={result['regime']} "
                f"SCORE={result['score']}/60 仓位={result['position_advice']} 风格={result['style_advice']}")
        print(line)
        try:
            logp = r"D:\zcodesj\reports\macro_regime_log.txt"
            with open(logp, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    else:
        # 人类可读
        print("=" * 60)
        print(f"  宏观环境闸门判定报告")
        print(f"  时间: {result['timestamp']}")
        print("=" * 60)
        print(f"\n  📊 环境等级: {result['label']}")
        print(f"  📈 综合得分: {result['score']}/{result['max_score']}")
        print(f"\n  💰 仓位建议: {result['position_advice']}")
        print(f"  🎯 风格建议: {result['style_advice']}")
        print(f"\n  🔍 判定依据:")
        for s in result["signals"]:
            print(f"     • {s}")
        print(f"\n  📋 宏观数据:")
        for k, v in macro.items():
            if "error" in v:
                print(f"     {k}: {v['error']}")
            else:
                vals = ", ".join(f"{kk}={vv}" for kk, vv in v.items())
                print(f"     {k}: {vals}")
        print()


if __name__ == "__main__":
    main()