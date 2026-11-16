# -*- coding: utf-8 -*-
"""R36 高位缩量接货 WARN 档 单元测试（PD-030② 2026-10-03「全部落地」落地件）
用临时 qfq 库替换 QFQ_DB, 构造三合取命中/未命中/数据不足三种场景。
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, r"D:\zcodesj")
import pre_trade_check_v2 as m  # noqa: E402


def make_db(path, closes, vols, chgs):
    """按 closes/vols/chgs 生成合成 60+ 根 Bar(high=close*1.02, low=close*0.98)"""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE stock_daily_meta_qfq (code TEXT, date TEXT, open REAL,"
                " high REAL, low REAL, close REAL, volume REAL, amount REAL, change_pct REAL)")
    n = len(closes)
    base = 20250101
    for i in range(n):
        c = closes[i]
        con.execute("INSERT INTO stock_daily_meta_qfq VALUES (?,?,?,?,?,?,?,?,?)",
                    ("000001", str(base + i), c, round(c * 1.02, 4), round(c * 0.98, 4),
                     c, vols[i], 1e7, chgs[i]))
    con.commit()
    con.close()


def run(code="000001"):
    return m.check_r36_highpos_shrink(code)


def main():
    results = []
    tmp = tempfile.mkdtemp()
    orig_db = m.QFQ_DB

    # 场景1: 三合取命中 = 高位横盘(pos60高) + 尾段缩量 + 近10日有一次>=9.5%
    closes = [10.0] * 55 + [10.5, 11.0, 11.5, 12.0, 12.0]  # 尾部小推升后横住
    vols = [1_000_000] * 58 + [400_000, 350_000]           # 尾两日缩量
    chgs = [0.0] * 58 + [5.0, 0.0]                          # 近10日内无>9.5 → 需注入
    chgs[-6] = 9.9                                          # 6日前一次涨停
    db1 = os.path.join(tmp, "qfq1.db")
    make_db(db1, closes, vols, chgs)
    m.QFQ_DB = db1
    r1 = run()
    results.append(("三合取命中→warn", r1["status"] == "warn", r1))

    # 场景2: 同形态但放量 → clear
    vols2 = [1_000_000] * 58 + [3_000_000, 2_800_000]
    db2 = os.path.join(tmp, "qfq2.db")
    make_db(db2, closes, vols2, chgs)
    m.QFQ_DB = db2
    r2 = run()
    results.append(("高位放量(不缩量)→clear", r2["status"] == "clear", r2))

    # 场景3: 缩量但无涨停基因 → clear
    chgs3 = [0.5] * 60
    db3 = os.path.join(tmp, "qfq3.db")
    make_db(db3, closes, vols, chgs3)
    m.QFQ_DB = db3
    r3 = run()
    results.append(("无涨停基因→clear", r3["status"] == "clear", r3))

    # 场景4: 数据不足 → fail_open
    db4 = os.path.join(tmp, "qfq4.db")
    make_db(db4, closes[:30], vols[:30], chgs[:30])
    m.QFQ_DB = db4
    r4 = run()
    results.append(("Bar不足→fail_open", r4["status"] == "fail_open", r4))

    m.QFQ_DB = orig_db
    ok = True
    for label, passed, detail in results:
        print(("PASS" if passed else "FAIL"), label, "|", str(detail.get("note", ""))[:90])
        ok = ok and passed
    print("R36 单测:", "ALL PASS" if ok else "HAS FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
