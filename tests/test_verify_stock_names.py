# -*- coding: utf-8 -*-
"""verify_stock_names 三型误报回归测试 (2026-09-10).
用例全部来自 2026-09-09 实战误报 (恒源煤电/低位反转扫描报告三轮校验):
  ① markdown 加粗: **好太太(603848)**
  ② 库名内部空格: 柳工 vs 库中「柳    工」
  ③ 括号前缀吃字: 权重拍自恒源煤电(600971)
负例确保修复不放松编造拦截。
运行: python tests/test_verify_stock_names.py  (exit 0=全过)
"""
import sys, io, contextlib
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'D:\zcodesj')
from verify_stock_names import verify_text

CASES_PASS = [
    ('加粗剥离', '**好太太(603848)** 是相似分第一'),
    ('代码在前+尾粘连', '600971 恒源煤电单案例复现'),
    ('库名内部空格', '000528 柳工今日放量'),
    ('括号前缀吃字', '权重拍自恒源煤电(600971)'),
    ('常规路径不回归', '603848 好太太'),
    ('ST星号写法', '*ST广糖(000911) 未出现在池内' ),
]
CASES_FAIL = [
    ('编造名必须拦', '600971 恒源电力'),
    ('前缀粘词编造必须拦', '600971 煤电集团'),
]

fails = 0
for tag, t in CASES_PASS:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = verify_text(t, source=f'回归:{tag}')
    ok = (rc == 0)
    print(('PASS' if ok else 'FAIL'), f'[{tag}]', t)
    if not ok:
        print('  --- 输出 ---'); print(buf.getvalue())
        fails += 1
for tag, t in CASES_FAIL:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = verify_text(t, source=f'回归:{tag}')
    ok = (rc == 1) and '❌' in buf.getvalue()
    print(('PASS' if ok else 'FAIL'), f'[{tag}]', t)
    if not ok:
        print('  --- 输出 ---'); print(buf.getvalue())
        fails += 1

print(f'\n{"✅ 全部通过" if fails == 0 else f"⛔ {fails} 个用例失败"} ({len(CASES_PASS)+len(CASES_FAIL)} 用例)')
sys.exit(1 if fails else 0)
