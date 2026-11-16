#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ST股排除名单构建
从hithink API获取全市场名称，提取含ST的代码，保存为JSON缓存。
所有选股脚本import此模块即可排除ST。
"""
import json
import os

# 从已知的ST股（通过实时行情验证确认）+ 常见ST代码段
# 这是硬编码的已知ST名单（通过本次验证发现）
KNOWN_ST = {
    # 本次验证发现的ST股
    'sz002759',  # ST天际
    'sz300068',  # ST南都
    'sz002667',  # *ST威领
    'sz300430',  # *ST益通
    'sz300352',  # ST信源
    'sz002193',  # ST如意（8/10涨停时发现）
}

# ST缓存文件路径
ST_CACHE_PATH = r'D:\zcodesj\attribution\st_blacklist.json'

def build_st_blacklist():
    """构建ST黑名单缓存"""
    # 从hithink的tickers list中提取（手动维护+API更新）
    # 目前用已知ST + 可扩展
    blacklist = set(KNOWN_ST)

    # 尝试从缓存加载更多
    if os.path.exists(ST_CACHE_PATH):
        with open(ST_CACHE_PATH, 'r', encoding='utf-8') as f:
            cached = json.load(f)
            blacklist.update(cached.get('st_codes', []))

    # 保存
    with open(ST_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump({
            'st_codes': sorted(list(blacklist)),
            'count': len(blacklist),
            'updated': '2026-08-10',
            'note': '已知ST股名单，需定期通过实时行情更新',
        }, f, ensure_ascii=False, indent=2)

    return sorted(list(blacklist))

def is_st(stock_code):
    """
    检查股票是否为ST股
    stock_code格式: sh600664 或 sz300068
    """
    if not os.path.exists(ST_CACHE_PATH):
        build_st_blacklist()

    with open(ST_CACHE_PATH, 'r', encoding='utf-8') as f:
        cache = json.load(f)

    return stock_code in cache.get('st_codes', [])

def is_excluded_enhanced(stock_code):
    """
    增强版排除函数——排除科创/北交所/ST
    所有选股脚本应使用此函数替代原is_excluded
    """
    code = stock_code[2:] if len(stock_code) > 6 else stock_code

    # 科创板
    if code.startswith('688'):
        return True
    # 北交所
    if code.startswith('8') or code.startswith('4'):
        return True
    # ST股
    if is_st(stock_code):
        return True

    return False

def add_st(stock_code, name=''):
    """动态添加ST股到黑名单"""
    if not os.path.exists(ST_CACHE_PATH):
        build_st_blacklist()

    with open(ST_CACHE_PATH, 'r', encoding='utf-8') as f:
        cache = json.load(f)

    if stock_code not in cache['st_codes']:
        cache['st_codes'].append(stock_code)
        cache['st_codes'].sort()
        cache['count'] = len(cache['st_codes'])
        if name:
            cache.setdefault('details', {})[stock_code] = name

        with open(ST_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f"[OK] 添加ST: {stock_code} {name}")

if __name__ == '__main__':
    bl = build_st_blacklist()
    print(f"ST黑名单: {len(bl)}只")
    for code in bl:
        print(f"  {code}")

    # 测试
    print(f"\nis_excluded_enhanced('sz300068'): {is_excluded_enhanced('sz300068')}")
    print(f"is_excluded_enhanced('sh600664'): {is_excluded_enhanced('sh600664')}")
    print(f"is_excluded_enhanced('sh688001'): {is_excluded_enhanced('sh688001')}")
