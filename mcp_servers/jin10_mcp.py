#!/usr/bin/env python3
"""
金十数据 MCP 服务器
=====================
数据来源：jin10.com + akshare（百度股市通财经日历）

工具清单：
1. get_flash_news          - 获取金十快讯（实时财经快讯，API直连，无需注册）
2. get_economic_calendar   - 获取财经日历（百度股市通数据，无需注册）
3. get_trading_clock       - 获取全球交易时间（jin10交易时钟）

用法：python jin10_mcp.py
注册到 ZCode config.json 后即可调用。
"""

import json
import sys
import httpx
from datetime import datetime, date
from typing import Optional
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("jin10-data", log_level="WARNING")

# ======== 通用 HTTP 客户端 ========

async def _get_json(url: str, params: dict = None, headers: dict = None) -> dict:
    """通用 GET 请求，返回 JSON"""
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.jin10.com/",
    }
    if headers:
        default_headers.update(headers)
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params, headers=default_headers)
        resp.raise_for_status()
        return resp.json()


# ======== 工具 1：金十快讯 ========

@mcp.tool()
async def get_flash_news(max_time: Optional[str] = None, limit: int = 20) -> str:
    """获取金十数据实时财经快讯（Flash News）

    Args:
        max_time: 可选，上次快讯的时间戳（如 1723718400），用于分页拉取更早内容
        limit: 返回条数，默认 20，最大 50

    Returns:
        JSON 字符串，每条包含 time（时间）、content（内容）、importance（1-3星）
    """
    url = "https://flash-api.jin10.com/get_flash_list"
    params = {
        "type": "0",
        "channel": "-8200",
        "limit": min(limit, 50),
    }
    if max_time:
        params["max_time"] = max_time

    headers = {
        "x-app-id": "bVBF4FyRTn5NJF5n",
        "x-version": "1.0.0",
    }

    try:
        data = await _get_json(url, params=params, headers=headers)
        flash_list = data.get("data", [])
        if not flash_list:
            return json.dumps([], ensure_ascii=False)

        result = []
        for item in flash_list:
            content_raw = item.get("data", "")
            if isinstance(content_raw, dict):
                content = content_raw.get("content", "")
            else:
                content = content_raw
            # 去除 HTML 标签
            import re
            content = re.sub(r'<[^>]+>', '', content).strip()
            result.append({
                "id": item.get("id", ""),
                "time": item.get("time", ""),
                "content": content,
                "importance": item.get("important", 1),
            })
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": f"获取快讯失败: {e}"}, ensure_ascii=False)


# ======== 工具 2：财经日历 ========

@mcp.tool()
async def get_economic_calendar(target_date: Optional[str] = None) -> str:
    """获取财经日历（经济数据公布日程）

    数据来源：百度股市通（https://finance.baidu.com/calendar），与金十数据同源，
    无需注册/API Key。

    Args:
        target_date: 日期，格式 YYYY-MM-DD 或 YYYYMMDD，默认今天

    Returns:
        JSON 字符串，每条包含 time（时间）、event（事件）、country（国家）、
        previous（前值）、consensus（预期）、actual（公布值）、importance（重要性）
    """
    if target_date is None:
        target_date = date.today().strftime("%Y%m%d")
    else:
        # 统一转 YYYYMMDD
        target_date = target_date.replace("-", "")

    try:
        import akshare as ak
        df = ak.news_economic_baidu(date=target_date)
        if df is None or df.empty:
            return json.dumps([], ensure_ascii=False)

        # 百度返回的列可能变化，做通用化处理
        result = []
        for _, row in df.iterrows():
            row_dict = {}
            for col in df.columns:
                val = row[col]
                if isinstance(val, str):
                    row_dict[col] = val
                elif val is not None:
                    row_dict[col] = str(val)
                else:
                    row_dict[col] = ""
            result.append(row_dict)

        return json.dumps(result, ensure_ascii=False, indent=2)

    except ImportError:
        return json.dumps({"error": "需要安装 akshare: pip install akshare"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"获取财经日历失败: {e}"}, ensure_ascii=False)


# ======== 工具 3：全球交易时间 ========

@mcp.tool()
async def get_trading_clock() -> str:
    """获取全球市场交易时间（冬令时/夏令时状态）

    数据来源：jin10 CDN（无需注册）

    Returns:
        JSON 字符串，包含各交易所的当前时令状态
    """
    url = "https://cdn.jin10.com/trading-clock/new/data.json"
    try:
        data = await _get_json(url)
        if isinstance(data, dict) and data.get("status") == 200:
            return json.dumps(data.get("data", {}), ensure_ascii=False, indent=2)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": f"获取交易时间失败: {e}"}, ensure_ascii=False)


# ======== 主入口 ========

if __name__ == "__main__":
    mcp.run()