# -*- coding: utf-8 -*-
"""证伪复核批次1 · 独立重实现(不复用原代码路径)

A) M2-UNLOCK-FILTER 全市场解禁窗事件研究
   档案口径(falsified_rules.M2-UNLOCK-FILTER): t0=解禁日前第10交易日买入, fwd10跨越解禁日,
   同日全市场等权基准超额, 按解禁占流通比(ratio)五分位, Q5-Q1 日度聚类t。
   原数字: 21,107事件 / Q1 -0.16% -> Q5 -2.27% / 日度聚类t=-8.46(1356日) / pooled Spearman=-0.101
   翻案判据(批次1报告): Q5-Q1 方向反转且 CI 不含0 -> 翻案候选; 方向一致重复 -> verified

B) MANTOU-CHASE-LIMITUP 涨停日追买全市场事件研究
   档案口径(六方互证): 涨停追买为负期望; 个案=交割单6笔fwd20中位-28.5%; 家族证据=涨停打板-1.89%等
   本检验: 涨停日判定代理(主板/中小>=9.8%, 创业>=19.8%), 入场A=当日收盘追入, 入场B=次日开盘追入,
   fwd20绝对收益 + 同窗市场超额(仅入场A), 分年中位数。
   翻案判据: 追入域 fwd20 中位转正且分年多数为正 -> 翻案候选

数据: dragon_quant_qfq.db (唯一真源, 只读) + event_calendar.db restricted_release_hist_em (只读)
输出: analysis/reaudit_batch1_20261020/reaudit_m2_unlock_result.json
      analysis/reaudit_batch1_20261020/reaudit_mantou_chase_result.json
fail-closed: 事件数/日历覆盖断言, 不足即 exit 1
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ECAL = r"D:\zcodesj\event_calendar.db"
PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")
OUT_A = os.path.join(HERE, "reaudit_m2_unlock_result.json")
OUT_B = os.path.join(HERE, "reaudit_mantou_chase_result.json")


def load_panel(start):
    con = sqlite3.connect("file:%s?mode=ro" % QFQ, uri=True)
    ph = ",".join("?" * len(PREFIXES))
    df = pd.read_sql(
        "SELECT code,date,open,close,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>=? AND substr(code,1,3) IN (%s)" % ph,
        con, params=(start,) + PREFIXES)
    con.close()
    df["date"] = df["date"].astype(str)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    assert len(df) > 5_000_000, "panel too small: %d" % len(df)
    return df


def daily_cluster_t(diff_by_day: pd.Series):
    d = diff_by_day.dropna()
    n = len(d)
    if n < 30:
        return {"n_days": n, "t": None, "mean": None}
    t = d.mean() / (d.std(ddof=1) / np.sqrt(n))
    return {"n_days": int(n), "t": round(float(t), 2), "mean": round(float(d.mean()) * 100, 3)}


def part_a():
    con = sqlite3.connect("file:%s?mode=ro" % ECAL, uri=True)
    ev = pd.read_sql(
        "SELECT float_date,code,ratio FROM restricted_release_hist_em", con)
    con.close()
    ev["code"] = ev["code"].astype(str).str.zfill(6)
    ev = ev[ev["code"].str[:3].isin(PREFIXES)].copy()
    ev["float_date"] = ev["float_date"].astype(str).str.replace("-", "", regex=False)  # 归一到qfq的YYYYMMDD
    assert len(ev) > 15000, "events too few: %d" % len(ev)

    df = load_panel("20150601")
    df["fwd10"] = df.groupby("code")["close"].shift(-10) / df["close"] - 1.0
    mkt = df.groupby("date")["fwd10"].mean().rename("mkt_fwd10")

    cal = np.array(sorted(df["date"].unique()))
    d2i = {d: i for i, d in enumerate(cal)}

    fpos = cal.searchsorted(ev["float_date"].values)
    ev["d_idx"] = fpos
    ev = ev[ev["d_idx"] >= 20].copy()  # 需 t0=d-10 且其前有数据
    ev["entry_date"] = cal[ev["d_idx"] - 10]
    ev["exit_date"] = cal[ev["d_idx"]]
    ev = ev.merge(mkt, left_on="entry_date", right_index=True, how="left")
    px = df[["code", "date", "close", "fwd10"]]
    ev = ev.merge(px.rename(columns={"date": "entry_date", "close": "entry_close"}),
                  on=["code", "entry_date"], how="left")
    ev = ev.dropna(subset=["fwd10", "mkt_fwd10", "ratio"]).copy()
    ev["excess"] = ev["fwd10"] - ev["mkt_fwd10"]
    assert len(ev) > 12000, "matched events too few: %d" % len(ev)

    ev["q"] = pd.qcut(ev["ratio"], 5, labels=False, duplicates="drop")
    qg = ev.groupby("q")["excess"].agg(["count", "mean", "median"])
    q1_days = ev[ev["q"] == 0].groupby("entry_date")["excess"].mean()
    q5_days = ev[ev["q"] == 4].groupby("entry_date")["excess"].mean()
    diff = (q5_days - q1_days).dropna()
    clu = daily_cluster_t(diff)
    rho = stats.spearmanr(ev["ratio"], ev["excess"])

    # 系统宇宙变体: t0 收盘<=30元
    sysu = ev[ev["entry_close"] <= 30].copy()
    qg_s = sysu.groupby("q")["excess"].agg(["count", "mean", "median"]) if len(sysu) > 5000 else None
    q1d_s = sysu[sysu["q"] == 0].groupby("entry_date")["excess"].mean()
    q5d_s = sysu[sysu["q"] == 4].groupby("entry_date")["excess"].mean()
    clu_s = daily_cluster_t((q5d_s - q1d_s).dropna()) if len(sysu) > 5000 else None

    res = {
        "id": "M2-UNLOCK-FILTER",
        "mode": "independent_reimpl",
        "n_events_raw": 22519,
        "n_events_universe": int(len(ev)),
        "window": [str(cal[0]), str(cal[-1])],
        "quintile_mean_pct": {int(k): round(float(v) * 100, 3) for k, v in qg["mean"].items()},
        "quintile_median_pct": {int(k): round(float(v) * 100, 3) for k, v in qg["median"].items()},
        "q5_minus_q1_daily_cluster": clu,
        "pooled_spearman": {"rho": round(float(rho.statistic), 4), "p": float(rho.pvalue)},
        "sysu_le30": {
            "n": int(len(sysu)),
            "quintile_mean_pct": {int(k): round(float(v) * 100, 3) for k, v in qg_s["mean"].items()} if qg_s is not None else None,
            "q5_minus_q1_daily_cluster": clu_s,
        },
        "caveats": [
            "宇宙=沪深主板/中小/创业前缀(剔688/北交), 与原21,107口径微差",
            "五分位=pooled qcut(ratio), 与原实现分位边界的微观差异可能存在",
            "独立重实现目标=方向与显著性复验, 非数字逐位对拍",
        ],
    }
    json.dump(res, open(OUT_A, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("A) M2-UNLOCK n=%d Q1=%+.3f%% Q5=%+.3f%% cluster=%s spearman=%.4f" % (
        len(ev), res["quintile_mean_pct"][0], res["quintile_mean_pct"][4], clu, rho.statistic))
    return res


def part_b():
    df = load_panel("20150601")
    df["pct"] = df.groupby("code")["close"].pct_change() * 100.0
    is_cyb = df["code"].str[:3].isin(["300", "301", "302"])
    thr = np.where(is_cyb, 19.8, 9.8)
    df["is_zt"] = df["pct"] >= thr

    df["close_fwd20"] = df.groupby("code")["close"].shift(-20)
    df["open_next"] = df.groupby("code")["open"].shift(-1)
    mkt = df.groupby("date")["close_fwd20"].mean()

    zt = df[df["is_zt"] & df["close_fwd20"].notna()].copy()
    zt["fwd20_close_entry"] = zt["close_fwd20"] / zt["close"] - 1.0
    bb = zt["open_next"].notna()
    zt_b = zt[bb].copy()
    zt_b["fwd20_nextopen_entry"] = zt_b["close_fwd20"] / zt_b["open_next"] - 1.0
    zt = zt.merge(mkt.rename("mkt_fwd20"), left_on="date", right_index=True, how="left")
    zt["excess_close_entry"] = zt["fwd20_close_entry"] - zt["mkt_fwd20"]
    zt["year"] = zt["date"].str[:4]

    def pack(s, mkt_col=None):
        out = {
            "n": int(s.notna().sum()),
            "mean_pct": round(float(s.mean()) * 100, 3),
            "median_pct": round(float(s.median()) * 100, 3),
            "hit_rate": round(float((s > 0).mean()), 4),
        }
        return out

    yearly = zt.groupby("year")["fwd20_close_entry"].median().mul(100).round(3)
    res = {
        "id": "MANTOU-CHASE-LIMITUP",
        "mode": "independent_reimpl",
        "window": [str(df["date"].min()), str(df["date"].max())],
        "entry_close_board": dict(pack(zt["fwd20_close_entry"]),
                                  excess_mean_pct=round(float(zt["excess_close_entry"].mean()) * 100, 3),
                                  excess_median_pct=round(float(zt["excess_close_entry"].median()) * 100, 3)),
        "entry_next_open": dict(pack(zt_b["fwd20_nextopen_entry"])),
        "yearly_median_close_entry_pct": {k: float(v) for k, v in yearly.items()},
        "caveats": [
            "涨停判定=日收盘涨幅阈值代理(主板9.8/创业19.8), ST(5%板)天然不入事件, 一字板不可买未剔除",
            "入场A=涨停日收盘追入为可交易性上界代理; 打板实际成交口径差异已在家族证据中各自处理",
        ],
    }
    json.dump(res, open(OUT_B, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("B) MANTOU n=%d close_entry med=%+.3f%% mean=%+.3f%% hit=%.3f | nextopen med=%+.3f%%" % (
        res["entry_close_board"]["n"], res["entry_close_board"]["median_pct"],
        res["entry_close_board"]["mean_pct"], res["entry_close_board"]["hit_rate"],
        res["entry_next_open"]["median_pct"]))
    return res


if __name__ == "__main__":
    part_a()
    part_b()
    print("done")
