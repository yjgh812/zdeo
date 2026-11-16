# -*- coding: utf-8 -*-
"""类型样本选择器(主人令: 每种上涨类型选几只测试样本) 20261026 — 只读零交易权。
五臂设计(同窗20日竞赛, 各臂预测来自已验证类型学):
  A臂 HIGHWIN组合   = 已部署周一5只(相位×燃料×阶段, 预测63.7→71.5%) — 在役不重选
  B臂 超跌探底阶段   = pos<25 & mom20<-15% (预测+6.94%)
  C臂 低位启动日     = A格 & 当日涨2-8% (预测+4.14%)
  D臂 中枢对照       = pos 40-60 & |mom20|<4% (裸基线对照, 预测~+0.5%)
  E臂 加速赶顶观察   = pos>75 & mom20>20% (预测-2.15%, 只观察不买=避雷对照)
选样规则(确定性): 非ST/<=30元/6/0/3/上市>60日, 按10/23收盘额降序取前3(E臂按观察)。
输出: analysis/type_sample_arms_20261026.json + stdout
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/type_sample_arms_20261026.json"
ASOF = 20261023


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    f"SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq "
    f"WHERE date>=20251001 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]") & ~df["code6"].str.startswith("688")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["volume"] > 0)]
scon = sqlite3.connect(STDB, uri=True)
# 修复20261026: 最新记录法(联创光电教训)
st_latest = scon.execute("""
    SELECT code, is_st FROM st_daily s1
    WHERE date = (SELECT MAX(date) FROM st_daily s2 WHERE s2.code = s1.code)
""").fetchall()
scon.close()
st_codes = set()
for raw_code, is_st in st_latest:
    if is_st == 1:
        dg = "".join(c for c in str(raw_code) if c.isdigit())
        st_codes.add(dg[-6:] if len(dg) >= 6 else dg)
# 排除当前westock持仓(样本独立性; 复用stop_loss_executor的MCP直连只读)
held = set()
try:
    sys.path.insert(0, r"D:\zcodesj")
    from stop_loss_executor import _fetch_positions_via_mcp
    for p in _fetch_positions_via_mcp() or []:
        held.add(p["code"][-6:])
except Exception:
    pass

last = df[df["d"] == ASOF].copy()
rows = []
for code, gg in df.groupby("code6", sort=False):
    gg = gg.sort_values("d")
    c = gg["close"].to_numpy(float)
    d = gg["d"].to_numpy("int64")
    n = len(c)
    if n < 120 or ASOF not in d:
        continue
    if code in st_codes or c[-1] > 30 or c[-1] < 2:
        continue
    if code in held:  # 样本独立性: 排除当前持仓
        continue
    s = pd.Series(c)
    mn = s.rolling(250, min_periods=120).min().iloc[-1]
    mx = s.rolling(250, min_periods=120).max().iloc[-1]
    pos = (c[-1] - mn) / (mx - mn) * 100 if mx > mn else np.nan
    mom20 = c[-1] / c[-21] - 1 if n >= 21 else np.nan
    chg1 = c[-1] / c[-2] - 1
    chg5 = (c[-1] / c[-6] - 1) * 100
    amt = float(gg["amount"].iloc[-1])
    if not (np.isfinite(pos) and np.isfinite(mom20)):
        continue
    arm = None
    if pos < 25 and mom20 < -0.15:
        arm = "B_超跌探底"
    elif pos < 30 and abs(chg5) < 3 and 0.02 <= chg1 < 0.08:
        arm = "C_低位启动日"
    elif 40 <= pos <= 60 and abs(mom20) < 0.04:
        arm = "D_中枢对照"
    elif pos > 75 and mom20 > 0.20:
        arm = "E_加速赶顶观察"
    if arm:
        rows.append({"code": code, "name": "", "arm": arm, "close": round(float(c[-1]), 2),
                     "pos250": round(float(pos), 1), "mom20": round(float(mom20), 3),
                     "chg1": round(float(chg1), 3), "amt_wan": round(amt / 1e4, 0)})

ev = pd.DataFrame(rows)
# 名称补全
import subprocess
codes = ev["code"].tolist()[:60]
names = {}
try:
    r = subprocess.run([sys.executable, "stock_name_lookup.py"] + codes, capture_output=True,
                       text=True, encoding="utf-8", timeout=60, cwd=r"D:\zcodesj")
    for ln in r.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            names[k.strip()] = v.strip()
except Exception:
    pass
ev["name"] = ev["code"].map(names).fillna("?")

arms = {}
for arm, g in ev.groupby("arm"):
    top = g.sort_values("amt_wan", ascending=False).head(3)
    arms[arm] = top[["code", "name", "close", "pos250", "mom20", "chg1", "amt_wan"]].to_dict("records")

pred = {"B_超跌探底": "+6.94%(阶段学)", "C_低位启动日": "+4.14%(启动日×深跌)",
        "D_中枢对照": "~+0.5%(裸基线)", "E_加速赶顶观察": "-2.15%(避雷,只观察不买)"}
out = {"asof": ASOF, "design": "五臂同窗20日竞赛; A臂=周一已部署highwin5只在役",
       "predictions": pred, "arms": arms,
       "note": "B/C/D臂拟买入各3只x2000元=18,000元(须主人令); E臂纯观察零买入"}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"样本选择完成(基于{ASOF}收盘) → {OUT}\n")
for arm in ("B_超跌探底", "C_低位启动日", "D_中枢对照", "E_加速赶顶观察"):
    print(f"== {arm} | 预测: {pred[arm]} | 候选池{len(ev[ev['arm']==arm])}只, 取成交额前3 ==")
    for x in arms.get(arm, []):
        print(f"  {x['code']} {x['name']:<6} 收盘{x['close']:>7} pos{x['pos250']:>6} "
              f"mom20{x['mom20']:>+7.1%} 今涨{x['chg1']:>+6.1%} 额{x['amt_wan']/1e4:.1f}亿")
