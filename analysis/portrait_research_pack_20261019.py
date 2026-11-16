# -*- coding: utf-8 -*-
"""portrait_research_pack_20261019.py — 呈请②: 画像研究包（2026-10-19「继续」令）

四件（外部材料第三轮画像问 + 第二轮画像1/2收口），全读侧观察级·零交易权：
  A. 王者格(涨停入场×缩量) 格内外 MFE/MAE(runup20/dd20) 与 f20 终点收益对照
  B. LOTO(留一游资) 稳定性 + dim_stats 玩家×量带分桶 dispersion（n<30 一律标注不单独结论）
  C. T+0(hold_days=0) 剔除敏感性
  D. 同结构匹配对照组: 全市场 (日×行业×涨停态×量比带) 桶, 桶内非游资均值 f20 为反事实,
     段超额=段f20−桶均值; 王者格 vs 全体 vs 格外 三层对照
数据: entry_portrait.db episodes/dim_stats (六家交割单1201段) + qfq 全市场行情(桶构建)。
纪律: 观察级不接线; 83%先验已带 OOS 腰斩+月度价值域 n=0 注记(§9.4), 全部结论按观察级解读。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

EP_DB = r"file:D:/zcodesj/data/entry_portrait.db?mode=ro"
QFQ_URI = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
INDMAP_DB = r"D:\zcodesj\data\industry_group_map_snapshot.db"
OUT_JSON = r"D:\zcodesj\analysis\portrait_research_pack_20261019_out.json"
OUT_MD = r"D:\zcodesj\reports\portrait_research_pack_20261019.md"
KING = ("zt_entry", 1, "vol_band", "<0.8缩量")


def zt_threshold(code):
    return 19.8 if code.startswith(("300", "301", "68")) else 9.8


def main():
    c = sqlite3.connect(EP_DB, uri=True)
    ep = pd.read_sql_query(
        "select player, code, name, d0, hold_days, zt_entry, vol_band, runup20, dd20, "
        "f5, f20, seg_ret, is_winner, industry from episodes", c)
    dims = pd.read_sql_query("select dim_name, bucket, player, n, win5, med_f20 from dim_stats where dim_name='vol_band'", c)
    c.close()
    ep["code"] = (ep["code"].astype(str).str.lower()
                  .str.replace(r"^(sh|sz|bj)", "", regex=True).str.zfill(6))
    ep["d0n"] = pd.to_numeric(ep["d0"].astype(str).str.replace("-", ""), errors="coerce")
    ep = ep.dropna(subset=["d0n"])
    ep["d0n"] = ep["d0n"].astype("int64")
    d_min, d_max = int(ep["d0n"].min()), int(ep["d0n"].max())

    king_mask = (ep["zt_entry"] == KING[1]) & (ep["vol_band"] == KING[3])
    ep["is_king"] = king_mask

    # ---- 全市场行情（桶构建） ----
    indmap = {}
    ci = sqlite3.connect(INDMAP_DB)
    for code, l1 in ci.execute("select code, l1 from l1_member"):
        indmap[str(code).zfill(6)] = l1
    ci.close()
    load_from = d_min // 10000 * 10000 - 200  # 粗缓冲
    cq = sqlite3.connect(QFQ_URI, uri=True)
    rows = cq.execute(
        "select code, date, change_pct, volume from stock_daily_meta_qfq where date>=?",
        (str(load_from),),
    ).fetchall()
    cq.close()
    Q = pd.DataFrame(rows, columns=["code", "date", "ret", "vol"])
    Q["date"] = pd.to_numeric(Q["date"], errors="coerce").astype("Int64")
    Q = Q.dropna(subset=["date", "ret"])
    Q["date"] = Q["date"].astype("int64")
    Q["ret"] = pd.to_numeric(Q["ret"], errors="coerce") / 100.0
    Q = Q.dropna(subset=["ret"])
    Q = Q.sort_values(["code", "date"]).reset_index(drop=True)
    Q = Q[(Q["date"] >= d_min - 300) & (Q["date"] <= d_max + 60)]

    g = Q.groupby("code", sort=False)
    logr = np.log1p(Q["ret"])
    cs = g["logr"] if False else logr.groupby(Q["code"]).cumsum()
    f20 = cs.groupby(Q["code"]).shift(-20) - cs
    f5 = cs.groupby(Q["code"]).shift(-5) - cs
    same20 = Q["code"].groupby(Q["code"]).shift(-20)
    Q["f20"] = np.where(same20 == Q["code"], np.exp(f20) - 1, np.nan)
    same5 = Q["code"].groupby(Q["code"]).shift(-5)
    Q["f5"] = np.where(same5 == Q["code"], np.exp(f5) - 1, np.nan)
    csvol = Q.groupby("code")["vol"].cumsum()
    v5p = (csvol.groupby(Q["code"]).shift(1) - csvol.groupby(Q["code"]).shift(6)) / 5.0
    Q["vr"] = np.where(v5p > 0, Q["vol"] / v5p, np.nan)
    thr = Q["code"].map(zt_threshold)
    Q["zt"] = Q["ret"] * 100 >= thr
    band = pd.cut(Q["vr"], [-np.inf, 0.8, 1.2, 2.0, np.inf],
                  labels=["<0.8缩量", "0.8-1.2平量", "1.2-2放量", ">=2爆量"])
    Q["band"] = band.astype(str)
    Q["l1"] = Q["code"].map(indmap)
    Q = Q.dropna(subset=["l1"])

    # 桶 = (日, 行业, 涨停态, 量比带) 层级回退: 最细可用层(非游资成员≥5) → 日×涨停×带 → 日×带
    Q["is_ep"] = False
    ep_keys = set(zip(ep["code"].tolist(), ep["d0n"].tolist()))
    key_series = pd.Series(list(zip(Q["code"].tolist(), Q["date"].tolist())), index=Q.index)
    Q["is_ep"] = key_series.isin(ep_keys).to_numpy()
    Q["bkey"] = Q["date"].astype(str) + "|" + Q["l1"] + "|" + Q["zt"].astype(str) + "|" + Q["band"]
    Q["bkey3"] = Q["date"].astype(str) + "|" + Q["zt"].astype(str) + "|" + Q["band"]
    Q["bkey2"] = Q["date"].astype(str) + "|" + Q["band"]
    valid = Q[~Q["is_ep"]].dropna(subset=["f20"])
    bmaps = {}
    for k in ["bkey", "bkey3", "bkey2"]:
        b = valid.groupby(k)["f20"].agg(["mean", "count"])
        bmaps[k] = b[b["count"] >= 5]["mean"].to_dict()

    Q["bmean20"] = np.nan
    Q["blevel"] = ""
    for k, lvl in [("bkey", "L3行业层"), ("bkey3", "L2日×涨停×带"), ("bkey2", "L1日×带")]:
        m = bmaps[k]
        hit = Q["bmean20"].isna() & Q[k].map(m).notna()
        Q.loc[hit, "bmean20"] = Q.loc[hit, k].map(m)
        Q.loc[hit, "blevel"] = lvl
    Q["ex20"] = Q["f20"] - Q["bmean20"]
    diag = {"n_q": int(len(Q)), "is_ep_true": int(Q["is_ep"].sum()),
            "bstat_sizes": {k: len(bmaps[k]) for k in bmaps},
            "bmean_hit": int(Q["bmean20"].notna().sum()),
            "blevel_counts": Q.loc[Q["bmean20"].notna(), "blevel"].value_counts().to_dict()}

    lookup = Q.set_index(["code", "date"])[["f20", "f5", "vr", "bmean20", "ex20", "band_q"] if False else ["f20", "bmean20"]].to_dict("index")

    ep["bucket_n"] = np.nan
    f20s, bms, exs = [], [], []
    for r in ep.itertuples(index=False):
        d = lookup.get((r.code, int(r.d0n)))
        if d:
            f20s.append(d.get("f20"))
            bms.append(d.get("bmean20"))
            exs.append((d.get("f20") - d["bmean20"]) if (d.get("f20") is not None and d.get("bmean20") is not None) else np.nan)
        else:
            f20s.append(np.nan)
            bms.append(np.nan)
            exs.append(np.nan)
    ep["mkt_f20"] = f20s
    ep["bucket_f20"] = bms
    ep["ex20"] = exs

    def blk(sub, col):
        s = sub.dropna(subset=[col])
        n = len(s)
        if n == 0:
            return {"n": 0}
        return {"n": n, "win_pct": round(float((s[col] > 0).mean()) * 100, 2),
                "med": round(float(s[col].median()), 4)}

    res = {"meta": {"episodes": int(len(ep)), "qfq_rows": int(len(Q)),
                    "king_def": "zt_entry=1 且 vol_band=<0.8缩量",
                    "bucket_def": "层级回退: (日×L1×涨停态×量比带)→(日×涨停×带)→(日×带), 取非游资成员≥5的最细层",
                    "bucket_diag": diag,
                    "note": "83%先验带OOS腰斩+月度价值域n=0注记, 全部观察级"}}

    # A. 王者格格内外
    seg = {}
    for label, sub in [("王者格", ep[ep["is_king"]]), ("格外", ep[~ep["is_king"]]), ("全体", ep)]:
        seg[label] = {
            "n": int(len(sub)),
            "mfe_runup20_med": round(float(sub["runup20"].median()), 4) if len(sub) else None,
            "mae_dd20_med": round(float(sub["dd20"].median()), 4) if len(sub) else None,
            "f20": blk(sub, "f20"),
            "ex20": blk(sub, "ex20"),
            "seg_ret_win": round(float((sub["seg_ret"] > 0).mean()) * 100, 2) if len(sub) else None,
        }
    res["A_king_vs_rest"] = seg

    # B. LOTO（王者格 + 全体）
    loto = {}
    for label, sub in [("王者格", ep[ep["is_king"]]), ("全体", ep)]:
        players = sorted(sub["player"].unique())
        rows = []
        full_win = float((sub["f20"] > 0).mean())
        for p in players:
            loo = sub[sub["player"] != p]
            w = float((loo["f20"] > 0).mean())
            pin = sub[sub["player"] == p]
            rows.append({"exclude": p, "n_excluded": int(len(pin)),
                         "player_own_win": round(float((pin["f20"] > 0).mean()), 3) if len(pin) else None,
                         "loo_win": round(w, 3), "swing_pp": round((w - full_win) * 100, 2)})
        loto[label] = {"full_win_f20": round(full_win, 3), "n": int(len(sub)), "rows": rows}
    res["B_loto"] = loto

    # dim_stats 玩家×量带 dispersion（库内预计算 win5）
    piv = dims.pivot_table(index="player", columns="bucket", values="win5", aggfunc="first")
    disp = {}
    for bucket in piv.columns:
        col = piv[bucket].dropna()
        if len(col) >= 3:
            disp[bucket] = {"players": int(len(col)), "win5_min": float(col.min()),
                            "win5_max": float(col.max()), "spread_pp": round(float(col.max() - col.min()) * 100, 1)}
    res["B_dimstats_dispersion"] = disp

    # C. T+0 剔除敏感性
    t0 = ep[ep["hold_days"] == 0]
    res["C_t0"] = {"hold0_n": int(len(t0)),
                   "king_with_t0": blk(ep[ep["is_king"]], "f20"),
                   "king_no_t0": blk(ep[(ep["is_king"]) & (ep["hold_days"] > 0)], "f20")}

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=float)

    L = ["# 画像研究包：王者格 MFE/MAE + LOTO + T+0 剔除 + 同结构匹配对照（呈请② · 2026-10-19）\n",
         "> 只读研究件·观察级·零交易权。样本=六家交割单 1201 段（黄金380/只核246/小蟒夫190/神明167/02IM136/丨木丨82）；83% 先验已带在案注记（批1'修正版 CI + postlabel OOS 腰斩 + 月度价值域 n=0），全部按观察级解读，n<30 不单独结论。\n",
         "## A. 王者格（涨停入场×缩量）格内外对照\n",
         "| 层 | n | runup20中位 | dd20中位 | f20胜率% | f20中位 | 超额(vs桶)胜率% | 超额中位 |",
         "|---|---|---|---|---|---|---|---|"]
    for label in ["王者格", "格外", "全体"]:
        d = seg[label]
        f = d.get("f20") or {}
        e = d.get("ex20") or {}
        L.append(f"| {label} | {d['n']} | {d['mfe_runup20_med']} | {d['mae_dd20_med']} | {f.get('win_pct','-')} | {f.get('med','-')} | {e.get('win_pct','-')} | {e.get('med','-')} |")
    L.append("\n## B. LOTO 留一游资稳定性\n")
    for label in ["王者格", "全体"]:
        d = loto[label]
        L.append(f"- {label}（n={d['n']}, f20全胜率 {d['full_win_f20']}）:")
        for r in d["rows"]:
            warn = " (n<30不单独结论)" if r["n_excluded"] is not None and r["n_excluded"] < 30 else ""
            L.append(f"  - 剔除 {r['exclude']}: 自身n={r['n_excluded']}, 自身f20胜率={r['player_own_win']}, LOO胜率={r['loo_win']} (摆动 {r['swing_pp']}pp){warn}")
    L.append("\n### 库内 dim_stats 玩家×量带 win5 dispersion\n")
    for bucket, d in disp.items():
        L.append(f"- {bucket}: {d['players']}位玩家, win5 {d['win5_min']}~{d['win5_max']} (极差 {d['spread_pp']}pp)")
    L.append("\n## C. T+0（hold_days=0）剔除敏感性\n")
    L.append(f"- hold_days=0 段数: {res['C_t0']['hold0_n']}")
    for k, cn in [("king_with_t0", "王者格含T0"), ("king_no_t0", "王者格剔T0")]:
        b = res["C_t0"].get(k) or {}
        L.append(f"- {cn}: n={b.get('n')}, f20胜率 {b.get('win_pct')}, 中位 {b.get('med')}")
    L.append("\n- 判读框架: 若王者格优势在 MFE(runup20) 显著大于格外而 f20 终点无差 → 路径型优势=只可做仓位调制不可做独立信号(材料判定采纳)；若匹配对照超额(vs桶)不显著 → 格子效应=同结构市场效应而非游资选股效应。n 见表，小样本层按 CI 解读。")
    L.append("\n*脚本直出零转抄: analysis/portrait_research_pack_20261019.py。*")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("DIAG:", json.dumps(diag, ensure_ascii=False))
    print("A:", json.dumps(seg, ensure_ascii=False, default=float))
    print("C:", json.dumps(res["C_t0"], ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
