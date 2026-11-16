#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""theme_roles_scanner.py — 主线五角色标注器 v1（纯观测层，零交易权）

背景（2026-10-06 主人令，B站量化学习九轮沉淀）：主线五角色词汇层
（龙头/中军/助攻/卡位/补涨）此前屠龙未建——本件为其观测层实现。
Spec 来源：reports/fourphase_september_trajectory_20261006.md §四；
截面验证：reports/theme_roles_fourphase_demo_20261006.md（9/30 手工标注对照）。

数据源（分层，全部只读）：
  1) --input data/theme_roles_input_YYYYMMDD.json  必需。
     hithink 涨停池 items（交互层 MCP 抓取落盘，字段: ticker/name/continue_day_cnt/
     limit_up_reason/seal_money/limit_up_time/is_st/last_price）——题材原因唯一合法来源
     （铁律：申万行业≠题材概念）。
  2) 本地 daily_basic（fund 库）——市值（中军判据）。

五角色判据（LOCKED v1，同 spec）：
  龙头 = 链内连板数最大
  中军 = 链内总市值最大且非龙头
  助攻 = 同链跟板（连板数 < 龙头）
  卡位 = 跨日判据（原链内最高板断板后新板接管）——v1 需要前一日 input，缺省输出 N/A
  补涨 = 龙头连板≥5 时的链内首板

题材聚类 v1：reason 按「+/、/，」切标签词 → 倒排索引 → 词重叠连通分量。
已知局限（如实）：跨题材词（如出版链「拟收购界面财联社」与「AI漫剧」无共享词）连不上，
输出中该类链会分裂——chain_override 参数允许人工并链；v2 方向=概念成分表映射。

输出：
  data/theme_roles_YYYYMMDD.json   机读结果
  reports/theme_roles_YYYYMMDD.md  人读版（含口径与局限声明）
退出码: 0 正常 / 2 输入缺失或格式错
铁律: 本件零交易权——禁止下单/禁止接闸门/禁止接 cron；标注为描述层非买入触发器。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
FUND_DB = r"file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
# 万金油停用词: 无题材区分度, 入倒排会造成假连通(9/30实测「业绩增长」把创新药/种业/纸业连成一条)
STOPWORDS = {"业绩增长", "业绩预增", "中报扭亏", "龙头客户", "股份回购", "回购提议", "股东增持",
             "订单回暖", "国企背景", "国资背景", "诉讼进展", "复牌", "全国化", "海外拓展",
             "海外产能", "项目量产", "营收增长", "年轻化"}


def load_input(path):
    if not os.path.exists(path):
        print(f"[FAIL] 输入不存在: {path}（交互层 hithink 涨停池落盘后重试）", file=sys.stderr)
        return None
    data = json.loads(open(path, encoding="utf-8").read())
    items = data.get("item") if isinstance(data, dict) else data
    if not items:
        print("[FAIL] 输入为空", file=sys.stderr)
        return None
    return items


def load_mv(date_int):
    """市值(万元) -> {code6: total_mv}"""
    out = {}
    try:
        con = sqlite3.connect(FUND_DB, uri=True)
        for ts, mv in con.execute(
                "SELECT ts_code, total_mv FROM daily_basic WHERE trade_date=?", (str(date_int),)):
            out[ts[:6]] = mv
        con.close()
    except Exception as e:
        print(f"[WARN] daily_basic 市值不可用: {e}")
    return out


def split_tags(reason):
    return [w.strip() for w in re.split(r"[+、，,;；/]", reason or "")
            if len(w.strip()) >= 2 and w.strip() not in STOPWORDS]


def cluster(items):
    """标签词倒排连通分量。返回 [{codes:set, words:Counter}]"""
    inv = defaultdict(set)
    tags_of = {}
    for it in items:
        c = it["ticker"]
        tags = split_tags(it.get("limit_up_reason", ""))
        tags_of[c] = tags
        for w in tags:
            inv[w].add(c)
    # 并查集
    parent = {it["ticker"]: it["ticker"] for it in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for w, codes in inv.items():
        codes = list(codes)
        for a, b in zip(codes, codes[1:]):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
    chains = defaultdict(list)
    for it in items:
        chains[find(it["ticker"])].append(it)
    result = []
    for members in chains.values():
        wc = Counter()
        for it in members:
            wc.update(tags_of[it["ticker"]])
        result.append({"members": members, "shared_words": [w for w, _ in wc.most_common(6)]})
    result.sort(key=lambda ch: (-max(m["continue_day_cnt"] for m in ch["members"]),
                                -sum(m.get("seal_money") or 0 for m in ch["members"])))
    return result


def annotate_chain(chain, mv_map):
    ms = sorted(chain["members"], key=lambda m: (-m["continue_day_cnt"], -(m.get("seal_money") or 0)))
    leader = ms[0]
    roles = {"龙头": [], "中军": [], "助攻": [], "卡位": [], "补涨": []}
    roles["龙头"].append(leader)
    non_leader = ms[1:]
    mid = None
    if non_leader:
        # 中军=非龙头中市值最大且有市值数据者; 市值不可用时该票照常归助攻(不悬空)
        mid = max(non_leader, key=lambda m: mv_map.get(m["ticker"], 0) or 0)
        if (mv_map.get(mid["ticker"]) or 0) > 0:
            roles["中军"].append(mid)
    for m in non_leader:
        if m is mid and m in roles["中军"]:
            continue
        if leader["continue_day_cnt"] >= 5 and m["continue_day_cnt"] == 1:
            roles["补涨"].append(m)
        else:
            roles["助攻"].append(m)
    return leader, roles


def main():
    ap = argparse.ArgumentParser(description="主线五角色标注器 v1（观测层零交易权）")
    ap.add_argument("--date", required=True, help="YYYYMMDD")
    ap.add_argument("--input", default=None, help="hithink 涨停池 JSON 路径（默认 data/theme_roles_input_<date>.json）")
    ap.add_argument("--prev-leader", default=None, help="[弃用 v1.1] 单一前日最高板——链级卡位误报, 改用 --prev-input")
    ap.add_argument("--prev-input", default=None, help="前一日 input JSON 路径——链级卡位判据: 同链(按chain-map名或代码重叠)前日最高板≠今日龙头且今日更高→卡位")
    ap.add_argument("--chain-map", default=None, help="人工并链JSON路径 {链名:[code6,...]}——词聚类跨词链分裂的人工修正层")
    args = ap.parse_args()

    inp = args.input or os.path.join(ROOT, "data", f"theme_roles_input_{args.date}.json")
    items = load_input(inp)
    if not items:
        return 2
    items = [it for it in items if not it.get("is_st")]

    # 人工并链层: 词聚类分裂的跨词链由人工 map 指定(如 出版AI漫剧链=[600825,605577,605287])
    chain_map = {}
    if args.chain_map and os.path.exists(args.chain_map):
        chain_map = json.loads(open(args.chain_map, encoding="utf-8").read())

    by_code = {it["ticker"]: it for it in items}
    consumed = set()
    mapped_chains = []
    for cname, codes in (chain_map or {}).items():
        ms = [by_code[c] for c in codes if c in by_code]
        for c in codes:
            consumed.add(c)
        if len(ms) >= 2 or (ms and ms[0]["continue_day_cnt"] >= 2):
            mapped_chains.append({"name": cname, "members": ms,
                                  "shared_words": [cname + "(人工并链)"]})

    mv = load_mv(args.date)
    chains = cluster(items)

    # 链级卡位判据 v1.1（修正 v1.0 单一 --prev-leader 全链误报）:
    # 前日同链最高板（按 chain-map 成员跨两日解析, 允许前日成员今日已不在池=断板）
    # 今日缺席 且 今日链内出现≥2板新龙头 → 卡位(接管链内高度)。
    prev_map = None
    if args.prev_input and os.path.exists(args.prev_input):
        prev_items = load_input(args.prev_input) or []
        prev_map = {it["ticker"]: it for it in prev_items if not it.get("is_st")}

    def chain_takeover(cname, codes, ms, leader):
        if prev_map is None:
            return False
        prev_in_chain = [prev_map[c] for c in codes if c in prev_map]
        if not prev_in_chain:
            return False
        prev_top = max(prev_in_chain, key=lambda it: it["continue_day_cnt"])
        # 前日链内最高板今日缺席(断板) 且 前日它有高度(≥2板) 且 今日新龙头≥2板
        if (prev_top["ticker"] not in by_code and prev_top["continue_day_cnt"] >= 2
                and leader["continue_day_cnt"] >= 2
                and leader["ticker"] != prev_top["ticker"]):
            return True
        return False

    rest = [ch for ch in chains
            if not any(m["ticker"] in consumed for m in ch["members"])]
    all_chains = [{"name": f"自动链{i+1}", **ch} for i, ch in enumerate(rest)] + \
                 [{"name": k["name"], **k} for k in mapped_chains]
    all_chains.sort(key=lambda ch: (-max(m["continue_day_cnt"] for m in ch["members"]),
                                    -sum(m.get("seal_money") or 0 for m in ch["members"])))

    out = {"date": args.date, "total_limitup": len(items), "chains": [], "known_limits":
           ["题材聚类=标签词连通分量+人工并链层(chain_map);卡位=--prev-input链级对比(前日链最高板断板+新龙头≥2板接管);ST已剔除"]}
    md = [f"# 主线五角色标注 {args.date}（观测层零交易权）\n",
          f"> 涨停 {len(items)} 只（剔ST）→ 有效链 {len(all_chains)} 条（自动聚类+人工并链）| 数据源: hithink涨停池(input)+daily_basic市值(wb库)",
          "> 已知局限: 词聚类跨词链会分裂→--chain-map 人工并链;卡位判据=--prev-input 链级对比\n"]
    for ci, ch in enumerate(all_chains, 1):
        ms = ch["members"]
        top = max(ms, key=lambda m: m["continue_day_cnt"])
        if top["continue_day_cnt"] < 2 and len(ms) < 2:
            continue  # 单只首板无链结构,不入角色表
        cname = ch.get("name") or (",".join(ch["shared_words"][:3]))
        # chain-map 原始成员表(跨两日解析卡位), 自动链无 map 则用当日成员
        map_codes = None
        if chain_map:
            for k, codes in chain_map.items():
                if k == cname:
                    map_codes = codes
                    break
        codes_for_chain = map_codes or [m["ticker"] for m in ms]
        leader, roles = annotate_chain(ch, mv)
        takeover = chain_takeover(cname, codes_for_chain, ms, leader)
        if takeover:
            roles["卡位"] = [leader]
        rec = {"chain_id": ci, "chain_name": cname, "shared_words": ch.get("shared_words", []),
               "takeover": takeover,
               "leader": {"code": leader["ticker"], "name": leader["name"],
                          "boards": leader["continue_day_cnt"],
                          "seal_money": leader.get("seal_money"),
                          "mv_wan": mv.get(leader["ticker"])},
               "members": [{"code": m["ticker"], "name": m["name"], "boards": m["continue_day_cnt"],
                            "reason": m.get("limit_up_reason", ""), "mv_wan": mv.get(m["ticker"]),
                            "role": next((r for r, lst in roles.items() if m in lst), "助攻")}
                           for m in ms]}
        out["chains"].append(rec)
        md.append(f"## 链{ci} {cname} ({len(ms)}只)")
        md.append(f"- **龙头**: {leader['name']}({leader['ticker']}) {leader['continue_day_cnt']}板 "
                  f"封单{(leader.get('seal_money') or 0)/1e8:.1f}亿 市值{(mv.get(leader['ticker']) or 0)/1e4:.0f}亿")
        for r in ("中军", "助攻", "补涨", "卡位"):
            lst = [m for m in ms if m in roles[r]]
            if r == "卡位":
                lst = [m for m in lst if m is not leader] or roles[r]  # 卡位即接管链内高度的龙头本体
            elif leader in lst:
                lst = [m for m in lst if m is not leader]
            if lst:
                md.append(f"- {r}: " + "; ".join(f"{m['name']}({m['ticker']}){m['continue_day_cnt']}板" for m in lst))
        md.append("")

    jout = os.path.join(ROOT, "data", f"theme_roles_{args.date}.json")
    json.dump(out, open(jout, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    mout = os.path.join(ROOT, "reports", f"theme_roles_{args.date}.md")
    open(mout, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"[DONE] {len(items)}只 -> {len(out['chains'])}链 | {jout}")
    print(f"[DONE] 人读版 {mout}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
