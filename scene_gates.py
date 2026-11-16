# -*- coding: utf-8 -*-
"""
反臆想体系三场景硬约束闸门（m4风控闸门官 · 屠龙团队 L4 场景2/3/4）
====================================================================
背景：反臆想四大场景中主线识别已代码化（mainline_quant_gate.py），但归因/
回测/回头看三场景此前只有协议文字约束——文字约束靠不住，本文件把它们全部
变成机器强制执行（MUST-pass）的代码闸门。三函数共享同一原则：

  fail-closed：输入缺失/非法 → 一律 REJECTED，宁可误拦不可放行。

场景2 check_attribution —— 归因证据闸门
  每条归因结论必须含 {factor因子, evidence证据来源}；evidence为空或纯描述性
  （不含任何数据源引用）→ 该条强制改写为「短期随机波动，未识别明确驱动因子」。
  输出修订后报告 + 被强制改写条数（verdict: PASS / REVISED / REJECTED）。

场景3 check_backtest_integrity —— 回测完整性闸门
  用原始交易明细重算 收益/净值/交易数，与回测报告核对（±0.1%容差）；
  报告样本数≠明细样本数 → REJECTED「样本被裁剪」；
  报告缺PBO字段 → 警告「未附过拟合检查」（不拒但必须显式暴露）。

场景4 check_hindsight —— 回头看未来视角闸门
  任何 data_used_dates 晚于 sell_date → REJECTED「未来视角违规」并列出违规日期。

CLI 三模式：
  python scene_gates.py --scene attribution --data attribution.json [--pretty]
  python scene_gates.py --scene backtest --data result.json --trades trades.json
  python scene_gates.py --scene hindsight --data hindsight.json [--sell-date 2026-08-10]

输入格式见各函数 docstring。退出码：0=PASS/REVISED/PASS_WITH_WARNINGS；1=REJECTED。
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

GATE_NAME = "scene_gates"
GATE_VERSION = "1.0"

REJECT = "REJECTED"
DEFAULT_ATTRIBUTION = "短期随机波动，未识别明确驱动因子"
TOLERANCE = 0.001  # 回测数值核对容差 ±0.1%

# ---------------- 场景2：归因闸门 ----------------
# 判定evidence「有数据源引用」的特征词：数据源系统/数据表/可核验数据对象。
# 纯描述性文字（"走势强劲""感觉要涨""主力洗盘"）一个都命中不了。
SOURCE_KEYWORDS = [
    "来源", "数据源", "数据", "来源:", "akshare", "tushare", "baostock", "mootdx",
    "同花顺", "问财", "ths", "东方财富", "东财", "eastmoney", "ifind", "wind",
    "龙虎榜", "lhb", "北向", "公告", "财报", "逐笔", "l2", "L2", "盘口",
    "数据库", "db", "sqlite", "csv", "api", "kline", "daily", "quote", "涨跌停",
]
ATTR_LIST_KEYS = ["attributions", "conclusions", "findings", "items", "归因", "结论"]
FACTOR_KEYS = ["factor", "driver", "reason", "因子", "驱动因子", "归因"]
EVIDENCE_KEYS = ["evidence", "evidence_source", "source", "证据", "证据来源"]


def _pick(d: dict, keys: list):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _has_source_reference(text: str) -> bool:
    """evidence是否引用了数据源：含任一特征词才算可核验，否则视为纯描述。"""
    low = str(text).lower()
    return any(kw.lower() in low for kw in SOURCE_KEYWORDS)


def check_attribution(attribution_report: dict) -> dict:
    """
    归因证据闸门。
    输入 attribution_report：{"date":..., "attributions":[{"factor":..., "evidence":...}, ...]}
    输出：{"verdict":"PASS|REVISED|REJECTED", "report":修订后报告,
           "forced_rewrite_count":n, "rewritten":[改写明细], "reason":...}
    """
    out = {"gate": GATE_NAME, "scene": "attribution", "version": GATE_VERSION,
           "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "fail_closed": False}

    # fail-closed：输入缺失
    if not isinstance(attribution_report, dict):
        out.update({"verdict": REJECT, "fail_closed": True, "forced_rewrite_count": 0,
                    "report": None, "rewritten": [],
                    "reason": "输入缺失：attribution_report非dict——fail-closed"})
        return out

    conclusions = _pick(attribution_report, ATTR_LIST_KEYS)
    if not isinstance(conclusions, list) or len(conclusions) == 0:
        out.update({"verdict": REJECT, "fail_closed": True, "forced_rewrite_count": 0,
                    "report": None, "rewritten": [],
                    "reason": "输入缺失：无归因结论列表(attributions)或为空——fail-closed"})
        return out

    revised, rewritten = [], []
    for i, item in enumerate(conclusions):
        if not isinstance(item, dict):
            item = {}
        factor = _pick(item, FACTOR_KEYS)
        evidence = _pick(item, EVIDENCE_KEYS)
        ev_str = str(evidence).strip() if evidence is not None else ""

        if ev_str and _has_source_reference(ev_str):
            revised.append(dict(item))  # 证据可核验，原样保留
            continue

        # 强制改写：evidence为空 或 纯描述性(无数据源引用) 或 因子缺失
        why = ("evidence为空" if not ev_str
               else "证据纯描述性、无数据源引用")
        if factor is None:
            why = "因子缺失；" + why
        new_item = dict(item)
        new_item["factor"] = DEFAULT_ATTRIBUTION
        new_item["evidence"] = (f"[闸门强制改写|原因:{why}|原因子:{factor}|"
                                f"原证据:{ev_str[:80]}] 无可核验数据源，不采信主观归因")
        new_item["rewritten"] = True
        revised.append(new_item)
        rewritten.append({"index": i, "original_factor": factor,
                          "original_evidence": ev_str[:120], "violation": why})

    out["report"] = dict(attribution_report)
    out["report"]["attributions"] = revised
    out["forced_rewrite_count"] = len(rewritten)
    out["rewritten"] = rewritten
    if rewritten:
        out["verdict"] = "REVISED"
        out["reason"] = (f"{len(rewritten)}/{len(conclusions)}条归因无数据源支撑，"
                         f"已强制改写为「{DEFAULT_ATTRIBUTION}」")
    else:
        out["verdict"] = "PASS"
        out["reason"] = f"全部{len(conclusions)}条归因均附可核验数据源引用"
    return out


# ---------------- 场景3：回测完整性闸门 ----------------
PNL_KEYS = ["total_pnl", "total_return", "total_profit", "pnl_total", "总收益", "累计收益", "收益"]
NAV_KEYS = ["nav", "net_value", "final_nav", "净值", "期末净值"]
COUNT_KEYS = ["trade_count", "n_trades", "trades", "交易数", "交易笔数", "笔数"]
SAMPLE_KEYS = ["sample_count", "n_samples", "samples", "样本数", "样本"]
PBO_KEYS = ["pbo", "PBO", "pbo_rate", "overfit_pbo", "过拟合"]
CAPITAL_KEYS = ["initial_capital", "initial", "capital_base", "base_capital", "本金", "起始资金", "初始资金"]
TRADE_PNL_KEYS = ["pnl", "profit", "net_pnl", "盈亏", "收益", "每笔收益"]


def _close_enough(reported, recalc, abs_floor=1.0) -> bool:
    """±0.1%相对容差；重算值接近0时退化为绝对容差abs_floor。"""
    try:
        r, c = float(reported), float(recalc)
    except (TypeError, ValueError):
        return False
    return abs(r - c) <= TOLERANCE * max(abs(c), abs_floor)


def check_backtest_integrity(result: dict, raw_trades: dict) -> dict:
    """
    回测完整性闸门。
    输入 result：回测报告 {"total_pnl":..., "nav":..., "trade_count":..., "sample_count":..., "pbo":...}
    输入 raw_trades：{"initial_capital":1000000, "trades":[{"pnl":...}, ...]}
    输出：{"verdict":"PASS|PASS_WITH_WARNINGS|REJECTED", "checks":{...}, "warnings":[...], "reason":...}
    """
    out = {"gate": GATE_NAME, "scene": "backtest", "version": GATE_VERSION,
           "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "fail_closed": False, "warnings": [], "checks": {}}

    def reject(reason, fail_closed=True):
        out.update({"verdict": REJECT, "fail_closed": fail_closed, "reason": reason})
        return out

    # fail-closed：输入缺失
    if not isinstance(result, dict) or not isinstance(raw_trades, dict):
        return reject("输入缺失：result或raw_trades非dict——fail-closed")

    trades = raw_trades.get("trades")
    if not isinstance(trades, list) or len(trades) == 0:
        return reject("输入缺失：raw_trades无trades明细或为空——fail-closed")

    # 逐笔盈亏必须齐全可算，否则无法重算
    pnls = []
    for i, t in enumerate(trades):
        v = _pick(t, TRADE_PNL_KEYS) if isinstance(t, dict) else None
        try:
            pnls.append(float(v))
        except (TypeError, ValueError):
            return reject(f"输入缺失：trades[{i}]缺pnl字段，无法重算——fail-closed")

    capital = _pick(raw_trades, CAPITAL_KEYS) or _pick(result, CAPITAL_KEYS)
    try:
        capital = float(capital)
        if capital <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return reject("输入缺失：无initial_capital(本金)或非法，无法重算净值——fail-closed")

    rep_pnl = _pick(result, PNL_KEYS)
    rep_nav = _pick(result, NAV_KEYS)
    rep_count = _pick(result, SAMPLE_KEYS, ) if _pick(result, SAMPLE_KEYS) is not None else _pick(result, COUNT_KEYS)
    if rep_pnl is None or rep_nav is None or rep_count is None:
        missing = [k for k, v in (("收益(total_pnl)", rep_pnl), ("净值(nav)", rep_nav),
                                  ("交易数/样本数", rep_count)) if v is None]
        return reject(f"输入缺失：报告缺{','.join(missing)}字段，无法核对——fail-closed")

    # 重算
    recalc_pnl = sum(pnls)
    recalc_nav = (capital + recalc_pnl) / capital
    recalc_count = len(trades)

    # 检查1：样本数（先于数值——样本被裁剪则一切数字都不可信）
    try:
        rep_count_n = int(rep_count)
    except (TypeError, ValueError):
        return reject(f"报告交易数/样本数非法:{rep_count!r}——fail-closed")
    out["checks"]["sample_count"] = {"reported": rep_count_n, "recalculated": recalc_count}
    if rep_count_n != recalc_count:
        return reject(f"样本被裁剪：报告样本数{rep_count_n}笔 ≠ 明细{recalc_count}笔，"
                      f"差额{rep_count_n - recalc_count}笔去向不明", fail_closed=False)

    # 检查2：收益
    ok_pnl = _close_enough(rep_pnl, recalc_pnl)
    out["checks"]["total_pnl"] = {"reported": rep_pnl, "recalculated": round(recalc_pnl, 2),
                                  "tolerance": "±0.1%", "pass": ok_pnl}
    if not ok_pnl:
        return reject(f"收益对不上：报告{rep_pnl} vs 重算{recalc_pnl:.2f}（超±0.1%容差）", fail_closed=False)

    # 检查3：净值
    ok_nav = _close_enough(rep_nav, recalc_nav, abs_floor=1e-6)
    out["checks"]["nav"] = {"reported": rep_nav, "recalculated": round(recalc_nav, 6),
                            "tolerance": "±0.1%", "pass": ok_nav}
    if not ok_nav:
        return reject(f"净值对不上：报告{rep_nav} vs 重算{recalc_nav:.6f}（超±0.1%容差）", fail_closed=False)

    # 检查4：PBO过拟合字段（缺失→警告，不拒但必须显式暴露）
    pbo = _pick(result, PBO_KEYS)
    if pbo is None:
        out["warnings"].append("未附过拟合检查：报告缺PBO字段，回测结论可信度存疑")
        out["checks"]["pbo"] = {"reported": None, "pass": False, "warning": True}
    else:
        out["checks"]["pbo"] = {"reported": pbo, "pass": True}

    out["verdict"] = "PASS_WITH_WARNINGS" if out["warnings"] else "PASS"
    out["reason"] = (f"样本数{recalc_count}笔一致，收益/净值均在±0.1%容差内"
                     + ("；但存在警告：" + "；".join(out["warnings"]) if out["warnings"] else "，且已附PBO"))
    return out


# ---------------- 场景4：回头看未来视角闸门 ----------------
DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]
SELL_DATE_KEYS = ["sell_date", "exit_date", "decision_date", "卖出日期", "卖出日", "决策日期"]
DATA_DATES_KEYS = ["data_used_dates", "dates_used", "used_dates", "data_dates", "数据使用日期", "使用数据日期"]


def _parse_date(s):
    """解析日期，失败返回None。兼容 2026-08-10 / 2026/08/10 / 20260810。"""
    if s is None:
        return None
    t = str(s).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def check_hindsight(sell_date: str, data_used_dates: list) -> dict:
    """
    回头看未来视角闸门：决策（卖出）之后的数据绝不允许出现在决策依据里。
    输入 sell_date：卖出/决策日；data_used_dates：复盘声称使用的数据日期列表。
    输出：{"verdict":"PASS|REJECTED", "violations":[违规日期], "reason":...}
    """
    out = {"gate": GATE_NAME, "scene": "hindsight", "version": GATE_VERSION,
           "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "fail_closed": False, "violations": []}

    def reject(reason, fail_closed=True):
        out.update({"verdict": REJECT, "fail_closed": fail_closed, "reason": reason})
        return out

    # fail-closed：输入缺失
    if sell_date is None or str(sell_date).strip() == "":
        return reject("输入缺失：sell_date为空——fail-closed")
    if not isinstance(data_used_dates, list) or len(data_used_dates) == 0:
        return reject("输入缺失：data_used_dates非列表或为空（复盘必须声明所用数据）——fail-closed")

    sd = _parse_date(sell_date)
    if sd is None:
        return reject(f"输入缺失：sell_date无法解析:{sell_date!r}——fail-closed")

    parsed, bad = [], []
    for d in data_used_dates:
        p = _parse_date(d)
        if p is None:
            bad.append(str(d))
        else:
            parsed.append((str(d), p))
    if bad:
        return reject(f"输入缺失：以下数据日期无法解析:{bad}（无法判定即不可放行）——fail-closed")

    violations = [orig for orig, p in parsed if p > sd]
    out["sell_date"] = str(sell_date)
    out["data_used_dates"] = [str(d) for d in data_used_dates]
    out["violations"] = violations

    if violations:
        return reject(f"未来视角违规：{len(violations)}个数据日期晚于卖出日{sd}：{violations}——"
                      "决策用了决策后才能知道的数据，复盘结论作废", fail_closed=False)

    out["verdict"] = "PASS"
    out["reason"] = f"全部{len(parsed)}个数据日期均不晚于卖出日{sd}，无未来视角违规"
    return out


# ---------------- CLI ----------------
def _reject_file(scene, reason):
    return {"gate": GATE_NAME, "scene": scene, "version": GATE_VERSION,
            "verdict": REJECT, "fail_closed": True, "reason": reason,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def _load_json(path, scene):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, _reject_file(scene, f"输入文件不存在:{path}——fail-closed")
    except json.JSONDecodeError as e:
        return None, _reject_file(scene, f"输入JSON解析失败:{e}——fail-closed")


def main():
    ap = argparse.ArgumentParser(description="反臆想三场景硬约束闸门（归因/回测/回头看）")
    ap.add_argument("--scene", required=True, choices=["attribution", "backtest", "hindsight"],
                    help="场景：attribution=归因证据 / backtest=回测完整性 / hindsight=未来视角")
    ap.add_argument("--data", required=True, help="输入JSON路径（backtest模式=回测报告result.json）")
    ap.add_argument("--trades", help="backtest模式：原始交易明细JSON路径")
    ap.add_argument("--sell-date", dest="sell_date", default=None,
                    help="hindsight模式：卖出/决策日，覆盖JSON内的sell_date")
    ap.add_argument("--pretty", action="store_true", help="美化输出JSON")
    args = ap.parse_args()

    payload, err = _load_json(args.data, args.scene)
    if err is not None:
        result = err
    elif args.scene == "attribution":
        result = check_attribution(payload)
    elif args.scene == "backtest":
        trades_payload, err2 = _load_json(args.trades, args.scene) if args.trades else (None, _reject_file(args.scene, "输入缺失：backtest模式需--trades原始明细JSON——fail-closed"))
        result = err2 if err2 is not None else check_backtest_integrity(payload, trades_payload)
    else:  # hindsight
        if not isinstance(payload, dict):
            result = _reject_file(args.scene, "hindsight输入须为含sell_date与data_used_dates的JSON——fail-closed")
        else:
            sell_date = args.sell_date or _pick(payload, SELL_DATE_KEYS)
            dates = _pick(payload, DATA_DATES_KEYS)
            result = check_hindsight(sell_date, dates)

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    sys.exit(1 if result.get("verdict") == REJECT else 0)


if __name__ == "__main__":
    main()
