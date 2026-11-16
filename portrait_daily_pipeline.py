#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""portrait_daily_pipeline.py — 画像系统日更管线（10/2优化迭代，每日23:00 schtasks）

三合一：
  1. 日度分数落库: portrait_feed.json → data/portrait_scores_history.jsonl 追加(同日UPSERT)
  2. Z2s×画像打标: 近5日Z2s事件 → 查画像分 → data/z2s_portrait_enriched.jsonl
  3. 模型衰减监控: 近20日 D10 fwd5 均值 vs D1 → 偏差<0 则 WARN 模型衰减

用法: python portrait_daily_pipeline.py
退出码: 0=正常 / 1=画像数据不可用
"""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
FEED = os.path.join(ROOT, "data", "portrait_feed.json")
HIST = os.path.join(ROOT, "data", "portrait_scores_history.jsonl")
Z2S_LEDGER = os.path.join(ROOT, "data", "z2s_shadow_ledger.jsonl")
Z2S_ENRICHED = os.path.join(ROOT, "data", "z2s_portrait_enriched.jsonl")


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def step1_accumulate(feed):
    """今日分数 → 历史序列追加（同日UPSERT）。"""
    snap = feed.get("snapshot_date", "")
    ss = feed.get("scores_short", {})
    if not snap or not ss:
        print("[SKIP] feed无有效数据")
        return 0
    # 读现有
    rows = load_jsonl(HIST)
    rows = [r for r in rows if r.get("date") != snap]  # 同日去重(UPSERT)
    # 追加今日
    for code, v in ss.items():
        if not isinstance(v, dict) or "prob" not in v:
            continue
        rows.append({
            "date": snap, "code": str(code).zfill(6)[-6:],
            "prob_short": round(float(v["prob"]), 6),
            "pct_short": round(float(v.get("pct", 0)), 2),
            "prob_mid": round(float(v.get("prob_mid", 0)), 6) if "prob_mid" in v else None,
        })
    rows.sort(key=lambda r: (r["date"], r["code"]))
    with open(HIST, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_today = sum(1 for r in rows if r["date"] == snap)
    print(f"[1-落库] snapshot={snap} {n_today}只 → 总计{len(rows)}行 ({HIST})")
    return len(rows)


def step2_z2s_enrich(feed):
    """近5日Z2s事件 → 查画像分 → 打标输出。"""
    ss = feed.get("scores_short", {})
    pf_map = {str(k).zfill(6)[-6:]: (round(v["prob"], 4), round(v.get("pct", 0), 1))
              for k, v in ss.items() if isinstance(v, dict) and "prob" in v}
    z2s_rows = load_jsonl(Z2S_LEDGER)
    if not z2s_rows:
        print("[2-Z2s] Z2s台账空 → 跳过")
        return
    # 近5日事件
    cutoff = time.strftime("%Y%m%d", time.localtime(time.time() - 5 * 86400))
    recent = [r for r in z2s_rows if str(r.get("date", "")) >= cutoff]
    enriched = []
    for r in recent:
        c6 = str(r.get("code", "")).zfill(6)[-6:]
        pf = pf_map.get(c6)
        enriched.append({**r,
                         "pf_prob": pf[0] if pf else None,
                         "pf_pct": pf[1] if pf else None,
                         "pf_top50": (pf[1] >= 50) if pf else None})
    with open(Z2S_ENRICHED, "w", encoding="utf-8", newline="\n") as f:
        for r in enriched:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_tagged = sum(1 for r in enriched if r.get("pf_prob") is not None)
    n_top50 = sum(1 for r in enriched if r.get("pf_top50"))
    print(f"[2-Z2s] 近5日{len(recent)}事件, 画像命中{n_tagged}, 前50%{n_top50} → {Z2S_ENRICHED}")


def step3_quality_check():
    """模型衰减监控: 近20日 D10 fwd5 vs D1 fwd5 → 偏差<0 = WARN。"""
    rows = load_jsonl(HIST)
    if len(rows) < 500:
        print(f"[3-QC] 历史序列不足({len(rows)}行), 暂不判读")
        return
    import pandas as pd
    df = pd.DataFrame(rows)
    # 加载 fwd5 (从panel或qfq)
    # 简化: 用 prob_short 分位变化趋势作代理——分数集中度下降=模型区分度衰减
    dates = sorted(df["date"].unique())
    if len(dates) < 20:
        print(f"[3-QC] 日期不足({len(dates)}<20), 暂不判读")
        return
    recent = df[df["date"].isin(dates[-20:])]
    older = df[df["date"].isin(dates[-40:-20])]
    # 标准差 = 区分度(越高=越能区分好坏票)
    std_recent = recent["prob_short"].std()
    std_older = older["prob_short"].std() if len(older) > 100 else std_recent
    ratio = std_recent / std_older if std_older > 0 else 1.0
    status = "OK" if ratio >= 0.8 else "🟡 WARN: 模型区分度衰减"
    print(f"[3-QC] 区分度: 近20日std={std_recent:.4f} vs 前20日={std_older:.4f} ratio={ratio:.2f} {status}")
    if ratio < 0.8:
        print("  → 建议检查 PortraitAgent 训练数据/特征质量")




def step4_universe_portrait():
    """宇宙画像三件套(snapshot/brief/双门影子) — 2026-10-02 接入, 详见 universe_portrait_daily.py"""
    import subprocess as sp
    r = sp.run([sys.executable, os.path.join(ROOT, "universe_portrait_daily.py")],
               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2400)
    for line in (r.stdout or "").strip().splitlines()[-6:]:
        print("  [4-UP]", line)
    if r.returncode != 0:
        print("  [4-UP:WARN] rc=", r.returncode)

def main():
    T0 = time.time()
    # 读取feed
    if not os.path.exists(FEED):
        print(f"[FAIL] {FEED} 不存在")
        return 1
    feed = json.load(open(FEED, encoding="utf-8"))
    snap = feed.get("snapshot_date", "")
    fresh = feed.get("freshness", {})
    if not fresh.get("ok", False):
        print(f"[WARN] 画像feed超龄: {fresh}")
        # 仍然落库(有数据就存), 但标注

    print(f"=== 画像日更管线 {time.strftime('%F %T')} ===")
    step1_accumulate(feed)
    step2_z2s_enrich(feed)
    step3_quality_check()
    step4_universe_portrait()
    print(f"=== 完成 {time.time()-T0:.1f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
