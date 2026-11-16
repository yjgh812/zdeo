# -*- coding: utf-8 -*-
"""一次性修复: stock_info 中 302132(中航成飞) 品种错标 other→stock (2026-11-08)。
身份证据: 真实存在股票(深市创业板代码段302xxx新段)+qfq全史2623行(2016→2026-11-06,
中航电测→中航成飞代码延续口径)+名称库非空。误标后果=买入品种闸(IDENT-GATE)会把
真股票当非股票拦截。幂等守卫: WHERE instrument_type='other' 前置条件。
台账: 追加至 data/stock_info_mislabeled7_repair_ledger.jsonl。
"""
import json
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant.db'


def main(execute):
    con = sqlite3.connect(DB if execute else DB + '?mode=ro', uri=True)
    cur = con.cursor()
    r = cur.execute("SELECT code,name,market,instrument_type,is_st,status FROM stock_info "
                    "WHERE code='302132'").fetchone()
    assert r is not None, '302132 不在 stock_info'
    code_, name_, mkt, itype, is_st, status = r
    assert name_ == '中航成飞', f'名称漂移: {name_}, 拒绝执行'
    assert itype == 'other', f'itype={itype} 非预期(已修复?), 拒绝执行'
    assert mkt == '创业板', f'market={mkt} 非预期, 拒绝执行'
    ledger = {
        'ts': datetime.now().isoformat(timespec='seconds'),
        'scope': 'stock_info 302132 中航成飞 instrument_type other->stock',
        'evidence': '真股票(创业板302xxx)+qfq全史2623行(中航电测代码延续)+IDENT-GATE防误拦',
        'before': {'name': name_, 'market': mkt, 'itype': itype, 'is_st': is_st, 'status': status},
        'after': {'instrument_type': 'stock'},
    }
    with open(r'D:\zcodesj\data\stock_info_mislabeled7_repair_ledger.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(ledger, ensure_ascii=False) + '\n')
    if not execute:
        print('[DRY-RUN] 断言全过, 待写: 302132 instrument_type other->stock')
        con.close()
        return
    cur.execute("UPDATE stock_info SET instrument_type='stock' "
                "WHERE code='302132' AND instrument_type='other'")
    assert cur.rowcount == 1, '影响行数!=1'
    con.commit()
    r2 = cur.execute("SELECT code,name,market,instrument_type FROM stock_info WHERE code='302132'").fetchone()
    assert r2[3] == 'stock'
    print('[OK]', r2)
    print('[DONE] 302132 已修正, 台账已追加')
    con.close()


if __name__ == '__main__':
    main(execute='--execute' in sys.argv)