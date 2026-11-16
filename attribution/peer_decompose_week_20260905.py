#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本周买入同伴对照分解（只读）：个股买入后表现 vs 同THS二级行业+同市值带同伴等权表现
Δ=个股-同伴：Δ显著负=板块内选了弱票(个股选择层问题)；Δ≈0=损失全来自风格层(已修)
同伴带:流通市值[0.6x,1.67x]+价格≤30+非ST+同行业，剔除自身；表现窗口=买入日收盘→20260904收盘
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"

# 本周新建仓（买入日, code, 名称, 成本≈加权价, 现价20260904收盘）——现价取westock真值positions
BUYS = [
    ("20260902", "002688", "金河生物", 5.311, 5.28), ("20260902", "301256", "华融化学", 10.792, 10.33),
    ("20260902", "603230", "内蒙新华", 9.442, 9.80), ("20260902", "603585", "苏利股份", 10.952, 10.66),
    ("20260902", "605166", "聚合顺", 8.692, 8.77),
    ("20260903", "300670", "大烨智能", 7.461, 7.22), ("20260903", "603819", "神力股份", 16.934, 17.02),
    ("20260903", "300025", "华星创业", 6.051, 5.87), ("20260903", "300252", "金信诺", 12.693, 11.97),
    ("20260903", "002811", "郑中设计", 15.543, 14.92),
    ("20260904", "600694", "大商股份", 14.666, 14.90), ("20260904", "600717", "天津港", 4.364, 4.36),
    ("20260904", "600035", "楚天高速", 3.743, 3.71), ("20260904", "600018", "上港集团", 5.502, 5.43),
    ("20260904", "600020", "中原高速", 3.913, 3.90), ("20260904", "600650", "锦江在线", 11.346, 11.15),
    ("20260904", "002174", "游族网络", 12.320, 12.18), ("20260904", "601999", "出版传媒", 7.080, 7.15),
    ("20260904", "000567", "海德股份", 6.783, 6.72), ("20260904", "300588", "熙菱信息", 25.316, 25.34),
    ("20260904", "601186", "中国铁建", 6.113, 6.11), ("20260904", "600704", "物产中大", 4.965, 4.95),
    ("20260904", "600827", "百联股份", 7.875, 7.84), ("20260904", "601083", "锦江航运", 13.245, 13.23),
]


def main():
    con = sqlite3.connect(FUND)
    ind_map, names = {}, {}
    for code, name, ind in con.execute("select code, name, industry from industry_map_ths"):
        ind_map[code] = ind.split("-")[1] if "-" in ind else ind
        names[code] = name or ""
    db = pd.read_sql("select ts_code, trade_date, close, circ_mv, turnover_rate from daily_basic "
                     "where trade_date in ('20260902','20260903','20260904')", con)
    con.close()
    db["code"] = db["ts_code"].str[:6]
    for c in ("close", "circ_mv", "turnover_rate"):
        db[c] = pd.to_numeric(db[c], errors="coerce")
    db["mv_yi"] = db["circ_mv"] / 10000.0
    by_date = {d: g.set_index("code") for d, g in db.groupby("trade_date")}

    con = sqlite3.connect(QFQ)
    px = pd.read_sql("select code, date, close from stock_daily_meta_qfq where date >= '20260901'", con)
    con.close()
    piv = px.pivot_table(index="date", columns="code", values="close")
    qd = list(piv.index)
    END = "20260904" if "20260904" in qd else qd[-1]

    rows = []
    for T, code, nm, cost, cur in BUYS:
        sub = by_date.get(T)
        if sub is None or code not in sub.index:
            continue
        ind = ind_map.get(code)
        mv = sub.loc[code, "mv_yi"]
        if not ind or pd.isna(mv):
            continue
        peers = [c for c in sub.index
                 if c != code and ind_map.get(c) == ind
                 and "ST" not in names.get(c, "")
                 and sub.loc[c, "close"] <= 30.0
                 and pd.notna(sub.loc[c, "mv_yi"])
                 and 0.6 * mv <= sub.loc[c, "mv_yi"] <= 1.67 * mv]
        peers = [c for c in peers if c in piv.columns and T in piv.index
                 and pd.notna(piv.loc[T, c]) and pd.notna(piv.loc[END, c])]
        stock_ret = (cur / cost - 1) * 100  # 成本→9/4收盘(现价即9/4收盘价)
        if len(peers) < 5:
            rows.append({"T": T, "code": code, "name": nm, "ind": ind, "n_peers": len(peers),
                         "stock_ret": round(stock_ret, 2), "peer_ret": None, "delta": None})
            continue
        peer_ret = float(np.mean([(piv.loc[END, c] / piv.loc[T, c] - 1) * 100 for c in peers]))
        rows.append({"T": T, "code": code, "name": nm, "ind": ind, "n_peers": len(peers),
                     "stock_ret": round(stock_ret, 2), "peer_ret": round(peer_ret, 2),
                     "delta": round(stock_ret - peer_ret, 2)})

    df = pd.DataFrame(rows)
    valid = df.dropna(subset=["delta"])
    out = {
        "n_buys": len(df), "n_with_peers": len(valid),
        "stock_median": float(valid["stock_ret"].median()),
        "peer_median": float(valid["peer_ret"].median()),
        "delta_median": float(valid["delta"].median()),
        "delta_mean": float(valid["delta"].mean()),
        "delta_negative_pct": float((valid["delta"] < 0).mean() * 100),
        "detail": rows,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    json.dump(out, open(r"D:\zcodesj\results\peer_decompose_week_20260905.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
