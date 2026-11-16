# -*- coding: utf-8 -*-
"""驱动力样本观察库登记+双框架对垒卡(主人14样本池) 20261026 — 只读零交易权。
每个样本登记: 驱动力归类(主人框架) + 本地阶段状态(15状态机) + 双框架预测对垒:
  驱动力层预期(讲义) vs 阶段层预期(本地类型学) → 20日后实盘裁决哪层对。
约束标注: >30元/ST/688 = 只观察不可买(宪法铁律)。
输出: analysis/driver_sample_library_20261026.json + stdout
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = "analysis/driver_sample_library_20261026.json"
ASOF = 20261023

POOL = [
    # (code, name, 驱动类型, 归类依据摘要, 驱动力层预期, 验证节点)
    ("001309", "德明利", "业绩驱动", "前三季净利+32399%,存储景气", "增速爆发→持续性强", "下季毛利率/行业景气"),
    ("603186", "华正新材", "业绩驱动", "净利+1178%,毛利率18.7→36.1%", "量价齐升→看持续", "CCL提价持续性"),
    ("600487", "亨通光电", "业绩驱动", "净利预增151-168%,AI光通信", "产业逻辑清晰", "订单可见度;或已提前定价"),
    ("600241", "时代万恒", "政策驱动", "固态电池十五五规划,5连板", "政策预期先行", "半固态商业化/订单"),
    ("000862", "银星能源", "政策驱动", "节能装备政策,封单55万手", "板块级行情", "装机/招标数据"),
    ("002879", "兆威机电", "资金推动", "5日3板+50%,融资+18.75%", "资金持续则涨", "融资余额拐点"),
    ("002432", "九安医疗", "资金推动(争议)", "高换手高成交,主业-49%", "归类模糊样本", "AI医疗收入验证"),
    ("000498", "山东路桥", "估值修复", "PB0.44+大股东增持5.6-11.3%", "修复至中枢", "PB回归幅度/增持执行"),
    ("000651", "格力电器", "估值修复", "PB近5年底,股息8.1%", "底部修复", "分红/渠道份额"),
    ("601077", "渝农商行", "估值修复", "PB0.56+ROE企稳", "慢修复", "ROE持续性"),
    ("000430", "张家界", "ST/重整", "摘帽+负债86→54%", "重整修复", "摘帽后经营"),
    ("603268", "松发股份", "ST/重整", "摘帽+资产重组", "估值重塑", "资产质量"),
    ("000908", "*ST景峰", "ST/重整", "重整完毕+石药战投,9天7板", "高风险高弹性", "扣非转正;风险边界观察"),
]

con = sqlite3.connect(QFQ, uri=True)


def phase_of(code):
    gg = pd.read_sql_query(
        "SELECT date,close FROM stock_daily_meta_qfq WHERE code LIKE ? AND date>=20250901 ORDER BY date",
        con, params=(f"%{code}",))
    if len(gg) < 60:
        return None
    c = gg["close"].to_numpy(float)
    s = pd.Series(c)
    mn = s.rolling(250, min_periods=120).min().iloc[-1]
    mx = s.rolling(250, min_periods=120).max().iloc[-1]
    pos = (c[-1] - mn) / (mx - mn) * 100 if mx > mn else np.nan
    mom20 = c[-1] / c[-21] - 1
    mom5 = c[-1] / c[-6] - 1
    vol20 = s.pct_change().rolling(20, min_periods=20).std().iloc[-1] * np.sqrt(250)
    pl = "低" if pos < 25 else ("中" if pos < 70 else "高")
    ml = ("大跌" if mom20 < -0.15 else "跌" if mom20 < -0.04 else
          "平" if mom20 < 0.04 else "涨" if mom20 < 0.15 else "强涨")
    names = {("低","大跌"):"超跌探底",("低","跌"):"阴跌",("低","平"):"筑底横盘",("低","涨"):"低位启动",
             ("低","强涨"):"低位急拉",("中","大跌"):"破位下跌",("中","跌"):"中位回调",("中","平"):"中位震荡",
             ("中","涨"):"温和上行",("中","强涨"):"主升段",("高","跌"):"高位回落",("高","大跌"):"高位崩落",
             ("高","平"):"高位滞涨",("高","涨"):"高位续涨",("高","强涨"):"加速赶顶"}
    ph = names.get((pl, ml), "?")
    # 阶段层预期(来自phase_typology实测)
    phase_exp = {"超跌探底": "+6.94%", "破位下跌": "+4.58%", "低位急拉": "+2.86%", "阴跌": "+1.46%",
                 "中位回调": "+1.34%", "低位启动": "+1.01%", "高位续涨": "+0.88%", "中位震荡": "+0.49%",
                 "筑底横盘": "+0.52%", "温和上行": "+0.47%", "高位滞涨": "+0.37%", "主升段": "+0.20%",
                 "高位回落": "+0.16%", "加速赶顶": "-2.15%", "高位崩落": "+3.03%"}
    return {"close": round(float(c[-1]), 2), "pos250": round(float(pos), 1),
            "mom20": round(float(mom20), 3), "mom5": round(float(mom5), 3),
            "vol20": round(float(vol20), 2), "phase": ph, "phase_exp": phase_exp.get(ph, "?")}


rows = []
for code, name, dtype, basis, dexp, vnode in POOL:
    ph = phase_of(code)
    if ph is None:
        ph = {"close": None, "phase": "本地无数据(疑似非上市/新码)", "phase_exp": "?"}
    st_flag = "ST" in name
    px_flag = (ph["close"] or 0) > 30
    rows.append({"code": code, "name": name, "driver_type": dtype, "basis": basis,
                 "driver_expect": dexp, "verify_node": vnode,
                 **ph, "tradable": "只观察(ST)" if st_flag else ("只观察(>30元)" if px_flag else "可买域")})
con.close()
json.dump(rows, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"样本观察库登记 → {OUT} (14样本, 基准{ASOF}, 20日后结算双框架对垒)\n")
print(f"{'代码':<8}{'名称':<7}{'驱动类型':<10}{'收盘':>7}{'pos':>6}{'mom20':>8}  阶段(本地)      阶段层预期   可交易性")
for r in rows:
    print(f"{r['code']:<8}{r['name']:<7}{r['driver_type']:<10}{str(r['close']):>7}{str(r['pos250']):>6}"
          f"{('%+.0f%%' % (r['mom20']*100)) if r.get('mom20') is not None else '  n/a':>8}"
          f"  {r['phase']:<12}{r['phase_exp']:>8}   {r['tradable']}")
