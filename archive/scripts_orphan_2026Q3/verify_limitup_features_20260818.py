# -*- coding: utf-8 -*-
"""
验证网上「涨停特征」说法（2026-08-18 调研后验证）
① 封单成交比 3~10 倍 → 次日高开 >=6%   (东财财富号说法)
② 14:00 后首板 次日晋级率 22.7%        (头条技术分析说法)
③ 连板晋级率随连板数上升, 五板起 50%+  (雪球全史统计/赵老哥体系)
数据: akshare stock_zt_pool_em(历史涨停池) + 本地 qfq 库(次日开盘/当日收盘)
样本: 2026-07-06 ~ 2026-08-14 涨停日, 次日表现跟踪到 2026-08-17
"""
import sqlite3, time, sys
import akshare as ak
import pandas as pd

QDATES = ["20260706","20260707","20260708","20260709","20260710",
          "20260713","20260714","20260715","20260716","20260717",
          "20260720","20260721","20260722","20260723","20260724",
          "20260727","20260728","20260729","20260730","20260731",
          "20260803","20260804","20260805","20260806","20260807",
          "20260810","20260811","20260812","20260813","20260814"]

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"

def load_pool():
    """拉 30 天涨停池, 返回 DataFrame"""
    rows = []
    for d in QDATES:
        for attempt in range(3):
            try:
                df = ak.stock_zt_pool_em(date=d)
                nn = len(df)
                if nn == 0:
                    print(f"[warn] {d} 空池", flush=True)
                df["date"] = d
                rows.append(df)
                print(f"[ok] {d} {nn}只", flush=True)
                break
            except Exception as e:
                print(f"[retry] {d} attempt{attempt}: {type(e).__name__} {e}", flush=True)
                time.sleep(2)
        time.sleep(0.3)
    pool = pd.concat(rows, ignore_index=True)
    pool["code"] = pool["代码"].astype(str).str.zfill(6)
    return pool

def get_qfq_close_open(codes, dates):
    """批量查 qfq 库: date -> code -> (close, open, change_pct)"""
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    placeholders = ",".join(["?"] * len(codes))
    out = {}  # (date, code) -> (close, open, change_pct)
    for d in dates:
        try:
            cur = con.execute(
                f"SELECT code, close, open, change_pct FROM stock_daily_meta_qfq WHERE date=? AND code IN ({placeholders})",
                [d] + codes)
            for c, cl, op, chg in cur.fetchall():
                out[(d, str(c).zfill(6))] = (cl, op, chg)
        except Exception as e:
            print(f"[qfq-err] {d}: {e}", flush=True)
    con.close()
    return out


def is_limit_up(code, change_pct):
    """按板块涨跌幅阈值判定是否涨停(qfq change_pct 单位: %)"""
    if change_pct is None:
        return False
    if code.startswith(("300", "301", "688")):
        return change_pct >= 19.5
    if code.startswith(("8", "4", "920")):
        return change_pct >= 29.5
    return change_pct >= 9.5

def main():
    print("== 1/3 拉涨停池 ==", flush=True)
    pool = load_pool()
    out_csv = r"D:/zcodesj/verify_limitup_pool_20260706_20260814.csv"
    # 增量合并: 保留旧 CSV 中已拉到的日期(接口窗口约3周会滚动丢失,如7/28),
    # 本次拉到的日期覆盖同日期旧数据; 空池日期不动旧数据
    import os
    if os.path.exists(out_csv):
        try:
            old = pd.read_csv(out_csv, dtype={"代码": str})
            old["date"] = old["date"].astype(int)
            new_dates = set(pool["date"].astype(int))
            keep = old[~old["date"].isin(new_dates) & old["date"].isin(QDATES)]
            pool = pd.concat([keep, pool], ignore_index=True)
            print(f"[merge] 保留旧日期数据 {len(keep)} 行(接口窗口外)", flush=True)
        except Exception as e:
            print(f"[merge-warn] 旧CSV合并失败,直接覆盖: {e}", flush=True)
    pool.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"pool saved: {out_csv} rows={len(pool)}", flush=True)

    # 次日交易日 = 下一交易日
    dates_sorted = sorted(QDATES)
    next_map = {d: dates_sorted[i+1] for i, d in enumerate(dates_sorted[:-1])}
    # 8/14 的次日是 8/17 (交易日历)
    next_map["20260814"] = "20260817"

    # 每个涨停日, 涨停代码集合 (仅用于统计池规模, 晋级判定改用 qfq change_pct)
    pool_by_date = pool.groupby("date")["code"].apply(set).to_dict()

    # 需要 qfq 价格的日期: 每个涨停日的当日(close) + 次日(open, change_pct)
    need_dates = set()
    for d in pool_by_date:
        need_dates.add(d)
        nd = next_map.get(d)
        if nd:
            need_dates.add(nd)
    all_codes = sorted(pool["code"].unique().tolist())
    print(f"== 2/3 查 qfq 价格: codes={len(all_codes)} dates={len(need_dates)} ==", flush=True)
    qmap = get_qfq_close_open(all_codes, sorted(need_dates))

    print("== 3/3 计算验证 ==", flush=True)
    recs = []
    for _, r in pool.iterrows():
        d = r["date"]
        code = r["code"]
        nd = next_map.get(d)
        if not nd:
            continue
        close_d = qmap.get((d, code), (None, None, None))[0]
        open_nd = qmap.get((nd, code), (None, None, None))[1]
        chg_nd = qmap.get((nd, code), (None, None, None))[2]
        if close_d is None or open_nd is None or chg_nd is None:
            continue
        recs.append({
            "date": d, "code": code, "name": r["名称"], "industry": r["所属行业"],
            "n_board": int(r["连板数"]), "first_time": str(r["首次封板时间"]).zfill(6),
            "breaks": int(r["炸板次数"]),
            "seal_money": float(r["封板资金"]) if pd.notna(r["封板资金"]) else None,
            "amount": float(r["成交额"]),
            "close_d": float(close_d), "open_nd": float(open_nd),
            "next_date": nd,
            "gap_pct": float(open_nd) / float(close_d) - 1.0,
            "next_limit_up": is_limit_up(code, chg_nd),
        })
    df = pd.DataFrame(recs)
    print(f"有效记录(有次日价): {len(df)}", flush=True)

    # 通用晋级判断: 次日涨停(含炸板池? 用次日涨停池集合)
    # ── ① 封单成交比 vs 次日高开/晋级 ──
    d1 = df.dropna(subset=["seal_money"]).copy()
    d1["seal_amt_ratio"] = d1["seal_money"] / d1["amount"]
    print("\n===== ① 封单成交比(封板资金/当日成交额) 分桶 =====\n", flush=True)
    bins = [0, 1, 3, 10, 30, 1e18]
    labels = ["<1x", "1~3x", "3~10x", "10~30x", ">30x"]
    d1["bucket"] = pd.cut(d1["seal_amt_ratio"], bins=bins, labels=labels, right=False)
    g = d1.groupby("bucket", observed=True).agg(
        样本=("code", "count"),
        次日高开中位数=("gap_pct", lambda s: round(s.median()*100, 2)),
        次日高开均值=("gap_pct", lambda s: round(s.mean()*100, 2)),
        高开6pct以上占比=("gap_pct", lambda s: round((s >= 0.06).mean()*100, 1)),
        次日晋级率=("next_limit_up", lambda s: round(s.mean()*100, 1)),
    )
    print(g.to_string(), flush=True)
    g.to_csv(r"D:/zcodesj/verify_result_bucket_sealratio.csv", encoding="utf-8-sig")

    # ── ② 首板封板时间分桶 ──
    first = df[df["n_board"] == 1].copy()
    def t2h(t):
        try:
            return int(t[:2]) * 60 + int(t[2:4])
        except Exception:
            return None
    first["time_min"] = first["first_time"].apply(t2h)
    tbins = [0, 570, 600, 690, 840, 1e9]  # 09:30前/09:30-10:00/10:00-11:30/13:00-14:00/14:00后
    tlabels = ["09:30前", "09:30~10:00", "10:00~11:30", "13:00~14:00", "14:00后"]
    first["tbucket"] = pd.cut(first["time_min"], bins=tbins, labels=tlabels, right=False)
    print("\n===== ② 首板首次封板时间 vs 次日表现 =====\n", flush=True)
    g2 = first.dropna(subset=["tbucket"]).groupby("tbucket", observed=True).agg(
        样本=("code", "count"),
        次日高开中位数=("gap_pct", lambda s: round(s.median()*100, 2)),
        次日晋级率=("next_limit_up", lambda s: round(s.mean()*100, 1)),
    )
    print(g2.to_string(), flush=True)
    g2.to_csv(r"D:/zcodesj/verify_result_bucket_firsttime.csv", encoding="utf-8-sig")

    # ── ③ 晋级率矩阵 ──
    print("\n===== ③ 连板晋级率矩阵(N板 -> N+1板) =====\n", flush=True)
    df["n_next"] = df["n_board"] + 1
    up_next = df[df["next_limit_up"]].copy()
    # 次日连板数对表: to_dict 向量化(避免 MultiIndex .get 的 Series 歧义与性能警告)
    pool_n = pool.drop_duplicates(["date", "code"]).set_index(["date", "code"])["连板数"].to_dict()
    up_next["next_n"] = [
        int(pool_n.get((r_next, r_code), r_nn))
        for r_next, r_code, r_nn in zip(
            up_next["next_date"], up_next["code"], up_next["n_next"])]
    # 晋级成功 = 次日连板数 == n+1 (严格口径)
    up_next["promoted"] = up_next["next_n"] == up_next["n_next"]
    g3 = []
    for n in sorted(df["n_board"].unique()):
        sub = df[df["n_board"] == n]
        if len(sub) == 0:
            continue
        prom = up_next[up_next["n_board"] == n]["promoted"].sum()
        g3.append({"当前连板": n, "样本": len(sub), "次日继续涨停": prom, "晋级率%": round(prom/len(sub)*100, 2)})
    g3df = pd.DataFrame(g3)
    print(g3df.to_string(index=False), flush=True)
    g3df.to_csv(r"D:/zcodesj/verify_result_promote_matrix.csv", index=False, encoding="utf-8-sig")

    # ── 附加: 连板高度分桶的次日高开 ──
    print("\n===== 附加: 连板数 vs 次日高开 =====\n", flush=True)
    g4 = df.groupby("n_board").agg(
        样本=("code", "count"),
        次日高开中位数=("gap_pct", lambda s: round(s.median()*100, 2)),
    )
    print(g4.to_string(), flush=True)

    print("\n===== DONE 报告/CSV 已输出到 D:/zcodesj/verify_result_*.csv =====", flush=True)

if __name__ == "__main__":
    main()