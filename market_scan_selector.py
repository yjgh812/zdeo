#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_scan_selector.py — 轨道B：全市场资金动量选股（唯一 owner）

双轨实验轨道B（PREREG_DUAL_TRACK_20260904）：与轨道A（bp_ep框架复刻,
prereg_stock_selector.py）分仓对照。贴合2026 regime（主人9/3实证中小盘
弹性>大盘），无历史回测，说话权靠双轨对照实验。

规则链（fail-closed，缺数据 exit 2）:
  扫描文件(全市场初筛) → 剔ST/科创/北交所/涨停 → 剔排除清单
  → qfq直连算动量: 20日涨幅≥30%剔(R32预筛) / 距MA20≥20%剔(R31预筛)
  → 板块5日主力净流入>0(code覆盖优先,行业文字映射fallback,与prereg_stock_selector共享映射)
  → 行业分散(同industry文字组取综合分最高) → 按板块5d资金×量比排序取N

诚实边界: 个股10日主力资金流不在本地库,由调用方经MCP复核后在台账标注;
名称校验/闸门(R1-R19/R22)仍由调用方执行前跑,本脚本只做名单侧筛选。

用法:
  python market_scan_selector.py --scan-file <scan.json> \
      --industry-file <code_industry.json> --sector-flow-file <sector5d.json> \
      --exclude-codes 600000,... --n 5 [--industry-map-file <json>] [--out <json>]

scan.json item: {"symbol","name","latest_price","volume_ratio","total_market_cap","change_pct"}
industry-file: {"600827": "超市连锁", ...}  (行业文字,用于板块映射与分散分组)
验收锚: 20260904 重放=汉钟精机/游族网络/锦江航运/出版传媒/锦江在线。
"""
import argparse
import json
import sqlite3
import sys

from prereg_stock_selector import INDUSTRY_TO_SECTOR, CODE_SECTOR_OVERRIDE

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
# 东财/常用行业文字 → 板块组(与prereg_stock_selector板块键一致), 未命中fail-closed
SCAN_INDUSTRY_MAP = {
    "游戏": "游戏Ⅱ", "出版": "出版",
    "通用设备": "通用设备", "仪器仪表": "通用设备",
    "航运港口": "航运港口", "铁路公路": "铁路公路", "物流": "物流",
    "一般零售": "一般零售", "百货零售": "一般零售",
    "钢铁": "特钢Ⅱ", "特钢": "特钢Ⅱ", "普钢": "普钢",
    "汽车零部件": "汽车零部件", "化学原料": "化学原料", "化纤": "化学纤维",
    "服装家纺": "服装家纺", "白色家电": "白色家电", "包装印刷": "包装印刷",
    "种植业": "种植业", "保险": "保险Ⅱ", "银行": "银行",
    "房地产开发": "房地产开发", "旅游及景区": "旅游及景区", "酒店餐饮": "酒店餐饮",
    "影视院线": "影视院线", "数字媒体": "数字媒体", "通信服务": "通信服务",
    "医药商业": "医药商业", "汽车服务": "汽车服务", "文娱用品": "文娱用品",
    "油服工程": "油服工程", "航空机场": "航空机场", "环保": "环境治理",
    "中药生产": "中药Ⅱ", "电力": "电力", "煤炭开采": "煤炭开采",
}


def fail(msg):
    print(json.dumps({"error": msg}, ensure_ascii=False), file=sys.stderr)
    sys.exit(2)


def qfq_momentum(code):
    """直连qfq库算 (r20, vs_ma20)；数据不足返回None。"""
    con = sqlite3.connect(QFQ_DB)
    rows = con.execute(
        "select close from stock_daily_meta_qfq where code=? order by date desc limit 21",
        (code,)).fetchall()
    con.close()
    if len(rows) < 21:
        return None
    closes = [r[0] for r in rows]
    r20 = (closes[0] / closes[20] - 1) * 100
    ma20 = sum(closes[:20]) / 20
    return r20, (closes[0] / ma20 - 1) * 100


def limit_pct(symbol):
    return 19.8 if symbol.startswith("30") else 9.8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan-file", required=True)
    ap.add_argument("--industry-file", required=True)
    ap.add_argument("--sector-flow-file", required=True)
    ap.add_argument("--exclude-codes", default="")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--max-price", type=float, default=30.0)
    ap.add_argument("--min-cap", type=float, default=3e9)
    ap.add_argument("--max-cap", type=float, default=5e10)
    ap.add_argument("--industry-map-file", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    try:
        scan = json.load(open(args.scan_file, encoding="utf-8"))
        industries = json.load(open(args.industry_file, encoding="utf-8"))
        sector_flow = json.load(open(args.sector_flow_file, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        fail(f"输入文件不可读: {e}")
    if not scan:
        fail("扫描文件为空")

    exclude = {c.strip()[-6:] for c in args.exclude_codes.split(",") if c.strip()}

    audit, candidates = [], []
    for it in scan:
        sym = str(it["symbol"])
        code = sym[-6:]
        name, price = it["name"], float(it["latest_price"])
        vr, cap, chg = float(it["volume_ratio"]), float(it["total_market_cap"]), float(it["change_pct"])
        rec = {"code": code, "name": name, "price": price, "volume_ratio": vr}

        if "ST" in name.upper():
            audit.append({**rec, "dropped": "ST"})
            continue
        if sym.startswith(("688", "689")):
            audit.append({**rec, "dropped": "科创板"})
            continue
        if sym.startswith(("92", "43", "83", "87")):
            audit.append({**rec, "dropped": "北交所"})
            continue
        if code in exclude:
            audit.append({**rec, "dropped": "持仓/已排程排除清单"})
            continue
        if price > args.max_price:
            audit.append({**rec, "dropped": f"价格{price}>{args.max_price}"})
            continue
        if not (args.min_cap <= cap <= args.max_cap):
            audit.append({**rec, "dropped": f"市值{cap/1e8:.0f}亿越界"})
            continue
        if chg >= limit_pct(sym):
            audit.append({**rec, "dropped": f"当日涨停{chg}%(R6不可低吸)"})
            continue

        mom = qfq_momentum(code)
        if mom is None:
            fail(f"qfq动量数据不足: {code} {name}")
        r20, vs_ma20 = mom
        rec["r20"], rec["vs_ma20"] = round(r20, 2), round(vs_ma20, 2)
        if r20 >= 30:
            audit.append({**rec, "dropped": f"R32预筛:20日涨幅{r20:.1f}%≥30%"})
            continue
        if vs_ma20 >= 20:
            audit.append({**rec, "dropped": f"R31预筛:偏离MA20 {vs_ma20:.1f}%≥20%"})
            continue

        ind_text = industries.get(code)
        if ind_text is None:
            fail(f"行业缺失: {code} {name} — 补industry-file后重跑")
        group_map = {**INDUSTRY_TO_SECTOR, **SCAN_INDUSTRY_MAP}
        if args.industry_map_file:
            group_map.update(json.load(open(args.industry_map_file, encoding="utf-8")))
        sector = CODE_SECTOR_OVERRIDE.get(code, group_map.get(ind_text))
        if sector is None:
            fail(f"行业映射缺失: {code} {name} industry={ind_text} — 扩SCAN_INDUSTRY_MAP/--industry-map-file")
        if sector not in sector_flow:
            fail(f"板块5d资金缺失: {sector}({code}) — 补sector-flow-file")
        flow5d = float(sector_flow[sector])
        rec["industry"], rec["sector"], rec["sector5d_wan"] = ind_text, sector, flow5d
        if flow5d <= 0:
            audit.append({**rec, "dropped": f"板块5日净流出({sector} {flow5d:.0f}万)"})
            continue
        rec["score"] = flow5d * vr  # 板块资金强度×个股量比
        candidates.append(rec)

    by_group = {}
    for rec in candidates:
        by_group.setdefault(rec["industry"], []).append(rec)
    survivors = sorted((max(g, key=lambda x: x["score"]) for g in by_group.values()),
                       key=lambda x: -x["score"])
    for rec in candidates:
        if rec not in survivors:
            audit.append({**rec, "dropped": f"行业分散让位(同{rec['industry']}组score更低)"})

    selected = survivors[: args.n]
    result = {
        "scan_file": args.scan_file,
        "rule": "market_scan_selector v1 (轨道B:全市场→剔ST/科创/北交/涨停/排除→R31R32预筛→板块5d净流入正→行业分散→取N)",
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
