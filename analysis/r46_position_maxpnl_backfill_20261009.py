# -*- coding: utf-8 -*-
"""position_history max_pnl/max_drawdown 回填 (一次性补数, 2026-10-09)
缺口: attribution.db position_history CLOSED 行 max_pnl_pct/max_drawdown_pct 全 0(116/142),
  盈利回吐度(giveback)不可算 = 风格复盘核心指标缺失。
口径(收盘近似, 标注非盘中极值): 以 qfq 库窗口首日收盘为基准,
  max_pnl_pct = (窗口最高close/首日close-1)*100
  max_drawdown_pct = (窗口最低close/此前峰值close-1)*100
幂等: 仅 UPDATE WHERE max_pnl_pct=0 AND max_drawdown_pct=0, 不触碰其他列/行;
写库前备份 attribution.db -> tmp/。
"""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ATTR = r"D:\zcodesj\attribution\attribution.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
BAK = Path(r"D:\zcodesj\tmp\attribution_backup_20261009_giveback.db")

shutil.copy2(ATTR, BAK)
print(f"[backup] {BAK} ({BAK.stat().st_size//1024}KB)")

con_a = sqlite3.connect(ATTR)
cur_a = con_a.cursor()
rows = cur_a.execute(
    "SELECT id, code, entry_date, exit_date FROM position_history "
    "WHERE status='CLOSED' AND max_pnl_pct=0 AND max_drawdown_pct=0 AND entry_date IS NOT NULL AND exit_date IS NOT NULL"
).fetchall()
print(f"[scan] 待回填 {len(rows)} 行")

con_q = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
cur_q = con_q.cursor()

done, miss = 0, 0
for rid, code, e, x in rows:
    c = (code or "").strip()
    c6 = c[-6:] if len(c) >= 6 else c  # 剥 sz/sh/bj 前缀取 6 位
    cur_q.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date>=? AND date<=? ORDER BY date",
        (c6, e, x))
    closes = [r[0] for r in cur_q.fetchall() if r[0]]
    if len(closes) < 2:
        miss += 1
        continue
    base = closes[0]
    peak, max_pnl, max_dd = base, 0.0, 0.0
    for c in closes:
        pnl = (c / base - 1) * 100
        if pnl > max_pnl:
            max_pnl = pnl
        if c > peak:
            peak = c
        dd = (c / peak - 1) * 100
        if dd < max_dd:
            max_dd = dd
    cur_a.execute(
        "UPDATE position_history SET max_pnl_pct=?, max_drawdown_pct=? WHERE id=? AND max_pnl_pct=0 AND max_drawdown_pct=0",
        (round(max_pnl, 4), round(max_dd, 4), rid))
    done += 1
con_a.commit()
con_a.close()
con_q.close()
print(f"[done] 回填 {done} 行, 缺行情跳过 {miss} 行 (幂等: 重跑=0)")
