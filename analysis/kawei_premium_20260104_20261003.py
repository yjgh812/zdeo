# -*- coding: utf-8 -*-
"""卡位溢价检验: 同日涨停股, 首次封板时间越早 → 后续收益越高? (她「更早封板的那只」核心假设)
数据: 2024-01-04 同花顺涨停池43只(封板时刻已采集) + 本地qfq库fwd收益
分组: 早板(≤10:00) / 午前(10:00-11:30) / 午后(≥13:00)
"""
import sqlite3

import numpy as np

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
EVENT_DATE = 20240104

# 1/4涨停池: code, seal_time, lianban(连板数), reason_theme(首个标签)
POOL = [
    ("000564", "09:30", 2, "ST"), ("000635", "09:30", 1, "氢能源"),
    ("000820", "09:30", 3, "氢能源"), ("003015", "09:30", 2, "消费电子"),
    ("600589", "09:30", 5, "ST"), ("600805", "09:30", 2, "氢能源"),
    ("002888", "09:31", 2, "消费电子"), ("600593", "09:31", 2, "旅游"),
    ("600844", "09:31", 2, "煤化工"), ("603273", "09:31", 1, "次新+氢"),
    ("603356", "09:31", 1, "光伏"), ("603162", "09:33", 1, "航运次新"),
    ("002819", "09:34", 2, "华为"), ("002981", "09:35", 1, "消费电子"),
    ("603004", "09:35", 1, "化工次新"), ("603029", "09:36", 1, "农机"),
    ("605167", "09:36", 4, "氢能源"), ("605378", "09:36", 1, "电池"),
    ("001339", "09:37", 2, "华为"), ("000691", "09:39", 1, "化工"),
    ("002564", "09:40", 1, "ST"), ("002758", "09:42", 1, "供销社"),
    ("002178", "09:46", 1, "华为"), ("301337", "09:46", 1, "华为20cm"),
    ("603099", "09:46", 3, "旅游"), ("002024", "09:47", 1, "ST"),
    ("003029", "09:56", 1, "鸿蒙"), ("603280", "10:04", 1, "核电"),
    ("000078", "10:16", 1, "医药"), ("601096", "10:26", 1, "次新"),
    ("003025", "10:39", 1, "冷锻"), ("603052", "10:39", 1, "MR"),
    ("603048", "10:48", 1, "汽车"), ("603390", "11:03", 1, "华为"),
    ("601188", "13:02", 1, "龙字辈"), ("002529", "13:11", 1, "光伏"),
    ("000829", "13:18", 1, "华为"), ("603097", "13:25", 1, "电网"),
    ("002432", "13:42", 1, "医疗"), ("002786", "13:51", 1, "华为"),
    ("605080", "14:02", 1, "露营"), ("603908", "14:17", 1, "户外"),
    ("002780", "14:18", 1, "户外"),
]

conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)

results = []
for code, st, lb, theme in POOL:
    tbl = "sh" if code.startswith("6") else "sz"
    rows = conn.execute(
        f"SELECT date, close FROM stock_daily_meta_qfq "
        f"WHERE code='{code}' AND date>='{EVENT_DATE}' ORDER BY date LIMIT 25"
    ).fetchall()
    if len(rows) < 21 or rows[0][0] != str(EVENT_DATE):
        continue
    base = rows[0][1]
    f5 = (rows[5][1] / base - 1) * 100 if len(rows) > 5 else None
    f10 = (rows[10][1] / base - 1) * 100 if len(rows) > 10 else None
    f20 = (rows[20][1] / base - 1) * 100 if len(rows) > 20 else None
    hh = int(st[:2])
    bucket = "早板≤10点" if hh < 10 else ("午前10-12点" if hh < 13 else "午后≥13点")
    results.append((code, st, bucket, lb, f5, f10, f20))

conn.close()
print(f"有效: {len(results)}/{len(POOL)}")

from collections import defaultdict
groups = defaultdict(list)
for code, st, bk, lb, f5, f10, f20 in results:
    groups[bk].append((f5, f10, f20))

print("\n=== 卡位溢价: 封板时间 vs fwd收益 (2024-01-04涨停潮43只) ===")
for bk in ["早板≤10点", "午前10-12点", "午后≥13点"]:
    lst = groups.get(bk, [])
    if not lst:
        continue
    a5 = np.nanmean([x[0] for x in lst])
    a20 = np.nanmean([x[2] for x in lst])
    m20 = np.nanmedian([x[2] for x in lst])
    w20 = np.mean([1 if (x[2] or 0) > 0 else 0 for x in lst if x[2] is not None]) * 100
    print(f"{bk:12s} n={len(lst):2d}  fwd5={a5:+.1f}%  fwd20 mean={a20:+.1f}% med={m20:+.1f}% win={w20:.0f}%")

print("\n=== 全部43只按fwd20排序(最差前10) ===")
srt = sorted(results, key=lambda x: x[6] if x[6] is not None else -999)
for code, st, bk, lb, f5, f10, f20 in srt[:10]:
    print(f"  {code} {st} {bk:12s} {lb}板 fwd20={f20:+.1f}%")
