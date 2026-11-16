# -*- coding: utf-8 -*-
"""
存量涨停特征回补（2026-08-18）
为已有 limit_up_patterns 的 feature_tags 补 seal_amt_ratio + afternoon_board
"""
import sqlite3, json, re

DB = r"D:/zcodesj/attribution/attribution.db"
PAT_SEAL = re.compile(r"封板([\d.]+)亿")

con = sqlite3.connect(DB)

def parse_seal_amt(seal_status):
    """从 '首封09:25 封板1.16亿 0炸' 提取封板金额(元)"""
    if not seal_status:
        return None
    m = PAT_SEAL.search(seal_status)
    if m:
        return float(m.group(1)) * 1e8
    return None

rows = con.execute(
    "SELECT id, seal_status, turnover, first_limit_time, feature_tags FROM limit_up_patterns "
    "WHERE pattern_type IN ('engine_v2','review_v2') AND feature_tags IS NOT NULL"
).fetchall()

print(f"待处理: {len(rows)} 行")
ok = skipped = 0
for rid, seal_status, turnover, first_limit_time, tags_json in rows:
    tags = json.loads(tags_json)
    # 已有则不重复覆盖
    if "seal_amt_ratio" in tags:
        skipped += 1
        continue
    # 解析封板金额
    seal_amt = parse_seal_amt(seal_status)
    if seal_amt and turnover and turnover > 0:
        tags["seal_amt_ratio"] = round(seal_amt / turnover, 3)
    else:
        tags["seal_amt_ratio"] = None
    # 午后板标记
    tags["afternoon_board"] = (first_limit_time or "") >= "130000"
    # UPDATE
    con.execute(
        "UPDATE limit_up_patterns SET feature_tags=? WHERE id=?",
        (json.dumps(tags, ensure_ascii=False), rid))
    ok += 1

con.commit()
con.close()
print(f"更新: {ok} 行, 跳过(已有): {skipped} 行")
