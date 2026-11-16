# -*- coding: utf-8 -*-
"""屠龙系统 全域历史多模型回测引擎 v2 (2026-10-22 专项指令)
主人专项指令: 2016-01-01~2026-10-22 / 初始10万 / 沪深主板+创业板 / 剔ST·科创·北交所·次新<60天
完整执行系统规则: 仓位体系(标准v1第7章) + 止盈止损唯一标准(第8章) + R闸可回测子集 + 熔断

证伪档案引用(宪法§10.7 / FALSIFIED_FIRST):
  - 追突破/追确认族已证伪(BT-FAIL-005, SKILL-SWEEP-STAGE1 突破x2免测条款) → 本引擎不含追突破腿
  - 低位补涨组合级已证伪(MAINLINE-STAGE2-S2B-LOWPOS) → 规则腿用已验证低位横盘A格子口径
    (PREREG_LOW_CONSOLIDATION_ALPHA_20261021 OOS_CONFIRMED, lowconsol_signal_daily.py 官方实现)
  - ML腿训练窗重叠样本内口径 → 全部带 ML_LEG_FOOTNOTE(backtest_template_std 固化铁律)

口径: qfq唯一价格源(收益/盈亏) + raw交叉源(30元铁律/涨跌停判定/成交价展示)
     / next-bar(信号T收盘,成交T+1开盘) / 配对同锁 / 无前视(触发仅用<=T数据)
已知实现边界(如实标注): ST为当前快照口径; 行业映射为快照口径; "趋势走坏"主观退出未实现;
     R19解禁/R35回补冷却未纳入(数据覆盖待验证); 跌停连续>10日以收盘强平近似并留痕。
输出: reports/backtest_fullhist_2016_2026_20261022/ {trades_*.csv, equity_*.csv, summary.json, run_log.txt}
"""
import os, sys, json, time, sqlite3, argparse
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
t0 = time.time()

AP = argparse.ArgumentParser()
AP.add_argument("--smoke", action="store_true", help="冒烟: 2016单年+M1/M3快速验证管线")
AP.add_argument("--round2", action="store_true", help="第二轮: 变体矩阵(stopAtr/tpdd2/ma20exit/liq/phase)")
AP.add_argument("--round3", action="store_true", help="第三轮: 验收变体组合叠加+M6 TopN扫描+ATR k扫描")
AP.add_argument("--round4", action="store_true", help="第四轮: 统一账户撮合+月度再平衡对照+行业约束+熔断前沿")
AP.add_argument("--round5", action="store_true", help="第五轮: R30a/b豁免A/B(回测层取证)+权重结构+M6熔断扫描")
AP.add_argument("--round6", action="store_true", help="第六轮: R30b剂量-反应(25/30/35/50/inf)+M6域MA20组合")
AP.add_argument("--round7", action="store_true", help="第七轮: R30a同日闸对篮子源摩擦A/B(cap3/4)")
AP.add_argument("--end", type=int, default=0, help="右端点延伸(YYYYMMDD, 如20261023); 默认20261022")
AP.add_argument("--round8", action="store_true",
                help="第二轮复测(20261023主人「继续」令): M1域A包组合取证(清单A1-A4) + B1落闸态旗舰回测等效(R30b帽30%无豁免+R30a cap3) + 右端点延伸")
AP.add_argument("--round9", action="store_true",
                help="第三轮复测(20261023主人「测试下按推荐继续」): A4/A2采纳子集隔离验收(M1/M6域) + 旗舰回归断言")
AP.add_argument("--round10", action="store_true",
                help="第四轮复测(20261023主人「继续」令): A1/A3隔离验收+A1A3A4无A2栈(M1域, 只取证不预采纳)")
AP.add_argument("--round11", action="store_true",
                help="第五轮复测(20261024主人「继续」令): A3+A4自然对补测(矩阵收尾, 决策规则跑前冻结)")
AP.add_argument("--round12", action="store_true",
                help="第六轮复测(20261024主人「继续」令): 修复后腿统一账户架构对照(U602020_r12已采纳规则三腿 / U7030_r12双腿) vs 旗舰")
AP.add_argument("--round13", action="store_true",
                help="第二轮迭代复测(20261026优化条目A/B): BP个股止损15%/持仓180天上限/等权MA200降半仓 + 旗舰回归断言")
AP.add_argument("--round14", action="store_true",
                help="预注册复测(20261026「继续」令): MA200月末再平衡半仓/BP池剔房地产L1/组合/动态权重BP70-M6 30熊态禁开 + 旗舰回归断言")
AP.add_argument("--round15", action="store_true",
                help="条目8胜率结构改造A/B(20261026「继续」令): BP腿止盈15/20/25% + 剔房地产+止盈20%组合; 冻结标准=胜率>=70%且PF>=3.0")
AP.add_argument("--outdir", default="",
                help="产物目录覆盖(20261024任务书重申: 20261020窗口独立目录, 不覆写既有round8-12档案)")
ARGS, _ = AP.parse_known_args()

# ================= 配置(唯一真源: tulong_system_standard_v1.md 第7/8章 + pre_trade_check_v2) =================
CFG = dict(
    start=20160101, end=20261022, warmup=20140601,
    init_cap=100000.0,
    max_positions=50,          # R9 持仓上限
    pos_weight=0.10,           # 单票目标权重(≤20%上限内; 实验信号源≤10%口径)
    price_max=30.0, px_floor=2.0,
    min_amt20=30e6, list_min_tradedays=61,
    r32_chg20_reject=0.30,     # R32 高位动量禁买(pre_trade_check_v2 L121)
    r30a_same_day_sector_cap=3, r30b_cluster_cap=0.25,   # R30a/b
    stop_ratio=0.92, tp_band1=0.10, tp_band2=0.30, tp_dd1=0.10, tp_dd2=0.06,  # 第8章唯一标准
    cb_dd_reduce=0.20, cb_dd_flat=0.30, cb_flat_days=3, cb_reduce_target=0.30,  # 熔断
    commission=0.00025, commission_min=5.0,
    stamp_normal=0.001, stamp_after_20230828=0.0005, slippage=0.001,
    v375_thr_a=0.75, v375_thr_b=0.70, breakout_thr=0.75, wfa_topn=20,
    gem_20cm_from=20200824,
    # ---- round2 variant keys (defaults = baseline) ----
    stop_buffer_atr=0.0,     # ③ 止损ATR缓冲: stop = max(cost*stop_floor, trail - k*ATR14)
    stop_floor=0.88,         #    缓冲硬底: 单笔最大亏损不超过-12%
    ma20_exit=False,         # ⑤ 趋势走坏规则化: 收盘<MA20连续3日 → 次日开盘卖
    phase_min_q=None,        # ② 相位开关: 仅活跃度分位(全市场20日额rank)≥阈值日出信号
    # ---- round13 variant keys (defaults = baseline, 20261026优化条目A/B) ----
    bp_stop_loss_pct=0.0,    # BP腿个股硬止损(收盘价相对成本跌幅≥阈值, 次日开盘卖; 0=关闭)
    bp_max_hold_days=0,      # BP腿持仓天数上限, 到期强制重评(次日开盘卖; 0=关闭)
    env_ma200_half=False,    # 等权宇宙MA200环境闸: 收盘<MA200 时次日买入预算减半
    # ---- round14 variant keys (defaults = baseline, 预注册复测) ----
    env_ma200_month_end=False,  # 熊态仅月末再平衡日(信号日次日)半仓, 其余熊态日照常
    env_ma200_off=False,        # 熊态该腿禁开新仓(动态权重: 趋势腿风险关闭)
    bp_exclude_l1=False,        # BP池剔除预设申万L1集合(见round14块 _RE_L1, 快照口径)
    # ---- round15 variant keys (defaults = baseline, 条目8胜率结构改造) ----
    bp_take_profit_pct=0.0,  # BP腿止盈: 收盘/成本-1>=阈值 -> 次日开盘全仓退出(0=关闭)
)
R2_MODELS = ["M1_lowconsol_main", "M6_wfa_top20"]
VARIANTS = [
    dict(tag="R2stopAtr", overrides=dict(stop_buffer_atr=1.0), models=R2_MODELS),
    dict(tag="R2tpdd2_8", overrides=dict(tp_dd2=0.08), models=R2_MODELS),
    dict(tag="R2ma20exit", overrides=dict(ma20_exit=True), models=R2_MODELS),
    dict(tag="R2liq5000", overrides=dict(min_amt20=50e6), models=R2_MODELS),
    dict(tag="R2liq2000", overrides=dict(min_amt20=20e6), models=R2_MODELS),
    dict(tag="R2phaseON", overrides=dict(phase_min_q=0.5), models=["M1_lowconsol_main"]),
]
R3_VARIANTS = [
    dict(tag="R3m1_p_l", overrides=dict(phase_min_q=0.5, min_amt20=20e6), models=["M1_lowconsol_main"]),
    dict(tag="R3m1_pl_sa05", overrides=dict(phase_min_q=0.5, min_amt20=20e6, stop_buffer_atr=0.5), models=["M1_lowconsol_main"]),
    dict(tag="R3m1_pl_sa15", overrides=dict(phase_min_q=0.5, min_amt20=20e6, stop_buffer_atr=1.5), models=["M1_lowconsol_main"]),
    dict(tag="R3m6_l", overrides=dict(min_amt20=20e6), models=["M6_wfa_top20"]),
    dict(tag="R3m6_l_top10", overrides=dict(min_amt20=20e6, wfa_topn=10), models=["M6_wfa_top20"]),
    dict(tag="R3m6_l_top50", overrides=dict(min_amt20=20e6, wfa_topn=50), models=["M6_wfa_top20"]),
    dict(tag="R3m6_l_sa05", overrides=dict(min_amt20=20e6, stop_buffer_atr=0.5), models=["M6_wfa_top20"]),
]
if ARGS.end:
    CFG["end"] = int(ARGS.end)
if ARGS.smoke:
    CFG["end"] = 20161231
REPORT_DIR = ARGS.outdir if ARGS.outdir else r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
os.makedirs(REPORT_DIR, exist_ok=True)
LOG = open(os.path.join(REPORT_DIR, "run_log.txt"), "a", encoding="utf-8")
def log(msg):
    s = f"[{time.time()-t0:7.1f}s] {msg}"
    print(s); LOG.write(s + "\n"); LOG.flush()

ML_FOOT = ("⚠️ML样本内口径(训练窗重叠); 对比仅同腿有效, 禁作为生产预期")
def die(msg):
    log(f"FATAL: {msg}"); LOG.close(); sys.exit(2)

DB_QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
DB_RAW = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
DB_L1 = r"D:\zcodesj\data\industry_group_map_snapshot.db"

# ================= 1. 数据加载 =================
log("加载qfq主表(>=warmup, ATTACH raw交叉列)...")
con = sqlite3.connect(f"file:{DB_QFQ}?mode=ro", uri=True)
con.execute(f"ATTACH DATABASE 'file:{DB_RAW}?mode=ro' AS rr")
df = pd.read_sql_query(
    """SELECT m.code, m.date, m.open AS o, m.high AS h, m.low AS l, m.close AS c,
              m.volume AS v, m.amount AS amt, d.open AS o_raw, d.close AS c_raw
       FROM stock_daily_meta_qfq m
       LEFT JOIN rr.stock_daily_raw d ON d.code=m.code AND d.date=m.date
       WHERE m.date >= ?
       ORDER BY m.code, m.date""", con, params=(str(CFG["warmup"]),))
con.close()
if df.empty: die("qfq加载为空")
df["date"] = df["date"].astype(np.int64)
log(f"qfq行数={len(df):,} raw缺失={int(df['c_raw'].isna().sum()):,}")
row1 = df.iloc[100]
log(f"sanity首行: code={row1['code']} date={row1['date']} c={row1['c']} c_raw={row1['c_raw']}")

df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
df = df[~df["code"].str.startswith(("688", "689"))].copy()
df["days_listed"] = df.groupby("code", observed=True).cumcount() + 1
df = df[df["days_listed"] >= CFG["list_min_tradedays"]].copy()

ci = sqlite3.connect(f"file:{DB_RAW}?mode=ro", uri=True)
info = pd.read_sql_query("SELECT code,name,is_st,status FROM stock_info", ci); ci.close()
info["code"] = info["code"].astype(str)
st_set = set(info.loc[(info["is_st"].astype(str).str.strip() == "1") |
                      (info["name"].fillna("").str.contains("ST")), "code"])
df = df[~df["code"].isin(st_set)].copy()
log(f"ST快照剔除 {len(st_set)} 只 | 过滤后行数={len(df):,}")
name_map = dict(zip(info["code"], info["name"]))
l1c = sqlite3.connect(DB_L1)
l1map = pd.read_sql_query("SELECT code,name,l1 FROM l1_member WHERE ambiguous_flag=0", l1c)
l1c.close(); l1map["code"] = l1map["code"].astype(str)
l1_of = dict(zip(l1map["code"], l1map["l1"]))
df["l1"] = df["code"].map(l1_of)
log(f"行业映射覆盖 {df['l1'].notna().mean()*100:.1f}%")

# ================= 2. 指标与信号 =================
log("计算指标...")
g = df.groupby("code", observed=True)
df["chg5"] = g["c"].transform(lambda s: s.pct_change(5) * 100)
mn = g["c"].transform(lambda s: s.rolling(250, min_periods=120).min())
mx = g["c"].transform(lambda s: s.rolling(250, min_periods=120).max())
df["pos250"] = np.where(mx > mn, (df["c"] - mn) / (mx - mn) * 100, np.nan)
df["volr"] = g["v"].transform(lambda s: s.rolling(5).mean() / s.rolling(60).mean())
df["chg20"] = df["c"] / g["c"].transform(lambda s: s.shift(21)) - 1.0
df["amt20"] = g["amt"].transform(lambda s: s.rolling(20).mean())
del g, mn, mx

ok_liq = df["amt20"] >= CFG["min_amt20"]
ok_px = (df["c_raw"] <= CFG["price_max"]) & (df["c_raw"] >= CFG["px_floor"])
ok_r32 = ~(df["chg20"] > CFG["r32_chg20_reject"])
df["is_A"] = (df["pos250"] < 30) & (df["chg5"].abs() < 3)

log("计算L1主线hot集合...")
w = df.dropna(subset=["l1"])[["date", "l1", "v", "chg5"]].copy()
w["vol_l1"] = w.groupby(["l1", "date"], observed=True)["v"].transform("sum")
ind = w.drop_duplicates(["date", "l1"])[["date", "l1", "vol_l1"]].merge(
    w.groupby(["date", "l1"], observed=True)["chg5"].median().rename("icm").reset_index(),
    on=["date", "l1"]).sort_values(["l1", "date"])
ig = ind.groupby("l1", observed=True)
ind["volr_l1"] = ig["vol_l1"].transform(lambda s: s.rolling(5).sum()) / \
                 ig["vol_l1"].transform(lambda s: s.shift(5).rolling(5).sum())
ind["c5"] = ig["icm"].transform(lambda s: s.rolling(5).mean())
hot = {}
for dt_, sub in ind.groupby("date", sort=True):
    s = sub.dropna(subset=["volr_l1", "c5"])
    hot[dt_] = set(s[s["c5"] > 0].nlargest(3, "volr_l1")["l1"])
del w, ind, ig
df["main_hit"] = [(l1v == l1v) and (l1v in hot.get(dt_, set()))
                  for dt_, l1v in zip(df["date"], df["l1"])]

def load_scores(table, valcol):
    cc = sqlite3.connect(f"file:{DB_RAW}?mode=ro", uri=True)
    s = pd.read_sql_query(f"SELECT code,date,{valcol} AS p FROM {table} WHERE date>=? AND date<=?",
                          cc, params=(str(CFG["start"]), str(CFG["end"])))
    cc.close(); s["code"] = s["code"].astype(str); s["date"] = s["date"].astype(np.int64)
    return s
log("加载模型库信号...")
v375 = load_scores("stock_signals_v375", "v375_prob")
brk = load_scores("stock_signals_breakout", "breakout_prob")
wfa = load_scores("stock_ml_scores_v23_wfa", "ml_prob")
for nm, s_ in (("v375", v375), ("breakout", brk), ("wfa", wfa)):
    if s_.empty:
        log(f"  {nm}: 0行(窗口无覆盖)")
        continue
    log(f"  {nm}: {len(s_):,}行 p分位[10/50/90]={np.percentile(s_['p'], [10,50,90]).round(3)}")

_mkt = df.groupby("date")["amt"].sum().sort_index()
amtq_map = _mkt.rolling(20).mean().rank(pct=True).to_dict()
sig = df.loc[df["date"].between(CFG["start"], CFG["end"]),
             ["code", "date", "volr", "amt20", "l1", "is_A", "main_hit"]].copy()
sig["amt_q"] = sig["date"].map(amtq_map)
sig = sig[ok_px.reindex(sig.index) & ok_r32.reindex(sig.index)]  # 流动性阈值移至build_daily(变体可调)
sig = sig.merge(v375.rename(columns={"p": "p375"}), on=["code", "date"], how="left")
sig = sig.merge(brk.rename(columns={"p": "pbrk"}), on=["code", "date"], how="left")
sig = sig.merge(wfa.rename(columns={"p": "pwfa"}), on=["code", "date"], how="left")
sig["m1"] = sig["is_A"] & sig["main_hit"]
sig["m2"] = sig["is_A"] & ~sig["main_hit"]
sig["m3"] = sig["p375"].fillna(-1) >= CFG["v375_thr_a"]
sig["m4"] = (sig["p375"].fillna(-1) >= CFG["v375_thr_b"]) & ~sig["m3"]
sig["m5"] = sig["pbrk"].fillna(-1) >= CFG["breakout_thr"]
del df, v375, brk, wfa
import gc; gc.collect()

MODEL_DEFS = {
    "M1_lowconsol_main": dict(mask="m1", score="volr", start=CFG["start"],
                              note="低位横盘A格子+主线对齐(官方口径, OOS_CONFIRMED方向)"),
    "M2_lowconsol_bgp": dict(mask="m2", score="volr", start=CFG["start"],
                             note="A格子背景池(无主线对齐, 集合级效应对照)"),
    "M3_v375_top30": dict(mask=None, score="p375", start=CFG["start"], topn=30,
                          note="v375逐日Top30(库分布p90=0.435, 文档0.75生产阈值在当前库饥饿→秩口径校准) " + ML_FOOT),
    "M4_v375_top10": dict(mask=None, score="p375", start=CFG["start"], topn=10,
                          note="v375逐日Top10(更严信号预算敏感性) " + ML_FOOT),
    "M5_breakout_top30": dict(mask=None, score="pbrk", start=CFG["start"], topn=30,
                              note="breakout模型库逐日Top30(库分布p90=0.586, 阈值改秩口径) " + ML_FOOT),
    "M6_wfa_top20": dict(mask=None, score="pwfa", start=20180102, topn=CFG["wfa_topn"],
                         note="wfa walk-forward Top20(2018+, OOS洁净)"),
}
if ARGS.smoke:
    MODEL_DEFS = {k: v for k, v in MODEL_DEFS.items() if k in ("M1_lowconsol_main", "M3_v375_top30")}

def build_daily(mname, V):
    md = MODEL_DEFS[mname]
    sub = sig[sig["date"] >= md["start"]]
    if md["mask"]:
        sub = sub[sub[md["mask"]]]
    else:
        sub = sub[sub[md["score"]].notna()]
    sub = sub[sub["amt20"] >= V.get("min_amt20", CFG["min_amt20"])]
    if V.get("phase_min_q") is not None:
        sub = sub[sub["amt_q"] >= V["phase_min_q"]]
    D = {}
    cap = V.get("ind_cap")
    for dt_, gg in sub.groupby("date"):
        topn = V.get("wfa_topn", md.get("topn"))
        if topn:
            gg = gg.nlargest(topn, md["score"])
        else:
            gg = gg.sort_values(md["score"], ascending=False)
        if cap:
            cnt = {}
            keep = []
            for code, sc in zip(gg["code"], gg[md["score"]].astype(float)):
                l1v = l1_of.get(code)
                c = cnt.get(l1v, 0)
                if c < cap:
                    keep.append((code, sc))
                    cnt[l1v] = c + 1
            D[int(dt_)] = keep
        else:
            D[int(dt_)] = list(zip(gg["code"], gg[md["score"]].astype(float)))
    return D

# ================= 3. 行情矩阵 =================
dates_all = np.array(sorted(sig["date"].unique()), dtype=np.int64)
didx = {int(d): i for i, d in enumerate(dates_all)}
codes = np.array(sorted(sig["code"].unique()))
cidx = {c: i for i, c in enumerate(codes)}
nd, nc = len(dates_all), len(codes)
log(f"回测网格: {nd}交易日 × {nc}标的")

log("构建行情矩阵...")
con = sqlite3.connect(f"file:{DB_QFQ}?mode=ro", uri=True)
con.execute(f"ATTACH DATABASE 'file:{DB_RAW}?mode=ro' AS rr")
px = pd.read_sql_query(
    """SELECT m.code, m.date, m.open, m.high, m.low, m.close, d.open AS o_raw, d.close AS c_raw
       FROM stock_daily_meta_qfq m
       LEFT JOIN rr.stock_daily_raw d ON d.code=m.code AND d.date=m.date
       WHERE m.date>=? AND m.date<=?""", con,
    params=(str(CFG["warmup"]), str(CFG["end"])))
con.close()
px["code"] = px["code"].astype(str)
px["date"] = px["date"].astype(np.int64)
px = px[px["code"].isin(cidx) & px["date"].isin(didx)]
px["ri"] = px["date"].map(didx).to_numpy(); px["ci"] = px["code"].map(cidx).to_numpy()
ri = px["ri"].to_numpy(); cxi = px["ci"].to_numpy()

def fill_matrix(vals):
    M = np.full((nd, nc), np.nan, dtype=np.float32)
    M[ri, cxi] = vals.astype(np.float32)
    return M

M_o = fill_matrix(px["open"].to_numpy())
M_c = fill_matrix(px["close"].to_numpy())
M_oraw = fill_matrix(px["o_raw"].to_numpy())
M_craw = fill_matrix(px["c_raw"].to_numpy())
M_craw_prev = np.full((nd, nc), np.nan, dtype=np.float32)
M_craw_prev[1:] = M_craw[:-1]
# round2变体预计算: ATR14 / MA20
log("预计算ATR14/MA20...")
M_h = fill_matrix(px["high"].to_numpy())
M_l = fill_matrix(px["low"].to_numpy())
_pc = np.full((nd, nc), np.nan, dtype=np.float32); _pc[1:] = M_c[:-1]
_tr = np.maximum.reduce([M_h - M_l, np.abs(M_h - _pc), np.abs(M_l - _pc)])
M_atr = pd.DataFrame(_tr).rolling(14, min_periods=14).mean().to_numpy(dtype=np.float32)
M_ma20 = pd.DataFrame(M_c).rolling(20, min_periods=20).mean().to_numpy(dtype=np.float32)
del _tr, _pc
del px; gc.collect()
log(f"矩阵完成 {M_o.shape}")

gem_mask = np.zeros(nc, dtype=bool)
for c_, i_ in cidx.items():
    if c_.startswith(("300", "301")): gem_mask[i_] = True
ratio = np.full((nd, nc), 0.10, dtype=np.float32)
ratio[:, gem_mask] = np.where(dates_all[:, None] >= CFG["gem_20cm_from"], 0.20, 0.10).astype(np.float32)

ci2 = sqlite3.connect(f"file:{DB_RAW}?mode=ro", uri=True)
idx = pd.read_sql_query("SELECT date,close FROM index_daily WHERE index_code='000001' AND date>=? AND date<=?",
                        ci2, params=(str(CFG["warmup"]), str(CFG["end"]))); ci2.close()
idx["date"] = idx["date"].astype(np.int64)
idx = idx.drop_duplicates("date").set_index("date")["close"]
bench_idx = idx.reindex(dates_all).ffill().bfill().to_numpy(dtype=np.float64)
with np.errstate(invalid="ignore"):
    eqw_ret = np.nanmean(M_c[1:] / M_c[:-1] - 1.0, axis=1)
bench_eqw = np.concatenate([[1.0], np.cumprod(1.0 + np.nan_to_num(eqw_ret))])

# ================= 4. 组合仿真 =================
def stamp_rate(dt_):
    return CFG["stamp_after_20230828"] if dt_ >= 20230828 else CFG["stamp_normal"]

def run_portfolio(mname, D, V=None):
    V = V or {}
    log(f"=== 仿真 {mname} (variant keys: " + ",".join(k for k in ("stop_buffer_atr","tp_dd2","ma20_exit") if V.get(k)) + ") ===")
    cash = CFG["init_cap"]
    positions = {}   # code -> dict(cost(qfq), shares, peak, entry_i, bought_i, buy_px_raw, buy_fee, buy_reason, defer)
    trades, equity_rows = [], []
    eq_peak = CFG["init_cap"]; pending = None; cb_flat_until = -1
    plan = {}
    for i in range(nd):
        dt_ = int(dates_all[i])
        up_lim = np.round(M_craw_prev[i] * (1 + ratio[i]), 2)
        dn_lim = np.round(M_craw_prev[i] * (1 - ratio[i]), 2)
        o_q, c_q, o_raw = M_o[i], M_c[i], M_oraw[i]

        def eq_now():
            s = cash
            for c_ in positions:
                p_ = o_q[cidx[c_]]
                s += positions[c_]["shares"] * (p_ if p_ == p_ else positions[c_]["cost"])
            return s

        # ---- A. circuit-breaker exec (triggered at prev close, executed at this open; HWM reset at exec) ----
        if pending:
            target_gross = 0.0 if pending == "flat" else CFG["cb_reduce_target"] * eq_now()
            gross = 0.0
            for c_ in positions:
                p_ = o_q[cidx[c_]]
                gross += positions[c_]["shares"] * (p_ if p_ == p_ else positions[c_]["cost"])
            while gross > target_gross and positions:
                c_worst = min(positions, key=lambda c_: (o_q[cidx[c_]] / positions[c_]["cost"] - 1)
                              if o_q[cidx[c_]] == o_q[cidx[c_]] else 0.0)
                ci_ = cidx[c_worst]; op = float(o_q[ci_])
                _ow = o_raw[ci_]
                if not (op == op) or (dn_lim[ci_] == dn_lim[ci_] and _ow == _ow and _ow <= dn_lim[ci_]):
                    break
                pxsell = op * (1 - CFG["slippage"])
                p = positions.pop(c_worst)
                gross_amt = p["shares"] * pxsell
                fee = max(gross_amt * CFG["commission"], CFG["commission_min"]) + gross_amt * stamp_rate(dt_)
                cash += gross_amt - fee; gross -= gross_amt
                _pnl = (pxsell - p["cost"]) * p["shares"] - fee - p["buy_fee"]
                trades.append(dict(code=c_worst, name=name_map.get(c_worst, c_worst),
                                   buy_date=int(dates_all[p["entry_i"]]), buy_px=float(p["buy_px_raw"]),
                                   sell_date=dt_, sell_px=float(pxsell),
                                   pnl=float(_pnl), pnl_pct=float(_pnl / (p["shares"] * p["cost"])),
                                   hold_days=int(i - p["entry_i"]),
                                   reason=("熔断清仓(空仓3日)" if pending == "flat" else "熔断减仓至30%"),
                                   buy_reason=p["buy_reason"]))
            eq_peak = eq_now()
            _residual = sum(positions[c_]["shares"] * (o_q[cidx[c_]] if o_q[cidx[c_]] == o_q[cidx[c_]] else positions[c_]["cost"])
                            for c_ in positions)
            if _residual <= target_gross + 1.0:
                if pending == "flat":
                    cb_flat_until = i + CFG["cb_flat_days"]
                pending = None
            # else: 跌停阻断未达目标 → 保持pending, 次晨继续执行(HWM已按当日复位)

        # ---- B. open sells (prev-close plan; limit-down defer via RAW open vs RAW limit) ----
        for c_, reason in list(plan.items()):
            if c_ not in positions: continue
            ci_ = cidx[c_]; op = float(o_q[ci_])
            if not (op == op): continue
            op_raw = o_raw[ci_]
            if dn_lim[ci_] == dn_lim[ci_] and op_raw == op_raw and op_raw <= dn_lim[ci_]:
                positions[c_]["defer"] = positions[c_].get("defer", 0) + 1
                if positions[c_]["defer"] <= 10:
                    continue
                cl = float(c_q[ci_])
                pxsell = (cl if cl == cl else positions[c_]["cost"]) * (1 - CFG["slippage"])
                p = positions.pop(c_)
                gross_amt = p["shares"] * pxsell
                fee = max(gross_amt * CFG["commission"], CFG["commission_min"]) + gross_amt * stamp_rate(dt_)
                cash += gross_amt - fee
                _pnl = (pxsell - p["cost"]) * p["shares"] - fee - p["buy_fee"]
                trades.append(dict(code=c_, name=name_map.get(c_, c_), buy_date=int(dates_all[p["entry_i"]]),
                                   buy_px=float(p["buy_px_raw"]), sell_date=dt_,
                                   sell_px=float(pxsell), pnl=float(_pnl),
                                   pnl_pct=float(_pnl / (p["shares"] * p["cost"])),
                                   hold_days=int(i - p["entry_i"]),
                                   reason=reason + "|LIMITDOWN_FORCE", buy_reason=p["buy_reason"]))
                log(f"  [WARN] {c_} {name_map.get(c_, '')} limitdown force-close approx")
                continue
            pxsell = op * (1 - CFG["slippage"])
            p = positions.pop(c_)
            gross_amt = p["shares"] * pxsell
            fee = max(gross_amt * CFG["commission"], CFG["commission_min"]) + gross_amt * stamp_rate(dt_)
            cash += gross_amt - fee
            _pnl = (pxsell - p["cost"]) * p["shares"] - fee - p["buy_fee"]
            trades.append(dict(code=c_, name=name_map.get(c_, c_), buy_date=int(dates_all[p["entry_i"]]),
                               buy_px=float(p["buy_px_raw"]), sell_date=dt_, sell_px=float(pxsell),
                               pnl=float(_pnl), pnl_pct=float(_pnl / (p["shares"] * p["cost"])),
                               hold_days=int(i - p["entry_i"]), reason=reason, buy_reason=p["buy_reason"]))
        plan = {}

        # ---- D. 开盘买入(T-1收盘信号, next-bar) ----
        if i < nd - 1 and i > cb_flat_until and pending is None and i > 0:  # 方案A末日禁新开仓(T+1,期末强平禁同日平,20261023一百八十四裁决)
            eq_est = eq_now()
            for c_, score in D.get(int(dates_all[i - 1]), []):
                if len(positions) >= CFG["max_positions"]: break
                if c_ in positions: continue            # R13 持有不重复买
                sec_ = l1_of.get(c_)
                if sec_ is None: continue
                ci_ = cidx[c_]; op = float(o_q[ci_])
                if not (op == op) or not (o_raw[ci_] == o_raw[ci_]): continue
                if up_lim[ci_] == up_lim[ci_] and o_raw[ci_] >= up_lim[ci_]:
                    continue                              # 开盘涨停不可买
                _ex = V.get("gate_exempt_ind")
                if not _ex:
                    n_sec = sum(1 for c2 in positions if l1_of.get(c2) == sec_ and positions[c2]["bought_i"] == i)
                    if n_sec >= CFG["r30a_same_day_sector_cap"]: continue   # R30a
                alloc = eq_est * CFG["pos_weight"]
                shares = int(alloc / op / 100) * 100
                if shares <= 0: continue
                if not _ex:
                    cl_val = sum(positions[c2]["shares"] * (o_q[cidx[c2]] if o_q[cidx[c2]] == o_q[cidx[c2]] else positions[c2]["cost"])
                                 for c2 in positions if l1_of.get(c2) == sec_)
                    if (cl_val + shares * op) / eq_est > V.get("r30b_cluster_cap", CFG["r30b_cluster_cap"]): continue  # R30b
                fee = max(shares * op * CFG["commission"], CFG["commission_min"])
                if shares * op + fee > cash:
                    shares = int((cash - fee) / op / 100) * 100
                    if shares <= 0: continue
                    fee = max(shares * op * CFG["commission"], CFG["commission_min"])
                cash -= shares * op + fee
                positions[c_] = dict(cost=op, shares=shares, peak=op, entry_i=i, bought_i=i,
                                     buy_px_raw=float(o_raw[ci_]), buy_fee=fee,
                                     buy_reason=f"{mname}信号(得分{float(score):.4f})", defer=0)
        # ---- E. 收盘盯市 + 退出评估(生成明日plan) ----
        eq_close = cash
        new_plan = {}
        for c_, p in positions.items():
            ci_ = cidx[c_]; cl = float(c_q[ci_])
            if cl == cl:
                eq_close += p["shares"] * cl
                if cl > p["peak"]: p["peak"] = cl
                if V.get("ma20_exit"):
                    _m20 = M_ma20[i, ci_]
                    if _m20 == _m20 and cl < _m20:
                        p["ma20s"] = p.get("ma20s", 0) + 1
                    else:
                        p["ma20s"] = 0
                if i > p["entry_i"]:   # T+1新仓当日不止损(第9章)
                    stop_px = max(p["cost"], p["peak"]) * CFG["stop_ratio"]
                    if V.get("stop_buffer_atr"):
                        _a = M_atr[i, ci_]
                        if _a == _a:
                            stop_px = max(p["cost"] * CFG["stop_floor"],
                                          stop_px - V["stop_buffer_atr"] * float(_a))
                    gain_peak = p["peak"] / p["cost"] - 1
                    dd_pk = (p["peak"] - cl) / p["peak"]
                    if cl <= stop_px:
                        new_plan[c_] = "止损(移动0.92" + ("+ATR缓冲" if V.get("stop_buffer_atr") else "") + ")"
                    elif V.get("ma20_exit") and p.get("ma20s", 0) >= 3:
                        new_plan[c_] = "趋势走坏(MA20×3)"
                    elif gain_peak > CFG["tp_band2"] and dd_pk >= V.get("tp_dd2", CFG["tp_dd2"]):
                        new_plan[c_] = (f"止盈(峰值盈利{gain_peak*100:.0f}%>30%档 "
                                        f"回撤{dd_pk*100:.1f}%≥{V.get('tp_dd2', CFG['tp_dd2'])*100:.0f}%)")
                    elif gain_peak >= CFG["tp_band1"] and dd_pk >= CFG["tp_dd1"]:
                        new_plan[c_] = f"止盈(峰值盈利{gain_peak*100:.0f}% 10-30%档 回撤{dd_pk*100:.1f}%≥10%)"
            else:
                eq_close += p["shares"] * p["cost"]
        plan = new_plan
        dd_close = 1 - eq_close / eq_peak if eq_peak > 0 else 0.0
        pending = ("flat" if dd_close > V.get("cb_dd_flat", CFG["cb_dd_flat"])
                   else ("reduce" if dd_close > V.get("cb_dd_reduce", CFG["cb_dd_reduce"]) else None))
        equity_rows.append((dt_, eq_close, amtq_map.get(dt_)))
        eq_peak = max(eq_peak, eq_close)
    # 期末强平
    last_dt = int(dates_all[-1])
    for c_, p in list(positions.items()):
        cl = float(M_c[-1, cidx[c_]])
        cl = cl if cl == cl else p["cost"]
        pxsell = cl * (1 - CFG["slippage"])
        gross_amt = p["shares"] * pxsell
        fee = max(gross_amt * CFG["commission"], CFG["commission_min"]) + gross_amt * stamp_rate(last_dt)
        cash += gross_amt - fee
        _pnl = (pxsell - p["cost"]) * p["shares"] - fee - p["buy_fee"]
        trades.append(dict(code=c_, name=name_map.get(c_, c_), buy_date=int(dates_all[p["entry_i"]]),
                           buy_px=float(p["buy_px_raw"]), sell_date=last_dt, sell_px=float(pxsell),
                           pnl=float(_pnl), pnl_pct=float(_pnl / (p["shares"] * p["cost"])),
                           hold_days=int(nd - 1 - p["entry_i"]), reason="期末强平", buy_reason=p["buy_reason"]))
        positions.pop(c_)
    eq = pd.DataFrame(equity_rows, columns=["date", "equity", "amt_q"])
    tr = pd.DataFrame(trades)
    # 账目守恒断言(精确): 初始资金 + Σ单笔pnl == 期末现金(全部持仓已清算)
    assert abs(cash - (CFG["init_cap"] + (tr["pnl"].sum() if len(tr) else 0.0))) < 0.01, \
        f"{mname} 账目不守恒: cash={cash} init+pnl={CFG['init_cap'] + tr['pnl'].sum()}"
    log(f"账目守恒断言 PASS ({mname})")
    return eq, tr

# ================= 5. 运行(基线) =================
RESULTS = {}
for mname in MODEL_DEFS:
    D = build_daily(mname, CFG)
    eq, tr = run_portfolio(mname, D, CFG)
    eq.to_csv(os.path.join(REPORT_DIR, f"equity_{mname}.csv"), index=False)
    tr.to_csv(os.path.join(REPORT_DIR, f"trades_{mname}.csv"), index=False, encoding="utf-8-sig")
    RESULTS[mname] = dict(eq=eq, tr=tr)
    log(f"{mname}: 期末净值={eq['equity'].iloc[-1]:,.0f} 总收益={(eq['equity'].iloc[-1]/CFG['init_cap']-1)*100:+.1f}% 交易={len(tr)}笔")

# ================= 6. 汇总 =================
def metrics_of(eq, tr):
    e = eq["equity"].to_numpy(dtype=np.float64); d = eq["date"].to_numpy(dtype=np.int64)
    ret = e[1:] / e[:-1] - 1
    yrs = len(e) / 243.0
    cagr = (e[-1] / e[0]) ** (1 / max(yrs, 1e-9)) - 1
    vol = ret.std() * np.sqrt(243)
    peak = np.maximum.accumulate(e); dd = e / peak - 1
    imin = int(dd.argmin()); peak_i = int(e[:imin + 1].argmax())
    m = dict(final_equity=float(e[-1]), total_return=float(e[-1] / e[0] - 1), cagr=float(cagr),
             ann_vol=float(vol), sharpe=float((ret.mean() * 243) / vol) if vol > 0 else None,
             mdd=float(dd.min()), mdd_peak_date=int(d[peak_i]), mdd_trough_date=int(d[imin]))
    if len(tr):
        gp = tr.loc[tr["pnl"] > 0, "pnl"].sum(); gl = -tr.loc[tr["pnl"] < 0, "pnl"].sum()
        m.update(n_trades=int(len(tr)), win_rate=float((tr["pnl_pct"] > 0).mean()),
                 profit_factor=float(gp / gl) if gl > 0 else None,
                 avg_hold=float(tr["hold_days"].mean()),
                 avg_win=float(tr.loc[tr["pnl_pct"] > 0, "pnl_pct"].mean()) if (tr["pnl_pct"] > 0).any() else None,
                 avg_loss=float(tr.loc[tr["pnl_pct"] <= 0, "pnl_pct"].mean()) if (tr["pnl_pct"] <= 0).any() else None)
    else:
        m.update(n_trades=0, win_rate=None, profit_factor=None, avg_hold=None, avg_win=None, avg_loss=None)
    return m

def yearly_of(eq):
    e = eq.copy(); e["y"] = (e["date"] // 10000).astype(int)
    out = []; prev = None
    for y, s in e.groupby("y")["equity"]:
        e0 = s.iloc[0] if prev is None else prev
        out.append(dict(year=int(y), ret=float(s.iloc[-1] / e0 - 1)))
        prev = s.iloc[-1]
    return out

summary = dict(cfg={k: v for k, v in CFG.items()}, models={}, benchmark={},
               meta=dict(run_date="2026-10-22", engine="backtest_fullhist_multimodel_20261022.py v4",
                         smoke=bool(ARGS.smoke)))

# ================= 5b. round2 变体矩阵(单次加载复用) =================
def acceptance_stats(tr):
    if len(tr) == 0:
        return dict(stop_avg=None, tp_n=0, cb_n=0, bigwin_share=None, trend_n=0, n=0)
    st = tr["reason"].str.startswith("止损")
    return dict(n=int(len(tr)),
                stop_avg=float(tr.loc[st, "pnl_pct"].mean()) if st.any() else None,
                tp_n=int(tr["reason"].str.startswith("止盈").sum()),
                cb_n=int(tr["reason"].str.startswith("熔断").sum()),
                bigwin_share=float((tr["pnl_pct"] > 0.30).mean()),
                trend_n=int(tr["reason"].str.startswith("趋势走坏").sum()))

if ARGS.round2 or ARGS.round3:
    _all = R3_VARIANTS if ARGS.round3 else VARIANTS
    _vars = _all if not ARGS.smoke else [dict(_all[0], models=["M1_lowconsol_main"])]
    R2 = {}
    for vd in _vars:
        tag = vd["tag"]
        V = dict(CFG); V.update(vd.get("overrides", {}))
        for mname in vd["models"]:
            D = build_daily(mname, V)
            n_sig = sum(len(v) for v in D.values())
            eq, tr = run_portfolio(mname, D, V)
            eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}__{mname}.csv"), index=False)
            tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}__{mname}.csv"), index=False, encoding="utf-8-sig")
            R2[f"{tag}__{mname}"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr),
                                         note=MODEL_DEFS[mname]["note"],
                                         variant=vd.get("overrides", {}), n_signals=n_sig)
            log(f"[{tag}] {mname}: 终值={eq['equity'].iloc[-1]:,.0f} CAGR={metrics_of(eq, tr)['cagr']*100:+.1f}% "
                f"信号={n_sig:,} 交易={len(tr)}")
    _sfn = "summary_round3.json" if ARGS.round3 else "summary_round2.json"
    with open(os.path.join(REPORT_DIR, _sfn), "w", encoding="utf-8") as f:
        json.dump(R2, f, ensure_ascii=False, indent=1, default=float)
    log(("ROUND3" if ARGS.round3 else "ROUND2") + " DONE")
    LOG.close(); sys.exit(0)

# ================= 5c. round4: 统一账户撮合 + 变体 =================
R4_VARIANTS = [
    dict(tag="R4m6_ind4", overrides=dict(min_amt20=20e6, wfa_topn=50, ind_cap=4), models=["M6_wfa_top20"]),
    dict(tag="R4m1_cb1525", overrides=dict(phase_min_q=0.5, min_amt20=20e6, stop_buffer_atr=1.5,
                                           cb_dd_reduce=0.15, cb_dd_flat=0.25), models=["M1_lowconsol_main"]),
    dict(tag="R4m1_cb2025", overrides=dict(phase_min_q=0.5, min_amt20=20e6, stop_buffer_atr=1.5,
                                           cb_dd_reduce=0.20, cb_dd_flat=0.25), models=["M1_lowconsol_main"]),
    dict(tag="R4m1_cb2535", overrides=dict(phase_min_q=0.5, min_amt20=20e6, stop_buffer_atr=1.5,
                                           cb_dd_reduce=0.25, cb_dd_flat=0.35), models=["M1_lowconsol_main"]),
]

def run_unified(legs, tag):
    """统一账户撮合: 共享现金/共享50槽/账户级熔断; legs=[(name,D,V,weight,n_slots,rule_exit)]
    BP腿(rule=False): 仅月末轮换退出(不在新篮子即卖), 无止损止盈; rule腿: 止损/止盈/熔断全套。
    槽位: 每腿 n_slots 上限(20/20/10), 全局由加和≤50。"""
    log(f"=== 统一账户 {tag}: {[(l[0], l[3]) for l in legs]} ===")
    cash = CFG["init_cap"]
    positions = {}   # code -> leg,cost,shares,peak,entry_i,buy_px_raw,buy_fee,defer
    trades, equity_rows = [], []
    eq_peak = CFG["init_cap"]; pending = None; cb_flat_until = -1; plan = {}
    W = {l[0]: l[3] for l in legs}; NS = {l[0]: l[4] for l in legs}
    VL = {l[0]: l[2] for l in legs}; RULE = {l[0]: l[5] for l in legs}

    # round13/14 环境闸: 任一腿开启 env_ma200_* 时预计算等权宇宙日度 MA200 熊态序列
    _bear_regime = None
    if any(l[2].get(k) for l in legs for k in ("env_ma200_half", "env_ma200_month_end", "env_ma200_off")):
        _ew_c = np.nanmean(M_c, axis=1)          # 等权宇宙收盘(逐日, 忽略NaN)
        _ew_s = pd.Series(_ew_c).rolling(200, min_periods=200).mean().to_numpy()
        _bear_regime = [bool(_ew_c[j] == _ew_c[j] and _ew_s[j] == _ew_s[j] and _ew_c[j] < _ew_s[j])
                        for j in range(nd)]

    def mtm_leg(lname):
        s = 0.0
        for c_, p in positions.items():
            if p["leg"] != lname: continue
            p_ = M_o[i, cidx[c_]]
            s += p["shares"] * (p_ if p_ == p_ else p["cost"])
        return s

    for i in range(nd):
        dt_ = int(dates_all[i])
        up_lim = np.round(M_craw_prev[i] * (1 + ratio[i]), 2)
        dn_lim = np.round(M_craw_prev[i] * (1 - ratio[i]), 2)
        o_q, c_q, o_raw = M_o[i], M_c[i], M_oraw[i]

        def eq_now():
            s = cash
            for c_ in positions:
                p_ = o_q[cidx[c_]]
                s += positions[c_]["shares"] * (p_ if p_ == p_ else positions[c_]["cost"])
            return s

        # ---- A. 账户级熔断执行 ----
        if pending:
            target_gross = 0.0 if pending == "flat" else CFG["cb_reduce_target"] * eq_now()
            gross = sum(p["shares"] * (o_q[cidx[c_]] if o_q[cidx[c_]] == o_q[cidx[c_]] else p["cost"])
                        for c_, p in positions.items())
            while gross > target_gross and positions:
                c_worst = min(positions, key=lambda c_: (o_q[cidx[c_]] / positions[c_]["cost"] - 1)
                              if o_q[cidx[c_]] == o_q[cidx[c_]] else 0.0)
                ci_ = cidx[c_worst]; op = float(o_q[ci_]); _ow = o_raw[ci_]
                if not (op == op) or (dn_lim[ci_] == dn_lim[ci_] and _ow == _ow and _ow <= dn_lim[ci_]):
                    break
                pxsell = op * (1 - CFG["slippage"])
                p = positions.pop(c_worst)
                gross_amt = p["shares"] * pxsell
                fee = max(gross_amt * CFG["commission"], CFG["commission_min"]) + gross_amt * stamp_rate(dt_)
                cash += gross_amt - fee; gross -= gross_amt
                _pnl = (pxsell - p["cost"]) * p["shares"] - fee - p["buy_fee"]
                trades.append(dict(code=c_worst, name=name_map.get(c_worst, c_worst), leg=p["leg"],
                                   buy_date=int(dates_all[p["entry_i"]]), buy_px=float(p["buy_px_raw"]),
                                   sell_date=dt_, sell_px=float(pxsell), pnl=float(_pnl),
                                   pnl_pct=float(_pnl / (p["shares"] * p["cost"])),
                                   hold_days=int(i - p["entry_i"]), reason="熔断" + ("清仓(空仓3日)" if pending == "flat" else "减仓至30%"),
                                   buy_reason=p["buy_reason"]))
            eq_peak = eq_now()
            _residual = sum(p["shares"] * (o_q[cidx[c_]] if o_q[cidx[c_]] == o_q[cidx[c_]] else p["cost"])
                            for c_, p in positions.items())
            if _residual <= target_gross + 1.0:
                if pending == "flat":
                    cb_flat_until = i + CFG["cb_flat_days"]
                pending = None

        # ---- A2. BP月度轮换(信号日次日开盘: 不在新篮子即卖) ----
        for lname, D, V, w, ns, rule in legs:
            if rule: continue
            picks = D.get(int(dates_all[i - 1])) if i > 0 else None
            if picks is None: continue
            new = set(c for c, _ in picks)
            for c_, p in list(positions.items()):
                if p["leg"] != lname or c_ in new: continue
                ci_ = cidx[c_]; op = float(o_q[ci_]); _ow = o_raw[ci_]
                if not (op == op): continue
                if dn_lim[ci_] == dn_lim[ci_] and _ow == _ow and _ow <= dn_lim[ci_]:
                    continue  # 跌停顺延(次日篮子已不在, 由止损/期末兜底; 如实罕见)
                pxsell = op * (1 - CFG["slippage"])
                p = positions.pop(c_)
                gross_amt = p["shares"] * pxsell
                fee = max(gross_amt * CFG["commission"], CFG["commission_min"]) + gross_amt * stamp_rate(dt_)
                cash += gross_amt - fee
                _pnl = (pxsell - p["cost"]) * p["shares"] - fee - p["buy_fee"]
                trades.append(dict(code=c_, name=name_map.get(c_, c_), leg=lname,
                                   buy_date=int(dates_all[p["entry_i"]]), buy_px=float(p["buy_px_raw"]),
                                   sell_date=dt_, sell_px=float(pxsell), pnl=float(_pnl),
                                   pnl_pct=float(_pnl / (p["shares"] * p["cost"])),
                                   hold_days=int(i - p["entry_i"]), reason="BP月度轮换", buy_reason=p["buy_reason"]))

        # ---- B. rule腿 止损/止盈plan 卖出 ----
        for c_, reason in list(plan.items()):
            if c_ not in positions: continue
            ci_ = cidx[c_]; op = float(o_q[ci_])
            if not (op == op): continue
            _ow = o_raw[ci_]
            if dn_lim[ci_] == dn_lim[ci_] and _ow == _ow and _ow <= dn_lim[ci_]:
                positions[c_]["defer"] = positions[c_].get("defer", 0) + 1
                continue
            pxsell = op * (1 - CFG["slippage"])
            p = positions.pop(c_)
            gross_amt = p["shares"] * pxsell
            fee = max(gross_amt * CFG["commission"], CFG["commission_min"]) + gross_amt * stamp_rate(dt_)
            cash += gross_amt - fee
            _pnl = (pxsell - p["cost"]) * p["shares"] - fee - p["buy_fee"]
            trades.append(dict(code=c_, name=name_map.get(c_, c_), leg=p["leg"],
                               buy_date=int(dates_all[p["entry_i"]]), buy_px=float(p["buy_px_raw"]),
                               sell_date=dt_, sell_px=float(pxsell), pnl=float(_pnl),
                               pnl_pct=float(_pnl / (p["shares"] * p["cost"])),
                               hold_days=int(i - p["entry_i"]), reason=reason, buy_reason=p["buy_reason"]))
        plan = {}

        # ---- D. 买入(共享现金+槽位, 按腿预算) ----
        if i < nd - 1 and i > cb_flat_until and pending is None and i > 0:  # 方案A末日禁新开仓(T+1,期末强平禁同日平,20261023一百八十四裁决)
            _bear_prev = bool(_bear_regime is not None and _bear_regime[i - 1])
            for lname, D, V, w, ns, rule in legs:
                if _bear_prev and V.get("env_ma200_off"):
                    continue                     # 熊态禁开该腿(round14动态权重)
                _f = 1.0
                if _bear_prev and (V.get("env_ma200_half")
                                   or (V.get("env_ma200_month_end") and int(dates_all[i - 1]) in D)):
                    _f = 0.5                     # 熊态半仓: 常态half / 仅月末再平衡日half(round14)
                eq_est = eq_now()
                budget = (W[lname] * eq_est - mtm_leg(lname)) * _f
                n_leg = sum(1 for p in positions.values() if p["leg"] == lname)
                for c_, score in D.get(int(dates_all[i - 1]), []):
                    if c_ in positions: continue
                    if n_leg >= ns or len(positions) >= 50: break
                    sec_ = l1_of.get(c_)
                    if sec_ is None: continue
                    ci_ = cidx[c_]; op = float(o_q[ci_])
                    if not (op == op) or not (o_raw[ci_] == o_raw[ci_]): continue
                    if up_lim[ci_] == up_lim[ci_] and o_raw[ci_] >= up_lim[ci_]: continue
                    if V.get("r30a_on"):
                        _today = [c2 for c2, p2 in positions.items() if p2.get("bought_i") == i]
                        _same = sum(1 for c2 in _today if l1_of.get(c2) == sec_)
                        _capa = V.get("r30a_same_day_sector_cap", CFG["r30a_same_day_sector_cap"])
                        if _same >= _capa or (len(_today) >= 4 and _same / max(len(_today), 1) > 0.25):
                            continue  # R30a 同日同行业闸(双约束)
                    alloc = budget / ns if not rule else budget * CFG["pos_weight"]
                    shares = int(alloc / op / 100) * 100
                    if shares <= 0: continue
                    if not V.get("gate_exempt_ind"):
                        cl_val = sum(p["shares"] * (o_q[cidx[c2]] if o_q[cidx[c2]] == o_q[cidx[c2]] else p["cost"])
                                     for c2, p in positions.items() if l1_of.get(c2) == sec_)
                        if (cl_val + shares * op) / eq_est > V.get("r30b_cluster_cap", CFG["r30b_cluster_cap"]): continue
                    fee = max(shares * op * CFG["commission"], CFG["commission_min"])
                    if shares * op + fee > cash:
                        shares = int((cash - fee) / op / 100) * 100
                        if shares <= 0: continue
                        fee = max(shares * op * CFG["commission"], CFG["commission_min"])
                    cash -= shares * op + fee
                    positions[c_] = dict(leg=lname, cost=op, shares=shares, peak=op, entry_i=i, bought_i=i,
                                         buy_px_raw=float(o_raw[ci_]), buy_fee=fee, defer=0,
                                         buy_reason=f"{lname}信号({float(score):.4f})")
                    n_leg += 1

        # ---- E. 收盘盯市 + rule腿退出评估 + 熔断触发 ----
        eq_close = cash
        new_plan = {}
        for c_, p in positions.items():
            ci_ = cidx[c_]; cl = float(c_q[ci_])
            if cl == cl:
                eq_close += p["shares"] * cl
                if cl > p["peak"]: p["peak"] = cl
                if RULE[p["leg"]] and i > p["entry_i"]:
                    stop_px = max(p["cost"], p["peak"]) * CFG["stop_ratio"]
                    if VL[p["leg"]].get("stop_buffer_atr"):
                        _a = M_atr[i, ci_]
                        if _a == _a:
                            stop_px = max(p["cost"] * CFG["stop_floor"],
                                          stop_px - VL[p["leg"]]["stop_buffer_atr"] * float(_a))
                gain_peak = p["peak"] / p["cost"] - 1
                dd_pk = (p["peak"] - cl) / p["peak"]
                if RULE[p["leg"]] and i > p["entry_i"]:
                    if cl <= stop_px:
                        new_plan[c_] = "止损(移动0.92+ATR缓冲)" if VL[p["leg"]].get("stop_buffer_atr") else "止损(移动0.92)"
                    elif gain_peak > CFG["tp_band2"] and dd_pk >= VL[p["leg"]].get("tp_dd2", CFG["tp_dd2"]):
                        new_plan[c_] = "止盈(30%档)"
                    elif gain_peak >= CFG["tp_band1"] and dd_pk >= CFG["tp_dd1"]:
                        new_plan[c_] = "止盈(10-30%档)"
                elif i > p["entry_i"]:
                    # round13/15 BP腿变体退出(仅变体开启时生效, 基线行为不变)
                    _bp_sl = VL[p["leg"]].get("bp_stop_loss_pct", 0.0)
                    _bp_mh = VL[p["leg"]].get("bp_max_hold_days", 0)
                    _bp_tp = VL[p["leg"]].get("bp_take_profit_pct", 0.0)
                    if _bp_tp and cl / p["cost"] - 1 >= _bp_tp:
                        new_plan[c_] = f"BP止盈({_bp_tp:+.0%})"
                    elif _bp_sl and cl / p["cost"] - 1 <= -_bp_sl:
                        new_plan[c_] = f"BP止损({-_bp_sl:+.0%})"
                    elif _bp_mh and (i - p["entry_i"]) >= _bp_mh:
                        new_plan[c_] = "BP持仓超限重评"
            else:
                eq_close += p["shares"] * p["cost"]
        plan = new_plan
        dd_close = 1 - eq_close / eq_peak if eq_peak > 0 else 0.0
        pending = ("flat" if dd_close > CFG["cb_dd_flat"]
                   else ("reduce" if dd_close > CFG["cb_dd_reduce"] else None))
        equity_rows.append((dt_, eq_close))
        eq_peak = max(eq_peak, eq_close)
    last_dt = int(dates_all[-1])
    for c_, p in list(positions.items()):
        cl = float(M_c[-1, cidx[c_]]); cl = cl if cl == cl else p["cost"]
        pxsell = cl * (1 - CFG["slippage"])
        gross_amt = p["shares"] * pxsell
        fee = max(gross_amt * CFG["commission"], CFG["commission_min"]) + gross_amt * stamp_rate(last_dt)
        cash += gross_amt - fee
        _pnl = (pxsell - p["cost"]) * p["shares"] - fee - p["buy_fee"]
        trades.append(dict(code=c_, name=name_map.get(c_, c_), leg=p["leg"],
                           buy_date=int(dates_all[p["entry_i"]]), buy_px=float(p["buy_px_raw"]),
                           sell_date=last_dt, sell_px=float(pxsell), pnl=float(_pnl),
                           pnl_pct=float(_pnl / (p["shares"] * p["cost"])),
                           hold_days=int(nd - 1 - p["entry_i"]), reason="期末强平", buy_reason=p["buy_reason"]))
        positions.pop(c_)
    eq = pd.DataFrame(equity_rows, columns=["date", "equity"])
    tr = pd.DataFrame(trades)
    assert abs(cash - (CFG["init_cap"] + (tr["pnl"].sum() if len(tr) else 0.0))) < 0.01, f"{tag} 账目不守恒"
    log(f"账目守恒断言 PASS ({tag})")
    return eq, tr

if ARGS.round4:
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json — 先运行 bt_bp_ep_r2_2016_100k.py(非--qfq)")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    V6 = dict(CFG); V6.update(min_amt20=20e6, wfa_topn=50)
    V1 = dict(CFG); V1.update(phase_min_q=0.5, min_amt20=20e6, stop_buffer_atr=1.5)
    legs = [("BP", D_BP, dict(CFG), 0.6, 20, False),
            ("M6", build_daily(M6 := "M6_wfa_top20", V6), V6, 0.2, 20, True),
            ("M1", build_daily(M1N := "M1_lowconsol_main", V1), V1, 0.2, 10, True)]
    R4 = {}
    for tag, ls in (("U602020", legs), ("U100BP", [legs[0]])):
        eq, tr = run_unified(ls, tag)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}.csv"), index=False, encoding="utf-8-sig")
        R4[tag] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr))
        log(f"[{tag}] 终值={eq['equity'].iloc[-1]:,.0f} CAGR={R4[tag]['metrics']['cagr']*100:+.1f}% 交易={len(tr)}")
    # ④ 月度再平衡链(与统一账户同三腿, 用统一引擎BP腿+R3双腿)
    navs = {}
    for name, f in (("BP", "equity_U100BP.csv"), ("M6", "equity_R3m6_l_top50__M6_wfa_top20.csv"),
                    ("M1", "equity_R3m1_pl_sa15__M1_lowconsol_main.csv")):
        e = pd.read_csv(os.path.join(REPORT_DIR, f))
        navs[name] = e.set_index("date")["equity"] / 1e5
    cal = sorted(set.intersection(*[set(v.index) for v in navs.values()]))
    ndf = pd.DataFrame({k: v.reindex(cal).ffill().fillna(1.0) for k, v in navs.items()})
    ndf["ym"] = [(d // 10000, d // 100 % 100) for d in ndf.index]
    nav, ws, chain = 1.0, {"BP": 0.6, "M6": 0.2, "M1": 0.2}, [(cal[0], 1.0)]
    for ym, seg in ndf.groupby("ym"):
        r = sum(ws[k] * (seg[k].iloc[-1] / seg[k].iloc[0] - 1) for k in ws)
        nav *= (1 + r)
        chain.append((int(seg.index[-1]), nav))
    chain_eq = pd.DataFrame(chain, columns=["date", "equity"])
    chain_eq.to_csv(os.path.join(REPORT_DIR, "equity_CHAIN602020.csv"), index=False)
    _yrs = (chain_eq["date"].iloc[-1] - chain_eq["date"].iloc[0]) / 10000.0
    _ce = chain_eq["equity"].to_numpy()
    R4["CHAIN602020"] = dict(metrics=dict(final_equity=float(_ce[-1] * 1e5), total_return=float(_ce[-1] - 1),
                                          cagr=float(_ce[-1] ** (1 / _yrs) - 1),
                                          mdd=float((_ce / np.maximum.accumulate(_ce) - 1).min())),
                              note="月度再平衡链口径(分月腿收益加权复利; 年化按日期跨度折算)")
    log(f"[CHAIN602020] 终值={chain_eq['equity'].iloc[-1]*1e5:,.0f} "
        f"CAGR={R4['CHAIN602020']['metrics']['cagr']*100:+.1f}%")
    # ⑤⑥ 变体
    for vd in R4_VARIANTS:
        V = dict(CFG); V.update(vd["overrides"])
        for mname in vd["models"]:
            D = build_daily(mname, V)
            eq, tr = run_portfolio(mname, D, V)
            eq.to_csv(os.path.join(REPORT_DIR, f"equity_{vd['tag']}__{mname}.csv"), index=False)
            tr.to_csv(os.path.join(REPORT_DIR, f"trades_{vd['tag']}__{mname}.csv"), index=False, encoding="utf-8-sig")
            _k4 = f"{vd['tag']}__{mname}"
            R4[_k4] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr),
                           variant=vd["overrides"])
            log(f"[{vd['tag']}] {mname}: CAGR={R4[_k4]['metrics']['cagr']*100:+.1f}% 交易={len(tr)}")
    with open(os.path.join(REPORT_DIR, "summary_round4.json"), "w", encoding="utf-8") as f:
        json.dump(R4, f, ensure_ascii=False, indent=1, default=float)
    log("ROUND4 DONE")
    LOG.close(); sys.exit(0)

# ================= 5d. round5: R30a/b豁免A/B(回测层取证, 不落生产闸门) =================
if ARGS.round5:
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    VBPf = dict(CFG); VBPf["gate_exempt_ind"] = True
    V6f = dict(CFG); V6f.update(min_amt20=20e6, wfa_topn=50, gate_exempt_ind=True)
    V1f = dict(CFG); V1f.update(phase_min_q=0.5, min_amt20=20e6, stop_buffer_atr=1.5, gate_exempt_ind=True)
    D6 = build_daily("M6_wfa_top20", V6f)
    D1 = build_daily("M1_lowconsol_main", V1f)
    R5 = {}

    def _u(tag, legs_):
        eq, tr = run_unified(legs_, tag)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}.csv"), index=False, encoding="utf-8-sig")
        R5[tag] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr))
        log(f"[{tag}] 终值={eq['equity'].iloc[-1]:,.0f} CAGR={R5[tag]['metrics']['cagr']*100:+.1f}% "
            f"MDD={R5[tag]['metrics']['mdd']*100:.1f}%")

    _u("R5U100BPfree", [("BP", D_BP, VBPf, 1.0, 20, False)])
    _u("R5U602020free", [("BP", D_BP, VBPf, 0.6, 20, False),
                         ("M6", D6, V6f, 0.2, 20, True), ("M1", D1, V1f, 0.2, 10, True)])
    _u("R5U503020free", [("BP", D_BP, VBPf, 0.5, 20, False),
                         ("M6", D6, V6f, 0.3, 20, True), ("M1", D1, V1f, 0.2, 10, True)])
    _u("R5U404020free", [("BP", D_BP, VBPf, 0.4, 20, False),
                         ("M6", D6, V6f, 0.4, 20, True), ("M1", D1, V1f, 0.2, 10, True)])
    # 豁免单腿(链式对照用)
    for mname, V, D in (("M6_wfa_top20", V6f, D6), ("M1_lowconsol_main", V1f, D1)):
        eq, tr = run_portfolio(mname, D, V)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_R5free__{mname}.csv"), index=False)
        R5[f"R5free__{mname}"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr))
        log(f"[R5free] {mname}: CAGR={R5[f'R5free__{mname}']['metrics']['cagr']*100:+.1f}%")
    # 链式(豁免口径)
    navs = {"BP": pd.read_csv(os.path.join(REPORT_DIR, "equity_R5U100BPfree.csv")).set_index("date")["equity"] / 1e5,
            "M6": pd.read_csv(os.path.join(REPORT_DIR, "equity_R5free__M6_wfa_top20.csv")).set_index("date")["equity"] / 1e5,
            "M1": pd.read_csv(os.path.join(REPORT_DIR, "equity_R5free__M1_lowconsol_main.csv")).set_index("date")["equity"] / 1e5}
    cal5 = sorted(set.intersection(*[set(v.index) for v in navs.values()]))
    ndf = pd.DataFrame({k: v.reindex(cal5).ffill().fillna(1.0) for k, v in navs.items()})
    ndf["ym"] = [(d // 10000, d // 100 % 100) for d in ndf.index]
    nav5, ws5, chain5 = 1.0, {"BP": 0.6, "M6": 0.2, "M1": 0.2}, [(cal5[0], 1.0)]
    for ym, seg in ndf.groupby("ym"):
        nav5 *= (1 + sum(ws5[k] * (seg[k].iloc[-1] / seg[k].iloc[0] - 1) for k in ws5))
        chain5.append((int(seg.index[-1]), nav5))
    chain_eq5 = pd.DataFrame(chain5, columns=["date", "equity"])
    chain_eq5.to_csv(os.path.join(REPORT_DIR, "equity_R5CHAIN602020free.csv"), index=False)
    _yrs5 = (chain_eq5["date"].iloc[-1] - chain_eq5["date"].iloc[0]) / 10000.0
    _e5 = chain_eq5["equity"].to_numpy()
    R5["R5CHAIN602020free"] = dict(metrics=dict(
        final_equity=float(_e5[-1] * 1e5), total_return=float(_e5[-1] - 1),
        cagr=float(_e5[-1] ** (1 / _yrs5) - 1),
        mdd=float((_e5 / np.maximum.accumulate(_e5) - 1).min()), win_rate=None),
        note="月度再平衡链(豁免口径, 年化按日期跨度)")
    # ⑤ M6域熔断扫描
    V6cb = dict(V6f); V6cb.update(cb_dd_reduce=0.15, cb_dd_flat=0.25)
    eq, tr = run_portfolio("M6_wfa_top20", build_daily("M6_wfa_top20", V6cb), V6cb)
    eq.to_csv(os.path.join(REPORT_DIR, "equity_R5m6_cb1525__M6_wfa_top20.csv"), index=False)
    R5["R5m6_cb1525__M6_wfa_top20"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr))
    with open(os.path.join(REPORT_DIR, "summary_round5.json"), "w", encoding="utf-8") as f:
        json.dump(R5, f, ensure_ascii=False, indent=1, default=float)
    log("ROUND5 DONE")
    LOG.close(); sys.exit(0)

# ================= 5e. round6: R30b剂量-反应 + M6域MA20组合 =================
if ARGS.round6:
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    R6 = {}
    # 剂量-反应: R30b帽 25(基线)→30→35→50→inf(等效豁免)
    for capv, tag in ((0.30, "R6cap30"), (0.35, "R6cap35"), (0.50, "R6cap50"), (9.9, "R6capinf")):
        Vc = dict(CFG); Vc["r30b_cluster_cap"] = capv
        eq, tr = run_unified([("BP", D_BP, Vc, 1.0, 20, False)], f"U100BP_{tag}")
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_U100BP_{tag}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_U100BP_{tag}.csv"), index=False, encoding="utf-8-sig")
        R6[f"U100BP_{tag}"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr),
                                   variant=dict(r30b_cluster_cap=capv))
        log(f"[{tag}] CAGR={R6[f'U100BP_{tag}']['metrics']['cagr']*100:+.1f}% "
            f"MDD={R6[f'U100BP_{tag}']['metrics']['mdd']*100:.1f}%")
    # M6域: liq2000+Top50 + MA20退出(域矩阵中M6唯一正收益未组合规则)
    V6m = dict(CFG); V6m.update(min_amt20=20e6, wfa_topn=50, ma20_exit=True)
    eq, tr = run_portfolio("M6_wfa_top20", build_daily("M6_wfa_top20", V6m), V6m)
    eq.to_csv(os.path.join(REPORT_DIR, "equity_R6m6_ma20__M6_wfa_top20.csv"), index=False)
    tr.to_csv(os.path.join(REPORT_DIR, "trades_R6m6_ma20__M6_wfa_top20.csv"), index=False, encoding="utf-8-sig")
    R6["R6m6_ma20__M6_wfa_top20"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr))
    log(f"[R6m6_ma20] CAGR={R6['R6m6_ma20__M6_wfa_top20']['metrics']['cagr']*100:+.1f}%")
    with open(os.path.join(REPORT_DIR, "summary_round6.json"), "w", encoding="utf-8") as f:
        json.dump(R6, f, ensure_ascii=False, indent=1, default=float)
    log("ROUND6 DONE")
    LOG.close(); sys.exit(0)

# ================= 5f. round7: R30a同日闸对篮子源摩擦A/B =================
if ARGS.round7:
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    R7 = {}
    Vb3 = dict(CFG); Vb3["gate_exempt_ind"] = True; Vb3["r30a_on"] = True
    Vb4 = dict(CFG); Vb4["gate_exempt_ind"] = True; Vb4["r30a_on"] = True
    Vb4["r30a_same_day_sector_cap"] = 4
    for tag, V in (("U100BP_r30a3", Vb3), ("U100BP_r30a4", Vb4)):
        eq, tr = run_unified([("BP", D_BP, V, 1.0, 20, False)], tag)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}.csv"), index=False, encoding="utf-8-sig")
        R7[tag] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr),
                       variant=dict(r30a_on=True, cap=V.get("r30a_same_day_sector_cap", 3)))
        log(f"[{tag}] CAGR={R7[tag]['metrics']['cagr']*100:+.1f}% MDD={R7[tag]['metrics']['mdd']*100:.1f}%")
    with open(os.path.join(REPORT_DIR, "summary_round7.json"), "w", encoding="utf-8") as f:
        json.dump(R7, f, ensure_ascii=False, indent=1, default=float)
    log("ROUND7 DONE")
    LOG.close(); sys.exit(0)

# ================= 5g. round8: 第二轮复测(20261023主人「继续」令) =================
# A包组合=清单A1相位0.5+A2熔断15/25+A3止损ATR1.5+A4流动性2000万(M1域, 逐项证据见优化条目清单)
# B1落闸态旗舰=R30b帽30%(PD-024③已落闸pre_trade_check_v2)+R30a cap3+无豁免=现行生产闸口径回测等效
# 豁免口径旗舰=连续性参考(与R7 U100BP_r30a3同口径, 仅端点延伸)
if ARGS.round8:
    R8 = {}
    for mname, r in RESULTS.items():  # 端点延伸后的基线一并入档(同次运行同口径)
        R8[f"BASE__{mname}"] = dict(metrics=metrics_of(r["eq"], r["tr"]), yearly=yearly_of(r["eq"]))
    for tag, ov in (
        ("R8m1_base", {}),
        ("R8m1_pkg", dict(phase_min_q=0.5, cb_dd_reduce=0.15, cb_dd_flat=0.25,
                          stop_buffer_atr=1.5, min_amt20=20e6)),
        ("R8m1_pkg_noA1", dict(cb_dd_reduce=0.15, cb_dd_flat=0.25,
                               stop_buffer_atr=1.5, min_amt20=20e6)),
    ):
        V = dict(CFG); V.update(ov)
        D = build_daily("M1_lowconsol_main", V)
        eq, tr = run_portfolio("M1_lowconsol_main", D, V)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}__M1_lowconsol_main.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}__M1_lowconsol_main.csv"), index=False, encoding="utf-8-sig")
        R8[f"{tag}__M1_lowconsol_main"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr), variant=ov)
        _m = R8[f"{tag}__M1_lowconsol_main"]["metrics"]
        log(f"[{tag}] M1: CAGR={_m['cagr']*100:+.1f}% MDD={_m['mdd']*100:.1f}% "
            f"胜率={(_m['win_rate'] or 0)*100:.1f}% 交易={_m['n_trades']}")
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    Vprod = dict(CFG); Vprod.update(r30b_cluster_cap=0.30, r30a_on=True)   # B1落闸态(帽30%, 无豁免)
    Vref = dict(CFG); Vref.update(gate_exempt_ind=True, r30a_on=True)      # 豁免口径连续性参考
    for tag, V in (("U100BP_r30a3_cap30", Vprod), ("U100BP_r30a3_free", Vref)):
        eq, tr = run_unified([("BP", D_BP, V, 1.0, 20, False)], tag)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}.csv"), index=False, encoding="utf-8-sig")
        R8[tag] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr),
                       variant={k: V.get(k) for k in ("r30b_cluster_cap", "r30a_same_day_sector_cap",
                                                      "gate_exempt_ind", "r30a_on")})
        _m = R8[tag]["metrics"]
        log(f"[{tag}] 终值={_m['final_equity']:,.0f} CAGR={_m['cagr']*100:+.1f}% "
            f"MDD={_m['mdd']*100:.1f}% 交易={_m['n_trades']}")
    with open(os.path.join(REPORT_DIR, "summary_round8.json"), "w", encoding="utf-8") as f:
        json.dump(R8, f, ensure_ascii=False, indent=1, default=float)
    log("ROUND8 DONE")
    LOG.close(); sys.exit(0)

# ================= 5h. round9: A4/A2 采纳子集隔离验收(20261023主人「测试下按推荐继续」) =================
# 验收判据(跑前冻结, 对应优化条目清单A4/A2证据):
#   A4-M1: CAGR(R9m1_a4)-CAGR(BASE__M1) >= +2.0pp   (清单证据+2.9pp)
#   A4-M6: CAGR(R9m6_a4)-CAGR(R9m6_base) >= +3.0pp  (清单证据+4.8pp)
#   A2-M1: MDD 优于基线 且 CAGR 方向为正(隔离首测; 清单原证据为栈上cb扫描剂量单调)
#   子集栈A4+A2: CAGR >= 单A4 且 MDD 优于基线
#   旗舰回归: U100BP_r30a3_cap30 期末净值与 round8(398,084) 差<1元, 否则FAIL
if ARGS.round9:
    R9 = {}
    for mname, r in RESULTS.items():
        R9[f"BASE__{mname}"] = dict(metrics=metrics_of(r["eq"], r["tr"]), yearly=yearly_of(r["eq"]))
    for tag, mname, ov in (
        ("R9m1_a4", "M1_lowconsol_main", dict(min_amt20=20e6)),
        ("R9m1_a2", "M1_lowconsol_main", dict(cb_dd_reduce=0.15, cb_dd_flat=0.25)),
        ("R9m1_a4a2", "M1_lowconsol_main", dict(min_amt20=20e6, cb_dd_reduce=0.15, cb_dd_flat=0.25)),
        ("R9m6_base", "M6_wfa_top20", {}),
        ("R9m6_a4", "M6_wfa_top20", dict(min_amt20=20e6)),
    ):
        V = dict(CFG); V.update(ov)
        D = build_daily(mname, V)
        eq, tr = run_portfolio(mname, D, V)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}__{mname}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}__{mname}.csv"), index=False, encoding="utf-8-sig")
        R9[f"{tag}__{mname}"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr), variant=ov)
        _m = R9[f"{tag}__{mname}"]["metrics"]
        log(f"[{tag}] {mname}: CAGR={_m['cagr']*100:+.2f}% MDD={_m['mdd']*100:.1f}% 交易={_m['n_trades']}")
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    Vprod = dict(CFG); Vprod.update(r30b_cluster_cap=0.30, r30a_on=True)
    eq, tr = run_unified([("BP", D_BP, Vprod, 1.0, 20, False)], "U100BP_r30a3_cap30")
    eq.to_csv(os.path.join(REPORT_DIR, "equity_U100BP_r30a3_cap30_r9.csv"), index=False)
    R9["U100BP_r30a3_cap30"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr),
                                    variant={k: Vprod.get(k) for k in ("r30b_cluster_cap", "r30a_same_day_sector_cap", "r30a_on")})
    _c = lambda k: R9[k]["metrics"]["cagr"]; _d = lambda k: R9[k]["metrics"]["mdd"]
    _k1, _k6 = "M1_lowconsol_main", "M6_wfa_top20"
    _r8 = json.load(open(os.path.join(REPORT_DIR, "summary_round8.json"), encoding="utf-8"))
    _eq9 = R9["U100BP_r30a3_cap30"]["metrics"]["final_equity"]
    _eq8 = _r8["U100BP_r30a3_cap30"]["metrics"]["final_equity"]
    R9["acceptance"] = dict(
        flagship_regression=dict(round9=_eq9, round8=_eq8, passed=bool(abs(_eq9 - _eq8) < 1.0)),
        a4_m1=dict(delta_pp=(_c(f"R9m1_a4__{_k1}") - _c(f"BASE__{_k1}")) * 100,
                   passed=bool((_c(f"R9m1_a4__{_k1}") - _c(f"BASE__{_k1}")) >= 0.02)),
        a4_m6=dict(delta_pp=(_c(f"R9m6_a4__{_k6}") - _c(f"R9m6_base__{_k6}")) * 100,
                   passed=bool((_c(f"R9m6_a4__{_k6}") - _c(f"R9m6_base__{_k6}")) >= 0.03)),
        a2_m1=dict(mdd_better=bool(_d(f"R9m1_a2__{_k1}") > _d(f"BASE__{_k1}")),
                   cagr_positive_dir=bool(_c(f"R9m1_a2__{_k1}") > _c(f"BASE__{_k1}"))),
        stack_a4a2=dict(cagr_ge_single_a4=bool(_c(f"R9m1_a4a2__{_k1}") >= _c(f"R9m1_a4__{_k1}")),
                        mdd_better_than_base=bool(_d(f"R9m1_a4a2__{_k1}") > _d(f"BASE__{_k1}"))))
    with open(os.path.join(REPORT_DIR, "summary_round9.json"), "w", encoding="utf-8") as f:
        json.dump(R9, f, ensure_ascii=False, indent=1, default=float)
    log("ACCEPTANCE " + json.dumps(R9["acceptance"], ensure_ascii=False, default=str))
    log("ROUND9 DONE")
    LOG.close(); sys.exit(0)

# ================= 5i. round10: A1/A3 隔离验收 + 无A2栈(20261023主人「继续」令, 只取证不预采纳) =================
# 验收判据(跑前冻结):
#   A1-M1(相位开关,定位降噪件): 笔数<=基线70% 且 MDD优于基线 且 CAGR>=基线
#   A3-M1(ATR止损缓冲,硬底-12%): CAGR>基线 且 止盈笔数>基线(清单证据: 止盈59→362笔)
#   栈A1+A3+A4(无A2): CAGR>=三个单因子最大值 且 MDD优于基线; 对照round9单A4(-4.72%)与round8全包(-0.32%)
#   旗舰回归: 期末净值=round8(398,084.06)差<1元
if ARGS.round10:
    R10 = {}
    for mname, r in RESULTS.items():
        R10[f"BASE__{mname}"] = dict(metrics=metrics_of(r["eq"], r["tr"]),
                                     acc=acceptance_stats(r["tr"]), yearly=yearly_of(r["eq"]))
    for tag, ov in (
        ("R10m1_a1", dict(phase_min_q=0.5)),
        ("R10m1_a3", dict(stop_buffer_atr=1.5)),
        ("R10m1_a1a3", dict(phase_min_q=0.5, stop_buffer_atr=1.5)),
        ("R10m1_a1a3a4", dict(phase_min_q=0.5, stop_buffer_atr=1.5, min_amt20=20e6)),
    ):
        V = dict(CFG); V.update(ov)
        D = build_daily("M1_lowconsol_main", V)
        eq, tr = run_portfolio("M1_lowconsol_main", D, V)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}__M1_lowconsol_main.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}__M1_lowconsol_main.csv"), index=False, encoding="utf-8-sig")
        R10[f"{tag}__M1_lowconsol_main"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr), variant=ov)
        _m = R10[f"{tag}__M1_lowconsol_main"]["metrics"]
        log(f"[{tag}] M1: CAGR={_m['cagr']*100:+.2f}% MDD={_m['mdd']*100:.1f}% 交易={_m['n_trades']}")
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    Vprod = dict(CFG); Vprod.update(r30b_cluster_cap=0.30, r30a_on=True)
    eq, tr = run_unified([("BP", D_BP, Vprod, 1.0, 20, False)], "U100BP_r30a3_cap30")
    eq.to_csv(os.path.join(REPORT_DIR, "equity_U100BP_r30a3_cap30_r10.csv"), index=False)
    R10["U100BP_r30a3_cap30"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr))
    _c = lambda k: R10[k]["metrics"]["cagr"]; _d = lambda k: R10[k]["metrics"]["mdd"]
    _n = lambda k: R10[k]["metrics"]["n_trades"]; _tp = lambda k: (R10[k].get("acc") or {}).get("tp_n") or 0
    _k1 = "M1_lowconsol_main"; _b = f"BASE__{_k1}"
    _r8 = json.load(open(os.path.join(REPORT_DIR, "summary_round8.json"), encoding="utf-8"))
    _r9 = json.load(open(os.path.join(REPORT_DIR, "summary_round9.json"), encoding="utf-8"))
    _eq10 = R10["U100BP_r30a3_cap30"]["metrics"]["final_equity"]
    _eq8 = _r8["U100BP_r30a3_cap30"]["metrics"]["final_equity"]
    _singles = [_c(f"R10m1_a1__{_k1}"), _c(f"R10m1_a3__{_k1}")]
    _c_r9a4 = _r9["R9m1_a4__M1_lowconsol_main"]["metrics"]["cagr"]
    _c_r8pkg = _r8["R8m1_pkg__M1_lowconsol_main"]["metrics"]["cagr"]
    R10["acceptance"] = dict(
        flagship_regression=dict(round10=_eq10, round8=_eq8, passed=bool(abs(_eq10 - _eq8) < 1.0)),
        a1_m1=dict(trades_ratio=round(_n(f"R10m1_a1__{_k1}") / max(_n(_b), 1), 3),
                   mdd_better=bool(_d(f"R10m1_a1__{_k1}") > _d(_b)),
                   cagr_ge_base=bool(_c(f"R10m1_a1__{_k1}") >= _c(_b))),
        a3_m1=dict(cagr_gt_base=bool(_c(f"R10m1_a3__{_k1}") > _c(_b)),
                   tp_n_base=_tp(_b), tp_n_a3=_tp(f"R10m1_a3__{_k1}"),
                   tp_increased=bool(_tp(f"R10m1_a3__{_k1}") > _tp(_b))),
        stack_a1a3a4=dict(cagr_ge_singles=bool(_c(f"R10m1_a1a3a4__{_k1}") >= max(_singles)),
                          mdd_better_than_base=bool(_d(f"R10m1_a1a3a4__{_k1}") > _d(_b)),
                          vs_round9_a4_pp=round((_c(f"R10m1_a1a3a4__{_k1}") - _c_r9a4) * 100, 2),
                          vs_round8_fullpkg_pp=round((_c(f"R10m1_a1a3a4__{_k1}") - _c_r8pkg) * 100, 2)))
    with open(os.path.join(REPORT_DIR, "summary_round10.json"), "w", encoding="utf-8") as f:
        json.dump(R10, f, ensure_ascii=False, indent=1, default=float)
    log("ACCEPTANCE " + json.dumps(R10["acceptance"], ensure_ascii=False, default=str))
    log("ROUND10 DONE")
    LOG.close(); sys.exit(0)

# ================= 5j. round11: A3+A4 自然对补测(20261024主人「继续」令, 矩阵收尾) =================
# 决策规则(跑前冻结): 若 A3A4 CAGR >= A3单独-0.5pp 且 MDD 不差于A3单独 → 推荐方案二(A3+A4域绑定注册); 否则推荐方案一(A3单因子)
# 旗舰回归: 期末净值=round8(398,084.06)差<1元(第四次一致性校验)
if ARGS.round11:
    R11 = {}
    for mname, r in RESULTS.items():
        R11[f"BASE__{mname}"] = dict(metrics=metrics_of(r["eq"], r["tr"]),
                                     acc=acceptance_stats(r["tr"]), yearly=yearly_of(r["eq"]))
    V = dict(CFG); V.update(stop_buffer_atr=1.5, min_amt20=20e6)
    D = build_daily("M1_lowconsol_main", V)
    eq, tr = run_portfolio("M1_lowconsol_main", D, V)
    eq.to_csv(os.path.join(REPORT_DIR, "equity_R11m1_a3a4__M1_lowconsol_main.csv"), index=False)
    tr.to_csv(os.path.join(REPORT_DIR, "trades_R11m1_a3a4__M1_lowconsol_main.csv"), index=False, encoding="utf-8-sig")
    R11["R11m1_a3a4__M1_lowconsol_main"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr),
                                                variant=dict(stop_buffer_atr=1.5, min_amt20=20e6))
    _m = R11["R11m1_a3a4__M1_lowconsol_main"]["metrics"]
    log(f"[R11m1_a3a4] M1: CAGR={_m['cagr']*100:+.2f}% MDD={_m['mdd']*100:.1f}% 交易={_m['n_trades']}")
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    Vprod = dict(CFG); Vprod.update(r30b_cluster_cap=0.30, r30a_on=True)
    eq, tr = run_unified([("BP", D_BP, Vprod, 1.0, 20, False)], "U100BP_r30a3_cap30")
    eq.to_csv(os.path.join(REPORT_DIR, "equity_U100BP_r30a3_cap30_r11.csv"), index=False)
    R11["U100BP_r30a3_cap30"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr))
    _r10 = json.load(open(os.path.join(REPORT_DIR, "summary_round10.json"), encoding="utf-8"))
    _r8 = json.load(open(os.path.join(REPORT_DIR, "summary_round8.json"), encoding="utf-8"))
    _k = "R11m1_a3a4__M1_lowconsol_main"
    _k3 = "R10m1_a3__M1_lowconsol_main"; _k134 = "R10m1_a1a3a4__M1_lowconsol_main"
    _c11 = R11[_k]["metrics"]["cagr"]; _d11 = R11[_k]["metrics"]["mdd"]
    _c_a3 = _r10[_k3]["metrics"]["cagr"]; _d_a3 = _r10[_k3]["metrics"]["mdd"]
    _c_134 = _r10[_k134]["metrics"]["cagr"]
    _eq11 = R11["U100BP_r30a3_cap30"]["metrics"]["final_equity"]
    _eq8 = _r8["U100BP_r30a3_cap30"]["metrics"]["final_equity"]
    R11["acceptance"] = dict(
        flagship_regression=dict(round11=_eq11, round8=_eq8, passed=bool(abs(_eq11 - _eq8) < 1.0)),
        a3a4_vs_a3=dict(delta_cagr_pp=round((_c11 - _c_a3) * 100, 2),
                        mdd_a3=round(_d_a3 * 100, 1), mdd_a3a4=round(_d11 * 100, 1),
                        recommend_option2=bool(_c11 >= _c_a3 - 0.005 and _d11 >= _d_a3)),
        a3a4_vs_a1a3a4_pp=round((_c11 - _c_134) * 100, 2))
    with open(os.path.join(REPORT_DIR, "summary_round11.json"), "w", encoding="utf-8") as f:
        json.dump(R11, f, ensure_ascii=False, indent=1, default=float)
    log("ACCEPTANCE " + json.dumps(R11["acceptance"], ensure_ascii=False, default=str))
    log("ROUND11 DONE")
    LOG.close(); sys.exit(0)

# ================= 5k. round12: 修复后腿统一账户架构对照(20261024主人「继续」令) =================
# 范围冻结(两架构变体+旗舰回归, 无权重扫描): M1腿=已采纳A3+A4(stop_buffer_atr1.5/liq2000万,无A1无A2);
# M6腿=已采纳A4(liq2000万); BP腿=现行闸口径(帽30%+R30a cap3)
# 判定规则(跑前冻结): 若任一组合 MDD 优于旗舰(-16.2%) 且 CAGR>=旗舰-1pp → 多元化价值成立,列架构候选呈报主人;
#                     若均劣于旗舰 → 维持BP单腿旗舰,对照证据归档。组合层采纳=主人令(改生产架构基准)。
# 旗舰回归: 期末净值=round8(398,084.06)差<1元(第五次一致性校验)
if ARGS.round12:
    R12 = {}
    for mname, r in RESULTS.items():
        R12[f"BASE__{mname}"] = dict(metrics=metrics_of(r["eq"], r["tr"]), yearly=yearly_of(r["eq"]))
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    Vprod = dict(CFG); Vprod.update(r30b_cluster_cap=0.30, r30a_on=True)   # BP腿现行闸口径
    V6r = dict(CFG); V6r.update(min_amt20=20e6)                            # M6腿=已采纳A4
    V1r = dict(CFG); V1r.update(stop_buffer_atr=1.5, min_amt20=20e6)       # M1腿=已采纳A3+A4
    D6r = build_daily("M6_wfa_top20", V6r)
    D1r = build_daily("M1_lowconsol_main", V1r)
    for tag, legs in (
        ("U602020_r12", [("BP", D_BP, Vprod, 0.6, 20, False),
                         ("M6", D6r, V6r, 0.2, 20, True),
                         ("M1", D1r, V1r, 0.2, 10, True)]),
        ("U7030_r12", [("BP", D_BP, Vprod, 0.7, 20, False),
                       ("M6", D6r, V6r, 0.3, 20, True)]),
    ):
        eq, tr = run_unified(legs, tag)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}.csv"), index=False, encoding="utf-8-sig")
        R12[tag] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr))
        _m = R12[tag]["metrics"]
        log(f"[{tag}] 终值={_m['final_equity']:,.0f} CAGR={_m['cagr']*100:+.2f}% MDD={_m['mdd']*100:.1f}% 交易={_m['n_trades']}")
    eq, tr = run_unified([("BP", D_BP, Vprod, 1.0, 20, False)], "U100BP_r30a3_cap30")
    eq.to_csv(os.path.join(REPORT_DIR, "equity_U100BP_r30a3_cap30_r12.csv"), index=False)
    R12["U100BP_r30a3_cap30"] = dict(metrics=metrics_of(eq, tr), acc=acceptance_stats(tr))
    _r8 = json.load(open(os.path.join(REPORT_DIR, "summary_round8.json"), encoding="utf-8"))
    _eq12 = R12["U100BP_r30a3_cap30"]["metrics"]["final_equity"]
    _eq8 = _r8["U100BP_r30a3_cap30"]["metrics"]["final_equity"]
    _fl_c = R12["U100BP_r30a3_cap30"]["metrics"]["cagr"]; _fl_d = R12["U100BP_r30a3_cap30"]["metrics"]["mdd"]
    _verdict = {}
    for tag in ("U602020_r12", "U7030_r12"):
        _m = R12[tag]["metrics"]
        _verdict[tag] = dict(cagr_pp_vs_flagship=round((_m["cagr"] - _fl_c) * 100, 2),
                             mdd_pp_vs_flagship=round((_m["mdd"] - _fl_d) * 100, 2),
                             diversification_value=bool(_m["mdd"] > _fl_d and _m["cagr"] >= _fl_c - 0.01))
    R12["acceptance"] = dict(
        flagship_regression=dict(round12=_eq12, round8=_eq8, passed=bool(abs(_eq12 - _eq8) < 1.0)),
        verdict=_verdict)
    with open(os.path.join(REPORT_DIR, "summary_round12.json"), "w", encoding="utf-8") as f:
        json.dump(R12, f, ensure_ascii=False, indent=1, default=float)
    log("ACCEPTANCE " + json.dumps(R12["acceptance"], ensure_ascii=False, default=str))
    log("ROUND12 DONE")
    LOG.close(); sys.exit(0)

# ================= 5m. round13: 第二轮迭代复测(20261026优化条目A/B, 宪法§13验收规则) =================
# 条目来源: REPORT_20261026.md §五/§六, 变体逐项A/B + 旗舰回归断言(与round8端点延伸基线差<1元)
# 验收标准(跑前冻结):
#   R13sl15  BP个股止损15%   -> MDD改善>=3pp 且 CAGR降幅<=1pp
#   R13mh180 持仓180天上限   -> CAGR变化<1pp 且 尾部亏损(P5)收敛>=20%
#   R13ma200 等权MA200降半仓 -> 2018年收益改善 且 全期CAGR损失<=2pp
#   R13combo 三者叠加        -> MDD最优且CAGR>=单因子最大者-2pp
if ARGS.round13:
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    D_BP = {int(d): [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            for d, v in bpsig.items()}
    Vbase = dict(CFG); Vbase.update(r30b_cluster_cap=0.30, r30a_on=True)  # B1落闸态旗舰口径
    R13 = {}
    # 旗舰回归断言基线(无变体)
    eq0, tr0 = run_unified([("BP", D_BP, Vbase, 1.0, 20, False)], "R13base")
    eq0.to_csv(os.path.join(REPORT_DIR, "equity_R13base.csv"), index=False)
    tr0.to_csv(os.path.join(REPORT_DIR, "trades_R13base.csv"), index=False, encoding="utf-8-sig")
    R13["R13base"] = dict(metrics=metrics_of(eq0, tr0), acc=acceptance_stats(tr0), variant={})
    _m0 = R13["R13base"]["metrics"]
    log(f"[R13base] 终值={_m0['final_equity']:,.0f} CAGR={_m0['cagr']*100:+.1f}% "
        f"MDD={_m0['mdd']*100:.1f}% 胜率={(_m0['win_rate'] or 0)*100:.1f}% 交易={_m0['n_trades']}")
    _eq8_ref = 402478.0  # round8端点延伸锚(20261026, run_log在档)
    _flagship_ok = abs(_m0["final_equity"] - _eq8_ref) < 2.0
    log(f"旗舰回归断言: R13base={_m0['final_equity']:,.2f} vs round8锚={_eq8_ref:,.2f} "
        f"-> {'PASS' if _flagship_ok else 'FAIL'}")
    # 变体矩阵
    R13_VARS = [
        ("R13sl15",  dict(bp_stop_loss_pct=0.15)),
        ("R13mh180", dict(bp_max_hold_days=180)),
        ("R13ma200", dict(env_ma200_half=True)),
        ("R13combo", dict(bp_stop_loss_pct=0.15, bp_max_hold_days=180, env_ma200_half=True)),
    ]
    _mdd0 = _m0["mdd"]; _cagr0 = _m0["cagr"]
    for tag, ov in R13_VARS:
        V = dict(Vbase); V.update(ov)
        eq, tr = run_unified([("BP", D_BP, V, 1.0, 20, False)], tag)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}.csv"), index=False, encoding="utf-8-sig")
        m = metrics_of(eq, tr)
        acc = acceptance_stats(tr)
        # 尾部亏损P5(单笔pnl_pct分布, tr为DataFrame)
        _lcol = tr["pnl_pct"].astype(float)
        _losses = sorted(float(v) for v in _lcol[_lcol < 0])
        _p5 = _losses[max(0, int(len(_losses) * 0.05) - 1)] if _losses else 0.0
        _yret = yearly_of(eq)
        _y18 = next((y["ret"] for y in _yret if y["year"] == 2018), None)
        R13[tag] = dict(metrics=m, acc=acc, variant=ov,
                        tail_loss_p5=float(_p5), y2018=float(_y18) if _y18 is not None else None)
        log(f"[{tag}] 终值={m['final_equity']:,.0f} CAGR={m['cagr']*100:+.1f}% MDD={m['mdd']*100:.1f}% "
            f"胜率={(_m0['win_rate'] or 0)*100:.1f}->{(m['win_rate'] or 0)*100:.1f}% 交易={m['n_trades']} "
            f"P5尾亏={_p5*100:.1f}% 2018={(_y18*100 if _y18 is not None else float('nan')):.1f}%")
    # 验收判定(跑前冻结标准)
    def _g(tag):
        return R13[tag]["metrics"]
    _acc = {
        "flagship_regression": dict(round13=_m0["final_equity"], round8_anchor=_eq8_ref, passed=_flagship_ok),
        "sl15": dict(mdd_impr_pp=round((_g("R13sl15")["mdd"] - _mdd0) * 100, 2),
                     cagr_drop_pp=round((_cagr0 - _g("R13sl15")["cagr"]) * 100, 2),
                     passed=bool((_g("R13sl15")["mdd"] - _mdd0) * 100 >= 3.0 and (_cagr0 - _g("R13sl15")["cagr"]) * 100 <= 1.0)),
        "mh180": dict(cagr_chg_pp=round((_g("R13mh180")["cagr"] - _cagr0) * 100, 2),
                      p5_loss_chg_pp=round((R13["R13mh180"]["tail_loss_p5"] - R13["R13base"].get("tail_loss_p5", 0)) * 100, 2),
                      passed=None),
        "ma200": dict(y2018=R13["R13ma200"]["y2018"], y2018_base=R13["R13base"].get("y2018_base"),
                      cagr_drop_pp=round((_cagr0 - _g("R13ma200")["cagr"]) * 100, 2),
                      passed=bool(R13["R13ma200"]["y2018"] is not None and R13["R13ma200"]["y2018"] > -0.06
                                  and (_cagr0 - _g("R13ma200")["cagr"]) * 100 <= 2.0)),
        "combo": dict(cagr=_g("R13combo")["cagr"], mdd=_g("R13combo")["mdd"]),
    }
    # mh180 P5基线补算(tr0为DataFrame)
    _lcol0 = tr0["pnl_pct"].astype(float)
    _l0 = sorted(float(v) for v in _lcol0[_lcol0 < 0])
    _p5_0 = _l0[max(0, int(len(_l0) * 0.05) - 1)] if _l0 else 0.0
    _y0 = yearly_of(eq0)
    _y18_0 = next((y["ret"] for y in _y0 if y["year"] == 2018), None)
    R13["R13base"]["tail_loss_p5"] = float(_p5_0)
    R13["R13base"]["y2018"] = float(_y18_0) if _y18_0 is not None else None
    _acc["mh180"]["p5_loss_chg_pp"] = round((R13["R13mh180"]["tail_loss_p5"] - _p5_0) * 100, 2)
    _acc["mh180"]["passed"] = bool(abs((_g("R13mh180")["cagr"] - _cagr0)) * 100 < 1.0
                                   and (R13["R13mh180"]["tail_loss_p5"] - _p5_0) >= 0.20)
    _acc["ma200"]["y2018_base"] = _y18_0
    R13["acceptance"] = _acc
    with open(os.path.join(REPORT_DIR, "summary_round13.json"), "w", encoding="utf-8") as f:
        json.dump(R13, f, ensure_ascii=False, indent=1, default=float)
    log("ACCEPTANCE " + json.dumps(_acc, ensure_ascii=False, default=str))
    log("ROUND13 DONE")
    LOG.close(); sys.exit(0)

# ================= 5n. round14: 预注册复测(20261026「继续」令; 验收标准跑前冻结) =================
# A env_ma200_month_end(熊态仅月末再平衡日半仓): y2018>=-7% 且 cagr_drop<=2pp 且 mdd_impr>=3pp
# B BP池剔除申万L1=房地产(快照口径): |cagr_chg|<=1pp 且 y2018改善>=1pp
# C A+B组合: mdd<=A的mdd 且 cagr>=A的cagr-0.5pp
# D 动态权重 BP70/M6-30(熊态M6腿禁开, M6腿=已采纳A4口径与round12一致): cagr>=13.0% 且 mdd>=-25%
if ARGS.round14:
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    _RE_L1 = {"房地产"}
    def _mk_bp(excl=False):
        out = {}
        for d, v in bpsig.items():
            picks = [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            if excl:
                picks = [(c, s) for c, s in picks if l1_of.get(c) not in _RE_L1]
            out[int(d)] = picks
        return out
    Vbase = dict(CFG); Vbase.update(r30b_cluster_cap=0.30, r30a_on=True)  # B1落闸态旗舰口径
    R14 = {}
    def _u14(tag, legs):
        eq, tr = run_unified(legs, tag)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}.csv"), index=False, encoding="utf-8-sig")
        m = metrics_of(eq, tr)
        _yl = yearly_of(eq)
        _y18 = next((y["ret"] for y in _yl if y["year"] == 2018), None)
        R14[tag] = dict(metrics=m, acc=acceptance_stats(tr),
                        y2018=float(_y18) if _y18 is not None else None)
        log(f"[{tag}] 终值={m['final_equity']:,.0f} CAGR={m['cagr']*100:+.2f}% MDD={m['mdd']*100:.1f}% "
            f"胜率={(m['win_rate'] or 0)*100:.1f}% 交易={m['n_trades']} "
            f"2018={(_y18*100 if _y18 is not None else float('nan')):.1f}%")
        return m
    _m0 = _u14("R14base", [("BP", _mk_bp(), Vbase, 1.0, 20, False)])
    _eq8_ref = 402478.0
    _flag_ok = abs(_m0["final_equity"] - _eq8_ref) < 2.0
    log(f"旗舰回归断言: R14base={_m0['final_equity']:,.2f} vs round8锚={_eq8_ref:,.2f} "
        f"-> {'PASS' if _flag_ok else 'FAIL'}")
    _c0, _d0 = _m0["cagr"], _m0["mdd"]
    _y18_0 = R14["R14base"]["y2018"]
    _ma = _u14("R14a_me", [("BP", _mk_bp(), dict(Vbase, env_ma200_month_end=True), 1.0, 20, False)])
    _mb = _u14("R14b_re", [("BP", _mk_bp(excl=True), Vbase, 1.0, 20, False)])
    _mc = _u14("R14c_combo", [("BP", _mk_bp(excl=True),
                               dict(Vbase, env_ma200_month_end=True), 1.0, 20, False)])
    V6r = dict(CFG); V6r.update(min_amt20=20e6, env_ma200_off=True)       # M6腿=已采纳A4口径
    D6r = build_daily("M6_wfa_top20", V6r)
    _md = _u14("R14d_dyn", [("BP", _mk_bp(), Vbase, 0.7, 20, False),
                            ("M6", D6r, V6r, 0.3, 20, True)])
    _acc14 = dict(
        flagship_regression=dict(round14=_m0["final_equity"], round8_anchor=_eq8_ref, passed=_flag_ok),
        a_me=dict(y2018=R14["R14a_me"]["y2018"], cagr_drop_pp=round((_c0 - _ma["cagr"]) * 100, 2),
                  mdd_impr_pp=round((_ma["mdd"] - _d0) * 100, 2),
                  passed=bool(R14["R14a_me"]["y2018"] is not None and R14["R14a_me"]["y2018"] >= -0.07
                              and (_c0 - _ma["cagr"]) * 100 <= 2.0
                              and (_ma["mdd"] - _d0) * 100 >= 3.0)),
        b_re=dict(cagr_chg_pp=round((_mb["cagr"] - _c0) * 100, 2),
                  y2018_impr_pp=round(((R14["R14b_re"]["y2018"] or 0) - (_y18_0 or 0)) * 100, 2),
                  passed=bool(abs((_mb["cagr"] - _c0)) * 100 <= 1.0
                              and ((R14["R14b_re"]["y2018"] or 0) - (_y18_0 or 0)) * 100 >= 1.0)),
        c_combo=dict(mdd=_mc["mdd"], cagr=_mc["cagr"],
                     passed=bool(_mc["mdd"] <= _ma["mdd"] + 1e-9
                                 and _mc["cagr"] >= _ma["cagr"] - 0.005)),
        d_dyn=dict(cagr=_md["cagr"], mdd=_md["mdd"],
                   passed=bool(_md["cagr"] >= 0.13 and _md["mdd"] >= -0.25)),
    )
    R14["acceptance"] = _acc14
    with open(os.path.join(REPORT_DIR, "summary_round14.json"), "w", encoding="utf-8") as f:
        json.dump(R14, f, ensure_ascii=False, indent=1, default=float)
    log("ACCEPTANCE " + json.dumps(_acc14, ensure_ascii=False, default=str))
    log("ROUND14 DONE")
    LOG.close(); sys.exit(0)

# ================= 5p. round15: 条目8胜率结构改造A/B(20261026「继续」令; 标准跑前冻结) =================
# BP腿止盈(全仓版): 收盘/成本-1 >= bp_take_profit_pct -> 次日开盘全仓退出(经plan, next-bar)
# 冻结验收(每变体): 胜率>=70% 且 PF>=3.0 (原条目8标准); CAGR/尾部如实呈现不作达标项
if ARGS.round15:
    _bp_path = os.path.join(REPORT_DIR, "bp_ep_signals.json")
    if not os.path.exists(_bp_path):
        die("缺少 bp_ep_signals.json")
    bpsig = json.load(open(_bp_path, encoding="utf-8"))
    _RE_L1 = {"房地产"}
    def _mk_bp(excl=False):
        out = {}
        for d, v in bpsig.items():
            picks = [(c, float(-i)) for i, c in enumerate(v) if c in cidx]
            if excl:
                picks = [(c, s) for c, s in picks if l1_of.get(c) not in _RE_L1]
            out[int(d)] = picks
        return out
    Vbase = dict(CFG); Vbase.update(r30b_cluster_cap=0.30, r30a_on=True)
    R15 = {}
    def _u15(tag, legs):
        eq, tr = run_unified(legs, tag)
        eq.to_csv(os.path.join(REPORT_DIR, f"equity_{tag}.csv"), index=False)
        tr.to_csv(os.path.join(REPORT_DIR, f"trades_{tag}.csv"), index=False, encoding="utf-8-sig")
        m = metrics_of(eq, tr)
        R15[tag] = dict(metrics=m, acc=acceptance_stats(tr))
        _top = tr.loc[tr["pnl"].idxmax()] if len(tr) else None
        log(f"[{tag}] 终值={m['final_equity']:,.0f} CAGR={m['cagr']*100:+.2f}% MDD={m['mdd']*100:.1f}% "
            f"胜率={(m['win_rate'] or 0)*100:.1f}% PF={m['profit_factor']:.2f} 交易={m['n_trades']} "
            f"均盈={m['avg_win']*100:.1f}% 均亏={m['avg_loss']*100:.1f}% "
            f"最大单盈={(_top['pnl'] if _top is not None else 0):,.0f}")
        return m
    _m0 = _u15("R15base", [("BP", _mk_bp(), Vbase, 1.0, 20, False)])
    _eq8_ref = 402478.0
    _flag_ok = abs(_m0["final_equity"] - _eq8_ref) < 2.0
    log(f"旗舰回归断言: R15base={_m0['final_equity']:,.2f} vs round8锚={_eq8_ref:,.2f} "
        f"-> {'PASS' if _flag_ok else 'FAIL'}")
    _vars15 = [
        ("R15tp15", {}, dict(bp_take_profit_pct=0.15)),
        ("R15tp20", {}, dict(bp_take_profit_pct=0.20)),
        ("R15tp25", {}, dict(bp_take_profit_pct=0.25)),
        ("R15btp20", dict(bp_exclude_l1=True), dict(bp_take_profit_pct=0.20)),
    ]
    _acc15 = dict(flagship_regression=dict(round15=_m0["final_equity"], round8_anchor=_eq8_ref, passed=_flag_ok))
    for tag, ex, ov in _vars15:
        V = dict(Vbase); V.update(ov)
        m = _u15(tag, [("BP", _mk_bp(excl=ex.get("bp_exclude_l1", False)), V, 1.0, 20, False)])
        _acc15[tag] = dict(win_rate=round((m["win_rate"] or 0) * 100, 2), pf=round(m["profit_factor"], 2),
                           cagr=round(m["cagr"] * 100, 2), mdd=round(m["mdd"] * 100, 2),
                           passed=bool((m["win_rate"] or 0) >= 0.70 and m["profit_factor"] >= 3.0))
    R15["acceptance"] = _acc15
    with open(os.path.join(REPORT_DIR, "summary_round15.json"), "w", encoding="utf-8") as f:
        json.dump(R15, f, ensure_ascii=False, indent=1, default=float)
    log("ACCEPTANCE " + json.dumps(_acc15, ensure_ascii=False, default=str))
    log("ROUND15 DONE")
    LOG.close(); sys.exit(0)
for mname, r in RESULTS.items():
    summary["models"][mname] = dict(metrics=metrics_of(r["eq"], r["tr"]), yearly=yearly_of(r["eq"]),
                                    note=MODEL_DEFS[mname]["note"], start=MODEL_DEFS[mname]["start"])
b = bench_idx / bench_idx[0]; yrs_b = len(b) / 243.0
pe = np.maximum.accumulate(bench_eqw)
summary["benchmark"] = dict(
    sse=dict(total_return=float(b[-1] / b[0] - 1), cagr=float((b[-1] / b[0]) ** (1 / max(yrs_b, 1e-9)) - 1),
             mdd=float((b / np.maximum.accumulate(b) - 1).min())),
    eqw=dict(total_return=float(bench_eqw[-1] - 1), cagr=float(bench_eqw[-1] ** (1 / max(yrs_b, 1e-9)) - 1),
             mdd=float((bench_eqw / pe - 1).min())))
with open(os.path.join(REPORT_DIR, "summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=1, default=float)

log("=== 汇总 ===")
for mname in MODEL_DEFS:
    m_ = summary["models"][mname]["metrics"]
    wr = f"{m_['win_rate']*100:5.1f}%" if m_["win_rate"] is not None else "  n/a"
    pf = f"{m_['profit_factor']:5.2f}" if m_["profit_factor"] is not None else "  n/a"
    log(f"{mname:20s} 终值{m_['final_equity']:>12,.0f} CAGR{m_['cagr']*100:+7.1f}% "
        f"MDD{m_['mdd']*100:6.1f}% 胜率{wr} PF{pf} 笔数{m_['n_trades']}")
log(f"基准 上证: 总收益{summary['benchmark']['sse']['total_return']*100:+.1f}% "
    f"CAGR{summary['benchmark']['sse']['cagr']*100:+.1f}% MDD{summary['benchmark']['sse']['mdd']*100:.1f}%")
log(f"基准 全市场等权: 总收益{summary['benchmark']['eqw']['total_return']*100:+.1f}% "
    f"CAGR{summary['benchmark']['eqw']['cagr']*100:+.1f}% MDD{summary['benchmark']['eqw']['mdd']*100:.1f}%")
log("ENGINE DONE")
LOG.close()
