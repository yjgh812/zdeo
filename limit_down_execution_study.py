#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""limit_down_execution_study.py — 极端行情卖出可行性研究（L2 首版，2026-09-29）。

自省报告 §五L2：一字跌停卖不出场景的概率测算，为止损预案表提供数据底稿。
研究问题（预注册，防挑报）：
  Q1 触发日不可执行占比：跌停事件中「一字跌停」（开=高=低=收=跌停价, 全天零成交机会）
     与「收盘封死」（收=跌停价, 盘中有过成交机会）的占比。
  Q2 封死次日的执行滑差：次日开盘卖出实际价 / 当日跌停价 - 1 的分布（排队假设）。
  Q3 连续性风险：封死次次日仍跌停的比例（连续跌停天数分布）。
  Q4 分组：涨停次日跌停（天地板/A杀族）vs 非涨停跌停（阴跌族）的 Q1-Q3 差异。
口径: 真实价(stock_daily_meta); 跌停判定 close<=round(prev_close*0.90,2)(主板10%,
创业/科创20%——本宇宙剔科创, 创业按20%); 一字判定 open=high=low=close。
样本: 20260101-20260928 全A(剔688/8/9开头)。零交易权, 产出供预案裁决。

用法: python limit_down_execution_study.py
输出: reports/limit_down_execution_20260929.md
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime

META_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro"
OUT = r"D:\zcodesj\reports\limit_down_execution_20260929.md"


def pct_block(vals: list[float], base: float = 1.0) -> str:
    if not vals:
        return "—"
    s = sorted(vals)
    return (f"n={len(vals)} 均值{sum(vals) / len(vals) * 100:+.2f}% "
            f"中位{s[len(s) // 2] * 100:+.2f}% P10={s[max(0, int(len(s) * 0.1))] * 100:+.2f}% "
            f"P90={s[min(len(s) - 1, int(len(s) * 0.9))] * 100:+.2f}%")


def main() -> int:
    m = sqlite3.connect(META_DB, uri=True)
    rows = m.execute("""
        SELECT code, date, open, high, low, close,
               LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev_close,
               LEAD(open) OVER (PARTITION BY code ORDER BY date) AS next_open,
               LEAD(close) OVER (PARTITION BY code ORDER BY date) AS next_close
        FROM stock_daily_meta
        WHERE date>='20260101' AND substr(code,1,1) IN ('0','3','6')
          AND code NOT LIKE '688%' AND code NOT LIKE '8%' AND code NOT LIKE '9%'
        ORDER BY code, date""").fetchall()
    m.close()

    events = []  # 跌停事件
    for code, date, o, h, l, c, pc, no, nc in rows:
        if not pc or not c or pc <= 0:
            continue
        # 跌停价系数: 主板0.9(-10%) 创业板0.8(-20%)——首版曾误写1.2(+20%涨停价)致创业板全命中
        lim_mult = 0.8 if str(code).startswith("30") else 0.9
        lim = round(float(pc) * lim_mult, 2)
        c, o, h, l = float(c), float(o), float(h), float(l)
        # 2026-09-29 勘误: 首跑25万事件=除权除息日误判(自然跌幅恰越10%线,如高送转-50%),
        # 加除权剔除: 跌幅超涨跌停上限(主板11%/创业21%)即排除
        drop = c / float(pc) - 1
        drop_cap = 0.21 if str(code).startswith("30") else 0.11
        if c <= lim + 1e-9 and drop >= -drop_cap:
            events.append({
                "code": code, "date": date, "lim": lim, "close": c, "open": o, "high": h, "low": l,
                "yesterday_zt": None,  # 二遍填充
                "one_word": abs(o - lim) < 1e-9 and abs(h - lim) < 1e-9 and abs(l - lim) < 1e-9,
                "sealed": abs(c - lim) < 1e-9,
                "next_open": float(no) if no else None,
                "next_close": float(nc) if nc else None,
            })
    # 前日涨停标记（用于 Q4 分组: 涨停次日跌停）
    zt_set = set()
    prev_map = {}
    for code, date, o, h, l, c, pc, no, nc in rows:
        if pc and c and float(pc) > 0:
            lim_mult = 1.2 if str(code).startswith("30") else 1.1
            if float(c) >= round(float(pc) * lim_mult, 2) - 1e-9:
                zt_set.add((code, date))
    for e in events:
        i_prev = None
        e["yesterday_zt"] = (e["code"], e["date"]) in zt_set and False  # 自身日非前日
    # 正确口径: 前一交易日涨停——用 (code, date) 相邻关系: 直接查 zt_set 含 (code, 前日)
    cal = sorted({r[1] for r in rows})
    idx = {d: i for i, d in enumerate(cal)}
    for e in events:
        i = idx.get(e["date"])
        if i and i > 0:
            e["yesterday_zt"] = (e["code"], cal[i - 1]) in zt_set

    one_word = [e for e in events if e["one_word"]]
    sealed = [e for e in events if e["sealed"] and not e["one_word"]]
    # 2026-09-29 口径细分: 封死桶中「盘中开过板」(low<跌停价=有过非跌停成交机会, 当日可执行)
    # vs 「盘中从未开板」(仅集合竞价挂单机会)——两档执行含义完全不同, 混桶会高估不可执行率
    sealed_opened = [e for e in sealed if e["low"] < e["lim"] - 1e-9]
    sealed_never = [e for e in sealed if e["low"] >= e["lim"] - 1e-9]
    touched = [e for e in events if not e["sealed"]]
    # Q2: 封死(一字+尾盘封死)次日开盘滑差
    slip = [e["next_open"] / e["lim"] - 1 for e in sealed + one_word if e["next_open"]]
    # Q3: 次日仍跌停
    still = 0
    for e in sealed + one_word:
        if e["next_close"] and e["next_open"]:
            lim2 = round(e["next_open"] * 0.9 if not str(e["code"]).startswith("30") else e["next_open"] * 0.8, 2)
            if e["next_close"] <= lim2 + 1e-9:
                still += 1
    n_sealed = len(sealed) + len(one_word)
    # Q4 分组
    for grp, sel in (("涨停次日跌停(A杀/天地板族)", lambda e: e["yesterday_zt"]),
                     ("非涨停跌停(阴跌族)", lambda e: not e["yesterday_zt"])):
        g_ow = [e for e in one_word if sel(e)]
        g_se = [e for e in sealed if sel(e)]
        g_slip = [e["next_open"] / e["lim"] - 1 for e in g_se + g_ow if e["next_open"]]

    lines = [f"# 极端行情卖出可行性研究 L2 首版（{datetime.now().strftime('%Y-%m-%d %H:%M')}）", "",
             f"- 样本: 20260101~20260928 全A(剔科创/北交所) 跌停事件 **{len(events)}** 起",
             "", "## Q1 触发日可执行性", "",
             f"- 一字跌停(全天零成交机会): **{len(one_word)}** 起 ({len(one_word) / max(1, len(events)) * 100:.1f}%)",
             f"- 收盘封死·盘中从未开板(仅竞价挂单机会): **{len(sealed_never)}** 起 ({len(sealed_never) / max(1, len(events)) * 100:.1f}%)",
             f"- 收盘封死·盘中开过板(当日可执行): **{len(sealed_opened)}** 起 ({len(sealed_opened) / max(1, len(events)) * 100:.1f}%)",
             f"- 未封死(盘中可执行): **{len(touched)}** 起 ({len(touched) / max(1, len(events)) * 100:.1f}%)",
             f"- **当日完全无法成交合计(一字+从未开板): {len(one_word) + len(sealed_never)} 起 "
             f"({(len(one_word) + len(sealed_never)) / max(1, len(events)) * 100:.1f}%)**",
             "", "## Q2 封死事件次日开盘滑差(开盘卖出 vs 当日跌停价)", "",
             f"- {pct_block(slip)}",
             "- 负值=次日开盘低于当日跌停价(连续跌停开口), 正值=高开可减损。", "",
             f"## Q3 连续跌停风险(封死{n_sealed}起中次日再跌停)", "",
             f"- 次日继续跌停: {still} 起 ({still / max(1, n_sealed) * 100:.1f}%)",
             "", "## Q4 分组(预注册两族)", ""]
    for grp, sel in (("涨停次日跌停(A杀/天地板族)", lambda e: e["yesterday_zt"]),
                     ("非涨停跌停(阴跌族)", lambda e: not e["yesterday_zt"])):
        g_ev = [e for e in events if sel(e)]
        g_ow = [e for e in one_word if sel(e)]
        g_se = [e for e in sealed if sel(e)]
        g_slip = [e["next_open"] / e["lim"] - 1 for e in g_se + g_ow if e["next_open"]]
        g_still = 0
        for e in g_se + g_ow:
            if e["next_close"] and e["next_open"]:
                lim2 = round(e["next_open"] * (0.8 if str(e["code"]).startswith("30") else 0.9), 2)
                if e["next_close"] <= lim2 + 1e-9:
                    g_still += 1
        lines += [f"### {grp}",
                  f"- 事件 {len(g_ev)} | 一字 {len(g_ow)} ({len(g_ow) / max(1, len(g_ev)) * 100:.1f}%) "
                  f"| 尾盘封死 {len(g_se)} | 次日开盘滑差 {pct_block(g_slip)} "
                  f"| 次日续跌停 {g_still / max(1, len(g_se) + len(g_ow)) * 100:.1f}%", ""]
    lines += ["## 预案数据结论（供主人裁决, 本件零交易权）", "",
              "- 数据支撑三档预案素材: ①一字占比=触发日即不可执行的比例下限; ②封死次日开盘滑差中位=排队开盘卖出的预期减损; ③续跌停率=连续锁死风险。具体预案档位(挂跌停价排队/次日集合竞价/分批)由主人裁决, 本件不预设。"]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[L2] 跌停事件{len(events)} 一字{len(one_word)}({len(one_word) / max(1, len(events)) * 100:.1f}%) "
          f"封死{len(sealed)} 次日滑差中位={statistics.median(slip) * 100:+.2f}%" if slip else "[L2] 无滑差样本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
