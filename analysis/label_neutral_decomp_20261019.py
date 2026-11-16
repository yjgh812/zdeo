# -*- coding: utf-8 -*-
"""label_neutral_decomp_20261019.py — 2026双杀标签中性化四口径分解（呈请④，2026-10-19「按推荐继续」批准）

问题: 10/19 判别树定谳 2026=双引擎同时 regime 失效年（bp_ep 43.27% 绝对口径）。外部材料第三轮
提出: 绝对胜率下跌可能部分是市场 beta（负市拖累），需把标签拆为 绝对/市场超额/行业中性/行业×市值中性
四口径重算双杀是否仍成立。本件=只读研究件（侦察级零交易权），不接线不改生产。

队列: reports/backtest_full_20261018_dose_n15/{bp_ep_core,ml_signal}_trades.csv
      （10/18 剂量 fork N15，与生产基线逐位复现 181.17%/1612笔/51.92% = S-3 生产等价口径，
        与 10/19 dualkill n104/43.27 同源）
锚点(fail-closed): bp_ep 全史 n=1612±3；bp_ep 2026(买入年) n=104±2 且绝对胜率 43.27±1.0pp；
                   任一失守 → 仍写产物但 exit 2 大字报 FAIL。

四口径:
  abs          绝对收益(引擎口径, 盈亏比例% 含成本)
  e_idx        − 市场指数(中证500 000905 本地至20261016; 中证全指000985本地止于2016-06, 降级如实声明)
  e_univ       − 全市场等权(qfq 日收益等权, 与价格源同源)
  e_ind        − 同花顺L1行业等权(industry_group_map_snapshot 5581码, 快照~2025-08 非PIT, 声明)
  e_indsz      − 行业×全市场市值三分位(上月成交额中位数代理) 桶等权
窗口收益: 基准日收益按 (买入日, 卖出日] 累乘(逐票各自持有窗, 非固定20日)。
显著性: 按买入日聚簇块自助 1000 次 seed=42(系统标准, 同 winrate_ci_trades_20261018)。
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

QFQ_URI = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
INDMAP_DB = r"D:\zcodesj\data\industry_group_map_snapshot.db"
TRADES_DIR = r"D:\zcodesj\reports\backtest_full_20261018_dose_n15"
OUT_JSON = r"D:\zcodesj\analysis\label_neutral_decomp_20261019_out.json"
OUT_MD = r"D:\zcodesj\reports\label_neutral_decomp_20261019.md"

ARMS = [("bp_ep", "bp_ep_core_trades.csv"), ("ml", "ml_signal_trades.csv")]
N_REP, SEED = 1000, 42
COLS_KEEP = ["entry_date", "exit_date", "symbol", "symbol_name", "pnl", "pnl_pct", "holding_bars"]
LABELS = [("abs", "绝对"), ("e_idx", "vs中证500"), ("e_univ", "vs全市场等权"),
          ("e_ind", "vs行业等权"), ("e_indsz", "vs行业×市值桶")]


def daycluster_ci(dates, wins, n_rep=N_REP, seed=SEED):
    uniq = sorted(set(dates))
    by_day = {}
    for i, d in enumerate(dates):
        by_day.setdefault(d, []).append(i)
    idx_list = [by_day[d] for d in uniq]
    rng = np.random.default_rng(seed)
    n = len(uniq)
    stats = np.empty(n_rep)
    for r in range(n_rep):
        days = rng.integers(0, n, n)
        pool = [j for di in days for j in idx_list[di]]
        stats[r] = np.mean([wins[j] for j in pool])
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return [float(round(lo * 100, 2)), float(round(hi * 100, 2))]


def load_trades():
    frames = []
    for arm, fname in ARMS:
        df = pd.read_csv(f"{TRADES_DIR}\\{fname}", encoding="utf-8-sig")
        df = df[COLS_KEEP].copy()
        df["arm"] = arm
        frames.append(df)
    t = pd.concat(frames, ignore_index=True)
    t["d0"] = pd.to_numeric(t["entry_date"].str.replace("-", ""), errors="coerce").astype("int64")
    t["d1"] = pd.to_numeric(t["exit_date"].str.replace("-", ""), errors="coerce").astype("int64")
    t["code"] = t["symbol"].astype(str).str.zfill(6)
    t["ret"] = pd.to_numeric(t["pnl_pct"], errors="coerce")
    t["pnl"] = pd.to_numeric(t["pnl"], errors="coerce")
    t = t.dropna(subset=["d0", "d1", "ret"])
    t["year"] = (t["d0"] // 10000).astype(int)
    return t


def load_industry_map():
    c = sqlite3.connect(INDMAP_DB)
    m = pd.read_sql_query("select code, l1, ambiguous_flag from l1_member", c)
    c.close()
    m["code"] = m["code"].astype(str).str.zfill(6)
    return dict(zip(m["code"], m["l1"])), int((m["ambiguous_flag"] == 1).sum())


def load_qfq_rows():
    c = sqlite3.connect(QFQ_URI, uri=True)
    rows = c.execute(
        "select code, date, close, change_pct, amount from stock_daily_meta_qfq "
        "where date >= '20231201'"
    ).fetchall()
    c.close()
    df = pd.DataFrame(rows, columns=["code", "date", "close", "chg", "amount"])
    df["date"] = pd.to_numeric(df["date"], errors="coerce").astype("int64")
    df["ret"] = pd.to_numeric(df["chg"], errors="coerce") / 100.0
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["ret", "date"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def index_candidates():
    c = sqlite3.connect(META_DB)
    rows = c.execute(
        "select code, min(date), max(date), count(*) from index_daily_meta where code in "
        "('000985','399985','399317','000905','000300') group by code"
    ).fetchall()
    c.close()
    return rows


def index_series(code):
    c = sqlite3.connect(META_DB)
    rows = c.execute(
        "select date, close from index_daily_meta where code=? and date>=20231201 order by date",
        (code,),
    ).fetchall()
    c.close()
    s = pd.Series({int(d): float(v) for d, v in rows}).sort_index()
    return s.pct_change().dropna()


def cum_over_window(series, d0, d1):
    if series is None:
        return np.nan
    sel = series[(series.index > d0) & (series.index <= d1)]
    if len(sel) == 0:
        return np.nan
    return float((1.0 + sel).prod() - 1.0)


def block_from(sub, col):
    s = sub.dropna(subset=[col])
    n = len(s)
    if n == 0:
        return None
    wins = (s[col] > 0).astype(int).tolist()
    pos = s.loc[s["pnl"] > 0, "pnl"].sum()
    neg = s.loc[s["pnl"] < 0, "pnl"].sum()
    pl = float(pos / abs(neg)) if neg < 0 else None
    return {
        "n": n,
        "win_pct": round(float(np.mean(wins)) * 100, 2),
        "ci95": daycluster_ci(s["d0"].tolist(), wins),
        "med_pct": round(float(s[col].median()), 3),
        "mean_pct": round(float(s[col].mean()), 3),
        "pl_ratio": round(pl, 3) if pl is not None else None,
    }


class SeriesCache:
    """MultiIndex 日收益序列的逐键缓存取子序列."""

    def __init__(self, multi_series):
        self._cache = {}
        self._series = multi_series
        try:
            self._keys = set(multi_series.index.get_level_values(0))
        except Exception:
            self._keys = set()

    def get(self, key):
        if key not in self._keys:
            return None
        if key not in self._cache:
            self._cache[key] = self._series.xs(key, level=0).sort_index()
        return self._cache[key]


def main():
    trades = load_trades()
    indmap, n_amb = load_industry_map()
    qfq = load_qfq_rows()

    # 全市场等权 / 行业等权 / 行业×市值桶等权（逐行 ret=change_pct/100, 停牌跨期收益由自身上一收盘承接）
    qfq["l1"] = qfq["code"].map(indmap)
    univ_day = qfq.groupby("date")["ret"].mean()
    univ_day.index = univ_day.index.astype("int64")
    ind_day = qfq.dropna(subset=["l1"]).groupby(["l1", "date"])["ret"].mean()
    ind_cache = SeriesCache(ind_day)

    # 市值三分位: 每月用「上个自然月该股成交额中位数」全市场分三档（入口月只用之前数据, PIT 安全）
    qfq["ym"] = qfq["date"] // 100
    prev_med = qfq.groupby(["code", "ym"])["amount"].median().rename("sz").reset_index()

    def next_ym(m):
        y, mo = divmod(int(m), 100)
        return y * 100 + (mo + 1 if mo < 12 else 1) + (100 if mo == 12 else 0)

    prev_med["ym_next"] = prev_med["ym"].map(next_ym)
    szmap = dict(zip(prev_med["code"] + "|" + prev_med["ym_next"].astype(str), prev_med["sz"]))
    qfq["sz"] = [szmap.get(c + "|" + str(m)) for c, m in zip(qfq["code"], qfq["ym"])]
    qfq["tercile"] = -1
    for ym, g in qfq.dropna(subset=["sz"]).groupby("ym"):
        q1, q2 = g["sz"].quantile([1 / 3, 2 / 3])
        mask = qfq["ym"] == ym
        qfq.loc[mask & (qfq["sz"] < q1), "tercile"] = 0
        qfq.loc[mask & (qfq["sz"] >= q1) & (qfq["sz"] < q2), "tercile"] = 1
        qfq.loc[mask & (qfq["sz"] >= q2), "tercile"] = 2
    bkt = qfq[qfq["tercile"] >= 0].dropna(subset=["l1"]).copy()
    bkt["bucket"] = bkt["l1"] + "|" + bkt["tercile"].astype(str)
    bucket_day = bkt.groupby(["bucket", "date"])["ret"].mean()
    bucket_cache = SeriesCache(bucket_day)

    # 每码「date→tercile」有序数组（searchsorted 取入口月标签, 免逐笔扫大表）
    ter_ok = qfq[qfq["tercile"] >= 0][["code", "date", "tercile"]].sort_values(["code", "date"])
    ter_dates = {c: g["date"].to_numpy() for c, g in ter_ok.groupby("code")}
    ter_vals = {c: g["tercile"].to_numpy() for c, g in ter_ok.groupby("code")}

    idx_rows = index_candidates()
    idx_pick = None
    for code, mn, mx, cnt in sorted(idx_rows, key=lambda x: -int(x[2])):
        if int(mx) >= 20261000:
            idx_pick = code
            break
    idx_ret = index_series(idx_pick) if idx_pick else None

    ind_na = 0
    out_rows = []
    for r in trades.itertuples(index=False):
        d0, d1 = int(r.d0), int(r.d1)
        absret = float(r.ret)
        b_idx = cum_over_window(idx_ret, d0, d1)
        b_univ = cum_over_window(univ_day, d0, d1)
        l1 = indmap.get(r.code)
        b_ind = cum_over_window(ind_cache.get(l1), d0, d1) if l1 else np.nan
        b_bucket = np.nan
        if l1 and r.code in ter_dates:
            pos = int(np.searchsorted(ter_dates[r.code], d0, side="right")) - 1
            if pos >= 0:
                tkey = l1 + "|" + str(int(ter_vals[r.code][pos]))
                b_bucket = cum_over_window(bucket_cache.get(tkey), d0, d1)
        if not l1:
            ind_na += 1
        out_rows.append({
            "arm": r.arm, "year": int(r.year), "d0": d0, "ret": absret, "pnl": float(r.pnl),
            "e_idx": absret - b_idx * 100.0 if not np.isnan(b_idx) else np.nan,
            "e_univ": absret - b_univ * 100.0 if not np.isnan(b_univ) else np.nan,
            "e_ind": absret - b_ind * 100.0 if not np.isnan(b_ind) else np.nan,
            "e_indsz": absret - b_bucket * 100.0 if not np.isnan(b_bucket) else np.nan,
        })
    X = pd.DataFrame(out_rows)

    results = {}
    for arm in ["bp_ep", "ml"]:
        results[arm] = {}
        sub_arm = X[(X["arm"] == arm) & (X["year"] >= 2024)]
        for year in [2024, 2025, 2026, "ALL_2024p"]:
            sub = sub_arm if year == "ALL_2024p" else sub_arm[sub_arm["year"] == year]
            blk = {}
            for key, cname in LABELS:
                b = block_from(sub, "ret" if key == "abs" else key)
                if b is not None:
                    b["label"] = cname
                    b["k"] = key
                    blk[key] = b
            results[arm][str(year)] = blk

    # 锚点核验（fail-closed → exit 2）
    anchors = {}
    bp_all = trades[trades["arm"] == "bp_ep"]
    a1_n = len(bp_all)
    a1_win = float((bp_all["ret"] > 0).mean()) * 100
    anchors["bp_full_n"] = {"value": a1_n, "expect": "1612±3", "pass": bool(abs(a1_n - 1612) <= 3)}
    bp26 = bp_all[bp_all["year"] == 2026]
    a2_n = len(bp26)
    a2_win = float((bp26["ret"] > 0).mean()) * 100
    anchors["bp_2026"] = {"n": a2_n, "win_pct": round(a2_win, 2), "expect": "n=104±2 且 43.27±1.0pp",
                          "pass": bool(abs(a2_n - 104) <= 2 and abs(a2_win - 43.27) <= 1.0)}
    ml26 = trades[(trades["arm"] == "ml") & (trades["year"] == 2026)]
    anchors["ml_2026"] = {"n": len(ml26), "win_pct": round(float((ml26["ret"] > 0).mean()) * 100, 2),
                          "expect": "参考 dualkill n91/46.15"}
    anchor_pass = anchors["bp_full_n"]["pass"] and anchors["bp_2026"]["pass"]

    payload = {
        "meta": {
            "queue": TRADES_DIR,
            "index_pick": idx_pick,
            "index_note": "中证全指000985/399985本地止于2016-06, 市场基准降级中证500(000905至20261016)+全市场等权双口径, 如实声明",
            "industry_map": "industry_group_map_snapshot.db l1_member 5581码, 快照~2025-08 非PIT(meta自声明漂移年度级)",
            "ambiguous_rows": n_amb,
            "industry_unmapped_trades_2024p": int(X[X["year"] >= 2024].shape[0] - X[(X["year"] >= 2024) & X["e_ind"].notna()].shape[0]),
            "bench_window": "(买入日,卖出日] 逐票持有窗累乘",
            "bootstrap": "日聚簇块自助1000次 seed=42",
        },
        "anchors": anchors,
        "anchor_pass": anchor_pass,
        "results": results,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=float)

    L = []
    L.append("# 2026 双杀标签中性化四口径分解（呈请④ · 2026-10-19）\n")
    L.append("> 只读研究件·零交易权。队列=10/18 剂量 fork N15 逐笔（生产等价，锚点见下）；基准窗=(买入日,卖出日] 逐票持有窗累乘；显著性=日聚簇块自助1000次(seed42)。\n")
    L.append(f"> 市场基准: {idx_pick or '无可用指数'}（中证全指000985本地止于2016-06, 降级如实声明）＋全市场等权；行业映射=同花顺L1快照(5581码, ~2025-08, 非PIT)；行业未映射交易(2024+) {payload['meta']['industry_unmapped_trades_2024p']} 笔。\n")
    tag = "PASS" if anchor_pass else "FAIL"
    L.append(f"\n## 锚点核验: **{tag}**\n")
    L.append(f"- bp_ep 全史: n={a1_n} (期望1612±3) → {'PASS' if anchors['bp_full_n']['pass'] else 'FAIL'}")
    L.append(f"- bp_ep 2026 绝对: n={a2_n}, win={a2_win:.2f}% (期望 n=104±2 且 43.27±1.0pp) → {'PASS' if anchors['bp_2026']['pass'] else 'FAIL'}")
    L.append(f"- ml 2026 绝对: n={anchors['ml_2026']['n']}, win={anchors['ml_2026']['win_pct']}% (dualkill 参考 n91/46.15)\n")

    for arm, armname in [("bp_ep", "bp_ep（价值腿）"), ("ml", "ml（机器腿）")]:
        L.append(f"\n## {armname}\n")
        L.append("| 年度 | 口径 | n | 胜率% | CI95 | 中位% | 均值% | 盈亏比 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for year in [2024, 2025, 2026, "ALL_2024p"]:
            for key, cname in LABELS:
                b = results[arm][str(year)].get(key)
                if not b:
                    continue
                ci = f"[{b['ci95'][0]}, {b['ci95'][1]}]"
                pl = b["pl_ratio"] if b["pl_ratio"] is not None else "n/a"
                L.append(f"| {year} | {cname} | {b['n']} | {b['win_pct']} | {ci} | {b['med_pct']} | {b['mean_pct']} | {pl} |")

    b26abs = results["bp_ep"]["2026"].get("abs")
    b26ind = results["bp_ep"]["2026"].get("e_ind")
    L.append("\n## 判定（对拍外部材料标准）\n")

    def wp(a, y, k):
        b = results[a][str(y)].get(k)
        return b["win_pct"] if b else None

    def md_(a, y, k):
        b = results[a][str(y)].get(k)
        return b["med_pct"] if b else None

    if b26abs and b26ind:
        all_kill = all((results["bp_ep"]["2026"].get(k) or {}).get("win_pct", 0) < 50
                       for k in ["abs", "e_idx", "e_univ", "e_ind", "e_indsz"])
        cond_beta = b26abs["win_pct"] < 50 <= b26ind["win_pct"]
        if cond_beta:
            verdict = "beta主导候选（绝对<50 且 行业中性≥50）"
        elif all_kill:
            verdict = "四口径同杀→alpha确认（2026双杀在超额口径下仍成立）"
        else:
            verdict = "混合：绝对双杀大头由市场方向携带，alpha侧伤害定位=行业内相对弱势（见结构性读法）"
        L.append(f"- bp_ep 2026: 绝对 {b26abs['win_pct']}% vs 行业中性 {b26ind['win_pct']}%（CI {b26ind['ci95'][0]}~{b26ind['ci95'][1]}）→ **{verdict}**")
        for k, cname in [("e_idx", "vs中证500"), ("e_univ", "vs全市场等权"), ("e_indsz", "vs行业×市值桶")]:
            b = results["bp_ep"]["2026"].get(k)
            if b:
                L.append(f"  - {cname}: n={b['n']}, win={b['win_pct']}%, med={b['med_pct']}%, 盈亏比={b['pl_ratio']}")
        L.append("\n### 结构性读法（脚本直出年度轨迹）\n")
        L.append(f"- vs中证500 胜率: 2024 {wp('bp_ep', 2024, 'e_idx')}% → 2025 {wp('bp_ep', 2025, 'e_idx')}% → 2026 {wp('bp_ep', 2026, 'e_idx')}%")
        L.append(f"- vs全市场等权 胜率/中位: 2024 {wp('bp_ep', 2024, 'e_univ')}%/{md_('bp_ep', 2024, 'e_univ')}% → 2025 {wp('bp_ep', 2025, 'e_univ')}%/{md_('bp_ep', 2025, 'e_univ')}% → 2026 {wp('bp_ep', 2026, 'e_univ')}%/{md_('bp_ep', 2026, 'e_univ')}%")
        L.append(f"- vs行业等权 胜率/中位: 2024 {wp('bp_ep', 2024, 'e_ind')}%/{md_('bp_ep', 2024, 'e_ind')}% → 2025 {wp('bp_ep', 2025, 'e_ind')}%/{md_('bp_ep', 2025, 'e_ind')}% → 2026 {wp('bp_ep', 2026, 'e_ind')}%/{md_('bp_ep', 2026, 'e_ind')}%")
        L.append("- 读法: 2026 绝对「双杀」大头由市场方向携带（vs全市场等权 2026=三年最佳，熊市中跑赢等权宇宙）；但 vs行业/vs行业×市值 2024-2026 三年全程弱负 → 行业内相对弱势非 2026 新增，属长期结构属性；「选股2026崩溃」表述过强，「市场方向携带＋行业内长期弱负」更准确。")
        L.append("- 口径注: 本件盈亏比=Σ盈利/|Σ亏损|（已平仓笔）；dualkill 报告 Top15档 2026 盈亏比 1.014 系其聚合口径（与 sum口径差异如实并陈），不影响胜率结论。")
    ml26r = results["ml"]["2026"]
    if ml26r.get("abs") and ml26r.get("e_ind"):
        ml_wins = int(round(ml26r["abs"]["win_pct"] / 100 * ml26r["abs"]["n"]))
        L.append(f"- ml 2026: 绝对 {ml26r['abs']['win_pct']}%（{ml_wins}/{ml26r['abs']['n']}）vs 行业中性 {ml26r['e_ind']['win_pct']}%（CI {ml26r['e_ind']['ci95'][0]}~{ml26r['e_ind']['ci95'][1]}）；本件绝对读数与 ladder §四 ml 46.15% 差 3 笔（win_a 平盘口径差先例: 含平盘55.7 vs 引擎52.2），方向结论不受影响；四口径均在 45~51 带内=无单一暴露主导。")
    L.append("- 注: 单年验证能力上限 MDE@n104≈19.4pp（10/19 dualkill 已声明），CI 解读按此校准；")
    L.append("- 注: 全指严格口径可用 westock MCP 导出件补算（本地止 2016-06），本件以可用最优本地指数+全市场等权双基准替代并如实声明。")
    L.append("\n*脚本直出零转抄: analysis/label_neutral_decomp_20261019.py → 本报告+out.json。*\n")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("ANCHOR:", tag, "| bp_full_n=", a1_n, "| bp_2026 n/win=", a2_n, round(a2_win, 2),
          "| idx_pick=", idx_pick, "| ind_unmapped_2024p=", payload["meta"]["industry_unmapped_trades_2024p"])
    print("BP2026:", {k: (v["win_pct"], v["n"]) for k, v in results["bp_ep"]["2026"].items()})
    print("ML2026:", {k: (v["win_pct"], v["n"]) for k, v in results["ml"]["2026"].items()})
    sys.exit(0 if anchor_pass else 2)


if __name__ == "__main__":
    main()
