#!/usr/bin/env python3
r"""
ERP 大盘估值闸门 (Equity Risk Premium Gate)
==========================================
方法论（华泰金工 2025-05）:
    ERP = 1/PE_TTM(沪深300) - 中国10年期国债收益率
    均值回归，信号稀疏，仅极端值触发。
    只对大盘域（上证50/沪深300）有效，对中证500/1000/2000为负贡献——
    本闸门只输出【大盘参考仓位】，不用于个股买卖闸门。

【信号方向说明 - 与任务书差异，实证纠正】
    任务书原文写的是 "<20%=买入区 / >80%=警戒区"，方向反了。
    ERP = 股票盈利收益率 - 债券收益率。ERP 高 => 股票相对债券便宜 => 买入区。
    历史拐点实证（沪深300, 2005-2026）:
      2007-10-16 大顶 ERP分位 0.2%   2015-06-08 杠杆牛顶 0.1%   2021-02-18 茅指数顶 0.7%
      2008-11-04 大底 98.4%          2020-03-23 疫情底  99.8%  2024-02-05 危机底  98.9%
    大顶全在分位<1%，大底全在分位>80%。回测亦双向验证（--backtest 可复现）。
    故本闸门采用: 分位>=80% => 买入区(大盘加仓参考)；<=20% => 警戒区(大盘减仓参考)。

数据:
    指数PE_TTM: akshare stock_index_pe_lg(乐咕乐股, 沪深300滚动市盈率)
                ⚠️2026-09 上游改版: 免费API仅返回月末+最新交易日快照(日频转VIP)。
                现方案: 真实PE锚点(乐咕月末/最新快照+本地库末行) 之间用
                隐含盈利E(=close/PE)线性插值 × 当日指数收盘(东财/新浪日K) 重构日频PE。
                锚点日=真值; 期间误差主要来自TTM盈利变动, 实测<1%PE, 对5年分位闸门影响可忽略。
    10年国债:   akshare bond_zh_us_rate(中国国债收益率10年, 日频, 正常)
    本地入库:   D:\zcodesj\erp_history.db 表 erp_daily(date/index_pe/erp[+bond_yield/erp_pct_5y])
    写入守卫:   新行数少于库内行数时拒绝覆盖(防上游粒度退化再次清空日频历史)

用法:
    python erp_gate.py             -> 当日ERP状态卡片（联网刷新，失败回退本地库并如实标注）
    python erp_gate.py --history   -> 近5年ERP分位走势关键节点
    python erp_gate.py --backtest  -> 历史回测: 分位>=80%%持有沪深300 vs 全时段持有 (双向对照)
    python erp_gate.py --json      -> JSON格式
    python erp_gate.py --offline   -> 只用本地库，不联网
"""

import argparse
import contextlib
import io
import json
import sqlite3
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

# ----------------------------- 配置 -----------------------------
DB_PATH = r"D:\zcodesj\erp_history.db"
INDEX_NAME = "沪深300"
ROLL_WINDOW = 1220          # 滚动5年交易日
ROLL_MIN_PERIODS = 250      # 至少1年数据才开始算分位
BUY_TH = 80.0               # 分位>=80% => 买入区(实证方向, 见文件头说明)
WARN_TH = 20.0              # 分位<=20% => 警戒区
COST_ONE_WAY = 0.001        # 回测单边成本 0.1%


# ----------------------------- 数据层 -----------------------------
def _quiet_fetch(func, **kwargs):
    """调用akshare并吞掉其内部tqdm进度条输出"""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        return func(**kwargs)


def fetch_daily_closes(start_ts, end_ts) -> pd.Series:
    """沪深300日收盘(东财index_zh_a_hist, 失败回退新浪stock_zh_index_daily); Series(date->close)"""
    import akshare as ak

    try:
        d = _quiet_fetch(
            ak.index_zh_a_hist, symbol="000300", period="daily",
            start_date=start_ts.strftime("%Y%m%d"), end_date=end_ts.strftime("%Y%m%d"),
        )
        s = pd.Series(d["收盘"].astype(float).values, index=pd.to_datetime(d["日期"]))
    except Exception:
        d = _quiet_fetch(ak.stock_zh_index_daily, symbol="sh000300")
        d["date"] = pd.to_datetime(d["date"])
        s = d.set_index("date")["close"].astype(float)
        s = s.loc[(s.index >= start_ts) & (s.index <= end_ts)]
    return s[~s.index.duplicated(keep="last")].sort_index()


def fetch_erp_frame(db: pd.DataFrame | None) -> pd.DataFrame:
    """合成ERP日频序列(2026-09乐咕免费API改月末粒度后的适配):
    真实PE锚点(乐咕月末+最新快照, 及本地库末行)之间以隐含盈利E=close/PE线性插值重构日频PE,
    国债收益率仍为bond_zh_us_rate日频。锚点日写入真值。"""
    import akshare as ak

    pe_raw = _quiet_fetch(ak.stock_index_pe_lg, symbol=INDEX_NAME)
    api_anchors = pe_raw.rename(columns={"日期": "date", "滚动市盈率": "pe", "指数": "close"})[
        ["date", "pe", "close"]
    ]
    api_anchors["date"] = pd.to_datetime(api_anchors["date"])

    bond_raw = _quiet_fetch(ak.bond_zh_us_rate, start_date="20050101")
    bond = bond_raw[["日期", "中国国债收益率10年"]].rename(
        columns={"日期": "date", "中国国债收益率10年": "bond_yield"}
    )
    bond["date"] = pd.to_datetime(bond["date"])
    bond = bond.dropna(subset=["bond_yield"]).set_index("date")["bond_yield"]

    if db is not None and not db.empty:
        last_date = db["date"].iloc[-1]
        anchor_pe = {last_date: float(db["index_pe"].iloc[-1])}   # 本地末行=真值锚点
        anchor_close = {last_date: float(db["close"].iloc[-1])}
    else:
        last_date = api_anchors["date"].min()
        anchor_pe, anchor_close = {}, {}
    for _, r in api_anchors[api_anchors["date"] > last_date].iterrows():
        anchor_pe[r["date"]] = float(r["pe"])
        anchor_close[r["date"]] = float(r["close"])
    if len(anchor_pe) < 2:
        return db  # 锚点无新增, 无需更新

    last_anchor = max(anchor_pe)
    closes_all = fetch_daily_closes(last_date, last_anchor)
    # 锚点收盘补全(锚点须有收盘才能参与插值): 优先日K源, 缺失回退乐咕自带当日指数收盘
    for d, p in anchor_pe.items():
        if d not in closes_all.index and d in anchor_close:
            closes_all.loc[d] = anchor_close[d]
    closes_all = closes_all.sort_index()
    e_anchor = {d: float(closes_all.loc[d]) / p
                for d, p in anchor_pe.items()
                if d in closes_all.index and pd.notna(closes_all.loc[d])}
    # 只写库内缺的日期(本地末行之后)
    closes = closes_all[(closes_all.index > last_date) & (closes_all.index <= last_anchor)]

    rows = []
    for d, c in closes.items():
        if d not in bond.index or pd.isna(bond.loc[d]):
            continue
        if d in e_anchor:
            pe = anchor_pe[d]  # 锚点日=真值
        else:
            earlier = [x for x in e_anchor if x <= d]
            later = [x for x in e_anchor if x >= d]
            if not earlier or not later:
                continue
            e0, e1 = earlier[-1], later[0]
            e = e_anchor[e0] if e0 == e1 else (
                e_anchor[e0] + (e_anchor[e1] - e_anchor[e0]) * (d - e0).days / (e1 - e0).days
            )
            pe = float(c) / e
        rows.append({"date": d, "index_pe": pe, "bond_yield": float(bond.loc[d]),
                     "close": float(c), "erp": 100.0 / pe - float(bond.loc[d])})

    new_df = pd.DataFrame(rows)
    if new_df.empty:
        return db
    # 量级守卫: 防混入异口径PE源(如东财加权口径37x)或单位错误
    ref = float(db["index_pe"].iloc[-1]) if db is not None and not db.empty else 13.0
    if not new_df["index_pe"].between(0.5 * ref, 1.5 * ref).all():
        raise RuntimeError(
            f"PE量级异常拒绝写入: 新{new_df['index_pe'].min():.2f}~{new_df['index_pe'].max():.2f} vs 本地末值{ref:.2f}"
        )
    frame = pd.concat([db, new_df], ignore_index=True) if db is not None and not db.empty else new_df
    return frame.dropna(subset=["erp"]).reset_index(drop=True)


def compute_pct(df: pd.DataFrame) -> pd.DataFrame:
    """滚动5年分位(含当日), 返回新增列 erp_pct_5y"""
    df = df.copy().reset_index(drop=True)
    df["erp_pct_5y"] = df["erp"].rolling(
        ROLL_WINDOW, min_periods=ROLL_MIN_PERIODS
    ).apply(lambda x: (x <= x[-1]).mean() * 100.0, raw=True)
    return df


def save_to_db(df: pd.DataFrame) -> int:
    """全量覆写入库 erp_daily; 返回行数"""
    out = df[["date", "index_pe", "erp", "bond_yield", "erp_pct_5y", "close"]].copy()
    out["date"] = out["date"].dt.strftime("%Y%m%d").astype(int)
    out["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(DB_PATH)
    try:
        try:
            prev = con.execute("SELECT COUNT(*) FROM erp_daily").fetchone()[0]
        except sqlite3.OperationalError:
            prev = 0
        if len(out) < prev:
            # 写入守卫(2026-09-22): 8/28起上游免费API退化为月末粒度曾把5193行日频历史
            # 覆盖成258行, 该守卫确保任何"缩水"的抓取结果都不会覆盖本地历史。
            raise RuntimeError(
                f"写入守卫: 新序列{len(out)}行 < 库内{prev}行, 拒绝覆盖(上游粒度退化或部分失败)"
            )
        con.execute("DROP TABLE IF EXISTS erp_daily")
        con.execute(
            """CREATE TABLE erp_daily (
                date INTEGER PRIMARY KEY,
                index_pe REAL,
                erp REAL,
                bond_yield REAL,
                erp_pct_5y REAL,
                index_close REAL,
                updated_at TEXT
            )"""
        )
        con.executemany(
            "INSERT INTO erp_daily VALUES (?,?,?,?,?,?,?)",
            out.itertuples(index=False, name=None),
        )
        con.commit()
        return len(out)
    finally:
        con.close()


def load_db() -> pd.DataFrame | None:
    con = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM erp_daily ORDER BY date", con)
    except pd.errors.DatabaseError:
        return None
    finally:
        con.close()
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    df = df.rename(columns={"index_close": "close"})
    return df


def get_data(offline: bool = False) -> tuple[pd.DataFrame, str]:
    """返回 (含分位的DataFrame, 数据来源说明)"""
    if not offline:
        db = load_db()
        try:
            df = fetch_erp_frame(db)
            df = compute_pct(df)
            n = save_to_db(df)
            src = (f"akshare实时拉取并入库({n}行, PE=乐咕真值锚点+指数收盘插值[免费API已改月末粒度], "
                   f"国债=bond_zh_us_rate)")
            return df, src
        except Exception as e:
            if db is None:
                raise RuntimeError(f"联网拉取失败且本地库不可用: {type(e).__name__}: {e}")
            db = compute_pct(db)
            src = f"联网失败回退本地库({type(e).__name__}: {str(e)[:120]}), 数据止于 {db['date'].iloc[-1].date()}"
            return db, src
    db = load_db()
    if db is None:
        raise RuntimeError("本地库不存在或为空, 请先联网运行一次 python erp_gate.py")
    db = compute_pct(db)
    src = f"本地库({DB_PATH}), 数据止于 {db['date'].iloc[-1].date()}"
    return db, src


# ----------------------------- 信号层 -----------------------------
def zone_of(pct: float) -> str:
    if pct >= BUY_TH:
        return "买入区"
    if pct <= WARN_TH:
        return "警戒区"
    return "中性"


def build_status(df: pd.DataFrame, src: str) -> dict:
    last = df.dropna(subset=["erp_pct_5y"]).iloc[-1]
    d0 = df.iloc[0]
    pct = float(last["erp_pct_5y"])
    zone = zone_of(pct)

    # 关键年份对比: 年末 + 关键大底
    key_rows = []
    for y in range(2019, int(last["date"].year) + 1):
        sub = df[df["date"] <= f"{y}-12-31"]
        if sub.empty:
            continue
        r = sub.iloc[-1]
        tag = "当前" if y == int(last["date"].year) else "年末"
        key_rows.append(
            {"时点": f"{r['date'].date()}({tag})", "沪深300": round(float(r["close"]), 0),
             "PE_TTM": round(float(r["index_pe"]), 2), "10Y国债%": round(float(r["bond_yield"]), 2),
             "ERP%": round(float(r["erp"]), 2), "5年分位%": (round(float(r["erp_pct_5y"]), 1) if pd.notna(r["erp_pct_5y"]) else None)}
        )
    for label, d in [("2021-02-18", "2021-02-18"), ("2024-02-05", "2024-02-05"), ("2024-09-23", "2024-09-23")]:
        r = df[df["date"] <= d].iloc[-1]
        key_rows.append(
            {"时点": f"{r['date'].date()}({label[5:]}关键点)", "沪深300": round(float(r["close"]), 0),
             "PE_TTM": round(float(r["index_pe"]), 2), "10Y国债%": round(float(r["bond_yield"]), 2),
             "ERP%": round(float(r["erp"]), 2), "5年分位%": round(float(r["erp_pct_5y"]), 1)}
        )

    # 历史极值
    hist = df.dropna(subset=["erp_pct_5y"])
    hi = hist.loc[hist["erp"].idxmax()]
    lo = hist.loc[hist["erp"].idxmin()]

    if zone == "买入区":
        advice = "大盘可加仓参考(仅大盘域: 沪深300/上证50; 不适用于中小盘, 不进个股闸门)"
    elif zone == "警戒区":
        advice = "大盘减仓/防守参考(仅大盘域; 不进个股闸门)"
    else:
        advice = "大盘中性, 不因ERP调整仓位(信号稀疏, 仅极端值触发)"

    return {
        "date": str(last["date"].date()),
        "index_close": round(float(last["close"]), 2),
        "index_pe": round(float(last["index_pe"]), 2),
        "earn_yield_pct": round(100.0 / float(last["index_pe"]), 2),
        "bond_yield_10y_pct": round(float(last["bond_yield"]), 3),
        "erp_pct": round(float(last["erp"]), 2),
        "erp_pctile_5y": round(pct, 1),
        "zone": zone,
        "advice": advice,
        "erp_5y_range": [round(float(hist[hist["date"] >= last["date"] - pd.Timedelta(days=365 * 5)]["erp"].min()), 2),
                          round(float(hist[hist["date"] >= last["date"] - pd.Timedelta(days=365 * 5)]["erp"].max()), 2)],
        "hist_max": {"date": str(hi["date"].date()), "erp": round(float(hi["erp"]), 2)},
        "hist_min": {"date": str(lo["date"].date()), "erp": round(float(lo["erp"]), 2)},
        "key_compare": key_rows,
        "data_start": str(d0["date"].date()),
        "source": src,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ----------------------------- --history -----------------------------
def history_report(df: pd.DataFrame) -> None:
    hist = df.dropna(subset=["erp_pct_5y"]).copy()
    hist["zone"] = hist["erp_pct_5y"].map(zone_of)
    five_years_ago = hist["date"].iloc[-1] - pd.Timedelta(days=365 * 5)
    recent = hist[hist["date"] >= five_years_ago].copy()

    print("=" * 78)
    print(f"  ERP 近5年分位走势关键节点 ({INDEX_NAME})")
    print(f"  ERP = 1/PE_TTM - 10Y国债 | 分位>= {BUY_TH:.0f}% 买入区, <= {WARN_TH:.0f}% 警戒区")
    print("=" * 78)

    # 1) 分区切换事件（过滤<5天的边界噪声碎段，另行计数说明）
    recent["zone_shift"] = (recent["zone"] != recent["zone"].shift()).cumsum()
    print("\n[分区切换事件] (仅列持续>=5天的片段; 短碎段系分位在阈值附近反复穿越, 已略)")
    print(f"  {'区间':<24}{'持续':>8}{'ERP区间%':>14}{'分位区间%':>12}{'沪深300区间涨跌':>16}")
    n_short = 0
    for _, g in recent.groupby("zone_shift"):
        if len(g) < 5:
            n_short += 1
            continue
        z = g["zone"].iloc[0]
        entry = g.iloc[0]
        exit_ = g.iloc[-1]
        days = len(g)
        ret = exit_["close"] / entry["close"] * 100.0 - 100.0
        print(f"  {str(entry['date'].date()):<11}~{str(exit_['date'].date()):<11} [{z}]"
              f"{days:>5}天  {entry['erp']:>6.2f}~{exit_['erp']:<6.2f}"
              f"{g['erp_pct_5y'].min():>6.1f}~{g['erp_pct_5y'].max():<5.1f}"
              f"{ret:>15.1f}%")
    if n_short:
        print(f"  (另有 {n_short} 个 <5天 的短碎段未列出)")

    # 2) 年末快照
    print("\n[年末快照]")
    print(f"  {'日期':<12}{'沪深300':>9}{'PE_TTM':>8}{'10Y%':>7}{'ERP%':>7}{'5年分位%':>9}  分区")
    for y in range(int(recent["date"].iloc[0].year), int(recent["date"].iloc[-1].year) + 1):
        sub = recent[recent["date"] <= f"{y}-12-31"]
        if sub.empty:
            continue
        r = sub.iloc[-1]
        print(f"  {str(r['date'].date()):<12}{r['close']:>9.1f}{r['index_pe']:>8.2f}"
              f"{r['bond_yield']:>7.2f}{r['erp']:>7.2f}{r['erp_pct_5y']:>9.1f}  {zone_of(r['erp_pct_5y'])}")

    # 3) 极值日
    print("\n[近5年极值日]")
    for tag, r in [("ERP最高", recent.loc[recent["erp"].idxmax()]),
                   ("ERP最低", recent.loc[recent["erp"].idxmin()]),
                   ("分位最高", recent.loc[recent["erp_pct_5y"].idxmax()]),
                   ("分位最低", recent.loc[recent["erp_pct_5y"].idxmin()])]:
        print(f"  {tag}: {r['date'].date()} ERP={r['erp']:.2f}% 分位={r['erp_pct_5y']:.1f}% 沪深300={r['close']:.1f}")
    print()


# ----------------------------- --backtest -----------------------------
def _perf(ret: pd.Series, pos: pd.Series, cost: float) -> dict:
    """pos(t)为t日收盘持仓(由t-1信号决定); 成本在换仓日扣"""
    churn = pos.diff().abs().fillna(pos.abs())
    strat_ret = ret * pos - churn * cost
    equity = (1 + strat_ret).cumprod()
    years = len(ret) / 244.0
    total = equity.iloc[-1] - 1
    cagr = equity.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    dd = (equity / equity.cummax() - 1).min()
    vol = strat_ret.std() * np.sqrt(244)
    exposure = float((pos > 0).mean())
    return {"total_ret_pct": round(total * 100, 1), "cagr_pct": round(cagr * 100, 2),
            "max_dd_pct": round(dd * 100, 1), "vol_pct": round(vol * 100, 1),
            "exposure_pct": round(exposure * 100, 1),
            "episodes": int((pos > 0).ne((pos > 0).shift()).cumsum()[pos > 0].nunique())}


def backtest_report(df: pd.DataFrame) -> dict:
    hist = df.dropna(subset=["erp_pct_5y"]).reset_index(drop=True)
    ret = hist["close"].pct_change().fillna(0.0)

    # 实证方向: 分位>=80% 持有(买入区), 其余空仓
    sig_hi = (hist["erp_pct_5y"] >= BUY_TH).astype(int).shift(1).fillna(0)
    # 任务书原方向(对照): 分位<=20% 持有
    sig_lo = (hist["erp_pct_5y"] <= WARN_TH).astype(int).shift(1).fillna(0)

    # 前向收益事件研究(干净的预测性检验, 与退出路径无关): 每个连续进区片段的首日
    def fwd_episodes(zone_mask, horizon=250):
        grp_key = (zone_mask != zone_mask.shift()).cumsum()
        fwd_col = hist["close"].shift(-horizon) / hist["close"] - 1
        out = []
        for _, g in hist.groupby(grp_key):
            if not zone_mask.loc[g.index[0]]:
                continue
            i = g.index[0]
            out.append({"date": str(hist["date"].iloc[i].date()),
                        "days": len(g), "fwd": fwd_col.iloc[i]})
        return out

    eps_hi = fwd_episodes(hist["erp_pct_5y"] >= BUY_TH)
    eps_lo = fwd_episodes(hist["erp_pct_5y"] <= WARN_TH)
    fwd_uncond = (hist["close"].shift(-250) / hist["close"] - 1).dropna()

    def _fwd_summary(eps):
        s = pd.Series([e["fwd"] for e in eps]).dropna()
        if s.empty:
            return {}
        return {"n_episodes": len(s), "mean_fwd250_pct": round(s.mean() * 100, 1),
                "median_fwd250_pct": round(s.median() * 100, 1),
                "win_rate_pct": round((s > 0).mean() * 100, 0)}

    # 子期对照(2016起, 滚动窗口基期效应消除后的现代样本)
    m = (hist["date"] >= pd.Timestamp("2016-01-01")).values
    r2 = ret[m]
    sub = {
        "buy_hold": _perf(r2, pd.Series(1, index=hist.index)[m], 0.0),
        "hold_ge80": _perf(r2, sig_hi[m], COST_ONE_WAY),
        "hold_le20": _perf(r2, sig_lo[m], COST_ONE_WAY),
        "fwd_ge80": _fwd_summary([e for e in eps_hi if e["date"] >= "2016-01-01"]),
        "fwd_le20": _fwd_summary([e for e in eps_lo if e["date"] >= "2016-01-01"]),
    }

    return {
        "period": f"{hist['date'].iloc[0].date()} ~ {hist['date'].iloc[-1].date()}",
        "buy_hold": _perf(ret, pd.Series(1, index=hist.index), 0.0),
        "hold_when_pct_ge80": _perf(ret, sig_hi, COST_ONE_WAY),
        "hold_when_pct_le20": _perf(ret, sig_lo, COST_ONE_WAY),
        "fwd_event_study": {
            "pct_ge80_episodes": _fwd_summary(eps_hi),
            "pct_le20_episodes": _fwd_summary(eps_lo),
            "unconditional_fwd250": {"mean_pct": round(fwd_uncond.mean() * 100, 1),
                                     "median_pct": round(fwd_uncond.median() * 100, 1)},
            "pct_ge80_detail": eps_hi,
            "pct_le20_detail": eps_lo,
        },
        "subperiod_2016plus": sub,
        "cost_one_way_pct": COST_ONE_WAY * 100,
        "note": "持仓规则: T-1日收盘信号 -> T日持仓; 空仓期收益按0计(未计货基收益); 单边成本0.1%",
    }


def print_backtest(df: pd.DataFrame) -> None:
    r = backtest_report(df)
    print("=" * 78)
    print(f"  ERP闸门历史回测 ({INDEX_NAME})  {r['period']}")
    print(f"  规则: T-1收盘信号->T日持仓, 空仓收益0, 单边成本{r['cost_one_way_pct']:.2f}%")
    print("=" * 78)
    rows = [
        ("全时段持有(基准)", r["buy_hold"]),
        ("ERP分位>=80%时持有(买入区, 实证方向)", r["hold_when_pct_ge80"]),
        ("ERP分位<=20%时持有(任务书原方向, 对照)", r["hold_when_pct_le20"]),
    ]
    print(f"\n  {'策略':<34}{'年化%':>8}{'总收益%':>9}{'最大回撤%':>10}{'波动%':>8}{'持仓占比%':>10}{'进场次数':>8}")
    for name, p in rows:
        print(f"  {name:<34}{p['cagr_pct']:>8.2f}{p['total_ret_pct']:>9.1f}{p['max_dd_pct']:>10.1f}"
              f"{p['vol_pct']:>8.1f}{p['exposure_pct']:>10.1f}{p['episodes']:>8}")

    s = r["subperiod_2016plus"]
    print(f"\n  [子期 2016-01 起 (滚动5年窗口基期效应消除后的现代样本)]")
    print(f"  {'全时段持有':<34}{s['buy_hold']['cagr_pct']:>8.2f}{s['buy_hold']['total_ret_pct']:>9.1f}{s['buy_hold']['max_dd_pct']:>10.1f}")
    print(f"  {'>=80%%持有':<34}{s['hold_ge80']['cagr_pct']:>8.2f}{s['hold_ge80']['total_ret_pct']:>9.1f}{s['hold_ge80']['max_dd_pct']:>10.1f}")
    print(f"  {'<=20%持有':<34}{s['hold_le20']['cagr_pct']:>8.2f}{s['hold_le20']['total_ret_pct']:>9.1f}{s['hold_le20']['max_dd_pct']:>10.1f}")

    f = r["fwd_event_study"]
    print(f"\n  [进区日前向250日收益事件研究 (预测性检验, 与退出路径无关)]")
    u = f["unconditional_fwd250"]
    print(f"  无条件基准: 均值 {u['mean_pct']}% / 中位数 {u['median_pct']}%")
    for tag, key in ([(">=80% 买入区进区日", "pct_ge80_episodes"), ("<=20% 警戒区进区日", "pct_le20_episodes")]):
        p = f[key]
        print(f"  {tag}: {p.get('n_episodes', 0)}段, 前向250日 均值 {p.get('mean_fwd250_pct')}% / "
              f"中位数 {p.get('median_fwd250_pct')}% / 胜率 {p.get('win_rate_pct')}%")
    for tag, key in ([(">=80% 进区日(2016后)", "fwd_ge80"), ("<=20% 进区日(2016后)", "fwd_le20")]):
        p = s[key]
        if p:
            print(f"  {tag}: {p.get('n_episodes', 0)}段, 前向250日 均值 {p.get('mean_fwd250_pct')}% / "
                  f"中位数 {p.get('median_fwd250_pct')}% / 胜率 {p.get('win_rate_pct')}%")
    print("\n  实证解读:")
    print("  1) 拐点: 历史大顶(2007/2015/2021.2)分位均<1%, 大底(2008/2020.3/2024.2)均>80% -> 高ERP=便宜=买入区")
    print("  2) 2016后现代样本: <=20%持有为负收益, >=80%%持有≈基准但最大回撤减半(-21.5% vs -45.6%)")
    print("  3) 全期<=20%持有CAGR(6.0%)虚高, 系2006-08两段melt-up贡献(5年窗口被2005熊市极值污染), 不可外推")
    print("  4) 持有型回测受退出路径拖累(V型反转甩下车+空仓0收益): ERP应作仓位参考闸门, 非独立策略")
    print()


# ----------------------------- CLI -----------------------------
def print_status(st: dict) -> None:
    z = st["zone"]
    bar_len = 40
    pos = int(st["erp_pctile_5y"] / 100 * bar_len)
    bar = "." * pos + "|" + "." * (bar_len - pos - 1)
    mark = {">=80 买入区", "<=20 警戒区"}
    print("=" * 78)
    print(f"  ERP 大盘估值闸门 | {INDEX_NAME} | {st['date']} (最近交易日)")
    print(f"  生成时间: {st['timestamp']}")
    print("=" * 78)
    print(f"""
  ERP = 1/PE_TTM - 10Y国债收益率
       = 100/{st['index_pe']:.2f} - {st['bond_yield_10y_pct']:.3f}% = {st['earn_yield_pct']:.2f}% - {st['bond_yield_10y_pct']:.3f}% = {st['erp_pct']:.2f}%

  5年滚动分位: {st['erp_pctile_5y']:.1f}%
  0%[{'<' + bar + '>':^50}]100%
  {' ' * (4 + int(st['erp_pctile_5y'] / 100 * bar_len))}^ 当前{st['erp_pctile_5y']:.1f}%
     {WARN_TH:<20.0f}{50:^20}{BUY_TH:>20.0f}(阈值: <=20 警戒 | 中性 | >=80 买入)

  >>> 三档判定: 【{z}】
  >>> 仓位参考: {st['advice']}

  近5年ERP区间: {st['erp_5y_range'][0]:.2f}% ~ {st['erp_5y_range'][1]:.2f}%
  历史极值: 最高 {st['hist_max']['erp']:.2f}% ({st['hist_max']['date']})
            最低 {st['hist_min']['erp']:.2f}% ({st['hist_min']['date']})""")
    print("  关键年份对比:")
    print(f"    {'时点':<26}{'沪深300':>8}{'PE_TTM':>8}{'10Y%':>7}{'ERP%':>7}{'5年分位%':>9}")
    for k in st["key_compare"]:
        p = k["5年分位%"]
        p_str = f"{p:.1f}" if p is not None else "-"
        print(f"    {k['时点']:<26}{k['沪深300']:>8.0f}{k['PE_TTM']:>8.2f}{k['10Y国债%']:>7.2f}{k['ERP%']:>7.2f}{p_str:>9}")
    print(f"""
  数据来源: {st['source']}
  数据起点: {st['data_start']}
  边界: 仅大盘域参考信号, 不进个股买卖闸门(中小盘实证为负贡献), 不修改bp+ep策略
  方向说明: 分位>=80%=买入区(ERP高=股比债便宜), 与任务书原文相反, 依据历史拐点+回测实证, 详见报告""")


def main():
    ap = argparse.ArgumentParser(description="ERP大盘估值闸门")
    ap.add_argument("--history", action="store_true", help="打印近5年ERP分位走势关键节点")
    ap.add_argument("--backtest", action="store_true", help="历史回测: 分位>=80%%持有 vs 全时段持有(双向对照)")
    ap.add_argument("--json", action="store_true", help="JSON格式输出")
    ap.add_argument("--offline", action="store_true", help="只用本地库, 不联网")
    args = ap.parse_args()

    t0 = time.time()
    df, src = get_data(offline=args.offline)

    if args.history:
        history_report(df)
        print(f"[耗时 {time.time() - t0:.1f}s | {src}]")
        return
    if args.backtest:
        r = backtest_report(df)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            print_backtest(df)
            print(f"[耗时 {time.time() - t0:.1f}s | {src}]")
        return

    st = build_status(df, src)
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        print_status(st)
        print(f"\n[耗时 {time.time() - t0:.1f}s]")


if __name__ == "__main__":
    main()
