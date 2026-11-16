# -*- coding: utf-8 -*-
"""一次性修复: stock_info 中 7 只被错标为指数的真股票 (2026-11-08)。

背景: 99 个 market='其他' 码 = 92 真指数 + 7 真股票(标签错标)。7 码均有 vipdoc
sz/lday 深市 .day 文件 + 真实股票级价格序列(如 000851 实际交易至 20250926 退市),
tushare 退市名单(list_status='D')给出权威名称(退市后缀按既有库惯例去"(退)")。

字段修正: name / market(其他→深市主板) / instrument_type(index→stock)
          is_st/status/list_date 保持原值(模板对齐 000018 神城A退/000038 大通退)。
幂等守卫: UPDATE 带 instrument_type='index' 前置条件, 重跑零改动。
台账: data/stock_info_mislabeled7_repair_ledger.jsonl (修复前快照+证据)。

证据链:
  身份=深市lday文件存在 + 股票级价格(退市前) + tushare退市名单命中
  名称=tushare list_status='D' 权威返回(去"(退)"后缀, 对齐 000018'神城A退'惯例)
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant.db'

# tushare list_status='D' 2026-11-08 查询权威名称(去"(退)"状态后缀, 对齐既有库惯例)
TRUE_NAMES = {
    '000033': '新都退',
    '000047': 'ST中侨',
    '000689': 'ST宏业',
    '000693': '华泽退',
    '000699': 'S*ST佳纸',
    '000827': '*ST长兴',
    '000851': '*ST高鸿',
}
WRONG_LABELS = {'上证材料', '上证全指', '科创材料', '科创机械', '科创200', '中证环保', '百发100'}


def main(execute: bool):
    con = sqlite3.connect(DB if execute else DB + '?mode=ro', uri=True)
    if execute:
        con.execute('PRAGMA busy_timeout=30000')
    cur = con.cursor()
    codes = sorted(TRUE_NAMES)
    rows = {c: cur.execute(
        'SELECT code,name,market,instrument_type,is_st,status,list_date FROM stock_info WHERE code=?',
        (c,)).fetchone() for c in codes}

    # ---- 前置断言(任一失败拒绝执行) ----
    for c, r in rows.items():
        assert r is not None, f'{c} 不在 stock_info, 拒绝执行'
        code_, name_, mkt, itype, is_st, status, ldate = r
        assert name_ in WRONG_LABELS, f'{c} 当前名={name_} 不在已知错标集, 拒绝执行(防重复漂移)'
        assert itype == 'index' and mkt == '其他', f'{c} itype={itype}/mkt={mkt} 非预期, 拒绝执行'

    # ---- 台账(修复前快照) ----
    ledger = {
        'ts': datetime.now().isoformat(timespec='seconds'),
        'scope': 'stock_info 7只错标真股票名称/市场/品种修正',
        'evidence': 'sz lday文件存在+股票级价格(000851至20250926退市)+tushare list_status=D 名称权威',
        'before': {c: dict(zip(('name', 'market', 'itype', 'is_st', 'status', 'list_date'), r[1:])) for c, r in rows.items()},
        'after_names': dict(TRUE_NAMES),
    }
    os.makedirs(r'D:\zcodesj\data', exist_ok=True)
    with open(r'D:\zcodesj\data\stock_info_mislabeled7_repair_ledger.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(ledger, ensure_ascii=False) + '\n')

    if not execute:
        print('[DRY-RUN] 断言全过, 待写名称:', {c: TRUE_NAMES[c] for c in codes})
        print('[DRY-RUN] 字段: market=深市主板, instrument_type=stock; is_st/status/list_date 不动')
        con.close()
        return

    # ---- 执行(幂等守卫) ----
    for c in codes:
        cur.execute(
            "UPDATE stock_info SET name=?, market='深市主板', instrument_type='stock' "
            "WHERE code=? AND instrument_type='index'", (TRUE_NAMES[c], c))
        n = cur.rowcount
        assert n == 1, f'{c} 影响行数={n} != 1, 回滚'
    con.commit()

    # ---- 验证 ----
    for c in codes:
        r = cur.execute('SELECT code,name,market,instrument_type FROM stock_info WHERE code=?', (c,)).fetchone()
        assert r[1] == TRUE_NAMES[c] and r[2] == '深市主板' and r[3] == 'stock', f'{c} 修复后校验失败: {r}'
        print('[OK]', r)
    con.close()
    print('[DONE] 7 只错标真股票已修正, 台账已落 data/stock_info_mislabeled7_repair_ledger.jsonl')


if __name__ == '__main__':
    main(execute='--execute' in sys.argv)