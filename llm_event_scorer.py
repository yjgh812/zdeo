#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 事件情绪打分工具 (屠龙系统 P0-4)
================================================================
输入个股新闻标题 -> 逐条输出情绪分 [-1,1] + 类型标签
(利好/利空/中性 × 业绩/并购重组/政策/增减持/订单合同/股价异动/其他)。

两级实现:
  (1) 默认: 关键词规则引擎(自建词表 65 词, 见 POLARITY_LEXICON):
      预增/中标/回购/增持/涨停 -> 正分; 预减/减持/质押/立案/问询/退市 -> 负分;
      得分 = 命中词权重求和后截断到 [-1,1], 方向阈值 ±0.05。
  (2) --llm: 走 DeepSeek API (Lopez-Lira 2023《Can ChatGPT Forecast Stock
      Price Movements?》prompt 范式: 判断新闻标题对股价的影响方向+置信度,
      再映射为 [-1,1] 分数)。
      需先设置环境变量 DEEPSEEK_API_KEY (当前机器未发现该 key, 本工具
      未实际调用过 LLM 通道; 接口已实现, 设 key 后即可用):
        set DEEPSEEK_API_KEY=sk-xxxx
        python llm_event_scorer.py --llm --codes 600288
      可选 DEEPSEEK_BASE_URL 覆盖默认 https://api.deepseek.com

新闻源: 优先财联社电报接口 (复用本目录 cls_api.py 的 get_telegraph,
无个股新闻接口, 采用"电报全量 + 按代码/名称过滤"方案); 联网失败或无匹配
时用 --text-file 模式兜底(每行一条标题, 纯离线)。

用法:
  python llm_event_scorer.py --codes 600288,002167            # 电报取新闻并过滤
  python llm_event_scorer.py --text-file headlines.txt        # 离线打分
  python llm_event_scorer.py --codes 600288 --llm             # DeepSeek 通道
  python llm_event_scorer.py --codes 600288 --out score.json  # JSON 落盘

输出: JSON 到 stdout (items + summary), --out 可选保存文件。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# (1) 关键词规则引擎词表: 词 -> (极性权重, 类别)   共 65 词 (>=40)
# ---------------------------------------------------------------------------
POLARITY_LEXICON: dict[str, tuple[float, str]] = {
    # --- 正向: 业绩/订单/增减持/并购/政策/股价异动/其他 ---
    "预增": (0.7, "业绩"), "扭亏": (0.6, "业绩"), "业绩预喜": (0.6, "业绩"),
    "净利增": (0.5, "业绩"), "营收增": (0.3, "业绩"),
    "中标": (0.7, "订单合同"), "大额订单": (0.6, "订单合同"), "订单": (0.4, "订单合同"),
    "签订合同": (0.5, "订单合同"), "框架协议": (0.3, "订单合同"),
    "战略合作": (0.5, "其他"), "中选": (0.5, "政策"),
    "回购": (0.5, "增减持"), "增持": (0.5, "增减持"), "分红": (0.3, "增减持"),
    "收购": (0.3, "并购重组"), "重组": (0.4, "并购重组"), "借壳": (0.3, "并购重组"),
    "资产注入": (0.4, "并购重组"),
    "获批": (0.5, "政策"), "批复": (0.4, "政策"), "试点": (0.3, "政策"),
    "补贴": (0.4, "政策"), "减税": (0.3, "政策"), "政策支持": (0.5, "政策"),
    "涨停": (0.6, "股价异动"), "大涨": (0.4, "股价异动"), "新高": (0.5, "股价异动"),
    "上调": (0.3, "其他"), "授信": (0.2, "其他"), "股权激励": (0.3, "其他"),
    # --- 负向 ---
    "预减": (-0.7, "业绩"), "业绩变脸": (-0.7, "业绩"), "亏损": (-0.6, "业绩"),
    "巨亏": (-0.8, "业绩"), "减值": (-0.6, "业绩"), "计提": (-0.4, "业绩"),
    "减持": (-0.5, "增减持"), "质押": (-0.4, "增减持"), "解禁": (-0.4, "增减持"),
    "立案": (-0.8, "其他"), "立案调查": (-0.8, "其他"), "问询": (-0.5, "其他"),
    "处罚": (-0.7, "其他"),
    "调查": (-0.5, "其他"), "退市": (-0.9, "其他"), "违规": (-0.5, "其他"),
    "诉讼": (-0.4, "其他"), "仲裁": (-0.3, "其他"), "自查": (-0.3, "其他"),
    "被曝": (-0.4, "其他"), "造假": (-0.8, "其他"), "资金占用": (-0.6, "其他"),
    "终止": (-0.5, "其他"), "失败": (-0.4, "其他"), "停产": (-0.5, "其他"),
    "风险提示": (-0.4, "其他"), "澄清": (-0.2, "其他"), "辟谣": (-0.3, "其他"),
    "下调": (-0.3, "其他"), "出售资产": (-0.3, "并购重组"),
    "跌停": (-0.7, "股价异动"), "大跌": (-0.5, "股价异动"), "新低": (-0.5, "股价异动"),
    "闪崩": (-0.6, "股价异动"), "天地板": (-0.7, "股价异动"), "炸板": (-0.3, "股价异动"),
}

# 类型标签辅助词(无极性, 仅用于类别归类)
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "业绩": ["业绩", "净利", "营收", "年报", "季报", "快报", "财报", "预增", "预减"],
    "并购重组": ["并购", "重组", "收购", "借壳", "注入", "卖壳"],
    "政策": ["政策", "集采", "目录", "新规", "部委", "国务院", "发改委", "证监会",
             "规划", "征求意见"],
    "增减持": ["股东", "大宗交易", "解禁", "质押", "回购", "增持", "减持"],
    "订单合同": ["订单", "合同", "签约", "中标", "供货", "中标候选人"],
    "股价异动": ["涨停", "跌停", "异动", "龙虎榜", "新高", "新低", "闪崩"],
}
TYPE_ORDER = ["业绩", "并购重组", "政策", "增减持", "订单合同", "股价异动", "其他"]

FUND_DB = Path(r"D:\workbuddyspace\databases\fundamental_data.db")


def rule_score(title: str) -> dict:
    """关键词规则引擎: 返回 {score, direction, type, confidence, matched}。"""
    matched = [w for w in POLARITY_LEXICON if w in title]
    # 去重: 若某命中词是另一更长命中词的子串(如"立案"与"立案调查"), 只留长词
    matched = [w for w in matched
               if not any(w != o and w in o for o in matched)]
    score = max(-1.0, min(1.0, sum(POLARITY_LEXICON[w][0] for w in matched)))
    direction = "利好" if score > 0.05 else ("利空" if score < -0.05 else "中性")
    # 类别: 极性词投票优先, 无命中再扫类别词
    cat_votes: dict[str, int] = {}
    for w in matched:
        cat_votes[POLARITY_LEXICON[w][1]] = cat_votes.get(POLARITY_LEXICON[w][1], 0) + 1
    if not cat_votes:
        for cat, kws in CATEGORY_KEYWORDS.items():
            n = sum(1 for k in kws if k in title)
            if n:
                cat_votes[cat] = n
    type_ = max(TYPE_ORDER, key=lambda c: cat_votes.get(c, -1)) if cat_votes else "其他"
    conf = 0.0
    if matched:
        conf = round(min(0.9, 0.35 + 0.2 * (len(matched) - 1) + 0.25 * abs(score)), 2)
    return {"score": round(score, 2), "direction": direction, "type": type_,
            "confidence": conf, "matched": matched}


# ---------------------------------------------------------------------------
# (2) DeepSeek LLM 通道 (Lopez-Lira prompt 范式) —— 需 DEEPSEEK_API_KEY
# ---------------------------------------------------------------------------
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

LLM_PROMPT = """你是一名严谨的A股卖方分析师。请判断下面这条新闻标题对指定公司股价的\
短期影响方向与置信度(参考 Lopez-Lira (2023) 新闻打分范式, 只依据标题文本, 不臆测):
公司: {subject}
标题: {title}
请只输出JSON: {{"direction": "good"|"bad"|"neutral", "confidence": 0到1, \
"type": "业绩|并购重组|政策|增减持|订单合同|股价异动|其他", "reason": "15字内"}}"""


def llm_score(title: str, subject: str) -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("[error] 未设置 DEEPSEEK_API_KEY 环境变量, 无法走 LLM 通道 "
              "(默认关键词引擎无需该变量)", file=sys.stderr)
        sys.exit(2)
    import requests  # 延迟导入, 离线模式不依赖
    resp = requests.post(
        f"{DEEPSEEK_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}",
                 "User-Agent": "tulong-llm-event-scorer/1.0"},
        json={"model": DEEPSEEK_MODEL, "temperature": 0,
              "messages": [{"role": "user",
                            "content": LLM_PROMPT.format(subject=subject, title=title)}]},
        timeout=30)
    resp.raise_for_status()
    txt = resp.json()["choices"][0]["message"]["content"].strip()
    m = re.search(r"\{.*\}", txt, re.S)
    d = json.loads(m.group(0)) if m else {"direction": "neutral", "confidence": 0.0,
                                          "type": "其他"}
    score = {"good": 1.0, "bad": -1.0}.get(d.get("direction"), 0.0) * float(
        d.get("confidence") or 0.0)
    return {"score": round(score, 2),
            "direction": {"good": "利好", "bad": "利空"}.get(d.get("direction"), "中性"),
            "type": d.get("type", "其他"),
            "confidence": round(float(d.get("confidence") or 0.0), 2),
            "matched": [], "reason": d.get("reason", "")}


# ---------------------------------------------------------------------------
# 新闻源: 财联社电报 + 股票名称解析
# ---------------------------------------------------------------------------
def resolve_names(codes: list[str]) -> dict[str, str]:
    """code -> 股票名。本地(龙虎榜/北向表)优先, 腾讯行情接口兜底, 再兜底空。"""
    names: dict[str, str] = {}
    if FUND_DB.exists():
        try:
            con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
            for c in codes:
                row = con.execute(
                    "SELECT name FROM (SELECT ts_code,name FROM dragon_tiger UNION ALL "
                    "SELECT ts_code,name FROM northbound_holding) "
                    "WHERE ts_code LIKE ? LIMIT 1", (c + "%",)).fetchone()
                if row and row[0]:
                    names[c] = row[0]
            con.close()
        except sqlite3.Error:
            pass
    missing = [c for c in codes if c not in names]
    if missing:
        try:
            import requests
            q = ",".join(("sh" if c.startswith(("6", "9")) else
                          ("bj" if c.startswith(("4", "8")) else "sz")) + c
                         for c in missing)
            r = requests.get(f"https://qt.gtimg.cn/q={q}", timeout=10)
            for part in r.content.decode("gbk", "ignore").split(";"):
                m = re.search(r'v_\w{2}(\d{6})="([^~]*)~', part)
                if m and m.group(2):
                    names[m.group(1)] = m.group(2)
            time.sleep(0.5)
        except Exception:  # noqa: BLE001 离线时名称解析失败不致命
            pass
    return names


def fetch_cls_headlines(codes: list[str], pages: int) -> tuple[list[dict], str]:
    """拉取财联社电报多页, 按代码/名称过滤个股相关条目。
    返回 ([{title, ctime, code}], 来源说明)。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cls_api import get_telegraph  # 本目录签名库
    names = resolve_names(codes)
    picked: list[dict] = []
    last_time, scanned = None, 0
    for _ in range(pages):
        # 注: cls 接口 rn>约50 时返回空列表, 实测 rn=30 稳定
        items = get_telegraph(rn=30, last_time=last_time)
        if not items:
            break
        scanned += len(items)
        last_time = items[-1].get("ctime")
        for it in items:
            title = (it.get("title") or "").strip()
            content = re.sub(r"<[^>]+>", "", it.get("content") or "")
            text = f"{title} {content[:80]}"
            if not title:
                title = content[:50].strip() or "(无题电报)"
            for c in codes:
                nm = names.get(c, "")
                if c in text or (nm and nm in text):
                    picked.append({"title": title, "ctime": it.get("ctime"), "code": c})
                    break
        time.sleep(0.6)
    src = (f"财联社电报 get_telegraph (扫描{scanned}条/{pages}页, "
           f"名称解析{ {c: names.get(c, '?') for c in codes} })")
    return picked, src


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def load_text_file(path: str) -> list[dict]:
    lines = [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    return [{"title": ln, "ctime": None, "code": None} for ln in lines]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="新闻标题情绪打分: 关键词引擎(默认)/DeepSeek LLM(--llm)",
        epilog="LLM通道需先 set DEEPSEEK_API_KEY=sk-xxxx (本机未预置该key); "
               "可选 DEEPSEEK_BASE_URL/DEEPSEEK_MODEL。新闻源默认财联社电报"
               "(cls_api.get_telegraph, 无个股接口故按代码/名称过滤), "
               "联网失败用 --text-file 每行一条标题离线兜底。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codes", default="", help="股票代码, 逗号分隔, 如 600288,002167")
    ap.add_argument("--text-file", default="", help="标题文件(每行一条), 离线模式")
    ap.add_argument("--llm", action="store_true",
                    help="改走 DeepSeek API (Lopez-Lira 范式, 需 DEEPSEEK_API_KEY)")
    ap.add_argument("--pages", type=int, default=4,
                    help="--codes 模式扫描电报页数(默认4, 每页30条, 接口rn上限)")
    ap.add_argument("--limit", type=int, default=0, help="最多打分条数(0=不限)")
    ap.add_argument("--out", default="", help="JSON 结果保存文件(可选)")
    args = ap.parse_args(argv)

    if not args.codes and not args.text_file:
        ap.error("需要 --codes 或 --text-file 之一")

    if args.llm and not os.environ.get("DEEPSEEK_API_KEY"):
        print("[error] --llm 需要先设置环境变量 DEEPSEEK_API_KEY "
              "(本机未发现该key; 默认关键词引擎无需此变量)", file=sys.stderr)
        return 2

    engine = "llm(deepseek)" if args.llm else "keyword-rule"
    if args.text_file:
        items = load_text_file(args.text_file)
        source = f"text-file: {args.text_file}"
    else:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        try:
            items, source = fetch_cls_headlines(codes, args.pages)
        except Exception as e:  # noqa: BLE001 联网失败提示兜底
            print(f"[error] 财联社接口失败: {e}\n"
                  f"       请改用 --text-file 模式(每行一条标题)离线兜底",
                  file=sys.stderr)
            return 2
        if not items:
            print(f"[warn] 电报中未匹配到 {args.codes} 相关条目(个股新闻常不在电报流), "
                  f"建议用 --text-file 模式", file=sys.stderr)
    if args.limit:
        items = items[:args.limit]

    out_items = []
    for it in items:
        subject = it.get("code") or "全市场"
        try:
            sc = (llm_score(it["title"], subject) if args.llm
                  else rule_score(it["title"]))
        except SystemExit:
            raise
        sc.update({"title": it["title"], "code": it.get("code"),
                   "time": (datetime.fromtimestamp(it["ctime"]).strftime("%m-%d %H:%M")
                            if it.get("ctime") else None), "engine": engine})
        out_items.append(sc)

    scores = [x["score"] for x in out_items] or [0.0]
    summary = {"n": len(out_items), "avg_score": round(sum(scores) / len(scores), 3),
               "n_bull": sum(1 for x in out_items if x["direction"] == "利好"),
               "n_bear": sum(1 for x in out_items if x["direction"] == "利空"),
               "n_neutral": sum(1 for x in out_items if x["direction"] == "中性"),
               "lexicon_words": len(POLARITY_LEXICON)}
    result = {"meta": {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "engine": engine, "source": source}, "items": out_items,
              "summary": summary}
    js = json.dumps(result, ensure_ascii=False, indent=2)
    print(js)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(js, encoding="utf-8")
        print(f"\n[save] {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
