"""
新浪财经 MCP Server — 免费 A 股实时/历史数据
数据源: 新浪财经免费接口 (无需注册)
覆盖: 实时行情、历史K线、复权因子、板块
注意: 本机东财 API 被拦截，新浪接口是独立的，不受影响
"""

from mcp.server.fastmcp import FastMCP
import urllib.request
import json
import pandas as pd
import re

mcp = FastMCP("sina-finance-mcp")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn"
}


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("gbk")


def _fetch_utf8(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


@mcp.tool()
def get_realtime_quote(codes: str) -> str:
    """
    获取 A 股实时行情（新浪接口）
    Args:
        codes: 股票代码，逗号分隔，带市场前缀，如 sh600519,sz000001
    Returns: 实时行情 CSV
    """
    try:
        url = f"https://hq.sinajs.cn/list={codes}"
        text = _fetch(url)
        rows = []
        for line in text.strip().split("\n"):
            if not line:
                continue
            # 解析格式: var hq_str_sh600519="茅台...
            m = re.search(r'var hq_str_(\w+)=\"(.*)\"', line)
            if m:
                code = m.group(1)
                parts = m.group(2).split(",")
                if len(parts) >= 32:
                    rows.append({
                        "code": code,
                        "name": parts[0],
                        "open": parts[1],
                        "prev_close": parts[2],
                        "price": parts[3],
                        "high": parts[4],
                        "low": parts[5],
                        "volume": parts[8],
                        "amount": parts[9],
                        "bid1": parts[10],
                        "ask1": parts[12],
                        "date": parts[30],
                        "time": parts[31]
                    })
        if not rows:
            return "No data"
        return pd.DataFrame(rows).to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_history_kline(symbol: str, scale: str = "day", datalen: int = 100) -> str:
    """
    获取历史 K 线数据（新浪）
    Args:
        symbol: 股票代码，如 sh600519 或 sz000001
        scale: 周期 day(日)/week(周)/month(月)/5min/15min/30min/60min
        datalen: 返回条数，默认 100（日线最多1000）
    Returns: 历史 K 线 JSON
    """
    try:
        url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"
        text = _fetch_utf8(url)
        data = json.loads(text)
        if not data:
            return "No data"
        return pd.DataFrame(data).to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_qfq_factor(symbol: str) -> str:
    """
    获取前复权因子（新浪 qfq）
    Args:
        symbol: 股票代码，如 sh600519 或 sz000001
    Returns: 复权因子 CS: date/因子值
    """
    try:
        # 从代码中提取市场前缀和纯代码
        market = symbol[:2]
        code = symbol[2:]
        url = f"https://finance.sina.com.cn/realstock/company/{market}{code}/qfq.js"
        text = _fetch_utf8(url)
        # 解析 JS 对象格式
        m = re.search(r'\[(.*?)\]', text, re.DOTALL)
        if not m:
            return "No qfq data"
        # 提取键名和数组
        key_match = re.search(r'var\s+\w+\s*=\s*\{"([^"]+)":', text)
        content = m.group(1)
        items = re.findall(r'\["([^"]+)","([^"]+)"\]', content)
        if not items:
            return "No qfq data parsed"
        rows = [{"date": i[0], "factor": i[1]} for i in items]
        return pd.DataFrame(rows).to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_realtime_index(codes: str) -> str:
    """
    获取指数实时行情（新浪）
    Args:
        codes: 指数代码，如 sh000001(上证), sz399001(深证)
    Returns: 指数行情 CSV
    """
    return get_realtime_quote(codes)


@mcp.tool()
def get_fund_flow(symbol: str) -> str:
    """
    获取个股资金流向（新浪）
    Args:
        symbol: 股票代码，如 sh600519
    Returns: 资金流向 CSV
    """
    try:
        url = f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=5&ma=no&datalen=100"
        text = _fetch_utf8(url)
        data = json.loads(text)
        if not data:
            return "No data"
        return pd.DataFrame(data).to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()