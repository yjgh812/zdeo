#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
炸板率 / 晋级率 情绪刻度校准器 (屠龙系统 P2)
================================================================
数据源:
  1) 主库 dragon_quant.db 表 stock_daily_hsj (code, date, open/high/low/close, vol)
     —— 用未复权价自查炸板: 当日 high>=涨停价 且 close<涨停价 = 炸板。
        涨停价 = round_half_up(昨收 x (1+涨幅限制), 2), 四舍五入到分:
          主板(00/60开头) 10%; 创业板(30开头)/科创板(68开头) 20%
        昨收 = 该 code 按日期排序的上一条收盘价(shift, 停牌日自然跳过)。
     —— 涨停封板: close>=涨停价; 触板: high>=涨停价。
  2) fundamental_data.db 表 feature_market_sentiment (Tushare口径涨停数) 做交叉核对。
  3) 主库 zt_pool (当日涨停池快照) 做当日数量核对。

输出:
  * 2024 起的日频: 封板数/炸板数/炸板率/晋级率/最高连板/情绪档位;
  * 最近 N(默认60) 个交易日情绪刻度表;
  * 研报刻度对照 (炸板率): <8% 情绪强(亢奋) | 8-30% 健康强势 | 30-50% 分歧 |
    >50% 退潮;  晋级率(昨涨停今再涨停比例) >30% 强接力 / <10% 退潮信号。

用法:
  python zt_sentiment_calibrator.py                     # 默认 20240102 起, 打印最近60日
  python zt_sentiment_calibrator.py --start 20240102 --days 60
  python zt_sentiment_calibrator.py --csv D:\\zcodesj\\reports\\zt_broken_rate_series.csv
  python zt_sentiment_calibrator.py --json out.json     # 供 M-6 情绪脚本对接

已知限制(实事求是):
  * 2026-08-15 起接入 price_limits(D:/zcodesj/price_limits.py)唯一口径
    (整数分 half-up + ST 规则 + 2026-07-06 主板ST 5%→10% 切换日),
    is_st 取主库 stock_info 当前快照 —— 历史时点的 ST 摘帽/戴帽状态是近似
    (现在摘帽的股票历史段仍按 ST 算);
  * 未复权价在除权除息日, "昨收"应为交易所的除权基准价, 本工具用原始昨收,
    除权日当天误判会被 vol/价格跳变部分掩盖, 误差很小但存在;
  * 新股上市首日无昨收, 自动排除; 北交所(4/8开头)与B股不在此刻度体系内。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from price_limits import numpy_limit_cents  # noqa: E402  涨停价唯一真理源

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"

# 研报/M-6 刻度体系: 炸板率分档
SCALE = [
    (0.08, "情绪强(亢奋)"),
    (0.30, "健康强势"),
    (0.50, "分歧"),
    (9.99, "退潮"),
]


def grade_broken_rate(br: float) -> str:
    if pd.isna(br):
        return "n/a"
    for th, name in SCALE:
        if br <= th:
            return name
    return SCALE[-1][1]


def grade_promotion(pr: float) -> str:
    if pd.isna(pr):
        return "n/a"
    if pr >= 0.30:
        return "强接力"
    if pr >= 0.15:
        return "中性"
    return "退潮信号"


def limit_cents(prev_cents: np.ndarray, code_prefix20: np.ndarray) -> np.ndarray:
    """
    [2026-08-15 已弃用, 保留仅作回退] 旧版: 仅区分10/20cm, 无ST/切换日。
    新代码一律用 price_limits.numpy_limit_cents。
    """
    num = np.where(code_prefix20, prev_cents * 12, prev_cents * 11)
    return (num + 5) // 10


def _load_is_st(db_path: str) -> dict:
    """主库 stock_info.is_st 当前快照 → {code: bool}。历史时点为近似。"""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute("SELECT code, is_st FROM stock_info").fetchall()
        con.close()
        return {c: bool(v) for c, v in rows if v is not None}
    except Exception as e:
        print(f"[warn] stock_info.is_st 拉取失败, ST 按非ST处理: {e}")
        return {}


def load_hsj(start: int, end: int | None = None) -> pd.DataFrame:
    """按 date 索引范围拉主库日线(只拉需要的列, 利用 idx_sdhs_date)。"""
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    q = "SELECT code,date,high,close,vol FROM stock_daily_hsj WHERE date>=?"
    params: list = [start]
    if end:
        q += " AND date<=?"
        params.append(end)
    cur = con.cursor()
    cur.execute(q, params)
    codes, dates, highs, closes, vols = [], [], [], [], []
    while True:
        rows = cur.fetchmany(500_000)
        if not rows:
            break
        for c, d, h, cl, v in rows:
            codes.append(c)
            dates.append(d)
            highs.append(h)
            closes.append(cl)
            vols.append(v)
    con.close()
    df = pd.DataFrame({"code": codes, "date": dates, "high": highs,
                       "close": closes, "vol": vols})
    return df


def compute_broken_board(df: pd.DataFrame, start: int) -> pd.DataFrame:
    """输入 load_hsj 的原始日线(含 start 前的缓冲期), 输出日频情绪序列。"""
    df = df[df["code"].str.match(r"^(00|30|60|68)")].copy()
    # 数据充足度守卫: 主库偶有下载不完整的残缺日(如 20260627 仅半数个股),
    # 会导致昨收错位、涨停/炸板误判, 当日行数低于滚动中位数 60% 时整日剔除。
    rows_per_day = df.groupby("date").size()
    med = float(rows_per_day.median())
    bad_days = rows_per_day[rows_per_day < 0.6 * med].index
    if len(bad_days):
        print(f"[warn] 剔除数据残缺日 {len(bad_days)} 天 (行数<60%中位数{med:.0f}): "
              f"{[int(b) for b in bad_days][:10]}")
        df = df[~df["date"].isin(bad_days)]
    df = df.sort_values(["code", "date"], kind="mergesort")
    df["prev_close"] = df.groupby("code")["close"].shift(1)
    df = df.dropna(subset=["prev_close"])
    df = df[df["date"] >= start].reset_index(drop=True)

    prev_cents = np.round(df["prev_close"].to_numpy() * 100).astype(np.int64)
    high_cents = np.round(df["high"].to_numpy() * 100).astype(np.int64)
    close_cents = np.round(df["close"].to_numpy() * 100).astype(np.int64)
    # 唯一口径: price_limits.numpy_limit_cents (10/20cm + ST 5%含2026-07-06切换日, half-up)
    is_st_map = _load_is_st(MAIN_DB)
    st_arr = df["code"].map(lambda c: bool(is_st_map.get(c, False))).to_numpy()
    lc = numpy_limit_cents(prev_cents, df["code"].to_numpy(), df["date"].to_numpy(dtype=np.int64), st_arr)

    tradable = (df["vol"].to_numpy() > 0) & (high_cents > 0)
    touched = tradable & (high_cents >= lc)
    sealed = touched & (close_cents >= lc)
    broken = touched & ~sealed

    df = df.assign(touched=touched, sealed=sealed, broken=broken)

    g = df.groupby("date")
    daily = pd.DataFrame({
        "n_touched": g["touched"].sum(),
        "n_sealed": g["sealed"].sum(),
        "n_broken": g["broken"].sum(),
    })
    # 零封板守卫: A股 2024 以来不存在"零封板且当日触板>=5家"的交易日。
    # 出现该形态说明源数据当日残缺(涨停股整批缺失, 如 20260627 两口径同时
    # 显示 limit_up=0), 剔除以免污染炸板率/晋级率。
    bad2 = daily[(daily["n_sealed"] == 0) & (daily["n_touched"] >= 5)]
    if len(bad2):
        print(f"[warn] 剔除零封板异常日 {len(bad2)} 天 (疑似源数据残缺, 涨停股缺失): "
              f"{[int(b) for b in bad2.index][:10]}")
        daily = daily.drop(bad2.index)
    daily["broken_rate"] = daily["n_broken"] / daily["n_touched"]

    # 晋级率代理: 昨日封板集合今日再封板的比例; 最高连板高度
    sealed_sets = df[df["sealed"]].groupby("date")["code"].apply(set)
    promo, max_streak = [], []
    streak: dict[str, int] = {}
    prev_set: set = set()
    for d in daily.index:
        cur_set = sealed_sets.get(d, set())
        promo.append(len(cur_set & prev_set) / len(prev_set) if prev_set else np.nan)
        for c in cur_set:
            streak[c] = streak.get(c, 0) + 1
        # 非涨停的清零(遍历当日全部交易股代价大, 用集合差近似即可)
        for c in streak:
            if c not in cur_set:
                streak[c] = 0
        max_streak.append(max(streak.values()) if streak else 0)
        prev_set = cur_set
    daily["promotion_rate"] = promo
    daily["max_streak"] = max_streak
    daily["grade_broken"] = daily["broken_rate"].apply(grade_broken_rate)
    daily["grade_promotion"] = daily["promotion_rate"].apply(grade_promotion)
    return daily.reset_index()


def cross_check(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """与 feature_market_sentiment 的 limit_up(Tushare口径) 对账, 返回对照与统计。"""
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    sent = pd.read_sql_query(
        "SELECT trade_date, limit_up, limit_down, total_stocks FROM feature_market_sentiment",
        con,
    )
    con.close()
    sent["date"] = sent["trade_date"].str.replace("-", "").astype(int)
    m = daily.merge(sent[["date", "limit_up", "total_stocks"]], on="date", how="inner")
    m = m[m["date"] >= 20240101]
    stat = {
        "对账天数": int(len(m)),
        "我的封板数-涨停数 均值差": float((m["n_sealed"] - m["limit_up"]).mean()),
        "我的封板数/涨停数 中位数": float((m["n_sealed"] / m["limit_up"]).median()),
        "相关系数": float(m["n_sealed"].corr(m["limit_up"])),
    }
    return m, stat


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="炸板率/晋级率情绪刻度校准")
    ap.add_argument("--start", type=int, default=20240102, help="起始日期 yyyymmdd")
    ap.add_argument("--end", type=int, default=None, help="结束日期, 默认库内最新")
    ap.add_argument("--days", type=int, default=60, help="打印最近 N 个交易日")
    ap.add_argument("--csv", default="", help="保存完整日频序列 CSV")
    ap.add_argument("--json", default="", help="保存最新一日指标 JSON(供M-6等消费)")
    args = ap.parse_args(argv)

    t0 = time.time()
    buf_start = int(str(args.start)[:4]) * 10000 + ((int(str(args.start)[4:6]) - 1) or 1) * 100 + 1
    # 简单缓冲: start 前 15 天
    d0 = pd.Timestamp(str(args.start)) - pd.Timedelta(days=15)
    buf_start = int(d0.strftime("%Y%m%d"))
    print(f"[load] stock_daily_hsj date>={buf_start} (含前15日缓冲, 主库date索引)...")
    raw = load_hsj(buf_start, args.end)
    print(f"[load] {len(raw):,} 行, {time.time()-t0:.0f}s")
    daily = compute_broken_board(raw, args.start)
    daily = daily[daily["n_touched"] >= 5].reset_index(drop=True)
    print(f"[calc] 有效交易日 {len(daily)} 天 ({daily['date'].min()}-{daily['date'].max()})")

    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        daily.to_csv(args.csv, index=False, encoding="utf-8-sig")
        print(f"[save] {args.csv}")

    # 交叉核对
    m, stat = cross_check(daily)
    print("\n=== 与 feature_market_sentiment (Tushare口径) 交叉核对 (2024起) ===")
    for k, v in stat.items():
        print(f"  {k}: {v}")
    tail10 = m.tail(10)[["date", "n_sealed", "limit_up", "n_broken", "broken_rate"]]
    print("\n最近10日封板数对账 (我方封板 vs Tushare涨停):")
    print(tail10.to_string(index=False,
                          formatters={"broken_rate": lambda x: f"{x:.1%}"}))

    # zt_pool 当日核对
    try:
        con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
        zt_cnt = con.execute("SELECT COUNT(*), MAX(seq) FROM zt_pool").fetchone()
        zt_prev_cnt = con.execute("SELECT COUNT(*) FROM zt_pool_previous").fetchone()[0]
        con.close()
        last = daily.iloc[-1]
        print(f"\n=== zt_pool 快照核对 ===")
        print(f"  zt_pool {zt_cnt[0]} 只 (seq最大{zt_cnt[1]}), zt_pool_previous {zt_prev_cnt} 只; "
              f"我方最新交易日 {last['date']} 封板 {int(last['n_sealed'])} 只 + 炸板 {int(last['n_broken'])} 只")
        print("  (zt_pool 为第三方池快照, 口径含 ST/新股时会有差异)")
    except Exception as e:  # pragma: no cover
        print(f"  zt_pool 核对失败: {e}")

    # 最近 N 日刻度表
    show = daily.tail(args.days).copy()
    show["date_s"] = show["date"].astype(str)
    print(f"\n=== 最近 {len(show)} 个交易日情绪刻度表 ===")
    print("(炸板率刻度: <8%情绪强 | 8-30%健康 | 30-50%分歧 | >50%退潮; "
          "晋级率: 昨日涨停今日再板比例)")
    cols = ["date_s", "n_sealed", "n_broken", "broken_rate",
            "promotion_rate", "max_streak", "grade_broken", "grade_promotion"]
    out = show[cols].rename(columns={"date_s": "date", "n_sealed": "封板",
                                     "n_broken": "炸板", "broken_rate": "炸板率",
                                     "promotion_rate": "晋级率", "max_streak": "最高连板",
                                     "grade_broken": "炸板档位",
                                     "grade_promotion": "晋级档位"})
    print(out.to_string(index=False,
                        formatters={"炸板率": lambda x: f"{x:.1%}",
                                    "晋级率": lambda x: ("" if pd.isna(x) else f"{x:.1%}"),
                                    "最高连板": lambda x: f"{int(x)}"}))

    # 当前状态结论
    last = daily.iloc[-1]
    br5 = daily["broken_rate"].tail(5).mean()
    pr5 = daily["promotion_rate"].tail(5).mean()
    print(f"\n=== 当前情绪状态 ({last['date']}) ===")
    print(f"  最新炸板率 {last['broken_rate']:.1%} ({last['grade_broken']}), "
          f"近5日均值 {br5:.1%}")
    print(f"  最新晋级率 {last['promotion_rate']:.1%} ({last['grade_promotion']}), "
          f"近5日均值 {pr5:.1%}")
    print(f"  最高连板 {int(last['max_streak'])} 板")
    print(f"  综合判读: 炸板率维度={last['grade_broken']}, 晋级率维度={last['grade_promotion']}")

    if args.json:
        payload = {
            "date": int(last["date"]),
            "n_sealed": int(last["n_sealed"]),
            "n_broken": int(last["n_broken"]),
            "broken_rate": round(float(last["broken_rate"]), 4),
            "promotion_rate": None if pd.isna(last["promotion_rate"])
            else round(float(last["promotion_rate"]), 4),
            "max_streak": int(last["max_streak"]),
            "grade_broken": last["grade_broken"],
            "grade_promotion": last["grade_promotion"],
            "broken_rate_5d": round(float(br5), 4),
            "scale": {str(th): name for th, name in SCALE[:-1]},
            "source": "zt_sentiment_calibrator (stock_daily_hsj 自查口径, 10%/20%涨停价)",
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"[save] {args.json}")
    print(f"\n[done] 总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
