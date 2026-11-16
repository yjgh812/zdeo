#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全市场ST名单批量更新
通过stock-sdk接口获取全市场股票名称，提取含ST/*ST的代码。
更新 st_blacklist.json
"""
import json
import os
import sys

sys.path.insert(0, r'D:\zcodesj\attribution')

ST_CACHE = r'D:\zcodesj\attribution\st_blacklist.json'

def update_st_from_sdk():
    """从stock-sdk获取全市场代码列表，批量查名称提取ST"""
    # 分批获取全市场行情（stock_sdk的get_all_a_share_quotes）
    # 但这个接口数据量大很慢，改用get_a_share_code_list获取代码+名称
    try:
        from mcp__stock_sdk__get_a_share_code_list import __wrapped__
    except Exception:
        pass

    # 直接用已缓存的候选数据 + 实时行情批量验证
    # 实际操作：从全市场扫描的2362只中，批量查名称找ST
    scan_path = r'D:\zcodesj\attribution\full_scan_20260811.json'
    if not os.path.exists(scan_path):
        print("需先运行 full_market_scan.py")
        return []

    with open(scan_path, 'r', encoding='utf-8') as f:
        scan = json.load(f)

    # 收集所有候选股票代码
    all_codes = set()
    for strategy, stocks in scan.get('strategies', {}).items():
        for s in stocks:
            all_codes.add(s['stock'])

    print(f"候选池股票: {len(all_codes)}只")
    print("需要通过实时行情接口批量验证名称...")

    # 这里需要调用MCP接口，但在脚本中无法直接调用
    # 改为输出待验证清单
    return sorted(list(all_codes))

def manual_st_check(codes_with_names):
    """
    手动验证：给定 {code: name} 字典，返回ST代码列表
    """
    st_codes = []
    for code, name in codes_with_names.items():
        if 'ST' in name or 'st' in name:
            st_codes.append(code)
    return st_codes

def update_st_cache(new_st_codes):
    """更新ST缓存"""
    # 读取现有
    existing = set()
    if os.path.exists(ST_CACHE):
        with open(ST_CACHE, 'r', encoding='utf-8') as f:
            existing = set(json.load(f).get('st_codes', []))

    # 合并
    all_st = sorted(list(existing | set(new_st_codes)))

    # 写入
    with open(ST_CACHE, 'w', encoding='utf-8') as f:
        json.dump({
            'st_codes': all_st,
            'count': len(all_st),
            'updated': '2026-08-10',
            'note': 'ST股黑名单，通过实时行情批量验证',
        }, f, ensure_ascii=False, indent=2)

    print(f"[OK] ST黑名单更新: {len(all_st)}只 (新增{len(set(new_st_codes) - existing)})")
    return all_st

if __name__ == '__main__':
    codes = update_st_from_sdk()
    print(f"\n待验证ST的候选: {len(codes)}只")
    print(f"请在主会话中通过MCP接口批量查名称后，将ST代码传入update_st_cache()")
