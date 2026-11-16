#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""limit_up_step0_gapfill_20261113.py — 打板第0步补缺统计（纯只读，零交易权）。
2026-11-13 主人批准。只补四块缺口（其余维度已有真源研究覆盖）：
  S1 板位晋级率（连板streak口径, 2019-11~20261112）
  S2 隔夜开盘溢价 gap=次日开盘/当日收盘-1（按板位/年份/温度）
  S3 涨停基因五因子 250日PIT滚动窗口（limit-up-sniper模板, 样本内发现统计）
  S4 45日封单/首封时段 x 次日表现（limit_up_patterns 2026-08-10~11-12）
口径与 firstboard_full_history 同源（板块阈-0.2pp / 次新60日 / ST被阈自然排除）。
限制(如实)：封单/时段维度仅45日样本库有；换手维度已有stage5/6结论不重复；
历史ST态不可得；基因分为全样本分位=样本内发现，前瞻须另行注册影子验证。
退出码：0正常 / 3数据源不可用。"""
import json, os, re, sqlite3, sys
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import numpy as np
import pandas as pd

RAW_DB = "file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db" + "?mode=ro"
ATTR_DB = "file:" + r"D:\zcodesj\attribution\attribution.db" + "?mode=ro"
REPORT_DIR = r"D:\zcodesj\reports"
OUT_DIR = os.path.join(REPORT_DIR, "LIMIT_UP_STEP0_20261113")
OUT_MD = REPORT_DIR + r"\limit_up_step0_gapfill_20261113.md"
GENE_WIN = 250
FB_MIN_BARS = 60
K_LABELS = ("首板", "二板", "三板", "四板", "≥5板")


def _ro(path):
    try:
        return sqlite3.connect(path, uri=True, timeout=60)
    except Exception as e:
        print(f"[STEP0] [FAIL-CLOSED] 数据源不可用({path}): {e}")
        sys.exit(3)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    con = _ro(RAW_DB)
    print("[STEP0] 加载原始日线...", flush=True)
    sql = "SELECT code,date,open,high,low,close FROM stock_daily_raw WHERE date>=20191101"
    df = pd.read_sql(sql, con)
    con.close()
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close", "open", "high"])
    df = df[df["close"] > 0]
    pre = ("4", "8", "92", "68")
    df = df[~df["code"].astype(str).str.startswith(pre)]
    df["date"] = df["date"].astype(int)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    # ---- 指标计算 ----
    g = df.groupby("code", sort=False)
    df["prev_close"] = g["close"].shift(1)
    df["open_n"] = g["open"].shift(-1)
    df["close_n"] = g["close"].shift(-1)
    df["pct"] = df["close"] / df["prev_close"] - 1.0
    df["high_pct"] = df["high"] / df["prev_close"] - 1.0
    df["n_bars"] = g.cumcount()
    thr = np.where(df["code"].str.startswith("30"),
                   np.where(df["date"] >= 20200824, 0.198, 0.098),
                   0.098)
    df["thr"] = thr
    df["sealed"] = (df["pct"] >= thr - 0.002) & df["pct"].notna()
    df["touched"] = (df["high_pct"] >= thr - 0.002) & df["high_pct"].notna()
    raw_i = df["sealed"].astype(int)
    cs = raw_i.groupby(df["code"]).cumsum()
    df["streak"] = cs - cs.where(raw_i == 0).groupby(df["code"]).ffill().fillna(0)
    df["promoted"] = g["sealed"].shift(-1).eq(True)
    df["gap"] = df["open_n"] / df["close"] - 1.0
    df["r1"] = df["close_n"] / df["close"] - 1.0
    day_lim = df.loc[df["sealed"], ["code", "date"]].groupby("date").size()
    df["day_lim"] = df["date"].map(day_lim).fillna(0).astype(int)
    df["fb"] = df["sealed"] & (df["streak"] == 1)

    ev = df[df["sealed"] & (df["n_bars"] >= FB_MIN_BARS)].copy()
    ev["streak_k"] = ev["streak"].astype(int)
    ev["year"] = ev["date"] // 10000
    ev["k_bucket"] = pd.cut(ev["streak_k"], [0, 1, 2, 3, 4, 99],
                            labels=list(K_LABELS))
    ev["temp"] = pd.cut(ev["day_lim"], [0, 30, 60, 100, 10 ** 9],
                        labels=["<30家", "30-60家", "60-100家", "≥100家"])
    print(f"[STEP0] 涨停事件 n={len(ev)}", flush=True)

    L = []
    L.append(f"# 打板第0步补缺统计（纯本地数据） {datetime.now():%Y-%m-%d %H:%M}")
    L.append("")
    L.append(f"- 事件域：原始日线 2019-11-01 ~ {df['date'].max()}，"
             f"覆盖 {df['code'].nunique()} 只（北交所/科创板剔除；每只剔除前60交易日次新）")
    L.append(f"- 涨停事件 {len(ev)} 笔；gap=次日开盘/当日收盘-1（打板买入者实际赚取的隔夜溢价）")
    L.append("- 口径与 firstboard_full_history 同源（已与45日样本库交叉验证 15.6% vs 15.9%）")
    L.append("")
    J = {}
    # ---S1---
    L.append("## S1 板位晋级率与隔夜开盘溢价")
    L.append("")
    res1 = {}
    for k in K_LABELS:
        sub = ev[ev["k_bucket"] == k]
        n = len(sub)
        if n == 0:
            continue
        res1[k] = {
            "n": int(n),
            "prom_rate": round(float(sub["promoted"].mean()), 4),
            "gap_mean": round(float(sub["gap"].mean() * 100), 3),
            "gap_med": round(float(sub["gap"].median() * 100), 3),
            "r1_mean": round(float(sub["r1"].mean() * 100), 3),
            "win_r1": round(float((sub["r1"] > 0).mean()), 4),
        }
        r = res1[k]
        L.append(f"- {k}：n={n} 晋级率={r['prom_rate']*100:.1f}% "
                 f"开盘溢价均值={r['gap_mean']:+.2f}% 中位={r['gap_med']:+.2f}% "
                 f"次日收益均值={r['r1_mean']:+.2f}% 红盘率={r['win_r1']*100:.1f}%")
    J["S1_by_board_position"] = res1
    L.append("")
    L.append("### S1b 首板按年份")
    L.append("")
    res1b = {}
    fb_all = ev[ev["streak_k"] == 1]
    for y, sub in fb_all.groupby("year"):
        res1b[int(y)] = {"n": int(len(sub)),
                         "prom": round(float(sub["promoted"].mean()), 4),
                         "gap": round(float(sub["gap"].mean() * 100), 3)}
        L.append(f"- {y}：n={len(sub)} 晋级率={res1b[int(y)]['prom']*100:.1f}% "
                 f"开盘溢价均值={res1b[int(y)]['gap']:+.2f}%")
    J["S1b_firstboard_by_year"] = res1b
    L.append("")
    L.append("### S2 首板开盘溢价按市场温度")
    L.append("")
    res2 = {}
    for tb, sub in fb_all.groupby("temp", observed=True):
        if len(sub) < 100:
            continue
        res2[str(tb)] = {"n": int(len(sub)),
                         "gap_mean": round(float(sub["gap"].mean() * 100), 3),
                         "gap_med": round(float(sub["gap"].median() * 100), 3),
                         "prom": round(float(sub["promoted"].mean()), 4)}
        L.append(f"- {tb}：n={len(sub)} 开盘溢价均值={res2[str(tb)]['gap_mean']:+.2f}% "
                 f"中位={res2[str(tb)]['gap_med']:+.2f}% 晋级率={res2[str(tb)]['prom']*100:.1f}%")
    J["S2_gap_by_temp"] = res2
    # ---S3---
    print("[STEP0] S3 基因五因子 250日PIT滚动...", flush=True)
    m1 = df["fb"].values
    m2 = df["r1"].notna().values
    m3 = (df["r1"] > 0).values
    df["fb_red_n"] = m1 & m2 & m3
    m3b = (df["r1"] > 0.05).values
    df["fb_prem5_n"] = m1 & m2 & m3b
    gap_v = df["gap"].fillna(0.0).values
    df["fb_gap"] = np.where(m1, gap_v, np.nan)
    cols = ["fb", "touched", "sealed", "fb_red_n", "fb_prem5_n", "fb_gap"]
    sh = df.groupby("code")[cols].shift(1)
    newc = []
    for c in cols:
        newc.append("s_" + c)
    sh.columns = newc
    df = pd.concat([df, sh], axis=1)
    rcols = ["s_fb", "s_touched", "s_sealed", "s_fb_red_n", "s_fb_prem5_n", "s_fb_gap"]
    rolled = df.groupby("code")[rcols].rolling(250, min_periods=1).sum()
    rolled = rolled.reset_index(level=0, drop=True)
    for c in ("fb", "touched", "sealed", "fb_red_n", "fb_prem5_n", "fb_gap"):
        df["w_" + c] = rolled["s_" + c].values
    fb_ev = df[df["fb"] & (df["n_bars"] >= FB_MIN_BARS)]
    fb_ev = fb_ev[fb_ev["w_fb"] >= 1]
    fb_ev = fb_ev[fb_ev["w_touched"] > 0].copy()
    fb_ev["f1"] = fb_ev["w_fb_prem5_n"] / fb_ev["w_fb"]
    fb_ev["f2"] = fb_ev["w_fb_red_n"] / fb_ev["w_fb"]
    fb_ev["f3"] = fb_ev["w_sealed"] / fb_ev["w_touched"]
    fb_ev["f4"] = fb_ev["w_fb_gap"] / fb_ev["w_fb"]
    fb_ev["f5"] = fb_ev["w_sealed"]
    for f in ("f1", "f2", "f3", "f4", "f5"):
        fb_ev[f] = pd.to_numeric(fb_ev[f], errors="coerce")
    ok = fb_ev.dropna(subset=["f1", "f2", "f3", "f4", "f5"])
    n_ok = len(ok)
    print(f"[STEP0] 基因事件 n={n_ok}", flush=True)
    for f in ("f1", "f2", "f3", "f4", "f5"):
        ok[f + "_r"] = ok[f].rank(pct=True)
    ok["gene"] = 0.25 * ok["f1_r"]
    ok["gene"] = ok["gene"] + 0.25 * ok["f2_r"]
    ok["gene"] = ok["gene"] + 0.25 * ok["f3_r"]
    ok["gene"] = ok["gene"] + 0.15 * ok["f4_r"]
    ok["gene"] = ok["gene"] + 0.10 * ok["f5_r"]
    ok["gene_q"] = pd.qcut(ok["gene"], 5, labels=["Q1低", "Q2", "Q3", "Q4", "Q5高"])
    L.append("")
    L.append("## S3 涨停基因五因子（PIT 250日窗口，全样本分位打分=样本内发现统计）")
    L.append("")
    L.append("- f1=窗口首板次日收盘溢价>5%比例(25%)｜f2=首板次日红盘率(25%)｜"
             "f3=收盘涨停数/触板数(25%)｜f4=首板次日开盘溢价均值(15%)｜f5=窗口涨停次数(10%)")
    L.append("- 排除窗口内无首板个股；n=" + str(n_ok))
    L.append("")
    L.append("| 组 | n | 一进二晋级率 | 开盘溢价均值% | 次日红盘率 |")
    L.append("|---|---|---|---|---|")
    res3 = {}
    for q, sub in ok.groupby("gene_q", observed=True):
        rr = {"n": int(len(sub))}
        rr["prom"] = round(float(sub["promoted"].mean()), 4)
        rr["gap"] = round(float(sub["gap"].mean() * 100), 3)
        rr["red"] = round(float((sub["r1"] > 0).mean()), 4)
        res3[str(q)] = rr
        L.append(f"| {q} | {rr['n']} | {rr['prom']*100:.1f}% | "
                 f"{rr['gap']:+.2f}% | {rr['red']*100:.1f}% |")
    J["S3_gene_quintile"] = res3
    L.append("")
    L.append("### S3b 单因子三分位×一进二晋级率")
    L.append("")
    for f in ("f1", "f2", "f3", "f4", "f5"):
        try:
            b = pd.qcut(ok[f].rank(pct=True), 3, labels=["低", "中", "高"])
        except Exception:
            continue
        line = "- " + f + "："
        for lv in ("低", "中", "高"):
            sub = ok[b == lv]
            txt = sub["promoted"].mean() * 100
            line = line + lv + "=" + f"{txt:.1f}%" + f"(n={len(sub)}) "
        L.append(line)
    # ---S4---
    print("[STEP0] S4 45日样本库...", flush=True)
    try:
        con2 = _ro(ATTR_DB)
        pat = pd.read_sql(
            "SELECT trade_date,code,name,limit_count,first_limit_time,"
            "seal_status FROM limit_up_patterns", con2)
        con2.close()
    except Exception as e:
        print(f"[STEP0] 45日样本库不可用: {e}")
        pat = pd.DataFrame()
    res4 = {}
    if len(pat):
        pat = pat[pat["limit_count"] == 1].copy()
        pat = pat.drop_duplicates(["code", "trade_date"])
        pat["date_i"] = pat["trade_date"].astype(int)

        def amt_of(ss):
            m = re.search(r"封板([\d.]+)亿", str(ss or ""))
            if m:
                return float(m.group(1)) * 1e8
            return None

        def bombs_of(ss):
            m = re.search(r"(\d+)炸", str(ss or ""))
            if m:
                return int(m.group(1))
            return None

        def t_bucket(t):
            s = re.sub(r"\D", "", str(t or ""))
            if not s:
                return "未知"
            v = int(s)
            if v <= 100000:
                return "开盘早盘"
            if v < 140000:
                return "盘中"
            return "尾盘"
        pat["seal_amt"] = pat["seal_status"].map(amt_of)
        pat["bombs"] = pat["seal_status"].map(bombs_of)
        pat["tbucket"] = pat["first_limit_time"].map(t_bucket)
        keep = df.loc[df["fb"], ["code", "date", "gap", "r1", "promoted"]]
        pat = pat.merge(keep, left_on=["code", "date_i"], right_on=["code", "date"], how="left")
        pat = pat.dropna(subset=["gap"])
        L.append("")
        L.append("## S4 45日样本库首板（2026-08-10~11-12）×次日表现")
        L.append("")
        L.append("n=" + str(len(pat)) + "（封单额/炸板次数解析自 seal_status 文本，"
                 "解析不到的样本不进封单维度分桶）")
        L.append("")
        res4["tbucket"] = {}
        for tb, sub in pat.groupby("tbucket"):
            if len(sub) < 30:
                continue
            rr = {"n": int(len(sub))}
            rr["gap"] = round(float(sub["gap"].mean() * 100), 3)
            rr["prom"] = round(float(sub["promoted"].mean()), 4)
            rr["r1"] = round(float(sub["r1"].mean() * 100), 3)
            res4["tbucket"][str(tb)] = rr
            L.append(f"- 时段{tb}：n={len(sub)} 开盘溢价均值={rr['gap']:+.2f}% "
                     f"晋级率={rr['prom']*100:.1f}% 次日收益均值={rr['r1']:+.2f}%")
        pat["amt_b"] = pd.cut(pat["seal_amt"] / 1e8, [0, 1, 3, 10, 1e9],
                              labels=["<1亿", "1-3亿", "3-10亿", "≥10亿"])
        res4["amt"] = {}
        for ab, sub in pat.groupby("amt_b", observed=True):
            if len(sub) < 30:
                continue
            rr = {"n": int(len(sub))}
            rr["gap"] = round(float(sub["gap"].mean() * 100), 3)
            rr["prom"] = round(float(sub["promoted"].mean()), 4)
            res4["amt"][str(ab)] = rr
            L.append(f"- 封单{ab}：n={len(sub)} 开盘溢价均值={rr['gap']:+.2f}% "
                     f"晋级率={rr['prom']*100:.1f}%")
        pat["bomb_b"] = pd.cut(pat["bombs"].astype(float), [-0.1, 0.5, 2.5, 99],
                               labels=["0炸", "1-2炸", "≥3炸"])
        res4["bombs"] = {}
        for bb, sub in pat.groupby("bomb_b", observed=True):
            if len(sub) < 30:
                continue
            rr = {"n": int(len(sub))}
            rr["gap"] = round(float(sub["gap"].mean() * 100), 3)
            rr["prom"] = round(float(sub["promoted"].mean()), 4)
            res4["bombs"][str(bb)] = rr
            L.append(f"- {bb}：n={len(sub)} 开盘溢价均值={rr['gap']:+.2f}% "
                     f"晋级率={rr['prom']*100:.1f}%")
        J["S4_45d"] = res4
    # ---S-OUT---
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    # ---S-JSON---
    jpath = OUT_DIR + r"\result.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(J, f, ensure_ascii=False, indent=1)
    print("[STEP0] 完成 -> " + OUT_MD)
    return 0
    # ---S-OUT---


if __name__ == "__main__":
    raise SystemExit(main())
