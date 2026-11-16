#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票名称+基本面缓存模块
从实时行情接口批量获取名称/PE/PB/ST状态，缓存到本地JSON。
所有选股脚本import此模块即可获取已验证的名称和基本面数据。

铁律执行：
  1. 代码必标名称（不猜测）
  2. PE<0的亏损股排除
  3. ST股排除
"""
import json
import os

CACHE_PATH = r'D:\zcodesj\attribution\stock_info_cache.json'

def load_cache():
    """加载缓存"""
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'stocks': {}, 'updated': ''}

def save_cache(cache):
    """保存缓存"""
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def get_stock_info(code):
    """
    获取单只股票的已验证信息
    返回: {'name': str, 'pe': float, 'pb': float, 'is_st': bool, 'is_loss': bool}
    如果缓存中没有返回None
    """
    cache = load_cache()
    return cache.get('stocks', {}).get(code)

def is_valid_buy(code):
    """
    判断股票是否可以买入（综合过滤）
    排除条件：仅ST/科创/北交所
    PE为负不排除（亏损股可能是周期底/困境反转/扭亏预期）
    PE只作为参考信息标注，不作为硬性过滤
    """
    import sys
    sys.path.insert(0, r'D:\zcodesj\attribution')
    from st_filter import is_excluded_enhanced

    # 唯一硬性排除：ST/科创/北交所
    if is_excluded_enhanced(code):
        return False, 'ST/科创/北交所'

    info = get_stock_info(code)
    if info is None:
        return None, '未验证（缓存中无数据，需通过实时行情查证名称）'

    return True, 'PASS'

def batch_update_from_quotes(quotes_data):
    """
    从stock-sdk的get_a_share_quotes返回数据批量更新缓存
    quotes_data: list of quote dicts
    """
    cache = load_cache()
    for q in quotes_data:
        code_prefix = 'sh' if q.get('marketId') == '1' else 'sz'
        code = code_prefix + q['code']
        name = q.get('name', '')
        pe = q.get('pe')
        pb = q.get('pb')

        is_st = 'ST' in name or 'st' in name
        is_loss = pe is not None and pe < 0

        cache['stocks'][code] = {
            'name': name,
            'pe': round(pe, 2) if pe and pe > -10000 else None,
            'pb': round(pb, 2) if pb else None,
            'is_st': is_st,
            'is_loss': is_loss,
            'price': q.get('price'),
        }

        # 如果发现新ST，自动加入ST黑名单
        if is_st:
            try:
                from st_filter import add_st
                add_st(code, name)
            except Exception:
                pass

    from datetime import datetime
    cache['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    save_cache(cache)
    return len(quotes_data)

def print_validated_list(codes):
    """打印已验证的股票清单"""
    cache = load_cache()
    print(f"{'代码':10s} | {'名称':10s} | {'PE':>8s} | {'PB':>6s} | {'状态':6s}")
    print("-" * 55)
    for code in codes:
        info = cache.get('stocks', {}).get(code)
        if info:
            status = '❌ST' if info['is_st'] else ('⚠️亏损' if info['is_loss'] else '✅')
            pe_str = f"{info['pe']:.1f}" if info['pe'] else '?'
            pb_str = f"{info['pb']:.1f}" if info['pb'] else '?'
            print(f"{code:10s} | {info['name']:10s} | {pe_str:>8s} | {pb_str:>6s} | {status}")
        else:
            print(f"{code:10s} | {'未验证':10s} | {'?':>8s} | {'?':>6s} | ❓")

if __name__ == '__main__':
    cache = load_cache()
    print(f"缓存: {len(cache.get('stocks',{}))}只, 更新于{cache.get('updated','未更新')}")
