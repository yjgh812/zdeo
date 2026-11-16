#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mainline_exposure_lag.py — 主线识别→组合暴露时滞重放器（M3，2026-09-29）。

补自省报告 M3：度量「C1 主线达成日 → 组合首笔暴露日」时滞（复盘实证 9/17 汽车零部件
零暴露=识别滞后）。观测层研究件，零交易权。

口径:
  - 主线锚 = data/industry_volume_state_daily.jsonl 的 C1(volr20>=1.3 ∧ hot5>=4) 首达日。
    ⚠️ 台账自 20260914 起(7日), 更早 C1 状态不可回放=时滞上限截断, 如实标注。
  - 暴露锚 = trade_records BUY × fundamental_data.industry_map(申万一级) 首笔交易日。
  - 时滞 = 暴露首日 - C1首达日(交易日); 负值=暴露先于C1(潜伏, 合法)。
  - 附注: 归因库 trade_date 存在双格式(9/29 发现 62/511 行带横线), 一律归一后比对。

用法: python mainline_exposure_lag.py [--month 202609]
输出: reports/mainline_exposure_lag_<month>.md/.json
⚠️ 口径: C1 台账=申万一级(industry_volume_state), trade_records 归属=通达信行业
   (industry_map src='stock_basic')——精确匹配外另设 CHAIN_HINT 链内近似口径(人工可复核),
   首次运行 202609 实证: 按精确口径房地产/建材零暴露, 但链下游路桥(浦东建设sh600284)已暴露。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime

ROOT = r"D:\zcodesj"
ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db")
FUND_DB = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
META_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro"

# 链内近似口径: C1 申万一级行业 → 通达信链下游行业集(人工可复核, 非精确行业学映射)
CHAIN_HINT = {
    "房地产": {"路桥", "装修装饰", "基础建设", "建筑工程", "园区开发", "全国地产",
               "房地产服务", "房产服务", "水泥", "玻璃", "其他建材", "民用爆破"},
    "建筑材料": {"水泥", "玻璃", "其他建材", "装修装饰", "路桥", "基础建设", "耐火材料"},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="202609")
    a = ap.parse_args()
    start = a.month + "01"

    rows = [json.loads(l) for l in open(
        os.path.join(ROOT, "data", "industry_volume_state_daily.jsonl"), encoding="utf-8")
        if l.strip()]
    c1_first = {}
    for r in rows:
        d = str(r.get("date"))
        for ind in r.get("c1_industries") or []:
            c1_first.setdefault(ind, min(d, c1_first.get(ind, "99999999")))
    ledger_range = (str(rows[0].get("date")), str(rows[-1].get("date"))) if rows else ("-", "-")

    mcon = sqlite3.connect(META_DB, uri=True)
    cal = [str(r[0]) for r in mcon.execute(
        "SELECT DISTINCT date FROM stock_daily_meta WHERE date<=? ORDER BY date", (ledger_range[1],))]
    idx = {d: i for i, d in enumerate(cal)}
    mcon.close()

    ind_map = {}
    fcon = sqlite3.connect(FUND_DB, uri=True)
    # ⚠️ industry_map 混两种语义行: src='SW2021'=行业节点行(industry_name=映射目标),
    #    src='stock_basic'=个股行(industry_name=个股所属申万一级)——只取后者
    for ts, ind in fcon.execute(
            "SELECT ts_code, industry_name FROM industry_map WHERE src='stock_basic'"):
        ind_map[str(ts).split('.')[0][-6:]] = ind
    fcon.close()

    acon = sqlite3.connect(ATTR_DB)
    buys = acon.execute(
        "SELECT code, name, trade_date FROM trade_records "
        "WHERE action='BUY' AND trade_date>=? AND trade_date<?",
        (start, start[:6] + "40")).fetchall()
    acon.close()

    expo_first, industry_dist = {}, {}
    for code, name, td in buys:
        c6, tdn = str(code)[-6:], str(td).replace("-", "")
        ind = ind_map.get(c6, "未映射")
        industry_dist[ind] = industry_dist.get(ind, 0) + 1
        if ind not in expo_first or tdn < expo_first[ind][0]:
            expo_first[ind] = (tdn, code, name)

    items, na = [], []
    for ind, c1d in sorted(c1_first.items(), key=lambda x: x[1]):
        e = expo_first.get(ind)
        chain_e = None
        if not e:
            # 链内近似口径: 首笔 CHAIN_HINT 行业暴露
            cands = [(tdn, code, name, indm) for indm, (tdn, code, name)
                     in expo_first.items() if indm in CHAIN_HINT.get(ind, set())]
            if cands:
                chain_e = min(cands)
        i0 = idx.get(c1d)
        lag = chain_lag = None
        if e and i0 is not None and idx.get(e[0]) is not None:
            lag = idx[e[0]] - i0
        if chain_e and i0 is not None and idx.get(chain_e[0]) is not None:
            chain_lag = idx[chain_e[0]] - i0
        if lag is not None:
            note = (f"滞后{lag}交易日(申万精确)" if lag >= 0 else "暴露先于C1=潜伏(合法)")
        elif chain_lag is not None:
            note = f"精确口径零暴露; 链内近似: 滞后{chain_lag}交易日({chain_e[3]})"
        elif e:
            note = "日期不在台账范围(时滞不可算)"
        else:
            note = "C1后组合零暴露(含链内近似口径)"
        items.append({"industry": ind, "c1_first": c1d,
                      "expo_first": e[0] if e else None,
                      "expo_stock": f"{e[1]} {e[2]}" if e else None,
                      "lag_trade_days": lag,
                      "chain_expo": (f"{chain_e[0]} {chain_e[1]} {chain_e[2]} ({chain_e[3]})"
                                     if chain_e else None),
                      "chain_lag": chain_lag, "note": note})
    for ind, n in sorted(industry_dist.items(), key=lambda x: -x[1]):
        if ind not in c1_first:
            na.append((ind, n))

    lines = [f"# 主线识别→组合暴露时滞重放 {a.month}", "",
             f"- C1 台账范围: {ledger_range[0]}~{ledger_range[1]}（仅 {len(rows)} 日——"
             "早于此的 C1 状态不可回放, 时滞为下限估计）",
             f"- C1 主线: {len(c1_first)} 个 | 9月BUY {len(buys)} 笔 | 行业分布 Top: "
             f"{', '.join(f'{k}{v}' for k, v in sorted(industry_dist.items(), key=lambda x: -x[1])[:6])}", "",
             "| 行业 | C1首达日 | 组合首暴露 | 首暴露标的 | 时滞(交易日) | 判读 |", "|---|---|---|---|---|---|"]
    for it in items:
        lines.append(f"| {it['industry']} | {it['c1_first']} | {it['expo_first'] or '—'} "
                     f"| {it['expo_stock'] or it.get('chain_expo') or '—'} "
                     f"| {it['lag_trade_days'] if it['lag_trade_days'] is not None else it.get('chain_lag') if it.get('chain_lag') is not None else '—'} "
                     f"| {it['note']} |")
    lines += ["", f"## 非 C1 行业暴露分布（逆风/中性暴露，{len(na)} 行业）", ""]
    lines += [f"- {ind}: {n} 笔" for ind, n in na]
    out = {"month": a.month, "generated": datetime.now().isoformat(timespec="seconds"),
           "ledger_range": ledger_range, "c1_first": c1_first,
           "items": items, "non_c1_exposure": dict(na)}
    with open(os.path.join(ROOT, "reports", f"mainline_exposure_lag_{a.month}.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(os.path.join(ROOT, "reports", f"mainline_exposure_lag_{a.month}.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[LAG-EXPO] C1={len(c1_first)} BUY={len(buys)} -> reports/mainline_exposure_lag_{a.month}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
