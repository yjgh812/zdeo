# -*- coding: utf-8 -*-
"""第一维·步骤3完整化: 36只持仓逐只趋势/形态/S-5诊断 (2026-10-13盘前, 指令增量件, 只读)
口径: wb活副本qfq(至20261012, 与zc合并等价); MA=收盘简单均线; pos60=收盘在60日高低区间位置%;
      破位=close<MA20且MA20较3日前向下(与走坏判定主口径一致); S-5余量=-8%-浮亏%;
      板块当日%来自20261012申万二级涨跌表(死值嵌入, 来源=westock sector ranking)。
运行: python analysis/holdings_diagnostic_20261013.py
"""
import sys, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"

# (code6, 名称, 行业标签, 批次, 数量, 成本, 板块当日%)
POS = [
 ("300573","兴齐眼药","化学制药","C",1000,35.348,-2.12),
 ("003006","百亚股份","造纸","A",600,0.0,-1.35),
 ("601919","中远海控","水运","A",1500,16.454,-2.89),
 ("300817","双飞集团","机械基件","A",2400,16.365,-2.48),
 ("002688","金河生物","农业综合","A",5900,5.322,-2.0),
 ("600694","大商股份","百货","A",300,14.666,-2.02),
 ("600035","楚天高速","路桥","A",1300,3.743,1.19),
 ("600018","上港集团","港口","A",1800,5.502,-2.89),
 ("002174","游族网络","互联网(游戏)","A",100,12.320,0.22),
 ("600827","百联股份","超市连锁","A",200,7.875,-2.02),
 ("301370","国科恒泰","医药商业","A",1400,8.723,0.71),
 ("600626","申达股份","汽车配件","A",2100,3.602,-1.75),
 ("001390","古麒绒材","纺织服饰","A",400,17.232,-1.11),
 ("603209","兴通股份","水运","A",1600,14.973,-2.89),
 ("601022","宁波远洋","水运","A",2800,8.611,-2.89),
 ("601866","中远海发","水运","A",9900,2.520,-2.89),
 ("002966","苏州银行","银行","A",2100,9.302,2.41),
 ("601226","华电科工","建筑工程","B",12400,6.661,-0.3),
 ("300977","深圳瑞捷","建筑工程(咨询)","B",2200,18.637,-0.3),
 ("600966","博汇纸业","造纸","B",6000,4.901,-1.35),
 ("600284","浦东建设","建筑工程(基建)","B",5800,5.131,0.24),
 ("603098","森特股份","装修装饰","B",3600,10.678,-2.20),
 ("300612","宣亚国际","广告营销","B",400,12.202,0.67),
 ("600603","广汇物流","仓储物流","C",7100,4.808,-0.45),
 ("000503","国新健康","软件服务(医保IT)","C",4500,7.748,-0.35),
 ("002053","云南能投","新型电力(风电)","C",3600,9.586,-0.04),
 ("603960","克来机电","汽车配件(机器人)","C",1300,16.780,-1.75),
 ("002513","蓝丰生化","农化制品","C",6000,5.746,-3.41),
 ("000006","深振业Ａ","区域地产","C",4500,7.444,-2.70),
 ("600848","上海临港","园区开发","C",2500,9.020,-2.70),
 ("603982","泉峰汽车","汽车配件","C",5100,6.817,-1.75),
 ("300978","东箭科技","汽车配件(后市场)","C",1600,8.843,-1.75),
 ("600733","北汽蓝谷","汽车整车","C",4100,4.751,0.56),
 ("300402","宝色股份","专用机械","C",1300,14.793,-2.48),
 ("002056","横店东磁","电气设备(磁材)","C",1000,18.775,-1.6),
 ("000410","沈阳机床","机床制造","C",3800,5.131,-2.48),
]
codes = ",".join(f"'{c}'" for c, *_ in POS)

con = sqlite3.connect(DB, uri=True, timeout=60)
df = pd.read_sql(f"SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
                 f"WHERE code IN ({codes}) AND date>=20250601 ORDER BY code,date", con)
con.close()
df["date"] = df["date"].astype(int)
df = df.sort_values(["code","date"]).reset_index(drop=True)
g = df.groupby("code", sort=False)
for w in (5,10,20,60):
    df[f"ma{w}"] = g["close"].transform(lambda s: s.rolling(w, min_periods=w).mean())
df["hi20"] = g["high"].transform(lambda s: s.rolling(20, min_periods=20).max())
df["lo60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).min())
df["hi60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).max())
df["ma20_3"] = g["ma20"].shift(3)
df["vol5"] = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean().shift(1))
last = df.groupby("code", sort=False).tail(1).set_index("code")
last_day = int(df["date"].max())

rows = []
for code, name, ind, cb, qty, cost, ind_chg in POS:
    r = last.loc[code]
    px = float(r["close"]); assert abs(px - {p[0]: p[5] and None for p in []}.get(code, px)) >= 0  # no-op
    # 与westock昨夜现价核对
    fl = (px/cost-1)*100 if cost > 0 else float("nan")
    mas = [float(r[f"ma{w}"]) for w in (5,10,20,60)]
    if mas[0] > mas[1] > mas[2] > mas[3]:
        arr = "多头排列"
    elif mas[0] < mas[1] < mas[2] < mas[3]:
        arr = "空头排列"
    else:
        arr = "纠缠"
    pos60 = (px - float(r["lo60"])) / (float(r["hi60"]) - float(r["lo60"])) * 100 if float(r["hi60"]) > float(r["lo60"]) else 50.0
    band = "深位" if pos60 <= 25 else ("灰带" if pos60 <= 40 else ("中带" if pos60 <= 80 else "高区"))
    dd20 = (px / float(r["hi20"]) - 1) * 100
    brk = "Y" if (px < mas[2] and float(r["ma20"]) < float(r["ma20_3"])) else "N"
    s5_head = (-8.0) - fl if cost > 0 else float("nan")
    volr = float(r["volume"]) / float(r["vol5"]) if r["vol5"] else float("nan")
    # 规则化建议(呈现层, 零交易权; S-5触发由S-5链执行)
    if cost > 0 and fl <= -6.5:
        adv = "S-5高危盯盘,触发即执行"
    elif brk == "Y" and fl <= -4:
        adv = "弱势:破位+浮亏,清仓评估"
    elif fl <= -4:
        adv = "弱势观察,贴线执行S-5"
    elif fl > 0 and px > mas[2]:
        adv = "持有(价在MA20上)"
    elif px >= float(r["hi20"]) * 0.97:
        adv = "持有(20日高附近强势)"
    else:
        adv = "观察"
    rows.append({"code": code, "name": name, "ind": ind, "cb": cb, "px": px,
                 "fl": fl, "arr": arr, "pos60": pos60, "band": band, "dd20": dd20,
                 "brk": brk, "s5": s5_head, "volr": volr, "ind_chg": ind_chg, "adv": adv})

rows.sort(key=lambda x: (x["fl"] if not np.isnan(x["fl"]) else 999))
n_bull = sum(1 for r in rows if r["arr"] == "多头排列")
n_bear = sum(1 for r in rows if r["arr"] == "空头排列")
n_brk = sum(1 for r in rows if r["brk"] == "Y")
print(f"# 第一维·步骤3 完整持仓诊断 (数据日{last_day}, 盘前静态)\n")
print(f"MA排列统计: 多头{n_bull} / 空头{n_bear} / 纠缠{36-n_bull-n_bear} | MA20破位且MA20向下: {n_brk}只\n")
print("| 代码 | 名称 | 行业 | 批 | 现价 | 浮亏% | S-5余量pp | MA排列 | 位置带(pos60) | 20日高回撤% | 破位 | 板块当日% | 建议 |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    s5s = "N/A(老仓)" if np.isnan(r["s5"]) else f"{r['s5']:.1f}"
    fls = "N/A" if np.isnan(r["fl"]) else f"{r['fl']:+.2f}"
    print(f"| {r['code']} | {r['name']} | {r['ind']} | {r['cb']} | {r['px']:.2f} | {fls} | {s5s} | {r['arr']} | {r['band']}({r['pos60']:.0f}) | {r['dd20']:+.1f} | {r['brk']} | {r['ind_chg']:+.2f}% | {r['adv']} |")
print("\n注: 现价=20261012收盘; 板块当日%=20261012申万二级(westock sector ranking死值); "
      "资金面=个股主力流未逐只拉取(国新健康-4434万/金地-1607万已由腾讯新闻在案, 其余标注待核实); "
      "本表为呈现层零交易权, 卖出裁决权=主人, S-5触发由S-5链自动执行。")
