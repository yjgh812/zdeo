# -*- coding: utf-8 -*-
"""
情绪策略体系化 — 情绪复合信号合成器 Phase 2.2
================================================
整合四源 → 统一 sentiment_composite_score (0-100) + 档位:
  [历史可回填] ①涨停活跃/连板/炸板率/跌停压力/涨跌比 (stock_daily_hsj, 白名单过滤防串码污染)
  [实时增强]   ②股吧反向见顶(guba) ③新闻情绪(llm_event) ④六维雷达(emotion_radar)

本模块职责:
  A. 历史回填 2019-2026 日频情绪序列（涨停价用 price_limits.numpy_limit_cents 唯一真理源）
  B. 预测力验证: 情绪score → 未来5/20日等权市场收益 IC + 分档条件收益
  C. 输出应用规则: 档位→仓位建议 / REVERSE_TOP → 个股排除

产出:
  - _cache/sentiment_history.csv (日频序列)
  - reports/SENTIMENT_COMPOSITE_20260816.md
"""
from __future__ import annotations

import pickle
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
import price_limits  # noqa: E402 涨停唯一真理源

HSJ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
CACHE = Path(r"D:\zcodesj\_cache")
REPORT = Path(r"D:\zcodesj\reports")
QFQ_CHG_CACHE = CACHE / "qfq_chg_pivot_2019.pkl"
WHITELIST_PREFIX = ("60", "00", "30", "68", "92")
# END 动态取源库最新交易日（8/20修复：原硬编码20260814导致情绪合成永久停在8/14）
import sqlite3 as _sc
try:
    _c = _sc.connect(f"file:{HSJ_DB}?mode=ro", uri=True, timeout=120)
    _end = _c.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0]
    _c.close()
except Exception:
    _end = 20260814
START, END = 20190101, _end


# ═══════════════════════════════════════════════════════
#  A. 历史回填: 日频情绪指标
# ═══════════════════════════════════════════════════════

def load_hsj_clean() -> pd.DataFrame:
    # 缓存按源库最新日期命名，数据更新后自动重建（8/20修复：原固定名缓存永久停在首建日）
    try:
        _c = sqlite3.connect(f"file:{HSJ_DB}?mode=ro", uri=True, timeout=120)
        _max = _c.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0]
        _c.close()
    except Exception:
        _max = 0
    cache = CACHE / f"hsj_clean_{_max}.pkl"
    if cache.exists():
        return pd.read_pickle(cache)
    t0 = time.time()
    conn = sqlite3.connect(f"file:{HSJ_DB}?mode=ro", uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT code, date, high, low, close, vol FROM stock_daily_hsj "
        "WHERE date >= 20181001 AND close > 0",
        conn)
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(WHITELIST_PREFIX)]
    df = df.drop_duplicates(["code", "date"], keep="last").sort_values(["code", "date"])
    df.to_pickle(cache)
    # 2026-09-24 保留窗(二轮垃圾审计根治): 缓存按源库MAX(date)命名→每日新键旧键永不清,
    # 曾累积13份×505M=6.5G; 写入侧即清, 只留最新2份(今+前一交易日, 兼容跨日读取)
    try:
        for _f in sorted(CACHE.glob("hsj_clean_*.pkl"))[:-2]:
            _f.unlink()
            print(f"  hsj保留窗: 清旧快照 {_f.name}")
    except OSError:
        pass
    print(f"  hsj清洗: {len(df):,}行 ({time.time()-t0:.0f}s)")
    return df


def compute_daily_sentiment() -> pd.DataFrame:
    """日频情绪指标 + 复合score。"""
    df = load_hsj_clean()
    t0 = time.time()
    codes = df["code"].to_numpy()
    dates = df["date"].to_numpy()
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()

    prev_close = df.groupby("code")["close"].shift(1).to_numpy()
    ok = ~np.isnan(prev_close)
    df = df[ok].copy()
    codes, dates = codes[ok], dates[ok]
    close, high, prev = close[ok], high[ok], prev_close[ok]

    # 涨停/跌停价（唯一真理源，分单位）
    zt_cents = price_limits.numpy_limit_cents(
        np.rint(prev * 100).astype(np.int64), codes, dates, None, up=True)
    dt_cents = price_limits.numpy_limit_cents(
        np.rint(prev * 100).astype(np.int64), codes, dates, None, up=False)
    zt = zt_cents / 100.0
    dt = dt_cents / 100.0
    tol = 1e-3  # 封板tol（bt-ashare经验）

    df["is_limit_up"] = (close >= zt - tol).astype(bool)  # 收盘封板, 入口统一bool避免后续fillna downcast警告
    df["is_touched"] = high >= zt - tol            # 盘中触板
    df["is_limit_down"] = (close <= dt + tol) & (prev > 0)
    df["is_up"] = close > prev
    df["date"] = dates

    # 连板: per-code 涨停streak
    df = df.sort_values(["code", "date"])
    df["prev_lu"] = df.groupby("code")["is_limit_up"].shift(1, fill_value=False).astype(bool)
    df["streak"] = 0
    stk = df["is_limit_up"].to_numpy()
    prev_lu = df["prev_lu"].to_numpy()
    streak = np.zeros(len(df), dtype=np.int32)
    for i in range(1, len(df)):
        if stk[i]:
            streak[i] = streak[i-1] + 1 if prev_lu[i] else 1
        else:
            streak[i] = 0
    df["streak"] = streak

    # 按日聚合
    g = df.groupby("date")
    daily = pd.DataFrame({
        "n_touched": g["is_touched"].sum(),
        "n_limit_up": g["is_limit_up"].sum(),
        "n_limit_down": g["is_limit_down"].sum(),
        "n_broken": g.apply(lambda x: int((x["is_touched"] & ~x["is_limit_up"]).sum()),
                            include_groups=False),
        "max_boards": g["streak"].max(),
        "n_stocks": g["close"].count(),
        "n_up": g["is_up"].sum(),
    })
    daily["broken_rate_pct"] = daily["n_broken"] / daily["n_touched"].replace(0, np.nan) * 100
    daily["up_ratio_pct"] = daily["n_up"] / daily["n_stocks"] * 100

    # 晋级率: 昨日封板股今日再封板比例
    df["prev_day_lu"] = df.groupby("code")["is_limit_up"].shift(1, fill_value=False).astype(bool)
    promo_num = df[df["prev_day_lu"]].groupby("date")["is_limit_up"].sum()
    promo_den = df[df["prev_day_lu"]].groupby("date")["is_limit_up"].count()
    daily["promotion_pct"] = (promo_num / promo_den.replace(0, np.nan) * 100)

    daily = daily[(daily.index >= START) & (daily.index <= END)]
    daily["score"] = daily.apply(composite_score, axis=1)
    daily["stage"] = daily["score"].apply(stage_label)
    print(f"  日频情绪: {len(daily)}天 ({time.time()-t0:.0f}s)", file=sys.stderr)
    return daily.reset_index().rename(columns={"index": "date"})


def composite_score(r) -> float:
    """与 attribution/emotion_index.py 完全同款打分。"""
    lu = r["n_limit_up"]
    lu_s = 100 if lu >= 80 else 75 if lu >= 60 else 50 if lu >= 40 else 25 if lu >= 20 else 0
    mb = r["max_boards"]
    mb_s = 100 if mb >= 7 else 80 if mb >= 5 else 50 if mb >= 3 else 25 if mb >= 2 else 0
    br = r["broken_rate_pct"] if not np.isnan(r["broken_rate_pct"]) else 50
    br_s = 100 if br <= 15 else 75 if br <= 25 else 50 if br <= 35 else 25 if br <= 50 else 0
    ld = r["n_limit_down"]
    ld_s = 100 if ld <= 3 else 75 if ld <= 8 else 50 if ld <= 15 else 25 if ld <= 30 else 0
    ur = r["up_ratio_pct"]
    ur_s = 100 if ur >= 70 else 75 if ur >= 50 else 50 if ur >= 30 else 25 if ur >= 15 else 0
    return lu_s * 0.30 + mb_s * 0.25 + br_s * 0.20 + ld_s * 0.15 + ur_s * 0.10


def stage_label(score: float) -> str:
    if score >= 80: return "沸点"
    if score >= 60: return "热"
    if score >= 40: return "常温"
    if score >= 20: return "冷"
    return "冰点"


# ═══════════════════════════════════════════════════════
#  B. 预测力验证
# ═══════════════════════════════════════════════════════

def validate(daily: pd.DataFrame) -> dict:
    """score → 未来5/20日等权市场收益 IC + 分档条件收益。"""
    with open(QFQ_CHG_CACHE, "rb") as f:
        chg = pickle.load(f)  # 行=code, 列=date (change_pct百分数)
    chg = chg.loc[[c for c in chg.index if c[:2] in WHITELIST_PREFIX]]
    # change_pct单位=百分数(qfq库铁律)
    ew_ret = chg.mean(axis=0) / 100.0  # 等权日收益(小数)
    ew_ret.index = pd.to_datetime(ew_ret.index.astype(str), format="%Y%m%d")
    ew_ret = ew_ret.sort_index()

    d = daily.copy()
    d["dt"] = pd.to_datetime(d["date"].astype(str), format="%Y%m%d")
    d = d.set_index("dt").sort_index()

    fwd5 = ew_ret.rolling(5).sum().shift(-5).reindex(d.index)
    fwd20 = ew_ret.rolling(20).sum().shift(-20).reindex(d.index)

    from scipy.stats import spearmanr
    m5 = d["score"].notna() & fwd5.notna()
    m20 = d["score"].notna() & fwd20.notna()
    ic5, p5 = spearmanr(d["score"][m5], fwd5[m5])
    ic20, p20 = spearmanr(d["score"][m20], fwd20[m20])

    # 分档条件收益（未来20日年化视角直接看均值）
    tiers = {}
    for stg in ["冰点", "冷", "常温", "热", "沸点"]:
        m = (d["stage"] == stg) & fwd20.notna()
        if m.sum() >= 10:
            tiers[stg] = {"n": int(m.sum()), "fwd20_mean": float(fwd20[m].mean()),
                          "fwd5_mean": float(fwd5[m].mean()) if fwd5[m].notna().any() else np.nan}
    # 分位数细分
    qbins = pd.qcut(d["score"], 5, labels=["Q1低", "Q2", "Q3", "Q4", "Q5高"], duplicates="drop")
    qres = {}
    for q in ["Q1低", "Q2", "Q3", "Q4", "Q5高"]:
        m = (qbins == q) & fwd20.notna()
        if m.sum() >= 10:
            qres[q] = float(fwd20[m].mean())
    return {"ic5": float(ic5), "p5": float(p5), "ic20": float(ic20), "p20": float(p20),
            "n5": int(m5.sum()), "n20": int(m20.sum()), "tiers": tiers, "quintiles": qres}


# ═══════════════════════════════════════════════════════
#  C. 应用规则输出
# ═══════════════════════════════════════════════════════

POSITION_RULES = {
    "冰点": {"max_position": 0.2, "action": "防守/低吸仅最强主线", "buy_freeze": True},
    "冷":   {"max_position": 0.5, "action": "半仓试错", "buy_freeze": False},
    "常温": {"max_position": 0.8, "action": "正常操作严控回撤", "buy_freeze": False},
    "热":   {"max_position": 1.0, "action": "积极操作主线重仓", "buy_freeze": False},
    "沸点": {"max_position": 1.0, "action": "满仓但警惕退潮信号(炸板率>50%立即降档)", "buy_freeze": False},
}


def main():
    t0 = time.time()
    print("=== 情绪复合信号合成器 ===")
    daily = compute_daily_sentiment()
    daily.to_csv(CACHE / "sentiment_history.csv", index=False, encoding="utf-8-sig")
    print(f"  序列已存: {len(daily)}天")

    val = validate(daily)
    print(f"  IC(5日)={val['ic5']:.4f}(p={val['p5']:.3f}) IC(20日)={val['ic20']:.4f}(p={val['p20']:.3f})")

    # 报告
    lines = [
        "# 情绪策略体系化 — 合成器实证报告",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 样本: 2019-01~2026-08 共{len(daily)}个交易日",
        f"- 数据: stock_daily_hsj 白名单过滤(60/00/30/68/92, 剔除999998/999999串码)",
        f"- 涨停价: price_limits.numpy_limit_cents 唯一真理源",
        "",
        "## 合成公式（与 attribution/emotion_index.py 同款）",
        "涨停活跃30% + 连板强度25% + 炸板质量20% + 跌停压力15% + 涨跌比10%",
        "",
        "## 预测力验证",
        "",
        "| 期数 | IC | p值 | 样本 |",
        "|------|-----|------|------|",
        f"| 未来5日 | {val['ic5']:.4f} | {val['p5']:.4f} | {val['n5']} |",
        f"| 未来20日 | {val['ic20']:.4f} | {val['p20']:.4f} | {val['n20']} |",
        "",
        "## 分档条件收益 (未来20日等权市场)",
        "",
        "| 档位 | 天数 | 未来20日均值 | 未来5日均值 |",
        "|------|------|-------------|------------|",
    ]
    for stg, r in val["tiers"].items():
        lines.append(f"| {stg} | {r['n']} | {r['fwd20_mean']:.2%} | {r['fwd5_mean']:.2%} |")
    lines += ["", "## 五分位条件收益 (score Q1低→Q5高)", ""]
    for q, v in val["quintiles"].items():
        lines.append(f"- {q}: 未来20日 {v:.2%}")
    lines += ["", "## 应用规则", ""]
    for stg, r in POSITION_RULES.items():
        lines.append(f"- **{stg}**: 仓位上限{r['max_position']:.0%} — {r['action']}")
    lines += ["",
              "## 实时增强源（不进历史回测）",
              "- guba_reverse_signal: 个股REVERSE_TOP_WARN → 加入当日排除清单",
              "- llm_event_scorer: 个股新闻情绪<-0.5 → 买入闸门加WARN",
              "",
              f"总耗时 {time.time()-t0:.0f}s",
              ""]
    p = REPORT / f"SENTIMENT_COMPOSITE_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")
    return val


def today_signal():
    """实时模式: 最新交易日情绪score+档位+仓位建议。"""
    daily = compute_daily_sentiment()
    last = daily.iloc[-1]
    rules = POSITION_RULES.get(last["stage"], POSITION_RULES["常温"])
    return {
        "date": str(int(last["date"])), "score": float(last["score"]),
        "stage": last["stage"], "n_limit_up": int(last["n_limit_up"]),
        "n_limit_down": int(last["n_limit_down"]), "broken_rate_pct":
            None if pd.isna(last.get("broken_rate_pct")) else round(float(last["broken_rate_pct"]), 1),
        "max_boards": int(last["max_boards"]),
        "max_position": rules["max_position"], "action": rules["action"],
    }


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "--today":
        import json as _json
        print(_json.dumps(today_signal(), ensure_ascii=False, indent=2))
    else:
        main()
