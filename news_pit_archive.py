#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
news_pit_archive.py — 财联社电报 PIT 存档（研究层，仅存储，禁入生产选股路径）
Owner: D:\zcodesj 数据链层（研究数据积累）

用途：
  屠龙目前无新闻快讯历史存档；LLM 情绪因子候选（P3，见
  reports/LLM_QUANT_DEEP_LEARNING_20260829.md）需要带发布时间戳的中文财经文本。
  本脚本把 cls-news MCP 返回的财联社电报原始 JSON 原子化落盘为 JSONL，
  按日分文件、按月分目录，按 id 幂等去重，支持只读自验证（fail-closed）。

数据流（cron 运行合同）：
  agent 调 cls_raw / cls_more -> 原始 JSON 数组写入 _cache 临时文件
  -> python news_pit_archive.py --input <tmp.json> [--date YYYYMMDD]
  -> python news_pit_archive.py --verify --date YYYYMMDD   (行数=0 则 exit 2)

存储口径（诚实标注）：
  单时点采样存档（约每日一次，cls_raw+cls_more 覆盖最近数小时），
  非全量回溯。未来研究只可按「被存档新闻」的事件口径使用，
  不得当全市场新闻流口径引用；缺失日以 --verify 报告，禁止补写历史。

退出码：
  0 = 成功；2 = 校验/验证失败；3 = 输入格式错误
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
NEWS_DIR = ROOT / "data" / "news_pit"
SOURCE_TAG = "cls_telegraph"

# 归一化后保留的最小字段集（PIT 研究最小必需 + 溯源）
# 注意：title 可合法为空（财联社快讯大量无标题条目），只以 content 为文本必需项
REQUIRED_FIELDS = ("id", "ctime", "content")
KEEP_FIELDS = (
    "id", "ctime", "level", "title", "content", "author",
    "modified_time", "reading_num", "share_num", "is_ad",
)


def _norm_item(raw: dict) -> dict | None:
    """提取保留字段；缺必需字段返回 None（由调用方计数并报告）。"""
    missing = [f for f in REQUIRED_FIELDS if raw.get(f) in (None, "")]
    if missing:
        return None
    subjects = [s.get("subject_name") for s in (raw.get("subjects") or [])
                if isinstance(s, dict) and s.get("subject_name")]
    # 新闻→个股映射（8/29 起）：源字段 stock_list=[{StockID:"sz002459", name:"晶澳科技", ...}]，
    # 归一为 [{"code","name"}]；2026-08-29 前已归档行无此字段（未采集≠缺失数据，诚实口径）。
    stocks = [{"code": s.get("StockID"), "name": s.get("name")}
              for s in (raw.get("stock_list") or [])
              if isinstance(s, dict) and s.get("StockID") and s.get("name")]
    item = {"source": SOURCE_TAG}
    for f in KEEP_FIELDS:
        item[f] = raw.get(f)
    if not item.get("title"):
        item["title"] = ""  # 无标题快讯合法
    item["subjects"] = subjects
    item["stocks"] = stocks
    return item


def load_input(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[FAIL] 输入文件读取/解析失败: {e}", file=sys.stderr)
        sys.exit(3)
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    if not isinstance(data, list):
        print("[FAIL] 输入必须是 JSON 数组（cls_raw/cls_more 返回结构）", file=sys.stderr)
        sys.exit(3)
    return data


def archive(input_path: Path, date_str: str) -> None:
    raw_items = load_input(input_path)
    items, skipped = [], 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        norm = _norm_item(raw)
        if norm is None:
            skipped += 1
            continue
        items.append(norm)

    month_dir = NEWS_DIR / date_str[:6]
    month_dir.mkdir(parents=True, exist_ok=True)
    out_path = month_dir / f"news_{date_str}.jsonl"

    # 幂等：读已有文件按 id 去重后合并（窗口重写，等价 UPSERT 语义）
    existing: dict = {}
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(f"[FAIL] 既有文件存在坏行，拒绝覆盖（防手改生成物）: {out_path}", file=sys.stderr)
                sys.exit(2)
            existing[str(row.get("id"))] = row

    before = len(existing)
    for it in items:
        existing[str(it["id"])] = it  # 同 id 以新数据覆盖
    rows = sorted(existing.values(), key=lambda r: r.get("ctime") or 0)

    tmp_path = out_path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp_path, out_path)  # 原子替换

    print(f"[OK] {date_str} 存档 {len(rows)} 条（本次输入 {len(items)} 条，"
          f"新增 {len(rows) - before}，更新 {len(items) - (len(rows) - before)}，跳过 {skipped}）")
    print(f"[OK] 文件: {out_path}")


def verify(date_str: str) -> None:
    out_path = NEWS_DIR / date_str[:6] / f"news_{date_str}.jsonl"
    if not out_path.exists():
        print(f"[FAIL] {date_str} 无存档文件（cron 落库自验证 0 行 = fail-closed）: {out_path}", file=sys.stderr)
        sys.exit(2)
    n = sum(1 for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip())
    if n == 0:
        print(f"[FAIL] {date_str} 存档文件存在但 0 行: {out_path}", file=sys.stderr)
        sys.exit(2)
    # 首末时间戳，供台账/巡检引用
    first_ct, last_ct = None, None
    for line in out_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ct = row.get("ctime")
        first_ct = ct if first_ct is None else min(first_ct, ct)
        last_ct = ct if last_ct is None else max(last_ct, ct)
    fmt = lambda ct: datetime.fromtimestamp(ct).strftime("%Y-%m-%d %H:%M:%S") if ct else "N/A"
    print(f"[OK] {date_str} 存档 {n} 条，覆盖 {fmt(first_ct)} ~ {fmt(last_ct)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="财联社电报 PIT 存档（研究层）")
    ap.add_argument("--input", help="cls_raw/cls_more 返回的 JSON 文件路径")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                    help="归档日 YYYYMMDD，默认今天")
    ap.add_argument("--verify", action="store_true", help="只读自验证当日存档行数")
    args = ap.parse_args()

    date_str = args.date.replace("-", "")
    if len(date_str) != 8 or not date_str.isdigit():
        print(f"[FAIL] 非法日期: {args.date}", file=sys.stderr)
        sys.exit(3)

    if args.verify:
        verify(date_str)
    elif args.input:
        archive(Path(args.input), date_str)
    else:
        print("[FAIL] 需要 --input 或 --verify", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
