#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
隔夜美股映射因子 · 盘前选股权重输入（2026-08-18 落地，复盘线索①）

========== 用途 ==========
A股半导体/AI硬件链受隔夜美股存储/费半行情强映射（8/17实证：美股存储暴涨
→ 当日A股半导体全链涨停潮）。本脚本在盘前（07:30 cron）解析财联社电报中
美股关键标的涨跌幅，输出 A 股板块映射信号，供 daily_trade_plan 选股权重调整。

========== 映射表 ==========
美股标的 → A股板块信号（信号强度 = 标的涨跌幅加权均值）
  SK海力士/美光/闪迪/铠侠/WD           → 存储芯片
  英伟达/博通/AST/Coherent/Lumentum/迈威尔 → CPO/光通信
  台积电/日月光/安靠                  → 封测/制造
  应用材料/科磊/泛林/泰瑞达/AXT         → 半导体设备
  PCB相关(台耀/金像电等, 若披露)        → PCB
  寒武纪/中芯国际(港股/ADR)            → 中芯概念(国产替代)

========== 用法 ==========
    python premarket_us_signal.py              # 生成今日信号(自动日期)
    python premarket_us_signal.py --date 20260818
    python premarket_us_signal.py --refresh    # 强制重拉电报

输出: D:/zcodesj/reports/us_overnight_signal_YYYYMMDD.json
"""
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
import cls_api

OUT_DIR = r"D:\zcodesj\reports"

# 美股关键词 → A股板块 映射（含中文/英文别名）
US_MAP = {
    "存储芯片": ["SK海力士", "海力士", "美光", "闪迪", "铠侠", "西部数据", "希捷", "SanDisk", "Micron", "SK Hynix", "Kioxia"],
    "CPO光通信": ["英伟达", "博通", "迈威尔", "Coherent", "Lumentum", "Marvell", "AXT", "Fabrinet"],
    "封测制造": ["台积电", "日月光", "安靠", "Amkor", "阿斯麦", "ASML"],
    "半导体设备": ["应用材料", "科磊", "泛林", "泰瑞达", "Applied Materials", "KLA", "Lam", "Teradyne"],
    "国产替代": ["寒武纪", "中芯国际", "华虹"],
}


def extract_chg(text: str):
    """从电报文本提取 (标的, 涨跌幅)。支持 '+5.7%'、'上涨5.7%'、'涨2%'、'跌3.2%' 等格式"""
    pats = [
        # 主模式: 名称 + 0-15字 + +/-x.xx% (覆盖 "美光科技股价上涨4.25%")
        r"([\u4e00-\u9fa5A-Za-z\.&\- ]{0,12}?(?:海力士|美光|闪迪|铠侠|西部数据|希捷|英伟达|博通|迈威尔|Coherent|台积电|日月光|安靠|应用材料|科磊|泛林|泰瑞达|阿斯麦|寒武纪|中芯|华虹|AXT))[\s\S]{0,15}?([+\-]?\d+\.?\d*)%",
        # 涨幅语义: 上涨/涨 + x% (名称关键词后)
        r"([\u4e00-\u9fa5A-Za-z\.&\- ]{0,12}?(?:海力士|美光|闪迪|铠侠|西部数据|希捷|英伟达|博通|迈威尔|台积电|日月光|应用材料|科磊|泛林|泰瑞达|阿斯麦|寒武纪|中芯|华虹|AXT))[\s\S]{0,12}?上涨([+\-]?\d+\.?\d*)%",
        r"([\u4e00-\u9fa5A-Za-z\.&\- ]{0,12}?(?:海力士|美光|闪迪|铠侠|西部数据|希捷|英伟达|博通|迈威尔|台积电|日月光|应用材料|科磊|泛林|泰瑞达|阿斯麦|寒武纪|中芯|华虹|AXT))[\s\S]{0,12}?涨([+\-]?\d+\.?\d*)%",
        # 跌幅语义
        r"([\u4e00-\u9fa5A-Za-z\.&\- ]{0,12}?(?:海力士|美光|闪迪|铠侠|西部数据|希捷|英伟达|博通|迈威尔|台积电|日月光|应用材料|科磊|泛林|泰瑞达|阿斯麦|寒武纪|中芯|华虹|AXT))[\s\S]{0,12}?下跌([+\-]?\d+\.?\d*)%",
        # 费城半导体指数
        r"(费城半导体指数|费半)[\s\S]{0,8}?(?:涨|上涨|跌|下跌)([+\-]?\d+\.?\d*)%",
    ]
    found = []
    for pat in pats:
        for m in re.finditer(pat, text):
            name = m.group(1).strip()
            chg = float(m.group(2))
            # 若匹配段含"下跌/跌"且数值为正 → 取负
            seg = m.group(0)
            if ("下跌" in seg or "跌" in seg.split(name)[-1][:4]) and chg > 0:
                chg = -chg
            found.append((name, chg))
    return found


def main(date_str: str | None = None):
    date = date_str or datetime.now().strftime("%Y%m%d")
    # 分页拉 150 条（覆盖隔夜美股收盘+盘前信息）
    tele = {}
    cursor = None
    for _ in range(2):
        batch = cls_api.get_telegraph(rn=100, last_time=cursor)
        if not batch:
            break
        for t in batch:
            tele[t.get("id")] = t
        ts = [t.get("ctime") for t in batch if t.get("ctime")]
        cursor = min(ts) if ts else None
        if cursor is None:
            break
    texts = [f"{(t.get('title') or '')} {(t.get('content') or '')}" for t in tele.values()]

    sector_changes = {k: [] for k in US_MAP}
    matched = []
    for txt in texts:
        for name, chg in extract_chg(txt):
            for sector, keys in US_MAP.items():
                if any(k.lower() in name.lower() for k in keys):
                    sector_changes[sector].append((name, chg))
                    matched.append({"sector": sector, "stock": name, "chg_pct": chg})
                    break

    # 板块信号：加权均值（强涨跌样本取中位数抗噪）
    signal = {}
    for sector, lst in sector_changes.items():
        if not lst:
            signal[sector] = {"stock_count": 0, "signal_pct": None, "strength": "NO_DATA"}
            continue
        chgs = [c for _, c in lst]
        med = sorted(chgs)[len(chgs) // 2]
        strength = "STRONG_UP" if med >= 4 else "UP" if med >= 1.5 else (
            "FLAT" if med > -1.5 else "DOWN" if med > -4 else "STRONG_DOWN")
        signal[sector] = {"stock_count": len(lst),
                          "signal_pct": round(med, 2),
                          "strength": strength,
                          "stocks": lst[:6]}

    out = {
        "date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "隔夜美股映射因子：财联社电报解析，供盘前选股权重输入。STRONG_UP/UP=该A股板块可加权重，DOWN=降权或规避",
        "sectors": signal,
        "matched_news": matched[:40],
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"us_overnight_signal_{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in signal.items()}, ensure_ascii=False, indent=1))
    print("saved:", path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    main(a.date)