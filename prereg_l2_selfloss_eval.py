# -*- coding: utf-8 -*-
"""PREREG_L2_SELFLOSS_FILTER_20260913 评估器 (指纹 f15b28105280d594)
只读重放 bp_ep_core_trades_full.csv, 交易层反事实近似 (prereg 冻结判据, 零自由度):
规则: 月度选股日 T 剔除「该股截至T已平仓自身交易中大亏(≤-5%)笔数>=K」的候选
三窗: in_sample 2016-2021 / validation 2022-2024 / holdout 2025-2026.09 (只测一次)
判据: validation+holdout 合并, 剔除笔反事实合计盈亏<0 且 大亏笔数降>=15% 且 两窗方向一致
首轮即终局; FAIL->falsified_rules L2-SELFLOSS-FILTER; PASS->仅提请引擎二次验证
"""
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(r"D:\zcodesj")
TRADES = BASE / "reports" / "drift_check_20260912e_0911end" / "bp_ep_core_trades_full.csv"
PREREG = BASE / "data" / "prereg_l2_selfloss_filter_20260913.json"
OUT = BASE / "reports" / "PREREG_L2_SELFLOSS_RESULT_20260913.md"
FALSIFIED = BASE / "data" / "falsified_rules.json"

BIG = -5.0
WINDOWS = [("in_sample", "2016-01-01", "2021-12-31"),
           ("validation", "2022-01-01", "2024-12-31"),
           ("holdout", "2025-01-01", "2026-09-11")]


def load():
    with open(TRADES, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["pct"] = float(r["盈亏比例%"])
        r["amt"] = float(r["盈亏金额"])
    rows.sort(key=lambda r: (r["买入日期"], r["股票代码"]))
    return rows


def eval_k(rows, K):
    # PIT 事件流: 买入笔按买入日序处理; 计数器只含「卖出日严格早于当前选股日」的已实现大亏(≤-5%)
    realized = defaultdict(int)
    buys = sorted(rows, key=lambda r: (r["买入日期"], r["股票代码"]))
    sells = sorted(rows, key=lambda r: (r["卖出日期"], r["股票代码"]))
    events = []
    si = 0
    for r in buys:
        d = r["买入日期"]
        while si < len(sells) and sells[si]["卖出日期"] < d:  # 严格早于选股日=已实现
            if sells[si]["pct"] <= BIG:
                realized[sells[si]["股票代码"]] += 1
            si += 1
        snap = realized.get(r["股票代码"], 0)
        excluded = snap >= K
        win = next((w for w, a, b in WINDOWS if a <= d <= b), None)
        events.append({"win": win, "month": d[:7], "code": r["股票代码"], "name": r["股票名称"],
                       "pct": r["pct"], "amt": r["amt"], "excluded": excluded, "big": r["pct"] <= BIG})
    return events


def window_stats(events, wname):
    g = [e for e in events if e["win"] == wname]
    if not g:
        return None
    ex = [e for e in g if e["excluded"]]
    keep = [e for e in g if not e["excluded"]]
    big_all = sum(1 for e in g if e["big"])
    big_ex = sum(1 for e in ex if e["big"])
    # 反事实月度差: 组内 avg(keep)-avg(all), 等权近似
    grp = defaultdict(list)
    for e in g:
        grp[e["month"]].append(e)
    deltas = []
    for m, es in sorted(grp.items()):
        if all(e["excluded"] for e in es):  # 整组被剔=空仓月, 跳过(如实)
            continue
        avg_all = sum(e["pct"] for e in es) / len(es)
        ks = [e for e in es if not e["excluded"]]
        avg_keep = sum(e["pct"] for e in ks) / len(ks)
        deltas.append(avg_keep - avg_all)
    ex_amt = sum(e["amt"] for e in ex)
    return {
        "n": len(g), "n_ex": len(ex), "ex_ratio": len(ex) / len(g) if g else 0,
        "avg_ex": sum(e["pct"] for e in ex) / len(ex) if ex else None,
        "avg_keep": sum(e["pct"] for e in keep) / len(keep) if keep else None,
        "ex_amt": ex_amt,
        "big_all": big_all, "big_ex": big_ex,
        "big_cut": big_ex / big_all if big_all else 0,
        "cf_total_pp": sum(deltas), "cf_months": len(deltas),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    K_main = prereg["rule_frozen"]["K_main"]
    rows = load()
    L = []
    A = L.append
    A("# PREREG_L2_SELFLOSS_FILTER_20260913 评估结果")
    A("")
    A(f"> 生成: {datetime.now():%Y-%m-%d %H:%M} | 指纹: {prereg['fingerprint']} | 数据: bp_ep_core_trades_full.csv {len(rows)} 笔 (2016-01~2026-09-11)")
    A(f"> 规则(冻结): 月度选股剔除「截至选股日该股已平仓自身交易中大亏(≤-5%)笔数>=K」| PIT: 卖出日严格早于选股日才计数")
    A("> 反事实口径: 交易层等权月组近似 (prereg 冻结条款); 零生产接线")
    A("")
    verdicts = {}
    for K in [K_main] + prereg["rule_frozen"]["K_sensitivity"]:
        events = eval_k(rows, K)
        A(f"## K={K}")
        A("")
        A("| 窗 | 总笔 | 剔除笔(占比) | 剔除笔均盈亏 | 保留笔均盈亏 | 剔除笔合计金额 | 大亏笔:剔/全(降幅) | 反事实累计pp(月数) |")
        A("|---|---|---|---|---|---|---|---|")
        for wname, _, _ in WINDOWS:
            s = window_stats(events, wname)
            if not s:
                continue
            verdicts[(K, wname)] = s
            avg_ex = f"{s['avg_ex']:+.2f}%" if s["avg_ex"] is not None else "N/A"
            A(f"| {wname} | {s['n']} | {s['n_ex']} ({s['ex_ratio']*100:.0f}%) | {avg_ex} | {s['avg_keep']:+.2f}% | {s['ex_amt']:+,.0f}元 | {s['big_ex']}/{s['big_all']} ({s['big_cut']*100:.0f}%) | {s['cf_total_pp']:+.1f} ({s['cf_months']}) |")
        A("")
    # 主判据 K=2: validation+holdout 合并
    s_v = verdicts.get((K_main, "validation"))
    s_h = verdicts.get((K_main, "holdout"))
    A("## 主判据判定 (K=2, validation+holdout 合并)")
    A("")
    if s_v and s_h:
        cond_amt = (s_v["ex_amt"] + s_h["ex_amt"]) < 0
        cond_cut = ((s_v["big_ex"] + s_h["big_ex"]) / max(1, s_v["big_all"] + s_h["big_all"])) >= 0.15
        cond_dir = (s_v["ex_amt"] < 0) == (s_h["ex_amt"] < 0)
        passed = cond_amt and cond_cut and cond_dir
        A(f"- 剔除笔合计(validation+holdout): {s_v['ex_amt'] + s_h['ex_amt']:+,.0f} 元 (要求<0: {'✅' if cond_amt else '❌'})")
        A(f"- 大亏笔数降幅: {(s_v['big_ex']+s_h['big_ex'])}/{s_v['big_all']+s_h['big_all']} = {(s_v['big_ex']+s_h['big_ex'])/max(1,s_v['big_all']+s_h['big_all'])*100:.0f}% (要求>=15%: {'✅' if cond_cut else '❌'})")
        A(f"- 两窗方向一致: {'✅' if cond_dir else '❌'} (v={s_v['ex_amt']:+,.0f}, h={s_h['ex_amt']:+,.0f})")
        A("")
        verdict_txt = "PASS_CANDIDATE (仅提请引擎 additive 账户级二次验证, 不构成放行)" if passed else "FAIL (首轮即终局, 登记 falsified_rules L2-SELFLOSS-FILTER)"
        A(f"**判定: {verdict_txt}**")
        A("")
        out_txt = "\n".join(L) + "\n\n## 风险提示\n\n回测历史理论绩效不代表未来实盘收益; 反事实为交易层近似口径, 与账户级重放存在仓位/约束差异。\n"
        OUT.write_text(out_txt, encoding="utf-8")
        print(f"OK -> {OUT}")
        print("VERDICT:", "PASS_CANDIDATE" if passed else "FAIL")
        if not passed:
            reg = {"rule": "L2-SELFLOSS-FILTER", "date": "2026-09-13",
                   "prereg": "PREREG_L2_SELFLOSS_FILTER_20260913",
                   "fingerprint": prereg["fingerprint"],
                   "summary": f"K={K_main} 剔除笔合计(v+h)={s_v['ex_amt']+s_h['ex_amt']:+,.0f}元, 大亏降幅={(s_v['big_ex']+s_h['big_ex'])/max(1,s_v['big_all']+s_h['big_all'])*100:.0f}%, 两窗方向一致={cond_dir}",
                   "verdict": "FAIL", "scope": "bp_ep月度选股自身大亏史PIT剔除; 首轮即终局"}
            data = json.loads(FALSIFIED.read_text(encoding="utf-8"))
            rules = data.setdefault("rules", data)
            rules["L2-SELFLOSS-FILTER"] = reg
            FALSIFIED.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print("falsified_rules 登记: L2-SELFLOSS-FILTER")
    else:
        A("窗口数据不足, 如实标注不判定")


if __name__ == "__main__":
    main()
