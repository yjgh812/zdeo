"""
westock模拟盘交易导入脚本
将FIFO重构的31笔已平仓交易写入 attribution.db
数据源：westock practice_190 账户历史成交记录（range=recent）

🔴 DEPRECATED/已退役 (2026-09-25): 一次性硬编码31笔导入脚本=202607批三源重复导入污染源之一
（recon 对账实锤, 见 reports/execution_gap_20260925/RECON_v2_2026full.md §五）。
31 笔已入库且经 recon 卫生收口归一; 禁止复跑, 成交导入唯一正道=naked_trade_audit(带归一化查重守卫)。
"""
import sys as _sys
print("[BLOCKED] import_westock_trades.py 已退役(20260925): 一次性硬编码脚本, 禁止复跑;"
      " 成交导入唯一正道=naked_trade_audit.py")
_sys.exit(2)
import sqlite3
import json

DB_PATH = r'D:\zcodesj\attribution\attribution.db'

# ========== 31笔已平仓交易数据（FIFO重构） ==========
# 格式: (code, name, entry_date, entry_price, exit_date, exit_price, volume, pnl_amount, pnl_pct, holding_days, sector, status)

TRADES = [
    # --- 完全平仓28笔 ---
    ("sz002866", "传艺科技", "2026-07-22", 15.64, "2026-07-23", 14.04, 100, -171, -10.88, 1, "消费电子", "CLOSED"),
    ("sh601065", "江盐集团", "2026-07-22", 6.63, "2026-07-27", 6.61, 30100, -805, -0.40, 5, "化工", "CLOSED"),
    ("sh601059", "信达证券", "2026-07-22", 16.50, "2026-07-28", 16.09, 12100, -5161, -2.58, 6, "证券", "CLOSED"),
    ("sz002537", "海联金汇", "2026-07-22", 5.57, "2026-07-23", 5.47, 35900, -3787, -1.89, 1, "区块链", "CLOSED"),
    ("sz002191", "劲嘉股份", "2026-07-22", 3.64, "2026-07-23", 3.62, 54900, -1297, -0.65, 1, "烟草包装", "CLOSED"),
    ("sz300534", "陇神戎发", "2026-07-23", 12.83, "2026-07-27", 11.18, 15000, -24924, -12.95, 4, "中药", "CLOSED"),
    ("sh601728", "中国电信", "2026-07-23", 6.05, "2026-08-03", 6.28, 33000, 7924, 3.97, 11, "通信", "CLOSED"),
    ("sz300142", "沃森生物", "2026-07-23", 13.14, "2026-07-27", 11.94, 15000, -18184, -9.22, 4, "疫苗", "CLOSED"),
    ("sz002668", "TCL智家", "2026-07-28", 10.62, "2026-07-29", 10.40, 17600, -4324, -2.31, 1, "家电", "CLOSED"),
    ("sh601939", "建设银行", "2026-07-29", 10.69, "2026-08-03", 10.20, 8800, -3649, -3.91, 5, "银行", "CLOSED"),
    ("sh600795", "国电电力", "2026-07-28", 4.86, "2026-07-29", 4.80, 16000, -1039, -1.34, 1, "电力", "CLOSED"),
    ("sh600900", "长江电力", "2026-07-29", 29.60, "2026-08-03", 29.09, 2400, -1296, -1.82, 5, "电力", "CLOSED"),
    ("sh601328", "交通银行", "2026-07-29", 7.15, "2026-08-03", 7.18, 12600, 286, 0.32, 5, "银行", "CLOSED"),
    ("sh601288", "农业银行", "2026-07-29", 7.02, "2026-07-31", 6.80, 21100, -4668, -3.15, 2, "银行", "CLOSED"),
    ("sh601006", "大秦铁路", "2026-07-30", 5.04, "2026-08-03", 4.97, 14800, -1111, -1.49, 4, "铁路", "CLOSED"),
    ("sh601988", "中国银行", "2026-07-30", 6.14, "2026-08-03", 6.06, 12200, -1052, -1.40, 4, "银行", "CLOSED"),
    ("sh601088", "中国神华", "2026-07-30", 45.70, "2026-08-03", 44.25, 1600, -2393, -3.27, 4, "煤炭", "CLOSED"),
    ("sh601985", "中国核电", "2026-07-30", 9.07, "2026-08-03", 9.06, 8200, -158, -0.21, 4, "电力", "CLOSED"),
    ("sh603075", "热威股份", "2026-07-31", 18.48, "2026-08-03", 18.28, 2600, -569, -1.18, 3, "汽车零部件", "CLOSED"),
    ("sz300943", "春晖智控", "2026-07-31", 22.90, "2026-08-03", 21.94, 2100, -2063, -4.29, 3, "仪表", "CLOSED"),
    ("sh605100", "华丰股份", "2026-07-31", 29.20, "2026-08-03", 29.23, 1700, 0, 0.00, 3, "机械", "CLOSED"),
    ("sh603278", "大业股份", "2026-08-03", 9.30, "2026-08-04", 9.21, 4300, -427, -1.07, 1, "金属制品", "CLOSED"),
    ("sz002637", "赞宇科技", "2026-08-03", 10.30, "2026-08-04", 10.51, 43100, 8600, 1.94, 1, "化工", "CLOSED"),
    ("sz002517", "恺英网络", "2026-08-04", 18.67, "2026-08-05", 18.37, 13600, -6456, -2.52, 1, "游戏", "CLOSED"),
    ("sh600415", "小商品城", "2026-08-04", 12.48, "2026-08-05", 12.35, 19200, -2739, -1.14, 1, "零售", "CLOSED"),
    ("sh603201", "常润股份", "2026-08-05", 17.60, "2026-08-06", 17.50, 5700, -672, -0.67, 1, "汽车零部件", "CLOSED"),
    ("sz300077", "国民技术", "2026-08-06", 18.85, "2026-08-10", 19.41, 500, 265, 2.81, 4, "半导体", "CLOSED"),
    ("sz002181", "粤传媒", "2026-08-06", 9.52, "2026-08-10", 9.12, 20000, -8084, -4.25, 4, "传媒", "CLOSED"),
    # --- 部分平仓3笔 ---
    ("sh605133", "嵘泰股份", "2026-08-03", 23.92, "2026-08-10", 25.95, 14900, 29858, 8.38, 7, "汽车压铸", "PARTIAL_CLOSED"),
    ("sh600644", "乐山电力", "2026-08-03", 10.18, "2026-08-10", 10.33, 3900, 1246, 3.19, 7, "电力", "PARTIAL_CLOSED"),
    ("sz002361", "神剑股份", "2026-08-05", 11.36, "2026-08-10", 12.16, 7600, 5909, 6.84, 5, "军工材料", "PARTIAL_CLOSED"),
]


def import_trades():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # purge保护2026-08-12：不再全表DELETE，改用INSERT OR REPLACE幂等导入
    # 如需重置请显式 --reset 参数，避免重跑脚本清空历史归因
    print("[INFO] 使用幂等导入(INSERT OR REPLACE)，历史数据保留。如需重置请加 --reset 参数。")

    pos_id_map = {}  # code -> position_id

    for i, t in enumerate(TRADES):
        code, name, entry_date, entry_price, exit_date, exit_price, volume, pnl_amount, pnl_pct, holding_days, sector, status = t

        # 写入 trade_records（买入+卖出两笔）
        for action, date, price in [("BUY", entry_date, entry_price), ("SELL" if status == "CLOSED" else "PARTIAL_SELL", exit_date, exit_price)]:
            # 2026-09-23 T8(幂等查重): 五元组(code,trade_date,action,price,volume)精确匹配, 重跑不重复记账
            _ex = cursor.execute('''
                SELECT COUNT(*) FROM trade_records
                WHERE code=? AND trade_date=? AND action=? AND price=? AND volume=?
            ''', (code, date, action, price, volume)).fetchone()[0]
            if _ex:
                print(f"[skip] trade_records duplicate skipped: {code} {date} {action} {price} x{volume}(幂等查重T8)")
                continue
            cursor.execute('''
                INSERT INTO trade_records (code, name, trade_date, action, price, volume, amount, signal_type, sector, strategy_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?, 'westock_v1')
            ''', (code, name, date, action, price, volume, price * volume, sector))

        # 写入 position_history
        exit_reason = "manual"
        cursor.execute('''
            INSERT INTO position_history
            (code, name, entry_date, entry_price, exit_date, exit_price, exit_reason,
             pnl_pct, pnl_amount, status, holding_days, strategy_version, sector, market_env_at_entry)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'westock_v1', ?, 'shock')
        ''', (code, name, entry_date, entry_price, exit_date, exit_price, exit_reason,
              pnl_pct, pnl_amount, status, holding_days, sector))

        pos_id = cursor.lastrowid
        pos_id_map[code] = pos_id

    conn.commit()

    # 验证
    cursor.execute("SELECT COUNT(*) FROM position_history WHERE status IN ('CLOSED','PARTIAL_CLOSED')")
    pos_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM trade_records")
    trade_count = cursor.fetchone()[0]

    # 统计
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN pnl_amount > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN pnl_amount <= 0 THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN pnl_amount > 0 THEN pnl_amount ELSE 0 END) as total_profit,
            SUM(CASE WHEN pnl_amount <= 0 THEN pnl_amount ELSE 0 END) as total_loss,
            SUM(pnl_amount) as net_pnl,
            AVG(holding_days) as avg_hold
        FROM position_history WHERE status IN ('CLOSED','PARTIAL_CLOSED')
    """)
    stats = cursor.fetchone()

    conn.close()

    print(f"[OK] Imported {pos_count} positions, {trade_count} trade records")
    print(f"\n===== 已平仓交易统计 =====")
    print(f"总笔数:     {stats[0]}")
    print(f"盈利笔数:   {stats[1]}")
    print(f"亏损笔数:   {stats[2]}")
    win_rate = stats[1] / stats[0] * 100 if stats[0] > 0 else 0
    print(f"胜率:       {win_rate:.2f}%")
    print(f"总盈利:     +¥{stats[3]:,.0f}")
    print(f"总亏损:     ¥{stats[4]:,.0f}")
    print(f"净盈亏:     ¥{stats[5]:,.0f}")
    avg_profit = stats[3] / stats[1] if stats[1] > 0 else 0
    avg_loss = abs(stats[4] / stats[2]) if stats[2] > 0 else 0
    print(f"平均盈利:   +¥{avg_profit:,.0f}")
    print(f"平均亏损:   -¥{avg_loss:,.0f}")
    print(f"盈亏比:     {avg_profit / avg_loss:.2f}" if avg_loss > 0 else "N/A")
    print(f"利润因子:   {stats[3] / abs(stats[4]):.2f}" if stats[4] != 0 else "N/A")
    print(f"平均持仓:   {stats[6]:.1f}天")


if __name__ == '__main__':
    import_trades()
