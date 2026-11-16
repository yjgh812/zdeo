# -*- coding: utf-8 -*-
"""build_topup.py — 盈利模式扩容: 9模式各补足至10只(老成员计入) + 挂单计划 + 模式登记表。
选股池=已三源验证的sel11名单 + bp_ep引擎新鲜排名(M1/M9)。零交易权, 只产计划JSON。"""
import os, sys, json, sqlite3, io
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))

from stock_name_lookup import get_name

sel = json.load(open(os.path.join(HERE, "sel11_out.json"), encoding="utf-8"))
T = int(sel["signal_date"])
G = sel["groups"]

W23_SKIP = {"001318", "600928", "000001", "601528", "002839", "600908", "000415"}

# 各方法老成员(官方报告在册)
OLD = {
    "bp_ep_engine": ["601997", "601668", "600739", "600064", "600704"],
    "breakout": ["603278", "001979", "002223", "000559"],
    "tandi": ["002172", "000949", "601872"],
    "dibo": ["603939", "601857", "601728", "600350", "600012"],
    "starter": ["601601", "000100", "300059"],
    "manxian": ["600688", "603697", "600098", "600320"],
    "shuangyou": ["601577", "600023", "601156", "600926"],
    "industry": ["601328", "600573", "002966", "002807"],
    "bpep_weekly": ["600900", "601169"],
}
QUOTA = {k: 10 - len(v) for k, v in OLD.items()}

# ---------- bp_ep新鲜排名(M1/M9) ----------
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
rows = con.execute(
    "select code,date,open,high,low,close,volume,amount from stock_daily_meta_qfq "
    "where date>=? order by code,date", (str(int(str(T)[:4]) - 3) + "0000",)).fetchall()
con.close()
series = {}
for code, d, o, h, l, c, v, a in rows:
    s = series.setdefault(code, {"c": [], "d": []})
    s["c"].append(float(c))
    s["d"].append(int(d))

bcon = sqlite3.connect("file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro", uri=True)
db_rows = bcon.execute(
    "select ts_code, pe_ttm, pb, trade_date from daily_basic "
    "where trade_date>=? and trade_date<=? order by ts_code, trade_date",
    (str(T - 40), str(T))).fetchall()
bcon.close()
dbp = {}
for ts, pe, pb, td in db_rows:
    dbp[ts[:6]] = (pe, pb)

scon = sqlite3.connect("file:data/st_history.db?mode=ro", uri=True)
st_latest = scon.execute(
    "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 where s2.code=s1.code)").fetchall()
scon.close()
st_set = {str(c)[-6:] for c, s_ in st_latest if s_ == 1}

held = set()
for v in OLD.values():
    held.update(v)
# 其余持仓(非九模式老成员)也剔除
rep = json.load(open("data/group_pnl_report_20261031.json", encoding="utf-8"))
held_all = set()
for gv in rep["groups_source"].values():
    for s in gv.get("stocks", []):
        held_all.add(str(s["code"])[-6:])
held |= held_all

pairs = []
for code, (pe, pb) in dbp.items():
    try:
        pe_f, pb_f = float(pe), float(pb)
    except Exception:
        continue
    if pe_f > 0 and pb_f > 0:
        pairs.append((code, 1.0 / pe_f, 1.0 / pb_f))
dfp = pd.DataFrame(pairs, columns=["code", "ep", "bp"])
dfp["score"] = dfp["ep"].rank(pct=True) + dfp["bp"].rank(pct=True)
dfp = dfp.sort_values("score", ascending=False)

bp_ranked = []
for _, r in dfp.iterrows():
    code = r["code"]
    s = series.get(code)
    if s is None or len(s["c"]) < 60 or s["d"][-1] != T:
        continue
    if code in held or code in st_set or code in W23_SKIP:
        continue
    if code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        continue
    if not (0.5 < s["c"][-1] <= 30):
        continue
    bp_ranked.append((code, float(r["score"]), float(s["c"][-1])))
print("bp_ep可用排名数:", len(bp_ranked), "前13:", [c for c, _, _ in bp_ranked[:13]])

m1_new = bp_ranked[:QUOTA["bp_ep_engine"]]
m9_new = bp_ranked[QUOTA["bp_ep_engine"]:QUOTA["bp_ep_engine"] + QUOTA["bpep_weekly"]]

# ---------- M2-M8: 从sel11名单按序补足(跳W23/跨模式去重) ----------
def pick_from(keys, quota, taken):
    out = []
    for k in keys:
        for s in G[k]["stocks"]:
            code = s["code"]
            if code in taken or code in W23_SKIP:
                continue
            out.append((code, float(s["score"]), s["reason"], k))
            if len(out) >= quota:
                return out
    return out

taken = set(c for c, _, _ in m1_new) | set(c for c, _, _ in m9_new)
plan_groups = {}

# 跨模式股票按最优名次预分配
def best_rank(code):
    best = (99, None)
    for k in ("breakout_mainline", "revival_tandi", "revival_dibo", "type_sample_C_starter",
              "xushi_manxian", "xushi_shuangyou", "revival_hydl", "revival_hy_tail"):
        for i, (score, c, reason) in enumerate(G[k]["stocks"]):
            if c == code and (i + 1) < best[0]:
                best = (i + 1, k)
    return best

# M2 突破共振
g2 = pick_from(["breakout_mainline"], QUOTA["breakout"], taken)
# M3 探底
g3 = pick_from(["revival_tandi"], QUOTA["tandi"], taken | {x[0] for x in g2})
# M4 低波
g4 = pick_from(["revival_dibo"], QUOTA["dibo"], taken | {x[0] for x in g2} | {x[0] for x in g3})
# M5 低位启动
g5 = pick_from(["type_sample_C_starter"], QUOTA["starter"],
               taken | {x[0] for x in g2} | {x[0] for x in g3} | {x[0] for x in g4})
# M6 满弦
prev = taken | {x[0] for x in g2} | {x[0] for x in g3} | {x[0] for x in g4} | {x[0] for x in g5}
g6 = pick_from(["xushi_manxian"], QUOTA["manxian"], prev)
# M7 双优
prev |= {x[0] for x in g6}
g7 = pick_from(["xushi_shuangyou"], QUOTA["shuangyou"], prev)
# M8 行业动量族(hydl先, hy_tail补)
prev |= {x[0] for x in g7}
g8 = pick_from(["revival_hydl", "revival_hy_tail"], QUOTA["industry"], prev)

def close_of(code):
    s = series.get(code)
    return float(s["c"][-1]) if s and s["d"][-1] == T else None

METHOD_CN = {
    "bp_ep_engine": "bp_ep价值引擎", "breakout": "突破共振", "tandi": "探底缩量企稳",
    "dibo": "低波", "starter": "低位启动", "manxian": "满弦蓄势",
    "shuangyou": "蓄势双优", "industry": "行业动量族", "bpep_weekly": "bp_ep周滚动",
}
plan = []
for mk, picks in (("bp_ep_engine", m1_new), ("breakout", g2), ("tandi", g3), ("dibo", g4),
                  ("starter", g5), ("manxian", g6), ("shuangyou", g7),
                  ("industry", g8), ("bpep_weekly", m9_new)):
    cn = METHOD_CN[mk]
    n_old = len(OLD[mk])
    for i, item in enumerate(picks, 1):
        if len(item) == 4:
            code, score, reason, k = item
        else:
            code, score, close_px = item
            reason = "bp_ep=%.3f" % score
        px = close_of(code)
        if px is None:
            continue
        limit = round(px * 1.005, 2)
        plan.append({
            "code": code, "name": get_name(code) or "?",
            "source": "manual_confirmed" if False else mk,
            "volume": 100, "limit_price": limit,
            "note": "主人令20261101盈利模式扩容[%s]老%d+新%d之新#%d; 高挂0.5%%; 信号日%d; %s" % (
                cn, n_old, 10 - n_old, i, T, reason[:60]),
        })
        taken.add(code)
    print(f"{cn}: 老{n_old}+新{len(picks)}=10")

PLAN_PATH = os.path.realpath(os.path.join(ROOT, "data", "winner_methods_topup_plan_20261101.json"))
# Mimosa L3 加固: 输出精确路径白名单(文件名硬编码常量, 规范化后必须等于项目data目录预期路径)
assert PLAN_PATH == os.path.realpath(r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json"), "输出路径越界"
json.dump(plan, open(r"D:\zcodesj\data\winner_methods_topup_plan_20261101.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
tot = sum(p["limit_price"] * p["volume"] for p in plan)
print("计划总笔数:", len(plan), "| 预算≈%.0f元 (可用现金89,159)" % tot)

# ---------- 模式登记表(记忆固化) ----------
registry = {
    "version": "V1_20261101",
    "owner_directive": "主人令20261101: 盈利模式要记住, 不要每次问都搞不清",
    "observe_window": "2026-11 (本月观察模拟盘盈利)",
    "tracking": "group_pnl_report每日自动跟踪(源级=模式级)",
    "methods": {
        "第一梯队_净赚已验证": [
            {"name": "bp_ep价值引擎体系", "src": "bp_ep_core/bpep_weekly", "net": "+6900",
             "evidence": "A+C级", "逻辑": "便宜+质量(账面市值比×盈利收益率)"},
            {"name": "主人令+量化综合排名第一", "src": "manual_confirmed+排名背书", "net": "+5472",
             "evidence": "A级备注原文", "逻辑": "主人方向×量化第一背书(建行胜率76%)", "状态": "排名工具待定位(P2)"},
            {"name": "深贴回补", "src": "sold_rebuy_deep8", "net": "+80",
             "evidence": "A级(预注册)", "逻辑": "自家深贴-8%卖出票回补", "状态": "事件触发型+R38闸在位"},
            {"name": "ML胜率排名Top10", "src": "ml_signal", "net": "≈持平(-410)",
             "evidence": "A级备注原文", "逻辑": "个股后验胜率排名(赢家能力强/盈亏比差)", "状态": "名单来自外部App, 系统侧缺考"},
        ],
        "第二梯队_小仓全绿_窗口未满": [
            {"name": "突破共振", "src": "breakout_mainline", "net": "+246", "winrate": "4/4",
             "逻辑": "创20日新高+量1.5x+行业强于大盘"},
            {"name": "探底缩量企稳", "src": "revival_tandi", "net": "+237", "winrate": "3/3",
             "逻辑": "20日跌15%+当日收阳+量<0.9x20均量"},
            {"name": "低波", "src": "revival_dibo", "net": "+217", "winrate": "4/5",
             "逻辑": "60日收益波动率下1/3+动量正"},
            {"name": "低位启动", "src": "type_sample_C_starter", "net": "+81", "winrate": "2/3",
             "逻辑": "pos250<=30+首阳2~5.5%+动量弱", "状态": "重构口径"},
            {"name": "满弦蓄势", "src": "xushi_manxian", "net": "+71", "winrate": "3/4",
             "逻辑": "低位+未创新低+10日振幅<=10%+量缩+MA20翘"},
            {"name": "蓄势双优", "src": "xushi_shuangyou", "net": "+49", "winrate": "4/4",
             "逻辑": "满弦池×自身历史共振胜率", "状态": "近似实现"},
            {"name": "行业动量族", "src": "revival_hydl+revival_hy_tail", "net": "+46", "winrate": "2/2",
             "逻辑": "20日强行业成员/价值×强行业"},
        ],
        "明确淘汰": ["方法精选(-5575)", "首板封板(净-209+打板偏差)", "驱动力对垒臂(净-219)",
                  "综合分(-80)", "无共振突破(-64)", "振幅A/B/三因子(净负)", "LSTM/ICIR/118异象(净负)"],
        "尺子非方法": ["中枢对照(兴业+131为环境β)"],
    },
}
REG_PATH = os.path.realpath(os.path.join(ROOT, "data", "profitable_methods_registry.json"))
assert REG_PATH == os.path.realpath(r"D:\zcodesj\data\profitable_methods_registry.json"), "输出路径越界"
json.dump(registry, open(r"D:\zcodesj\data\profitable_methods_registry.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("登记表: data/profitable_methods_registry.json")
