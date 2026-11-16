# -*- coding: utf-8 -*-
"""A3 事件驱动触发研究——高管增持事件 fwd20 条件期望（学习计划阶段A3首件, 只读零交易风险）
数据: analysis/ggcg_raw_akshare_20261020.csv (东财高管持股变动 147,120 行, akshare 1.18.55)
口径冻结(歧义处双臂并报):
  事件 = 增减=增持, 变动数量>0, 剔ST/科创688/北交(4,8,9开头), (代码,公告日)去重
  入场 = 公告日次一有交易bar开盘(E1, 主判据) 与 第3bar开盘(E3, 披露滞后敏感臂)
  判据 = 入场收盘起 fwd20 收盘收益; 安慰剂 = 同票同位置+120个交易日移位的fwd20(自对照, 吸收个股漂移)
  效应 = 事件fwd20 − 安慰剂fwd20 的中位与正占比; 分年切片=regime条件化检验
  限流 = 每年最多400事件分层抽样(算力边界, 如实声明)
"""
import json
import sqlite3
import statistics

import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
RAW = r"D:\zcodesj\analysis\ggcg_raw_akshare_20261020.csv"
OUT = r"D:\zcodesj\analysis\event_holder_increase_probe_20261020.json"
CAP_PER_YEAR = 400
FWD = 20
SHIFT = 120


def _dnorm(d):
    d = str(d)
    return d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:]}"


def main():
    df = pd.read_csv(RAW, dtype={"代码": str})
    df = df[df["持股变动信息-增减"] == "增持"].copy()
    df["代码"] = df["代码"].str.zfill(6)
    df = df[~df["代码"].str.startswith(("688", "4", "8", "9"))]
    df = df[~df["名称"].astype(str).str.contains("ST")]
    df = df[pd.to_numeric(df["持股变动信息-变动数量"], errors="coerce") > 0]
    df["公告日"] = _dnorm_col(df["公告日"])
    df = df.dropna(subset=["公告日"])
    df = df.drop_duplicates(subset=["代码", "公告日"])
    df["year"] = df["公告日"].str[:4]
    df = df[df["year"] >= "2016"]
    sampled = df.groupby("year", group_keys=False).head(CAP_PER_YEAR)
    print(f"[load] 增持事件(去重后)={len(df)}, 分层抽样={len(sampled)}, 码数={sampled['代码'].nunique()}", flush=True)

    codes = sorted(sampled["代码"].unique())
    db = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    bars = {}
    for c in codes:
        bars[c] = db.execute(
            "select date,open,close from stock_daily_meta_qfq where code=? order by date", (c,)
        ).fetchall()
    db.close()

    recs = []
    for _, r in sampled.iterrows():
        code, ann = r["代码"], r["公告日"]
        rows = bars.get(code) or []
        dates = [x[0] for x in rows]
        i0 = None
        for i, dt in enumerate(dates):
            if dt > ann:
                i0 = i
                break
        if i0 is None or i0 + FWD >= len(rows) or i0 + SHIFT + FWD >= len(rows):
            continue
        out = {}
        for tag, ie in (("E1", i0), ("E3", min(i0 + 2, len(rows) - 1))):
            entry_px = rows[ie][1]
            j = ie + FWD
            if j < len(rows):
                out[tag] = rows[j][2] / entry_px - 1
        ie_p = i0 + SHIFT
        if ie_p + FWD < len(rows):
            out["placebo"] = rows[ie_p + FWD][2] / rows[ie_p][1] - 1
        if "E1" in out and "placebo" in out:
            out.update({"code": code, "year": r["year"], "ann": ann,
                        "eff_E1": out.get("E1", 0) - out["placebo"]})
            recs.append(out)

    res = {"n_events": len(recs), "years": sorted(set(x["year"] for x in recs))}
    for tag in ("E1", "E3"):
        vals = [x[tag] for x in recs if tag in x]
        eff = [x[f"eff_{tag}"] if f"eff_{tag}" in x else x["E1"] - x["placebo"] for x in recs]
        res[f"{tag}"] = {
            "fwd20_med": round(statistics.median(vals), 4),
            "fwd20_win": round(sum(1 for v in vals if v > 0) / len(vals), 4),
            "eff_med_vs_placebo": round(statistics.median(eff), 4),
            "eff_pos": round(sum(1 for v in eff if v > 0) / len(eff), 4),
        }
    byyear = {}
    for y in res["years"]:
        ys = [x for x in recs if x["year"] == y]
        eff = [x["eff_E1"] for x in ys]
        byyear[y] = {"n": len(ys), "eff_med": round(statistics.median(eff), 4) if eff else None,
                     "eff_pos": round(sum(1 for v in eff if v > 0) / len(eff), 4) if eff else None}
    res["by_year"] = byyear
    res["conventions"] = {"entry": "公告日次bar开盘(E1主)/第3bar(E3)", "fwd": 20,
                          "placebo": "+120bar同票自对照", "cap": CAP_PER_YEAR,
                          "literature_ref": "公告类事件+2.9%/15日, regime条件化(外部文献, 未本地验证)"}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("[E1]", json.dumps(res["E1"], ensure_ascii=False), flush=True)
    print("[E3]", json.dumps(res["E3"], ensure_ascii=False), flush=True)
    print("[by_year]", json.dumps(byyear, ensure_ascii=False)[:400], flush=True)
    print("[done] →", OUT, flush=True)


def _dnorm_col(s):
    return s.astype(str).map(lambda d: d if "-" in d else f"{d[:4]}-{d[4:6]}-{d[6:]}") if "-" not in str(
        s.iloc[0]) else s


if __name__ == "__main__":
    main()
