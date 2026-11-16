#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_data_fetcher.py — 数据取数API v2.1 (屠龙三层决策体系·数据供给层)
========================================================================

升级说明 (v1.0→v2.0, t14 任务):
  + 全市场扫描: scan_full_market() → Sina分页并行获取全部4700只A股
  + 板块效应计算: calc_sector_effects() → 按概念聚合涨停数/涨幅均值
  + 模糊主线匹配: fuzzy_match_mainline() → "汽车芯片" 匹配 "芯片" 包含关系
  + 评分维度全覆盖: 量比(D1)/距涨停空间(D2)/板块效应(D3)/涨幅健康度(D4)
                    热榜关注度(D5)/趋势位置(D6)/首板加分(D7)/缩量扣分(D8)
  + 软失效: 数据缺失 -> 返回 None (评分层给0分, 不再硬否决)

升级说明 (v2.0→v2.1, t31 任务, v3.0闸门三字段管道补齐):
  + days_since_listing  上市至今交易日数 → G0次新股校验(上市<60交易日拒)
    数据源: 本地qfq库(dragon_quant_qfq.db)该code日线总行数=上市至今交易日数
    (最早K线=上市日; qfq每日22:30由daily_chain_update.py更新, 当日新上市票
     次日入库, 60日边界差1日可接受)
  + limit_up_count_10d  近10个交易日涨停次数 → G3断续板冗余校验(≥3次拒)
    数据源: westock kline相邻收盘涨幅≥9.8%(10cm)/19.8%(20cm)
    (除权日相邻收盘比会失真, 作为streak主防线之外的冗余校验可接受)
  + avg_amount_20d      20日日均成交额/元 → G18流动性底线(<2000万拒)
    数据源: westock kline前20节点amount均值(含当日; 实测金田601609当日
    15.35亿与真实成交一致; 不用qfq.amount列——前复权污染风险)

数据源:
  1. westock MCP data_quote  -> 行情快照 (价/量比/涨幅/20日涨幅/涨停价/板块)
  2. 同花顺热榜API            -> 热度排名/人气标签/概念标签 (TOP100)
  3. westock MCP data_kline  -> 日K线 (20日均振幅/涨停次数/日均成交额)
  4. 新浪行情API               -> 全市场扫描 (批量获取全部A股)
  5. 本地qfq库                -> 上市至今交易日数 (次新校验)

用法:
  python gate_data_fetcher.py --codes 600272,002354,000620
  python gate_data_fetcher.py --codes 600272 --source mainline_radar --mainlines "半导体,国企改革"
  python gate_data_fetcher.py --scan-full-market             # 全市场扫描
  python gate_data_fetcher.py --sector-effects               # 板块效应计算

版本: v2.1 — 2026-08-17 t31
作者: m1 数据管家 (t14 任务建立, t31 三字段补齐)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ===== 数据源配置 =====
WESTOCK_CLIENT_PATH = r"D:\workbuddyspace"

# t31: G0次新校验数据源 — qfq库(全历史日线, 最早行=上市日)
QFQ_DB_PATH = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

# t31: D3概念兜底数据源 — 热榜跌出TOP100后concept为空 → 本地concept_map(70268行, ths)
CONCEPT_DB_PATH = r"D:\workbuddyspace\databases\fundamental_data.db"

HOT_RANK_URL = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
HOT_RANK_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://eq.10jqka.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/119.0.0.0 Safari/537.36"
    ),
}

SINA_MARKET_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/"
    "api/json_v2.php/Market_Center.getHQNodeData"
)


# ===== 辅助函数 =====


def _code_with_prefix(code: str) -> str:
    """6位股票代码 -> westock API 带前缀格式 (sh/sz/bj)。"""
    code = code.strip().zfill(6)
    if code.startswith(("6", "9")):
        return "sh" + code
    elif code.startswith(("4", "8")):
        return "bj" + code
    else:
        return "sz" + code


def _norm_code(code: str) -> str:
    """统一归一化为6位代码。"""
    code = code.strip()
    for p in ("sh", "sz", "bj", "SH", "SZ", "BJ"):
        if code.startswith(p):
            code = code[len(p):]
    code = code.split(".")[0]
    return code.zfill(6)


def _board_type(symbol: str) -> str:
    """从新浪symbol推断板块类型。"""
    if symbol.startswith("sh"):
        return "上海主板"
    elif symbol.startswith("sz"):
        return "深圳主板"
    elif symbol.startswith("bj"):
        return "北京"
    return "未知"


def _is_limit_up(change_pct: float | None, board_type_: str | None = None) -> bool:
    """判断是否涨停。主板~10%, 科创/创业~20%, 北交所~30%。"""
    if change_pct is None:
        return False
    threshold = 9.5
    if board_type_:
        bt = board_type_.lower()
        if "科创" in bt or "创业" in bt:
            threshold = 19.5
        elif "北" in bt:
            threshold = 29.5
    return change_pct >= threshold


# ===== 数据获取层 =====


def fetch_westock_quote(code: str) -> dict | None:
    """从 westock MCP data_quote 获取单股行情快照。"""
    try:
        if WESTOCK_CLIENT_PATH not in sys.path:
            sys.path.insert(0, WESTOCK_CLIENT_PATH)
        import importlib
        westock_mod = importlib.import_module("westock_client")
        ws = westock_mod.WeStockClient()
        prefixed = _code_with_prefix(code)
        r = ws.call("data_quote", {"code": prefixed})
        if r.get("ok") and r.get("data"):
            data = r["data"].get(prefixed)
            if data and data.get("price") is not None:
                return data
        return None
    except Exception as e:
        logger.warning("westock quote failed for %s: %s", code, e)
        return None


def fetch_westock_kline(code: str, count: int = 25) -> list[dict] | None:
    """从 westock MCP data_kline 获取日K线。"""
    try:
        if WESTOCK_CLIENT_PATH not in sys.path:
            sys.path.insert(0, WESTOCK_CLIENT_PATH)
        import importlib
        westock_mod = importlib.import_module("westock_client")
        ws = westock_mod.WeStockClient()
        prefixed = _code_with_prefix(code)
        r = ws.call("data_kline", {"code": prefixed, "count": count})
        if r.get("ok") and r.get("data"):
            nodes = r["data"].get("nodes", [])
            if nodes:
                return nodes
        return None
    except Exception as e:
        logger.warning("westock kline failed for %s: %s", code, e)
        return None


def fetch_hot_rank(use_cache: bool = True, ttl_minutes: int = 30) -> dict[str, dict] | None:
    """获取同花顺A股热度排行榜 (日榜 TOP100)。

    阶段2.1缓存层(2026-08-18落地): use_cache=True时优先读本地缓存
    .gate_logs/hot_rank_cache.json, 年龄≤ttl_minutes直接复用; 超龄/缺失/
    损坏才打API并回写。消除"每次闸门评估必打同花顺API"的单点(限流/超时风险),
    盘中30分钟粒度足够(热榜本身约30分钟级更新)。API失败时超龄缓存仍可用
    (标注stale)——热榜仅影响D5十分权重, 降级可接受。

    Returns:
        {6位代码: {hot_order, hot_rank_chg, hot_rate, popularity_tag, concept_tags}}
    """
    cache_path = os.path.join(r"D:\zcodesj\.gate_logs", "hot_rank_cache.json")
    now = time.time()
    if use_cache:
        try:
            with open(cache_path, encoding="utf-8") as f:
                c = json.load(f)
            age = now - float(c.get("_ts", 0))
            if age <= ttl_minutes * 60 and c.get("data"):
                return c["data"]
            # 超龄但存在: API失败时的降级备胎(下面用到)
            stale = c.get("data")
        except Exception:
            stale = None
    else:
        stale = None

    url = f"{HOT_RANK_URL}?stock_type=a&type=day&list_type=normal"
    try:
        req = urllib.request.Request(url, headers=HOT_RANK_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("status_code") != 0:
            logger.warning("hot rank API error: %s", data.get("status_msg"))
            return stale
        stock_list = data.get("data", {}).get("stock_list", [])
        result: dict[str, dict] = {}
        for s in stock_list:
            raw_code = s.get("code", "")
            code = raw_code.zfill(6)
            tag = s.get("tag") or {}
            result[code] = {
                "hot_order": s.get("order"),          # 排名 1=最热
                "hot_rank_chg": s.get("hot_rank_chg"),# 排名变化
                "hot_rate": s.get("rate"),             # 热度值
                "rise_and_fall": s.get("rise_and_fall"), # 涨幅%
                "popularity_tag": tag.get("popularity_tag", ""),
                "concept_tags": tag.get("concept_tag", []),
            }
        # 回写缓存(失败不阻断)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"_ts": now, "_n": len(result), "data": result},
                          f, ensure_ascii=False)
        except Exception:
            pass
        return result
    except Exception as e:
        logger.warning("hot rank fetch failed: %s", e)
        return stale


# ===== 全市场扫描 (v2.0 新增) =====


def _fetch_sina_page(page: int, num: int = 100) -> list[dict]:
    """获取单页新浪行情数据。返回空列表表示已无更多数据。"""
    params = f"page={page}&num={num}&sort=code&asc=1&node=hs_a"
    req = urllib.request.Request(
        f"{SINA_MARKET_URL}?{params}",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
    )
    resp = urllib.request.urlopen(req, timeout=15)
    raw = resp.read().decode("gbk")
    return json.loads(raw)


def _total_pages() -> int:
    """探测新浪全市场行情总页数。"""
    for p in range(2, 100):
        page_data = _fetch_sina_page(p, 100)
        if not page_data:
            return p - 1
    return 47  # 兜底


def scan_full_market(
    max_workers: int = 8,
    progress_callback: callable | None = None,
) -> dict[str, dict]:
    """全市场扫描：从新浪API获取全部A股行情数据。

    使用线程池并行拉取所有页面 (~47页x100=4700只)，耗时约5-8秒。

    Args:
        max_workers: 并行线程数
        progress_callback: 可选进度回调 (current, total)

    Returns:
        {6位代码: {name, price, change_pct, volume, amount, board_type, ...}}
        空dict表示数据不可用。
    """
    t0 = time.time()

    # 先取第一页
    first_page = _fetch_sina_page(1, 100)
    if not first_page:
        logger.warning("scan_full_market: 第1页为空，可能非交易时段")
        return {}

    total = _total_pages()
    if total <= 0:
        return {}

    logger.info("scan_full_market: %s页, %s线程并行", total, max_workers)

    all_pages: list[list[dict]] = [first_page]
    remaining = list(range(2, total + 1))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        fut_map = {executor.submit(_fetch_sina_page, p): p for p in remaining}
        for i, fut in enumerate(as_completed(fut_map)):
            page = fut_map[fut]
            try:
                page_data = fut.result()
                if page_data:
                    all_pages.append(page_data)
            except Exception as e:
                logger.warning("scan_full_market page %s failed: %s", page, e)
            if progress_callback:
                progress_callback(i + 1, len(remaining))

    # 合并结果
    result: dict[str, dict] = {}
    for page_data in all_pages:
        for s in page_data:
            code = _norm_code(s.get("code", ""))
            if not code:
                continue
            symbol = s.get("symbol", "")
            chg_str = s.get("changepercent", "0")
            try:
                change_pct = float(chg_str)
            except (ValueError, TypeError):
                change_pct = None
            try:
                turnover = float(s.get("turnoverratio", 0)) if s.get("turnoverratio") else None
            except (ValueError, TypeError):
                turnover = None

            result[code] = {
                "code": code,
                "name": s.get("name", ""),
                "symbol": symbol,
                "board_type": _board_type(symbol),
                "price": float(s.get("trade", 0)) if s.get("trade") else None,
                "prev_close": float(s.get("settlement", 0)) if s.get("settlement") else None,
                "open": float(s.get("open", 0)) if s.get("open") else None,
                "high": float(s.get("high", 0)) if s.get("high") else None,
                "low": float(s.get("low", 0)) if s.get("low") else None,
                "change_pct": change_pct,
                "volume": int(s.get("volume", 0)) if s.get("volume") else None,
                "amount": float(s.get("amount", 0)) if s.get("amount") else None,
                "turnover_rate": turnover,
                "pe_ratio": float(s.get("per", 0)) if s.get("per") and float(s.get("per", 0)) not in (0, 0.0) else None,
                "pb_ratio": float(s.get("pb", 0)) if s.get("pb") and float(s.get("pb", 0)) not in (0, 0.0) else None,
                "mktcap": float(s.get("mktcap", 0)) if s.get("mktcap") else None,
                "nmc": float(s.get("nmc", 0)) if s.get("nmc") else None,
            }

    elapsed = time.time() - t0
    logger.info("scan_full_market: %s stocks in %.1fs", len(result), elapsed)
    return result


# ===== 板块效应计算 (v2.0 新增) =====


def calc_sector_effects(
    hot_rank: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """计算板块效应数据。

    从热榜数据的概念标签聚合：
      - 每个概念的入榜股票数、涨停数、涨幅均值、平均热度排名

    Args:
        hot_rank: fetch_hot_rank() 的输出 (含 concept_tags)

    Returns:
        {概念名: {
            limit_up_count, stock_count, avg_change, avg_hot_order, hot_stocks
        }}
    """
    if not hot_rank:
        return {}

    sector_map: dict[str, dict] = {}

    for code, info in hot_rank.items():
        concept_tags = info.get("concept_tags", [])
        if not concept_tags:
            continue
        rise_and_fall = info.get("rise_and_fall")
        hot_order = info.get("hot_order")

        for ct in concept_tags:
            if ct not in sector_map:
                sector_map[ct] = {
                    "limit_up_count": 0,
                    "stock_count": 0,
                    "changes": [],
                    "hot_ranks": [],
                    "hot_stocks": [],
                }
            sector_map[ct]["stock_count"] += 1
            sector_map[ct]["hot_stocks"].append(code)
            if rise_and_fall is not None:
                sector_map[ct]["changes"].append(rise_and_fall)
                if _is_limit_up(rise_and_fall):
                    sector_map[ct]["limit_up_count"] += 1
            if hot_order is not None:
                sector_map[ct]["hot_ranks"].append(hot_order)

    # 计算聚合值
    result: dict[str, dict] = {}
    for ct, data in sector_map.items():
        changes = data["changes"]
        hot_ranks = data["hot_ranks"]
        result[ct] = {
            "limit_up_count": data["limit_up_count"],
            "stock_count": data["stock_count"],
            "avg_change": round(sum(changes) / len(changes), 2) if changes else None,
            "avg_hot_order": round(sum(hot_ranks) / len(hot_ranks), 1) if hot_ranks else None,
            "hot_stocks": data["hot_stocks"],
        }

    return result


def get_sector_effect_for_stock(
    code: str,
    concept: str | None,
    sector_effects: dict[str, dict],
) -> dict:
    """获取单只股票的板块效应数据。

    Returns:
        {concept, sector_limit_up_count, sector_stock_count,
         sector_avg_change, sector_avg_hot_order, matched_concept}
    """
    result = {
        "concept": concept,
        "sector_limit_up_count": None,
        "sector_stock_count": None,
        "sector_avg_change": None,
        "sector_avg_hot_order": None,
        "matched_concept": None,
    }

    if not concept or not sector_effects:
        return result

    concepts = [c.strip() for c in re.split(r"[,，/、\s]+", concept) if c.strip()]
    for c in concepts:
        if c in sector_effects:
            se = sector_effects[c]
            result.update({
                "sector_limit_up_count": se.get("limit_up_count", 0),
                "sector_stock_count": se.get("stock_count", 0),
                "sector_avg_change": se.get("avg_change"),
                "sector_avg_hot_order": se.get("avg_hot_order"),
                "matched_concept": c,
            })
            break

    return result


# ===== 模糊主线匹配 (v2.0 新增) =====


def fuzzy_match_mainline(
    concept: str | None,
    mainlines: list[str] | None,
) -> tuple[bool, str, str]:
    """模糊主线匹配：检查概念是否与主线有包含关系。

    Args:
        concept: 股票概念 (如 "汽车芯片,无人驾驶")
        mainlines: 当日主线列表 (如 ["芯片", "无人驾驶", "人工智能"])

    Returns:
        (是否匹配, 匹配的概念token, 匹配的主线token)
        例: ("汽车芯片", "芯片") -> (True, "汽车芯片", "芯片")
        不匹配: (False, "", "")
    """
    if not concept or not mainlines:
        return False, "", ""

    # Token化概念
    concept_tokens = {t.strip() for t in re.split(r"[,，/、\s]+", concept) if t.strip()}

    # Token化主线
    mainline_tokens: set[str] = set()
    for m in mainlines:
        for t in re.split(r"[,，/、\s]+", m):
            t = t.strip()
            if t:
                mainline_tokens.add(t)

    # 直接匹配
    direct = concept_tokens & mainline_tokens
    if direct:
        hit = list(direct)[0]
        return True, hit, hit

    # 包含匹配: "汽车芯片" 包含 "芯片", 或反转
    for ct in concept_tokens:
        for mt in mainline_tokens:
            if (ct in mt or mt in ct) and len(ct) >= 2 and len(mt) >= 2:
                return True, ct, mt

    return False, "", ""


# ===== 数据计算层 =====


def calc_avg_amplitude_20d(kline_nodes: list[dict] | None) -> float | None:
    """从K线数据计算最近20个交易日的日均振幅(%)。"""
    if not kline_nodes or len(kline_nodes) < 2:
        return None
    recent = kline_nodes[:20]
    amplitudes: list[float] = []
    for n in recent:
        high = n.get("high")
        low = n.get("low")
        if high and low and high > 0:
            amp = (high - low) / low * 100
            amplitudes.append(amp)
    if not amplitudes:
        return None
    return round(sum(amplitudes) / len(amplitudes), 2)


# ===== t31新增: v3.0闸门三字段 (G0次新/G3断续板/G18流动性) =====

_qfq_conn = None


def _get_qfq_conn():
    """惰性单例qfq连接(sqlite轻量, 失败返回None走fail-soft)。"""
    global _qfq_conn
    if _qfq_conn is None:
        import sqlite3
        try:
            _qfq_conn = sqlite3.connect(QFQ_DB_PATH, timeout=5)
        except Exception as e:
            logger.warning("qfq db connect failed: %s", e)
            _qfq_conn = False  # 标记失败, 不反复重连
    return _qfq_conn or None


def calc_days_since_listing(code: str) -> int | None:
    """上市至今交易日数 = qfq库该code的日线总行数。

    原理: qfq从上市首日起有全历史日线, COUNT(*)即上市至今交易日数。
    实测601609最早行2020-04-22=上市日; 主键含code, 单次查询~0.01s。
    查无此code(qfq未收录/连接失败)→None(闸门fail-open记warning)。
    """
    conn = _get_qfq_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM stock_daily_meta_qfq WHERE code = ?",
            (_norm_code(code),),
        ).fetchone()
        n = int(row[0]) if row else 0
        return n if n > 0 else None
    except Exception as e:
        logger.warning("days_since_listing query failed for %s: %s", code, e)
        return None


def _limit_up_threshold(code: str | None, board_type: str | None) -> float:
    """涨停判定涨幅阈值: 10cm=9.8%(实际涨停round后9.93~10.07%), 20cm=19.8%。"""
    c = str(code or "")
    if c.startswith(("30", "68")):
        return 19.8
    bt = str(board_type or "")
    if "创业" in bt or "科创" in bt:
        return 19.8
    return 9.8


def calc_limit_up_count_10d(kline_nodes: list[dict] | None,
                            code: str | None = None,
                            board_type: str | None = None) -> int | None:
    """近10个交易日涨停次数 (G3断续板冗余校验数据源)。

    westock kline倒序(最新在前); 日涨幅=相邻收盘比(node[i].last/node[i+1].last-1)。
    已知局限: 除权日相邻比失真(前复权), 但作为streak主防线外的冗余校验可接受
    (10日窗口内除权概率低; 且主防线parse_limit_up_cnt用热榜标签)。
    K线不足2根→None(fail-soft)。"""
    if not kline_nodes or len(kline_nodes) < 2:
        return None
    threshold = _limit_up_threshold(code, board_type)
    count = 0
    days_checked = 0
    for i in range(min(10, len(kline_nodes) - 1)):
        cur = kline_nodes[i].get("last")
        prev = kline_nodes[i + 1].get("last")
        if cur and prev and prev > 0:
            chg = (cur - prev) / prev * 100
            days_checked += 1
            if chg >= threshold:
                count += 1
    return count if days_checked > 0 else None


def calc_avg_amount_20d(kline_nodes: list[dict] | None) -> float | None:
    """20日日均成交额(元) (G18流动性底线数据源)。

    westock kline前20节点amount均值(含当日); 实测金田601609 2026-08-17
    amount=15.35亿与真实成交一致。不用qfq.amount列(前复权污染风险,
    alpha005伪信号教训)。amount缺失/非正的节点跳过, 全缺→None。"""
    if not kline_nodes:
        return None
    amounts: list[float] = []
    for n in kline_nodes[:20]:
        a = n.get("amount")
        try:
            a = float(a) if a is not None else None
        except (TypeError, ValueError):
            a = None
        if a and a > 0:
            amounts.append(a)
    if not amounts:
        return None
    return round(sum(amounts) / len(amounts), 0)


_concept_conn = None


def _get_concept_conn():
    """惰性单例concept_map连接(失败返回None走fail-soft)。"""
    global _concept_conn
    if _concept_conn is None:
        import sqlite3
        try:
            _concept_conn = sqlite3.connect(CONCEPT_DB_PATH, timeout=5)
        except Exception as e:
            logger.warning("concept db connect failed: %s", e)
            _concept_conn = False
    return _concept_conn or None


def lookup_concept_map(code: str) -> str | None:
    """本地concept_map概念兜底: 热榜跌出TOP100/概念为空时查
    fundamental_data.db.concept_map (70268行, ths来源, 一票多概念逗号连接)。
    t31实证: 金田601609跌出热榜后靠此兜底拿到'东数西算(算力)'。
    查无→None(D3给0分, 维度下限封顶WARN)。"""
    conn = _get_concept_conn()
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT concept FROM concept_map WHERE code = ?",
            (_norm_code(code),),
        ).fetchall()
        concepts = [r[0] for r in rows if r and r[0]]
        if not concepts:
            return None
        return ",".join(concepts)
    except Exception as e:
        logger.warning("concept_map lookup failed for %s: %s", code, e)
        return None


def parse_limit_up_cnt(
    code: str,
    quote: dict | None,
    hot_info: dict | None,
    kline: list[dict] | None,
) -> int:
    """推断连板天数 (limit_up_streak)。

    优先级:
      1. 热榜 popularity_tag 解析 ("首板涨停"=1, "2天2板"=2, "7天5板"=5)
      2. K线最近连续涨停天数 (涨幅>=9.5%)
      3. 默认 0
    """
    # 优先级1: 热榜标签
    if hot_info:
        pop_tag = hot_info.get("popularity_tag", "")
        if pop_tag:
            m = re.search(r"(\d+)天(\d+)板", pop_tag)
            if m:
                return int(m.group(2))
            if "首板" in pop_tag:
                return 1

    # 优先级2: K线最近连续涨停
    if kline and len(kline) >= 2:
        streak = 0
        for n in kline:
            last = n.get("last", 0) or 0
            prev = n.get("open", 0) or 0
            if prev > 0:
                chg = (last - prev) / prev * 100
                if chg >= 9.5:
                    streak += 1
                else:
                    break
            else:
                break
        return streak

    return 0


def _is_st(quote: dict | None = None, name: str | None = None) -> bool | None:
    """判断是否ST/*ST股票。"""
    n = name or (quote.get("name") if quote else None)
    if n:
        return n.startswith(("*ST", "ST", "S*ST", "SST"))
    return None


# ===== 主入口 =====


def get_gate_data(
    codes: list[str],
    source: str = "manual_confirmed",
    mainlines: list[str] | None = None,
    available_cash: float | None = None,
    total_assets: float | None = None,
    holdings: list[str] | None = None,
    today_ordered: list[str] | None = None,
    sector_effects: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """为 strategy_gate.py v2.0 获取完整评分数据。

    覆盖8个评分维度所需全部字段:
      D1 volume_ratio / D2 price+daily_limit+board_type / D3 concept+mainlines
      D4 change_pct / D5 hot_rank / D6 pct_20d / D7 tags+change_pct / D8 volume_ratio

    fail-soft: 数据缺失字段返回 None (评分层给0分).

    Args:
        codes: 6位股票代码列表
        source: 信号源
        mainlines: 当日主线列表
        available_cash: 可用现金 (G10)
        total_assets: 总资产 (G10)
        holdings: 已持仓代码 (G9)
        today_ordered: 今日已下单代码 (G9)
        sector_effects: calc_sector_effects() 结果 (可选, 避免重复计算)

    Returns:
        {6位代码: 完整数据字典}
    """
    # 1. 热榜数据
    hot_rank_map = fetch_hot_rank()

    # 2. 板块效应 (如需)
    if sector_effects is None and hot_rank_map:
        sector_effects = calc_sector_effects(hot_rank=hot_rank_map)
    elif sector_effects is None:
        sector_effects = {}

    # 3. 逐股获取
    result: dict[str, dict] = {}
    for code in codes:
        code = _norm_code(code)
        entry: dict[str, Any] = {
            "code": code,
            "source": source,
            "mainlines": mainlines or [],
            "holdings": holdings or [],
            "today_ordered": today_ordered or [],
            "available_cash": available_cash,
            "total_assets": total_assets,
            # 评分维度字段 (fail-soft: 默认None)
            "price": None,           # D2 / G1
            "change_pct": None,      # D4 / G8
            "volume_ratio": None,    # D1 / D8
            "pct_20d": None,         # D6 / G4
            "daily_limit": None,     # D2
            "board_type": None,      # D2
            "hot_rank": None,        # D5 / G6
            "hot_tag": None,         # D7 / G5/G6/G8
            "concept": None,         # D3 / G7
            "tags": [],              # D7 / G5/G6/G8/G15
            "limit_up_streak": 0,    # D7 / G3
            "avg_amplitude_20d": None,  # G14
            "name": None,            # 显示
            # t31新增: v3.0闸门三字段 (G0次新/G3断续板/G18流动性)
            "days_since_listing": None,   # G0
            "limit_up_count_10d": None,   # G3
            "avg_amount_20d": None,       # G18
        }

        # 3a. westock 行情快照
        quote = fetch_westock_quote(code)
        if quote:
            entry["name"] = quote.get("name")
            entry["price"] = quote.get("price")
            entry["prev_close"] = quote.get("prev_close")
            entry["open"] = quote.get("open")
            entry["high"] = quote.get("high")
            entry["low"] = quote.get("low")
            entry["change"] = quote.get("change")
            entry["change_pct"] = quote.get("change_percent")       # D4
            entry["volume_ratio"] = quote.get("volume_ratio")       # D1 / D8
            entry["volume"] = quote.get("volume")
            entry["amount"] = quote.get("amount")
            entry["turnover_rate"] = quote.get("turnover_rate")
            entry["pct_20d"] = quote.get("chg_20d")                 # D6
            entry["pct_10d"] = quote.get("chg_10d")
            entry["pct_5d"] = quote.get("chg_5d")
            entry["pct_60d"] = quote.get("chg_60d")
            entry["pct_ytd"] = quote.get("chg_ytd")
            entry["daily_limit"] = quote.get("price_ceiling")       # D2
            entry["daily_floor"] = quote.get("price_floor")
            entry["board_type"] = quote.get("market_name")          # D2
            entry["market_type"] = quote.get("market_type")
            entry["high_52week"] = quote.get("high_52week")
            entry["low_52week"] = quote.get("low_52week")
            entry["pe_ratio"] = quote.get("pe_ratio")
            entry["pe_fwd"] = quote.get("pe_fwd")
            entry["pe_lyr"] = quote.get("pe_lyr")
            entry["pb_ratio"] = quote.get("pb_ratio")
            entry["total_market_cap"] = quote.get("total_market_cap")
            entry["circ_market_cap"] = quote.get("circulating_market_cap")
            entry["outer_volume"] = quote.get("outer_volume")
            entry["inner_volume"] = quote.get("inner_volume")
            entry["wb_ratio"] = quote.get("wb_ratio")
            entry["dividend_ratio"] = quote.get("dividend_ratio_ttm")
            entry["is_st"] = _is_st(quote=quote)

        # 3b. westock K线 (日均振幅, 连板辅助)
        kline = fetch_westock_kline(code, count=25)
        entry["kline_nodes"] = kline
        entry["avg_amplitude_20d"] = calc_avg_amplitude_20d(kline)  # G14

        # 3c. 同花顺热榜
        hot_info = hot_rank_map.get(code) if hot_rank_map else None
        if hot_info:
            entry["hot_rank"] = hot_info.get("hot_order")            # D5 / G6
            entry["hot_rank_chg"] = hot_info.get("hot_rank_chg")
            entry["hot_rate"] = hot_info.get("hot_rate")
            entry["hot_tag"] = hot_info.get("popularity_tag", "")    # D7
            entry["concept_tags"] = hot_info.get("concept_tags", []) # D3
            entry["concept"] = (
                ",".join(hot_info["concept_tags"])
                if hot_info.get("concept_tags")
                else None
            )                                                         # D3 / G7
        if not entry.get("concept"):
            # 3c2. t31概念兜底: 热榜跌出TOP100/概念为空 → 本地concept_map
            #      (防D3主线匹配数据断供; t16曾主张的兜底在fetcher物化)
            fb = lookup_concept_map(code)
            if fb:
                entry["concept"] = fb
                entry["concept_source"] = "concept_map"

        # 3d. 连板计数
        entry["limit_up_streak"] = parse_limit_up_cnt(code, quote, hot_info, kline)  # G3 / D7

        # 3e. tags 统一列表 (hot_tag + concept_tags)
        tags: list[str] = []
        if entry.get("hot_tag"):
            tags.append(str(entry["hot_tag"]))
        if entry.get("concept_tags"):
            for ct in entry["concept_tags"]:
                if ct not in tags:
                    tags.append(str(ct))
        entry["tags"] = tags  # D7 / G5/G6/G8/G15

        # 3f. 板块效应 (D3)
        entry["sector_effect"] = get_sector_effect_for_stock(
            code, entry.get("concept"), sector_effects
        )

        # 3g. 模糊主线匹配 (D3)
        fm = fuzzy_match_mainline(entry.get("concept"), mainlines)
        entry["mainline_match"] = {
            "matched": fm[0],
            "concept_token": fm[1],
            "mainline_token": fm[2],
        }

        # 3h. 数据标记
        entry["data_date"] = datetime.now().strftime("%Y-%m-%d")
        entry["is_trading_day"] = datetime.now().weekday() < 5

        # 3i. t31新增: v3.0闸门三字段 (G0次新/G3断续板/G18流动性)
        #     补齐后 strategy_gate.py 对应fail-open warning自动转为实校验
        entry["days_since_listing"] = calc_days_since_listing(code)          # G0
        entry["limit_up_count_10d"] = calc_limit_up_count_10d(
            kline, code, entry.get("board_type"))                            # G3
        entry["avg_amount_20d"] = calc_avg_amount_20d(kline)                 # G18

        result[code] = entry

    return result


# ===== CLI 入口 =====


def _print_json(data, pretty: bool = False):
    indent = 2 if pretty else None
    print(json.dumps(data, ensure_ascii=False, indent=indent, default=str))


def main() -> None:
    p = argparse.ArgumentParser(
        description="gate_data_fetcher.py v2.0 — 数据取数API (全市场+板块效应+模糊匹配)"
    )

    # 常规参数
    p.add_argument("--codes", help="逗号分隔的6位股票代码")
    p.add_argument("--source", default="manual_confirmed",
                   choices=["bp_ep_core", "turnover_mvp", "mainline_radar",
                            "pattern_match", "manual_confirmed",
                            "full_market_scan", "daily_pipeline",
                            "early_board", "early_board_r1", "dragon_r3"])
    p.add_argument("--mainlines", default="", help="逗号分隔的主线列表")
    p.add_argument("--pretty", action="store_true", help="格式化输出")

    # 资金/持仓
    p.add_argument("--available-cash", type=float, dest="available_cash")
    p.add_argument("--total-assets", type=float, dest="total_assets")
    p.add_argument("--holdings", default="", help="逗号分隔的持仓代码")
    p.add_argument("--today-ordered", default="", help="逗号分隔的今日已下单代码")

    # v2.0 新增操作模式
    p.add_argument("--scan-full-market", action="store_true",
                   help="全市场扫描模式")
    p.add_argument("--sector-effects", action="store_true",
                   help="板块效应计算模式")
    p.add_argument("--max-workers", type=int, default=8,
                   help="全市场扫描并行线程数")

    args = p.parse_args()

    # ---- 全市场扫描模式 ----
    if args.scan_full_market:
        print("正在全市场扫描...", file=sys.stderr)
        market = scan_full_market(max_workers=args.max_workers)
        if not market:
            print("扫描失败：数据不可用", file=sys.stderr)
            sys.exit(1)
        print(f"完成: {len(market)}只股票", file=sys.stderr)

        # 统计
        limit_ups = [
            s for s in market.values()
            if s.get("change_pct") is not None and s["change_pct"] >= 9.5
        ]
        up_stocks = [
            s for s in market.values()
            if s.get("change_pct") is not None and s["change_pct"] >= 0
        ]
        print(f"涨停: {len(limit_ups)}只 | 上涨: {len(up_stocks)}只", file=sys.stderr)

        # 输出精简摘要
        summary = {}
        for code, info in market.items():
            summary[code] = {
                "name": info["name"],
                "price": info["price"],
                "change_pct": info["change_pct"],
                "board_type": info["board_type"],
                "volume": info["volume"],
                "turnover_rate": info["turnover_rate"],
            }
        _print_json(summary, args.pretty)
        return

    # ---- 板块效应模式 ----
    if args.sector_effects:
        print("正在获取热榜数据...", file=sys.stderr)
        hot_rank = fetch_hot_rank()
        if not hot_rank:
            print("热榜数据不可用", file=sys.stderr)
            sys.exit(1)
        effects = calc_sector_effects(hot_rank=hot_rank)
        print(f"完成: {len(effects)}个概念板块", file=sys.stderr)

        # 按涨停数排序输出
        sorted_effects = dict(
            sorted(effects.items(), key=lambda x: x[1]["limit_up_count"], reverse=True)
        )
        _print_json(sorted_effects, args.pretty)
        return

    # ---- 数据获取模式 (默认) ----
    if not args.codes:
        p.print_help()
        return

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    mainlines = [m.strip() for m in args.mainlines.split(",") if m.strip()] if args.mainlines else None
    holdings = [h.strip().zfill(6) for h in args.holdings.split(",") if h.strip()] if args.holdings else None
    today_ordered = [t.strip().zfill(6) for t in args.today_ordered.split(",") if t.strip()] if args.today_ordered else None

    # 自动计算板块效应
    print("正在获取热榜数据...", file=sys.stderr)
    hot_rank = fetch_hot_rank()
    sector_effects = calc_sector_effects(hot_rank=hot_rank) if hot_rank else {}

    data = get_gate_data(
        codes=codes,
        source=args.source,
        mainlines=mainlines,
        available_cash=args.available_cash,
        total_assets=args.total_assets,
        holdings=holdings,
        today_ordered=today_ordered,
        sector_effects=sector_effects,
    )

    _print_json(data, args.pretty)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()