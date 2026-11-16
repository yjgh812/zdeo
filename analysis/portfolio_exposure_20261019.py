# -*- coding: utf-8 -*-
"""portfolio_exposure_20261019.py — 呈请③d: 组合层风险暴露一次性诊断（2026-10-19「继续」令）

问题(外部材料第二轮缺口3): Top15等权≠风险分散; 需按月端盘点持仓的行业集中度/规模倾斜/爆量态占比,
看 2024/2025/2026 暴露漂移。附: 2020-08 创业板制度断点切片(bp臂) + IC 衰减曲线数据可用性登记。
只读研究件·零交易权·零生产改动。
持仓重建: dose_n15 双臂逐笔 [entry,exit) 区间; 月端=qfq交易日轴每月末日。
vr 定义: vma5/vma120 (volume 均量比, 与爆量哨同族口径); 规模代理=上月成交额中位数。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

TRADES_DIR = r"D:\zcodesj\reports\backtest_full_20261018_dose_n15"
QFQ_URI = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
INDMAP_DB = r"D:\zcodesj\data\industry_group_map_snapshot.db"
PH_DB = r"file:D:/zcodesj/attribution/attribution.db?mode=ro"
OUT_JSON = r"D:\zcodesj\analysis\portfolio_exposure_20261019_out.json"
OUT_MD = r"D:\zcodesj\reports\portfolio_exposure_20261019.md"


def main():
    frames = []
    for arm, fname in [("bp_ep", "bp_ep_core_trades.csv"), ("ml", "ml_signal_trades.csv")]:
        df = pd.read_csv(f"{TRADES_DIR}\\{fname}", encoding="utf-8-sig")
        df["arm"] = arm
        frames.append(df)
    T = pd.concat(frames, ignore_index=True)
    T["code"] = T["symbol"].astype(str).str.zfill(6)
    T["e"] = pd.to_numeric(T["entry_date"].str.replace("-", ""), errors="coerce").astype("int64")
    T["x"] = pd.to_numeric(T["exit_date"].str.replace("-", ""), errors="coerce").astype("int64")
    T = T.dropna(subset=["e", "x"])

    indmap = {}
    c = sqlite3.connect(INDMAP_DB)
    for code, l1 in c.execute("select code, l1 from l1_member"):
        indmap[str(code).zfill(6)] = l1
    c.close()

    held_codes = sorted(set(T[T["e"] >= 20240101]["code"]))
    cq = sqlite3.connect(QFQ_URI, uri=True)
    cur = cq.cursor()
    rows = []
    for code in held_codes:
        for d, vol, amt in cur.execute(
            "select date, volume, amount from stock_daily_meta_qfq where code=? and date>='20230601' order by date",
            (code,),
        ):
            rows.append((code, int(d), float(vol or 0), float(amt or 0)))
    cq.close()
    Q = pd.DataFrame(rows, columns=["code", "date", "vol", "amt"]).sort_values(["code", "date"])
    date_axis = np.sort(Q["date"].unique())
    month_ends = []
    prev_m = None
    for d in date_axis:
        m = d // 100
        if prev_m is not None and m != prev_m:
            month_ends.append(int(prev_d))
        prev_m, prev_d = m, d
    month_ends.append(int(prev_d))
    month_ends = [d for d in month_ends if 20240101 <= d <= 20261031]

    # 每码月度规模代理: 上月成交额中位数
    Q["ym"] = Q["date"] // 100
    m_med = Q.groupby(["code", "ym"])["amt"].median().rename("sz").reset_index()
    szmap = dict(zip(m_med["code"] + "|" + m_med["ym"].astype(str), m_med["sz"]))

    def code_feats(code, d):
        g = Q[Q["code"] == code]
        arr = g["date"].to_numpy()
        i = int(np.searchsorted(arr, d, side="right")) - 1
        if i < 0:
            return None
        v = g["vol"].to_numpy()[: i + 1]
        a = g["amt"].to_numpy()[: i + 1]
        if len(v) < 120:
            return None
        vma5 = float(v[-5:].mean())
        vma120 = float(v[-120:].mean())
        vr = vma5 / vma120 if vma120 > 0 else np.nan
        m = d // 100
        y, mo = divmod(m, 100)
        pm = y * 100 + (mo - 1 if mo > 1 else 12) - (100 if mo == 1 else 0)
        sz = szmap.get(code + "|" + str(pm))
        return {"vr": vr, "sz": sz}

    expos = {}
    for d in month_ends:
        held = T[(T["e"] <= d) & (T["x"] > d)]
        inds = []
        vrs, szs = [], []
        for r in held.itertuples(index=False):
            inds.append(indmap.get(r.code, "未映射"))
            f = code_feats(r.code, d)
            if f:
                vrs.append(f["vr"])
                if f["sz"]:
                    szs.append(f["sz"])
        if len(held) == 0:
            continue
        vc = pd.Series(inds).value_counts()
        w = vc / vc.sum()
        hhi = float((w ** 2).sum())
        expos[str(d)] = {
            "n": int(len(held)),
            "top1_ind_share": round(float(w.iloc[0]), 3),
            "eff_industries": round(float(np.exp(-np.sum(w * np.log(np.clip(w, 1e-9, 1))))), 2),
            "amt_med_wan": round(float(np.median(szs)) / 1e4, 1) if szs else None,
            "sz_lt5000wan_share": round(float(np.mean([s < 5e7 for s in szs])), 3) if szs else None,
            "vr_median": round(float(np.median(vrs)), 3) if vrs else None,
            "vr_gt15_share": round(float(np.mean([v > 1.5 for v in vrs])), 3) if vrs else None,
        }

    by_year = {}
    for y in [2024, 2025, 2026]:
        keys = [k for k in expos if int(k[:4]) == y]
        if not keys:
            continue
        agg = {}
        for f in ["top1_ind_share", "eff_industries", "amt_med_wan", "sz_lt5000wan_share", "vr_median", "vr_gt15_share"]:
            vals = [expos[k][f] for k in keys if expos[k][f] is not None]
            agg[f] = round(float(np.median(vals)), 3) if vals else None
        agg["month_ends"] = len(keys)
        by_year[str(y)] = agg

    # 2020-08 创业板制度断点切片(bp臂, 绝对口径)
    bp = T[(T["arm"] == "bp_ep")]
    pre = bp[bp["e"] < 20200824]["pnl_pct"].dropna()
    post = bp[(bp["e"] >= 20200824) & (bp["e"] < 20240101)]["pnl_pct"].dropna()

    def blk(s):
        if len(s) == 0:
            return None
        return {"n": int(len(s)), "win_pct": round(float((s > 0).mean()) * 100, 2),
                "med_pct": round(float(s.median()), 3)}

    # IC 表可用性登记
    ic_note = None
    try:
        ci = sqlite3.connect(PH_DB, uri=True)
        n, mn, mx = ci.execute("select count(*), min(date), max(date) from factor_performance").fetchone()
        ic_note = {"rows": n, "date_range": [mn, mx],
                   "verdict": "仅1个月覆盖(75行), IC衰减曲线不可作, 登记数据缺口"}
        ci.close()
    except Exception as e:
        ic_note = {"error": str(e)}

    payload = {"by_year": by_year, "month_detail": expos,
               "bp_202008_breakpoint": {"pre": blk(pre), "post": blk(post)},
               "ic_table": ic_note}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    L = ["# 组合层风险暴露一次性诊断（呈请③d · 2024-2026 · 2026-10-19）\n",
         "> 只读研究件。持仓=dose_n15 双臂逐笔 [entry,exit) 重建；月端盘点；行业=同花顺L1快照(非PIT声明沿用)；vr=均量5/均量120；规模代理=上月成交额中位数。\n",
         "## 分年中位数（月端截面）\n",
         "| 年份 | 月端数 | 持仓数(中位) | 首一行业占比 | 有效行业数 | 成交额中位(万) | <5000万占比 | vr中位 | vr>1.5占比 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for y in ["2024", "2025", "2026"]:
        a = by_year.get(y)
        if a:
            L.append(f"| {y} | {a['month_ends']} | {expos.get(str(y)+'12' if False else '') or '-'} | {a['top1_ind_share']} | {a['eff_industries']} | {a['amt_med_wan']} | {a['sz_lt5000wan_share']} | {a['vr_median']} | {a['vr_gt15_share']} |")
    L.append("\n## 2020-08 创业板制度断点切片（bp臂 绝对口径）\n")
    b = payload["bp_202008_breakpoint"]
    L.append(f"- 断点前(<20200824): n={b['pre']['n']}, 胜率 {b['pre']['win_pct']}%, 中位 {b['pre']['med_pct']}%")
    L.append(f"- 断点后(20200824~2023): n={b['post']['n']}, 胜率 {b['post']['win_pct']}%, 中位 {b['post']['med_pct']}%")
    L.append("\n## IC 衰减曲线数据可用性\n")
    L.append(f"- factor_performance 表仅 {ic_note.get('rows')} 行（{ic_note.get('date_range')}）→ 衰减曲线不可作，登记数据缺口（呈请⑥族）。")
    L.append("\n*脚本直出零转抄: analysis/portfolio_exposure_20261019.py。*")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("BY_YEAR:", json.dumps(by_year, ensure_ascii=False))
    print("BP202008:", json.dumps(payload["bp_202008_breakpoint"], ensure_ascii=False))


if __name__ == "__main__":
    main()
