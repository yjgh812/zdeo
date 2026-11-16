# -*- coding: utf-8 -*-
"""
主线量化阈值判定器（m4风控闸门官 · 屠龙反臆想体系 L4 场景1）
================================================================
背景：主线识别是最容易主观臆断的环节。本闸门将主线判定彻底量化：
  - 只允许用三组客观数据（板块涨停数 / 连续资金净流入天数 / 5日板块涨幅）做阈值判定
  - 不达标 → 强制输出「当前市场无清晰量化主线，不强行指定主线」
  - 单日脉冲板块（今日涨停>=5但5日累计涨幅<0）不算主线
  - fail-closed：三组数据任一缺失 → 直接 NO_CLEAR_MAINLINE（宁可空判，不可臆断）

量化阈值（全部硬编码常量，禁止运行时凭感觉调整）：
  一级主线：涨停数>=5只 且 连续资金净流入>=3日 且 5日板块涨幅>3%
  次主线  ：涨停数3-4只，或 满足其余两条（资金>=3日 且 5日涨幅>3%）
  不达标  ：其余一切情形 → 默认语句，不强行指定

评分（借鉴 main_theme_calc.py 的RS分档思想，纯客观三因子加权）：
  score = 0.4*涨停档 + 0.3*资金档 + 0.3*涨幅档   （区间0~90，只作排序参考，不参与达标判定）

输入格式（--data 指向JSON）：
{
  "date": "2026-08-17",
  "meta": {"来源说明": "..."},
  "sectors": [
    {"name":"算力", "limit_up_count":11, "net_inflow_days":5, "pct_5d":5.2},
    ...
  ]
}
兼容中文键名：涨停数 / 连续资金净流入天数|资金净流入天数 / 5日涨幅|五日涨幅。

输出（stdout，JSON）：
{
  "verdict": "HAS_MAINLINE" | "NO_CLEAR_MAINLINE",
  "default_statement": "当前市场无清晰量化主线，不强行指定主线",   # 仅NO_CLEAR_MAINLINE时出现
  "mainlines": [{"name","level","score","evidence":{"涨停数","资金流天数","5日涨幅"}}],
  "single_day_pulse": [...],   # 被脉冲过滤掉的板块
  "fail_closed": true/false,
  "reason": "...",
  ...
}
用法：python mainline_quant_gate.py --data mainline_input.json [--pretty]
退出码：0=判定完成（含NO_CLEAR_MAINLINE）；1=输入文件级错误（同样fail-closed输出JSON）
"""

import argparse
import json
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

GATE_NAME = "mainline_quant_gate"
GATE_VERSION = "1.0"

# ---------------- 硬性量化阈值（唯一判定依据，禁止主观调整） ----------------
TH_MAIN_LIMIT_UP = 5      # 一级主线：板块涨停数 >= 5 只
TH_SUB_LIMIT_UP = (3, 4)  # 次主线：涨停数 3-4 只
TH_INFLOW_DAYS = 3        # 连续资金净流入 >= 3 日
TH_PCT_5D = 3.0           # 5日板块涨幅 > 3%（严格大于）
TH_PULSE_LIMIT_UP = 5     # 单日脉冲：今日涨停 >= 5
TH_PULSE_PCT_5D = 0.0     # 单日脉冲：5日累计涨幅 < 0

DEFAULT_STATEMENT = "当前市场无清晰量化主线，不强行指定主线"

# 三组客观数据的字段名（英文规范名 + 中文兼容别名）
FIELD_ALIASES = {
    "limit_up_count": ["limit_up_count", "limit_up", "涨停数", "今日涨停数", "首板涨停数"],
    "net_inflow_days": ["net_inflow_days", "inflow_days", "连续资金净流入天数", "资金净流入天数", "资金流天数"],
    "pct_5d": ["pct_5d", "pct5d", "gain_5d", "5日涨幅", "五日涨幅", "板块5日涨幅"],
}
NAME_ALIASES = ["name", "板块名", "板块", "sector"]

THRESHOLDS_DESC = {
    "一级主线": f"涨停数>={TH_MAIN_LIMIT_UP} 且 资金净流入>={TH_INFLOW_DAYS}日 且 5日涨幅>{TH_PCT_5D}%",
    "次主线": f"涨停数{TH_SUB_LIMIT_UP[0]}-{TH_SUB_LIMIT_UP[1]}只，或 资金净流入>={TH_INFLOW_DAYS}日 且 5日涨幅>{TH_PCT_5D}%",
    "单日脉冲": f"今日涨停>={TH_PULSE_LIMIT_UP} 且 5日累计涨幅<{TH_PULSE_PCT_5D}% → 不算主线",
}


def bucket_limit_up(n: float) -> int:
    """涨停数分档（只影响score排序，不影响达标判定）"""
    if n >= 8: return 90
    if n >= 5: return 70
    if n >= 3: return 40
    if n >= 1: return 10
    return 0


def bucket_inflow(d: float) -> int:
    """连续资金净流入天数分档"""
    if d >= 5: return 90
    if d >= 3: return 70
    if d >= 1: return 40
    return 0


def bucket_pct5(p: float) -> int:
    """5日涨幅分档（<=0 直接0档，单日脉冲自然落入）"""
    if p > 8: return 90
    if p > TH_PCT_5D: return 70
    if p > 0: return 40
    return 0


def calc_score(limit_up: float, inflow_days: float, pct_5d: float) -> int:
    """三因子加权评分：0.4*涨停档 + 0.3*资金档 + 0.3*涨幅档"""
    return round(0.4 * bucket_limit_up(limit_up)
                 + 0.3 * bucket_inflow(inflow_days)
                 + 0.3 * bucket_pct5(pct_5d))


def extract_sector_fields(sector: dict):
    """
    从板块字典中提取三组客观数据。
    返回 (name, limit_up, inflow_days, pct_5d, missing_list)
    missing_list 非空 = 数据缺失/非法 → fail-closed。
    """
    missing = []
    name = None
    for key in NAME_ALIASES:
        if key in sector and sector[key]:
            name = str(sector[key])
            break
    if not name:
        missing.append("name(板块名)")

    values = {}
    for canon, aliases in FIELD_ALIASES.items():
        val = None
        for key in aliases:
            if key in sector and sector[key] is not None:
                val = sector[key]
                break
        if val is None or not isinstance(val, (int, float)) or isinstance(val, bool):
            missing.append(canon)
        else:
            values[canon] = float(val)

    return (name,
            values.get("limit_up_count"),
            values.get("net_inflow_days"),
            values.get("pct_5d"),
            missing)


def classify(name, limit_up, inflow_days, pct_5d):
    """
    单板块量化阈值判定（纯函数，无主观输入）。
    返回 (level, score, evidence, note)
      level ∈ {"一级主线","次主线","不达标","单日脉冲"}
    """
    score = calc_score(limit_up, inflow_days, pct_5d)
    evidence = {"涨停数": limit_up, "资金流天数": inflow_days, "5日涨幅": pct_5d}
    note = ""

    # 规则0：单日脉冲过滤（优先级最高——即便资金面达标，5日累计涨幅<0说明只是当日脉冲）
    if limit_up >= TH_PULSE_LIMIT_UP and pct_5d < TH_PULSE_PCT_5D:
        return ("单日脉冲", score, evidence,
                f"今日涨停{limit_up:.0f}>= {TH_PULSE_LIMIT_UP} 但5日累计涨幅{pct_5d}%<0，单日脉冲不算主线")

    # 规则1：一级主线（三条件严格同时满足）
    if (limit_up >= TH_MAIN_LIMIT_UP
            and inflow_days >= TH_INFLOW_DAYS
            and pct_5d > TH_PCT_5D):
        return ("一级主线", score, evidence,
                f"涨停{limit_up:.0f}>={TH_MAIN_LIMIT_UP} 且 资金净流入{inflow_days:.0f}日>={TH_INFLOW_DAYS} 且 5日涨幅{pct_5d}%>{TH_PCT_5D}%")

    # 规则2：次主线（涨停3-4只，或 满足其余两条：资金>=3日 且 涨幅>3%）
    sub_by_count = TH_SUB_LIMIT_UP[0] <= limit_up <= TH_SUB_LIMIT_UP[1]
    sub_by_other_two = (inflow_days >= TH_INFLOW_DAYS and pct_5d > TH_PCT_5D)
    if sub_by_count or sub_by_other_two:
        why = []
        if sub_by_count:
            why.append(f"涨停{limit_up:.0f}只∈{TH_SUB_LIMIT_UP}")
        if sub_by_other_two:
            why.append(f"满足其余两条(资金{inflow_days:.0f}日>={TH_INFLOW_DAYS} 且 涨幅{pct_5d}%>{TH_PCT_5D}%)")
        return ("次主线", score, evidence, "；".join(why))

    # 规则3：不达标 → 默认语句
    fails = []
    if limit_up < TH_SUB_LIMIT_UP[0]:
        fails.append(f"涨停{limit_up:.0f}<{TH_SUB_LIMIT_UP[0]}")
    if inflow_days < TH_INFLOW_DAYS:
        fails.append(f"资金净流入{inflow_days:.0f}日<{TH_INFLOW_DAYS}")
    if pct_5d <= TH_PCT_5D:
        fails.append(f"5日涨幅{pct_5d}%≤{TH_PCT_5D}%")
    return ("不达标", score, evidence, "未过阈值：" + "，".join(fails))


def run_gate(payload: dict) -> dict:
    """主判定流程。任何数据问题一律 fail-closed → NO_CLEAR_MAINLINE。"""
    result = {
        "gate": GATE_NAME,
        "version": GATE_VERSION,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "input_date": payload.get("date", ""),
        "thresholds": THRESHOLDS_DESC,
        "fail_closed": False,
        "reason": "",
    }
    sectors = payload.get("sectors")
    if not isinstance(sectors, list) or len(sectors) == 0:
        result.update({
            "verdict": "NO_CLEAR_MAINLINE",
            "default_statement": DEFAULT_STATEMENT,
            "mainlines": [],
            "single_day_pulse": [],
            "rejected": [],
            "fail_closed": True,
            "reason": "输入数据包无sectors列表或为空——数据缺失，fail-closed",
        })
        return result

    mainlines, pulses, rejected = [], [], []
    missing_report = []

    for i, sector in enumerate(sectors):
        if not isinstance(sector, dict):
            missing_report.append(f"sectors[{i}]非对象")
            continue
        name, lu, inflow, p5d, missing = extract_sector_fields(sector)
        if missing:
            # fail-closed 核心：三组客观数据任一缺失 → 该板块不可判定，整体不输出主线
            missing_report.append(f"{name or sector.get('name', f'sectors[{i}]')}缺失字段:{','.join(missing)}")
            continue

        level, score, evidence, note = classify(name, lu, inflow, p5d)
        item = {"name": name, "level": level, "score": score, "evidence": evidence, "note": note}
        if level == "一级主线" or level == "次主线":
            mainlines.append(item)
        elif level == "单日脉冲":
            pulses.append(item)
        else:
            rejected.append(item)

    # 任一候选板块三组数据不完整 → 整体fail-closed，禁止输出任何主线
    if missing_report:
        result.update({
            "verdict": "NO_CLEAR_MAINLINE",
            "default_statement": DEFAULT_STATEMENT,
            "mainlines": [],
            "single_day_pulse": [p["name"] for p in pulses],
            "rejected": rejected,
            "fail_closed": True,
            "reason": "三组客观数据(涨停数/资金净流入/5日涨幅)任一缺失→fail-closed：" + "；".join(missing_report),
        })
        return result

    mainlines.sort(key=lambda x: -x["score"])
    result["single_day_pulse"] = pulses
    result["rejected"] = rejected

    if mainlines:
        result["verdict"] = "HAS_MAINLINE"
        result["mainlines"] = mainlines
        result["reason"] = f"共{len(mainlines)}个板块通过量化阈值（一级{sum(1 for m in mainlines if m['level']=='一级主线')}/次{sum(1 for m in mainlines if m['level']=='次主线')}），证据见evidence字段"
    else:
        result["verdict"] = "NO_CLEAR_MAINLINE"
        result["mainlines"] = []
        result["default_statement"] = DEFAULT_STATEMENT
        result["reason"] = "全部候选板块未通过量化阈值（含单日脉冲过滤），执行强制默认值"
    return result


def emit(obj: dict, pretty: bool, exit_code: int) -> None:
    if pretty:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(obj, ensure_ascii=False))
    sys.exit(exit_code)


def main():
    ap = argparse.ArgumentParser(description="主线量化阈值判定器（反臆想L4场景1）")
    ap.add_argument("--data", required=True, help="输入数据包JSON路径（板块涨停数/资金净流入/5日涨幅三组客观数据）")
    ap.add_argument("--pretty", action="store_true", help="美化输出JSON")
    args = ap.parse_args()

    try:
        with open(args.data, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        emit({"gate": GATE_NAME, "version": GATE_VERSION,
              "verdict": "NO_CLEAR_MAINLINE", "default_statement": DEFAULT_STATEMENT,
              "mainlines": [], "single_day_pulse": [], "fail_closed": True,
              "reason": f"输入文件不存在:{args.data}——数据缺失，fail-closed"},
             args.pretty, 1)
    except json.JSONDecodeError as e:
        emit({"gate": GATE_NAME, "version": GATE_VERSION,
              "verdict": "NO_CLEAR_MAINLINE", "default_statement": DEFAULT_STATEMENT,
              "mainlines": [], "single_day_pulse": [], "fail_closed": True,
              "reason": f"输入JSON解析失败:{e}——数据缺失，fail-closed"},
             args.pretty, 1)

    emit(run_gate(payload), args.pretty, 0)


if __name__ == "__main__":
    main()
