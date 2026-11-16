r"""pullback_anchor_scan.py — 首板/放量启动→缩量回踩不破锚→确认买入 每日扫描器。

方法来源: 主人 2026-11-10 令(华安32,615首板样本口径+涨停换手U型+环境闸)。
零交易权: 输出候选观察清单, 任何买入仍须走 trade_executor + 主人裁定。

条件(按主人十条清单, 全部 qfq 日线可算):
  S1 启动锚点: 窗口[start,end]内存在涨停日(主板 change_pct>=9.8, 创业板>=19.8, 收=高)
  S2 回踩时窗: T(扫描日)距涨停日 3-5 个交易日
  S3 缩量: T量 <= 0.5×涨停日量(严格档 1/3 另标)
  S4 价格锚: 区间收盘均不破涨停日收盘价; T收盘 ∈ [anchor, anchor×1.03] 入场区
  S5 确认: T收阳 且 T最低 >= 区间前低(回踩不创新低)
输出: reports/pullback_scan_<date>.json + 控制台清单。
用法: python attribution/pullback_anchor_scan.py [YYYYMMDD=最新交易日]
"""
import json
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_DIR = r"D:\zcodesj\reports"


def main():
    conn = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    if len(sys.argv) > 1:
        scan_date = sys.argv[1]
    else:
        scan_date = conn.execute(
            "SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        print("[INFO] scan_date 缺省=库内最新交易日 %s" % scan_date)
    rows = conn.execute(
        "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? ORDER BY code,date",
        ("20260901", scan_date)).fetchall()
    conn.close()
    by_code = {}
    for c, d, o, h, l, cl, v, cp in rows:
        by_code.setdefault(c, []).append((d, o, h, l, cl, v, cp))
    cands, stats = [], {"codes": len(by_code), "board_hits": 0}
    for c, bars in by_code.items():
        if len(bars) < 25 or str(c).startswith(("4", "8", "9")):
            continue
        idx = {d: i for i, b in enumerate(bars) for d in [b[0]]}
        if scan_date not in idx:
            continue
        ti = idx[scan_date]
        is_gem = c.startswith(("300", "301", "302"))
        thr = 19.8 if is_gem else 9.8
        for bi in range(max(0, ti - 5), ti - 2):  # 涨停日距T 3-5交易日
            d, o, h, l, cl, v, cp = bars[bi]
            if cp is None or cp < thr or cl < h * 0.999 or v is None or v <= 0:
                continue
            stats["board_hits"] += 1
            b_bars = bars[bi + 1:ti + 1]
            if len(b_bars) < 2:
                break
            t = bars[ti]
            if t[5] is None or t[5] > v * 0.5:            # S3 缩量<=50%
                break
            if min(b[4] for b in b_bars) < cl:            # S4 区间不破锚
                break
            if not (cl <= t[4] <= cl * 1.03):             # S4 入场区 [anchor,+3%]
                break
            prior_low = min(b[3] for b in b_bars[:-1]) if len(b_bars) > 1 else t[3]
            strict_vol = t[5] <= v / 3.0
            cands.append({
                "code": c, "board_date": d, "board_chg": round(cp, 2),
                "anchor": cl, "close": t[4], "open_T": t[1],
                "vol_ratio_vs_board": round(t[5] / v, 3),
                "strict_vol_le_1_3": strict_vol, "yang_confirm": t[4] > t[1],
                "no_new_low": t[3] >= prior_low, "days_since": ti - bi})
            break
    confirmed = [x for x in cands if x["yang_confirm"] and x["no_new_low"]]
    watchlist = [x for x in cands if not (x["yang_confirm"] and x["no_new_low"])]
    # v2: 板日换手率(低换手结构并联标记, 数据源=fundamental_data.db daily_basic)
    try:
        fconn = sqlite3.connect(
            r"file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro", uri=True)
        for grp in (confirmed, watchlist):
            for x in grp:
                row = fconn.execute(
                    "SELECT turnover_rate FROM daily_basic WHERE ts_code=? AND trade_date=?",
                    (x["code"] + (".SH" if x["code"].startswith("6") else ".SZ"),
                     str(x["board_date"]))).fetchone()
                x["board_turnover"] = row[0] if row else None
                x["low_turnover_board"] = bool(row and row[0] is not None and row[0] < 3.0)
        fconn.close()
    except sqlite3.Error as e:
        for grp in (confirmed, watchlist):
            for x in grp:
                x["board_turnover"] = None
                x["low_turnover_board"] = None
        print("[WARN] daily_basic 不可用: %r" % e)
    out = {"scan_date": scan_date, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "stats": stats, "n_cands": len(confirmed),
           "candidates": confirmed, "watchlist": watchlist}
    path = os.path.join(OUT_DIR, "pullback_scan_%s.json" % scan_date)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("扫描完成: %d 码 / 涨停锚命中 %d / 确认候选 %d / 观察档 %d -> %s" % (
        stats["codes"], stats["board_hits"], len(confirmed), len(watchlist), path))
    for x in confirmed:
        print("[候选] %s 板%s@%.2f 现收%.2f 量比%.2f 板换手%s 距%d日" % (
            x["code"], x["board_date"], x["anchor"], x["close"],
            x["vol_ratio_vs_board"], x.get("board_turnover"), x["days_since"]))
    for x in watchlist:
        print("[观察] %s 板%s@%.2f 现收%.2f 量比%.2f 板换手%s 阳=%s 不破低=%s 距%d日" % (
            x["code"], x["board_date"], x["anchor"], x["close"],
            x["vol_ratio_vs_board"], x.get("board_turnover"),
            x["yang_confirm"], x["no_new_low"], x["days_since"]))


if __name__ == "__main__":
    main()
