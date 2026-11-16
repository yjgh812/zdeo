#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屠龙系统·主线预警雷达 (Mainline Radar)

功能：盘前输入昨夜催化信号 → 自动匹配涨停范式库 → 输出次日主线候选清单。
目标：解决"主线踏空"问题(8/12通信设备+130亿主力净流入但模拟盘未主动布局)。

数据流：
    催化信号(外盘/商品/政策/事件)
        → detect_themes() 主题识别(关键词匹配 CATALYST_THEME_MAP)
        → query_patterns() 范式库召回(predictability≥阈值)
        → 输出主线候选(主题/催化强度/范式龙头/可预判性/策略参考)

用法：
    # 方式1: 命令行直接输入催化描述
    python mainline_radar.py --catalyst "昨晚美股Lumentum涨20%,韩股SK海力士涨8%,碳酸锂期货涨3%"

    # 方式2: 从JSON文件读取多条催化(适合定时任务生成)
    python mainline_radar.py --catalyst-file catalyst_today.json

    # 方式3: 自动获取昨夜外盘(待接入akshare,第一版回退手动)
    python mainline_radar.py --auto

范式库: D:\\zcodesj\\attribution\\attribution.db -> limit_up_patterns 表
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attribution", "attribution.db")

# ===== 催化-主题映射表（基于涨停范式库6大主线提炼，持续迭代）=====
# 每个主题: catalyst_keywords(催化信号关键词) + pattern_keywords(范式库匹配词)
#           catalyst_strength(催化强度权重) + overnight_sources(隔夜外盘数据源,供auto用)
CATALYST_THEME_MAP = {
    "光通信/CPO/AI算力": {
        "catalyst_keywords": [
            "光通信", "光模块", "CPO", "Lumentum", "Coherent", "光芯片",
            "英伟达", "NVIDIA", "GPU", "算力", "PCB", "覆铜板",
            "SK海力士", "三星", "HBM", "半导体", "SOX", "费城半导体",
        ],
        "pattern_keywords": ["光通信", "CPO", "PCB", "英伟达", "AI算力", "光模块", "通信", "覆铜板"],
        "catalyst_strength_base": 8.0,  # 外部催化驱动型，基础强度高
        "overnight_sources": {
            "indices": ["100.NDX", "100.SPX"],          # 纳斯达克/标普
            "us_stocks": ["NVDA", "LITE", "COHR", "AMD", "ASML"],  # 英伟达/Lumentum/Coherent/AMD/ASML
        },
        "notes": "外部催化映射型，看昨晚美股光通信/半导体龙头涨幅",
    },
    "人形机器人": {
        "catalyst_keywords": [
            "机器人", "人形", "Optimus", "宇树", "Figure", "特斯拉机器人",
            "减速器", "RV", "谐波", "灵巧手", "GR00T", "电子皮肤",
        ],
        "pattern_keywords": ["人形机器人", "减速器", "RV", "谐波", "灵巧手", "电子皮肤", "机器人"],
        "catalyst_strength_base": 6.5,
        "overnight_sources": {"events": ["特斯拉Optimus动态", "宇树/优必选IPO", "Figure更新"]},
        "notes": "事件驱动型，关注IPO/发布会/量产进展",
    },
    "AI应用": {
        "catalyst_keywords": [
            "AI应用", "智能体", "Agent", "AIGC", "大模型", "OpenAI",
            "软件", "十五五", "教育AI", "医疗AI", "AI+",
        ],
        "pattern_keywords": ["AI应用", "智能体", "AIoT", "AIGC", "AI医疗", "AI+"],
        "catalyst_strength_base": 6.0,
        "overnight_sources": {"events": ["OpenAI发布", "上海/北京AI政策", "教育/医疗AI政策"]},
        "notes": "政策+产品双驱动，看盘后政策文件",
    },
    "地产链": {
        "catalyst_keywords": [
            "房地产", "楼市", "新政", "住建", "公积金", "限购",
            "房贷", "首付", "保障房", "城中村",
        ],
        "pattern_keywords": ["地产", "房地产", "债务重组", "物业管理", "建材"],
        "catalyst_strength_base": 5.5,
        "overnight_sources": {"events": ["一线城市楼市新政", "住建部文件"]},
        "notes": "政策博弈型，关注一线城市新政窗口期",
    },
    "大消费/影视": {
        "catalyst_keywords": [
            "票房", "暑期档", "春节档", "国庆档", "电影", "观影",
            "消费券", "零售", "白酒", "食品", "社零",
        ],
        "pattern_keywords": ["影视", "票房", "暑期档", "消费", "白酒", "食品", "红枣"],
        "catalyst_strength_base": 6.0,
        "overnight_sources": {"events": ["档期票房数据", "消费刺激政策"]},
        "notes": "档期事件+消费数据驱动",
    },
    "有色/资源": {
        "catalyst_keywords": [
            "碳酸锂", "锂", "黄金", "铝", "铜", "稀土",
            "期货", "涨价", "商品", "有色",
        ],
        "pattern_keywords": ["碳酸锂", "黄金", "有色", "铝", "锂", "铜", "资源"],
        "catalyst_strength_base": 7.0,
        "overnight_sources": {"futures": ["碳酸锂主力", "COMEX黄金", "LME铝", "沪铜"]},
        "notes": "商品价格直接驱动，看期货盘",
    },
    "重组/资本运作": {
        "catalyst_keywords": [
            "重组", "并购", "借壳", "要约收购", "重整", "预重整",
            "注入", "国资", "央企", "整合",
        ],
        "pattern_keywords": ["重组", "重整", "要约", "国资", "并购"],
        "catalyst_strength_base": 5.0,
        "overnight_sources": {"events": ["重组公告", "国资整合动态"]},
        "notes": "个股事件驱动，持续性弱",
    },
    "油气/炼化": {
        "catalyst_keywords": [
            "原油", "WTI", "布伦特", "OPEC", "石油", "油价", "EIA",
            "战略石油储备", "页岩油", "油服", "炼化", "成品油",
            "天然气", "LNG", "沙特", "伊朗", "胡塞", "霍尔木兹", "地缘",
        ],
        "pattern_keywords": ["油服", "炼化", "石油", "燃气", "油气", "原油"],
        "catalyst_strength_base": 7.5,
        "overnight_sources": {
            "futures": ["WTI", "布伦特", "SC原油"],
            "events": ["OPEC产量", "EIA库存", "中东地缘"],
        },
        "notes": "地缘+商品双驱动(2026-09-09 Phase1实弹暴露盲区补登, 照9/8糖链先例: WTI+1.69%/SPR 44年新低/OPEC减产未命中任何主题; 9/8盘面炼化+3.43%净流入10.8亿/油服+4.71%已演绎)",
    },
    "糖/代糖(农业农化)": {
        "catalyst_keywords": [
            "糖价", "原糖", "白糖", "蔗糖", "甘蔗", "厄尔尼诺", "拉尼娜",
            "减产", "榨季", "代糖", "赤藓糖醇", "三氯蔗糖", "安赛蜜",
            "甜味剂", "无糖", "糖醇", "功能糖", "FAO", "粮农组织",
        ],
        "pattern_keywords": ["糖", "食糖", "代糖", "功能糖", "糖醇", "甜味剂", "淀粉糖", "农业农化"],
        "catalyst_strength_base": 7.5,
        "overnight_sources": {"events": ["原糖期货收盘", "WMO厄尔尼诺公告", "FAO食品价格指数", "安赛蜜/三氯蔗糖报价"]},
        "notes": "气象+商品双驱动(2026-09-09 主人红棉复盘裁决补登: 9/8 糖链五股共振因主题未登记而盲区)；厄尔尼诺→巴西/印度/泰国减产→原糖上行→食糖+代糖替代双逻辑",
    },
}

PREDICTABILITY_THRESHOLD = 7.0  # 范式召回可预判性阈值


def norm_text(s: str) -> str:
    return re.sub(r"\s+", "", str(s).lower())


def detect_themes(catalyst_text: str) -> list[dict]:
    """从催化文本识别触发的主线主题。返回 [{theme, hit_keywords, strength}]"""
    text = norm_text(catalyst_text)
    triggered = []
    for theme, cfg in CATALYST_THEME_MAP.items():
        hits = [kw for kw in cfg["catalyst_keywords"] if norm_text(kw) in text]
        if hits:
            # 催化强度 = base + 命中关键词加成(每个+0.5，上限+3)
            strength = min(cfg["catalyst_strength_base"] + len(hits) * 0.5,
                           cfg["catalyst_strength_base"] + 3.0)
            triggered.append({
                "theme": theme,
                "hit_keywords": hits,
                "catalyst_strength": round(strength, 1),
            })
    triggered.sort(key=lambda x: x["catalyst_strength"], reverse=True)
    return triggered


def query_patterns(theme: str, db_path: str = DB_PATH, min_score: float = PREDICTABILITY_THRESHOLD) -> list[dict]:
    """从涨停范式库召回该主题的可复用范式。"""
    cfg = CATALYST_THEME_MAP.get(theme)
    if not cfg:
        return []
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # 取所有范式，在python侧用pattern_keywords过滤（feature_tags是JSON）
    cur.execute("""SELECT trade_date, code, name, industry, secondary_attribution,
                          core_driver, predictability_score, feature_tags, strategy_value
                   FROM limit_up_patterns
                   WHERE pattern_type IN ('可复用范式','engine_v2') AND predictability_score >= ?
                   ORDER BY predictability_score DESC""", (min_score,))
    rows = cur.fetchall()
    conn.close()
    pks = [norm_text(k) for k in cfg["pattern_keywords"]]
    results = []
    for r in rows:
        tags_norm = norm_text(r[7]) if r[7] else ""
        sec_norm = norm_text(r[5]) if r[5] else ""  # secondary_attribution在r[5]? 重新对齐
        # 字段顺序: trade_date,code,name,industry,secondary,core_driver,score,tags,strategy
        secondary = r[4] or ""
        core = r[5] or ""
        blob = norm_text(secondary + " " + core + " " + (r[7] or ""))
        if any(pk in blob for pk in pks):
            results.append({
                "trade_date": r[0], "code": r[1], "name": r[2], "industry": r[3],
                "secondary": secondary, "core_driver": core,
                "predictability": r[6],
                "feature_tags": json.loads(r[7]) if r[7] else [],
                "strategy": r[8],
            })
    return results


SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "data", "overnight_snapshot_latest.json")


def load_overnight_snapshot() -> str:
    """读取盘前 agent 生成的隔夜快照并转为催化描述文本（结构化路径，优先）。

    snapshot 合同（data/overnight_snapshot_latest.json，由盘前情报 agent 实拉生成）：
      {"date":"YYYY-MM-DD","indices":[{name,change_pct}],"commodities":[{name,change_pct}],
       "us_stocks":[{name,change_pct}],"news_bullets":["..."],"source":"..."}
    缺文件/解析失败返回空串（调用方回退 jin10/akshare 旧路径）。
    """
    if not os.path.exists(SNAPSHOT_PATH):
        return ""
    try:
        with open(SNAPSHOT_PATH, encoding="utf-8") as f:
            snap = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] 隔夜快照解析失败，回退 jin10/akshare: {e}", file=sys.stderr)
        return ""
    parts = []
    for grp, key in (("隔夜指数", "indices"), ("隔夜商品", "commodities"), ("隔夜美股", "us_stocks")):
        for it in snap.get(key) or []:
            if isinstance(it, dict) and it.get("name"):
                pct = it.get("change_pct")
                pct_s = f" {pct:+.2f}%" if isinstance(pct, (int, float)) else ""
                parts.append(f"{grp}:{it['name']}{pct_s}")
    for b in snap.get("news_bullets") or []:
        if isinstance(b, str) and b.strip():
            parts.append(b.strip()[:120])
    return "；".join(parts)


def fetch_overnight_auto() -> str:
    """自动获取昨夜催化信号 → 整合为催化描述文本。

    数据源（已接入）：
      1. jin10 flash news（金十快讯）→ 提取昨夜外盘/商品/政策信息
      2. akshare 期货行情（碳酸锂/黄金/铝等）
      3. 美股指数/龙头涨幅（待细化接入，目前用快讯替代）

    Returns:
        str: 聚合后的催化描述文本，为空则表示没有明确催化
    """
    catalyst_parts = []

    # ---- 数据源1: jin10 flash news ----
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_servers"))
        from jin10_mcp import get_flash_news
        import asyncio, json

        async def _fetch_jin10():
            raw = await get_flash_news(limit=30)
            return json.loads(raw)

        flash_items = asyncio.run(_fetch_jin10())
        if isinstance(flash_items, list) and flash_items:
            # 从快讯中提取催化关键词
            keywords = {
                "美股": ["美股", "道指", "纳指", "标普", "S&P", "NASDAQ"],
                "商品/期货": ["原油", "黄金", "白银", "铜", "铝", "碳酸锂", "期货", "COMEX", "LME"],
                "地缘/政策": ["中东", "制裁", "关税", "美联储", "非农", "CPI", "利率"],
                "科技": ["英伟达", "NVIDIA", "AI", "芯片", "半导体", "台积电", "ASML"],
                "汇率": ["美元", "人民币", "日元", "离岸", "汇率"],
            }
            # 逐条看命中哪些主题
            category_texts = {k: [] for k in keywords}
            for item in flash_items:
                content = item.get("content", "")
                for cat, kws in keywords.items():
                    if any(kw in content for kw in kws):
                        category_texts[cat].append(content)
                        break  # 一条只归一类

            for cat, texts in category_texts.items():
                if texts:
                    # 最多取3条最重要代表
                    samples = texts[:3]
                    for s in samples:
                        # 截取核心内容（去HTML标签、去前缀、截短）
                        import re
                        clean = re.sub(r'<[^>]+>', '', s).strip()
                        if len(clean) > 20:
                            catalyst_parts.append(clean[:120])

    except Exception as e:
        catalyst_parts.append(f"[jin10获取异常: {e}]")

    # ---- 数据源2: akshare 隔夜外盘指数 ----
    try:
        import akshare as ak
        # 部分指数行情
        idx_data = ak.stock_zh_index_daily(symbol="sh000001")  # 上证
        if not idx_data.empty:
            last = idx_data.iloc[-1]
            catalyst_parts.append(f"上证昨收{last['close']}涨跌{last['close']-last['open']:.2f}")
    except Exception:
        pass

    # ---- 数据源3: akshare 商品期货 ----
    try:
        import akshare as ak
        # 可尝试获取期货主力合约行情
        # ak.futures_main_sina() 可能适用
        pass
    except Exception:
        pass

    if not catalyst_parts:
        return ""

    # 去重 + 整合
    seen = set()
    deduped = []
    for p in catalyst_parts:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    return "；".join(deduped[:15])  # 最多15条，防止上下文过长


def generate_report(catalyst_text: str, triggered: list[dict]) -> str:
    """生成主线预警报告。"""
    lines = []
    lines.append("=" * 70)
    lines.append(f"📡 屠龙系统·主线预警雷达  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 70)
    lines.append(f"【催化输入】{catalyst_text}")
    lines.append("")
    if not triggered:
        lines.append("⚠️ 未识别到明确主线催化。可能原因：")
        lines.append("  1. 昨夜无明显催化(持币观望)")
        lines.append("  2. 催化关键词未覆盖(请补充 --catalyst 描述)")
        lines.append("  3. 催化属随机事件(不作为范式依据)")
        return "\n".join(lines)

    lines.append(f"【识别到 {len(triggered)} 条主线催化】(按强度排序)")
    lines.append("")
    for i, t in enumerate(triggered, 1):
        patterns = query_patterns(t["theme"])
        lines.append(f"{'━'*60}")
        lines.append(f"🎯 主线{i}: {t['theme']}  | 催化强度 {t['catalyst_strength']}/10")
        lines.append(f"   命中催化词: {', '.join(t['hit_keywords'])}")
        lines.append(f"   {CATALYST_THEME_MAP[t['theme']]['notes']}")
        if patterns:
            lines.append(f"   📚 范式库召回 {len(patterns)} 条可复用范式(可预判≥{PREDICTABILITY_THRESHOLD}):")
            for p in patterns:
                lines.append(f"      • {p['code']} {p['name']} | {p['industry']} | 可预判{p['predictability']} | {p['secondary']}")
                # feature_tags 是 dict(save_to_db json.dumps) — dict不可切片,取前4项
                ft = p["feature_tags"]
                if isinstance(ft, dict):
                    ft_brief = dict(list(ft.items())[:4])
                elif isinstance(ft, list):
                    ft_brief = ft[:4]
                else:
                    ft_brief = ft
                lines.append(f"        标签:{ft_brief}")
                lines.append(f"        策略:{p['strategy']}")
        else:
            lines.append(f"   ⚠️ 范式库暂无该主题历史范式(新主线，需首日观察)")
        lines.append("")
    lines.append("━" * 60)
    lines.append("【使用建议】")
    lines.append("  1. 优先关注催化强度≥7的主线(外部催化映射型，持续性最强)")
    lines.append("  2. 范式龙头若符合标的池(主板+创业板,价格≤30,非ST)，盘前纳入观察")
    lines.append("  3. 开盘后验证：集合竞价高开+量比放大=范式兑现，可低吸")
    lines.append("  4. 新主线(无范式库召回)只观察不追，等首日范式入库后再参与")
    lines.append("=" * 70)
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="屠龙系统·主线预警雷达")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--catalyst", help="催化描述文本，如 '美股Lumentum涨20%%,碳酸锂涨3%%'")
    g.add_argument("--catalyst-file", help="催化JSON文件(每行一条)")
    g.add_argument("--auto", action="store_true", help="自动获取昨夜外盘(快照data/overnight_snapshot_latest.json优先,jin10/akshare回退)")
    p.add_argument("--min-score", type=float, default=PREDICTABILITY_THRESHOLD, help="范式召回可预判性阈值")
    args = p.parse_args()

    # 注：--min-score 当前版本需改源码常量 PREDICTABILITY_THRESHOLD 生效（避免global作用域冲突）

    if args.auto:
        catalyst_text = load_overnight_snapshot() or fetch_overnight_auto()
        if not catalyst_text:
            print("⚠️ 无隔夜快照(data/overnight_snapshot_latest.json)且 jin10/akshare 自动获取失败。", file=sys.stderr)
            print("盘前 agent 请先实拉外盘生成 snapshot；或手动: python mainline_radar.py --catalyst '美股光通信Lumentum涨20%'", file=sys.stderr)
            return 3
    elif args.catalyst_file:
        with open(args.catalyst_file, encoding="utf-8") as f:
            catalyst_text = " ".join(line.strip() for line in f if line.strip())
    else:
        catalyst_text = args.catalyst

    triggered = detect_themes(catalyst_text)
    report = generate_report(catalyst_text, triggered)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
