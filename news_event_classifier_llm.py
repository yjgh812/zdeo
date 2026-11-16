#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
news_event_classifier_llm.py — 新闻事件分类器 LLM 语义增强版 v3（MiniMax-M3，影子）

定位：规则分类器方向判定 ~51% 不可用（质量基线 data/news_events_classified_meta.json），
  LLM 语义增强候选。判据（meta 冻结）：37 条人工验证集方向准确率 ≥80% 才准进过滤层；
  <80% 则档案记结论，路线关闭。影子运行，不进信号/闸门/executor。

密钥：os.environ["MINIMAX_API_KEY"] 显式下传（禁硬编码/禁日志明文）。
模型：/v1/models 枚举现役=MiniMax-M3/M2.7/M2.5/M2.1系列（2026-09-09 实探，旧 abab 族已
  套餐不兼容 status 2061）；默认 MiniMax-M3，端点 https://api.minimaxi.com/v1/chat/completions
  （OpenAI 兼容）。429 退避 20s×5。

用法：
  --probe          单条连通性测试
  --run            全量分类（规则台账 join news_pit 原文 → LLM → 独立台账）
  --validate       37 条人工验证集对比 → 判 ≥80% 门槛
退出码：0 完成；2 配置/数据不可用；3 探针失败
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
SRC_LEDGER = ROOT / "data" / "news_events_classified.jsonl"
NEWS_DIR = ROOT / "data" / "news_pit"
LLM_LEDGER = ROOT / "data" / "news_events_llm_classified.jsonl"
HUMAN = ROOT / "data" / "news_events_human_labels_37.json"
MODEL = "MiniMax-M3"
ENDPOINT = "https://api.minimaxi.com/v1/chat/completions"
BATCH = 5
SYS_PROMPT = ('你是A股财经新闻分类器。对给定新闻列表逐条输出JSON数组，每项'
              '{"idx":序号,"type":"announcement|policy|macro|industry|geo|other",'
              '"sentiment":"bullish|bearish|mixed|neutral"}。'
              'sentiment=该新闻对相关A股个股/板块的实质影响方向(非字面词)，无明确影响=neutral。'
              '自然灾害/国际政治与A股无关=neutral。只输出JSON数组，不要多余文字。')
ctx = ssl.create_default_context()


def call_llm(items: list, retries: int = 5) -> list | None:
    """items=[{idx,text}]，返回解析后的 LLM 判定列表；失败返回 None。"""
    user = "\n".join(f'{i+1}. {it["text"][:220]}' for i, it in enumerate(items))
    body = json.dumps({"model": MODEL,
                       "messages": [{"role": "system", "content": SYS_PROMPT},
                                    {"role": "user", "content": user}],
                       "temperature": 0.1},
                      ensure_ascii=False).encode("utf-8")
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        print("[FAIL] MINIMAX_API_KEY 未配置", file=sys.stderr)
        sys.exit(2)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(ENDPOINT, data=body,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
                r = json.loads(resp.read())
            content = (r.get("choices") or [{}])[0].get("message", {}).get("content", "")
            m = re.search(r"\[.*\]", content, re.DOTALL)
            if not m:
                raise ValueError(f"无JSON数组: {content[:80]}")
            parsed = json.loads(m.group(0))
            return parsed if isinstance(parsed, list) else None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 20 * (attempt + 1)
                print(f"  [429] backoff {wait}s", file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"  [HTTP {e.code}] {e.read().decode('utf-8', 'replace')[:120]}", file=sys.stderr)
                return None
        except Exception as e:
            print(f"  [ERR] {str(e)[:120]}", file=sys.stderr)
            if attempt == retries - 1:
                return None
            time.sleep(5)
    return None


def load_texts() -> dict:
    """(source,id) → 原文文本"""
    texts = {}
    for f in NEWS_DIR.rglob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                texts[(r.get("source", "cls_telegraph"), str(r["id"]))] = \
                    f'{r.get("title") or ""} {r.get("content") or ""}'.strip()
            except json.JSONDecodeError:
                continue
    return texts


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--probe", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--validate", action="store_true")
    args = p.parse_args()

    if args.probe:
        out = call_llm([{"text": "【盛科通信：大基金减持707.47万股 减持计划结束】大基金累计减持707.47万股，占总股本1.73%。"}])
        if out is None:
            print("[FAIL] 探针失败", file=sys.stderr)
            return 3
        print(f"[PROBE_OK] {json.dumps(out, ensure_ascii=False)[:200]}")
        return 0

    if args.run:
        ledger = {}
        for line in SRC_LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                ledger[(r["source"], str(r["id"]))] = r
        texts = load_texts()
        keys = [k for k in ledger if texts.get(k)]
        print(f"[INFO] 待分类 {len(keys)} 条")
        results, done = {}, 0
        for i in range(0, len(keys), BATCH):
            chunk = keys[i:i + BATCH]
            items = [{"idx": j + 1, "text": texts[k]} for j, k in enumerate(chunk)]
            out = call_llm(items)
            if out is None:
                print(f"  批 {i//BATCH+1} 失败，跳过 {len(chunk)} 条（如实标记）", file=sys.stderr)
                continue
            by_idx = {o.get("idx"): o for o in out if isinstance(o, dict)}
            for j, k in enumerate(chunk):
                o = by_idx.get(j + 1)
                if o and o.get("sentiment") in ("bullish", "bearish", "mixed", "neutral"):
                    base = ledger[k]
                    results[k] = {"date": base["date"], "source": base["source"], "id": base["id"],
                                  "type": o.get("type", "other"), "sentiment": o["sentiment"],
                                  "stocks": base.get("stocks", []),
                                  "classifier": f"llm_{MODEL}", "batch": i // BATCH + 1}
                else:
                    print(f"  [WARN] idx={j+1} 批{i//BATCH+1} LLM输出缺/非法，该条不落账（如实）", file=sys.stderr)
            done += len(chunk)
            print(f"  进度 {done}/{len(keys)}")
            time.sleep(1)
        ordered = sorted(results.values(), key=lambda r: (r["date"], r["id"]))
        LLM_LEDGER.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in ordered) + "\n",
                              encoding="utf-8")
        print(f"[OK] LLM分类落账 {len(ordered)}/{len(keys)} 条 -> {LLM_LEDGER.name}")
        return 0

    if args.validate:
        if not LLM_LEDGER.exists():
            print("[FAIL] 先 --run 生成 LLM 台账", file=sys.stderr)
            return 2
        human = json.loads(HUMAN.read_text(encoding="utf-8"))["labels"]
        llm = {}
        for line in LLM_LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                llm[str(r["id"])] = r["sentiment"]
        pairs = [(hid, hlab, llm.get(hid)) for hid, hlab in human.items()]
        missed = [(h, l, m) for h, l, m in pairs if m is None]
        scored = [(h, l, m) for h, l, m in pairs if m is not None]
        exact = sum(1 for _, l, m in scored if l == m)
        nonn = [(l, m) for _, l, m in scored if l != "neutral" or m != "neutral"]
        print(f"验证集 37 条，LLM 覆盖 {len(scored)} 条（缺失 {len(missed)} 条如实标注）")
        print(f"方向四分类一致率: {exact}/{len(scored)} = {exact/len(scored):.0%}（门槛≥80%）")
        print("分歧明细（human vs llm）:")
        for h, l, m in scored:
            if l != m:
                print(f"  {h}: human={l} llm={m}")
        verdict = "PASS: 达≥80%门槛，过滤层解锁候选（仍影子，接线须主人裁决）" \
            if len(scored) >= 30 and exact / len(scored) >= 0.80 \
            else "FAIL: 未达≥80%门槛，LLM路线按档案纪律处理"
        print(f"【VERDICT】{verdict}")
        meta_p = ROOT / "data" / "news_events_classified_meta.json"
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        meta["v3_llm_minimax"] = {
            "model": MODEL, "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "covered": len(scored), "exact": exact,
            "accuracy": round(exact / len(scored), 4) if scored else None,
            "verdict": verdict}
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[OK] 结果已存档 {meta_p.name}")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
