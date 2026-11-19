#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_select_research_20261120.py — 画像选股重开题·深度描述性研究（2026-11-20）
================================================================================
背景: 主人 2026-11-20 令「重新深度全面寻找，画像系统如何能选到上涨的股票」——
知情边界后的正式重开（推翻 2026-11-18 PORTRAIT 选股族归档裁决，程序=条件不同
论证+预注册，本件=重开题研究，不含任何验证性检验）。

宪法边界（先立此存照）:
  已证伪空间 = E2/E3/E4 三轮「静态截面特征(pos250/pos60/vola20/ret60/ret20 低位
  低波族) → t+20 收益」的截面选股, OOS 均 t≈1.1 弱正不足, 宽幅震荡市稳定失效。
  本研究只做**描述性统计**（历史结构描述, 不含"今日选股→未来收益"的检验）;
  任何假设验证须预注册跑前冻结判据后另跑。

四个分析（全部纯描述）:
  A1 状态转移矩阵: P(下一状态|当前状态×段龄桶) —— 状态序列的结构
  A2 上涨段收益时间结构: 完结确认段的段长/幅度分布 + 段内收益到达曲线
     （段的肉在前半还是后半 —— 早期识别的结构空间）
  A3 市场三态占比时序: 2016-2026 月度采样 + 普跌事件描述基线
     （市场 DOWN 占比≥60% 事件后的指数表现, 事件研究式描述, 非信号）
  A4 输出: 全部统计落 reports/PORTRAIT_SELECT_RESEARCH_20261120.json 供预注册引用

用法: python -I analysis/portrait_select_research_20261120.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import defaultdict

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REGIME_DB = r"D:\zcodesj\data\universe_portrait_regime.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
DIM_DB = r"D:\zcodesj\data\universe_portrait_fullseries.db"
OUT = r"D:\zcodesj\reports\PORTRAIT_SELECT_RESEARCH_20261120.json"


def load_segments():
    con = sqlite3.connect(f"file:{REGIME_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT code, state, start_date, end_date, n_days, ret_pct, is_confirmed "
        "FROM regime_segments ORDER BY code, start_date").fetchall()
    con.close()
    return rows


def analysis_A1(segs):
    """状态转移矩阵（确认段链, 全市场聚合）+ 段龄条件化。"""
    by_code = defaultdict(list)
    for r in segs:
        by_code[r[0]].append(r)
    trans = defaultdict(lambda: defaultdict(int))          # 无条件 P(next|cur)
    trans_age = defaultdict(lambda: defaultdict(int))      # 按当前段龄桶
    for code, lst in by_code.items():
        for a, b in zip(lst[:-1], lst[1:]):
            cur, nxt = a[1], b[1]
            trans[cur][nxt] += 1
            age = a[4]
            bucket = ("<=20" if age <= 20 else "21-60" if age <= 60
                      else "61-120" if age <= 120 else ">120")
            trans_age[(cur, bucket)][nxt] += 1
    out = {}
    for cur, row in trans.items():
        tot = sum(row.values())
        out[cur] = {k: round(v / tot, 4) for k, v in row.items()}
    out_age2 = {}
    for (cur, bucket), row in trans_age.items():
        s = sum(row.values())
        if s:
            out_age2[f"{cur}|age={bucket}"] = {k: round(v / s, 4) for k, v in row.items()}
    return out, out_age2


def analysis_A2(con_q):
    """完结确认 UP/DOWN 段的收益时间结构（段内到达曲线）。"""
    con = sqlite3.connect(f"file:{REGIME_DB}?mode=ro", uri=True)
    segs = con.execute(
        "SELECT code, state, start_date, end_date FROM regime_segments "
        "WHERE is_confirmed=1").fetchall()
    con.close()
    # 逐段拉价格算到达曲线（按段长比例点采样, 全段太多; 采样至 20 万段以内可行）
    # 为控制耗时: 段表 22 万确认段, 每段一次 SQL 太慢 → 一次性载入每股收盘价内存
    codes = sorted({r[0] for r in segs})
    price = {}
    sc = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    cur = sc.execute(
        "SELECT code, date, close FROM stock_daily_meta_qfq "
        "WHERE close>0 AND volume>0 ORDER BY code, date")
    import itertools
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        if code not in set(codes):
            next(grp, None)
            continue
        rows = list(grp)
        price[code] = {int(r[1]): float(r[2]) for r in rows}
    sc.close()
    # 到达曲线: 第 k 比例点的段内收益占段总收益比例（仅 UP 段, DOWN 段对称处理）
    fracs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    arr_frac_up = defaultdict(list)     # frac -> [占比]
    lens_up, rets_up = [], []
    # 日期索引: 段的 start/end 是交易日键, 需要序号——载入时保存有序日期列表
    # （上面存了 dict, 再补一个有序列表）
    # 简化: 用 bisect 在有序日期列表上定位
    import bisect
    dates_sorted = {c: sorted(d) for c, d in price.items()}
    n_done = 0
    for code, st, sd, ed in segs:
        pm = price.get(code)
        if not pm:
            continue
        dl = dates_sorted[code]
        i0 = bisect.bisect_left(dl, sd)
        i1 = bisect.bisect_left(dl, ed)
        L = i1 - i0 + 1
        if L < 15:  # 极短段到达曲线噪声大
            continue
        p0 = pm[dl[i0]]
        p1 = pm[dl[i1]]
        if not p0 or not p1:
            continue
        total = p1 / p0 - 1.0
        if st == "UP":
            lens_up.append(L)
            rets_up.append(total * 100)
        if abs(total) < 0.05:
            continue  # 段总幅度过小, 占比不稳定
        for f in fracs:
            k = int(round((L - 1) * f))
            pk = pm[dl[i0 + k]]
            if pk and p0:
                arr_frac_up[f].append((pk / p0 - 1.0) / total)
        n_done += 1
    arrival = {str(f): dict(
        med=round(float(np.median(v)), 3),
        p25=round(float(np.percentile(v, 25)), 3),
        p75=round(float(np.percentile(v, 75)), 3), n=len(v))
        for f, v in arr_frac_up.items()}
    return dict(
        n_confirmed_used=n_done,
        up_seg_len=dict(med=int(np.median(lens_up)), mean=round(float(np.mean(lens_up)), 1),
                        p25=int(np.percentile(lens_up, 25)), p75=int(np.percentile(lens_up, 75)),
                        n=len(lens_up)),
        up_seg_ret_pct=dict(med=round(float(np.median(rets_up)), 1),
                            p25=round(float(np.percentile(rets_up, 25)), 1),
                            p75=round(float(np.percentile(rets_up, 75)), 1)),
        arrival_curve_frac_of_total=arrival,
        note="arrival=段内第f比例点时已实现收益占段总收益比例(中位); >0.5早期集中")


def analysis_A3():
    """市场三态占比月度时序 + 普跌事件描述基线。"""
    con = sqlite3.connect(f"file:{REGIME_DB}?mode=ro", uri=True)
    segs = con.execute("SELECT state, start_date, end_date FROM regime_segments").fetchall()
    con.close()
    # 月度切片: 段与该月有交集即计入该月该状态; 用月末日历区间与段区间求交
    segs2 = sqlite3.connect(f"file:{REGIME_DB}?mode=ro", uri=True).execute(
        "SELECT code, state, start_date, end_date FROM regime_segments").fetchall()
    seg_by_month = defaultdict(lambda: defaultdict(set))
    for code, st, sd, ed in segs2:
        y0, y1 = sd // 10000, ed // 10000
        for yy in range(y0, y1 + 1):
            for mm in range(1, 13):
                m0, m1 = yy * 10000 + mm * 100 + 1, yy * 10000 + mm * 100 + 31
                if ed >= m0 and sd <= m1:
                    seg_by_month[(yy, mm)][st].add(code)
    series = {}
    for (yy, mm), d in sorted(seg_by_month.items()):
        tot = sum(len(v) for v in d.values())
        if tot > 500:
            series[f"{yy}-{mm:02d}"] = {st: round(len(v) / tot, 3)
                                        for st, v in d.items()}
    # 普跌事件(月度口径描述): DOWN占比>=0.55 的月份序列
    dn = {k: v.get("DOWN", 0) for k, v in series.items()}
    keys = sorted(dn)
    events = []
    i = 0
    while i < len(keys):
        if dn[keys[i]] >= 0.55:
            j = i
            while j + 1 < len(keys) and dn[keys[j + 1]] >= 0.55:
                j += 1
            events.append(dict(start=keys[i], end=keys[j], n=j - i + 1,
                               peak=round(max(dn[k] for k in keys[i:j + 1]), 3)))
            i = j + 1
        else:
            i += 1
    return dict(monthly_share=series, panic_events=events,
                note="事件=月度DOWN占比≥55%的连续段(描述, 非信号)")


def analysis_A3b(events):
    """普跌事件后的市场表现基线（事件研究式描述, 完备历史, 非信号）。
    基准=全市场个股收益中位数(等权代理, qfq 截面), 规避 market_daily 仅 2020+ 的覆盖缺陷。"""
    sc = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    all_dates = sorted(d for (d,) in sc.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq"))
    dd = np.asarray(all_dates)

    def first_td_after(y, m):
        tgt = y * 10000 + m * 100 + 1
        i = int(np.searchsorted(dd, tgt))
        return int(dd[i]) if i < len(dd) else None

    targets = set()
    plan = []  # (event, base_date, {k: date})
    for e in events:
        y, m = int(e["end"][:4]), int(e["end"][5:7])
        base = first_td_after(y, m + 1) if m < 12 else first_td_after(y + 1, 1)
        if base is None:
            continue
        ks = {}
        for k in (3, 6, 12):
            yy, mm = (y, m + k) if m + k <= 12 else (y + (m + k - 1) // 12, (m + k - 1) % 12 + 1)
            d = first_td_after(yy, mm)
            if d:
                ks[k] = d
        targets.add(base)
        targets.update(ks.values())
        plan.append((e, base, ks))
    ph = ",".join("?" * len(targets))
    px = defaultdict(dict)
    for d, code, c in sc.execute(
            f"SELECT date, code, close FROM stock_daily_meta_qfq "
            f"WHERE date IN ({ph}) AND close>0 AND volume>0", sorted(targets)):
        px[int(d)][code] = float(c)
    sc.close()
    out = []
    for e, base, ks in plan:
        b = px.get(base, {})
        rec = dict(event=e)
        for k, d in ks.items():
            t = px.get(d, {})
            common = set(b) & set(t)
            if len(common) < 500:
                rec[f"eq_med_{k}m_pct"] = None
                continue
            rets = [(t[c] / b[c] - 1.0) * 100 for c in common]
            rec[f"eq_med_{k}m_pct"] = round(float(np.median(rets)), 1)
        out.append(rec)
    s3 = [r["eq_med_3m_pct"] for r in out if r.get("eq_med_3m_pct") is not None]
    s6 = [r["eq_med_6m_pct"] for r in out if r.get("eq_med_6m_pct") is not None]
    return dict(events_after=out,
                summary=dict(n=len(out),
                             med_3m=round(float(np.median(s3)), 1) if s3 else None,
                             med_6m=round(float(np.median(s6)), 1) if s6 else None,
                             share_3m_pos=round(sum(1 for x in s3 if x > 0) / len(s3), 3) if s3 else None),
                note="事件结束月次月起全市场个股中位收益3/6/12月(等权基线, 描述非信号; 含2026-10在途)")


def main():
    t0 = time.time()
    segs = load_segments()
    print(f"段 {len(segs)} 载入 {time.time()-t0:.1f}s")
    a1, a1_age = analysis_A1(segs)
    print("A1 状态转移矩阵完成")
    a2 = analysis_A2(QFQ_DB)
    print(f"A2 段收益时间结构完成 (用段 {a2['n_confirmed_used']})")
    a3 = analysis_A3()
    print(f"A3 市场时序完成 (月数 {len(a3['monthly_share'])}, 普跌事件 {len(a3['panic_events'])})")
    a3b = analysis_A3b(a3["panic_events"])
    print("A3b 事件后基线:", a3b.get("summary"))
    out = dict(built_at=time.strftime("%F %T"),
               scope="纯描述性研究; 已证伪空间=E2/E3/E4静态截面低位低波族截面选股; "
                     "本研究不含验证性检验, 验证须预注册",
               A1_transitions=a1, A1_transitions_by_age=a1_age,
               A2_up_segment_structure=a2, A3_market_series=a3,
               A3b_after_panic_baseline=a3b)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"落 {OUT} {time.time()-t0:.1f}s")
    # 控制台摘要
    print("\n== A1 P(下一状态|当前) ==")
    for cur, row in a1.items():
        print(f"  {cur}: {row}")
    print("\n== A1 段龄条件化(前8行) ==")
    for k in list(a1_age)[:8]:
        print(f"  {k}: {a1_age[k]}")
    print("\n== A2 上涨段结构 ==")
    print(" 段长:", a2["up_seg_len"], " 幅度%:", a2["up_seg_ret_pct"])
    print(" 到达曲线(占总收益比例中位):",
          {k: v["med"] for k, v in a2["arrival_curve_frac_of_total"].items()})
    print("\n== A3 普跌事件 ==")
    for e in a3["panic_events"]:
        print(" ", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
