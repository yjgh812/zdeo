# -*- coding: utf-8 -*-
"""rescan_all.py — 主人令20261101「所有都现在干」: 六法复现出票。
A 量化综合排名第一(重建版: 250日滚动20日窗 胜率/期望/尾部)
B 主线方向法(复盘主线=20日最强3行业的资金龙头)
C 对垒法复刻(估值修复侧: 低PE+修复动量+放量)
D 跟踪池0826重扫(池成员过当前宇宙规则, 动量排序)
E 厄尔尼诺重开(同五链映射设计, 当前价第二轮)
F 组合C抄底复刻(深跌磨底+量能转暖反转)
全部零交易权, 候选追加至挂单计划尾部。"""
import os, sys, json, sqlite3, io
import numpy as np
import pandas as pd

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
    s = series.setdefault(code, {"d": [], "o": [], "c": [], "v": [], "a": []})
    s["d"].append(int(d)); s["o"].append(float(o)); s["c"].append(float(c))
    s["v"].append(float(v or 0)); s["a"].append(float(a or 0))
for code, s in series.items():
    for k in s:
        s[k] = np.array(s[k], dtype=float)
print("T =", T, "| 载入", len(series))

plan_path = os.path.join(ROOT, "data", "winner_methods_topup_plan_20261101.json")
plan = json.load(open(plan_path, encoding="utf-8"))
# 幂等: 先移除本脚本六个方法的旧行, 重扫后限额重加(防重复堆叠)
for m in ("quant_rank_rebuild", "mainline_direction", "f_arm_replica",
          "tracking_pool_0826", "elnino_rerun", "combo_c_rerun"):
    plan = [p for p in plan if p["source"] != m]
taken = {p["code"] for p in plan}
W23_SKIP = {"001318", "600928", "000001", "601528", "002839", "600908", "000415"}
scon = sqlite3.connect("file:data/st_history.db?mode=ro", uri=True)
st_latest = scon.execute(
    "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)").fetchall()
scon.close()
st_set = {str(c)[-6:] for c, s_ in st_latest if s_ == 1}
# 已持有票也剔除(防同一票重复买)
rep = json.load(open("data/group_pnl_report_20261031.json", encoding="utf-8"))
held = set()
for gv in rep["groups_source"].values():
    for s in gv.get("stocks", []):
        held.add(str(s["code"])[-6:])

bcon = sqlite3.connect("file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro", uri=True)
db_rows = bcon.execute(
    "select ts_code, pe_ttm, pb, trade_date from daily_basic "
    "where trade_date>=? and trade_date<=? order by ts_code, trade_date",
    (str(T - 40), str(T))).fetchall()
bcon.close()
dbp = {}
for ts, pe, pb, td in db_rows:
    dbp[ts[:6]] = (pe, pb)

def uni(code, s, min_bars):
    if code in taken or code in W23_SKIP or code in st_set or code in held:
        return False
    if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        return False
    if len(s["c"]) < min_bars or s["d"][-1] != T:
        return False
    return 0.5 < s["c"][-1] <= 30

def amt20(s):
    return float(np.mean(s["a"][-20:])) if len(s["a"]) >= 20 else 0.0

def emit(method, cn, cand, quota):
    out = []
    for item in cand[:quota]:
        code = item[1]
        px = float(series[code]["c"][-1])
        out.append({
            "code": code, "name": get_name(code) or "?", "source": method,
            "volume": 100, "limit_price": round(px * 1.005, 2),
            "note": "主人令20261101全法复现[%s]; 高挂0.5%%; 信号日%d; %s" % (cn, T, item[2][:55]),
        })
        taken.add(code)
    plan.extend(out)
    print(f"[{cn}] 补{len(out)}笔: " + " ".join(f"{o['code']}{o['name']}@{o['limit_price']}" for o in out))
    return out

# ===== A 量化综合排名第一(重建版) =====
cand = []
for code, s in series.items():
    if not uni(code, s, 270):
        continue
    cl = s["c"]
    wins = exps = n = 0
    tails = []
    for i in range(len(cl) - 270, len(cl) - 20, 10):
        fwd = cl[i + 20] / cl[i] - 1
        n += 1
        if fwd > 0:
            wins += 1
        exps += fwd
        tails.append(fwd)
    if n < 8:
        continue
    wr = wins / n
    exp = exps / n
    tail = float(np.quantile(tails, 0.05)) * 100
    if wr >= 0.60 and exp >= 0.01 and amt20(s) >= 1e8:
        cand.append((exp * 100, code, f"胜率{wr*100:.0f}% 期望20日{exp*100:+.1f}% 尾部{tail:+.1f}%"))
cand.sort(key=lambda x: -x[0])
emit("quant_rank_rebuild", "量化综合排名第一(重建版)", cand, 9)  # 老1(建行)+新9=10

# ===== B 主线方向法(20日最强3行业的成交额龙头) =====
icon = sqlite3.connect("file:data/industry_group_map_snapshot.db?mode=ro", uri=True)
ind_all = icon.execute("select code,l1 from l1_member").fetchall()
icon.close()
ind = {str(k)[-6:]: v for k, v in ind_all}
ind_r20 = {}
for code, s in series.items():
    k = ind.get(code)
    if k and len(s["c"]) >= 21 and s["d"][-1] == T and s["c"][-21] > 0:
        ind_r20.setdefault(k, []).append(s["c"][-1] / s["c"][-21] - 1)
ind_rank = sorted(((float(np.mean(v)), k) for k, v in ind_r20.items() if len(v) >= 3), reverse=True)
top3 = {k for _, k in ind_rank[:3]}
print("主线(20日最强3行业):", [(k, f"{r*100:+.1f}%") for r, k in ind_rank[:3]])
cand = []
for code, s in series.items():
    if not uni(code, s, 60) or ind.get(code) not in top3:
        continue
    r20 = s["c"][-1] / s["c"][-21] - 1
    if r20 <= 0:
        continue
    cand.append((amt20(s), code, f"[{ind.get(code)}] ret20={r20*100:+.1f}%"))
cand.sort(key=lambda x: -x[0])
emit("mainline_direction", "主线方向法(复盘主线=最强3行业龙头)", cand, 10)

# ===== C 对垒法复刻(估值修复侧: 低PE+修复动量+放量) =====
cand = []
for code, s in series.items():
    if not uni(code, s, 60) or code not in dbp:
        continue
    pe, pb = dbp[code]
    try:
        pe_f = float(pe)
    except Exception:
        continue
    if not (0 < pe_f <= 30):
        continue
    m20 = s["c"][-1] / s["c"][-21] - 1
    if not (0 <= m20 <= 0.15):
        continue
    v20 = float(np.mean(s["v"][-21:-1]))
    if v20 <= 0 or s["v"][-1] < 1.2 * v20:
        continue
    if amt20(s) < 1e8:
        continue
    cand.append((m20 * 100, code, f"PE{pe_f:.1f} 修复动量{m20*100:+.1f}%"))
cand.sort(key=lambda x: -x[0])
emit("f_arm_replica", "对垒法复刻(估值修复侧)", cand, 6)  # 老4(伊利/山东路桥/深物业A/天娱数科)+新6=10

# ===== D 跟踪池0826重扫 =====
pool = ["301223", "000530", "600527", "301032", "300306", "002026", "603017", "002282",
        "600262", "603408", "300082", "300519", "300445", "300341", "601226"]
cand = []
for code in pool:
    s = series.get(code)
    if not uni(code, s, 60):
        continue
    m20 = s["c"][-1] / s["c"][-21] - 1
    cand.append((m20 * 100, code, f"池成员 动量{m20*100:+.1f}%"))
cand.sort(key=lambda x: -x[0])
emit("tracking_pool_0826", "跟踪池0826重扫", cand, 7)  # 老3(澳洋/灵康/海油发展)+新7=10

# ===== E 厄尔尼诺重开(同五链映射, 第二轮) =====
elnino = [("600737", "中粮糖业 糖链对照"), ("601118", "海南橡胶 胶链对照"),
          ("002637", "赞宇科技 棕榈油加工"), ("002011", "盾安环境 空调高温样本")]
cand = []
for code, role in elnino:
    s = series.get(code)
    if s is None or code in taken or code in held:
        continue
    cand.append((0, code, role))
emit("elnino_rerun", "厄尔尼诺映射实验第二轮", cand, 5)

# ===== F 组合C抄底复刻(深跌磨底+量能转暖反转) =====
cand = []
for code, s in series.items():
    if not uni(code, s, 250):
        continue
    cl = s["c"]
    hi250 = float(np.max(cl[-250:]))
    dd = cl[-1] / hi250 - 1
    if dd > -0.40:
        continue
    chg20 = cl[-1] / cl[-21] - 1
    if chg20 > -0.10:
        continue
    v5 = float(np.mean(s["v"][-5:]))
    v60 = float(np.mean(s["v"][-60:]))
    if v5 <= v60 * 1.2:
        continue
    if cl[-1] <= cl[-2]:
        continue
    cand.append((dd * 100, code, f"距一年高{dd*100:.0f}% 20日{chg20*100:.0f}% 量转暖"))
cand.sort(key=lambda x: x[0])
emit("combo_c_rerun", "组合C抄底复刻", cand, 6)  # 老4(申达/双飞/国科恒泰/浦东建设)+新6=10

out = os.path.realpath(plan_path)
assert out == os.path.realpath(r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json"), "路径越界"
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
tot = sum(p["limit_price"] * p["volume"] for p in plan)
print("计划总计:", len(plan), "笔 | 预算≈%.0f元" % tot)
