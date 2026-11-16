#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_ml_signal.py — ML多因子(Ridge·PIT-D17) 月度生产信号器
================================================================================
口径与 factor_ml_walkforward_v3.py 完全一致：
    特征集 D(17) = PIT9[droe,roa,roe,lev,ato,pm,ep=eps,bp=close/bps,sp=close/sps]
                 + daily_basic4[turnover,volume_ratio,dv_ratio,size]
                 + 量价4[rev20,vol20,illiq20,max_ret20]
    模型 Ridge(α=1, 子采样600k, seed42)；训练=最近500交易日（末20日标签未完成
    由 train_matrix 的 y-NaN 剔除天然覆盖 = 与回测 purge 同语义，无前视）。
    过滤: ≤30元(原始close) + 非ST；输出 Top N(默认20)。

fail-closed（任何一步不满足即 exit 1，禁止空清单/脏清单流入）:
    ① qfq 数据新鲜度: 最新交易日距今天 ≤ 7 自然日
    ② 截面有效股票 ≥ 50
    ③ 名称走 stock_name_lookup.require_name（铁律，查不到直接抛异常）

输出:
    reports/ml_ridge_top20_latest.csv        （含名称/行业/价格/分数/新进标记）
    reports/ML_RIDGE_PICKS_<yyyymmdd>.md     （人类可读报告）
用法: python factor_ml_signal.py [--top 20] [--train 500]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import factor_ml_walkforward_v2 as f2  # noqa: E402
import factor_combo_walkforward as fcw  # noqa: E402
import factor_ml_walkforward_v3 as f3  # noqa: E402
from stock_name_lookup import require_name  # noqa: E402  名称铁律

QFQ_DB = f2.QFQ_DB
DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
OUT_DIR = Path(r"D:\zcodesj\reports")
FRESHNESS_DAYS = 7


def qfq_latest_date() -> int:
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    try:
        return con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    finally:
        con.close()


def load_industry() -> dict[str, str]:
    con = sqlite3.connect(f"file:{f2.META_DB}?mode=ro", uri=True, timeout=30)
    try:
        return {c[-6:]: s for c, s in
                con.execute("SELECT code, sector_name FROM stock_sector_sw1").fetchall()}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="ML多因子 Ridge-PIT-D17 月度信号器(fail-closed)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--train", type=int, default=500)
    ap.add_argument("--fwd", type=int, default=20)
    a = ap.parse_args()
    t0 = time.time()

    # ── ① 数据新鲜度（fail-closed）──
    latest = qfq_latest_date()
    age = (datetime.now() - datetime.strptime(str(latest), "%Y%m%d")).days
    print(f"[新鲜度] qfq 最新交易日 {latest}（距今 {age} 天）")
    if age > FRESHNESS_DAYS:
        print(f"[拒绝] qfq 数据过期（>{FRESHNESS_DAYS}天）——先跑统一链路更新，禁止用过期数据出清单")
        return 1

    # ── ② 面板（窗口=最新;口径与v3一致）──
    start = (datetime.strptime(str(latest), "%Y%m%d") - pd.Timedelta(days=950)).strftime("%Y%m%d")
    print(f"[面板] 构建窗口 {start}~{latest}（train={a.train}d, PIT通道）...", flush=True)
    panels = f2.build_panels(start, str(latest), a.fwd, warmup=90)
    cal = panels["calendar"]
    raw_close = panels["raw_close"]
    wides = {k: panels["wides"][k] for k in f3.CLEAN_DB + f3.CLEAN_VOL}
    wides.update(f3.load_pit_wides(cal, raw_close))
    st_codes = fcw.load_st_codes()
    print(f"      日历 {len(cal)} 天（{cal[0].date()}~{cal[-1].date()}）| ST剔除 {len(st_codes)} 只")

    # ── ③ 训练 Ridge（末20日无标签自动剔除=purge语义）──
    features = f3.FEATURESETS["D"]
    aligned = f2.AlignedFeatures(wides, features, cal)
    last = len(cal) - 1
    p0 = max(0, last - a.train - a.fwd)
    fwd_al = panels["fwd"].reindex(columns=aligned.cols)   # PIT列并集对齐(与v2 WF同法)
    Xtr, ytr, _ = aligned.train_matrix(p0, last, fwd_al)
    if len(ytr) < 1000:
        print(f"[拒绝] 训练样本不足: {len(ytr)}")
        return 1
    rng = np.random.default_rng(42)
    model, meta = f2.fit_models("ridge", Xtr, ytr, rng, max_train_samples=600_000, seed=42)
    print(f"[训练] Ridge p0={cal[p0].date()}~{cal[last].date()} n_fit={meta['n_fit']:,} "
          f"（{time.time()-t0:.0f}s）")

    # ── ④ 当日截面打分 + 过滤 ──
    xs, stale_max = aligned.snapshot(last)
    day_price = raw_close.iloc[-1] if len(raw_close) else None
    if day_price is None or len(xs) < 50 or stale_max > 60:
        print(f"[拒绝] 截面异常: n={len(xs)}, stale={stale_max}")
        return 1
    valid = [c for c in xs.index
             if c not in st_codes and c in day_price.index
             and not np.isnan(day_price[c]) and day_price[c] <= 30]
    if len(valid) < 50:
        print(f"[拒绝] 过滤后候选池仅 {len(valid)} 只（<50）")
        return 1
    pred = pd.Series(model.predict(xs.loc[valid].values.astype(np.float64)), index=valid)
    top = pred.nlargest(a.top)

    # ── ⑤ 输出（名称铁律 + 新进标记）──
    ind = load_industry()
    latest_csv = OUT_DIR / "ml_ridge_top20_latest.csv"
    prev = set()
    if latest_csv.exists():
        try:
            prev = set(pd.read_csv(latest_csv, dtype={"code": str})["code"])
        except Exception:
            prev = set()
    rows = []
    for i, (code, score) in enumerate(top.items(), 1):
        rows.append({"rank": i, "code": code, "name": require_name(code),
                     "industry": ind.get(code, ""), "close": round(float(day_price[code]), 2),
                     "score": round(float(score), 5), "is_new": int(code not in prev)})
    df = pd.DataFrame(rows)
    dated = OUT_DIR / f"ml_ridge_top20_{latest}.csv"
    df.to_csv(latest_csv, index=False, encoding="utf-8-sig")
    df.to_csv(dated, index=False, encoding="utf-8-sig")
    exits = sorted(prev - set(df["code"]))

    md = OUT_DIR / f"ML_RIDGE_PICKS_{latest}.md"
    cfg = (f"特征D17(PIT9+db4+量价4) | Ridge(α=1,600k子采样) | train={a.train}d截止"
           f"{cal[last].date()}(末{a.fwd}d无标签自动剔除) | 截面{len(valid)}只(≤30元非ST)")
    with open(md, "w", encoding="utf-8") as fp:
        fp.write(f"# ML多因子 Ridge-PIT-D17 月度清单（{latest}）\n\n"
                 f"- 口径: {cfg}\n"
                 f"- 回测成色(2020-2026, EP防泄漏): OOS IC+0.0744 / CAGR+5.1% / Sharpe0.214"
                 f"（PIT清洁后真实水平; 污染上界B集为+8.7%）\n"
                 f"- 🔴 DSR门禁: FAIL(PSR0.754<0.95)——禁止作独立实盘策略\n"
                 f"- ⚠️ 定位: 研究/战术提示层（主人8/15 C裁决 + DSR门禁），实盘买入必须过 pre_trade_check 全闸门\n"
                 f"- 名称: stock_name_lookup.require_name 铁律 {len(df)}/{len(df)}✅\n\n"
                 f"| 排名 | 代码 | 名称 | 行业 | 价格 | 分数 | 新进 |\n|---|---|---|---|---:|---:|:-:|\n")
        for r in rows:
            fp.write(f"| {r['rank']} | {r['code']} | {r['name']} | {r['industry']} | "
                     f"{r['close']:.2f} | {r['score']:+.4f} | {'🆕' if r['is_new'] else ''} |\n")
        fp.write(f"\n- 退出上期: {'、'.join(f'{c} {require_name(c)}' for c in exits) if exits else '无'}\n"
                 f"- 新进 {sum(r['is_new'] for r in rows)} / 持续 {a.top - sum(r['is_new'] for r in rows)}\n"
                 f"- 生成: {datetime.now().isoformat(timespec='seconds')} | 耗时 {time.time()-t0:.0f}s\n")
    print(f"[输出] {latest_csv.name} | {md.name}")
    print(f"[摘要] 新进 {sum(r['is_new'] for r in rows)}/{a.top} | 退出 {len(exits)} | "
          f"候选池 {len(valid)} 只 | 总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
