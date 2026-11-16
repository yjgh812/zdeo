# -*- coding: utf-8 -*-
"""回填 vibe_history 近 N 个交易日（逐日真算，幂等）。非定时，一次性数据准备。
盘感分与 vibe_card v2/v3 同款：情绪40%+溢价20%+量能15%+动能15%+梯队10%。"""
import sys, sqlite3
import pandas as pd
sys.path.insert(0, r"D:\zcodesj")
import sentiment_composite as sc
import vibe_card as vc

N = 15
daily = sc.compute_daily_sentiment().set_index("date")
daily = daily[daily.index >= 20250601].tail(N)
rows = []
for d in daily.index:
    r = daily.loc[d]
    senti = {"date": str(int(d)), "score": float(r["score"]), "stage": r["stage"],
             "n_limit_up": int(r["n_limit_up"]), "n_limit_down": int(r["n_limit_down"]),
             "broken_rate_pct": None if pd.isna(r["broken_rate_pct"]) else round(float(r["broken_rate_pct"]),1),
             "max_boards": int(r["max_boards"])}
    vol = vc.volume_dim(int(d))
    idx = vc.index_dim(int(d))
    prem = vc.premium_dim(int(d))
    lad = vc.ladder_dim(int(d))
    vibe = round(0.40*senti["score"] + 0.20*prem["score"] + 0.15*vol["score"]
                 + 0.15*idx["score"] + 0.10*lad["score"], 1)
    stage, cap, action = vc.stage_of(vibe)
    rows.append({"date": str(int(d)), "vibe_score": vibe, "stage": stage,
                 "max_position": cap, "senti_score": senti["score"],
                 "n_limit_up": senti["n_limit_up"], "n_limit_down": senti["n_limit_down"],
                 "broken_rate_pct": senti["broken_rate_pct"], "max_boards": senti["max_boards"],
                 "prem_seal": prem.get("prem_seal"), "prem_score": prem.get("score"),
                 "vol_ratio": vol.get("ratio"), "vol_score": vol["score"],
                 "index_score": idx["score"], "ladder_score": lad.get("score"),
                 "cycle_stage": vc.cycle_stage(senti),
                 "generated_at": "backfill-20260822"})
    print(f"{int(d)}: vibe={vibe} {stage} 溢价={prem.get('prem_seal')} 量能={vol.get('ratio')} 动能={idx['score']} 梯队={lad.get('score')} 周期={vc.cycle_stage(senti)}")

df = pd.DataFrame(rows)
hist_path = vc.HISTORY_CSV
old = pd.read_csv(hist_path, dtype={"date": str}) if hist_path.exists() else pd.DataFrame()
# 先剔除旧序列中本次要覆盖的日期，再 concat（避免同日期不稳排序 + keep=last 覆盖错乱）
new_dates = set(df["date"])
old = old[~old["date"].isin(new_dates)] if len(old) else old
merged = pd.concat([old, df], ignore_index=True).sort_values("date").drop_duplicates("date", keep="last")
merged.to_csv(hist_path, index=False, encoding="utf-8-sig")
print(f"\n合并后 {len(merged)} 行 ({merged['date'].min()}-{merged['date'].max()})")
print("近5日盘感趋势:", merged.tail(5)[["date", "vibe_score", "stage"]].values.tolist())
