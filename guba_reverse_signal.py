#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股吧反向见顶监控 (屠龙系统 P0-5)
================================================================
方法论: 东方财富股吧个股发帖量是散户关注度的直观代理。历史经验(2026-08
东财实证 + QIML 公众号学习): 当个股股吧发帖量较基线环比暴增 >=5 倍时,
通常意味着场外散户大规模进场接筹 —— 散户情绪过热的"反向见顶"信号,
与"天量见天价"同源。规则已同步写入大跌风险库 attribution/attribution.db
limit_down_risks (trade_date=20260815, code=ALL, drop_type=methodology_rule)。

数据源: https://guba.eastmoney.com/list,{code}.html
  页面为服务端渲染, 内嵌 `var article_list={"re":[...80条/页], "count":N,
  "bar_name":...}`; count=该吧累计帖子总数, 页数=ceil(count/每页条数)。
  (注意: 600288=大恒科技, 光讯科技是 002281)

流程:
  1. 逐只爬取帖子总数/页数 (requests + 常规Chrome UA, 限速1秒/次,
     失败重试2次, 退避3s);
  2. 与本地基线 _cache/guba_baseline.json 对比 (首次运行自动建立基线,
     基线默认不自动刷新, 用 --update-baseline 显式更新);
  3. 发帖量/基线 >= 5 倍 -> 输出 REVERSE_TOP_WARN (反向见顶预警, 散户
     进场信号), 建议减仓/禁追高。

用法:
  python guba_reverse_signal.py --codes 600288,002167          # 真实爬取
  python guba_reverse_signal.py --codes 600288 --update-baseline  # 刷新基线
  python guba_reverse_signal.py --dry-run                       # 离线模拟流程
  python guba_reverse_signal.py --sync-rule                     # 规则写入大跌风险库
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

GUBA_URL = "https://guba.eastmoney.com/list,{code}.html"
BASELINE = Path(r"D:\zcodesj\_cache\guba_baseline.json")
RISK_DB = Path(r"D:\zcodesj\attribution\attribution.db")

RATIO_WARN = 5.0     # 发帖量较基线暴增倍数阈值
RATE_LIMIT = 1.0     # 爬取限速(秒/次)
RETRY = 2            # 失败重试次数
TIMEOUT = 15

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# ----------------------------------------------------------------------------
# 爬取与解析
# ----------------------------------------------------------------------------
def fetch_guba(code: str, session: requests.Session | None = None) -> dict:
    """抓取个股股吧列表页, 解析内嵌 article_list JSON。
    返回 {code, bar_name, count, pages, per_page, fetch_time}。"""
    s = session or requests
    url = GUBA_URL.format(code=code)
    last_err = None
    for attempt in range(1 + RETRY):
        try:
            r = s.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
            r.raise_for_status()
            return parse_article_list(r.text, code)
        except Exception as e:  # noqa: BLE001 网络层任何异常都重试
            last_err = e
            if attempt < RETRY:
                time.sleep(3)  # 重试退避
    raise RuntimeError(f"{code} 抓取失败(重试{RETRY}次): {last_err}")


def parse_article_list(html: str, code: str) -> dict:
    """从 HTML 中提取 var article_list={...}; 的 JSON 并解析关键字段。"""
    import re
    m = re.search(r"var\s+article_list\s*=", html)
    if not m:
        raise RuntimeError("页面中未找到 article_list 数据(页面结构可能已变更)")
    raw = html[m.end():].lstrip()
    data, _ = json.JSONDecoder().raw_decode(raw)
    count = int(data.get("count") or 0)
    per_page = len(data.get("re") or []) or 80
    return {
        "code": code,
        "bar_name": data.get("bar_name") or "",
        "count": count,
        "per_page": per_page,
        "pages": math.ceil(count / per_page) if count else 0,
        "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ----------------------------------------------------------------------------
# 基线缓存
# ----------------------------------------------------------------------------
def load_baseline() -> dict:
    if BASELINE.exists():
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    return {"updated": None, "bars": {}}


def save_baseline(bl: dict) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(bl, ensure_ascii=False, indent=2),
                        encoding="utf-8")


# ----------------------------------------------------------------------------
# 预警判断
# ----------------------------------------------------------------------------
def judge(cur: dict, base: dict) -> dict:
    """cur=当前爬取结果, base=基线记录(可为空)。返回对比结果行。"""
    if not base or not base.get("count"):
        return {"signal": "BASELINE_INIT", "ratio": None,
                "msg": f"基线建立: {cur['count']} 帖 (首次运行, 暂不预警)"}
    ratio = cur["count"] / base["count"] if base["count"] else None
    if ratio is not None and ratio >= RATIO_WARN:
        return {"signal": "REVERSE_TOP_WARN", "ratio": ratio,
                "msg": (f"发帖量较基线暴增 {ratio:.1f} 倍(>={RATIO_WARN:.0f}x) -> "
                        f"反向见顶预警: 散户大规模进场, 建议减仓/禁追高")}
    return {"signal": "OK", "ratio": ratio,
            "msg": f"发帖量/基线 = {ratio:.2f}x (<{RATIO_WARN:.0f}x), 未触发预警"}


def run_codes(codes: list[str], update_baseline: bool) -> int:
    bl = load_baseline()
    session = requests.Session()
    warns = 0
    print(f"{'代码':<8}{'股吧名':<14}{'帖子总数':>10}{'页数':>8}"
          f"{'基线':>10}{'倍数':>8}  信号")
    print("-" * 100)
    for i, code in enumerate(codes):
        if i:
            time.sleep(RATE_LIMIT)          # 限速 1 秒/次
        try:
            cur = fetch_guba(code, session)
        except Exception as e:  # noqa: BLE001
            print(f"{code:<8}{'-':<14}{'抓取失败':>10}  {e}")
            continue
        base = bl["bars"].get(code, {})
        res = judge(cur, base)
        ratio_s = f"{res['ratio']:.1f}x" if res["ratio"] else "-"
        print(f"{code:<8}{cur['bar_name']:<14}{cur['count']:>10,}{cur['pages']:>8}"
              f"{(base.get('count') or '-'):>10}{ratio_s:>8}  "
              f"{res['signal']}: {res['msg']}")
        if res["signal"] == "REVERSE_TOP_WARN":
            warns += 1
        if update_baseline or not base:
            bl["bars"][code] = {"count": cur["count"], "pages": cur["pages"],
                                "bar_name": cur["bar_name"],
                                "updated": cur["fetch_time"]}
    bl["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_baseline(bl)
    print("-" * 100)
    print(f"[done] 基线文件: {BASELINE}  REVERSE_TOP_WARN: {warns}/{len(codes)} 只")
    return 0 if warns == 0 else 1


def run_dry_run() -> int:
    """离线模拟: 用模拟数据演示 基线建立→对比→暴增预警 全流程(不联网)。"""
    print("[dry-run] 使用模拟数据演示流程, 不联网, 不写基线文件\n")
    mock_base = {"count": 1200, "pages": 15, "bar_name": "模拟甲", "updated": "2026-07-15"}
    mock_cur_warn = {"code": "600MOCK", "bar_name": "模拟甲", "count": 8600,
                     "per_page": 80, "pages": 108, "fetch_time": "2026-08-15 12:00:00"}
    mock_cur_ok = {"code": "000MOCK", "bar_name": "模拟乙", "count": 1410,
                   "per_page": 80, "pages": 18, "fetch_time": "2026-08-15 12:00:01"}
    for cur in (mock_cur_warn, mock_cur_ok):
        res = judge(cur, mock_base)
        ratio_s = f"{res['ratio']:.2f}x" if res["ratio"] else "-"
        print(f"{cur['code']:<10}{cur['bar_name']:<8} 当前 {cur['count']:>6,} 帖 / "
              f"{cur['pages']:>4} 页   基线 {mock_base['count']:>6,} 帖   "
              f"倍数 {ratio_s:>7}  -> {res['signal']}")
        print(f"            {res['msg']}\n")
    print("[dry-run] 演示完毕: 模拟甲 7.2 倍触发 REVERSE_TOP_WARN, 模拟乙 1.18x 正常")
    return 0


# ----------------------------------------------------------------------------
# 方法论条目 -> 大跌风险库 limit_down_risks
# ----------------------------------------------------------------------------
RULE_TRADE_DATE = "20260815"
RULE_DROP_TYPE = "methodology_rule"


def sync_rule() -> int:
    """把'股吧发帖量暴增=反向见顶'规则写入大跌风险库(幂等)。"""
    if not RISK_DB.exists():
        print(f"[error] 风险库不存在: {RISK_DB}", file=sys.stderr)
        return 2
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(RISK_DB)
    cur = con.cursor()
    exists = cur.execute(
        "SELECT id FROM limit_down_risks WHERE trade_date=? AND code='ALL' "
        "AND drop_type=?", (RULE_TRADE_DATE, RULE_DROP_TYPE)).fetchone()
    if exists:
        print(f"[skip] 方法论条目已存在 (id={exists[0]}), 不重复插入")
        con.close()
        return 0
    cur.execute("""INSERT INTO limit_down_risks
        (trade_date, code, name, industry, board, drop_type, primary_attribution,
         secondary_attribution, avoidability_score, risk_tags,
         info_available_pre_open, strategy_value, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        RULE_TRADE_DATE, "ALL", "通用方法论", None, None, RULE_DROP_TYPE,
        "散户情绪过热(反向见顶)", "股吧发帖量环比暴增>=5倍", 8.0,
        json.dumps(["REVERSE_TOP_WARN", "股吧发帖量环比>=5倍", "散户进场信号",
                    "反向见顶", "天量见天价同源", "工具:guba_reverse_signal.py"],
                   ensure_ascii=False),
        1,
        "股吧发帖量环比暴增>=5倍=散户情绪过热反向见顶信号; 来源=2026-08东财实证"
        "+QIML公众号学习; 日期20260815; 监控工具: guba_reverse_signal.py --codes",
        now))
    con.commit()
    new_id = cur.lastrowid
    con.close()
    print(f"[入库] limit_down_risks 新增方法论条目 id={new_id} (trade_date={RULE_TRADE_DATE})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="东财股吧发帖量反向见顶监控(散户进场信号 REVERSE_TOP_WARN)")
    ap.add_argument("--codes", default="", help="股票代码, 逗号分隔, 如 600288,002167")
    ap.add_argument("--dry-run", action="store_true", help="不联网, 用模拟数据演示流程")
    ap.add_argument("--update-baseline", action="store_true",
                    help="本次结果刷新为基线(默认仅首次建立基线)")
    ap.add_argument("--sync-rule", action="store_true",
                    help="把该规则写入大跌风险库 attribution/attribution.db (幂等)")
    args = ap.parse_args(argv)

    if args.sync_rule:
        return sync_rule()
    if args.dry_run:
        return run_dry_run()
    if not args.codes:
        ap.error("需要 --codes (或用 --dry-run / --sync-rule)")
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    return run_codes(codes, args.update_baseline)


if __name__ == "__main__":
    sys.exit(main())
