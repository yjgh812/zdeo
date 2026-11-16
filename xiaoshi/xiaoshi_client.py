# -*- coding: utf-8 -*-
"""
小石大数据 API 共享客户端 (xiaoshi_client)
==========================================
- 只读金融数据客户端, 从环境变量 XIAOSHI_API_KEY 读取密钥(不落盘、不展示、不转发)。
- 遵守小石 Manifest 契约: 单位/币种/复权/时区以数据字典为准; 429/bulk_download_required 为平台保护。
- 错误契约: 返回含应用/数据/Manifest/prompt 版本、接口、脱敏参数与错误指纹, 不含 Key/凭据/完整响应。

版本: xiaoshi-client-2026-08-22.1
"""
import json
import hashlib
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.shizixi.com"
APP_VERSION = "2026-08-22.1"
PROMPT_VERSION = "2026-08-22.1"
DATA_VERSION = "history-canonical-v2-20260821T093000Z"
MANIFEST_VERSION = "history-public-v1-20260821T093000Z"
CANONICAL_CONTRACT = "xiaoshi-history-public-v1"
# 记录来源 manifest 的校验和(用于版本一致性核对)
PROMPT_SHA256 = "7c3f2f036419682ebc88d2ba57b90164b5a5047f961ae7505f3382bbefee54e9"
SKILL_SHA256 = "47a4cc16c7341456a82d312a1b1f442655946a3a078b84d8b63ecc313fbfea56"
API_SCHEMA_SHA256 = "1c87568a17f4eb68eb173871a5058f951b24c0b4e6e12fb0f7b6e2233294b233"


class XiaoshiError(Exception):
    """小石 API 错误。携带脱敏后的上下文, 不含密钥或完整响应。"""

    def __init__(self, message, endpoint="", status=None, fingerprint=""):
        self.message = message
        self.endpoint = endpoint
        self.status = status
        self.fingerprint = fingerprint or fingerprint_of(endpoint, status, message)
        super().__init__(f"[xiaoshi] {message} (endpoint={endpoint}, status={status}, fp={self.fingerprint})")


def fingerprint_of(*parts):
    """稳定错误指纹: 由端点/状态/摘要构成, 不含敏感参数。"""
    s = "|".join(str(p) for p in parts if p is not None)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def redact_params(params):
    """脱敏查询参数: 仅保留参数名集合, 不保留值(避免 Key/凭据入错误上下文)。"""
    if not params:
        return {}
    return {k: "<redacted>" for k in params.keys()}


def get_key():
    key = os.environ.get("XIAOSHI_API_KEY", "")
    if not key:
        raise XiaoshiError(
            "XIAOSHI_API_KEY 未设置。请通过 Windows 用户环境变量提供, 勿写入项目文件。",
            endpoint="env",
            fingerprint=fingerprint_of("env", "missing_key"),
        )
    return key


def _request(path, params=None, method="GET", auth=True, body=None, no_cache=False, timeout=60):
    url = BASE + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method)
    if auth:
        req.add_header("Authorization", "Bearer " + get_key())
    if no_cache:
        req.add_header("Cache-Control", "no-store, no-cache")
    if body is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ct = r.headers.get("Content-Type", "")
            if "application/json" in ct:
                try:
                    return json.loads(raw)
                except Exception:
                    return raw
            return raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        detail = ""
        try:
            j = json.loads(raw)
            if isinstance(j, dict):
                detail = str(j.get("detail") or j.get("error") or j.get("message") or "")
        except Exception:
            detail = raw[:200].decode("utf-8", "replace")
        fp = fingerprint_of(path, e.code, detail[:200])
        raise XiaoshiError(
            f"HTTP {e.code}: {detail[:300]}",
            endpoint=path,
            status=e.code,
            fingerprint=fp,
        )
    except urllib.error.URLError as e:
        fp = fingerprint_of(path, "url", str(e.reason)[:200])
        raise XiaoshiError(
            f"网络错误: {e.reason}", endpoint=path, fingerprint=fp
        )


def get_json(path, params=None, auth=True, no_cache=False, timeout=60):
    return _request(path, params=params, method="GET", auth=auth, no_cache=no_cache, timeout=timeout)


def post_json(path, body=None, params=None, auth=True, timeout=60):
    return _request(path, params=params, method="POST", auth=auth, body=body, timeout=timeout)


# ---------------------------------------------------------------------------
# 发现与版本
# ---------------------------------------------------------------------------

def get_manifest(no_cache=True):
    """读取主 Manifest(每个新任务无缓存查一次, 版本/SHA 未变不下载正文)。"""
    return get_json("/api/v3/manifest", auth=False, no_cache=no_cache)


def get_history_manifest(no_cache=True):
    """读取历史分发 Manifest(history-public-v1)。"""
    return get_json("/api/v3/history/manifest", auth=True, no_cache=no_cache)


def check_api_key():
    return get_json("/api/v3/auth/api-key/check", auth=True, no_cache=True)


def get_capabilities():
    return get_json("/api/v3/market/capabilities", auth=True, no_cache=True)


# ---------------------------------------------------------------------------
# 实时行情
# ---------------------------------------------------------------------------

def quote(symbol, market="CN", instrument="stock"):
    """统一报价 /api/v3/market/quote/{symbol} (推荐)。"""
    return get_json(f"/api/v3/market/quote/{urllib.parse.quote(str(symbol))}",
                    params={"market": market, "instrument": instrument})


def quote_legacy(code):
    """旧版单只报价 /api/v3/data/quote/{code} (含五档盘口)。"""
    return get_json(f"/api/v3/data/quote/{urllib.parse.quote(str(code))}")


def quotes_batch(requests_list):
    """混合市场批量报价 POST /api/v3/market/quotes。requests_list: [{symbol,market,instrument},...]"""
    return post_json("/api/v3/market/quotes", body={"requests": requests_list})


def indices():
    return get_json("/api/v3/data/indices")


def market_snapshot(market="CN", offset=0, limit=6000):
    return get_json("/api/v3/data/market-snapshot", params={"market": market, "offset": offset, "limit": limit})


def market_sentiment():
    return get_json("/api/v3/data/market-sentiment")


def stocks(exchange="", industry="", offset=0, limit=6000):
    params = {"offset": offset, "limit": limit}
    if exchange:
        params["exchange"] = exchange
    if industry:
        params["industry"] = industry
    return get_json("/api/v3/data/stocks", params=params)


def search(q, market="ALL", limit=20):
    return get_json("/api/v3/data/search", params={"q": q, "market": market, "limit": limit})


def kline(code, period="daily", adjust="qfq", limit=3000, since=None, to=None, offset=0, market="CN", instrument="stock"):
    """统一日线。A股默认 qfq。"""
    params = {"period": period, "adjust": adjust, "limit": limit, "offset": offset}
    if market and market != "CN":
        params["market"] = market
    if instrument and instrument != "stock":
        params["instrument"] = instrument
    if since:
        params["since"] = since
    if to:
        params["to"] = to
    return get_json(f"/api/v3/data/kline/{urllib.parse.quote(str(code))}", params=params)


def kline_batch(codes, period="daily", adjust="qfq", limit=3000, since=None, to=None, offset=0):
    params = {"codes": ",".join(str(c) for c in codes), "period": period, "adjust": adjust,
              "limit": limit, "offset": offset}
    if since:
        params["since"] = since
    if to:
        params["to"] = to
    return get_json("/api/v3/data/kline/batch", params=params)


def kline_intraday(code, period="1min", adjust="none", limit=250, market="CN"):
    params = {"period": period, "adjust": adjust, "limit": limit}
    if market and market != "CN":
        params["market"] = market
    return get_json(f"/api/v3/stock/kline/{urllib.parse.quote(str(code))}", params=params)


def adjust_factors(code, adjust="qfq", since=None, to=None):
    params = {"code": code, "adjust": adjust}
    if since:
        params["since"] = since
    if to:
        params["to"] = to
    return get_json("/api/v3/data/adjust_factors", params=params)


# ---------------------------------------------------------------------------
# 财务 / 基本面 / 盘后快照
# ---------------------------------------------------------------------------

def financials(code=None, name=None, type_="all", periods=8):
    params = {"type": type_, "periods": periods}
    if code:
        return get_json(f"/api/v3/data/financials/{urllib.parse.quote(str(code))}", params=params)
    params["name"] = name
    return get_json("/api/v3/data/financials", params=params)


def fundamentals(code, date=None):
    params = {}
    if date:
        params["date"] = date
    return get_json(f"/api/v3/stock/fundamentals/{urllib.parse.quote(str(code))}", params=params)


def eod_snapshot(date=None, limit=1000, sort_by="amount", order="desc"):
    params = {"limit": limit, "sort_by": sort_by, "order": order}
    if date:
        params["date"] = date
    return get_json("/api/v3/stock/eod-snapshot", params=params)


# ---------------------------------------------------------------------------
# 新闻 / 公告 / 事件
# ---------------------------------------------------------------------------

def news_industry_taxonomy():
    return get_json("/api/v3/news/industry-taxonomy")


def news(params):
    """增量: after_id+page_size; 首拉: after_id=0+since/until(<=31天)。"""
    return get_json("/api/v3/news", params=params)


def flash(limit=20, industry=None):
    params = {"limit": limit}
    if industry:
        params["industry"] = industry
    return get_json("/api/v3/public/flash", params=params)


def announcements(code, days=365, page=1, page_size=20):
    return get_json(f"/api/v3/stock/announcements/{urllib.parse.quote(str(code))}",
                    params={"days": days, "page": page, "page_size": page_size})


def quant_events(params):
    """统一事件时间轴 /api/v3/quant/events。"""
    return get_json("/api/v3/quant/events", params=params)


# ---------------------------------------------------------------------------
# 龙虎榜 / 资金流 / 中低频因子
# ---------------------------------------------------------------------------

def lhb(date=None, limit=500, code=None):
    if code:
        params = {"limit": limit}
        if date:
            params["date"] = date
        return get_json(f"/api/v3/stock/lhb/{urllib.parse.quote(str(code))}", params=params)
    params = {"limit": limit}
    if date:
        params["date"] = date
    return get_json("/api/v3/stock/lhb", params=params)


def fundflow(code, date=None):
    params = {}
    if date:
        params["date"] = date
    return get_json(f"/api/v3/stock/fundflow/{urllib.parse.quote(str(code))}", params=params)


def factors_library():
    return get_json("/api/v3/factors/library")


def quant_data(dataset, code=None, since=None, to=None, limit=500):
    params = {"limit": limit}
    if code:
        params["code"] = code
    if since:
        params["since"] = since
    if to:
        params["to"] = to
    return get_json(f"/api/v3/quant-data/{urllib.parse.quote(str(dataset))}", params=params)


# ---------------------------------------------------------------------------
# 板块
# ---------------------------------------------------------------------------

def sector_industries(taxonomy="primary", level=1, offset=0, limit=90):
    return get_json("/api/v3/sector/industries",
                    params={"taxonomy": taxonomy, "level": level, "offset": offset, "limit": limit})


def sector_concepts(sort_by="涨跌幅", limit=50):
    return get_json("/api/v3/sector/concepts", params={"sort_by": sort_by, "limit": limit})


def sector_fundflow_rank(type_="industry", limit=50):
    return get_json("/api/v3/sector/fundflow-rank", params={"type": type_, "limit": limit})


def sector_constituents(taxonomy="primary", board=None, type_="industry", offset=0, limit=1000):
    params = {"taxonomy": taxonomy, "type": type_, "offset": offset, "limit": limit}
    if board:
        params["board"] = board
    return get_json("/api/v3/sector/cons", params=params)


def sector_history(sector_code, since=None, to=None, sector_type="industry"):
    params = {"sector_code": sector_code, "sector_type": sector_type}
    if since:
        params["since"] = since
    if to:
        params["to"] = to
    return get_json("/api/v3/sector/history", params=params)


# ---------------------------------------------------------------------------
# 历史数据分发 (catalog / download-session)
# ---------------------------------------------------------------------------

def history_catalog(dataset=None, market=None, since=None, to=None):
    params = {}
    if dataset:
        params["dataset"] = dataset
    if market:
        params["market"] = market
    if since:
        params["since"] = since
    if to:
        params["to"] = to
    return get_json("/api/v3/history/catalog", params=params)


def history_download_session(**params):
    """请求 R2 预签名下载会话(有效期 7200s)。参数见 download_contracts。"""
    return get_json("/api/v3/history/download-session", params=params)


# ---------------------------------------------------------------------------
# 研报 / 宏观 / 影响力事件
# ---------------------------------------------------------------------------

def macro_latest(region=None, code=None, limit=30):
    params = {"limit": limit}
    if region:
        params["region"] = region
    if code:
        params["code"] = code
    return get_json("/api/v3/public/macro/latest", params=params)


def influencers(q=None, category=None, region=None, limit=200):
    params = {"limit": limit}
    if q:
        params["q"] = q
    if category:
        params["category"] = category
    if region:
        params["region"] = region
    return get_json("/api/v3/public/influencers", params=params)


def influencer_news(person, limit=20):
    return get_json("/api/v3/public/influencer-news", params={"person": person, "limit": limit})


def research_news(institution=None, limit=30):
    params = {"limit": limit}
    if institution:
        params["institution"] = institution
    return get_json("/api/v3/public/research-news", params=params)


def movers_news():
    return get_json("/api/v3/public/movers-news")


def briefings_latest(report_type="morning"):
    return get_json("/api/v3/briefings/latest", params={"report_type": report_type})


def briefings_topics(limit=6):
    return get_json("/api/v3/briefings/topics", params={"limit": limit})


def briefings_industries(limit=20):
    return get_json("/api/v3/briefings/industries", params={"limit": limit})


def briefings_industry_latest(industry):
    return get_json("/api/v3/briefings/industry/latest", params={"industry": industry})
