# -*- coding: utf-8 -*-
"""L-2 翻案 Stage B：前向 walk 评估器（prereg=PREREG_L2_SEAT_COND_RANK_FWALK_20260909，判据冻结）
用法: python l2_seat_cond_rank_fwalk_eval.py [--asof YYYYMMDD]  (默认=今天)
口径: 前向事件=trade_date ≥ 20260910 且 fwd10 完整; 高组=inst_ratio≥0.0294 / 低组=inst_ratio≤-0.0416;
结果=同日 LHB 事件等权基准超额; 晋升级=①高>低 ②高−低日度聚类t≥2.0 ③月度方向一致率≥60%(≥3月)。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

PREREG = r"D:\zcodesj\data\PREREG_L2_SEAT_COND_RANK_FWALK_20260909.json"
OUT_DIR = r"D:\zcodesj\reports\L2_SEAT_COND_RANK_20260909"
FW_START = "20260910"
TH_HI, TH_LO = 0.0294, -0.0416
MIN_EVENTS = 60

os.makedirs(OUT_DIR, exist_ok=True)


def log(s):
    print(s, flush=True)


def daily_t(series):
    s = pd.Series(series).dropna()
    if len(s) < 10:
        return None, int(len(s))
    return float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))), int(len(s))


def main():
    asof = sys.argv[sys.argv.index("--asof") + 1] if "--asof" in sys.argv else datetime.now().strftime("%Y%m%d")
    R = {"asof": asof, "fw_start": FW_START, "th_hi": TH_HI, "th_lo": TH_LO}

    db = sqlite3.connect(r"D:\workbuddyspace\databases\fundamental_data.db")
    seat = pd.read_sql(
        "SELECT trade_date, security_code, operate_dept_name, buy, sell, net FROM lhb_seat_detail WHERE trade_date >= '2026-09-10'",
        db)
    db.close()
    if len(seat) == 0:
        R["verdict"] = "样本不足顺延(前向事件=0)"
        json.dump(R, open(os.path.join(OUT_DIR, f"fwalk_eval_{asof}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        log(R["verdict"])
        return
    seat["date"] = seat["trade_date"].str.replace("-", "", regex=False)
    seat["is_inst"] = seat["operate_dept_name"].str.contains("机构专用", na=False)
    seat["to"] = seat["buy"].fillna(0) + seat["sell"].fillna(0)
    g = seat.groupby(["date", "security_code"])
    ev = g.agg(inst_net=("net", lambda s: s[seat.loc[s.index, "is_inst"]].sum()),
               denom=("to", "sum")).reset_index()
    ev = ev[(ev["denom"] > 0) & (ev["date"] >= FW_START) & (ev["date"] <= asof)].copy()
    ev["inst_ratio"] = ev["inst_net"] / ev["denom"]
    log(f"前向事件(≤{asof})={len(ev):,}")

    db = sqlite3.connect(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
    px = pd.read_sql(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= '20260901'",
        db)
    db.close()
    px = px.sort_values(["code", "date"]).reset_index(drop=True)
    px["fwd10"] = px.groupby("code")["close"].shift(-10) / px["close"] - 1.0
    ev = ev.merge(px[["code", "date", "fwd10"]].rename(columns={"code": "security_code"}),
                  on=["security_code", "date"], how="left")
    n_incomplete = int(ev["fwd10"].isna().sum())
    ev = ev.dropna(subset=["fwd10"]).copy()
    ev["ex"] = ev["fwd10"] - ev.groupby("date")["fwd10"].transform("mean")
    hi = ev[ev["inst_ratio"] >= TH_HI]
    lo = ev[ev["inst_ratio"] <= TH_LO]
    R["n_events_complete"] = int(len(ev))
    R["n_incomplete_dropped"] = n_incomplete
    R["n_hi"] = int(len(hi))
    R["n_lo"] = int(len(lo))
    log(f"完整事件={len(ev):,} (剔期末不足 {n_incomplete}) | 高组={len(hi)} 低组={len(lo)}")

    if len(hi) < MIN_EVENTS or len(lo) < MIN_EVENTS:
        R["verdict"] = f"样本不足顺延(高{len(hi)}/低{len(lo)} < {MIN_EVENTS}，prereg 顺延条款)"
        json.dump(R, open(os.path.join(OUT_DIR, f"fwalk_eval_{asof}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        log(R["verdict"])
        return

    mean_hi, mean_lo = float(hi["ex"].mean()), float(lo["ex"].mean())
    d_hi = hi.groupby("date")["ex"].mean()
    d_lo = lo.groupby("date")["ex"].mean()
    sp = (d_hi - d_lo).dropna()
    t_sp, n_days = daily_t(sp)
    ev["ym"] = ev["date"].str[:6]
    ev["grp"] = np.where(ev["inst_ratio"] >= TH_HI, "hi",
                         np.where(ev["inst_ratio"] <= TH_LO, "lo", "mid"))
    pm = ev[ev["grp"].isin(["hi", "lo"])].groupby("ym").apply(
        lambda d: d[d["grp"] == "hi"]["ex"].mean() > d[d["grp"] == "lo"]["ex"].mean(),
        include_groups=False)
    ok1 = mean_hi > mean_lo
    ok2 = t_sp is not None and t_sp >= 2.0
    ok3 = float(pm.mean()) >= 0.60 and len(pm) >= 3
    R["result"] = {"mean_hi_pct": round(mean_hi * 100, 3), "mean_lo_pct": round(mean_lo * 100, 3),
                   "spread_daily_t": round(t_sp, 2) if t_sp else None, "n_days": n_days,
                   "monthly_consistency": round(float(pm.mean()), 3), "n_months": int(len(pm)),
                   "ok1_高大于低": ok1, "ok2_t≥2": ok2, "ok3_月度一致≥60%": ok3}
    R["verdict"] = "PASS_CANDIDATE" if (ok1 and ok2 and ok3) else "FAIL"
    log(f"高组 {mean_hi*100:+.3f}% vs 低组 {mean_lo*100:+.3f}% | 日度t={t_sp} | 月度一致率 {pm.mean():.0%} -> {R['verdict']}")

    with open(os.path.join(OUT_DIR, f"fwalk_eval_{asof}.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1)
    md = ["# L-2 翻案 Stage B 前向 walk 评估", "",
          f"> asof={asof} | prereg=PREREG_L2_SEAT_COND_RANK_FWALK_20260909(判据冻结) | 阈值 hi≥{TH_HI} / lo≤{TH_LO}", "",
          f"- 前向完整事件 {len(ev):,}（高组 {len(hi)} / 低组 {len(lo)}；期末不足剔 {n_incomplete}）",
          f"- 高组均值超额 {mean_hi*100:+.3f}% vs 低组 {mean_lo*100:+.3f}%（差 {((mean_hi-mean_lo)*100):+.3f}%）",
          f"- 高−低 日度聚类 t = {t_sp}（{n_days} 日）",
          f"- 月度方向一致率 {pm.mean():.0%}（{len(pm)} 个月）", "",
          f"## verdict = {R['verdict']}", "",
          "判定语义见 prereg：PASS_CANDIDATE 仅提请独立研究信号族立项，生产接线另须 wfa_gate 新指纹+主人逐项裁决。", "",
          "---", "*风险提示：历史/前向统计均不代表未来实盘收益。*"]
    with open(os.path.join(OUT_DIR, f"FWALK_EVAL_{asof}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    log("OK -> " + OUT_DIR)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
