#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_regime_query.py — 画像状态层·单股/全市场涨跌震荡时间线查询（2026-11-19）
================================================================================
数据源: data/universe_portrait_regime.db（由 analysis/portrait_regime_build.py 构建）。
边界: 纯描述性历史状态标注, 零预测零交易权, 禁作选股公式（详见库内 meta）。

用法:
  python -I analysis/portrait_regime_query.py --code 600519        # 单股完整时间线
  python -I analysis/portrait_regime_query.py --summary            # 全市场汇总
  python -I analysis/portrait_regime_query.py --state UP --min-days 60  # 当前上涨≥60日清单
  python -I analysis/portrait_regime_query.py --code 600519 --from 20240101  # 截取区间
  python -I analysis/portrait_regime_query.py --date 20240924      # 历史切片:该日全市场状态分布
  python -I analysis/portrait_regime_query.py --codes 600519,000001  # 批量:一组代码的当前状态表
"""
from __future__ import annotations

import json
import sqlite3
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB = r"D:\zcodesj\data\universe_portrait_regime.db"
SNAP = r"D:\zcodesj\data\portrait_regime_latest.json"
CN = {"UP": "上涨", "DOWN": "下跌", "RANGE": "震荡"}


def show_code(con, code: str, date_from=None):
    row = con.execute(
        "SELECT name, industry, first_date, last_date, n_days, n_segs, "
        "up_pct, down_pct, range_pct, cur_state, cur_start, cur_days, "
        "cur_ret_pct, cur_confirmed FROM regime_summary WHERE code=?",
        (code,)).fetchone()
    if not row:
        print(f"{code}: 库中无此代码（regime 库覆盖=qfq 库全部有效股票）")
        return 1
    (name, ind, fd, ld, nd, ns, up, dn, rg, cs, cst, cdy, cret, cconf) = row
    print(f"== {code} {name or '?'} ({ind or '-'}) 数据 {fd}-{ld} 共{nd}交易日 ==")
    print(f"历史三态占比: 上涨 {up}% | 下跌 {dn}% | 震荡 {rg}% （共{ns}段）")
    if cs:
        print(f"当前状态: {CN[cs]} 自 {cst} 已 {cdy} 交易日 ({cret:+.2f}%) "
              f"{'确认' if cconf else '暂定(未确认,随未来数据修正)'}")
    q = ("SELECT state, start_date, end_date, n_days, ret_pct, is_confirmed "
         "FROM regime_segments WHERE code=?")
    args = [code]
    if date_from:
        q += " AND end_date >= ?"
        args.append(int(date_from))
    segs = con.execute(q + " ORDER BY start_date", args).fetchall()
    if not segs:
        print("（区间内无段）")
        return 0
    print(f"时间线（{len(segs)}段）:")
    for st, sd, ed, dn_, ret, conf in segs:
        print(f"  {sd}-{ed} {CN[st]:>2} {dn_:>4}日 {ret:+9.2f}% "
              f"{'确认' if conf else '暂定'}")
    return 0


def show_summary(con):
    with open(SNAP, encoding="utf-8") as f:
        snap = json.load(f)
    print(f"=== 全市场状态画像 asof={snap['asof']} "
          f"（构建 {snap['built_at']}）===")
    print(f"覆盖 {snap['n_codes']} 只股票 / {snap['n_segments']} 段")
    print(f"参数: 反转确认阈值 {snap['params']['TAU']:.0%} / "
          f"趋势段幅度阈值 {snap['params']['T_TREND']:.0%}（zigzag 波段划分）")
    ds = snap["days_share"]
    print(f"全历史天数占比: 上涨 {ds['UP']}% | 下跌 {ds['DOWN']}% | "
          f"震荡 {ds['RANGE']}%")
    cd = snap["cur_dist"]
    tot = cd["UP"] + cd["DOWN"] + cd["RANGE"]
    print(f"当前状态分布: 上涨 {cd['UP']}（{cd['UP']/tot:.1%}） | "
          f"下跌 {cd['DOWN']}（{cd['DOWN']/tot:.1%}） | "
          f"震荡 {cd['RANGE']}（{cd['RANGE']/tot:.1%}）")
    for key, title in (("top_cur_up", "当前持续上涨 Top20（按持续天数）"),
                       ("top_cur_down", "当前持续下跌 Top20（按持续天数）")):
        print(f"\n{title}:")
        for r in snap[key]:
            print(f"  {r['code']} {r['name'] or '?'} ({r['industry'] or '-'}) "
                  f"自{r['since']} 已{r['days']}日 {r['ret_pct']:+.1f}%")
    print(f"\n边界: {snap['boundary_decl']}")
    return 0


def show_state(con, state: str, min_days: int):
    if state not in CN:
        print(f"非法状态 {state}（UP/DOWN/RANGE）")
        return 1
    rows = con.execute(
        "SELECT code, name, industry, cur_start, cur_days, cur_ret_pct, cur_confirmed "
        "FROM regime_summary WHERE cur_state=? AND cur_days>=? "
        "ORDER BY cur_days DESC", (state, min_days)).fetchall()
    print(f"当前{CN[state]}且已持续≥{min_days}交易日: {len(rows)} 只")
    for r in rows[:80]:
        print(f"  {r[0]} {r[1] or '?'} ({r[2] or '-'}) 自{r[3]} "
              f"已{r[4]}日 {r[5]:+.1f}% {'确认' if r[6] else '暂定'}")
    if len(rows) > 80:
        print(f"  ...（共 {len(rows)} 只, 仅显示前80）")
    return 0


def show_date(con, date: int):
    """历史切片: 某交易日全市场三态分布 + 行业聚合。"""
    row = con.execute("SELECT value FROM meta WHERE key='asof'").fetchone()
    if row and date > int(row[0]):
        print(f"{date}: 晚于库内最新日期 {row[0]}（数据未到，不做推演）")
        return 1
    segs = con.execute(
        "SELECT s.state, COUNT(DISTINCT s.code) n "
        "FROM regime_segments s WHERE s.start_date<=? AND s.end_date>=? "
        "GROUP BY s.state", (date, date)).fetchall()
    dist = {st: n for st, n in segs}
    tot = sum(dist.values())
    if not tot:
        print(f"{date}: 该日无覆盖（早于库内最早数据或非交易日区间）")
        return 0
    print(f"=== {date} 全市场状态切片（{tot} 只有状态） ===")
    print("  （事后完整划分视角：该段在当日未必已被确认，描述历史事实非当时可知）")
    for st in ("UP", "DOWN", "RANGE"):
        n = dist.get(st, 0)
        print(f"  {CN[st]}: {n} 只（{n/tot:.1%}）")
    # 行业聚合（普涨/普跌行业 Top）
    rows = con.execute(
        "SELECT m.industry, s.state, COUNT(*) n FROM regime_segments s "
        "JOIN regime_summary m ON m.code=s.code "
        "WHERE s.start_date<=? AND s.end_date>=? AND m.industry IS NOT NULL "
        "GROUP BY m.industry, s.state", (date, date)).fetchall()
    ind = {}
    for i_name, st, n in rows:
        d = ind.setdefault(i_name, {"UP": 0, "DOWN": 0, "RANGE": 0})
        d[st] = n
    ranked = sorted(ind.items(), key=lambda kv: -(kv[1]["UP"] / max(1, sum(kv[1].values()))))
    print("  普涨行业 Top5（行业内上涨段占比）: " + ", ".join(
        f"{k}({v['UP']}/{sum(v.values())})" for k, v in ranked[:5]))
    ranked_dn = sorted(ind.items(), key=lambda kv: -(kv[1]["DOWN"] / max(1, sum(kv[1].values()))))
    print("  普跌行业 Top5: " + ", ".join(
        f"{k}({v['DOWN']}/{sum(v.values())})" for k, v in ranked_dn[:5]))
    return 0


def show_codes(con, codes: list):
    """批量: 一组代码的当前状态紧凑表（供持仓透视等组合查询, 纯描述）。"""
    ph = ",".join("?" * len(codes))
    rows = con.execute(
        f"SELECT code, name, industry, cur_state, cur_start, cur_days, cur_ret_pct, "
        f"cur_confirmed, up_pct, down_pct, range_pct FROM regime_summary "
        f"WHERE code IN ({ph}) ORDER BY code", codes).fetchall()
    miss = set(codes) - {r[0] for r in rows}
    dist = {"UP": 0, "DOWN": 0, "RANGE": 0, None: 0}
    for r in rows:
        dist[r[3]] = dist.get(r[3], 0) + 1
    print(f"=== 批量状态透视（{len(codes)} 只, 命中 {len(rows)}） ===")
    print(f"上涨 {dist.get('UP', 0)} | 下跌 {dist.get('DOWN', 0)} | "
          f"震荡 {dist.get('RANGE', 0)} | 无数据 {dist.get(None, 0) + len(miss)}")
    for r in sorted(rows, key=lambda x: ({"DOWN": 0, "RANGE": 1, "UP": 2}.get(x[3], 3),
                                          -(x[5] or 0))):
        st = CN.get(r[3], r[3] or "?")
        print(f"  {r[0]} {r[1] or '?':<6} ({(r[2] or '-')[:4]}) {st:>2} "
              f"自{r[4]} 已{r[5]}日 {r[6] if r[6] is not None else 0:+7.1f}% "
              f"{'确认' if r[7] else '暂定'} | 历史占比 涨{r[8]}/跌{r[9]}/震{r[10]}")
    if miss:
        print(f"  （无数据: {','.join(sorted(miss))}）")
    return 0


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 0
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rc = 0
    if "--code" in argv:
        code = argv[argv.index("--code") + 1].strip()
        date_from = argv[argv.index("--from") + 1] if "--from" in argv else None
        rc = show_code(con, code, date_from)
    elif "--codes" in argv:
        codes = [c.strip().split(".")[-1][-6:].zfill(6)
                 for c in argv[argv.index("--codes") + 1].split(",") if c.strip()]
        codes = sorted(set(codes))
        rc = show_codes(con, codes) if codes else 1
    elif "--date" in argv:
        rc = show_date(con, int(argv[argv.index("--date") + 1]))
    elif "--summary" in argv:
        rc = show_summary(con)
    elif "--state" in argv:
        state = argv[argv.index("--state") + 1].upper()
        min_days = int(argv[argv.index("--min-days") + 1]) if "--min-days" in argv else 0
        rc = show_state(con, state, min_days)
    else:
        print(__doc__)
    con.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
