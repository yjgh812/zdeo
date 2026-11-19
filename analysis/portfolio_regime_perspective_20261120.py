#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portfolio_regime_perspective_20261120.py — 持仓×画像状态层透视报告（2026-11-20）
================================================================================
主人令(『继续』批准上轮呈报的持仓透视交付项)。纯描述·零交易权。
  持仓真值 = westock MCP 实时快照(2026-11-19 23:52 与 2026-11-20 盘前两次抓取完全一致
  =11-19收盘态, 总市值384089.00/浮盈+8777.44/212只), 落 data/portfolio_westock_20261120.tsv;
  状态真值 = data/universe_portrait_regime.db (asof=20261119)。
输出: reports/portfolio_regime_perspective_20261120.md (+json), 名称经 verify_stock_names 校验。
边界: 只描述状态不构成买卖指令; 卖出决策仍走既有防线(-8%止损/趋势走坏链主人终审)。
用法: python -I analysis/portfolio_regime_perspective_20261120.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TSV = r"D:\zcodesj\data\portfolio_westock_20261120.tsv"
REGIME_DB = r"D:\zcodesj\data\universe_portrait_regime.db"
OUT_MD = r"D:\zcodesj\reports\portfolio_regime_perspective_20261120.md"
OUT_JSON = r"D:\zcodesj\reports\portfolio_regime_perspective_20261120.json"
EXPECT_N = 212
EXPECT_MV = 384089.00
CN = {"UP": "上涨段", "DOWN": "下跌段", "RANGE": "震荡段", None: "无状态"}


def load_tsv():
    pos = {}
    with open(TSV, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            code, mv, pr = line.split("\t")
            pos[code] = (float(mv), float(pr))
    return pos


def main():
    t0 = time.time()
    pos = load_tsv()
    if len(pos) != EXPECT_N:
        print(f"[FAIL-CLOSED] 持仓数 {len(pos)} != {EXPECT_N}, 快照转录有误, 拒绝出报告")
        return 2
    mv_sum = sum(v[0] for v in pos.values())
    if abs(mv_sum - EXPECT_MV) > 1.0:
        print(f"[FAIL-CLOSED] 总市值 {mv_sum:.2f} != {EXPECT_MV:.2f}, 快照转录有误, 拒绝出报告")
        return 2
    print(f"快照校验通过: 212只 总市值 {mv_sum:.2f} 与 westock 一致")

    con = sqlite3.connect(f"file:{REGIME_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT code, name, industry, cur_state, cur_start, cur_days, cur_ret_pct, "
        "cur_confirmed, up_pct, down_pct, range_pct FROM regime_summary").fetchall()
    con.close()
    reg = {r[0]: r[1:] for r in rows}

    dist = {"UP": [], "DOWN": [], "RANGE": [], None: []}
    all_items = []
    for code, (mv, pr) in pos.items():
        r = reg.get(code)
        st = r[2] if r else None
        item = (code, mv, pr, r)
        dist[st].append(item)
        all_items.append(item)
    n_hit = sum(len(v) for k, v in dist.items() if k is not None)
    if n_hit < 200:
        print(f"[FAIL-CLOSED] 状态命中 {n_hit}/212 过低, 状态库或代码对齐异常")
        return 2
    print(f"状态命中 {n_hit}/212 {time.time()-t0:.1f}s")

    def mv_of(items):
        return sum(m for _, m, _, _ in items)

    def wpr(items):
        s = mv_of(items)
        return sum(m * p for _, m, p, _ in items) / s if s else None

    L = []
    L.append("# 持仓×画像状态层透视（2026-11-20, 纯描述·零交易权）")
    L.append("")
    L.append("> 持仓截面=2026-11-19收盘(westock两次抓取一致, 212只/总市值384,089元/浮盈+2.34%); "
             "状态截面=画像状态库asof=20261119。本报告只描述状态, 不构成买卖指令; "
             "卖出决策仍走既有防线(-8%止损/趋势走坏链主人终审)。")
    L.append("")
    up_mv, dn_mv, rg_mv = mv_of(dist["UP"]), mv_of(dist["DOWN"]), mv_of(dist["RANGE"])
    L.append(f"## 一、总览")
    L.append("")
    L.append(f"- 持仓 212 只: **上涨段 {len(dist['UP'])} 只 / 下跌段 {len(dist['DOWN'])} 只 / "
             f"震荡段 {len(dist['RANGE'])} 只** (无状态 {len(dist[None])} 只)")
    L.append(f"- 按市值: 上涨段 {up_mv:,.0f} 元({up_mv/mv_sum:.1%}) | 下跌段 {dn_mv:,.0f} 元({dn_mv/mv_sum:.1%}) | "
             f"震荡段 {rg_mv:,.0f} 元({rg_mv/mv_sum:.1%})")
    L.append(f"- 市值加权浮盈率: 上涨段 {wpr(dist['UP']):+.1f}% | 下跌段 {wpr(dist['DOWN']):+.1f}% | "
             f"震荡段 {wpr(dist['RANGE']):+.1f}%")
    L.append("")
    L.append("## 二、市值集中度 Top15（钱在哪里, 什么状态）")
    L.append("")
    L.append("| 代码 | 名称 | 行业 | 状态 | 自 | 已持续 | 段内幅度 | 持仓浮盈 | 市值(元) |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    top15 = sorted(pos.items(), key=lambda kv: -kv[1][0])[:15]
    for code, (mv, pr) in top15:
        r = reg.get(code)
        if r:
            name, ind, st, cst, cdy, cret, cconf = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
            L.append(f"| {code} | {name or '?'} | {ind or '-'} | {CN.get(st, st)} | {cst} | "
                     f"{cdy} | {cret:+.1f}% | {pr:+.1f}% | {mv:,.0f} |")
        else:
            L.append(f"| {code} | ? | - | 无状态 | - | - | - | {pr:+.1f}% | {mv:,.0f} |")
    L.append("")
    L.append("## 三、下跌段持仓全景（亏损/趋势风险观察区, 描述非指令）")
    L.append("")
    dn_sorted = sorted(dist["DOWN"], key=lambda x: -x[1])
    L.append(f"下跌段共 {len(dn_sorted)} 只, 市值 {dn_mv:,.0f} 元; 市值加权浮盈 {wpr(dist['DOWN']):+.1f}%。"
             "其中『持仓浮盈为正但处于下跌段』= 趋势走坏观察(既有卖出规则域, 主人终审); "
             "『浮盈<0且段龄长』= 深跌未修复观察。Top20 按市值:")
    L.append("")
    L.append("| 代码 | 名称 | 状态起点 | 已持续 | 段内幅度 | 持仓浮盈 | 市值(元) | 观察标签 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for code, mv, pr, r in dn_sorted[:20]:
        name, ind, st, cst, cdy, cret, cconf = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        if pr > 0:
            tag = "浮盈正×趋势走坏"
        elif cdy >= 90:
            tag = "长跌未修复"
        elif (cret or 0) <= -20:
            tag = "深跌中"
        else:
            tag = "下跌段"
        L.append(f"| {code} | {name or '?'} | {cst} | {cdy}日 | {cret:+.1f}% | {pr:+.1f}% | {mv:,.0f} | {tag} |")
    L.append("")
    prof_dn = [(c, m, p, r) for c, m, p, r in dist["DOWN"] if p > 0]
    L.append(f"**浮盈为正但处于下跌段(趋势走坏观察) 共 {len(prof_dn)} 只**: " +
             (", ".join(f"{reg[c][0]}({c}, 浮盈{p:+.1f}%, 下跌段已{reg[c][4]}日{reg[c][5]:+.1f}%)"
                        for c, m, p, r in prof_dn[:30]) if prof_dn else "无"))
    L.append("")
    L.append("## 四、上涨段持仓全景（趋势延续观察区）")
    L.append("")
    L.append("| 代码 | 名称 | 状态起点 | 已持续 | 段内幅度 | 持仓浮盈 | 市值(元) |")
    L.append("|---|---|---|---|---|---|---|")
    for code, mv, pr, r in sorted(dist["UP"], key=lambda x: -x[1]):
        name, ind, st, cst, cdy, cret, cconf = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        L.append(f"| {code} | {name or '?'} | {cst} | {cdy}日 | {cret:+.1f}% | {pr:+.1f}% | {mv:,.0f} |")
    L.append("")
    L.append("## 五、结构弱点（全历史以下跌为主 的持仓）")
    L.append("")
    weak = [(c, m, p, r) for c, m, p, r in all_items
            if reg.get(c) and reg[c][8] is not None and reg[c][8] >= 60]
    L.append("全历史下跌段占比≥60% = 历史上大部分时间在跌的票(结构弱, 反弹通常乏力): " +
             (", ".join(f"{reg[c][0]}({c}, 历史{reg[c][8]:.0f}%时间在下跌段, 浮盈{p:+.1f}%)"
                        for c, m, p, r in weak[:30]) if weak else "无"))
    L.append("")
    L.append("## 六、使用说明")
    L.append("- 「趋势走坏观察」名单只是状态描述: 按既有规则, 卖出需理由(趋势走坏或-8%硬止损)且卖多少须主人拍板, 本报告不替代该决策。")
    L.append("- 状态库每晚23:00日更(夜链第6步); 明日刷新后本报告可用同一脚本重跑。")
    L.append("- 边界: 纯描述零交易权; 名称经 verify_stock_names 校验。")
    out = "\n".join(L)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(out)
    js = dict(built_at=time.strftime("%F %T"), n=len(pos), mv=mv_sum,
              dist={k: dict(n=len(v), mv=mv_of(v), wpr=wpr(v)) for k, v in dist.items()},
              profit_but_down=[c for c, m, p, r in prof_dn],
              structurally_weak=[c for c, m, p, r in weak])
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(js, f, ensure_ascii=False, indent=1)
    print(f"落 {OUT_MD} {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
