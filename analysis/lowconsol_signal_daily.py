# -*- coding: utf-8 -*-
"""低位横盘候选·每日扫描呈报件 (零交易权 — 只产出候选清单, 买入须主人逐笔令 + trade_executor)
依据假设链(均过m7):
  ①低位横盘单因子: 跨相位稳健 +2.75~+4.55pp (PREREG_LOW_CONSOLIDATION_ALPHA_20261021, STAGE1B_OOS_CONFIRMED)
  ②主线首日增强: 相位条件有效 2024+ +5.11pp / 熊市无效 (PREREG_LOW_FRESH_MAINLINE_20261021, NOT_CONFIRMED_CROSS_PHASE)
用法: python analysis/lowconsol_signal_daily.py [--require-fresh] [--date YYYYMMDD]
  默认基于库内最新收盘日扫描; --require-fresh 仅保留主线首日(streak=1)命中者
输出: 控制台表 + data/lowconsol_signal_latest.json
口径: gauge官方A格子(pos250<30且|chg5|<3) + 申万L1量比Top3主线代理(非方向层实测);
      universe=6/0/3且上市>60日, 科创(688)单独标记(注意: 回测宇宙含科创, 交易口径剔除, 差异待量化);
      系统交易铁律提示: >30元不买/涨跌停不可成交/-8%止损口径
"""
import json, sys, sqlite3
import numpy as _np
rng2 = rng = __import__("numpy").random.default_rng(42)
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
REQUIRE_FRESH = "--require-fresh" in sys.argv
DATE_ARG = sys.argv[sys.argv.index("--date") + 1] if "--date" in sys.argv else None

db = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,close,volume,amount FROM stock_daily_meta_qfq ORDER BY code,date", db)
db.close()
df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].copy()
if DATE_ARG:
    df = df[df["date"] <= DATE_ARG]
latest = df["date"].max()
df = df[df["date"] <= latest]
import datetime as _dt
today = _dt.date.today().strftime("%Y%m%d")
stale = latest < today
print(f"[scan] 基于收盘日 {latest}" + ("  [STALE: qfq库尚未更新至今日, 结果为昨日口径]" if stale else ""))
g = df.groupby("code")["close"]
df["chg5"] = g.transform(lambda s: s.pct_change(5) * 100)
mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
gv = df.groupby("code")["volume"]
df["volr"] = gv.transform(lambda s: s.rolling(5).mean() / s.rolling(60).mean())
df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)
last = df[df["date"] == latest].copy()
# 相位开关标注(探索性, PREREG待前瞻验证): 全市场20日成交额分位
mkt = df.groupby("date").agg(mkt_amt=("amount", "sum")).reset_index().sort_values("date")
mkt["amt_20d"] = mkt["mkt_amt"].rolling(20).mean()
mkt["amt_q"] = mkt["amt_20d"].rank(pct=True)
amt_q_now = mkt.loc[mkt["date"] == latest, "amt_q"]
amt_q_val = round(float(amt_q_now.iloc[0]), 3) if len(amt_q_now) else None
phase_sw = ("ON" if (amt_q_val is not None and amt_q_val >= 0.5) else "OFF") if amt_q_val is not None else "NA"

# 主线代理(申万L1)
l1c = sqlite3.connect("data/industry_group_map_snapshot.db")
l1map = pd.read_sql_query("SELECT code,name,l1 FROM l1_member WHERE ambiguous_flag=0", l1c)
l1map["code"] = l1map["code"].astype(str)
l1c.close()
w = df.merge(l1map, on="code", how="left")
w["vol_l1"] = w.groupby(["l1", "date"])["volume"].transform("sum")
ind = w.drop_duplicates(["date", "l1"])[["date", "l1", "vol_l1"]].merge(
    w.groupby(["date", "l1"])["chg5"].median().rename("icm").reset_index(), on=["date", "l1"])
ind = ind.sort_values(["l1", "date"])
ind["v5"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.rolling(5).sum())
ind["v5p"] = ind.groupby("l1")["vol_l1"].transform(lambda s: s.shift(5).rolling(5).sum())
ind["volr_l1"] = ind["v5"] / ind["v5p"]
ind["c5"] = ind.groupby("l1")["icm"].transform(lambda s: s.rolling(5).mean())
hot, cur, streak = {}, {}, {}
for dt, sub in ind.groupby("date"):
    s = sub.dropna(subset=["volr_l1", "c5"])
    hot[dt] = set(s[s["c5"] > 0].nlargest(3, "volr_l1")["l1"])
for dt in sorted(hot):
    nxt = {}
    for l1 in hot[dt]:
        cur_v = cur.get(l1, 0) + 1
        nxt[l1] = cur_v
        streak[(dt, l1)] = cur_v
    cur = nxt
last = last.merge(l1map, on="code", how="left")
last["main_hit"] = [(l1v == l1v) and (l1v in hot.get(latest, set())) for l1v in last["l1"]]
last["streak"] = [streak.get((latest, l1v), 0) if (l1v == l1v) else 0 for l1v in last["l1"]]

cand = last[last["is_A"] & (last["close"] <= 30)].copy()  # 系统铁律: >30元不买
cand["star"] = cand["code"].str.startswith("688")
cand["px_floor_risk"] = cand["close"] < 2.0  # 呈现层风控: 面值退市风险区(非策略条件)
# ST过滤在 main_hit 子集构建前执行(上方), 同步作用于背景池
cand = cand[~cand["name"].str.contains("ST", na=False)]  # 系统铁律: 剔ST/*ST(宪法宇宙)
# 20261026修复: 名称匹配+st_daily数据库双保险(联创光电教训: 名称未更新但已是ST)
try:
    _stc = sqlite3.connect("file:data/st_history.db?mode=ro", uri=True)
    _st_latest = _stc.execute("""
        SELECT code FROM st_daily s1
        WHERE is_st=1 AND date = (SELECT MAX(date) FROM st_daily s2 WHERE s2.code = s1.code)
    """).fetchall()
    _stc.close()
    _st_codes = set()
    for (_rc,) in _st_latest:
        _dg = "".join(c for c in str(_rc) if c.isdigit())
        _st_codes.add(_dg[-6:] if len(_dg) >= 6 else _dg)
    _before = len(cand)
    cand = cand[~cand["code"].str[-6:].isin(_st_codes)]
    _dropped = _before - len(cand)
    if _dropped:
        print(f"[ST双保险] st_daily额外剔除{_dropped}只(名称未含ST但数据库标记is_st=1)")
except Exception as _e:
    print(f"[ST双保险] st_daily查询失败(名称匹配兜底): {_e}")
mh_all = cand[cand["main_hit"] & ~cand["px_floor_risk"]].sort_values(["streak", "close"])
mh_688 = mh_all[mh_all["star"]]  # 主人裁决20261021: 688跳过不买(回测宇宙含科创/交易口径剔除), 单列参考
mh = mh_all[~mh_all["star"]]
bg = cand[~cand["main_hit"] & ~cand["px_floor_risk"]]
if REQUIRE_FRESH:
    mh = mh[mh["streak"] == 1]
cols = ["code", "name", "close", "pos250", "chg5", "volr", "l1", "streak", "star"]
mh_show = mh[cols].copy()
for c_ in ("pos250", "chg5", "volr"):
    mh_show[c_] = mh_show[c_].round(2)
bg_show = bg[cols].assign(rn=rng.random(len(bg))).sort_values("rn").head(20).drop(columns=["rn"])
out = {"scan_date": latest, "stale": stale, "require_fresh": REQUIRE_FRESH,
       "phase_switch": {"amt_q": amt_q_val, "switch": phase_sw,
                        "note": "探索性开关(活跃度分位>=0.5=ON), 须前瞻验证, 不作为自动过滤"},
       "excluded_688": mh_688[cols].to_dict("records"),
       "context": {"hyp1": "低位横盘跨相位+2.75~4.55pp(OOS_CONFIRMED)",
                    "hyp2": "主线首日2024+ +5.11pp/熊市无效(NOT_CONFIRMED, 相位依赖)",
                    "zero_trade_authority": True,
                    "note": "主线命中子集=效应载体; 背景池为集合级效应, 无个股排序依据"},
       "n_total": int(len(cand)), "n_main_hit": int(len(mh)), "n_fresh_today": int((mh["streak"] == 1).sum()),
       "n_px_floor_excluded": int(cand["px_floor_risk"].sum()),
       "main_hit_list": mh_show.to_dict("records"),
       "background_sample": bg_show.to_dict("records"),
       "background_pool_full": bg[cols].to_dict("records")}  # 20261025增: 全量背景池导出, 供batch_battle_plan.py组合构建消费(语义零改动)
json.dump(out, open("data/lowconsol_signal_latest.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print(f"[相位] 活跃度分位={amt_q_val} → 首日规则开关={phase_sw} (探索性标注, 待前瞻验证)")
print(f"[候选] 总池 {len(cand)} 只 | 主线命中 {len(mh)} 只(首日 {int((mh['streak'] == 1).sum())}) | 面值风险剔除 {int(cand['px_floor_risk'].sum())}")
print("--- A) 主线命中子集(效应载体, 首日优先→低价) ---")
print(mh_show.to_string(index=False) if len(mh_show) else "  (今日无)")
print("--- B) 非主线背景池随机抽样20 ---")
print(bg_show.to_string(index=False))
print("\n[提示] 零交易权: 买入须主人逐笔令+trade_executor; 科创688=已剔除(主人裁决); 剔688口径预期效应+3.81pp CI(2.76,4.89)已量化(20261022)")
