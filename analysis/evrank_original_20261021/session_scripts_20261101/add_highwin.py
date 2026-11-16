# -*- coding: utf-8 -*-
"""add_highwin.py — HIGHWIN法复现(低位横盘×个股高胜率, 老7+新7=10); 幂等守卫在册。"""
import os, sys, json, sqlite3, io
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))

from stock_name_lookup import get_name

con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
T = int(con.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0])
rows = con.execute(
    "select code,date,open,high,low,close,volume,amount from stock_daily_meta_qfq "
    "where date>=? order by code,date", (str(int(str(T)[:4]) - 2) + "0000",)).fetchall()
con.close()
series = {}
for code, d, o, h, l, c, v, a in rows:
    s = series.setdefault(code, {"d": [], "c": [], "v": []})
    s["d"].append(int(d)); s["c"].append(float(c)); s["v"].append(float(v or 0))
for code, s in series.items():
    for k in s:
        s[k] = np.array(s[k], dtype=float)

plan_path = os.path.join(ROOT, "data", "winner_methods_topup_plan_20261101.json")
plan = json.load(open(plan_path, encoding="utf-8"))
if any(p["source"] == "highwin_rescan" for p in plan):
    print("已有highwin_rescan臂, 拒绝重复")
    sys.exit(0)
taken = {p["code"] for p in plan}
W23_SKIP = {"001318", "600928", "000001", "601528", "002839", "600908", "000415"}
scon = sqlite3.connect("file:data/st_history.db?mode=ro", uri=True)
st_latest = scon.execute(
    "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)").fetchall()
scon.close()
st_set = {str(c)[-6:] for c, s_ in st_latest if s_ == 1}
rep = json.load(open("data/group_pnl_report_20261031.json", encoding="utf-8"))
held = {str(s["code"])[-6:] for gv in rep["groups_source"].values() for s in gv.get("stocks", [])}

cand = []
for code, s in series.items():
    if code in taken or code in W23_SKIP or code in st_set or code in held:
        continue
    if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        continue
    if len(s["c"]) < 250 or s["d"][-1] != T or not (0.5 < s["c"][-1] <= 30):
        continue
    cl = s["c"]
    w250 = cl[-250:]
    mn, mx = float(w250.min()), float(w250.max())
    if mx <= mn:
        continue
    pos250 = (cl[-1] - mn) / (mx - mn) * 100
    if not (30 <= pos250 <= 70):
        continue
    hi10, lo10 = float(cl[-10:].max()), float(cl[-10:].min())
    if lo10 <= 0 or (hi10 - lo10) / lo10 > 0.12:
        continue
    v5 = float(np.mean(s["v"][-5:])); v60 = float(np.mean(s["v"][-60:]))
    if v5 >= v60:
        continue
    rets = cl[-250:] / cl[-251:-1] - 1
    wr = float(np.mean(rets > 0))
    if wr < 0.50:
        continue
    cand.append((wr, code, f"横盘带pos250={pos250:.0f}% 胜率{wr*100:.0f}%"))

cand.sort(key=lambda x: -x[0])
out = []
for wr, code, reason in cand[:7]:
    px = float(series[code]["c"][-1])
    out.append({
        "code": code, "name": get_name(code) or "?", "source": "highwin_rescan",
        "volume": 100, "limit_price": round(px * 1.005, 2),
        "note": "主人令20261101盈利模式复现[HIGHWIN高位横盘×高胜率]; 高挂0.5%%; 信号日%d; %s" % (T, reason),
    })
    print("  +", code, get_name(code), "@%.2f" % (px * 1.005), reason)
plan.extend(out)
outp = os.path.realpath(plan_path)
assert outp == os.path.realpath(r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json"), "路径越界"
json.dump(plan, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("计划:", len(plan), "笔 | 预算≈%.0f元" % sum(p["limit_price"] * p["volume"] for p in plan))
