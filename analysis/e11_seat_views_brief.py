#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E11 席位三维视图消费简报生成器（只读描述层，零交易权）。

读 attribution.db 三表（e11_seat_duel/switch/pct，由 e11_seat_views_build.py 维护），
产出 data/e11_seat_views_brief_latest.txt：最新交易日对峙Top5 / 换仓Top5 / 罕见体量
(分位≥95)名单。**仅为研判参考简报，未接入任何晨报链**（是否挂载由主人裁决）；
表未建/无数据 → 一行带过。可重复运行（幂等覆盖简报）。

运行: python analysis/e11_seat_views_brief.py
"""
import json
import sqlite3

DB = r"D:\zcodesj\attribution\attribution.db"
BRIEF = r"D:\zcodesj\data\e11_seat_views_brief_latest.txt"


def main():
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        latest = con.execute("SELECT MAX(trade_date) FROM e11_seat_duel").fetchone()[0]
        if not latest:
            print("E11简报: 三表为空(先运行 e11_seat_views_build.py)")
            return
        duel = con.execute(
            "SELECT trade_date, ts_code, security_name, buy_seat, buy_yi, "
            "sell_seat, sell_yi FROM e11_seat_duel WHERE trade_date=? "
            "ORDER BY total_yi DESC LIMIT 5", (latest,)).fetchall()
        switch = con.execute(
            "SELECT seat, sell_day, sold_code, sold_yi, buy_day, bought_code, "
            "bought_yi, gap_days FROM e11_seat_switch WHERE buy_day>=? "
            "ORDER BY sold_yi+bought_yi DESC LIMIT 5",
            (latest,)).fetchall()
        pct = con.execute(
            "SELECT trade_date, ts_code, seat, buy_yi, pct_in_own_hist "
            "FROM e11_seat_pct WHERE trade_date>=? AND pct_in_own_hist>=95 "
            "ORDER BY buy_yi DESC LIMIT 10", (latest,)).fetchall()
        con.close()
    except sqlite3.OperationalError as e:
        print(f"E11简报: 表不可读({e}) → 先运行 e11_seat_views_build.py")
        return

    lines = [f"E11席位三维简报 | 最新数据 {latest} | 观测层零交易权"]
    lines.append("【多空对峙Top5(同日同票双边≥3000万)】")
    for r in duel:
        lines.append(f"  {r['trade_date']} {r['ts_code']} {r['security_name'] or ''}: "
                     f"买[{r['buy_seat']}] {r['buy_yi']}亿 vs 卖[{r['sell_seat']}] {r['sell_yi']}亿")
    lines.append("【换仓轨迹Top5(卖A→1~4日买B)】")
    for r in switch:
        lines.append(f"  [{r['seat']}] {r['sell_day']}卖{r['sold_code']} {r['sold_yi']}亿 → "
                     f"{r['buy_day']}买{r['bought_code']} {r['bought_yi']}亿 (隔{r['gap_days']}日)")
    lines.append("【罕见体量买入(自身历史分位≥95)】")
    for r in pct:
        lines.append(f"  {r['trade_date']} {r['ts_code']} [{r['seat']}] {r['buy_yi']}亿 "
                     f"= 自身分位 {r['pct_in_own_hist']}%")
    txt = "\n".join(lines) + "\n(配置型大额买=中长线信号参考; 铺赛道型切换=板块内部轮动参考; "
    txt += "均为研判参考非交易依据)\n"
    with open(BRIEF, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


if __name__ == "__main__":
    main()
