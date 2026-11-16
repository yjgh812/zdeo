#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_report_numbers.py — 报告数字型错误自动校验（数字守门员）
================================================================================
背景（2026-08-15第三轮自省）：verify_stock_names只防名称编造；数字写错
（如涨停71写成实际60、元器件12写成实际9）无工具拦截——已实际发生两例。

校验项：
  ① "涨停N只/N只涨停"  → 按板块分阈值重算（剔ST/新股）
  ② "跌停N只"          → 同上
  ③ "prem_seal=±X.XX%" → prem_seal_daily.compute 重算
  ④ "CODE 名称: XX.XX元 +X.X%" → stock_daily_hsj 逐只核对价格与涨幅

用法：
  python verify_report_numbers.py <报告.md> [--date 20260814]
退出码：0=全部一致 / 1=有数字不符（禁止发布）
"""
import re
import sqlite3
import sys
from pathlib import Path

MAIN = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
sys.path.insert(0, str(Path(__file__).parent))
from prem_seal_daily import compute as compute_prem_seal, limit_threshold


def ground_truth(target: str) -> dict:
    """从数据库重算基准日真值（含前一日，供"昨日涨停N只"校验）。"""
    with sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True) as c:
        prev = c.execute('SELECT MAX(date) FROM stock_daily_hsj WHERE date<?', (target,)).fetchone()[0]
        pp = c.execute('SELECT MAX(date) FROM stock_daily_hsj WHERE date<?', (prev,)).fetchone()[0]
        rows = c.execute('''
            SELECT t.code, i.name, i.is_st, i.list_date, t.close, y.close,
            (t.close-y.close)/y.close*100, (y.close-z.close)/z.close*100
            FROM stock_daily_hsj t
            JOIN stock_daily_hsj y ON t.code=y.code AND y.date=?
            JOIN stock_daily_hsj z ON y.code=z.code AND z.date=?
            LEFT JOIN stock_info i ON t.code=i.code
            WHERE t.date=?''', (prev, pp, target)).fetchall()
    lu, ld, lu_prev, per_stock = 0, 0, 0, {}
    for code, name, is_st, ld_date, close, pclose, pct, ypct in rows:
        if name is None or str(is_st) == '1':
            continue
        per_stock[code] = (name, close, pct)
        th = limit_threshold(code)
        if pct is not None and pct >= th:
            lu += 1
        if pct is not None and pct <= -th:
            ld += 1
        # 前一日涨停数（同口径）
        if ypct is not None and ypct >= th:
            lu_prev += 1
    ps = compute_prem_seal(target)
    return {'lu': lu, 'ld': ld, 'lu_prev': lu_prev,
            'prem_seal': ps['prem_seal'] if ps else None, 'stocks': per_stock}


def verify(text: str, target: str) -> int:
    gt = ground_truth(target)
    errors = []

    # ① 涨停数（区分今日/昨日上下文）
    for m in re.finditer(r'(昨日|前一天|前日)?(?:的)?涨停\s*(\d+)\s*只', text):
        is_prev = bool(m.group(1))
        claimed = int(m.group(2))
        actual = gt['lu_prev'] if is_prev else gt['lu']
        tag = '昨日涨停' if is_prev else '涨停'
        if claimed != actual:
            errors.append(f'{tag}数: 报告写{claimed}只 → 实际{actual}只（剔ST/新股/板块分阈值口径）')
    for m in re.finditer(r'(\d+)\s*只涨停', text):
        claimed = int(m.group(1))
        if claimed != gt['lu']:
            errors.append(f'涨停数: 报告写{claimed}只 → 实际{gt["lu"]}只')

    # ② 跌停数（同上分开匹配）
    for m in re.finditer(r'跌停\s*(\d+)\s*只', text):
        claimed = int(m.group(1))
        if claimed != gt['ld']:
            errors.append(f'跌停数: 报告写{claimed}只 → 实际{gt["ld"]}只')
    for m in re.finditer(r'(\d+)\s*只跌停', text):
        claimed = int(m.group(1))
        if claimed != gt['ld']:
            errors.append(f'跌停数: 报告写{claimed}只 → 实际{gt["ld"]}只')

    # ③ prem_seal
    for m in re.finditer(r'prem_seal[^=]{0,10}=?\s*([+-]?\d+\.?\d*)%', text):
        claimed = float(m.group(1))
        if gt['prem_seal'] is not None and abs(claimed - gt['prem_seal']) > 0.05:
            errors.append(f'prem_seal: 报告写{claimed:+.2f}% → 实际{gt["prem_seal"]:+.2f}%')

    # ④ 个股价格与涨幅（"CODE 名称: XX.XX元" 后近处 "+X.X%"）
    for m in re.finditer(r'(\d{6})\s+[\u4e00-\u9fa5A-Za-z*]+[:：]?\s*(\d+\.?\d*)元[^\n]{0,30}?([+-]\d+\.?\d*)%', text):
        code, price_c, pct_c = m.group(1), float(m.group(2)), float(m.group(3))
        if code in gt['stocks']:
            name, price_a, pct_a = gt['stocks'][code]
            if abs(price_c - price_a) > 0.01:
                errors.append(f'{code} {name} 价格: 报告写{price_c}元 → 实际{price_a:.2f}元')
            if abs(pct_c - pct_a) > 0.15:
                errors.append(f'{code} {name} 涨幅: 报告写{pct_c:+.1f}% → 实际{pct_a:+.1f}%')

    # 汇总
    checks = len(gt['stocks'])
    if errors:
        print(f'⛔ {len(errors)} 处数字不符（基准日{target}: 涨停{gt["lu"]}/跌停{gt["ld"]}/prem_seal{gt["prem_seal"] or "无"}）:')
        for e in errors:
            print(f'  ❌ {e}')
        return 1
    print(f'✅ 数字校验通过（基准真值: 涨停{gt["lu"]}只 跌停{gt["ld"]}只 prem_seal={gt["prem_seal"] if gt["prem_seal"] is not None else "无"}；个股{checks}只可核）')
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    fp = Path(args[0])
    target = args[args.index('--date') + 1] if '--date' in args else None
    if not target:
        with sqlite3.connect(f'file:{MAIN}?mode=ro', uri=True) as c:
            target = str(c.execute('SELECT MAX(date) FROM stock_daily_hsj').fetchone()[0])
    text = fp.read_text(encoding='utf-8', errors='ignore') if fp.exists() else args[0]
    return verify(text, target)


if __name__ == '__main__':
    raise SystemExit(main())