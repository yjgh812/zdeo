# -*- coding: utf-8 -*-
"""右侧启动呈请3解禁切片v2: 严定义事件 × 限售解禁(东财全史) 分层 (2026-10-11)

呈请来源: rightside_spec_coverage §三呈请3(情绪事件维: 解禁)。回购子维已探针关闭(tushare repurchase 无权限)。
数据源(v2定案): akshare stock_restricted_release_detail_em 按年拉全市场解禁明细(2012-2026, 15次调用);
  v1 tushare share_float 逐股方案废弃(本账户限频小时级, 二次调用即撞限)。
  本地event_calendar.restricted_release仅2026-07起前瞻日历(已排除)。
口径(预声明): 事件D的解禁暴露 = 同票存在解禁日∈[D-10, D+20] (预声明单窗);
  幅度档 = 占解禁前流通市值比例 ≥5% 为大解禁(<5%小解禁)。
判据: 净h60(收官主口径)+ACTIVE漏斗子集。
自检: ①逐年拉取行数与年份覆盖 ②事件暴露率(无解禁应占大头) ③抽样对拍打印。
输出: stdout + tmp/unlock_pull_log.txt + reports/rightside_startup_20261011/unlock_band_summary.json
性质: 描述性研究件零交易权。
"""
import os, sys, json, time
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EV = r"D:\zcodesj\reports\rightside_startup_20261011\events_main.csv"
CACHE = r"D:\zcodesj\tmp\unlock_detail_em_20261011.parquet"
OUT = r"D:\zcodesj\reports\rightside_startup_20261011\unlock_band_summary.json"
W1, W2 = 10, 20  # [D-10, D+20] 预声明
BIG = 5.0  # 占解禁前流通市值%


def pull_history():
    if os.path.exists(CACHE):
        df = pd.read_parquet(CACHE)
        print(f"[cache] rows={len(df)} years={df.year.min()}-{df.year.max()}")
        return df
    import akshare as ak
    frames = []
    for y in range(2012, 2027):
        d = ak.stock_restricted_release_detail_em(start_date=f"{y}0101", end_date=f"{y}1231")
        d = d.rename(columns={"股票代码": "code", "解禁时间": "date", "解禁数量": "shares",
                              "实际解禁市值": "mv", "占解禁前流通市值比例": "pct"})
        d["code"] = d["code"].astype(str).str.zfill(6)
        d["date"] = d["date"].astype(str).str.replace("-", "")
        d["year"] = y
        frames.append(d[["code", "date", "shares", "mv", "pct", "year"]])
        print(f"[pull] {y}: {len(d)}")
        time.sleep(1.0)
    df = pd.concat(frames, ignore_index=True)
    df.to_parquet(CACHE)
    print(f"[cache] saved rows={len(df)}")
    return df


def stat(v):
    v = [x for x in v if x == x]
    if len(v) < 5:
        return {"n": int(len(v))}
    a = pd.Series(v)
    t = float(a.mean() / (a.std(ddof=1) / len(a) ** 0.5))
    return {"n": int(len(a)), "mean": round(float(a.mean()), 3),
            "win": round(float((a > 0).mean() * 100), 1), "t": round(t, 2)}


def main():
    em = pd.read_csv(EV, dtype={"code": str})
    em["D"] = em.d_date.astype(int)
    funnel = (em.pos60 <= 40) & (em.pre_shrink <= 0.75)
    ul = pull_history()
    ul["d8"] = ul["date"].astype(int)
    ul["pct"] = pd.to_numeric(ul["pct"], errors="coerce") * 100  # 0.113924→11.39%
    # 联接: 同票解禁日∈[D-W1, D+W2]
    hits, bigs = [], []
    ul_by = {c: g for c, g in ul.groupby("code")}
    for code, D in zip(em.code, em.D):
        g = ul_by.get(code)
        hit, big = False, False
        if g is not None:
            m = (g.d8 >= D - W1) & (g.d8 <= D + W2)
            if m.any():
                hit = True
                big = bool((g.loc[m, "pct"] >= BIG).any())
        hits.append(hit)
        bigs.append(big)
    em["unlock"] = hits
    em["unlock_big"] = bigs
    out = {"meta": {"date": "2026-10-11", "spec": "呈请3解禁切片v2(东财全史按年)",
                    "window": f"[D-{W1}, D+{W2}] 预声明单窗; 大解禁=占解禁前流通市值≥{BIG}%",
                    "unlock_rows": int(len(ul)), "years": f"{ul.year.min()}-{ul.year.max()}",
                    "repurchase": "tushare无权限, 子维关闭", "news_sentiment_report": "外部源重, 留档待勾选"},
           "layers": {}, "funnel_layers": {}}
    y = em.f60_net
    m_u, m_b = em.unlock, em.unlock & em.unlock_big
    m_s = em.unlock & ~em.unlock_big
    out["layers"] = {"无解禁": stat(y[~em.unlock]), "小解禁(<5%)": stat(y[m_s]),
                     "大解禁(>=5%)": stat(y[m_b]), "有解禁合计": stat(y[m_u])}
    out["funnel_layers"] = {"无解禁": stat(y[~em.unlock & funnel]),
                            "有解禁合计": stat(y[m_u & funnel])}
    cov = float(em.unlock.mean()) * 100
    print(f"[覆盖] 窗内有解禁事件 {int(em.unlock.sum())}/{len(em)} ({cov:.1f}%), 其中大解禁 {int(m_b.sum())}")
    for k, v in out["layers"].items():
        print(f"  {k}: {v}")
    print("[漏斗]", json.dumps(out["funnel_layers"], ensure_ascii=False))
    # 分年粗检(防单年幻象)
    em["yr"] = em.D // 10000
    yr_tab = {}
    for yr, g in em.groupby("yr"):
        yr_tab[int(yr)] = {"unlock_n": int(g.unlock.sum()), "unlock_mean": (round(g.loc[g.unlock, "f60_net"].mean(), 2) if g.unlock.any() else None),
                           "nounlock_mean": (round(g.loc[~g.unlock, "f60_net"].mean(), 2) if (~g.unlock).any() else None)}
    out["by_year"] = yr_tab
    print("[分年]", json.dumps({k: v for k, v in list(yr_tab.items()) if v["unlock_n"] >= 10}, ensure_ascii=False))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT}")


if __name__ == "__main__":
    main()
