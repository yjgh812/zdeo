# -*- coding: utf-8 -*-
"""group_pnl_report.py — 每日分组汇报（主人令 2026-10-27 为期10天）

v2 2026-10-30（SOP一百八十六补记四·口径统一）:
  v1 问题: 九组硬编码仅覆盖约37只, 143只持仓中106只不在汇报。
  v2 主报告 = 台账 source 全持仓口径（executor_orders.jsonl 最新BUY行source字段;
     无source=早期单笔·来源未登记, 多台账对账定谳不猜测补录）, 组间均值降序。
  v2 附录 = 旧九组对照（10/27令原组语义, 至11/6期满）。
输出: 控制台 + data/group_pnl_report_YYYYMMDD.json
"""
from __future__ import annotations
import json, sys, os
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")

ROOT = r"D:\zcodesj"
LEDGER = os.path.join(ROOT, "data", "executor_orders.jsonl")

# 旧九组（10/27主人令原组语义; 组员=码表, 名称用westock真值）
NINE = [
    ("①HIGHWIN", ["300397", "300424", "605088", "300560", "300873", "001234", "603949"]),
    ("②超跌探底", ["600522", "000938", "603993", "603668"]),
    ("③低位启动", ["300059", "000100", "601601", "300380", "603638"]),
    ("④中枢对照", ["601166", "000560", "300017", "601288"]),
    ("⑤横盘者超跌", ["000582", "600359", "600108"]),
    ("⑥排雷双清", ["300110", "603636", "601899"]),
    ("⑦驱动力对垒", ["000498", "000011", "600887"]),
    ("⑧突破共振", ["000559", "603278", "002354"]),
    ("⑨替补", ["600547", "002241", "000725", "000007", "000029"]),
]


def load_source_map() -> dict:
    src = {}
    if not os.path.exists(LEDGER):
        raise RuntimeError(f"台账缺失: {LEDGER}")
    for ln in open(LEDGER, encoding="utf-8", errors="replace"):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("action") == "BUY" and r.get("source"):
            src[str(r.get("code", ""))[-6:]] = r["source"]
    return src


def main() -> int:
    from stop_loss_executor import _fetch_positions_via_mcp
    from _common import SOURCE_CN_NAMES  # 2026-11-01 根修复: 源→中文名单一真源(防口径漂移复发, 见_common头注)
    # 2026-11-01 主人令「把选股方式都记住, 每次每次都不知道」: 组名→人话定义唯一出处=data/group_dictionary.json,
    # 每组表头下自动打印一句解释, 汇报自解释不再需要主人记忆组名。
    try:
        GROUP_DICT = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "group_dictionary.json"),
                                    encoding="utf-8")).get("groups", {})
    except Exception as _e:
        print(f"[组名字典] 加载失败降级无解释: {_e}", file=sys.stderr)
        GROUP_DICT = {}
    pos = _fetch_positions_via_mcp()
    if pos is None:
        print("❌ westock持仓拉取失败, 结构化失败不输出伪数据")
        return 2
    held = {p["code"][-6:]: p for p in pos if p.get("quantity", 0) > 0}
    src_map = load_source_map()

    groups = defaultdict(list)
    unreg = []
    for p in pos:
        if p.get("quantity", 0) <= 0:
            continue
        c6 = p["code"][-6:]
        s = src_map.get(c6)
        if s:
            groups[s].append(p)
        else:
            unreg.append(p)

    now = datetime.now()
    report = {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"),
              "mode": "v2_source_full", "groups_source": {}, "groups_nine_appendix": {}}
    LINE = "─" * 58
    print(f"{'='*70}")
    print(f"  每日分组汇报 v2 | {now.strftime('%Y-%m-%d %H:%M')} | 共{len(held)}只")
    print(f"{'='*70}")

    for g in sorted(groups, key=lambda k: -sum(x["float_pnl_pct"] for x in groups[k])):
        items = sorted(groups[g], key=lambda x: -x["float_pnl_pct"])
        pnls = [x["float_pnl_pct"] for x in items]
        ups = sum(1 for x in pnls if x > 0)
        wr = ups / len(items)
        report["groups_source"][g] = {
            "n": len(items), "win": ups, "wr": round(wr, 3),
            "avg": round(sum(pnls) / len(pnls), 4),
            "cn": SOURCE_CN_NAMES.get(g, ""),  # 2026-11-01 双语名: 机器名+业务中文名
            "stocks": [{"name": x["name"], "code": x["code"], "pnl": x["float_pnl_pct"]} for x in items],
        }
        tag = "涨" if wr >= 0.6 else ("跌" if wr <= 0.4 else "平")
        cn_disp = SOURCE_CN_NAMES.get(g, "")
        cn_suffix = f"|{cn_disp}" if cn_disp else ""
        print(f"\n{LINE}\n【{g}{cn_suffix}】{len(items)}只 [{tag}] 胜率{wr:.0%} 均值{sum(pnls)/len(pnls):+.2f}%")
        print(f"  定义: {GROUP_DICT.get(g, '新组，定义待补进 data/group_dictionary.json')}")
        for x in items:
            m = "↑" if x["float_pnl_pct"] > 0 else ("↓" if x["float_pnl_pct"] < 0 else "→")
            print(f"  {m} {x['name']:<8}{x['float_pnl_pct']:+7.2f}%")

    if unreg:
        pnls = [x["float_pnl_pct"] for x in unreg]
        report["groups_source"]["early_single_unregistered"] = {
            "n": len(unreg),
            "win": sum(1 for p in pnls if p > 0),
            "wr": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
            "avg": round(sum(pnls) / len(pnls), 4),
            "cn": SOURCE_CN_NAMES.get("early_single_unregistered", ""),
            "stocks": [{"name": x["name"], "code": x["code"], "pnl": x["float_pnl_pct"]}
                       for x in sorted(unreg, key=lambda x: -x["float_pnl_pct"])],
            "note": "9/4-9/10早期kind=single买入, 多台账交叉对账无source, 不猜测补录",
        }
        print(f"\n{LINE}\n【早期单笔·来源未登记】{len(unreg)}只 均{sum(pnls)/len(pnls):+.2f}%")
        for x in sorted(unreg, key=lambda x: -x["float_pnl_pct"]):
            m = "↑" if x["float_pnl_pct"] > 0 else "↓"
            print(f"  {m} {x['name']:<8}{x['float_pnl_pct']:+7.2f}%")

    all_pnls = [x["float_pnl_pct"] for x in held.values()]
    total = {"n": len(all_pnls), "win": sum(1 for p in all_pnls if p > 0),
             "wr": round(sum(1 for p in all_pnls if p > 0) / len(all_pnls), 3) if all_pnls else 0,
             "avg": round(sum(all_pnls) / len(all_pnls), 4) if all_pnls else 0}
    report["total"] = total
    print(f"\n{'='*70}")
    print(f"  全持仓总计: {total['n']}只 涨{total['win']} 跌{total['n']-total['win']} "
          f"胜率{total['wr']:.0%} 均值{total['avg']:+.2f}%")
    print(f"{'='*70}")

    if any(c in held for _, ms in NINE for c in ms):
        print(f"\n{LINE}\n附录: 旧九组对照")
        for gname, ms in NINE:
            pnls, rows = [], []
            for c in ms:
                p = held.get(c)
                if p:
                    pnl = p["float_pnl_pct"]
                    pnls.append(pnl)
                    rows.append((("↑" if pnl > 0 else "↓"), p["name"], pnl))
            if pnls:
                wr = sum(1 for x in pnls if x > 0) / len(pnls)
                tag = "涨" if wr >= 0.6 else ("跌" if wr <= 0.4 else "平")
                report["groups_nine_appendix"][gname] = {
                    "n": len(pnls), "wr": round(wr, 3), "avg": round(sum(pnls) / len(pnls), 4)}
                print(f"  {gname} [{tag}] {len(pnls)}只 均值{sum(pnls)/len(pnls):+.2f}%  "
                      + " | ".join(f"{m}{nm}{v:+.1f}%" for m, nm, v in rows))
    out_path = os.path.join(ROOT, "data", f"group_pnl_report_{now.strftime('%Y%m%d')}.json")
    json.dump(report, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n产物: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
