# -*- coding: utf-8 -*-
"""右侧启动收官研究增补件: 多基准超额列 + 块bootstrap 95%CI + 分年显著性 (2026-10-11)

呈请来源: reports/rightside_spec_coverage_20261011.md §三呈请1 (主人「继续」令按推荐路径执行)
输入: reports/rightside_startup_20261011/events_main.csv (主样本4,313 = 每段起首个合格D)
基准:
  沪深300/中证500/中证1000 = vipdoc指数日K (格式唯一owner=tdx_reader; 指数无复权概念)
  申万L1行业 → 定案=等权行业均值指数: industry_map(stock_basic,通达信二级)定成员, qfq日收益等权合成
    (801系行业指数无逐股映射/ths_sector仅2020+/sw_industry_daily 880系无名称桥, 详见industry_bench注记)
窗口口径(与事件收益精确同窗): 事件出场日 = qfq库D后第H根bar的日期(H∈{20,60});
  指数基准 = close(出场日)/open(入场日)-1; 行业基准 = close(出场日)/close(入场日)-1(该表无open,口径注记)
  超额 = f_H_net(事件净收益,含0.45%费) - 基准收益(无费)
事件bar约定(探针锁定): f_H = close(bar i+H)/open(bar i+1)-1 原始口径, i=D所在bar下标; f_net=f-0.45
bootstrap: 事件日聚类, 唯一事件日按时间切5日块, 1000次有放回, 95% percentile CI, seed=20261011
输出: stdout + reports/rightside_startup_20261011/multi_benchmark_summary.json + events_excess.csv
自检: ①f60_net/f20_net均值复现收官口径(+4.54%/+0.91%) ②基准窗口覆盖率 ③行业桥接覆盖率
性质: 研究件零交易权, 只读不回填
"""
import os, sys, json, sqlite3, bisect
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from tdx_reader import read_day  # vipdoc格式唯一owner

EV = r"D:\zcodesj\reports\rightside_startup_20261011\events_main.csv"
OUT_DIR = r"D:\zcodesj\reports\rightside_startup_20261011"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:/workbuddyspace/databases/fundamental_data.db"
SWDB = r"D:/workbuddyspace/databases/sw_industry.db"
FEE = 0.45  # %
SEED = 20261011
BLOCK = 5
NBOOT = 1000


def d8(x):
    """日期规范化为int YYYYMMDD(qfq/sw库date为TEXT, 指数为int)"""
    s = str(x).replace("-", "")
    return int(s) if s.isdigit() else None


def load_events():
    em = pd.read_csv(EV, dtype={"code": str})
    em["code"] = em["code"].str.zfill(6)
    em["d_date"] = em["d_date"].astype(int)
    return em


def stock_exit_dates(em, horizons=(20, 60)):
    """逐股从qfq库取入场日(D+1 bar)与出场日(bar i+H, 与f_H同下标), 返回 {(code,D): {"entry":..,H:..}} + miss统计"""
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    dates_by = {}
    for code in em["code"].unique():
        rows = con.execute("select date from stock_daily_meta_qfq where code=? order by date", (code,)).fetchall()
        dates_by[code] = [r[0] for r in rows]
    con.close()
    out, miss = {}, {"D_not_in_bars": 0, "no_next_bar": 0, "no_exit_bar": 0}
    for code, D in zip(em["code"], em["d_date"]):
        ds = dates_by.get(code, [])
        i = bisect.bisect_left(ds, str(D))
        if i >= len(ds) or ds[i] != str(D):
            miss["D_not_in_bars"] += 1
            continue
        if i + 1 >= len(ds):
            miss["no_next_bar"] += 1
            continue
        m = {"entry": d8(ds[i + 1])}
        for H in horizons:
            j = i + H
            m[H] = d8(ds[j]) if j < len(ds) else None
            if m[H] is None:
                miss["no_exit_bar"] += 1
        out[(code, D)] = m
    return out, miss


def index_benches():
    """vipdoc三指数: {name: (dates_sorted, open_map, close_map)}"""
    res = {}
    for nm, c in [("hs300", "000300"), ("zz500", "000905"), ("zz1000", "000852")]:
        rows = read_day("sh", c)
        dates = [r[0] for r in rows]
        res[nm] = (dates, {r[0]: r[1] for r in rows}, {r[0]: r[4] for r in rows})
    return res


def industry_bench(codes):
    """行业基准=等权行业均值指数(定案): industry_map(src=stock_basic, 通达信二级行业名)定成员,
    成员股qfq日change_pct(百分数)等权均值逐日合成行业指数路径(cumprod), 全历史2012-2026覆盖。
    口径注记: 成员=当前行业映射快照(含库内存续股), 与事件域同源幸存者口径; 无801/881行业指数全史源
    (sw_industry_kline_sw 801系无逐股映射/ths_sector仅2020+/sw_industry_daily 880系无名称桥)。"""
    def suf(code):
        return code + (".SH" if code.startswith(("60", "68")) else ".SZ")
    con = sqlite3.connect(f"file:{FUND}?mode=ro", uri=True)
    code2ind = {}
    uniq = sorted(set(codes))
    for k in range(0, len(uniq), 400):
        chunk = uniq[k:k + 400]
        q = ",".join("?" * len(chunk))
        ts_args = [suf(c) for c in chunk]
        for ts, nm in con.execute(f"select ts_code,industry_name from industry_map where ts_code in ({q}) and industry_name<>''", ts_args):
            code2ind[ts.split(".")[0]] = nm
    # 全体成员(按行业)
    members = {}
    for ts, nm in con.execute("select ts_code,industry_name from industry_map where industry_name<>''"):
        members.setdefault(nm, []).append(ts.split(".")[0])
    con.close()
    # qfq全成员日收益 -> 行业日均值 -> 指数路径
    cq = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    all_codes = sorted({c for v in members.values() for c in v})
    frames = []
    for k in range(0, len(all_codes), 800):
        chunk = all_codes[k:k + 800]
        q = ",".join("?" * len(chunk))
        df = pd.read_sql_query(f"select code,date,change_pct from stock_daily_meta_qfq where code in ({q})", cq, params=chunk)
        frames.append(df)
    cq.close()
    px = pd.concat(frames, ignore_index=True).dropna(subset=["change_pct"])
    px["date"] = px["date"].map(d8)
    px = px.dropna(subset=["date"])
    px["industry"] = px["code"].map({c: i for i, cs in members.items() for c in cs})
    px = px.dropna(subset=["industry"])
    g = px.groupby(["industry", "date"])["change_pct"].mean() / 100.0
    ind2series = {}
    for ind, ser in g.groupby(level=0):
        s = ser.droplevel(0).sort_index()
        lvl = (1.0 + s).cumprod()
        ind2series[ind] = (lvl.to_dict(), sorted(lvl.index))
    n_mapped = sum(1 for c in codes if c in code2ind)
    n_bridge = sum(1 for c in codes if code2ind.get(c) in ind2series)
    stat = {"codes": len(set(codes)), "has_industry_map": n_mapped,
            "industry_bridged": n_bridge, "industries_total": len(set(code2ind.values())),
            "industries_bridged": len(ind2series), "members_total": len(all_codes),
            "method": "eqw_industry_mean_from_qfq"}
    return code2ind, ind2series, stat


def block_bootstrap_ci(vals_by_date, block=BLOCK, nboot=NBOOT, seed=SEED):
    """事件日聚类块bootstrap: 唯一日期按时间切block日块, 有放回重采样至覆盖全部日期"""
    rng = np.random.default_rng(seed)
    dates = sorted(vals_by_date)
    blocks = [dates[k:k + block] for k in range(0, len(dates), block)]
    nblk = int(np.ceil(len(dates) / block))
    means = []
    for _ in range(nboot):
        pick = rng.integers(0, len(blocks), size=nblk)
        vals = [v for bi in pick for d in blocks[bi] for v in vals_by_date[d]]
        if vals:
            means.append(float(np.mean(vals)))
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def stat_block(series, em):
    """一条超额序列的统计: 总体+分年"""
    s = pd.Series(series, index=em.index).dropna()
    out = {"n": int(len(s))}
    if len(s) < 5:
        out["note"] = "n<5"
        return out
    v = s.values
    t = float(np.mean(v) / (np.std(v, ddof=1) / np.sqrt(len(v))))
    byd = {}
    for d, x in zip(em.loc[s.index, "d_date"], v):
        byd.setdefault(int(d // 10000) * 10000 + int(d // 100) % 100 * 100 + d % 100, []).append(float(x))
    lo, hi = block_bootstrap_ci(byd)
    out.update(mean_pp=round(float(np.mean(v)) * 100, 3), med_pp=round(float(np.median(v)) * 100, 3),
               win=round(float((v > 0).mean()) * 100, 1), t=round(t, 2),
               ci95=[round(lo * 100, 3), round(hi * 100, 3)])
    yr = {}
    s_y = s.copy()
    s_y.index = em.loc[s.index, "d_date"] // 10000
    for y, g in s_y.groupby(level=0):
        gv = g.values
        yt = float(np.mean(gv) / (np.std(gv, ddof=1) / np.sqrt(len(gv)))) if len(gv) > 2 else None
        yr[int(y)] = {"n": int(len(gv)), "mean_pp": round(float(np.mean(gv)) * 100, 2),
                      "win": round(float((gv > 0).mean()) * 100, 1), "t": round(yt, 2) if yt is not None else None}
    out["by_year"] = yr
    return out


def main():
    em = load_events()
    print(f"[load] events={len(em)} span={em.d_date.min()}-{em.d_date.max()}")
    # 自检①: 复现收官口径(报告line13: 全史4,313 h60净+3.19% t=7.16, 中位-1.12; 注: spec coverage所引+4.54%/2,492系修复前旧版)
    m60, m20 = em.f60_net.mean(), em.f20_net.mean()
    t60 = m60 / (em.f60_net.std(ddof=1) / np.sqrt(len(em)))
    print(f"[check1] f60_net mean={m60:.4f}% t={t60:.2f} (收官 +3.19%/t7.16) | f20_net mean={m20:.4f}% | med60={em.f60_net.median():.2f}% (收官-1.12)")
    ok1 = abs(m60 - 3.19) <= 0.02 and 7.0 <= t60 <= 7.7 and abs(em.f60_net.median() - (-1.12)) <= 0.02
    print(f"[check1] {'PASS' if ok1 else 'FAIL'}")

    xd, miss = stock_exit_dates(em)
    print(f"[exit_dates] mapped={len(xd)}/{len(em)} miss={miss}")

    idx = index_benches()
    code2ind, ind2series, bridge = industry_bench(sorted(em.code.unique()))
    print(f"[bridge] {bridge}")

    res = {}
    for nm in ["hs300", "zz500", "zz1000", "sw"]:
        for H in (20, 60):
            col = f"{nm}_ex{H}"
            vals = np.full(len(em), np.nan)
            for k, (code, D) in enumerate(zip(em.code, em.d_date)):
                mm = xd.get((code, D))
                if not mm or not mm.get(H):
                    continue
                entry, exit_ = mm["entry"], mm[H]
                if nm == "sw":
                    obj, kind = ind2series.get(code2ind.get(code)), "sw"
                else:
                    obj, kind = idx[nm], "idx"
                if obj is None:
                    continue
                if kind == "idx":
                    dates, omap, cmap = obj
                    if entry not in omap or exit_ not in cmap:
                        continue
                    i, j = bisect.bisect_left(dates, entry), bisect.bisect_left(dates, exit_)
                    if i >= len(dates) or j >= len(dates) or dates[i] != entry or dates[j] != exit_:
                        continue
                    vals[k] = cmap[exit_] / omap[entry] - 1
                else:
                    cmap, dl = obj  # (close_map, dates_sorted)
                    if entry not in cmap or exit_ not in cmap:
                        continue
                    i, j = bisect.bisect_left(dl, entry), bisect.bisect_left(dl, exit_)
                    if i >= len(dl) or j >= len(dl) or dl[i] != entry or dl[j] != exit_:
                        continue
                    vals[k] = cmap[exit_] / cmap[entry] - 1  # 行业无open, close-close口径
            res[col] = vals
    # 组装
    data = {"code": em.code.values, "d_date": em.d_date.values}
    for nm in ["hs300", "zz500", "zz1000", "sw"]:
        for H in (20, 60):
            data[f"{nm}_ex{H}"] = em[f"f{H}_net"].values / 100 - res[f"{nm}_ex{H}"]
    ex = pd.DataFrame(data)
    ex.to_csv(os.path.join(OUT_DIR, "events_excess.csv"), index=False, encoding="utf-8-sig")
    print(f"[out] events_excess.csv rows={len(ex)}")

    summary = {"meta": {"date": "2026-10-11", "source_spec": "rightside_spec_coverage_20261011 §三呈请1",
                        "events": len(em), "fee_pct": FEE, "block": BLOCK, "nboot": NBOOT, "seed": SEED,
                        "check1_pass": bool(ok1), "exit_mapped": len(xd), "exit_miss": miss, "bridge": bridge},
               "benches": {}}
    for nm in ["hs300", "zz500", "zz1000", "sw"]:
        for H in (20, 60):
            col = f"{nm}_ex{H}"
            summary["benches"][col] = stat_block(ex[col].values, em)
    with open(os.path.join(OUT_DIR, "multi_benchmark_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print("\n=== 主结果 (f60_net超额, pp) ===")
    for nm, cn in [("hs300", "沪深300"), ("zz500", "中证500"), ("zz1000", "中证1000"), ("sw", "申万L1行业")]:
        b = summary["benches"][f"{nm}_ex60"]
        print(f"{cn}: n={b.get('n')} mean={b.get('mean_pp')} win={b.get('win')}% t={b.get('t')} CI95={b.get('ci95')}")
    print("\n=== 对照 (f20_net超额) ===")
    for nm, cn in [("hs300", "沪深300"), ("sw", "申万L1行业")]:
        b = summary["benches"][f"{nm}_ex20"]
        print(f"{cn}: n={b.get('n')} mean={b.get('mean_pp')} win={b.get('win')}% t={b.get('t')} CI95={b.get('ci95')}")
    print("\n=== 分年显著性 (hs300_ex60 / sw_ex60 mean_pp, t) ===")
    y60, ys = summary["benches"]["hs300_ex60"], summary["benches"]["sw_ex60"]
    for y in sorted(set(y60.get("by_year", {})) | set(ys.get("by_year", {}))):
        a = y60.get("by_year", {}).get(y)
        b = ys.get("by_year", {}).get(y)
        print(f"{y}: hs300 n={a['n']} {a['mean_pp']}/t{a['t']} | sw n={b['n'] if b else '-'} {b['mean_pp'] if b else '-'}/t{b['t'] if b else '-'}")
    print("\n[done] json -> multi_benchmark_summary.json")


if __name__ == "__main__":
    main()
