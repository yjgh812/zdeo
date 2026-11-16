# -*- coding: utf-8 -*-
"""
小石大数据 MCP 服务器 (xiaoshi-mcp)
===================================
- 只读金融数据服务; 密钥经 XIAOSHI_API_KEY 环境变量注入, 不落盘、不展示、不转发。
- 遵循小石 Manifest 契约: 单位/币种/复权/时区以数据字典为准; 429 为平台保护。
- 错误契约: 错误消息含版本/接口/脱敏参数/错误指纹, 不含 Key/凭据/完整响应。

启动: python D:/zcodesj/mcp_servers/xiaoshi_mcp.py
"""
import os
import sys

sys.path.insert(0, r"D:\zcodesj\xiaoshi")
from mcp.server.fastmcp import FastMCP

import xiaoshi_client as xc

mcp = FastMCP("xiaoshi-mcp")

_APP = xc.APP_VERSION
_PROMPT = xc.PROMPT_VERSION
_DATA = xc.DATA_VERSION


def _err(e):
    return f"[xiaoshi error] {e} (app={_APP}, prompt={_PROMPT}, data={_DATA})"


@mcp.tool()
def xiaoshi_manifest() -> str:
    """读取小石主 Manifest(每个新任务无缓存查一次, 版本/SHA 未变不下载正文)。返回版本、校验和、端点契约。"""
    try:
        import json
        m = xc.get_manifest(no_cache=True)
        keep = {k: m.get(k) for k in
                ["manifest_version", "prompt_version", "skill_version", "prompt_id", "service_model",
                 "api_base", "market_data_base", "unified_market_base", "r2_base",
                 "prompt_sha256", "skill_sha256", "api_schema_sha256", "access_policy", "refresh_policy"]}
        return json.dumps(keep, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_history_manifest() -> str:
    """读取小石历史分发 Manifest(history-public-v1)。含 8 数据集覆盖范围(首末日期/文件数/行数)、schema、download_contracts。"""
    try:
        import json
        m = xc.get_history_manifest(no_cache=True)
        keep = {
            "version": m.get("version"),
            "quality_status": m.get("quality_status"),
            "publication_gate": m.get("publication_gate"),
            "coverage": m.get("coverage"),
            "datasets": {k: {"schema_version": v.get("schema_version"),
                             "primary_key": v.get("primary_key"),
                             "columns": v.get("columns"),
                             "time": v.get("time"),
                             "timezone": v.get("timezone"),
                             "adjustment": v.get("adjustment"),
                             "currency": v.get("currency")}
                         for k, v in (m.get("schemas") or {}).items()},
            "download_contracts": m.get("download_contracts"),
            "download_endpoint": m.get("download_endpoint"),
            "expires_in": m.get("expires_in"),
        }
        return json.dumps(keep, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_catalog(dataset: str = "", market: str = "") -> str:
    """列出小石历史 catalog 文件。先读 catalog/schema/coverage, 不遍历 R2 key 猜结构。dataset 如 cn-daily/cn-minute/global-daily/adjustment-factors。"""
    try:
        import json
        cat = xc.history_catalog(dataset=dataset or None, market=market or None)
        keep = {
            "version": cat.get("version"),
            "contract": cat.get("canonical_contract"),
            "count": cat.get("count"),
            "total": cat.get("total"),
            "items": cat.get("items"),
        }
        return json.dumps(keep, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_schema(dataset: str = "") -> str:
    """展示小石数据集 schema(数据字典)。dataset 为空返回全部; 指定则返回单数据集字段、单位、币种、复权、时区、主键、PIT 规则。"""
    try:
        import json
        m = xc.get_history_manifest()
        schemas = m.get("schemas") or {}
        if dataset:
            if dataset not in schemas:
                return f"数据集 {dataset} 不在 Manifest。可用: {sorted(schemas.keys())}"
            return json.dumps(schemas[dataset], ensure_ascii=False, indent=2)
        return json.dumps(schemas, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_coverage(dataset: str = "") -> str:
    """展示小石数据集覆盖范围(首末日期/文件数/行数/字节)。dataset 为空返回全部。空/缺失/停牌/非交易日 != 0。"""
    try:
        import json
        m = xc.get_history_manifest()
        cov = m.get("coverage") or {}
        if dataset:
            if dataset not in cov:
                return f"数据集 {dataset} 无覆盖记录。可用: {sorted(cov.keys())}"
            return json.dumps(cov[dataset], ensure_ascii=False, indent=2)
        return json.dumps(cov, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_quote(symbol: str, market: str = "CN", instrument: str = "stock") -> str:
    """统一实时报价(推荐)。A股 example: 600519/CN/stock; 港股 00700/HK/stock; 美股 AAPL/US/stock; 指数需 instrument=index 且须在 capabilities.live_index_symbols 中。"""
    try:
        import json
        q = xc.quote(symbol, market=market, instrument=instrument)
        keep = {k: q.get(k) for k in
                ["schema_version", "market", "instrument", "symbol", "name", "exchange", "currency",
                 "price", "previous_close", "open", "high", "low", "change", "change_pct", "amplitude_pct",
                 "volume", "amount", "turnover_pct", "bid1", "ask1", "mid_price", "spread", "spread_bps",
                 "source", "updated_at", "ts"] if k in q}
        return json.dumps(keep, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_quotes_batch(requests_json: str) -> str:
    """混合市场批量报价。requests_json 形如 [{"symbol":"600519","market":"CN","instrument":"stock"},{"symbol":"AAPL","market":"US","instrument":"stock"}]。单次1-100个。"""
    try:
        import json
        reqs = json.loads(requests_json)
        if not isinstance(reqs, list) or not (1 <= len(reqs) <= 100):
            return "requests 必须是 1-100 个对象的数组"
        return json.dumps(xc.quotes_batch(reqs), ensure_ascii=False, indent=2)
    except (xc.XiaoshiError, Exception) as e:
        return _err(e) if isinstance(e, xc.XiaoshiError) else f"[input error] {e}"


@mcp.tool()
def xiaoshi_market_snapshot(market: str = "CN", offset: int = 0, limit: int = 6000) -> str:
    """全市场实时快照(单次请求)。market=CN|HK|US。用于全市场扫描, 不要逐只轮询。"""
    try:
        import json
        snap = xc.market_snapshot(market=market, offset=offset, limit=limit)
        keep = {k: snap.get(k) for k in
                ["market", "updated_at", "freshness_sec", "coverage", "summary", "count", "service_counts"]
                if k in snap}
        keep["items"] = snap.get("items") or snap.get("quotes") or []
        return json.dumps(keep, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_indices() -> str:
    """主要 A 股指数行情。"""
    try:
        import json
        return json.dumps(xc.indices(), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_market_sentiment() -> str:
    """市场情绪/盘面广度(market-breadth-v1)。仅 status=available 家族可作为正式输入; proxy 标注近似; unavailable 不为0。"""
    try:
        import json
        return json.dumps(xc.market_sentiment(), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_search(q: str, market: str = "ALL", limit: int = 20) -> str:
    """搜索股票代码/名称。保留返回的 market 用于后续 quote/kline。"""
    try:
        import json
        return json.dumps(xc.search(q, market=market, limit=limit), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_stocks(exchange: str = "", industry: str = "", offset: int = 0, limit: int = 6000) -> str:
    """冻结可复现研究股票池(不是实时价源)。exchange 如 SH/SZ/BJ, industry 如 半导体。"""
    try:
        import json
        return json.dumps(xc.stocks(exchange=exchange, industry=industry, offset=offset, limit=limit),
                          ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_kline(symbol: str, period: str = "daily", adjust: str = "qfq",
                  limit: int = 3000, since: str = "", to: str = "",
                  market: str = "CN", instrument: str = "stock") -> str:
    """历史K线。A股默认 qfq; HK/US 日线 adjust=none(本地货币/时区)。period=daily|1min|5min|15min|30min。返回含 first_date/last_date/count/adjust/factor_date。"""
    try:
        import json
        data = xc.kline(symbol, period=period, adjust=adjust, limit=limit,
                        since=since or None, to=to or None, market=market, instrument=instrument)
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_kline_batch(codes: str, period: str = "daily", adjust: str = "qfq",
                        limit: int = 3000, since: str = "", to: str = "") -> str:
    """批量K线, codes 逗号分隔, 最多100个/次。"""
    try:
        import json
        cl = [c.strip() for c in codes.split(",") if c.strip()]
        if not (1 <= len(cl) <= 100):
            return "codes 数量须为 1-100"
        data = xc.kline_batch(cl, period=period, adjust=adjust, limit=limit,
                              since=since or None, to=to or None)
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_kline_intraday(symbol: str, period: str = "1min", adjust: str = "none",
                           limit: int = 250, market: str = "CN") -> str:
    """实时分钟K线。A股支持 none/qfq/hfq; HK/US 仅 adjust=none。"""
    try:
        import json
        data = xc.kline_intraday(symbol, period=period, adjust=adjust, limit=limit, market=market)
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_adjust_factors(code: str, adjust: str = "qfq", since: str = "", to: str = "") -> str:
    """复权因子。factor_rule: 复权价 = 原始价 * adj_factor。"""
    try:
        import json
        data = xc.adjust_factors(code, adjust=adjust, since=since or None, to=to or None)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_financials(code: str = "", name: str = "", type_: str = "all", periods: int = 8) -> str:
    """财务报表(含 abstract/balance/profit/cashflow)。保留报告期/公告日。code 或 name 二选一。"""
    try:
        import json
        data = xc.financials(code=code or None, name=name or None, type_=type_, periods=periods)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_fundamentals(code: str, date: str = "") -> str:
    """基本面快照(估值/股本)。date=YYYY-MM-DD 可选。"""
    try:
        import json
        data = xc.fundamentals(code, date=date or None)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_eod_snapshot(date: str = "", limit: int = 1000, sort_by: str = "amount", order: str = "desc") -> str:
    """盘后全市场快照/排行(已完成交易时段)。金额为元。"""
    try:
        import json
        data = xc.eod_snapshot(date=date or None, limit=limit, sort_by=sort_by, order=order)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_news_industry_taxonomy() -> str:
    """行业新闻分类(20 个稳定行业+别名)。查行业新闻前先读本接口取规范名。"""
    try:
        import json
        return json.dumps(xc.news_industry_taxonomy(), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_news_incremental(after_id: int = 0, since: str = "", until: str = "", page_size: int = 100) -> str:
    """增量新闻。after_id=0 首拉; 已同步用 after_id=<last_seen_id>。since/until 时间窗 <=31天。返回 mode/items/next_after_id/has_more。"""
    try:
        import json
        params = {"page_size": page_size}
        if after_id:
            params["after_id"] = after_id
        elif since:
            params["after_id"] = 0
            params["since"] = since
            params["until"] = until or since
        data = xc.news(params)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_flash(limit: int = 20, industry: str = "") -> str:
    """快讯(全市场或按行业)。"""
    try:
        import json
        data = xc.flash(limit=limit, industry=industry or None)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_announcements(code: str, days: int = 365, page: int = 1, page_size: int = 20) -> str:
    """股票公告(巨潮/交易所)。保留公告日。"""
    try:
        import json
        data = xc.announcements(code, days=days, page=page, page_size=page_size)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_lhb(date: str = "", code: str = "", limit: int = 500) -> str:
    """龙虎榜。date=YYYY-MM-DD 当日全市场; code 查单只。区分 no_public_records 与 source_unavailable。"""
    try:
        import json
        data = xc.lhb(date=date or None, code=code or None, limit=limit)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_fundflow(code: str, date: str = "") -> str:
    """个股资金流(最新快照; 历史累积中)。空数据 != 零流。"""
    try:
        import json
        data = xc.fundflow(code, date=date or None)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_factors_library() -> str:
    """因子库(含成熟度/最新衍生快照)。maturity/history_status; research_ready != 已验证历史。"""
    try:
        import json
        return json.dumps(xc.factors_library(), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_quant_data(dataset: str, code: str = "", since: str = "", to: str = "", limit: int = 500) -> str:
    """中低频量化数据: northbound_holdings/margin_trading/stock_fundflow/block_trades/shareholder_count/top_shareholders/restricted_unlock/broker_consensus/etf_flow/derivatives_sentiment/convertible_bonds/high_frequency_macro。"""
    try:
        import json
        data = xc.quant_data(dataset, code=code or None, since=since or None, to=to or None, limit=limit)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_sector_industries(taxonomy: str = "primary", level: int = 1, limit: int = 90) -> str:
    """行业板块行情。primary=版本化研究行业目录; secondary=当前会话行业行情(涨跌/换手/资金流)。"""
    try:
        import json
        data = xc.sector_industries(taxonomy=taxonomy, level=level, limit=limit)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_sector_fundflow_rank(type_: str = "industry", limit: int = 50) -> str:
    """板块资金流排行。type=industry|concept。"""
    try:
        import json
        data = xc.sector_fundflow_rank(type_=type_, limit=limit)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_sector_constituents(taxonomy: str = "primary", board: str = "", type_: str = "industry", limit: int = 1000) -> str:
    """板块成分股(当前快照)。board 为板块代码或名称, 如 煤炭。"""
    try:
        import json
        data = xc.sector_constituents(taxonomy=taxonomy, board=board or None, type_=type_, limit=limit)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_sector_history(sector_code: str, since: str = "", to: str = "", sector_type: str = "industry") -> str:
    """板块日频历史(每板块每交易日 PIT 记录: 等权OHLC/资金流/广度/主线/成分覆盖)。"""
    try:
        import json
        data = xc.sector_history(sector_code, since=since or None, to=to or None, sector_type=sector_type)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_macro_latest(region: str = "", code: str = "", limit: int = 30) -> str:
    """最新中美宏观数据。region=US/CN, code 如 US_CPI。保留 period/release_time/forecast/previous/actual/importance_score。"""
    try:
        import json
        data = xc.macro_latest(region=region or None, code=code or None, limit=limit)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_influencers(q: str = "", limit: int = 200) -> str:
    """影响力人物名单(完整配置名册, 含无证据人物; 不是从新闻推断)。"""
    try:
        import json
        data = xc.influencers(q=q or None, limit=limit)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_influencer_news(person: str, limit: int = 20) -> str:
    """指定人物的新闻证据。"""
    try:
        import json
        data = xc.influencer_news(person, limit=limit)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_movers_news() -> str:
    """异动股+新闻证据。"""
    try:
        import json
        return json.dumps(xc.movers_news(), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_briefings(report_type: str = "morning") -> str:
    """小石晨报/晚报。report_type=morning|evening。"""
    try:
        import json
        return json.dumps(xc.briefings_latest(report_type=report_type), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_briefings_industries(limit: int = 20) -> str:
    """行业简报列表。"""
    try:
        import json
        return json.dumps(xc.briefings_industries(limit=limit), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_briefings_industry(industry: str) -> str:
    """指定行业最新简报。industry 须为规范行业名。"""
    try:
        import json
        return json.dumps(xc.briefings_industry_latest(industry), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_download_session(dataset: str, param_year: str = "", param_adjust: str = "",
                             param_market: str = "", param_date: str = "", param_as_of: str = "",
                             param_code: str = "", param_since: str = "", param_to: str = "") -> str:
    """请求 R2 预签名下载会话(有效期7200s, 普通GET不带鉴权头)。
    契约: cn-daily 需 year+adjust; adjustment-factors 需 year+adjust; global-daily 需 market+year;
    sector-constituents 需 date; event-timeline 需 date; financial-current 需 year;
    financial-as-reported 需 date+as_of(带时区ISO8601); 重要: date 是 vintage_date 快照日(见 catalog 分区),
    as_of 必须 >= 该文件 min_available_at, 否则按 PIT 契约返回 404(不表示业务错误);
    cn-minute 需 code。返回 files(key/size/sha256/url) + verify + filter_policy。"""
    try:
        import json
        params = {"dataset": dataset}
        for k, v in [("year", param_year), ("adjust", param_adjust), ("market", param_market),
                     ("date", param_date), ("as_of", param_as_of), ("code", param_code),
                     ("since", param_since), ("to", param_to)]:
            if v:
                params[k] = v
        session = xc.history_download_session(**params)
        # 脱敏 url
        if "files" in session:
            for f in session["files"]:
                u = f.get("url", "")
                f["url"] = (u[:100] + "...") if len(u) > 100 else u
        return json.dumps(session, ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_capabilities() -> str:
    """统一市场能力/健康检查。查全球指数前先读 live_index_symbols。"""
    try:
        import json
        return json.dumps(xc.get_capabilities(), ensure_ascii=False, indent=2)
    except xc.XiaoshiError as e:
        return _err(e)


@mcp.tool()
def xiaoshi_auth_check() -> str:
    """密钥有效性检查(不展示密钥)。"""
    try:
        import json
        data = xc.check_api_key()
        keep = {k: data.get(k) for k in ["valid", "active", "user_id", "access", "principal_type"] if k in data}
        return json.dumps(keep, ensure_ascii=False)
    except xc.XiaoshiError as e:
        return _err(e)


if __name__ == "__main__":
    mcp.run()
