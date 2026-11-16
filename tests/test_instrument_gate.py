# -*- coding: utf-8 -*-
"""品种闸(IDENT-GATE)回归: check_instrument_type 语义三分 + NULL 分支。
20261108 99指数码撞码污染事件防御, 三段式立项(分析agent"修改后采纳")。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import trade_executor as te
import stock_name_lookup as snl


def main():
    cases = [
        ('stock', None),   # 正常股票 -> 放行
        ('index', '拒'),   # 指数 -> 拒(fail-closed 正向证据)
        ('fund', '拒'),    # 基金 -> 拒
        ('bond', '拒'),    # 债券 -> 拒
        (None, None),      # 无记录 -> 放(fail-open, 退市股/新股窗口)
    ]
    ok = True
    for itype, expect in cases:
        snl.get_instrument_type = (lambda t: (lambda code: t))(itype)
        got = te.check_instrument_type('000001')
        is_reject = isinstance(got, str)
        passed = (is_reject and expect == '拒') or (got is None and expect is None)
        tag = 'PASS' if passed else 'FAIL'
        act = '拒' if is_reject else '放'
        print(f"{tag}: itype={itype!r} -> {act}")
        if not passed:
            ok = False

    def _boom(code):
        raise RuntimeError('db down')

    snl.get_instrument_type = _boom
    got = te.check_instrument_type('000001')
    passed = got is None
    tag = 'PASS' if passed else 'FAIL'
    print(f"{tag}: 查询异常 -> {'放' if got is None else got!r}")
    if not passed:
        ok = False
    print('ALL PASS (6 cases)' if ok else 'FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())