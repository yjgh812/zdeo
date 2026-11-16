# -*- coding: utf-8 -*-
"""
W2 主线轮动量化描述研究 (B站学习第40轮, 2026-10-06)

立项依据(引用prereg_preflight 20261006查证):
- 证伪档案bf8fefa2(主升浪择时20260904) disposition原文: 「升级stage-2须:更稳健主线定义
  (涨停范式聚合/资金持续性,非5日涨幅榜)+行业K线样本延长」——本研究正是按该档案指定的
  「涨停范式聚合」替代定义做的描述性结构统计, 非复活被证伪的"5日涨幅榜主线资格约束"信号。
- 纯描述层: 寿命分布/衰减曲线/切换日特征, 不构成任何买入依据(追涨家族五次证伪在案),
  不接线不立信号, 零交易权。

判据 LOCKED (写死于看结果前, 设计值非拟合):
- 数据: qfq库全市场日线(2016-01~2026-09) × industry_map_ths同花顺L1(90类,三级取首段)
- 涨停: 主板 chg>=9.8 / 创业板(300/301) chg>=19.8; 跌停对称; 剔688/北交所(8/4/920开头);
  每股首日(无涨跌幅限制)剔除; ST按5%涨停不可判=口径漏ST(屠龙宇宙剔除ST,如实标注)
- 主线资格: 同花顺L1行业当日涨停家数>=3
- 主线段: 连续>=3个交易日满足资格
- 衰减读数: 段内第N日(N=1..5,>=6归6+) 次日行业等权收益(成员chg等权均值) 中位/胜率
- 对照组: 行业当日涨停1-2家(有热度未达主线)的次日行业等权收益
- 切换日: 段末日+1(首个资格丧失日) vs 段内日, 市场涨停总数/跌停总数对比
- 样本期: 2016-01-04 ~ 数据尾; 分年段2016-2020/2021-2026稳健性
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
WB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT = r"D:\zcodesj\tmp\mainline_rotation_20261006.json"

ZT_MAIN, ZT_GEM = 9.8, 19.8
MIN_ZT, MIN_STREAK = 3, 3


def load_map():
    conn = sqlite3.connect(f"file:{WB}?mode=ro", uri=True)
    m = {}
    for code, ind in conn.execute("SELECT code, industry FROM industry_map_ths"):
        if not code or not ind:
            continue
        m[code.strip()] = ind.split("-")[0]
    conn.close()
    return m


def is_gem(code):
    return code.startswith(("300", "301"))


def skip_code(code):
    return code.startswith(("688", "689", "8", "4", "920"))


def main():
    ind_map = load_map()
    print(f"industry_map rows={len(ind_map)}")
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date")]
    didx = {d: i for i, d in enumerate(dates)}
    nd = len(dates)
    print(f"dates={nd} span={dates[0]}..{dates[-1]}")

    first_day = {}
    for code, d in conn.execute("SELECT code, MIN(date) FROM stock_daily_meta_qfq GROUP BY code"):
        first_day[code] = d

    n_ind = len(set(ind_map.values()))
    zt = [defaultdict(int) for _ in range(nd)]      # 行业涨停家数
    ew_sum = [defaultdict(float) for _ in range(nd)]  # 行业chg求和
    ew_cnt = [defaultdict(int) for _ in range(nd)]
    mkt_zt = [0] * nd
    mkt_dt = [0] * nd

    cur = conn.execute("SELECT code, date, change_pct FROM stock_daily_meta_qfq WHERE change_pct IS NOT NULL")
    rows = 0
    for code, d, chg in cur:
        rows += 1
        if skip_code(code) or first_day.get(code) == d:
            continue
        i = didx[d]
        gem = is_gem(code)
        zt_th = ZT_GEM if gem else ZT_MAIN
        if chg >= zt_th:
            mkt_zt[i] += 1
        elif chg <= -zt_th:
            mkt_dt[i] += 1
        ind = ind_map.get(code)
        if not ind:
            continue
        if chg >= zt_th:
            zt[i][ind] += 1
        ew_sum[i][ind] += chg
        ew_cnt[i][ind] += 1
    conn.close()
    print(f"scanned rows={rows}")

    ew_ret = [None] * nd  # 市场等权
    tot, cnt = 0, 0
    for i in range(nd):
        tot += sum(ew_sum[i].values())
        cnt += sum(ew_cnt[i].values())
        ew_ret[i] = tot / cnt if cnt else None

    # 主线段提取
    segments = []  # (ind, start, end) inclusive idx
    by_ind = defaultdict(list)
    for ind in set(ind_map.values()):
        seq = [1 if zt[i].get(ind, 0) >= MIN_ZT else 0 for i in range(nd)]
        s = None
        for i, v in enumerate(seq + [0]):
            if v and s is None:
                s = i
            elif not v and s is not None:
                if i - s >= MIN_STREAK:
                    segments.append((ind, s, i - 1))
                s = None
    print(f"segments={len(segments)}")

    def med_win(xs):
        xs = sorted(x for x in xs if x is not None)
        if not xs:
            return None
        n = len(xs)
        med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
        return {"n": n, "med": round(med, 3), "win": round(100 * sum(1 for x in xs if x > 0) / n, 1)}

    # 衰减曲线: 段内第N日(1-based)次日行业等权收益
    decay = defaultdict(list)
    seg_lens = []
    for ind, s, e in segments:
        seg_lens.append(e - s + 1)
        for i in range(s, e + 1):
            N = min(i - s + 1, 6)
            if i + 1 < nd and ew_cnt[i + 1].get(ind, 0) >= 5:
                decay[N].append(ew_sum[i + 1][ind] / ew_cnt[i + 1][ind])
    decay_table = {str(k): med_win(v) for k, v in sorted(decay.items())}

    # 对照组: 当日涨停1-2家(热度未达资格)
    control = []
    for i in range(nd):
        for ind, c in zt[i].items():
            if 1 <= c < MIN_ZT and i + 1 < nd and ew_cnt[i + 1].get(ind, 0) >= 5:
                control.append(ew_sum[i + 1][ind] / ew_cnt[i + 1][ind])

    # 切换日特征: 段末日 vs 结束日(首个资格丧失日) vs 延续日
    in_seg_days, end_days = [], []
    for ind, s, e in segments:
        for i in range(s, e + 1):
            in_seg_days.append(i)
        if e + 1 < nd:
            end_days.append(e + 1)
    in_seg_days = sorted(set(in_seg_days))

    def day_feat(i):
        r = ew_ret[i]
        return (round(mkt_zt[i], 0), round(mkt_dt[i], 0), round(r, 3) if r is not None else None)

    # 分年段稳健性
    seg_year = defaultdict(lambda: defaultdict(list))
    for ind, s, e in segments:
        yr = dates[s][:4]
        bucket = "2016-2020" if yr <= "2020" else "2021-2026"
        for i in range(s, e + 1):
            N = min(i - s + 1, 6)
            if i + 1 < nd and ew_cnt[i + 1].get(ind, 0) >= 5:
                seg_year[bucket][N].append(ew_sum[i + 1][ind] / ew_cnt[i + 1][ind])

    result = {
        "meta": {
            "locked": {"zt_main": ZT_MAIN, "zt_gem": ZT_GEM, "min_zt": MIN_ZT, "min_streak": MIN_STREAK,
                        "industry": "同花顺L1(90类,三级取首段)", "skip": "688/北交所/每股首日/ST漏判如实标注"},
            "dates": [dates[0], dates[-1]], "n_dates": nd,
        },
        "segments": {"count": len(segments),
                      "life_dist": med_win([float(x) for x in seg_lens]),
                      "life_hist": {str(k): seg_lens.count(k) for k in range(MIN_STREAK, max(seg_lens) + 1)},
                      "top10_longest": sorted(
                          [{"ind": ind, "start": dates[s], "end": dates[e], "days": e - s + 1}
                           for ind, s, e in segments], key=lambda x: -x["days"])[:10]},
        "decay_next_day_by_N": decay_table,
        "control_1to2_zt": med_win(control),
        "switch_day_features": {
            "in_segment_mkt": [round(sum(mkt_zt[i] for i in in_seg_days) / len(in_seg_days), 1),
                                round(sum(mkt_dt[i] for i in in_seg_days) / len(in_seg_days), 1)],
            "segment_end_mkt": [round(sum(mkt_zt[i] for i in end_days) / len(end_days), 1),
                                 round(sum(mkt_dt[i] for i in end_days) / len(end_days), 1)],
            "n_end_days": len(end_days)},
        "year_split_decay": {b: {str(k): med_win(v) for k, v in sorted(d.items())} for b, d in seg_year.items()},
        "sample_day_features": {"first_seg_day": day_feat(segments[0][1]) if segments else None,
                                 "last_seg_day": day_feat(segments[-1][1]) if segments else None},
    }
    seg_rows = [{"ind": ind, "start": dates[s], "end": dates[e], "days": e - s + 1} for ind, s, e in segments]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"result": result, "segments": seg_rows}, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"saved -> {OUT}")


if __name__ == "__main__":
    main()
