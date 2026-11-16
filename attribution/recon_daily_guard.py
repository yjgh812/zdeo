# -*- coding: utf-8 -*-
"""trade_records 日对账守卫 (2026-09-25, recon 根因守卫第二层)
真值=fills_clean.db(westock status=已成交官方通道, fetch_fills_official.py 幂等日更);
比对对象=attribution.db trade_records(归一化聚合: 裸码+日期+向 -> 数量, vwap 容差 0.02);
差异组>0 -> notifications.log 告警 + exit 1。只读, 不写库。
用法: python attribution/recon_daily_guard.py [--date YYYYMMDD](缺省=fills 全量)
"""
import sys, sqlite3, datetime
from collections import defaultdict

sys.path.insert(0, r"D:\zcodesj")
from attribution.trade_records_guard import norm_code, norm_date, TOL_PRICE

FILLS = r"D:\zcodesj\data\fills_clean.db"
ATTR = r"D:\zcodesj\attribution\attribution.db"
NOTIFY = r"D:\zcodesj\reports\notifications.log"

def load_fills(day=None):
    con = sqlite3.connect(f"file:{FILLS}?mode=ro", uri=True)
    q = "SELECT code, filled_at, action, quantity, price, amount FROM fills"
    if day:
        q += f" WHERE REPLACE(filled_at,'-','') LIKE '{day}%'"
    rows = con.execute(q).fetchall()
    con.close()
    agg = {}
    for code, fa, act, qty, price, amt in rows:
        k = (norm_code(code), norm_date(fa), str(act).lower())
        a = agg.setdefault(k, [0, 0.0])
        a[0] += int(qty); a[1] += float(amt if amt else qty * price)
    return agg

def load_records(day=None):
    con = sqlite3.connect(f"file:{ATTR}?mode=ro", uri=True)
    q = "SELECT code, trade_date, action, volume, amount, price, notes FROM trade_records"
    rows = con.execute(q).fetchall()
    con.close()
    agg = {}
    for code, td, act, vol, amt, price, notes in rows:
        k = (norm_code(code), norm_date(td), str(act).lower())
        a = agg.setdefault(k, [0, 0.0, []])
        a[0] += int(vol or 0)
        a[1] += float(amt if amt else (vol or 0) * (price or 0))
        a[2].append(notes or "")
    return agg

def main():
    """默认=今日增量检测(新写入污染守卫, 告警零疲劳); --date YYYYMMDD 指定日; --all 全量(人工口径,
    存量粒度残差已知: 20260925基线=77组价格聚合残差+幽灵标注组, 见 data/recon_guard_baseline_20260925.json)。"""
    if "--all" in sys.argv:
        day = None
    elif "--date" in sys.argv:
        day = norm_date(sys.argv[sys.argv.index("--date") + 1])
    else:
        day = datetime.date.today().strftime("%Y%m%d")
    ws_all, lc_all = load_fills(None), load_records(None)
    if day:
        ws = {k: v for k, v in ws_all.items() if k[1] == day}
        lc = {k: v for k, v in lc_all.items() if k[1] == day}
    else:
        ws, lc = ws_all, lc_all
    diffs = []
    for k in sorted(set(ws) | set(lc)):
        w, l = ws.get(k), lc.get(k)
        wq = w[0] if w else 0
        lq = l[0] if l else 0
        if wq != lq:
            diffs.append(("QTY", k, f"westock={wq} local={lq}"))
        elif wq and w[1] and l[1] and abs(w[1] / wq - l[1] / lq) > TOL_PRICE:
            diffs.append(("PRICE", k, f"westock_vwap={w[1]/wq:.2f} local_vwap={l[1]/lq:.2f}"))
    if not diffs:
        print(f"[recon_guard] OK: fills与trade_records一致 (fills={len(ws)}键, records={len(lc)}键"
              f"{', date=' + day if day else ''})")
        return 0
    line = (f"{datetime.datetime.now().isoformat(timespec='seconds')} [P1][recon_guard] "
            f"trade_records与westock真值差异 {len(diffs)} 组"
            f"{', date=' + day if day else ''}: " + "; ".join(f"{t}{k[0]}{k[1]}{k[2]} {m}" for t, k, m in diffs[:10]))
    print("[recon_guard] DIFF:", line)
    with open(NOTIFY, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return 1

if __name__ == "__main__":
    sys.exit(main())
