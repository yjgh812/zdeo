# -*- coding: utf-8 -*-
"""pit_fundamental_backfill_20261028.py — stock_financial revenue_ttm/total_shares 断供回填 + Q2 窗口 sp 列补空重算

根因(20261028 定谳):
  二采集器(revenue_ttm 源)衰减死亡: 2025年报 3144只 → 2026Q1 1509只 → 2026中报 0只;
  Phase4 的 sp/lev/ato/pm 依赖 revenue_ttm → 20260630 报告行(19.4万 factor 行)四列全空。
数据源(内容级回填, 零结构变更):
  营收 = akshare stock_yjbb_em 全市场业绩报表(东财源, 11k+只/期, 营业总收入累计值, 元);
  TTM  = FY2025 + H1_2026 − H1_2025 (Q1 报告 = FY2025 + Q1_2026 − Q1_2025);
  股本 = fundamental_data.db daily_basic.total_share(日更, 万股→股);
  只填空(COALESCE), 不覆盖既有值; stock_financial.revenue_ttm 单位=万元(与 Phase4 公式约定一致)。
sp 重算(同语义同量纲只补空): sp = rev_ttm_万元 × 1e4 / (close × shares_股) = 1/PS_TTM。
  —— ato/lev/pm 三列不回填: 历史量纲错乱实锤(茅台 pm=1582, Phase4 公式按万股假设而库存股数),
     正确量纲重构(加列 sp_v2 族)属数据合同变更, 按宪法走三段式立项, 本脚本不擅动。
用法: python analysis/pit_fundamental_backfill_20261028.py [--check-only]
"""
from __future__ import annotations
import json, sys, os, sqlite3, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
MAIN = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
FUND = r"D:/workbuddyspace/databases/fundamental_data.db"
CHECK_ONLY = "--check-only" in sys.argv

PERIODS = {  # akshare 业绩报表期 → 用途
    "20251231": "FY2025",
    "20260630": "H1_2026",
    "20250630": "H1_2025",
    "20260331": "Q1_2026",
    "20250331": "Q1_2025",
    "20260930": "Q3_2026",
    "20250930": "Q3_2025",
}
SP_WINDOWS = "20260630, 20260331, 20260930"  # sp 补空窗口(Q3=10-31 披露窗口前瞻)


def pull_yjbb(date: str) -> dict[str, float]:
    """akshare 全市场业绩报表 → {code6: 营业总收入(元, 累计)}"""
    import akshare as ak
    df = ak.stock_yjbb_em(date=date)
    out = {}
    for _, r in df.iterrows():
        try:
            v = float(r["营业总收入-营业总收入"])
        except (TypeError, ValueError):
            continue
        out[str(r["股票代码"]).zfill(6)] = v
    return out


def pull_shares(con_f, pit_date: int) -> dict[str, float]:
    """daily_basic 指定日前最近截面总股本 → {code6: 股}"""
    rows = con_f.execute(
        "select ts_code, total_share from daily_basic "
        "where trade_date=(select max(trade_date) from daily_basic where trade_date<=?) "
        "and total_share is not null", (pit_date,)).fetchall()
    return {ts.split(".")[0].zfill(6): float(wan) * 1e4 for ts, wan in rows}


def main():
    t0 = time.time()
    con = sqlite3.connect(MAIN)
    con.execute("PRAGMA journal_mode=WAL")
    con_f = sqlite3.connect(f"file:{FUND}?mode=ro", uri=True)

    # 现状断供统计
    stat = con.execute(
        "select report_date, count(*), count(revenue_ttm), count(total_shares) "
        "from stock_financial where report_date in (20260630,20260331) "
        "group by report_date").fetchall()
    print("[现状] stock_financial (report_date, rows, rev_ttm非空, shares非空):", stat)

    if CHECK_ONLY:
        con.close(); con_f.close()
        return

    # 1) 拉营收 5 期
    rev = {}
    for d, tag in PERIODS.items():
        rev[d] = pull_yjbb(d)
        print(f"[拉取] yjbb {d}({tag}): {len(rev[d])} 只")

    # 2) TTM 合成: 元 → 万元 (TTM = FY2025 + 当期 − 上年同期)
    ttm_wan = {}
    for code in set().union(*(set(rev[p]) for p in PERIODS)):
        fy = rev["20251231"].get(code)
        if not fy:
            continue
        for rd, prev in (("20260630", "20250630"), ("20260331", "20250331"), ("20260930", "20250930")):
            cur, pv = rev[rd].get(code), rev[prev].get(code)
            if cur and pv:
                ttm_wan.setdefault(code, {})[rd] = (fy + cur - pv) / 1e4
    for rd in ("20260630", "20260331", "20260930"):
        n = sum(1 for v in ttm_wan.values() if rd in v)
        print(f"[TTM] 可合成: {rd}={n} 只")

    # 3) 股本截面(万股→股); 取可用最新截面, 覆盖 Q3 披露窗口(10-31)
    shares = pull_shares(con_f, 20261031)
    print(f"[股本] daily_basic@<=20261031: {len(shares)} 只")

    # 4) 回填 stock_financial(只填空)
    filled_rev = filled_sh = skipped = 0
    for rd in ("20260630", "20260331", "20260930"):
        rows = con.execute(
            "select id, code, revenue_ttm, total_shares from stock_financial where report_date=?",
            (int(rd),)).fetchall()
        for rid, code, rev_now, sh_now in rows:
            t = ttm_wan.get(code, {}).get(rd)
            s = shares.get(code)
            if t is None and s is None:
                skipped += 1
                continue
            new_rev = t if (rev_now is None and t is not None) else None
            new_sh = s if (sh_now is None and s is not None) else None
            if new_rev is None and new_sh is None:
                continue
            sets, args = [], []
            if new_rev is not None:
                sets.append("revenue_ttm=?"); args.append(round(new_rev, 2)); filled_rev += 1
            if new_sh is not None:
                sets.append("total_shares=?"); args.append(round(new_sh)); filled_sh += 1
            args.append(rid)
            con.execute(f"update stock_financial set {', '.join(sets)} where id=?", args)
    con.commit()
    print(f"[回填] revenue_ttm 填 {filled_rev} / total_shares 填 {filled_sh} / 双缺可补不足跳过 {skipped}")

    # 5) Q2 窗口 factor 行 sp 补空重算(同 Phase4 公式/单位, 只填空)
    #    fin_data 选取语义与 Phase4 一致: roe 非空行按 (code, report_date) 排序, bisect 取最新报告
    from bisect import bisect_right
    from collections import defaultdict
    fdata = defaultdict(list)
    for code, rd, rev_ttm, sh, bps, roe in con.execute(
        "select code, report_date, revenue_ttm, total_shares, bps, roe from stock_financial "
        "where roe is not null order by code, report_date"):
        fdata[code].append((int(rd), rev_ttm, sh, bps, roe))
    rows = con.execute(
        "select rowid, code, date, close, report_date, sp from stock_factors_fundamental "
        f"where report_date in ({SP_WINDOWS}) and sp is null").fetchall()
    upd, still = 0, 0
    per_code = {}
    for rid, code, d, close, rd, _sp in rows:
        seq = per_code.get(code)
        if seq is None:
            seq = [x[0] for x in fdata.get(code, [])]
            per_code[code] = seq
        i = bisect_right(seq, int(rd)) - 1
        if i < 0:
            still += 1
            continue
        _rd2, rev_ttm, sh, bps, roe = fdata[code][i]
        try:
            close_f = float(close); rev_f = float(rev_ttm) if rev_ttm is not None else None
            sh_f = float(sh) if sh is not None else None
        except (TypeError, ValueError):
            still += 1
            continue
        sp = (rev_f * 1e4 / (close_f * sh_f)) if (close_f > 0 and sh_f and rev_f and rev_f > 0) else None
        if sp is None:
            still += 1
            continue
        con.execute("update stock_factors_fundamental set sp=? where rowid=?", (round(sp, 10), rid))
        upd += 1
    con.commit()
    left = con.execute(
        f"select count(*) from stock_factors_fundamental where report_date in ({SP_WINDOWS}) and sp is null").fetchone()[0]
    total_q2 = con.execute(
        f"select count(*) from stock_factors_fundamental where report_date in ({SP_WINDOWS})").fetchone()[0]
    print(f"[sp重算] Q2+Q1+Q3前瞻窗口: 重算 {upd} 行 / 仍缺 {left} / 窗口总 {total_q2} 行")

    # 6) 抽检: 茅台 sp ≈ 1/PS
    r = con.execute(
        "select date, close, sp from stock_factors_fundamental "
        "where code='600519' and date in (20261027,20261026) order by date desc").fetchall()
    print("[抽检] 茅台(600519) (date, close, sp):", r,
          "→ 隐含PS=1/sp =", [round(1 / s, 1) if s else None for _, c, s in r])
    print(f"[耗时] {time.time() - t0:.0f}s")
    con.close(); con_f.close()


if __name__ == "__main__":
    main()
