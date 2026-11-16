#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_stock_names.py — 分析报告输出前强制校验（防编造守门员）
================================================================
扫描文本/报告中的所有 (股票代码, 名称) 组合，逐一与 stock_info 比对。
任何不一致 → 退出码1，列出错误。

用法：
  python verify_stock_names.py <报告文件.md>
  python verify_stock_names.py "300319 明阳电路是好标的"   # 直接校验字符串

设计（2026-08-15实战错误闭环）：
  编造在发生时无自知之明——记忆检索与数据查询的主观感受相同。
  因此防线必须是机械校验，不依赖生成时自觉。
  本工具是"程序型铁律"：报告里有代码+名称 → 必过此关。
"""
import re
import sys
from pathlib import Path

from stock_name_lookup import verify_pair

# 匹配模式：
# ① "300319 明阳电路" / 表格 "| 002724 | 海洋王 |"  → 代码后（可隔管道符/空格）跟名称
# ② "海洋王(002724)" / "海洋王（002724）"           → 名称在前带括号
# 名称字符集含全角字母（\uff21-\uff3a Ａ-Ｚ / \uff41-\uff5a ａ-ｚ）：如 "京东方Ａ"，2026-08-27 补
PAT_CODE_FIRST = re.compile(
    r'(?<!\d)(\d{6})\s*[|\s]*\s([\u4e00-\u9fa5][\u4e00-\u9fa5A-Za-z*\uff21-\uff3a\uff41-\uff5a]{1,11})')
PAT_NAME_FIRST = re.compile(
    r'([\u4e00-\u9fa5A-Za-z*\uff21-\uff3a\uff41-\uff5a][\u4e00-\u9fa5A-Za-z*\uff21-\uff3a\uff41-\uff5a]{1,11})[（(](\d{6})[）)]')

# 干扰词过滤：代码后跟的中文不是股票名的常见情况
NOISE_WORDS = {
    '元', '只', '板', '连板', '首板', '涨停', '跌停', '今日', '昨日', '当日',
    '开盘', '收盘', '买入', '卖出', '关注', '观察', '食品', '医药', '主板',
    '创业', '科创', '板块', '行业', '概念', '指数', '提示', '警示', '风险',
    '月', '日', '年', '时', '分', '星期', '周一', '周二', '周三', '周四', '周五',
}

# 名称尾部常粘连的连接词/标点（贪婪匹配会多吃，验证时按前缀比对兜底）
INDEX_HINTS = ('指数', '指数今日', '综指')


def _norm(x: str) -> str:
    """比对归一化（2026-09-10 三型误报闭环）:
    ① 剥 '*' —— markdown 加粗（**好太太）与 ST 前缀写法差异；
    ② 剥空白 —— 库名含内部全角空格（柳    工）vs 正文无空格（柳工）。"""
    return re.sub(r'[\*\s]+', '', x)


def extract_pairs(text: str) -> list[tuple[str, str]]:
    """提取文本中所有 (code, name) 组合（含表格管道格式）。"""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for m in PAT_CODE_FIRST.finditer(text):
        code, name = m.group(1), m.group(2).strip('|').strip()
        if name in NOISE_WORDS or len(name) < 2:
            continue
        # 指数上下文跳过（如"000001 上证指数"是指数不是平安银行）
        if any(name.startswith(h) or h in name[:6] for h in INDEX_HINTS):
            continue
        key = (code, name)
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    for m in PAT_NAME_FIRST.finditer(text):
        name, code = m.group(1), m.group(2)
        key = (code, name)
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    return pairs


def verify_text(text: str, source: str = '输入文本') -> int:
    pairs = extract_pairs(text)
    if not pairs:
        print(f'[verify_stock_names] {source}: 未发现代码+名称组合，无需校验')
        return 0

    from stock_name_lookup import get_name
    errors = 0
    print(f'[verify_stock_names] {source}: 发现 {len(pairs)} 组代码+名称，逐一校验...')
    for code, name in pairs:
        actual = get_name(code)
        if actual is None:
            # 库中无此代码：可能是指数/基金/已退市——降级为提示不算错
            print(f'  ⚠️ {code} 库中无此代码（指数/非A股？文中称「{name}」）')
            continue
        # 归一化后比对: 精确 / 捕获名以库名开头(尾部粘词) / 以库名结尾(头部粘词,
        # 如 "权重拍自恒源煤电(600971)")。剥 * 与空白后比对仍要求真实名完整出现,
        # 编造名(如"恒源电力")两头都不命中 → 依旧拦截。
        na, aa = _norm(name), _norm(actual)
        if na == aa or (aa and (na.startswith(aa) or na.endswith(aa))):
            print(f'  ✅ {code} {actual}')
        else:
            errors += 1
            print(f'  ❌ {code} 写的「{name}」→ 库中实际「{actual}」')

    print()
    if errors:
        print(f'⛔ {errors} 处名称不一致 —— 禁止发布，必须改为库中名称后重校验')
        return 1
    print(f'✅ 名称校验通过，可以发布')
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print('用法: python verify_stock_names.py <文件或字符串>')
        return 2

    arg = sys.argv[1]
    p = Path(arg)
    if p.exists():
        text = p.read_text(encoding='utf-8', errors='ignore')
        return verify_text(text, source=p.name)
    # 当作字符串直接校验
    return verify_text(arg, source='命令行字符串')


if __name__ == '__main__':
    raise SystemExit(main())