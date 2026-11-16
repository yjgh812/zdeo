r"""winners_contrast_v1.py — 盈利票vs亏损票 买点特征对照 V1（探索性）。

只读: trade_attribution_base.db(306行) × westock portfolio_paper_profit(现价盈亏真值)。
输出: 分组特征均值 + 5日涨幅分桶收益 + RankIC(特征vs未来收益)。
零交易权; 单状态(震荡轮动市)样本, 结论仅供假设生成。
"""
import json
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.append(r"D:\workbuddyspace")

BASE_DB = r"D:\zcodesj\attribution\trade_attribution_base.db"
OUT_JSON = r"D:\zcodesj\reports\winners_contrast_20261110.json"
FEATS = ["ret_5d_before", "dist_20d_high", "vol_20d", "amt_20d"]


def rank_ic(xs, ys):
    import math
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 10:
        return None, n
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    if vx == 0 or vy == 0:
        return None, n
    return cov / (vx * vy), n


def mean(v):
    v = [x for x in v if x is not None]
    return round(sum(v) / len(v), 4) if v else None


def main():
    from westock_client import WeStockClient
    resp = WeStockClient().call("portfolio_paper_profit", {})
    prof = {}
    for p in (resp.get("data") or {}).get("stockProfits") or []:
        c = str(p["code"]).strip().lower()
        for pre in ("sh", "sz", "bj"):
            if c.startswith(pre):
                c = c[len(pre):]
        prof[c.zfill(6)] = float(p.get("profitRate") or 0)

    db = sqlite3.connect("file:%s?mode=ro" % BASE_DB, uri=True)
    rows = db.execute(
        "SELECT code,name,row_type,buy_price,pnl_pct,ret_5d_before,dist_20d_high,"
        "vol_20d,amt_20d,fwd_ret_5d,fwd_ret_10d FROM trade_base").fetchall()
    db.close()
    cols = ["code", "name", "row_type", "buy_price", "pnl_pct", "ret_5d_before",
            "dist_20d_high", "vol_20d", "amt_20d", "fwd_ret_5d", "fwd_ret_10d"]
    recs = [dict(zip(cols, r)) for r in rows]
    for r in recs:
        if r["row_type"] == "open":
            r["pnl_now"] = prof.get(r["code"])  # 现价浮盈% 真值
        else:
            r["pnl_now"] = r["pnl_pct"]

    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "n_rows": len(recs)}

    # 1. 三组对照(开+闭仓合并, 以现价/已实现盈亏为准)
    win = [r for r in recs if (r["pnl_now"] or 0) >= 5]
    lose = [r for r in recs if (r["pnl_now"] or 0) <= -4]
    mid = [r for r in recs if r not in win and r not in lose]
    out["groups"] = {}
    for tag, g in (("win>=5%% n=%d" % len(win), win),
                   ("mid n=%d" % len(mid), mid),
                   ("lose<=-4%% n=%d" % len(lose), lose)):
        out["groups"][tag] = {f: mean([r[f] for r in g]) for f in FEATS}
        out["groups"][tag]["mean_pnl"] = mean([r["pnl_now"] for r in g])

    # 2. 买点5日涨幅分桶 → 平均盈亏(特征为小数, 桶界同单位)
    buckets = [(-9e9, -0.10), (-0.10, -0.05), (-0.05, 0.0), (0.0, 0.05),
               (0.05, 0.10), (0.10, 0.15), (0.15, 9e9)]
    out["ret5_buckets"] = []
    for lo, hi in buckets:
        g = [r for r in recs if r["ret_5d_before"] is not None
             and lo <= r["ret_5d_before"] < hi]
        if g:
            out["ret5_buckets"].append({
                "bucket": "[%s,%s)" % ("<=-10%" if lo < -1e8 else "%.0f%%" % (lo * 100),
                                       ">=+15%" if hi > 1e8 else "%.0f%%" % (hi * 100)),
                "n": len(g), "mean_pnl": mean([r["pnl_now"] for r in g]),
                "win_ratio": round(sum(1 for r in g if (r["pnl_now"] or 0) > 0) / len(g), 3)})

    # 2b. 联合形态: "高位+急涨"(华新式追高区) vs 其余
    chase = [r for r in recs if r["ret_5d_before"] is not None and r["dist_20d_high"] is not None
             and r["ret_5d_before"] >= 0.10 and r["dist_20d_high"] > -0.03]
    rest = [r for r in recs if r not in chase]
    out["joint_zones"] = {
        "chase_zone(5d涨>=10%%且距20日高点<3%%) n=%d" % len(chase): {
            "mean_pnl": mean([r["pnl_now"] for r in chase]),
            "win_ratio": round(sum(1 for r in chase if (r["pnl_now"] or 0) > 0) / len(chase), 3)
            if chase else None},
        "rest n=%d" % len(rest): {
            "mean_pnl": mean([r["pnl_now"] for r in rest]),
            "win_ratio": round(sum(1 for r in rest if (r["pnl_now"] or 0) > 0) / len(rest), 3)}}

    # 3. RankIC: 特征 vs 未来5日收益(239有值) / vs 现价盈亏
    for target, key in (("fwd_ret_5d", "fwd_ret_5d"), ("pnl_now", "pnl_now")):
        out["rankic_vs_" + target] = {}
        for f in FEATS:
            ic, n = rank_ic([r[f] for r in recs], [r[key] for r in recs])
            out["rankic_vs_" + target][f] = {"ic": None if ic is None else round(ic, 4), "n": n}

    # 4. 闭合组: 追高买入 vs 低位买入 的已实现R
    cl = [r for r in recs if r["row_type"] == "closed" and r["ret_5d_before"] is not None]
    hi = [r for r in cl if r["ret_5d_before"] >= 0.10]
    lo = [r for r in cl if r["ret_5d_before"] < 0.10]
    out["closed_split"] = {
        "buy_after_run10pct(高追) n=%d" % len(hi): {"mean_R": mean([r["pnl_pct"] / 8.0 for r in hi])},
        "buy_lt10pct(低位) n=%d" % len(lo): {"mean_R": mean([r["pnl_pct"] / 8.0 for r in lo])}}

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
