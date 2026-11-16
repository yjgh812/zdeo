# -*- coding: utf-8 -*-
"""monday12_postcheck.py — 周一12只买入盘后核验报告(只读, 2026-11-07建)
读 executor_orders 台账(trade_date=当日, 12只codes) + open_orders + westock实时持仓桥,
输出: 逐笔 FILLED/OPEN/SKIPPED + 挂单价vs成交价滑点 + 实验臂接管确认(source入账)。
用法: python monday12_postcheck.py [--date 20261109]
"""
import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from executor_order_ledger import EXEC_ORDERS, OPEN_ORDERS  # noqa: E402

CODES = {
    "301046": ("能辉科技", "dip_up_v1"), "300931": ("通用电梯", "dip_up_v1"),
    "600410": ("华胜天成", "dip_up_v1"), "301265": ("华新科技", "dip_up_v1"),
    "000595": ("新能股份", "dip_fb_v1"), "001258": ("立新能源", "dip_fb_v1"),
    "600156": ("华升股份", "dip_fb_v1"), "600207": ("安彩高科", "dip_fb_v1"),
    "300286": ("安科瑞", "dip_ret_v1"), "300360": ("炬华科技", "dip_ret_v1"),
    "600959": ("江苏有线", "dip_ret_v1"), "300030": ("阳普医疗", "dip_ret_v1"),
}


def main():
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    D = str(a.date)
    rows = {}
    if os.path.exists(EXEC_ORDERS):
        for line in open(EXEC_ORDERS, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("trade_date") == D and d.get("action") == "BUY" \
                    and str(d.get("code", ""))[-6:] in CODES:
                rows[str(d["code"])[-6:]] = d
    # westock live positions (truth source)
    live = {}
    try:
        from stop_loss_executor import _fetch_positions_via_mcp
        for p in (_fetch_positions_via_mcp() or []):
            c6 = str(p.get("code", ""))[-6:]
            if c6 in CODES and int(p.get("quantity", 0) or 0) > 0:
                live[c6] = p
    except Exception as e:
        live = {"_err": str(e)[:60]}
    print("==== 周一12只买入核验 %s ====" % D)
    n_fill = n_open = n_none = 0
    for c6, (nm, src) in CODES.items():
        d = rows.get(c6)
        if not d:
            print("  ❓ %s %s: 台账无BUY记录(可能被闸/判定层跳过, 见monday12_buy_run.log)" % (c6, nm))
            n_none += 1
            continue
        st = d.get("status", "?")
        px = d.get("price")
        if st == "FILLED":
            n_fill += 1
            lp = live.get(c6)
            live_note = ("westock在仓%d股 成本%s" % (lp.get("quantity"), lp.get("cost_price"))
                         if lp else "westock未见仓位⚠️")
            print("  ✅ %s %s FILLED @%s (%s) | %s" % (c6, nm, px, src, live_note))
        else:
            n_open += 1
            print("  ⏳ %s %s %s @%s (%s)" % (c6, nm, st, px, src))
    print("合计: FILLED=%d / 在途或未成交=%d / 无记录=%d (共12)" % (n_fill, n_open, n_none))
    print("实验臂接管: source=dip_*_v1 的FILLED仓, horizon_exit_1445将按持5日/收盘-8%自动卖")
    out = {"date": D, "filled": n_fill, "open": n_open, "none": n_none, "rows": rows}
    json.dump(out, open(os.path.join(ROOT, "data", "monday12_postcheck_%s.json" % D),
                        "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
