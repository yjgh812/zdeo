# -*- coding: utf-8 -*-
import sqlite3, sys
from collections import defaultdict
import numpy as np
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    # westock positions (truth source), trend dims from qfq daily
    sys.path.insert(0, r"D:\zcodesj")
    from stop_loss_executor import _fetch_positions_via_mcp
    pos = _fetch_positions_via_mcp() or []
    held = []
    for p in pos:
        try:
            q = int(p.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            continue
        if q > 0:
            held.append(p)
    if not held:
        print("no positions")
        return
    codes = sorted({str(p["code"])[-6:] for p in held})
    names = {str(p["code"])[-6:]: p.get("name", "?") for p in held}
    pnls = {}
    for p in held:
        try:
            pnls[str(p["code"])[-6:]] = float(p.get("float_pnl_pct") or 0)
        except (TypeError, ValueError):
            pnls[str(p["code"])[-6:]] = 0.0
    con = sqlite3.connect(
        "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    qmarks = ",".join("?" * len(codes))
    rows = con.execute(
        "select code,date,close,volume from stock_daily_meta_qfq "
        "where date>=20260601 and substr(code,-6) in (%s) order by code,date" % qmarks,
        codes).fetchall()
    con.close()
    g = defaultdict(list)
    for code, d, c, v in rows:
        g[str(code)[-6:]].append((int(d), float(c or 0), float(v or 0)))
    rows_out = []
    for c6 in codes:
        ser = g.get(c6)
        if not ser or len(ser) < 25:
            rows_out.append((c6, names.get(c6, "?"), pnls.get(c6, 0.0), None, None, None))
            continue
        arr = ser[-25:]
        closes = [x[1] for x in arr]
        px = closes[-1]
        ma20 = float(np.mean(closes[-21:-1]))
        dev = px / ma20 - 1.0 if ma20 > 0 else 0.0
        c21 = closes[-21] if len(closes) >= 21 else 0
        r20 = closes[-2] / c21 - 1.0 if c21 > 0 else 0.0
        rows_out.append((c6, names.get(c6, "?"), pnls.get(c6, 0.0), round(dev * 100, 1),
                         round(r20 * 100, 1), round(px, 2)))
    bad = [r for r in rows_out if r[3] is not None and r[3] < -5.0]
    hot = [r for r in rows_out if r[3] is not None and r[3] > 10.0]
    ok = [r for r in rows_out if r[3] is not None and -5.0 <= r[3] <= 10.0]
    nodata = [r for r in rows_out if r[3] is None]
    bad.sort(key=lambda x: x[3])
    hot.sort(key=lambda x: -x[3])
    lines = ["==== 持仓趋势体检(周五收盘口径, 只读参考) ====",
             "持仓%d只 | 趋势走坏参考线=收盘低于20日线5%%以上" % len(rows_out),
             "",
             "【趋势走坏参考】%d只 (收盘比20日线低5%%以上, 卖出决策权在主人)" % len(bad)]
    for c6, nm, pnl, dev, r20, px in bad:
        lines.append("  %s %s 偏离MA20=%+.1f%% 盈亏=%+.1f%% 价%s" % (nm, c6, dev, pnl, px))
    lines.append("")
    lines.append("【过热参考】%d只 (比20日线高10%%以上, 冲高减仓可关注)" % len(hot))
    for c6, nm, pnl, dev, r20, px in hot[:15]:
        lines.append("  %s %s 偏离MA20=%+.1f%% 盈亏=%+.1f%% 价%s" % (nm, c6, dev, pnl, px))
    if len(hot) > 15:
        lines.append("  (其余%d只详见json)" % (len(hot) - 15))
    lines.append("")
    lines.append("【健康带】%d只 (偏离-5%%~+10%%之间)" % len(ok))
    lines.append("【无行情数据】%d只: %s" % (len(nodata), ", ".join(r[1] for r in nodata[:10]) or "无"))
    import json as _json
    import os
    _json.dump({"n": len(rows_out), "bad": bad, "hot": hot, "ok_n": len(ok),
                "nodata": [r[1] for r in nodata]},
               open(os.path.join(r"D:\zcodesj\data", "pos_health_latest.json"), "w",
                    encoding="utf-8"), ensure_ascii=False, indent=1)
    txt = "\n".join(lines)
    open(os.path.join(r"D:\zcodesj\data", "pos_health_latest.txt"), "w",
         encoding="utf-8").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
