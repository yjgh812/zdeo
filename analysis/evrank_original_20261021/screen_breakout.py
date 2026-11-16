# -*- coding: utf-8 -*-
"""底部突破筛选(只读): 全宇宙, 60日平台→近5日放量突破上沿→站稳. 输出按突破质量排序."""
import sqlite3
import pandas as pd

QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
MAP = r"file:D:\zcodesj\data\industry_group_map_snapshot.db?mode=ro"
EXCLUDE = {
    "002292", "601939",
    "003006", "601919", "300817", "600035", "600018", "301370", "600626",
    "603209", "601866", "002966", "600284", "603098", "600603", "000503",
    "002053", "600000", "600380", "605338", "001287", "300364", "301500",
}
ASOF = "20261020"
PLATFORM_DAYS, PLATFORM_AMP = 50, 0.20     # 平台: 前50日振幅<=20%
BREAK_WINDOW = 5                            # 近5日内突破
BREAK_MARGIN = 1.02                         # 突破=收盘>平台上沿*1.02
VOL_MULT = 1.5                              # 突破量>=平台均量*1.5

con_m = sqlite3.connect(MAP, uri=True)
mem = pd.read_sql("SELECT code, name, l1 FROM l1_member WHERE ambiguous_flag=0", con_m)
con_m.close()
mem = mem[~mem["code"].isin(EXCLUDE)]
mem = mem[~mem["name"].str.contains("ST", na=False)]
mem = mem[~mem["code"].str.startswith(("688", "689", "8", "4", "9"))]
codes = mem["code"].tolist()
print(f"入池={len(codes)}")

con = sqlite3.connect(QFQ, uri=True)
ph = ",".join("?" * len(codes))
df = pd.read_sql(
    f"SELECT code, date, close, volume FROM stock_daily_meta_qfq "
    f"WHERE code IN ({ph}) AND date>='20250301' ORDER BY code, date", con, params=codes)
con.close()

out = []
for code, g in df.groupby("code"):
    if len(g) < PLATFORM_DAYS + BREAK_WINDOW + 10:
        continue
    c = g["close"].tolist(); v = g["volume"].tolist(); dts = g["date"].tolist()
    i = len(c) - 1
    if c[i] > 30:
        continue
    # 平台=突破窗口之前的50日
    plat_end = i - BREAK_WINDOW
    plat = c[plat_end - PLATFORM_DAYS:plat_end]
    if len(plat) < PLATFORM_DAYS:
        continue
    hi, lo = max(plat), min(plat)
    if lo <= 0 or hi / lo - 1 > PLATFORM_AMP:
        continue
    upper = hi * BREAK_MARGIN
    # 近5日内出现放量突破: 收盘>upper 且 当日量>=平台均量*1.5
    plat_vol = sum(v[plat_end - PLATFORM_DAYS:plat_end]) / PLATFORM_DAYS
    broke = False
    for t in range(i - BREAK_WINDOW + 1, i + 1):
        if c[t] > upper and v[t] >= plat_vol * VOL_MULT:
            broke = True
            break
    if not broke:
        continue
    # 站稳: 现价不低于平台上沿(回落但未破位, 容忍至 upper*0.99)
    if c[i] < upper * 0.99:
        continue
    brk_day_gain = c[i] / upper - 1
    vol_ratio_now = v[i] / plat_vol
    out.append(dict(code=code, close=c[i], upper=round(upper, 2), brk_gain=brk_day_gain,
                    vol_x=round(vol_ratio_now, 2), date=dts[i],
                    ret5=c[i] / c[i - BREAK_WINDOW] - 1))
res = pd.DataFrame(out)
if len(res):
    mem2 = mem.set_index("code")
    res["name"] = res["code"].map(mem2["name"])
    res["l1"] = res["code"].map(mem2["l1"])
    res = res.sort_values(["vol_x", "brk_gain"], ascending=False)
    cols = ["code", "name", "l1", "close", "upper", "brk_gain", "ret5", "vol_x"]
    pd.set_option("display.width", 240, "display.float_format", lambda x: f"{x:.3f}")
    print(f"底部突破入选={len(res)}")
    print(res[cols].head(25).to_string(index=False))
    res.to_json("breakout_list.json", orient="records", force_ascii=False, indent=1)
else:
    print("无符合标的")
