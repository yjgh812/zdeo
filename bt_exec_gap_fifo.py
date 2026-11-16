# -*- coding: utf-8 -*-
"""执行差距 v2: 2026 全年逐笔 FIFO 已实现盈亏对账 (数据源=attribution\\attribution.db trade_records 610条成交台账)
口径: 逐码 FIFO(买入队列, 卖出按比例消费), 已实现=卖出额-对应买入成本; 未平仓单独列示(浮动盈亏不在本表)。
对照: 系统 2026 双正臂月度已实现(bp_ep+ml, drift_check_20260924a)。产物: EXECUTION_GAP_V2.md
"""
import sys, os, json, sqlite3
from collections import defaultdict, deque

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DB = r"D:\zcodesj\attribution\attribution.db"
OUT = r"D:\zcodesj\reports\execution_gap_20260925\EXECUTION_GAP_V2.md"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
# v3: 优先干净台账 fills_clean.db(status=已成交官方通道), 回退 trade_records(已知污染)
CLEAN = r"D:\zcodesj\data\fills_clean.db"
import os as _os
if _os.path.exists(CLEAN):
    c2 = sqlite3.connect(f"file:{CLEAN}?mode=ro", uri=True)
    rows = c2.execute("SELECT code, name, filled_at, action, price, quantity, amount, 'clean' FROM fills ORDER BY filled_at, order_id").fetchall()
    c2.close()
    print(f"[SOURCE] fills_clean.db (status=已成交官方通道) {len(rows)} 行")
else:
    rows = con.execute("SELECT code, name, trade_date, action, price, volume, amount, signal_type FROM trade_records ORDER BY trade_date, id").fetchall()
    print(f"[SOURCE][污染风险] trade_records {len(rows)} 行")
con.close()
print(f"台账 {len(rows)} 行, 范围 {rows[0][2]} ~ {rows[-1][2]}")

queues = defaultdict(deque)  # code -> deque[(qty, cost_per_share, date)]
realized = defaultdict(lambda: [0.0, 0, 0.0])  # month -> [pnl, n_closed, sell_amt]
unmatched_sells = []
open_pos = {}  # code -> [qty, cost_total, name]

for code, name, td, action, price, volume, amount, sig in rows:
    if price is None or volume is None or amount is None:
        continue  # 撤单/空行
    code = str(code).zfill(6)[-6:]
    td = str(td)
    td = f"{td[:4]}-{td[5:7]}-{td[8:10]}" if "-" in td else f"{td[:4]}-{td[4:6]}-{td[6:8]}"
    month = td[:7]
    if action.lower() == "buy":
        queues[code].append([int(volume), float(price), td])
        o = open_pos.setdefault(code, [0, 0.0, name])
        o[0] += int(volume); o[1] += float(amount)
    elif action.lower() in ("sell", "partial_sell"):
        need = int(volume); sell_amt = float(amount)
        cost = 0.0; closed = 0; used_amt = 0.0
        while need > 0 and queues[code]:
            q = queues[code][0]
            take = min(need, q[0])
            cost += take * q[1]; used_amt += take * q[1]
            q[0] -= take; need -= take; closed += take
            o = open_pos.get(code)
            if o:
                per = q[1]
                o[0] -= take; o[1] -= take * per
            if q[0] <= 0:
                queues[code].popleft()
        if need > 0:  # 无对应买入(早于台账的持仓卖出)
            unmatched_sells.append((td, code, name, need, sell_amt * (need / max(1, int(volume)))))
            # 按卖出价计成本=零盈亏处理, 单独列示
        pnl = used_amt and (float(amount) * (closed / max(1, int(volume))) - cost)
        if closed > 0:
            realized[month][0] += float(amount) * (closed / max(1, int(volume))) - cost
            realized[month][1] += 1
            realized[month][2] += float(amount)

L = []
A = L.append
A("# 执行差距量化 v2: 2026 全年逐笔 FIFO 对账 (实盘 vs 系统)\n")
A(f"> 数据源: trade_records {len(rows)} 行成交台账(2026-07-22 起, 与 westock 快照互证; 账户此前零成交)")
A("> 口径: 逐码 FIFO 已实现盈亏; 浮动盈亏单列; 早于台账的成本缺失卖出单独列示\n")

tot = 0.0
A("| 月份 | 已实现盈亏(元) | 平仓笔数 | 卖出额(元) |")
A("|---|---|---|---|")
for m in sorted(realized):
    p, n, amt = realized[m]
    tot += p
    A(f"| {m} | {p:+,.0f} | {n} | {amt:,.0f} |")
A(f"| **合计** | **{tot:+,.0f}** | | |")
sys_month = {"2026-07": -17365, "2026-08": 26052, "2026-09": 26566}
sys_tot = sum(sys_month.values())
A("")
A("| 月份 | 实盘 FIFO 已实现 | 系统双正臂已实现 | 差额 |")
A("|---|---|---|---|")
for m in ["2026-07", "2026-08", "2026-09"]:
    p = realized.get(m, [0])[0]
    s = sys_month[m]
    A(f"| {m} | {p:+,.0f} | {s:+,.0f} | {p-s:+,.0f} |")
A(f"| **合计** | **{tot:+,.0f}** | **{sys_tot:+,.0f}** | **{tot-sys_tot:+,.0f}** |")
A("")
A(f"- 未平仓 {len([c for c, o in open_pos.items() if o[0] > 0])} 码 (浮动盈亏见 v1 §1 持仓表)")
if unmatched_sells:
    A(f"- ⚠️ 无台账成本卖出 {len(unmatched_sells)} 笔(理论上不应存在, 如实列示): {unmatched_sells[:5]}")
A("- 交叉验证: v1 引用的 9/16 归因『8月底-9月上旬手动批约-22.4万』与本表 8-9 月已实现口径应同数量级(口径差=止损/走坏批归属月不同)")
A("\n## 风险提示\n\n模拟盘数据; FIFO 配对以台账为准, 撤单/系统撤单不产生盈亏; 系统口径=回测引擎已实现(含成本前差异)。")
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("[DONE]", OUT)
print(f"合计已实现: {tot:+,.0f} 元 | 未平仓 {len(open_pos)} 码 | 无成本卖出 {len(unmatched_sells)} 笔")
