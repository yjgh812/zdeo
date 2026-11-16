"""
财经快讯新闻 MCP 服务 (v3)
===========================
✅ 财联社电报已深度接入（签名算法已破解, 2026-08-15）

工具清单:
  财联社(官方直连, 签名md5(sha1(sort_params))):
    - cls_telegraph      电报列表(全部)
    - cls_important      电报列表(仅重点A/B级)
    - cls_search         电报翻页获取更多

  免费聚合源(无需签名):
    - jin10_flash_news   金十快讯
    - eastmoney_flash_news 东方财富7x24
    - sina_global_news   新浪全球快讯
    - wallstreetcn_news  华尔街见闻
    - stock_news         个股新闻(东财直连, 限流静默空时回退新浪)
    - news_digest        综合摘要

签名算法逆向记录:
  模块85073 → 默认参数 app=CailianpressWeb/os=web/sv=8.7.9
  模块36498 → 参数排序 key=value& 拼接
  模块37371 → SHA-1 (hex输出)
  模块81383 → MD5
  最终: sign = md5(sha1_hex(querystring))
"""

from mcp.server.fastmcp import FastMCP
import requests
import json
import re
import csv
import io
from datetime import datetime

mcp = FastMCP("finance-news")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.cls.cn/telegraph",
}


# ═══════════════════════════════════════════════
# 财联社官方直连 (签名已破解)
# ═══════════════════════════════════════════════

import hashlib


def _cls_sign(params: dict) -> str:
    """财联社签名: md5(sha1_hex(排序参数串))"""
    parts = []
    for k in sorted(params.keys()):
        v = params[k]
        if v is None:
            continue
        parts.append(f"{k}={v}")
    query = "&".join(parts)
    sha1_hex = hashlib.sha1(query.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1_hex.encode("utf-8")).hexdigest()


def _cls_request(path: str, extra: dict = None) -> list:
    """带签名的财联社API请求"""
    p = {"app": "CailianpressWeb", "os": "web", "sv": "8.7.9"}
    if extra:
        p.update({k: str(v) for k, v in extra.items()})
    p["sign"] = _cls_sign(p)
    resp = requests.get(f"https://www.cls.cn{path}", params=p, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errno") not in (0, None):
        raise RuntimeError(f"CLS API错误: {data.get('msg')}")
    return data.get("data", {}).get("roll_data", [])


def _fmt_items(items: list, with_content: bool = True) -> str:
    lines = []
    for it in items:
        t = datetime.fromtimestamp(it.get("ctime", 0)).strftime("%m-%d %H:%M")
        level = it.get("level", "")
        tag = {"A": "🔴加红", "B": "🟠重点"}.get(level, "")
        title = it.get("title") or ""
        content = re.sub(r"<[^>]+>", "", it.get("content", "") or it.get("brief", ""))
        head = f"[{t}]{(' ' + tag) if tag else ''} {title}".strip()
        if with_content and content:
            head += f"\n  {content[:160]}"
        lines.append(head)
    return "\n".join(lines) if lines else "暂无数据"


@mcp.tool()
def cls_telegraph(limit: int = 30) -> str:
    """获取财联社电报（官方直连，最新快讯）

    财联社电报是A股最快的市场快讯，含政策/公告/异动/宏观。
    等级说明: A=加红(重大) B=重点 C/D=普通

    Args:
        limit: 条数，默认30，最大100
    Returns:
        每条: [时间] [等级标记] 标题 + 内容摘要
    """
    try:
        items = _cls_request("/v1/roll/get_roll_list", {"rn": min(limit, 100)})
        return _fmt_items(items)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def cls_important(limit: int = 30) -> str:
    """获取财联社重点电报（仅A加红/B重点级）

    过滤后只保留财联社编辑部标记的重点快讯，
    适合盘前快速扫描市场重大变化。

    Args:
        limit: 拉取条数(过滤前)，默认30
    Returns:
        重点电报列表
    """
    try:
        items = _cls_request("/v1/roll/get_roll_list", {"rn": min(limit, 100)})
        important = [i for i in items if i.get("level") in ("A", "B")]
        return _fmt_items(important) if important else "最近无重点电报"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def cls_more(last_time: int, limit: int = 30) -> str:
    """财联社电报翻页（获取更早的历史快讯）

    配合 cls_telegraph 使用: 传入上批结果中最早一条的 ctime 值。

    Args:
        last_time: 分页游标(上批最早条目的ctime时间戳)
        limit: 条数，默认30
    Returns:
        更早的电报列表
    """
    try:
        items = _cls_request("/v1/roll/get_roll_list", {"rn": min(limit, 100), "last_time": last_time})
        return _fmt_items(items)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def cls_raw(rn: int = 20) -> str:
    """获取财联社电报原始JSON（含ctime/level/subjects等全部字段）

    供程序化处理使用: ctime可做翻页游标, level可自行过滤,
    subjects含主题标签(如'宏观'/'行业'/'公司')。

    Args:
        rn: 条数，默认20
    Returns:
        JSON数组
    """
    try:
        items = _cls_request("/v1/roll/get_roll_list", {"rn": min(rn, 100)})
        return json.dumps(items, ensure_ascii=False)
    except Exception as e:
        return f"Error: {e}"


# ═══════════════════════════════════════════════
# 免费聚合源
# ═══════════════════════════════════════════════


@mcp.tool()
def jin10_flash_news(limit: int = 20) -> str:
    """获取金十数据最新快讯（A股+全球，速度最快覆盖最全）

    金十快讯是市场最主流的财经快讯源，涵盖：
    政策动态/经济数据/公司公告/市场异动/全球宏观

    Args:
        limit: 返回条数，默认20，最大50
    Returns:
        每行一条: [时间] 内容
    """
    try:
        url = "https://www.jin10.com/flash_newest.js"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        match = re.search(r"\[.*\]", resp.text, re.DOTALL)
        if not match:
            return "解析失败"
        data = json.loads(match.group())
        lines = []
        for item in data[:limit]:
            time_str = item.get("time", "")
            content = ""
            d = item.get("data", {})
            if isinstance(d, dict):
                content = d.get("content", "")
            lines.append(f"[{time_str}] {content}")
        return "\n\n".join(lines) if lines else "暂无数据"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def eastmoney_flash_news(limit: int = 20) -> str:
    """获取东方财富7x24快讯（A股市场专用快讯）

    覆盖：A股盘中异动/板块资金流/个股公告/政策发布

    Args:
        limit: 返回条数，默认20
    Returns:
        每行一条: [时间] 标题
    """
    try:
        url = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList"
        params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": str(min(limit, 50)),
            "req_trace": str(datetime.now().timestamp()),
        }
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
        items = data.get("data", {}).get("fastNewsList", [])
        lines = []
        for item in items[:limit]:
            title = item.get("title", "") or item.get("summary", "")
            lines.append(f"[{item.get('showTime', '')}] {title}")
        return "\n".join(lines) if lines else "暂无数据"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def sina_global_news() -> str:
    """获取新浪财经全球实时快讯（akshare数据源）

    覆盖：国内经济政策/国际财经/产业动态

    Returns:
        每行一条: [时间] 内容
    """
    try:
        import akshare as ak
        df = ak.stock_info_global_sina()
        lines = []
        for _, row in df.iterrows():
            lines.append(f"[{row.get('时间', '')}] {row.get('内容', '')}")
        return "\n\n".join(lines) if lines else "暂无数据"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def wallstreetcn_news(limit: int = 20) -> str:
    """获取华尔街见闻实时快讯（全球宏观视角）

    覆盖：美联储/海外市场/大宗商品/全球宏观事件

    Args:
        limit: 返回条数，默认20
    Returns:
        每行一条: [时间] 标题
    """
    try:
        url = "https://api-one-wscn.awtmt.com/apiv1/content/lives"
        params = {"channel": "global-channel", "limit": str(min(limit, 50))}
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        lines = []
        for item in items[:limit]:
            title = item.get("title", "") or item.get("content_text", "")
            ts = item.get("display_time", 0)
            time_str = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
            lines.append(f"[{time_str}] {title}")
        return "\n".join(lines) if lines else "暂无数据"
    except Exception as e:
        return f"Error: {e}"


def _em_clean(s: str) -> str:
    """去除东财搜索高亮标签"""
    return re.sub(r"</?em>", "", s or "")


def _stock_news_eastmoney_rows(symbol: str) -> list:
    """东财搜索API直连取个股新闻, 返回(标题,内容,时间,来源,链接)元组列表。

    ⚠️ 该接口按IP限流, 且限流时静默返回空类目(code=0/msg=OK但
    cmsArticleWebOld缺失), 空列表≠确无新闻, 由调用方回退第二源。
    显式timeout必须有: 原akshare实现内部请求无超时, 上游迟滞会无限挂起。
    """
    inner = {
        "uid": "", "keyword": symbol, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default", "pageIndex": 1,
            "pageSize": 10, "preTag": "<em>", "postTag": "</em>",
        }},
    }
    params = {
        "cb": "jQuery_tulong_news",
        "param": json.dumps(inner, ensure_ascii=False),
        "_": str(int(datetime.now().timestamp() * 1000)),
    }
    resp = requests.get("https://search-api-web.eastmoney.com/search/jsonp",
                        params=params, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    m = re.search(r"\((.*)\)\s*;?\s*$", resp.text, re.DOTALL)
    if not m:
        raise RuntimeError("东财返回非JSONP: " + resp.text[:80])
    arts = (json.loads(m.group(1)).get("result") or {}).get("cmsArticleWebOld") or []
    rows = []
    for a in arts:
        link = "http://finance.eastmoney.com/a/" + (a.get("code") or "") + ".html"
        content = _em_clean(a.get("content")).replace("\u3000", "").replace("\r\n", " ")
        rows.append((_em_clean(a.get("title")), content,
                     a.get("date", ""), a.get("mediaName", ""), link))
    return rows


def _stock_news_sina_rows(symbol: str) -> list:
    """新浪个股资讯页回退源(东财限流/异常时)。页面无摘要, 内容列以'-'占位。"""
    prefix = "sh" if symbol.startswith(("6", "9", "5")) else "sz"
    resp = requests.get(
        f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCB_AllNewsStock/symbol/{prefix}{symbol}.phtml",
        headers=HEADERS, timeout=10)
    resp.raise_for_status()
    resp.encoding = "gbk"
    pat = re.compile(
        r"(\d{4}-\d{2}-\d{2}(?:&nbsp;)+\d{2}:\d{2})(?:&nbsp;)+"
        r"<a target='_blank' href='(https?://[^']+)'>([^<]+)</a>")
    rows = []
    for dt, link, title in pat.findall(resp.text):
        rows.append((title.strip(), "-",
                     dt.replace("&nbsp;", " ").strip(), "新浪财经", link))
    return rows


@mcp.tool()
def stock_news(symbol: str) -> str:
    """获取个股相关新闻（东财直连约10条, 限流或异常时自动回退新浪约40条）

    Args:
        symbol: 股票代码，如 600519、000001、300750
    Returns:
        CSV格式：关键词、新闻标题、新闻内容、发布时间、文章来源、新闻链接
    """
    try:
        rows = _stock_news_eastmoney_rows(symbol)
        if not rows:
            rows = _stock_news_sina_rows(symbol)
        if not rows:
            return f"股票{symbol}两源均无数据(东财可能限流返回空, 新浪页无条目)"
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["关键词", "新闻标题", "新闻内容", "发布时间", "文章来源", "新闻链接"])
        for title, content, date, src, link in rows:
            w.writerow([symbol, title, content, date, src, link])
        return buf.getvalue()
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def news_digest() -> str:
    """获取综合新闻摘要（财联社重点+金十 各取最新，盘前速览）

    Returns:
        合并快讯列表
    """
    parts = []

    # 财联社重点电报
    try:
        items = _cls_request("/v1/roll/get_roll_list", {"rn": 30})
        important = [i for i in items if i.get("level") in ("A", "B")][:10]
        if not important:
            important = items[:10]
        parts.append("━━━ 财联社电报 (重点) ━━━")
        parts.append(_fmt_items(important, with_content=False))
    except Exception as e:
        parts.append(f"财联社获取失败: {e}")

    # 金十
    try:
        url = "https://www.jin10.com/flash_newest.js"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        match = re.search(r"\[.*\]", resp.text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            parts.append("\n━━━ 金十快讯 (最新10条) ━━━")
            for item in data[:10]:
                time_str = item.get("time", "")
                d = item.get("data", {})
                content = d.get("content", "") if isinstance(d, dict) else ""
                parts.append(f"[{time_str}] {content[:100]}")
    except Exception as e:
        parts.append(f"金十快讯获取失败: {e}")

    return "\n".join(parts)


if __name__ == "__main__":
    mcp.run(transport="stdio")