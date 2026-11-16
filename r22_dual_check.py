#!python
# -*- coding: utf-8 -*-
"""
R22 双口径资金硬闸 — 独立执行器 (2026-08-18 主人指令落地)

用法: D:/workbuddyspace/Claw/.venv_ml/Scripts/python.exe D:/zcodesj/r22_dual_check.py <code>
返回: status=pass/warn/reject/no_data/stale + 数字+说明

本闸独立于 pre_trade_check_v2.py (后者在热修中受损,待恢复);
R22 函数可直接复制回 pre_trade_check_v2.py 的 check() 函数中。

数据来源: D:/zcodesj/data/em_flow_snapshot.db (由 22:35 采集 cron 维护)
"""
import sqlite3, sys, re, os
from datetime import date as _d, timedelta as _td

DB_PATH = r"D:\zcodesj\data\em_flow_snapshot.db"
STALE_DAYS = 2

def r22(code: str) -> dict:
    code = code.strip().zfill(6)
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        row = con.execute(
            "SELECT em_5d, ths_5d, limit_days_5, trade_date FROM flow_snapshot "
            "WHERE code=? ORDER BY trade_date DESC LIMIT 1",
            (code,)
        ).fetchone()
        con.close()
    except Exception as e:
        return {"status": "no_data", "note": f"库不可用({type(e).__name__})"}
    
    if not row:
        return {"status": "no_data", "note": f"{code} 无双口径快照(跑22:35采集)"}
    
    em5, ths5, ld5, td_str = row
    em5 = em5 or 0; ths5 = ths5 or 0; ld5 = ld5 or 0
    
    # 新鲜度
    try:
        td = _d.fromisoformat(td_str)
        days_stale = (_d.today() - td).days
        if days_stale > STALE_DAYS:
            return {"status": "stale", "note": f"数据距今{days_stale}天>{STALE_DAYS}天"}
    except Exception:
        pass
    
    # 方向相反 → REJECT (核心争议)
    if (em5 > 0 and ths5 < 0) or (em5 < 0 and ths5 > 0):
        msg = f"EM5={em5:+,.0f} THS5={ths5:+,.0f} → 方向相反!"
        if ld5: msg += f" 近5日含{ld5}个停板日(争议区,须拆逐日)"
        return {"status": "reject", "note": msg, "em5": em5, "ths5": ths5}
    
    # 双负 → WARN
    if em5 <= 0 and ths5 <= 0:
        return {"status": "warn", "note": f"双口径均负: EM5={em5:+,.0f} THS5={ths5:+,.0f}",
                "em5": em5, "ths5": ths5}
    
    # 同正 → PASS (含停板日→WARN标注)
    note = f"双口径同正: EM5={em5:+,.0f} THS5={ths5:+,.0f}"
    if ld5:
        note += f" ⚠️近5日含{ld5}个停板日(争议区,须拆逐日)"
        return {"status": "warn", "note": note, "em5": em5, "ths5": ths5}
    return {"status": "pass", "note": note, "em5": em5, "ths5": ths5}


def main():
    if len(sys.argv) < 2:
        print("用法: python r22_dual_check.py <code1> [code2 ...]")
        sys.exit(1)
    for c in sys.argv[1:]:
        r = r22(c)
        print(f"{c.zfill(6)}: {r['status']} | {r['note']}")

if __name__ == "__main__":
    main()
