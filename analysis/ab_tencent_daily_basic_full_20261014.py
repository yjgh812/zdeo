# -*- coding: utf-8 -*-
"""腾讯批量路径全市场A/B对拍 (2026-10-14, 只读零写库)

对拍: qt.gtimg.cn 批量快照(88字段, 盘后=收盘口径) vs fundamental_data.db daily_basic
      现合同行(tushare/baostock 已写入), 全量非零行逐列统计差异分布。
验收线: close/量比/pe_ttm/dv_ttm/股本/市值 = 舍入级吻合; 换手率同; pb 列披露时点差如实统计。
用途: 接入 daily_chain_update.run_daily_basic 腾讯兜底路径前的唯一放行证据。

用法: python analysis/ab_tencent_daily_basic_full_20261014.py [target]
"""
import sqlite3
import statistics
import sys
import time
import urllib.request

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
TARGET = sys.argv[1] if len(sys.argv) > 1 else "20261013"
BATCH = 60


def fetch_batch(syms):
    url = "http://qt.gtimg.cn/q=" + ",".join(syms)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read().decode("gbk", errors="ignore")
    out = {}
    for line in data.strip().split(";"):
        line = line.strip()
        if "~" not in line:
            continue
        key = line.split("=", 1)[0].replace("v_", "").strip()
        out[key] = line.split("=", 1)[1].strip('"').split("~")
    return out


def main():
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT ts_code, close, turnover_rate, volume_ratio, pe_ttm, pb, dv_ttm,"
        " total_share, float_share, total_mv, circ_mv FROM daily_basic"
        " WHERE trade_date=? AND close>0", (TARGET,)).fetchall()
    con.close()
    print(f"[A/B] 库行 {len(rows)} target={TARGET}")

    diff = {k: [] for k in ("close", "turnover_rate", "volume_ratio", "pe_ttm", "pb",
                            "dv_ttm", "total_share", "float_share", "total_mv", "circ_mv")}
    miss = noquote = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        syms = [("sh" if c[0].endswith(".SH") else "sz") + c[0][:6] for c in chunk]
        for attempt in range(3):
            try:
                quotes = fetch_batch(syms)
                break
            except Exception as e:
                if attempt == 2:
                    raise
                time.sleep(2)
        for r, sym in zip(chunk, syms):
            v = quotes.get(sym)
            if not v or len(v) < 74:
                noquote += 1
                continue
            try:
                pairs = {
                    "close": (float(r[1]), float(v[3])),
                    "turnover_rate": (float(r[2]), float(v[38])),
                    "volume_ratio": (float(r[3]), float(v[49])),
                    "pe_ttm": (float(r[4]), float(v[39])),
                    "pb": (float(r[5]), float(v[46])),
                    "dv_ttm": (float(r[6]), float(v[64])),
                    "total_share": (float(r[7]), float(v[73]) / 1e4),
                    "float_share": (float(r[8]), float(v[72]) / 1e4),
                    "total_mv": (float(r[9]), float(v[45]) * 1e4),
                    "circ_mv": (float(r[10]), float(v[44]) * 1e4),
                }
            except (ValueError, IndexError):
                miss += 1
                continue
            for k, (db_v, tx_v) in pairs.items():
                if db_v == 0 or tx_v == 0:
                    continue  # 0=未采集语义, 不参与差异统计
                rel = abs(tx_v - db_v) / max(abs(db_v), 1e-9)
                diff[k].append(rel * 100)
        if (i // BATCH) % 20 == 0:
            print(f"  ... {i + len(chunk)}/{len(rows)}")

    print(f"\n[A/B] 相对差异(%) 统计  n={len(rows)} 无快照={noquote} 解析失败={miss}")
    print(f"{'列':<14}{'n':>6}{'中位':>10}{'p95':>10}{'max':>10}{'>1%':>8}")
    for k, arr in diff.items():
        if not arr:
            print(f"{k:<14}{0:>6}    (无有效样本)")
            continue
        arr_s = sorted(arr)
        p95 = arr_s[int(len(arr_s) * 0.95)]
        over1 = sum(1 for x in arr if x > 1.0)
        print(f"{k:<14}{len(arr):>6}{statistics.median(arr):>10.4f}{p95:>10.4f}"
              f"{max(arr):>10.4f}{over1:>5} ({over1 / len(arr) * 100:.2f}%)")


if __name__ == "__main__":
    sys.exit(main())
