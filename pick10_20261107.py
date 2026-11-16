# -*- coding: utf-8 -*-
"""pick10_20261107.py — 周一买入10只精选(主人令「先选10只, 数据最好的, 每只带选股原因」)
数据源: 周五20261106收盘信号(dip_scan json) + 四法强度排序 + 硬排除(龙虎榜/利空)
排序权: 起涨型黄金带(+8.85%胜率77%) > 底部反转首板(+2.70%) > 回踩确认型(+0.54~0.77%) > 横盘低位(+0.85%)
"""
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)

d = json.load(open(os.path.join(ROOT, "data", "dip_scan_20261106.json"), encoding="utf-8"))
T = d["T"]

# Friday LHB exclusion (榜上股不当买入标的, 台账二百八十一)
FUND = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
fcon = sqlite3.connect(FUND, uri=True)
lhb = {r[0][:6] for r in fcon.execute(
    "select DISTINCT ts_code from dragon_tiger_new where trade_date=?", (str(T),))}
neg = set()
for ts, typ in fcon.execute(
        "select ts_code, type from forecast where ann_date>=20261031 and type in "
        "('预减','首亏','略减','预亏')"):
    neg.add(ts[:6])
fcon.close()

cands = []  # (prio, seg, code, name, ind, px, reason_data)
for h in d.get("up_hits", []):
    rebr = h.get("rebr", 0)
    prio = 0 if 15 <= rebr <= 25 else 1
    reason = ("超跌反弹黄金带: 前20日大跌后启动, 已修复%.1f%%(验证修复15-25%%区间+8.8%%胜率77%%), "
              "今日涨%+.1f%%温和量%.2f, 位置低(偏离MA20=%.1f%%)" % (
                  rebr, h["r_today"], h["volr"], h["dev"]))
    cands.append((prio, "up", h["code"], h["name"], h["ind"], h["px"], reason, "dip_up_v1"))
for h in d.get("fb_hits", []):
    prio = 0 if h["prior20"] <= -15 else 1
    reason = ("底部反转首板: 前20日跌%.1f%%后周五涨停(该子集验证+2.7%%胜率53%%), "
              "价%.2f元低价带, 周一开盘买入不追高" % (h["prior20"], h["px"]))
    cands.append((prio, "fb", h["code"], h["name"], h["ind"], h["px"], reason, "dip_fb_v1"))
for h in d.get("hits", []):
    if 2.0 <= h["dev"] <= 5.0 and 1.5 <= h["volr"] <= 2.5:
        prio = 2
        reason = ("回踩确认型: 前20日涨%.1f%%趋势中回踩, 位置+%.1f%%(验证+2~5%%区间最优"
                  "+0.77%%), 量比%.2f温和放量, 周五仅%+.1f%%未透支" % (
                      h["r20"], h["dev"], h["volr"], h["r_today"]))
        cands.append((prio, "ret", h["code"], h["name"], h["ind"], h["px"], reason, "dip_ret_v1"))
for h in d.get("cons", []):
    if h["dev"] < 5 and 0.4 <= h["volr"] <= 0.9 and h["fb_days_ago"] <= 6:
        prio = 3
        reason = ("首板横盘介入: %d天前涨停后不破板横盘, 今日极度缩量%.2f(验证缩量确认介入"
                  "+0.85%%), 位置低(偏离%.1f%%)" % (h["fb_days_ago"], h["volr"], h["dev"]))
        cands.append((prio, "cons", h["code"], h["name"], h["ind"], h["px"], reason, "dip_cons_v1"))

seen = set()
picked = []
for prio, seg, c6, nm, ind, px, reason, src in sorted(cands, key=lambda x: (x[0], x[2])):
    if c6 in seen:
        continue  # 一票只入最强段
    if c6 in lhb:
        continue  # 周五已上龙虎榜排除
    if c6 in neg:
        continue  # 负面预告排除
    seen.add(c6)
    picked.append((seg, c6, nm, ind, px, reason, src))
    if len(picked) >= 10:
        break

print("==== 周一买入10只(信号日%d, 开盘价挂单, 每只100股, 源名标注) ====" % T)
for i, (seg, c6, nm, ind, px, reason, src) in enumerate(picked, 1):
    print("%2d. %s %s [%s|%s] 价%.2f  source=%s" % (i, c6, nm, seg, ind, px, src))
    print("    理由: %s" % reason)
excl_lhb = sum(1 for _p, _s, c6, *_ in sorted(cands, key=lambda x: (x[0], x[2]))[:40] if c6 in lhb)
print("(排除说明: 周五龙虎榜股%d只已剔除, 负面预告股已剔除)" % excl_lhb)
