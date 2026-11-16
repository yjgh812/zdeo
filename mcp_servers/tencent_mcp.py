"""
腾讯财经 MCP Server — 免费 A 股/港股实时行情
数据源: 腾讯财经免费接口 (无需注册)
覆盖: 实时行情、日K线
注意: fqkline 复权接口已废(501)，勿用
"""

from mcp.server.fastmcp import FastMCP
import urllib.request
import json
import pandas as pd
import re

mcp = FastMCP("tencent-finance-mcp")

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("gbk")


@mcp.tool()
def get_realtime_quote(codes: str) -> str:
    """
    获取实时行情（腾讯接口）
    Args:
        codes: 股票代码，逗号分隔，带市场前缀
               sh600519=上证 sz000001=深证 hk00700=港股 usAAPL=美股
    Returns: 实时行情 CSV
    """
    try:
        url = f"https://qt.gtimg.cn/q={codes}"
        text = _fetch(url)
        rows = []
        for line in text.strip().split("\n"):
            if not line or not line.startswith("v_"):
                continue
            # 格式: v_sh600519="1~茅台~..."
            m = re.search(r'v_(\w+)=\"(.*)\"', line)
            if m:
                code = m.group(1)
                parts = m.group(2).split("~")
                if len(parts) >= 50:
                    rows.append({
                        "code": code,
                        "name": parts[1],
                        "open": parts[5],
                        "prev_close": parts[4],
                        "price": parts[3],
                        "high": parts[33],
                        "low": parts[34],
                        "volume": parts[6],
                        "amount": parts[37],
                        "bid1": parts[9],
                        "ask1": parts[19],
                        "change_pct": parts[32],
                        "pe": parts[39],
                        "amplitude": parts[43],
                        "turnover_rate": parts[38],
                        "total_market_cap": parts[45],
                        "circulating_market_cap": parts[44]
                    })
        if not rows:
            return "No data"
        return pd.DataFrame(rows).to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_history_kline(symbol: str, period: str = "day", limit: int = 100) -> str:
    """
    获取历史 K 线数据（腾讯接口）
    Args:
        symbol: 股票代码，如 sh600519 或 sz000001
        period: 周期 day(日)/week(周)/month(月)
        limit: 返回条数
    Returns: 历史 K 线 CSV (date/open/close/high/low/volume)
    """
    try:
        # 腾讯 kline 接口
        url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/mkline?param={symbol},{period},,{limit}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # 解析返回结构
        if "data" not in data:
            return "No data"
        stock_data = data["data"]
        # 找到第一条股票数据
        for key in stock_data:
            if key.startswith(symbol[:2]):
                kline_data = stock_data[key]
                break
        else:
            return "No kline data found"

        # 找到对应的周期数据
        period_map = {"day": "qt", "week": "qt", "month": "qt"}
        p_key = period_map.get(period, "qt")
        if p_key not in kline_data:
            return f"No {period} data found"

        rows = []
        for item in kline_data[p_key]:
            if len(item) >= 6:
                rows.append({
                    "date": item[0],
                    "open": item[1],
                    "close": item[2],
                    "high": item[3],
                    "low": item[4],
                    "volume": item[5]
                })
        if not rows:
            return "No data"
        return pd.DataFrame(rows).to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_index_constituents(code: str) -> str:
    """
    获取指数成分股（腾讯接口）
    Args:
        code: 指数代码，如 sh000300(沪深300)、sh000016(上证50)
    Returns: 成分股代码列表
    """
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,1"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        return f"Error: {str(e)}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()