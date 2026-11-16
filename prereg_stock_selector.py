#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prereg_stock_selector.py — 名单→N只 选股规则唯一 owner（宪法§5收敛）

背景(20260904): 对话选股3轮未沿用最新prereg规则(violation_log type=constitution),
根因之一=选股规则无代码owner,执行包生成与对话入口各自为政。本脚本收敛之:
执行包生成器与对话选股一律调用本脚本,规则参数以最新prereg为准。

规则链(fail-closed, 步骤缺数据即exit 2,禁止静默跳过):
  Top50名单 → 剔除>max_price → 剔除exclude_codes(持仓/已排程/裁决)
  → 剔除exclude_industries(如银行=主人20260903裁决)
  → 板块5日主力净流入>0(逐票,code覆盖优先,industry文字映射fallback)
  → 行业分散(名单industry文字同组取rank最小) → 取N,不足如实报short

checklist三道核对与闸门不在本脚本内(由调用方执行前跑 pre_pick_checklist /
pre_trade_check_v2 / trade_executor),本脚本只负责名单侧筛选。

用法:
  python prereg_stock_selector.py --list-file <名单csv> --n 5 \
      --exclude-codes 600000,601169,... --exclude-industries 银行 \
      --sector-flow-file data/sector5d_YYYYMMDD.json [--max-price 30] \
      [--industry-map-file <json>] [--out <result.json>]

输出: stdout/文件 JSON {"selected":[...], "short":n, "audit":[逐票淘汰原因]}
验收锚: 9/3重放=批次A五只; 9/4实跑=百联+中原高速2只short3。
"""
import argparse
import csv
import json
import sys

# 名单industry文字 → 申万板块组(westock data_sector mainNetInflow5d 键名)
# 依据批次A实践(PREREG_BPEP_BATCH_20260904_V2), 新文字出现时脚本fail-closed报错,扩表后重跑
INDUSTRY_TO_SECTOR = {
    "百货": "一般零售", "超市连锁": "一般零售",
    "路桥": "铁路公路", "港口": "航运港口", "仓储物流": "物流",
    "中成药": "中药Ⅱ", "生物制药": "生物制品", "纺织": "纺织制造",
    "环境保护": "环境治理", "火力发电": "电力",
    "区域地产": "房地产开发", "园区开发": "房地产开发",
    "建筑工程": "房屋建设Ⅱ",
    "银行": "银行",
}
# 个股级板块覆盖(名单industry文字与其申万二级不一致者)
CODE_SECTOR_OVERRIDE = {
    "601186": "基础建设",  # 中国铁建:预注册归基础建设5d净流出批次(a9d610c)
    "601390": "基础建设",  # 中国中铁:同上,名单文字"建筑工程"与其二级板块不符
    "000498": "基础建设",  # 山东路桥:装饰工程文字下实为基础建设
    "601117": "专业工程",  # 中国化学:化学工程实为专业工程
}


# 行业分散组(批次A按申万一级粗分组; 默认=industry文字, 文字不同但同一级者在此归组)
INDUSTRY_GROUP = {
    "百货": "一般零售", "超市连锁": "一般零售",
}

def fail(msg):
    print(json.dumps({"error": msg}, ensure_ascii=False), file=sys.stderr)
    sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-file", required=True)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--exclude-codes", default="")
    ap.add_argument("--exclude-industries", default="")
    ap.add_argument("--sector-flow-file", required=True)
    ap.add_argument("--max-price", type=float, default=30.0)
    ap.add_argument("--industry-map-file", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    industry_map = dict(INDUSTRY_TO_SECTOR)
    if args.industry_map_file:
        industry_map.update(json.load(open(args.industry_map_file, encoding="utf-8")))

    try:
        rows = list(csv.DictReader(open(args.list_file, encoding="utf-8-sig")))
    except OSError as e:
        fail(f"名单文件不可读: {e}")
    if not rows:
        fail("名单为空")
    for col in ("code", "rank", "name", "industry", "close"):
        if col not in rows[0]:
            fail(f"名单缺列: {col}")

    try:
        sector_flow = json.load(open(args.sector_flow_file, encoding="utf-8"))
    except OSError as e:
        fail(f"板块资金文件不可读: {e}")

    exclude_codes = {c.strip()[-6:] for c in args.exclude_codes.split(",") if c.strip()}
    exclude_industries = {c.strip() for c in args.exclude_industries.split(",") if c.strip()}

    audit, candidates = [], []
    for r in rows:
        code, name = r["code"][-6:], r["name"].replace("Ａ", "A")
        rank, ind_text = int(r["rank"]), r["industry"]
        close = float(r["close"])
        rec = {"code": code, "name": name, "rank": rank, "industry": ind_text}

        if close > args.max_price:
            audit.append({**rec, "dropped": f"价格{close}>max_price{args.max_price}"})
            continue
        if ind_text in exclude_industries:
            audit.append({**rec, "dropped": f"裁决剔除行业:{ind_text}"})
            continue
        if code in exclude_codes:
            audit.append({**rec, "dropped": "持仓/已排程排除清单"})
            continue

        sector = CODE_SECTOR_OVERRIDE.get(code, industry_map.get(ind_text))
        if sector is None:
            fail(f"行业映射缺失: {code} {name} industry={ind_text} — 扩INDUSTRY_TO_SECTOR后重跑")
        if sector not in sector_flow:
            fail(f"板块5日资金数据缺失: {sector}({code} {name}) — 补sector-flow-file后重跑")
        flow5d = float(sector_flow[sector])
        rec["sector"], rec["sector5d_wan"] = sector, flow5d
        if flow5d <= 0:
            audit.append({**rec, "dropped": f"板块5日主力净流出({sector} {flow5d:.0f}万)"})
            continue
        candidates.append(rec)

    # 行业分散: 同分散组取rank最小
    by_group = {}
    for rec in candidates:
        by_group.setdefault(INDUSTRY_GROUP.get(rec["industry"], rec["industry"]), []).append(rec)
    survivors = sorted((min(g, key=lambda x: x["rank"]) for g in by_group.values()),
                       key=lambda x: x["rank"])
    for rec in candidates:
        if rec not in survivors:
            audit.append({**rec, "dropped": f"行业分散让位(同{INDUSTRY_GROUP.get(rec['industry'], rec['industry'])}组rank更小者保留)"})

    selected = survivors[: args.n]
    result = {
        "list_file": args.list_file,
        "rule": "prereg_stock_selector v1 (Top50→剔持仓/排程/裁决→板块5d净流入为正→行业分散→取N)",
        "selected": selected,
        "short": max(args.n - len(selected), 0),
        "audit": audit,
    }
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
