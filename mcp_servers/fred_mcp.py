"""
FRED MCP Server — 美国宏观经济数据
数据源: FRED (Federal Reserve Economic Data, 圣路易斯联储)
完全免费，需要 API Key: https://fred.stlouisfed.org/docs/api/api_key.html
"""

from mcp.server.fastmcp import FastMCP
from fredapi import Fred
import pandas as pd
import os

# 从环境变量读取 FRED API Key
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

mcp = FastMCP("fred-mcp")


def _get_fred():
    if not FRED_API_KEY:
        raise ValueError("FRED_API_KEY 未设置，请到 https://fred.stlouisfed.org/docs/api/api_key.html 免费申请")
    return Fred(api_key=FRED_API_KEY)


@mcp.tool()
def get_series(series_id: str, start: str = "", end: str = "") -> str:
    """
    获取经济数据时间序列
    Args:
        series_id: FRED 系列ID，如 GDP(美国GDP)、CPIAUCSL(CPI)、UNRATE(失业率)、FEDFUNDS(联邦基金利率)
        start: 开始日期 YYYY-MM-DD
        end: 结束日期 YYYY-MM-DD
    Returns: 时间序列 CSV (日期/值)
    """
    try:
        fred = _get_fred()
        data = fred.get_series(series_id, observation_start=start or None, observation_end=end or None)
        if data is None or data.empty:
            return "No data"
        df = data.to_frame(name="value")
        df.index = df.index.strftime("%Y-%m-%d")
        return df.to_csv(index=True, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def search_series(text: str, limit: int = 50) -> str:
    """
    搜索 FRED 数据系列
    Args:
        text: 搜索关键词，如 GDP、CPI、unemployment
        limit: 返回条数，默认 50
    Returns: 匹配的系列列表 CSV
    """
    try:
        fred = _get_fred()
        result = fred.search(text, limit=limit)
        if result is None or result.empty:
            return "No results"
        return result.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_series_info(series_id: str) -> str:
    """
    获取数据系列的元信息
    Args:
        series_id: FRED 系列 ID
    Returns: 系列信息 JSON
    """
    try:
        fred = _get_fred()
        info = fred.get_series_info(series_id)
        if info is None or info.empty:
            return "No info"
        return info.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_series_all_releases(series_id: str) -> str:
    """
    获取所有版本发布记录
    Args:
        series_id: FRED 系列 ID
    Returns: 发布记录 CSV
    """
    try:
        fred = _get_fred()
        data = fred.get_series_all_releases(series_id)
        if data is None or data.empty:
            return "No data"
        return data.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def get_series_vintage_dates(series_id: str) -> str:
    """
    获取数据系列的历史数据版本日期
    Args:
        series_id: FRED 系列 ID
    Returns: 版本日期 CSV
    """
    try:
        fred = _get_fred()
        dates = fred.get_series_vintage_dates(series_id)
        if dates is None or dates.empty:
            return "No data"
        df = dates.to_frame(name="vintage_date")
        return df.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)}"


# ===== 常用宏观经济指标快捷查询 =====

@mcp.tool()
def get_gdp(start: str = "", end: str = "") -> str:
    """
    获取美国 GDP 数据（快捷方式）
    Args:
        start: 开始日期 YYYY-MM-DD
        end: 结束日期 YYYY-MM-DD
    Returns: GDP 时间序列 CSV
    """
    return get_series("GDP", start, end)


@mcp.tool()
def get_cpi(start: str = "", end: str = "") -> str:
    """
    获取美国 CPI 数据（消费者物价指数，快捷方式）
    Args:
        start: 开始日期 YYYY-MM-DD
        end: 结束日期 YYYY-MM-DD
    Returns: CPI 时间序列 CSV
    """
    return get_series("CPIAUCSL", start, end)


@mcp.tool()
def get_unemployment_rate(start: str = "", end: str = "") -> str:
    """
    获取美国失业率（快捷方式）
    Args:
        start: 开始日期 YYYY-MM-DD
        end: 结束日期 YYYY-MM-DD
    Returns: 失业率时间序列 CSV
    """
    return get_series("UNRATE", start, end)


@mcp.tool()
def get_fed_rate(start: str = "", end: str = "") -> str:
    """
    获取美国联邦基金利率（快捷方式）
    Args:
        start: 开始日期 YYYY-MM-DD
        end: 结束日期 YYYY-MM-DD
    Returns: 利率时间序列 CSV
    """
    return get_series("FEDFUNDS", start, end)


@mcp.tool()
def get_pmi(start: str = "", end: str = "") -> str:
    """
    获取美国 ISM 制造业 PMI（快捷方式）
    Args:
        start: 开始日期 YYYY-MM-DD
        end: 结束日期 YYYY-MM-DD
    Returns: PMI 时间序列 CSV
    """
    return get_series("NAPM", start, end)


@mcp.tool()
def get_nonfarm_payroll(start: str = "", end: str = "") -> str:
    """
    获取美国非农就业人数（快捷方式）
    Args:
        start: 开始日期 YYYY-MM-DD
        end: 结束日期 YYYY-MM-DD
    Returns: 非农就业时间序列 CSV
    """
    return get_series("PAYEMS", start, end)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()