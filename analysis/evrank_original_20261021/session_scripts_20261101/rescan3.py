# -*- coding: utf-8 -*-
"""rescan3.py — 复现三个"被我说成不可复现"的方法(主人令20261101):
A 超跌探底B臂(type_sample_arms冻结核对: 20日跌>=20%+活跃)
B 盘前底部放量突破十条件(prescreen_20260902_am.py十条件, 窗口改最新)
C ML胜率排名复刻(个股自身历史上涨占比排名——系统侧重算, 不依赖外部App)
零交易权, 只出候选; 追加至挂单计划尾部(现金护栏自然优先级)。"""
import os, sys, json, sqlite3, io
import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))

from stock_name_lookup import get_name

QFQ = "D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
T = int(con.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0])
rows = con.execute(
    "select code,date,open,high,low,close,volume,amount from stock_daily_meta_qfq "
    "where date>=? order by code,date", (str(int(str(T)[:4]) - 2) + "0000",)).fetchall()
con.close()
series = {}
for code, d, o, h, l, c, v, a in rows:
    s = series.setdefault(code, {"d": [], "o": [], "c": [], "v": [], "a": []})
    s["d"].append(int(d)); s["o"].append(float(o)); s["c"].append(float(c))
    s["v"].append(float(v or 0)); s["a"].append(float(a or 0))
for code, s in series.items():
    for k in s:
        s[k] = np.array(s[k], dtype=float)
print("信号日T =", T, "| 载入", len(series))

plan_path = os.path.join(ROOT, "data", "winner_methods_topup_plan_20261101.json")
plan = json.load(open(plan_path, encoding="utf-8"))
taken = {p["code"] for p in plan}
W23_SKIP = {"001318", "600928", "000001", "601528", "002839", "600908", "000415"}

scon = sqlite3.connect("file:data/st_history.db?mode=ro", uri=True)
st_latest = scon.execute(
    "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)").fetchall()
scon.close()
st_set = {str(c)[-6:] for c, s_ in st_latest if s_ == 1}

def uni(code, s, min_bars):
    if code in taken or code in W23_SKIP or code in st_set:
        return False
    if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        return False
    if len(s["c"]) < min_bars or s["d"][-1] != T:
        return False
    return 0.5 < s["c"][-1] <= 30

def amt20(s):
    return float(np.mean(s["a"][-20:])) if len(s["a"]) >= 20 else 0.0

def emit(method, cn, quota, cand, key):
    cand.sort(key=key)
    out = []
    for item in cand:
        code = item[1]
        px = float(series[code]["c"][-1])
        out.append({
            "code": code, "name": get_name(code) or "?", "source": method,
            "volume": 100, "limit_price": round(px * 1.005, 2),
            "note": "主人令20261101盈利模式复现[%s]新#%d; 高挂0.5%%; 信号日%d; %s" % (
                cn, len(out) + 1, T, item[2][:60]),
        })
        taken.add(code)
        if len(out) >= quota:
            break
    plan.extend(out)
    print(f"[{method}|{cn}] 补{len(out)}笔: " + " ".join(f"{o['code']}{o['name']}@{o['limit_price']}" for o in out))

# ---- A 超跌探底B臂: 20日跌>=20% + 20日成交额>=0.5亿(活跃), 最深优先 ----
cand = []
for code, s in series.items():
    if not uni(code, s, 60):
        continue
    ret20 = s["c"][-1] / s["c"][-21] - 1
    if ret20 > -0.20 or amt20(s) < 0.5e8:
        continue
    cand.append((ret20 * 100, code, f"20日{ret20*100:.1f}% 活跃"))
emit("type_sample_B_oversold", "超跌探底B臂", 6, cand, key=lambda x: x[0])

# ---- B 盘前底部放量突破十条件(prescreen冻结条件, 窗口=近120日) ----
cand = []
for code, s in series.items():
    if not uni(code, s, 120):
        continue
    cl, vol, amt, op = s["c"], s["v"], s["a"], s["o"]
    c0 = cl[-1]
    pct0 = c0 / cl[-2] - 1
    if pct0 < 0.03 or pct0 >= 0.099:
        continue
    v20 = float(np.mean(vol[-21:-1]))
    if v20 <= 0 or vol[-1] < 1.5 * v20:
        continue
    pre_h20 = float(np.max(s["c"][-21:-1]))
    if c0 < pre_h20 * 0.98:
        continue
    lo20 = float(np.min(s["c"][-21:-1]))
    if lo20 <= 0 or (pre_h20 - lo20) / lo20 > 0.25:
        continue
    chg20 = c0 / cl[-21] - 1
    if chg20 > 0.20:
        continue
    ma20n = float(np.mean(cl[-20:])); ma20a = float(np.mean(cl[-25:-5]))
    if c0 <= ma20n or ma20n < ma20a * 0.995:
        continue
    a1 = amt[-1]
    if not (0.8e8 <= a1 <= 30e8):
        continue
    vr = vol[-1] / v20
    cand.append((vr, code, f"突破20高 量{vr:.1f}x 涨{pct0*100:.1f}%"))
emit("prescreen_breakout10", "盘前底部放量突破十条件", 10, cand, key=lambda x: -x[0])

# ---- C ML胜率排名复刻: 个股自身近250日上涨日占比(样本>=200), 高胜率优先 ----
cand = []
for code, s in series.items():
    if not uni(code, s, 250):
        continue
    rets = s["c"][-250:] / s["c"][-251:-1] - 1
    ups = int(np.sum(rets > 0))
    wr = ups / len(rets)
    if wr > 0.55:
        cand.append((wr, code, f"250日上涨占比{wr*100:.1f}%"))
emit("ml_winrate_replica", "ML胜率排名复刻", 7, cand, key=lambda x: -x[0])

out = os.path.realpath(plan_path)
assert out == os.path.realpath(r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json"), "路径越界"
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
tot = sum(p["limit_price"] * p["volume"] for p in plan)
print("计划总计:", len(plan), "笔 | 预算≈%.0f元" % tot)
