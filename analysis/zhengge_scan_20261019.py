# -*- coding: utf-8 -*-
"""
正格选股扫描（呈现层零交易权，20261016收盘口径）
口径=forward_up_prob_gauge 原生分组（A低位横盘/B启动确认/D高位滞涨/C追高禁区/R其余）
目标=声明条件交集：正期望画像组 ∩ 主线(传媒,方向层20261015) ∩ ml_prob排序 ∩ 宇宙/价格铁律
输出：data/zhengge_scan_20261019.json + 控制台
"""
import json, sqlite3
from collections import defaultdict
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"
ML_DATE = 20261016

conn_q = sqlite3.connect(QFQ, uri=True)
conn_d = sqlite3.connect(DB, uri=True)

ml = pd.read_sql(f"SELECT code, ml_prob FROM stock_ml_scores_v22 WHERE date={ML_DATE}", conn_d)
ml_map = dict(zip(ml["code"], ml["ml_prob"]))

si = pd.read_sql("SELECT code, name, industry, list_date, is_st FROM stock_info", conn_d)
si = si.drop_duplicates("code", keep="first").set_index("code")

# 全市场日线（一年窗口）
df = pd.read_sql("SELECT code, date, close FROM stock_daily_meta_qfq WHERE date>=20250901 ORDER BY code, date", conn_q)
conn_q.close()
print("日线行数:", len(df))

grp = df.groupby("code")
rows = []
for code, g in grp:
    closes = g["close"].tolist()
    if len(closes) < 200:
        continue
    if code not in ml_map:
        continue
    # 宇宙过滤
    if code.startswith(("688", "689", "8", "4", "92", "43")):
        continue
    if code not in si.index:
        continue
    try:
        if int(si.loc[code, "is_st"] or 0) == 1:
            continue
        if str(si.loc[code, "list_date"]).replace("-", "") >= "20260701":
            continue
    except Exception:
        pass
    w = closes[-250:]
    c = w[-1]
    pos250 = sum(1 for x in w if x < c) / len(w) * 100
    c5 = w[-6] if len(w) >= 6 else w[0]
    chg5 = (c / c5 - 1) * 100
    if c > 30:
        grp_name = "X超30元"
    elif pos250 < 30 and abs(chg5) < 3:
        grp_name = "A低位横盘"
    elif 60 <= pos250 < 95 and 5 <= chg5 < 10:
        grp_name = "B启动确认"
    elif chg5 >= 15:
        grp_name = "C追高禁区"
    elif pos250 >= 95 and chg5 < 5:
        grp_name = "D高位滞涨"
    else:
        grp_name = "R其余"
    rows.append({"code": code, "name": si.loc[code, "name"], "industry": si.loc[code, "industry"],
                 "grp": grp_name, "ml_prob": round(ml_map[code] * 100, 1), "close": round(c, 2)})

res = pd.DataFrame(rows)
print("分类完成:", len(res), "只")
print(res["grp"].value_counts().to_string())

# 主线行业(申万L1=传媒) 细分行业映射: 出版/影视/广告营销/游戏/数字媒体/电视广播/文化传媒
MEDIA_KEYS = ["出版", "影视", "广告", "游戏", "数字媒体", "电视", "传媒", "文化", "动漫", "视频"]
res["is_media"] = res["industry"].map(lambda x: any(k in str(x) for k in MEDIA_KEYS))

# 各组×主线 交叉统计 + 组内ml_prob Top
out = {}
for grp_name in ["A低位横盘", "B启动确认", "D高位滞涨"]:
    sub = res[res["grp"] == grp_name].sort_values("ml_prob", ascending=False)
    out[grp_name] = {
        "total": len(sub),
        "media_n": int(sub["is_media"].sum()),
        "top10": sub.head(10).to_dict("records"),
        "media_top": sub[sub["is_media"]].head(10).to_dict("records"),
    }
json.dump(out, open("data/zhengge_scan_20261019.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

for grp_name in ["A低位横盘", "B启动确认", "D高位滞涨"]:
    o = out[grp_name]
    print(f"\n=== {grp_name} 共{o['total']}只, 主线传媒{o['media_n']}只 ===")
    print("组内ml_prob Top10:")
    for r in o["top10"]:
        print(f"  {r['code']} {r['name']} [{r['industry']}] ml={r['ml_prob']}% px={r['close']}")
    if o["media_top"]:
        print("主线传媒∩组内:")
        for r in o["media_top"]:
            print(f"  {r['code']} {r['name']} [{r['industry']}] ml={r['ml_prob']}% px={r['close']}")
