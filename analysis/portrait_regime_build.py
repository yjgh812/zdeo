#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_regime_build.py — 画像状态层·全市场涨/跌/震荡状态时间线构建（2026-11-19）
================================================================================
目标（主人令 2026-11-19）: 采集现有所有数据, 搞清楚每只股票什么时候涨/什么时候跌/
什么时候震荡。

方法（纯描述性历史状态标注, 零预测零交易权; zigzag 峰谷波段划分）:
  1. 锯齿细分: 反向波动 >= TAU(15%) 才确认转折点 → 相邻峰谷间为单调波段
     (构造保证每段幅度 >= TAU 且方向交替)。
  2. 状态归类: 段幅度 >= T_TREND(20%) → UP(上涨)/DOWN(下跌); 其余 → RANGE(震荡)。
  3. 相邻同类段合并(连续 RANGE 合并; 连续 RANGE 夹在两同向趋势段之间也仅并 RANGE)。
  4. 首段/末段未经反向确认, 标 is_confirmed=0(暂定段, 随未来数据修正——这是
     分段算法固有属性, 不是预测)。
  选型说明: 曾试 60 日滚动回归(斜率+显著性+价格位置)方案, 实测段内收益与标签
  违例率 38-52%(窗口记忆滞后期: 急跌后反弹期斜率仍负), 语义错位无法根除, 弃用。

数据源（唯一价格源= qfq 库）:
  D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db stock_daily_meta_qfq
  全历史 19901219-最新; 有效行 = close>0 AND volume>0（剔除停牌填充行）。
  2016 前覆盖薄(199-220只/年)照算, 限制登记进 meta（沿 falsified_rules 2026-11-15 口径）。

输出:
  data/universe_portrait_regime.db
    regime_segments(code,state,start_date,end_date,n_days,ret_pct,is_confirmed)
    regime_summary(code,name,industry,first_date,last_date,n_days,n_segs,
                   up_days,down_days,range_days,up_pct,down_pct,range_pct,
                   cur_state,cur_start,cur_days,cur_ret_pct,cur_confirmed)
    meta(key,value)  # 参数/边界声明/数据限制/构建时间
  data/portrait_regime_latest.json  # 全市场当前状态分布+Top 快照

边界声明（铁律）:
  本产物=描述过去何时涨/跌/震荡, 不含任何未来预测; 零交易权; 禁作选股公式或
  买卖信号。任何信号化用途须新预注册并对照证伪#151（画像启动条件）与
  PORTRAIT-PRELAUNCH-E2/E3/E4（画像选股族 FAIL 归档, 2026-11-18 终局）。

内置验收闸（不通过 exit 2）:
  确认段中 UP 段收益>0 / DOWN 段收益<0 违例率 > 0.1% 即失败（zigzag 构造保证
  应为 0, 此闸防实现回归）。全市场三态天数占比任一为 0 也失败。

用法:
  python -I analysis/portrait_regime_build.py                 # 全量构建
  python -I analysis/portrait_regime_build.py --codes 600519  # 抽样试跑（只打印不落库）
"""
from __future__ import annotations

import itertools
import json
import sqlite3
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
try:
    from stock_name_lookup import get_name, get_instrument_type
except Exception:
    get_name = None  # type: ignore
    get_instrument_type = None  # type: ignore

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SRC_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
DIM_DB = r"D:\zcodesj\data\universe_portrait_fullseries.db"
OUT_DB = r"D:\zcodesj\data\universe_portrait_regime.db"
SNAP = r"D:\zcodesj\data\portrait_regime_latest.json"

TAU = 0.15        # 锯齿反转确认阈值（反向 15% 确认转折）
T_TREND = 0.20    # 趋势段幅度阈值（段幅度 ≥20% 判 UP/DOWN, 否则 RANGE）
VIOLATION_MAX = 0.001

BOUNDARY_DECL = (
    "纯描述性历史状态标注: 描述过去何时涨/跌/震荡, 不含任何未来预测; 零交易权; "
    "禁作选股公式或买卖信号; 任何信号化用途须新预注册并对照证伪#151"
    "(画像启动条件)与PORTRAIT-PRELAUNCH-E2/E3/E4(画像选股族FAIL归档2026-11-18)"
)
DATA_LIMIT = (
    "qfq库2010-2015年覆盖仅199-220只/年(2016起3151+), 早期个股状态段样本薄, "
    "全市场级统计以2016+为准; 沿用 falsified_rules data_limitations_20261115 口径"
)

STATE_UP, STATE_DOWN, STATE_RANGE = "UP", "DOWN", "RANGE"
CN = {STATE_UP: "上涨", STATE_DOWN: "下跌", STATE_RANGE: "震荡"}


def zigzag_pivots(closes: list):
    """标准锯齿: 反向 TAU 确认转折。返回 (pivots, tail_dir, tail_ext_i)。
    pivots=[(idx, +1峰/-1谷)] 交替; tail 为未确认的当前极值。
    未定阶段用候选峰/候选谷双追踪, 候选差达 TAU 时按时间先后定首个 pivot。"""
    n = len(closes)
    if n == 0:
        return [], 0, 0
    pivots = []
    cmax_i = cmin_i = 0
    direction = 0
    ext_i = 0
    for i in range(1, n):
        p = closes[i]
        if direction == 0:
            if p > closes[cmax_i]:
                cmax_i = i
            if p < closes[cmin_i]:
                cmin_i = i
            if cmax_i != cmin_i and closes[cmax_i] >= closes[cmin_i] * (1.0 + TAU):
                if cmax_i > cmin_i:      # 先谷后峰 → 上行
                    if cmin_i > 0:
                        pivots.append((cmin_i, -1))
                    direction, ext_i = +1, cmax_i
                else:                    # 先峰后谷 → 下行
                    if cmax_i > 0:
                        pivots.append((cmax_i, +1))
                    direction, ext_i = -1, cmin_i
        elif direction == +1:            # 上行: 追踪最高
            if p > closes[ext_i]:
                ext_i = i
            elif p <= closes[ext_i] * (1.0 - TAU):
                pivots.append((ext_i, +1))
                direction, ext_i = -1, i
        else:                            # 下行: 追踪最低
            if p < closes[ext_i]:
                ext_i = i
            elif p >= closes[ext_i] * (1.0 + TAU):
                pivots.append((ext_i, -1))
                direction, ext_i = +1, i
    return pivots, direction, ext_i


def segments_for_one(dates: list, closes: list):
    """单股 → 段列表 [(state, s_idx, e_idx, ret_pct, confirmed)]。"""
    n = len(closes)
    if n < 2:
        return []
    pivots, tail_dir, tail_ext_i = zigzag_pivots(closes)
    # 段边界 = 起点0 + 确认pivot序列 + 终点n-1(尾部以最新交易日收口, 暂定语义)
    bounds = [0] + [p[0] for p in pivots]
    if bounds[-1] != n - 1:
        bounds.append(n - 1)
    if len(bounds) < 2:
        return []
    raw = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b <= a:
            continue
        ret = closes[b] / closes[a] - 1.0
        if ret >= T_TREND:
            st = STATE_UP
        elif ret <= -T_TREND:
            st = STATE_DOWN
        else:
            st = STATE_RANGE
        # 确认段=两端均为确认 pivot（首段起点/末段终点不算）
        confirmed = a != 0 and b != n - 1
        # 段区间=左闭右闭但不与邻段共享边界日: 新段自 pivot 次日起
        # (转折日归后段, 全序列连续不重不漏; 段收益仍按 pivot 价 closes[b]/closes[a])
        seg_start = a if a == 0 else a + 1
        raw.append((st, seg_start, b, ret * 100.0, confirmed))
    # 相邻同类合并
    merged = []
    for seg in raw:
        if merged and merged[-1][0] == seg[0]:
            p = merged[-1]
            merged[-1] = (p[0], p[1], seg[2],
                          (closes[seg[2]] / closes[p[1]] - 1.0) * 100.0,
                          p[4] and seg[4])
        else:
            merged.append(seg)
    return merged


def iter_stocks(codes_filter=None):
    """流式逐股产出 (code, dates, closes)。有效行=close>0 AND volume>0。"""
    src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    cur = src.execute(
        "SELECT code, date, close FROM stock_daily_meta_qfq "
        "WHERE close>0 AND volume>0 ORDER BY code, date")
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        if codes_filter is not None and code not in codes_filter:
            next(grp, None)
            continue
        rows = list(grp)
        yield code, [int(r[1]) for r in rows], [float(r[2]) for r in rows]
    src.close()


def load_industry() -> dict:
    try:
        con = sqlite3.connect(f"file:{DIM_DB}?mode=ro", uri=True)
        m = {}
        for c, i in con.execute(
                "SELECT code, industry_name FROM stock_dim WHERE industry_name IS NOT NULL"):
            m[str(c).strip().zfill(6)] = i  # stock_dim.code 为整数存储, 补零对齐
        con.close()
        return m
    except Exception:
        return {}


def main():
    t0 = time.time()
    argv = sys.argv[1:]
    codes_filter = None
    if "--codes" in argv:
        idx = argv.index("--codes")
        codes_filter = set(argv[idx + 1].split(","))
        print("抽样试跑:", sorted(codes_filter))

    industry = load_industry()

    all_segs = []
    summaries = []
    skipped_inst = []  # 非股票序列(指数/债券混入qfq表, 品种闸拦截, 2026-11-19)
    viol_up = viol_dn = n_upseg = n_dnseg = 0
    total_days = up_days_all = dn_days_all = rg_days_all = 0
    asof = 0
    n_codes = 0
    for code, dates, closes in iter_stocks(codes_filter):
        # 品种闸: qfq 表混有指数/债券序列(实证 000022=沪公司债/000101=5年信用,
        # 回撤0.6%的10年压平线), 只保留 instrument_type=stock 或名称可查的代码
        itype = get_instrument_type(code) if get_instrument_type else None
        if itype is not None:
            is_stock = (itype == "stock")
        else:
            is_stock = (get_name(code) is not None) if get_name else True
        if not is_stock:
            skipped_inst.append(code)
            continue
        n_codes += 1
        asof = max(asof, dates[-1])
        segs = segments_for_one(dates, closes)
        name = get_name(code) if get_name else None
        industry_name = industry.get(code)
        cnt = {STATE_UP: 0, STATE_DOWN: 0, STATE_RANGE: 0}
        for st, s_i, e_i, ret, confirmed in segs:
            all_segs.append((code, st, dates[s_i], dates[e_i], e_i - s_i + 1,
                             round(ret, 2), 1 if confirmed else 0))
            cnt[st] += e_i - s_i + 1
            if confirmed:
                if st == STATE_UP:
                    n_upseg += 1
                    if ret <= 0:
                        viol_up += 1
                elif st == STATE_DOWN:
                    n_dnseg += 1
                    if ret >= 0:
                        viol_dn += 1
        total = cnt[STATE_UP] + cnt[STATE_DOWN] + cnt[STATE_RANGE]
        total_days += total
        up_days_all += cnt[STATE_UP]
        dn_days_all += cnt[STATE_DOWN]
        rg_days_all += cnt[STATE_RANGE]
        cur = segs[-1] if segs else None
        summaries.append((
            code, name, industry_name, dates[0], dates[-1],
            total, len(segs),
            cnt[STATE_UP], cnt[STATE_DOWN], cnt[STATE_RANGE],
            round(cnt[STATE_UP] / total * 100, 2) if total else None,
            round(cnt[STATE_DOWN] / total * 100, 2) if total else None,
            round(cnt[STATE_RANGE] / total * 100, 2) if total else None,
            cur[0] if cur else None, dates[cur[1]] if cur else None,
            (cur[2] - cur[1] + 1) if cur else None,
            round(cur[3], 2) if cur else None,
            cur[4] if cur else None))

    # ---- 内置验收闸 ----
    viol_rate = (viol_up + viol_dn) / max(1, n_upseg + n_dnseg)
    print(f"股票 {n_codes} 只 | 段 {len(all_segs)} | 覆盖天数 {total_days} | "
          f"确认UP段 {n_upseg}(违例 {viol_up}) 确认DOWN段 {n_dnseg}(违例 {viol_dn}) "
          f"违例率 {viol_rate:.4%}")
    gate_ok = viol_rate <= VIOLATION_MAX
    if codes_filter is None:
        shares = (up_days_all / total_days, dn_days_all / total_days,
                  rg_days_all / total_days)
        print(f"三态平均占比: 上涨 {shares[0]:.1%} | 下跌 {shares[1]:.1%} | "
              f"震荡 {shares[2]:.1%} | asof={asof}")
        gate_ok = gate_ok and all(s > 0 for s in shares)
    if not gate_ok:
        print("[FAIL] 验收闸未过, 拒绝落库")
        return 2
    if codes_filter is not None:
        for row in summaries:
            code, name, ind = row[0], row[1], row[2]
            print(f"\n== {code} {name or '?'} ({ind or '-'}) {row[3]}-{row[4]} "
                  f"覆盖{row[5]}日 段{row[6]} ==")
            print(f"   占比: 上涨{row[10]}% 下跌{row[11]}% 震荡{row[12]}% | "
                  f"当前: {CN.get(row[13], row[13])} 自{row[14]} 已{row[15]}日 "
                  f"({row[16]}%) 确认={row[17]}")
            segs_one = [s for s in all_segs if s[0] == code]
            for _, st, sd, ed, nd, ret, conf in segs_one[-10:]:
                print(f"   {sd}-{ed} {CN[st]:>2} {nd:>4}日 {ret:+9.2f}% "
                      f"{'确认' if conf else '暂定'}")
        print(f"\n抽样完成 {time.time()-t0:.1f}s（试跑不落库）")
        return 0

    # ---- 落库（幂等全量重建） ----
    out = sqlite3.connect(OUT_DB)
    out.executescript("""
    DROP TABLE IF EXISTS regime_segments;
    DROP TABLE IF EXISTS regime_summary;
    DROP TABLE IF EXISTS meta;
    CREATE TABLE regime_segments(
        code TEXT, state TEXT, start_date INTEGER, end_date INTEGER,
        n_days INTEGER, ret_pct REAL, is_confirmed INTEGER);
    CREATE TABLE regime_summary(
        code TEXT PRIMARY KEY, name TEXT, industry TEXT,
        first_date INTEGER, last_date INTEGER, n_days INTEGER, n_segs INTEGER,
        up_days INTEGER, down_days INTEGER, range_days INTEGER,
        up_pct REAL, down_pct REAL, range_pct REAL,
        cur_state TEXT, cur_start INTEGER, cur_days INTEGER, cur_ret_pct REAL,
        cur_confirmed INTEGER);
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE INDEX idx_seg_code ON regime_segments(code);
    CREATE INDEX idx_sum_state ON regime_summary(cur_state);
    """)
    out.executemany("INSERT INTO regime_segments VALUES (?,?,?,?,?,?,?)", all_segs)
    out.executemany("INSERT INTO regime_summary VALUES (" + ",".join("?" * 18) + ")",
                    summaries)
    meta = {
        "built_at": time.strftime("%F %T"),
        "asof": str(asof),
        "params": json.dumps({"TAU": TAU, "T_TREND": T_TREND,
                              "method": "zigzag"}),
        "n_codes": str(n_codes),
        "n_segments": str(len(all_segs)),
        "skipped_instrument": json.dumps(skipped_inst),
        "viol_rate": f"{viol_rate:.6f}",
        "boundary_decl": BOUNDARY_DECL,
        "data_limitations": DATA_LIMIT,
        "price_source": SRC_DB,
    }
    out.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
    out.commit()
    out.close()

    # ---- 全市场快照 JSON ----
    cur_dist = {"UP": 0, "DOWN": 0, "RANGE": 0}
    for row in summaries:
        if row[13] in cur_dist:
            cur_dist[row[13]] += 1

    def top_cur(state: str, n=20):
        rows = sorted((r for r in summaries if r[13] == state),
                      key=lambda r: -(r[15] or 0))[:n]
        return [{"code": r[0], "name": r[1], "industry": r[2],
                 "since": r[14], "days": r[15], "ret_pct": r[16]} for r in rows]

    snap = {
        "built_at": meta["built_at"], "asof": asof,
        "params": json.loads(meta["params"]),
        "boundary_decl": BOUNDARY_DECL,
        "n_codes": n_codes, "n_segments": len(all_segs),
        "days_share": {"UP": round(up_days_all / total_days * 100, 2),
                       "DOWN": round(dn_days_all / total_days * 100, 2),
                       "RANGE": round(rg_days_all / total_days * 100, 2)},
        "cur_dist": cur_dist,
        "top_cur_up": top_cur(STATE_UP),
        "top_cur_down": top_cur(STATE_DOWN),
        "data_limitations": DATA_LIMIT,
    }
    # 普跌事件监控（H-R3 降格为描述性监控指标, 2026-11-20; 非信号非择时）
    seg_m = {}
    for _c, st, sd, ed, *_rest in all_segs:
        for yy in range(sd // 10000, ed // 10000 + 1):
            for mm in range(1, 13):
                m0, m1 = yy * 10000 + mm * 100 + 1, yy * 10000 + mm * 100 + 31
                if ed >= m0 and sd <= m1:
                    d = seg_m.setdefault(f"{yy}-{mm:02d}", {"DOWN": 0, "_": 0})
                    d["DOWN" if st == STATE_DOWN else "_"] += 1
    monthly_dn = {ym: v["DOWN"] / (v["DOWN"] + v["_"])
                  for ym, v in seg_m.items() if (v["DOWN"] + v["_"]) > 0}
    cur_ym = max(monthly_dn)
    snap["panic_monitor"] = {
        "active": bool(monthly_dn.get(cur_ym, 0) >= 0.55),
        "month": cur_ym, "down_share": round(monthly_dn.get(cur_ym, 0), 3),
        "rule": "月度下跌段占比≥55%=普跌事件(描述)",
        "baseline": "历史12个已完结事件后3个月全市场个股中位+2.7%, 75%为正(3次熊市中继为负)",
        "note": "描述性监控非信号(H-R3降格裁决, PORTRAIT-REGIME-HR1已证伪个股级跟随)",
    }
    with open(SNAP, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print(f"落库 {OUT_DB} | 快照 {SNAP} | 当前分布 UP={cur_dist['UP']} "
          f"DOWN={cur_dist['DOWN']} RANGE={cur_dist['RANGE']} | {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
