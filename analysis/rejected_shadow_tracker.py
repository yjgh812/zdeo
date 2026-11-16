# -*- coding: utf-8 -*-
"""rejected_shadow_tracker.py — 被否决票影子跟踪器（零交易权）

回答主人方法论问题「连通60分钟线/K线/闸门过滤能否提高胜率」的唯一途径=模拟盘同批对照：
  臂A(bought)   = W23 计划中通过全部闸门的18只（周一09:23实买）
  臂B(rejected) = W23 计划中被闸门否决的18只（R39落刀/R31偏离/R1b熔断/R7涨停追入/归因层）
  同基counterfactual: 两臂入场基点=信号日(20261029)收盘冻结价；窗口=T+1..T+20交易日(自20261102)
  判决口径: T+20 各臂胜率(收益>0占比)与均值之差。若A显著优于B=过滤提高胜率(实证)；
  若无差=过滤层无用(降复杂度)。样本18v18一批统计力有限, 需2-3批累积——如实声明。

数据源: vipdoc .day 原始日线(tdx_reader, 收盘后客户端落盘, 15:45可取当日)——不用qfq(避免链时滞)。
价格口径: 原始不复权价, 20日窗内除权影响两臂同型, 比较自洽。
幂等: 每交易日一行, 重跑覆盖同日。

用法:
  python analysis/rejected_shadow_tracker.py --build-from-dryrun <dryrun结果json> <计划json>
  python analysis/rejected_shadow_tracker.py --daily          # 每日盯市(幂等)
  python analysis/rejected_shadow_tracker.py --report         # 当前对照快照
"""
import argparse
import json
import re
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
from tdx_reader import read_day, read_min5  # noqa: F401,E402

STATE = r"D:\zcodesj\data\rejected_shadow_state_W23.json"
TRACK = r"D:\zcodesj\data\rejected_shadow_track_W23.jsonl"
ANCHOR = 20261029          # 信号日(冻结价基点)
WINDOW = 20                # 交易日窗口


def _read_json(p):
    return json.load(open(p, encoding="utf-8"))


def build_from_dryrun(dryrun_json, plan_json):
    """从dry-run结果+计划构建三臂状态(bought/rejected/全36的60min标注)。"""
    txt = open(dryrun_json, encoding="utf-8", errors="replace").read()
    out = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    plan = {p["code"]: p for p in _read_json(plan_json)}
    bought, rejected = [], []
    for r in out["results"]:
        code = r.get("code", "")
        p = plan.get(code, {})
        entry = {
            "code": code, "name": r.get("name", ""),
            "entry_price": p.get("price"),          # 信号日收盘冻结价
            "verdict": r.get("gate_verdict"),
        }
        if r.get("gate_verdict") == "REJECT":
            entry["reject_reason"] = (r.get("gate_rejects") or ["?"])[0][:120]
            rejected.append(entry)
        else:
            bought.append(entry)
    state = {
        "batch": "W23_T20261029",
        "anchor": ANCHOR,
        "window": WINDOW,
        "built": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "counterfactual_note": "两臂同基=信号日收盘冻结价; 窗口自20261102起T+20交易日; "
                               "臂A=闸门通过(周一实买), 臂B=闸门否决(影子)",
        "bought": bought,
        "rejected": rejected,
    }
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[build] {state['batch']}: bought={len(bought)} rejected={len(rejected)} → {STATE}")
    return 0


def _arm_rows(state, arm):
    return state.get(arm, [])


def _trading_days_of_window(n=WINDOW + 2):
    """窗口内已发生的交易日(自20261102起, 用000001深市日线日历; 未来日不存在=窗口未开始)。"""
    days = read_day("sz", "000001")
    return [r[0] for r in days if r[0] >= 20261102][:n]


def daily():
    """每日盯市: 两臂逐票收盘价→臂级T+k累计收益; 每日一行追加(幂等覆盖)。"""
    state = _read_json(STATE)
    cal = _trading_days_of_window()
    if not cal:
        print("[daily] 窗口未开始(首个交易日=20261102周一), 等待开盘后自动积累")
        return 0
    all_codes = [e["code"] for e in _arm_rows(state, "bought") + _arm_rows(state, "rejected")]
    closes = {}
    for code in all_codes:
        mkt = "sh" if code.startswith(("6", "9", "5")) else "sz"
        rows = read_day(mkt, code)
        closes[code] = {r[0]: r[4] for r in rows}
    rows_out = []
    for i, d in enumerate(cal, 1):
        arms = {}
        for arm in ("bought", "rejected"):
            rets = []
            for e in _arm_rows(state, arm):
                px = closes.get(e["code"], {}).get(d)
                if px and e["entry_price"]:
                    rets.append(px / e["entry_price"] - 1)
            rets = [r for r in rets if r is not None]
            if rets:
                arms[arm] = {
                    "n": len(rets),
                    "mean": round(sum(rets) / len(rets) * 100, 2),
                    "median": round(sorted(rets)[len(rets) // 2] * 100, 2),
                    "winrate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                }
        row = {"t": i, "date": d, "arms": arms,
               "generated": datetime.now().strftime("%Y-%m-%d %H:%M")}
        rows_out.append(row)
    # 幂等写(同t覆盖)
    hist = {}
    try:
        for line in open(TRACK, encoding="utf-8"):
            r = json.loads(line)
            hist[r["t"]] = r
    except FileNotFoundError:
        pass
    for r in rows_out:
        hist[r["t"]] = r
    rows_sorted = [hist[k] for k in sorted(hist)]
    with open(TRACK, "w", encoding="utf-8") as f:
        for r in rows_sorted:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    latest = rows_out[-1]
    print(f"[daily] 最新行 T+{latest['t']} @{latest['date']}:")
    for arm, v in latest["arms"].items():
        print(f"  {arm}: n={v['n']} 均值{v['mean']:+.2f}% 胜率{v['winrate']}%")
    if "bought" in latest["arms"] and "rejected" in latest["arms"]:
        diff = latest["arms"]["bought"]["mean"] - latest["arms"]["rejected"]["mean"]
        print(f"  过滤效应差(买-拒): {diff:+.2f}pp")
    return 0


def report():
    """当前对照快照(全窗口)。"""
    try:
        rows = [json.loads(l) for l in open(TRACK, encoding="utf-8")]
    except FileNotFoundError:
        print("[report] 无盯市数据")
        return 2
    print(f"=== W23 过滤效应影子对照 | 锚={ANCHOR} 窗=T+{WINDOW} ===")
    print(f"{'T+':>3} {'日期':>8} {'买入臂均值':>9} {'否决臂均值':>9} {'效应差':>7} {'买胜率':>6} {'拒胜率':>6}")
    for r in rows:
        b = r["arms"].get("bought", {})
        j = r["arms"].get("rejected", {})
        diff = (b.get("mean", 0) - j.get("mean", 0)) if b and j else None
        print(f"T+{r['t']:<2} {r['date']:>8} "
              f"{('%+.2f%%' % b['mean']) if b else '-':>9} "
              f"{('%+.2f%%' % j['mean']) if j else '-':>9} "
              f"{('%+.2fpp' % diff) if diff is not None else '-':>7} "
              f"{str(b.get('winrate', '-')):>6} {str(j.get('winrate', '-')):>6}")
    last = rows[-1]
    b, j = last["arms"].get("bought", {}), last["arms"].get("rejected", {})
    if b and j and last["t"] >= WINDOW:
        verdict = ("过滤有效(买入臂优)" if b["mean"] > j["mean"] and b["winrate"] > j["winrate"]
                   else "过滤无效/反向(否决臂不差)" if j["mean"] >= b["mean"]
                   else "混合(均值优但胜率未优)")
        print(f"\nT+{WINDOW} 判决: {verdict} | 买{b['mean']:+.2f}%/胜率{b['winrate']}% vs "
              f"拒{j['mean']:+.2f}%/胜率{j['winrate']}%")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-from-dryrun", nargs=2, metavar=("DRYRUN_JSON", "PLAN_JSON"))
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.build_from_dryrun:
        return build_from_dryrun(*a.build_from_dryrun)
    if a.daily:
        return daily()
    if a.report:
        return report()
    print("用法: --build-from-dryrun / --daily / --report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
