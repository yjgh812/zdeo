#!/usr/bin/env python3
"""测试金十数据 MCP 服务器"""
import asyncio, json, sys
sys.path.insert(0, 'D:/zcodesj/mcp_servers')
from jin10_mcp import get_flash_news, get_economic_calendar, get_trading_clock

async def test():
    print('=== 测试1：金十快讯 ===')
    result = await get_flash_news(limit=3)
    data = json.loads(result)
    if isinstance(data, list):
        print(f'成功获取 {len(data)} 条快讯')
        for item in data[:3]:
            print(f'  [{item.get("time","")}] {item.get("content","")[:80]}...')
    else:
        print(f'返回: {json.dumps(data, ensure_ascii=False)[:200]}')

    print()
    print('=== 测试2：财经日历 ===')
    result2 = await get_economic_calendar()
    data2 = json.loads(result2)
    if isinstance(data2, list):
        print(f'成功获取 {len(data2)} 条日历事件')
        for item in data2[:3]:
            print(f'  {json.dumps(item, ensure_ascii=False)[:150]}')
    else:
        print(f'返回: {json.dumps(data2, ensure_ascii=False)[:200]}')

    print()
    print('=== 测试3：交易时钟 ===')
    result3 = await get_trading_clock()
    data3 = json.loads(result3)
    if isinstance(data3, dict) and 'error' not in data3:
        keys = list(data3.keys())[:5]
        print(f'成功获取，交易所数: {len(data3)}')
        print(f'交易所示例: {keys}')
    else:
        print(f'返回: {json.dumps(data3, ensure_ascii=False)[:200]}')

asyncio.run(test())