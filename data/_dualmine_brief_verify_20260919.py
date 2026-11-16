# -*- coding: utf-8 -*-
"""双雷WARN验收(2026-09-19): bp_ep_exec_brief.annotate 双雷路径对账
1) 浦发银行600000(清单在列,双雷'-'): 手算MA20/乖离/量分位 vs 脚本同口径
2) 内蒙新华603230(近期连板暴涨): 预期触发W1乖离雷, 验证warn输出路径
只读qfq, 零写入。
"""
import sys
sys.path.insert(0, r"D:\zcodesj")
sys.stdout.reconfigure(encoding="utf-8")
import sqlite3

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
db = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)

for code, name, expect in [("600000", "浦发银行", "清单在列,双雷应为-"),
                            ("603230", "内蒙新华", "暴涨票,预期W1乖离雷触发")]:
    hist = db.execute(
        "SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 250",
        (code, "20260919")).fetchall()
    px = hist[0][0]
    ma20 = sum(c for (c, v) in hist[:20]) / 20
    off = (px / ma20 - 1) * 100
    vols = sorted(v for (c, v) in hist)
    vrank = 100 * sum(1 for v in vols if v <= hist[0][1]) / len(vols)
    w1 = off > 3
    w2 = vrank >= 80
    warn = []
    if w1:
        warn.append(f"W1乖离{off:+.1f}%")
    if w2:
        warn.append(f"W2量位{vrank:.0f}%")
    print(f"{code} {name} ({expect}): 数据日行数={len(hist)} 最新close={px} "
          f"MA20={ma20:.3f} 乖离={off:+.2f}% 当日量分位={vrank:.1f} "
          f"=> warn={'⚠' + '+'.join(warn) if warn else '-'}")
db.close()
