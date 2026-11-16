#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
news_event_classifier.py — 新闻事件规则分类器 v1（过滤层影子件，纯离线）

定位（消息面融入选股 Phase 1b，主人方向令 2026-09-09）：
  对 news_pit 已存档新闻做 结构化分类（事件类型×方向×个股关联），
  为过滤层（负向排雷/正向确认）与方向层（板块映射）供数。
  影子运行：只落台账，不进任何信号/闸门/executor。

诚实边界（反臆想）：
  规则版=关键词命中，中文财经文本准确率未标注验证；每条输出命中词与
  hit_count 作为置信参考，无命中词→type=other/sentiment=neutral（如实）。
  LLM 增强分类留待后续（须密钥配置合规），本版可解释、可 PIT 复算。

数据流：
  python news_event_classifier.py --input <news_pit日文件.jsonl>   # 单日
  python news_event_classifier.py --all                            # 扫 data/news_pit 全部
  台账 data/news_events_classified.jsonl，幂等键=source+id（按 id UPSERT）

分类口径：
  type: announcement(公司公告)/policy(政策监管)/macro(宏观数据)/
        industry(行业产业)/geo(地缘)/other(未命中)
  sentiment: bullish/bearish/mixed/neutral
退出码：0 成功；2 输入不可用
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
NEWS_DIR = ROOT / "data" / "news_pit"
LEDGER = ROOT / "data" / "news_events_classified.jsonl"

BULL = ["中标", "回购", "增持", "预增", "扭亏", "涨价", "上调", "签订", "签署", "合作",
        "获批", "突破", "增长", "新高", "利好", "收购", "扩产", "订单", "复产", "提价",
        "涨价函", "批量交付", "中标公告", "超预期"]
BEAR = ["减持", "解禁", "处罚", "立案", "警示", "诉讼", "预亏", "下滑", "下调", "终止",
        "失败", "违规", "冻结", "利空", "退市", "亏损", "降价", "停产", "事故", "爆炸",
        "问询", "警示函", "调查"]
TYPE_RULES = [
    ("announcement", ["公告", "披露", "回购", "增持", "减持", "中标", "预增", "预亏",
                      "年报", "中报", "半年报", "季报", "分红", "停牌", "复牌", "重组",
                      "收购", "定增", "拟发行"]),
    ("policy", ["证监会", "央行", "人民银行", "国务院", "发改委", "工信部", "财政部",
                "金融监管总局", "住建部", "部委", "通知", "指导意见", "管理办法", "征求意见",
                "政策", "规划", "会议决定", "条例"]),
    ("macro", ["CPI", "PPI", "GDP", "PMI", "非农", "社融", "M2", "美联储", "加息", "降息",
               "国债", "汇率", "失业率", "通胀", "逆回购", "中期借贷便利", "买断式逆回购"]),
    ("geo", ["冲突", "制裁", "袭击", "空袭", "谈判", "地缘", "停火", "军演", "导弹", "无人机"]),
    ("industry", ["涨价", "期货", "产量", "库存", "出货", "需求", "景气", "出厂价",
                  "出口", "进口", "产能", "现货", "期货收盘", "交付", "发布", "推出"]),
]
TYPE_PRIORITY = {t: i for i, (t, _) in enumerate(TYPE_RULES)}


def classify_text(text: str) -> dict:
    bull = [w for w in BULL if w in text]
    bear = [w for w in BEAR if w in text]
    if bear and bull:
        sentiment, conf = "mixed", max(len(bull), len(bear))
    elif bear:
        sentiment, conf = "bearish", len(bear)
    elif bull:
        sentiment, conf = "bullish", len(bull)
    else:
        sentiment, conf = "neutral", 0
    hits = {}
    for t, kws in TYPE_RULES:
        n = sum(1 for w in kws if w in text)
        if n:
            hits[t] = n
    if hits:
        ntype = sorted(hits.items(), key=lambda kv: (-kv[1], TYPE_PRIORITY[kv[0]]))[0][0]
    else:
        ntype = "other"
    # v2 判定域限制（2026-09-09 抽检37条实证: v1方向准确率≈51%, bearish精确率4/13,
    # 误报9条中8条type∈geo/other=灾害/国际政治类字面噪声）→ geo/other 不判方向，
    # sentiment 强制 neutral（域外不判，如实标注）， announcement/industry/policy/macro 保留。
    domain_note = None
    if ntype in ("geo", "other") and sentiment != "neutral":
        domain_note = f"v2域外不判(原判{sentiment},词{','.join((bull + bear)[:3])})"
        sentiment = "neutral"
    return {"type": ntype, "sentiment": sentiment, "hit_conf": conf,
            "hit_bull": bull[:6], "hit_bear": bear[:6], "type_hits": hits,
            "domain_note": domain_note}


def upsert(records: list) -> int:
    rows = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    rows[(r.get("source"), r.get("id"))] = r
                except json.JSONDecodeError:
                    print("[WARN] 台账坏行跳过", file=sys.stderr)
    for r in records:
        rows[(r["source"], r["id"])] = r
    ordered = sorted(rows.values(), key=lambda r: (r.get("date", ""), r.get("id", 0)))
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in ordered) + "\n",
                   encoding="utf-8")
    tmp.replace(LEDGER)
    return len(ordered)


def main() -> int:
    p = argparse.ArgumentParser(description="新闻事件规则分类器 v1（影子）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--input", help="单日 news_pit JSONL 文件")
    g.add_argument("--all", action="store_true", help="扫描 data/news_pit 全部日文件")
    args = p.parse_args()

    files = sorted(NEWS_DIR.rglob("*.jsonl")) if args.all else [Path(args.input)]
    files = [f for f in files if f.exists()]
    if not files:
        print("[FAIL] 无输入文件", file=sys.stderr)
        return 2

    records, no_id = [], 0
    for f in files:
        date = f.stem.replace("news_", "")
        date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if raw.get("id") in (None, ""):
                no_id += 1
                continue
            text = f'{raw.get("title") or ""} {raw.get("content") or ""}'
            cls = classify_text(text)
            records.append({
                "date": date_fmt, "source": raw.get("source", "cls_telegraph"),
                "id": raw["id"], "ctime": raw.get("ctime"),
                "level": raw.get("level"), "type": cls["type"],
                "sentiment": cls["sentiment"], "hit_conf": cls["hit_conf"],
                "hit_bull": cls["hit_bull"], "hit_bear": cls["hit_bear"],
                "type_hits": cls["type_hits"],
                "stocks": raw.get("stocks", []),
                "subjects": raw.get("subjects", []),
                "classifier": "rule_v2_domain_limited",
                "domain_note": cls.get("domain_note"),
                "classified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

    total = upsert(records)
    from collections import Counter
    tc = Counter(r["type"] for r in records)
    sc = Counter(r["sentiment"] for r in records)
    print(f"[OK] 分类 {len(records)} 条（无id跳过{no_id}），台账共 {total} 条")
    print(f"  类型分布: {dict(tc)}")
    print(f"  方向分布: {dict(sc)}")
    print("  ⚠️ rule_v2_domain_limited：geo/other不判方向；抽检基线=v1方向准确率51%/bearish精确率31%（37条人工判读），v2待复检")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
