# -*- coding: utf-8 -*-
"""
vibe_card.py — 每日盘感/市场氛围卡片 v2（把"凭感觉"变成"可核对的读数"）
====================================================================
v2（2026-08-22，盘感模块全面落地）：在 v1 三维基础上并入 P0 已有件 + P1 进阶维度。

五维合成（权重公开，可复核）：
  ① 情绪复合 sentiment (40%) —— 复用 sentiment_composite.today_signal()
      涨停活跃30% + 连板强度25% + 炸板质量20% + 跌停压力15% + 涨跌比10%
  ② 打板溢价 premium (20%) —— 昨日涨停今日均涨幅（SC-001 情绪最灵敏温度计，
      复用 prem_seal_daily.compute；≤0 触发亏钱效应/熔断标注）
  ③ 量能 volume (15%) —— 全市场成交额 当日 vs 近5日均
  ④ 指数动能 index (15%) —— 上证/深成/创业板 均线结构+5日涨跌
  ⑤ 连板梯队 ladder (10%) —— 2/3/4/5板家数分布 + 连板高度

每张卡片额外输出（不计入0-100分，供判读）：
  - cycle_stage 情绪周期阶段（启动/主升/分歧/退潮，量化阈值）
  - percentile 盘感分近250交易日历史分位（自适应档位参考）
  - momentum 情绪动量（5日均 + 连续上升/下降天数）
  - divergence 情绪-指数背离预警
  - sc001 SC-001 熔断（prem_seal≤0 亏钱效应；rolling20≤0 空仓提示）

反臆想铁律（强制）：
  - 每张卡片必须带 data_date（合成所依据的数据日），绝不冒充"今天/实时"。
  - 各维度原始值全量透出，便于复核；缺失维度显式标注，不静默填数。
  - hsj 与 index_daily 数据日不一致 → freshness.warn；--expected-date 超期 → 滞后标注。

用法：
  python vibe_card.py                     # 自动取本地最新数据日
  python vibe_card.py --expected-date 20260821
  python vibe_card.py --json reports/vibe_card_YYYYMMDD.json
  python vibe_card.py --line               # 单行摘要（供作战清单/其他脚本消费）
  python vibe_card.py --append             # 生成卡片 + 追加历史序列(幂等) + 写 vibe_card_latest.json
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
import sentiment_composite as sc  # noqa: E402  情绪复合（只读）
import prem_seal_daily as psd     # noqa: E402  打板溢价（只读）
import seal_snapshot as ssnap     # noqa: E402  真封板率（只读）
import price_limits               # noqa: E402  涨停价唯一真理源

MAIN_DB = sc.HSJ_DB
INDEX_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
CACHE = Path(r"D:\zcodesj\_cache")
HISTORY_CSV = CACHE / "vibe_history.csv"
REPORT_DIR = Path(r"D:\zcodesj\reports")
LATEST_JSON = REPORT_DIR / "vibe_card_latest.json"
PREM_LATEST = REPORT_DIR / "prem_seal_latest.json"

# 权重（公开）v2
W = {"sentiment": 0.40, "premium": 0.20, "volume": 0.15, "index": 0.15, "ladder": 0.10}

# 指数动能监控（代码, 名称, 权重）
IDX_WATCH = [("000001", "上证指数", 0.30), ("399001", "深证成指", 0.35),
             ("399006", "创业板指", 0.35)]


# ═══════════════════════════════════════════════════════
#  量能维度
# ═══════════════════════════════════════════════════════

def volume_dim(hsj_date: int, lookback: int = 6) -> dict:
    """当日全市场成交额 vs 近5日均值。返回分值+原始值。
    ⚠️ 必须按 A 股白名单前缀过滤（60/00/30/68/92）：hsj 个别历史日混入 88/15/51/16
    等 ETF/LOF/指数代码，其巨额 amt 会把 SUM 撑爆 25-35 倍（2026-08-22 回填时发现
    7/28、8/6、8/7、8/11 四天被污染）。与 sentiment_composite.load_hsj_clean 同口径。"""
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=120)
    rows = con.execute(
        "SELECT date, SUM(amt) FROM stock_daily_hsj "
        "WHERE date>=? AND close>0 "
        "AND substr(code,1,2) IN ('60','00','30','68','92') "
        "GROUP BY date ORDER BY date",
        (int(str(hsj_date)[:4]) * 10000,)).fetchall()
    con.close()
    amts = pd.Series({int(d): a for d, a in rows}).sort_index()
    amts = amts[amts.index <= hsj_date].tail(lookback)
    if len(amts) < 2 or hsj_date not in amts.index:
        return {"score": 50.0, "ratio": None, "amt_today": None,
                "amt_5d_avg": None, "days": len(amts)}
    today = amts.iloc[-1]
    base = amts.iloc[:-1].mean()
    ratio = float(today / base) if base > 0 else None
    if ratio is None:
        score = 50.0
    elif ratio >= 1.30:
        score = 100.0
    elif ratio >= 1.10:
        score = 80.0
    elif ratio >= 0.95:
        score = 60.0
    elif ratio >= 0.85:
        score = 35.0
    else:
        score = 15.0
    return {"score": score, "ratio": None if ratio is None else round(ratio, 3),
            "amt_today": None if today != today else float(today),
            "amt_5d_avg": None if base != base else float(base), "days": len(amts)}


# ═══════════════════════════════════════════════════════
#  指数动能维度
# ═══════════════════════════════════════════════════════

def index_dim(asof: int) -> dict:
    """三指数均线结构+5日涨跌 → 0-100 动能分。"""
    con = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True, timeout=120)
    detail = []
    scores = []
    latest = None
    for code, name, w in IDX_WATCH:
        rows = con.execute(
            "SELECT date, close FROM index_daily WHERE index_code=? AND date<=? "
            "ORDER BY date DESC LIMIT 25", (code, asof)).fetchall()
        if len(rows) < 6:
            detail.append({"code": code, "name": name, "ok": False})
            continue
        s = pd.Series({int(d): c for d, c in rows}).sort_index()
        latest = latest or int(s.index[-1])
        close = float(s.iloc[-1])
        ma5 = float(s.tail(5).mean())
        ma20 = float(s.tail(20).mean())
        close5 = float(s.iloc[-6]) if len(s) >= 6 else close
        pct5 = close / close5 - 1 if close5 > 0 else 0.0
        if close > ma5 > ma20:
            trend = 100.0
        elif close > ma5:
            trend = 60.0
        elif close > ma20:
            trend = 40.0
        else:
            trend = 0.0
        if pct5 >= 0.02:
            mom = 100.0
        elif pct5 >= 0.0:
            mom = 60.0
        elif pct5 >= -0.02:
            mom = 30.0
        else:
            mom = 0.0
        idx = 0.6 * trend + 0.4 * mom
        scores.append(idx * w)
        detail.append({"code": code, "name": name, "ok": True, "close": round(close, 2),
                       "ma5": round(ma5, 2), "ma20": round(ma20, 2),
                       "pct5": round(pct5 * 100, 2), "trend_score": trend,
                       "mom_score": mom, "score": round(idx, 1)})
    con.close()
    total = round(sum(scores), 1) if scores else 50.0
    return {"score": total, "detail": detail, "latest_date": latest}


# ═══════════════════════════════════════════════════════
#  打板溢价维度（SC-001 温度计）
# ═══════════════════════════════════════════════════════

def premium_dim(hsj_date: int) -> dict:
    """昨日涨停今日均涨幅 → 0-100 分 + SC-001 熔断标注。"""
    try:
        r = psd.compute(str(hsj_date))
    except Exception as e:  # pragma: no cover
        return {"score": 50.0, "ok": False, "prem_seal": None, "reason": str(e)}
    if not r or r["prem_seal"] is None:
        return {"score": 50.0, "ok": False, "prem_seal": None, "count": 0}
    ps = r["prem_seal"]
    if ps >= 2:
        score, label = 100.0, "发酵/主升"
    elif ps >= 0.5:
        score, label = 70.0, "温和偏强"
    elif ps >= 0:
        score, label = 50.0, "临界"
    elif ps >= -1:
        score, label = 30.0, "亏钱效应"
    else:
        score, label = 0.0, "严重亏钱"
    # rolling20 熔断（读 prem_seal 历史序列）
    rolling20 = None
    if PREM_LATEST.exists():
        try:
            pj = json.loads(PREM_LATEST.read_text(encoding="utf-8"))
            rolling20 = pj.get("rolling20")
        except Exception:
            pass
    return {"score": score, "ok": True, "prem_seal": ps, "label": label,
            "count": r["count"], "up": r["up"], "down": r["down"],
            "best": f"{r['best'][1]}({r['best'][2]:+.1f}%)",
            "worst": f"{r['worst'][1]}({r['worst'][2]:+.1f}%)",
            "sc001_circuit_breaker": ps <= 0,
            "sc001_rolling20": rolling20,
            "sc001_empty_advice": rolling20 is not None and rolling20 <= 0}


# ═══════════════════════════════════════════════════════
#  连板梯队维度
# ═══════════════════════════════════════════════════════

def ladder_dim(hsj_date: int) -> dict:
    """今日连板梯队：2/3/4/5板+家数分布 + 强度分（price_limits 唯一口径）。"""
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=120)
    dates = [d for (d,) in con.execute(
        "SELECT DISTINCT date FROM stock_daily_hsj WHERE date<=? AND date>? "
        "ORDER BY date DESC LIMIT 8", (hsj_date, int(str(hsj_date)[:4]) * 10000)).fetchall()]
    dates = sorted(dates)
    if len(dates) < 2:
        con.close()
        return {"score": 50.0, "ok": False, "reason": "数据不足"}
    ph = ",".join("?" * len(dates))
    rows = con.execute(
        f"SELECT code, date, close FROM stock_daily_hsj "
        f"WHERE date IN ({ph}) AND close>0", dates).fetchall()
    con.close()
    df = pd.DataFrame(rows, columns=["code", "date", "close"])
    df["code"] = df["code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(["00", "30", "60", "68", "92"])]
    df = df.sort_values(["code", "date"])
    df["prev_close"] = df.groupby("code")["close"].shift(1)
    df = df.dropna(subset=["prev_close"])
    if len(df) == 0:
        return {"score": 50.0, "ok": False, "reason": "无白名单数据"}
    prev_cents = np.rint(df["prev_close"].to_numpy() * 100).astype(np.int64)
    lc = price_limits.numpy_limit_cents(
        prev_cents, df["code"].to_numpy(), df["date"].to_numpy(dtype=np.int64),
        None, up=True) / 100.0
    df["is_lu"] = df["close"].to_numpy() >= lc - 1e-3
    df["prev_lu"] = df.groupby("code")["is_lu"].shift(1, fill_value=False).astype(bool)
    lu = df["is_lu"].to_numpy()
    plu = df["prev_lu"].to_numpy()
    streak = np.zeros(len(df), dtype=np.int32)
    for i in range(1, len(df)):
        if lu[i]:
            streak[i] = streak[i - 1] + 1 if plu[i] else 1
    df["streak"] = streak
    today = df[(df["date"] == hsj_date) & df["is_lu"]]
    ladder = {k: int((today["streak"] >= k).sum()) for k in (2, 3, 4, 5)}
    n_5plus = int((today["streak"] >= 5).sum())
    max_b = int(today["streak"].max()) if len(today) else 0
    if max_b >= 5:
        base = 80.0
    elif max_b >= 4:
        base = 65.0
    elif max_b >= 3:
        base = 50.0
    elif max_b == 2:
        base = 35.0
    else:
        base = 20.0
    n3 = ladder[3] + ladder[4] + ladder[5]
    score = min(100.0, base + min(20.0, n3 * 5))
    return {"score": round(score, 1), "ok": True, "max_boards": max_b,
            "ladder": ladder, "n_5b_plus": n_5plus, "n_3b_plus": n3}


# ═══════════════════════════════════════════════════════
#  情绪周期阶段 + 动量 + 分位（复用 sentiment_composite 日频序列）
# ═══════════════════════════════════════════════════════

def cycle_stage(s: dict) -> str:
    """四阶段：主升/启动/分歧/退潮（实战量化阈值，非主观）。"""
    lu = s["n_limit_up"]
    mb = s["max_boards"]
    br = s["broken_rate_pct"] if s["broken_rate_pct"] is not None else 50
    ld = s["n_limit_down"]
    if lu > 60 and mb >= 5 and br < 30 and ld < 10:
        return "主升"
    if 30 <= lu <= 60 and br < 35 and ld < 10:
        return "启动"
    if lu < 30 or br > 50 or ld > 30:
        return "退潮"
    return "分歧"


def sentiment_series() -> pd.DataFrame:
    """sentiment_composite 日频序列（date, score, stage）——供分位/动量。"""
    daily = sc.compute_daily_sentiment()
    return daily[["date", "score"]].copy()


def momentum_and_percentile(daily: pd.DataFrame, hsj_date: int) -> tuple[dict, float | None]:
    """情绪动量（5日均+连续升降）+ 当前分位（近250交易日）。"""
    d = daily.sort_values("date")
    d = d[d["date"] <= hsj_date]
    if len(d) < 10:
        return {"ma5": None, "trend_days": 0, "change": None}, None
    today = float(d["score"].iloc[-1])
    ma5 = float(d["score"].tail(5).mean())
    change = round(today - float(d["score"].iloc[-2]), 1) if len(d) >= 2 else None
    # 连续上升/下降天数
    vals = d["score"].to_numpy()
    trend_days = 0
    if len(vals) >= 2:
        if vals[-1] > vals[-2]:
            k = -2
            while k >= -len(vals) and vals[k] > vals[k - 1]:
                trend_days += 1
                k -= 1
        elif vals[-1] < vals[-2]:
            k = -2
            while k >= -len(vals) and vals[k] < vals[k - 1]:
                trend_days -= 1
                k -= 1
    # 近250日分位
    win = d["score"].tail(250)
    percentile = round(float((win < today).mean() * 100), 1)
    return {"ma5": round(ma5, 1), "trend_days": trend_days, "change": change}, percentile


def divergence_warn(senti_score: float, index_score: float, prem: dict) -> str | None:
    """情绪-指数背离预警。"""
    ps = prem.get("prem_seal")
    if index_score >= 60 and (senti_score < 40 or (ps is not None and ps <= 0)):
        return "指数强但情绪弱/亏钱效应 → 背离(指数或虚涨)，追高谨慎"
    if senti_score >= 60 and index_score < 40:
        return "情绪热但指数弱 → 背离(情绪透支)，防退潮"
    return None


# ═══════════════════════════════════════════════════════
#  板块/主线热度维度（最强风口）
# ═══════════════════════════════════════════════════════

def sector_heat_dim(hsj_date: int) -> dict:
    """data_date 涨停股按行业统计 → Top板块 + 主线清晰度（不参与主分计分）。
    行业归属取主库 stock_info.industry（东财/通达信口径），涨停判定 price_limits 唯一口径。"""
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=120)
    prev = con.execute("SELECT MAX(date) FROM stock_daily_hsj WHERE date<?", (hsj_date,)).fetchone()[0]
    if not prev:
        con.close()
        return {"ok": False, "reason": "无前日数据"}
    rows = con.execute(
        "SELECT t.code, t.close, y.close FROM stock_daily_hsj t "
        "JOIN stock_daily_hsj y ON t.code=y.code AND y.date=? WHERE t.date=?",
        (prev, hsj_date)).fetchall()
    info = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT code, name, industry FROM stock_info").fetchall()}
    con.close()
    lu = []  # (code, name, industry)
    for code, close, yclose in rows:
        c6 = str(code)[:6]
        if c6[:2] not in ("00", "30", "60", "68", "92"):
            continue
        if not yclose or yclose <= 0:
            continue
        try:
            prev_cents = int(round(float(yclose) * 100))
            lc = price_limits.numpy_limit_cents(
                np.array([prev_cents]), np.array([c6]), np.array([hsj_date], dtype=np.int64),
                None, up=True)[0] / 100.0
        except Exception:
            continue
        if float(close) >= lc - 1e-3:
            name, ind = info.get(c6, ("", "未知"))
            lu.append((c6, name, ind or "未知"))
    if not lu:
        return {"ok": False, "n_limit": 0}
    from collections import Counter, defaultdict
    by_ind = Counter(ind for _, _, ind in lu)
    top = by_ind.most_common(6)
    names_by_ind = defaultdict(list)
    for c6, name, ind in lu:
        if name:
            names_by_ind[ind].append(name)
    top_detail = [{"industry": ind, "n": n,
                   "leaders": names_by_ind[ind][:3]} for ind, n in top]
    n_total = len(lu)
    top1_share = top[0][1] / n_total * 100 if n_total else 0
    if top1_share >= 30:
        clarity = f"主线明确（{top[0][0]} {top[0][1]}家/{top1_share:.0f}%）"
    elif top1_share >= 15:
        clarity = f"主线分散（{top[0][0]} {top[0][1]}家/{top1_share:.0f}%，多线并进）"
    else:
        clarity = "无主线/轮动（涨停高度分散）"
    return {"ok": True, "n_limit": n_total, "top": top_detail,
            "top1_share_pct": round(top1_share, 1), "mainline_clarity": clarity}


# ═══════════════════════════════════════════════════════
#  合成 + 档位
# ═══════════════════════════════════════════════════════

STAGE_RULES = [
    (80, "亢奋/沸点", 1.0, "进攻但警惕退潮（炸板率>50%立即降档）"),
    (60, "热", 1.0, "积极操作，围绕主线重仓"),
    (40, "常温", 0.8, "正常操作，严控回撤"),
    (20, "冷", 0.5, "半仓试错，仅最强主线低吸"),
    (-1, "冰点", 0.2, "防守，只做最强主线低吸或空仓观望"),
]


def stage_of(vibe: float) -> tuple[str, float, str]:
    for lo, name, cap, action in STAGE_RULES:
        if vibe >= lo:
            return name, cap, action
    return STAGE_RULES[-1][1], STAGE_RULES[-1][2], STAGE_RULES[-1][3]


def build_card(expected_date: int | None, line_only: bool) -> dict:
    t0 = time.time()
    senti = sc.today_signal()
    data_date = int(senti["date"])

    vol = volume_dim(data_date)
    idx = index_dim(data_date)
    prem = premium_dim(data_date)
    lad = ladder_dim(data_date)

    vibe = round(W["sentiment"] * senti["score"] + W["premium"] * prem["score"]
                 + W["volume"] * vol["score"] + W["index"] * idx["score"]
                 + W["ladder"] * lad["score"], 1)
    stage, cap, action = stage_of(vibe)

    # 上下文：周期/分位/动量/背离/封板质量
    daily = sentiment_series()
    mom, pct = momentum_and_percentile(daily, data_date)
    cycle = cycle_stage(senti)
    div = divergence_warn(senti["score"], idx["score"], prem)
    sc001 = {"prem_seal": prem.get("prem_seal"),
             "circuit_breaker": prem.get("sc001_circuit_breaker"),
             "rolling20": prem.get("sc001_rolling20"),
             "empty_advice": prem.get("sc001_empty_advice")}
    seal = ssnap.seal_quality(str(data_date))
    sector = sector_heat_dim(data_date)
    trend = vibe_trend(5)

    freshness = {"data_date": data_date, "warn": [], "stale_days": 0}
    if idx["latest_date"] and idx["latest_date"] != data_date:
        freshness["warn"].append(f"指数库滞后（index_daily={idx['latest_date']} vs hsj={data_date}）")
    if expected_date and data_date < expected_date:
        n = len(pd.bdate_range(pd.Timestamp(str(data_date)), pd.Timestamp(str(expected_date))))
        freshness["stale_days"] = max(0, n - 1)
        freshness["warn"].append(f"数据滞后于预期交易日 {expected_date}（{freshness['stale_days']} 个自然交易日）")

    card = {
        "version": "v2",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": data_date,
        "vibe_score": vibe,
        "stage": stage,
        "max_position": cap,
        "action": action,
        "dimensions": {
            "sentiment": {"score": senti["score"], "stage": senti["stage"],
                          "n_limit_up": senti["n_limit_up"], "n_limit_down": senti["n_limit_down"],
                          "broken_rate_pct": senti["broken_rate_pct"],
                          "max_boards": senti["max_boards"]},
            "premium": {k: prem.get(k) for k in
                        ("score", "ok", "prem_seal", "label", "count", "up", "down",
                         "best", "worst")},
            "volume": vol,
            "index_momentum": idx,
            "ladder": {k: lad.get(k) for k in
                       ("score", "ok", "max_boards", "ladder", "n_3b_plus", "n_5b_plus")},
        },
        "context": {
            "cycle_stage": cycle,
            "percentile_250d": pct,
            "momentum": mom,
            "divergence": div,
            "sc001": sc001,
            "seal_quality": seal,
            "sector_heat": sector,
            "trend": trend,
        },
        "weights": W,
        "freshness": freshness,
        "note": "盘感分v2=情绪40%+打板溢价20%+量能15%+指数动能15%+连板梯队10%；"
                "cycle_stage=启动/主升/分歧/退潮；sc001.circuit_breaker=打板溢价≤0亏钱效应；"
                "sc001.empty_advice=rolling20≤0空仓提示；data_date 非实时报价",
        "load_seconds": round(time.time() - t0, 1),
    }
    return card


def append_history(card: dict) -> Path:
    """把当日卡片追加到 _cache/vibe_history.csv（幂等：同 data_date 覆盖）。
    ⚠️ date 统一转 str（回填/追加混用时 int/str 混合会让 sort 崩溃，2026-08-22 测试抓到）。"""
    d = card["dimensions"]
    row = {
        "date": str(card["data_date"]), "vibe_score": card["vibe_score"],
        "stage": card["stage"], "max_position": card["max_position"],
        "senti_score": d["sentiment"]["score"],
        "n_limit_up": d["sentiment"]["n_limit_up"],
        "n_limit_down": d["sentiment"]["n_limit_down"],
        "broken_rate_pct": d["sentiment"]["broken_rate_pct"],
        "max_boards": d["sentiment"]["max_boards"],
        "prem_seal": d["premium"].get("prem_seal"),
        "prem_score": d["premium"].get("score"),
        "vol_ratio": d["volume"].get("ratio"),
        "vol_score": d["volume"]["score"],
        "index_score": d["index_momentum"]["score"],
        "ladder_score": d["ladder"].get("score"),
        "cycle_stage": card["context"]["cycle_stage"],
        "percentile_250d": card["context"]["percentile_250d"],
        "trend_days": card["context"]["momentum"]["trend_days"],
        "divergence": card["context"]["divergence"],
        "generated_at": card["generated_at"],
    }
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([row])
    if HISTORY_CSV.exists():
        old = pd.read_csv(HISTORY_CSV, dtype={"date": str})
        old = old[old["date"] != str(card["data_date"])]
        df = pd.concat([old, df], ignore_index=True)
    df["date"] = df["date"].astype(str)
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    df.to_csv(HISTORY_CSV, index=False, encoding="utf-8-sig")
    return HISTORY_CSV


def append_history_rows() -> pd.DataFrame:
    if HISTORY_CSV.exists():
        return pd.read_csv(HISTORY_CSV, dtype={"date": str})
    return pd.DataFrame()


def vibe_trend(n: int = 5) -> list[dict]:
    """近 N 日盘感趋势（读 _cache/vibe_history.csv，供判读连续走向）。"""
    try:
        df = pd.read_csv(HISTORY_CSV, dtype={"date": str}).tail(n)
        return [{"date": r.date, "vibe_score": round(float(r.vibe_score), 1), "stage": r.stage}
                for r in df.itertuples() if not pd.isna(r.vibe_score)]
    except Exception:
        return []


def refresh_sentiment_cache() -> Path:
    """--append 时顺带刷新共享缓存 _cache/sentiment_history.csv
    （该缓存仅 sentiment_composite.main() 会写，日常 --today 不写会停在旧日；
    8/22 曾停在 8/14，导致 ortho_check 等读缓存方拿到过期序列）。"""
    daily = sc.compute_daily_sentiment()
    daily.to_csv(CACHE / "sentiment_history.csv", index=False, encoding="utf-8-sig")
    return CACHE / "sentiment_history.csv"


def _build_with_retry(expected_date: int | None, line_only: bool) -> dict:
    """build_card 包一层：sentiment_composite 加载 8.9M 行 hsj 峰值约 2-3GB，
    内存压力下偶发 MemoryError（2026-08-22 并发测试实测）→ gc + 重试一次。"""
    import gc
    for attempt in range(2):
        try:
            return build_card(expected_date, line_only)
        except MemoryError:
            gc.collect()
            if attempt == 0:
                time.sleep(2)
                continue
            raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="每日盘感/市场氛围卡片 v2（只读）")
    ap.add_argument("--expected-date", type=int, default=None,
                    help="预期数据日 yyyymmdd（用于滞后检测；调度器可传上一交易日）")
    ap.add_argument("--json", default="", help="JSON 输出路径（默认 reports/vibe_card_<data_date>.json）")
    ap.add_argument("--line", action="store_true", help="只输出单行摘要")
    ap.add_argument("--append", action="store_true",
                    help="生成后：写 vibe_card_latest.json + 追加 _cache/vibe_history.csv（幂等）")
    args = ap.parse_args(argv)

    card = _build_with_retry(args.expected_date, args.line)

    if args.line:
        f = card["freshness"]
        w = " ⚠️" + "；".join(f["warn"]) if f["warn"] else ""
        d = card["dimensions"]
        c = card["context"]
        seal = c.get("seal_quality", {})
        seal_s = (f"真封单率{seal.get('true_seal_rate_pct')}%"
                  if seal.get("ok") else "封板数据缺")
        sect = c.get("sector_heat", {})
        sect_s = sect.get("mainline_clarity") if sect.get("ok") else ""
        print(f"盘感{vcard_date(card)}: {card['vibe_score']}分/{card['stage']} "
              f"(情绪{d['sentiment']['score']}/溢价{d['premium'].get('score')}"
              f"/量能{d['volume']['score']}/动能{d['index_momentum']['score']}"
              f"/梯队{d['ladder'].get('score')}) 仓位≤{card['max_position']:.0%} "
              f"— {card['action']} | 周期{c['cycle_stage']}"
              f" | 分位{c['percentile_250d']}% | 动量{c['momentum']['trend_days']}"
              f" | {seal_s}"
              + (f" | {sect_s}" if sect_s else "")
              + (f" | 🔴SC001熔断" if c["sc001"]["circuit_breaker"] else "")
              + (f" | ⚠️背离" if c["divergence"] else "") + w)
        return 0

    out = Path(args.json) if args.json else REPORT_DIR / f"vibe_card_{card['data_date']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_JSON.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(card, ensure_ascii=False, indent=2))
    print(f"[save] {out}")
    print(f"[save] {LATEST_JSON}（稳定路径，供作战清单/下游消费）")
    if args.append:
        hist = append_history(card)
        print(f"[append] {hist}（{len(append_history_rows())} 行历史）")
        try:
            scache = refresh_sentiment_cache()
            print(f"[cache] sentiment_history.csv 已刷新 → {scache}")
        except Exception as e:
            print(f"[cache] sentiment_history.csv 刷新失败: {e}")
    return 0


def vcard_date(card: dict) -> str:
    return str(card["data_date"])


if __name__ == "__main__":
    sys.exit(main())
