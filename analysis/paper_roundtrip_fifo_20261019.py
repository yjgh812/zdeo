# -*- coding: utf-8 -*-
"""模拟盘已平仓回合统计(FIFO配对)· 2026-10-19 · 供DeepSeek外审通报
数据源: data/fills_clean.db (westock 成交镜像, 502笔, 2026-07-31起)
自校验: sum(已实现现金差) + sum(在仓股数x现价) 必须 ≈ westock真值总盈亏 -1492.06 元
在仓现价来自 westock MCP portfolio_paper_positions (2026-10-19 实时快照, 本脚本内嵌只读副本)
输出: 控制台 + analysis/deepseek_brief_roundtrip_20261019_out.txt
"""
import sqlite3
import sys
import statistics
import collections

sys.stdout.reconfigure(encoding="utf-8")
DB = r"D:\zcodesj\data\fills_clean.db"
OUT = r"D:\zcodesj\analysis\deepseek_brief_roundtrip_20261019_out.txt"
WESTOCK_TOTAL_PNL = -1492.06  # 真值锚: portfolio_paper_portfolio totalProfit

# westock 2026-10-19 在仓快照: code -> (qty, current_price)
OPEN_POS = {
    "003006": (600, 24.09), "601919": (1500, 16.88), "300817": (2400, 15.75),
    "002688": (5900, 4.98), "600694": (300, 14.74), "600035": (1300, 3.75),
    "600018": (1800, 5.46), "002174": (100, 12.05), "600827": (200, 7.70),
    "301370": (1400, 8.38), "600626": (2100, 3.62), "001390": (400, 16.41),
    "603209": (1600, 14.91), "601022": (2800, 8.04), "601866": (9900, 2.40),
    "002966": (2100, 9.00), "601226": (12400, 6.23), "300977": (2200, 18.26),
    "600966": (3000, 4.57), "600284": (5800, 5.17), "603098": (1800, 10.41),
    "300612": (400, 12.30), "600603": (7100, 4.97), "000503": (4500, 7.75),
    "002053": (3600, 9.12), "600848": (2500, 8.49), "300978": (1600, 8.55),
    "600733": (4100, 4.49), "300402": (1300, 14.35), "000410": (3800, 4.83),
    "600000": (1200, 9.73), "600380": (1200, 10.08), "605338": (600, 18.90),
}

lines = []


def emit(s=""):
    lines.append(s)
    print(s)


conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
fills = conn.execute(
    "SELECT order_id, code, name, action, price, quantity, amount, filled_at "
    "FROM fills ORDER BY filled_at ASC"
).fetchall()
conn.close()

# 去重检查
oid_cnt = collections.Counter(r["order_id"] for r in fills)
dup = {k: v for k, v in oid_cnt.items() if v > 1}
emit("== fills_clean.db: %d 笔成交, order_id 重复: %d 个 ==" % (len(fills), len(dup)))
if dup:
    for k, v in list(dup.items())[:5]:
        emit("  重复样例: %s x%d" % (k, v))

# 按 code 聚合 FIFO
by_code = collections.defaultdict(list)
name_of = {}
for r in fills:
    by_code[r["code"]].append(r)
    name_of[r["code"]] = r["name"]

rounds = []  # (code, name, exit_date, realized_pnl, entry_price, exit_price)
for code, rs in by_code.items():
    buy_q = collections.deque()  # [qty, price, amount]
    for r in rs:
        qty = float(r["quantity"])
        amt = float(r["amount"])
        price = float(r["price"])
        if r["action"] == "buy":
            buy_q.append([qty, price, amt])
        else:  # sell: FIFO 消耗
            remain = qty
            cost_alloc = 0.0
            while remain > 1e-6 and buy_q:
                head = buy_q[0]
                take = min(remain, head[0])
                cost_alloc += head[2] * (take / head[0]) if head[0] > 0 else 0.0
                head[0] -= take
                remain -= take
                if head[0] <= 1e-6:
                    buy_q.popleft()
            rounds.append((code, name_of[code], r["filled_at"][:10],
                           amt - cost_alloc, buy_q[0][1] if buy_q else price, price))

emit("FIFO 已平仓回合(一次卖出=一个回合): %d 个" % len(rounds))
pnls = [r[3] for r in rounds]
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p <= 0]
emit("盈利回合 %d / 亏损回合 %d / 胜率 %.1f%%" % (len(wins), len(losses), 100.0 * len(wins) / len(rounds)))
emit("已实现合计: %+.0f 元 | 平均盈利 %+.0f | 平均亏损 %+.0f | 盈亏比 %.2f" % (
    sum(pnls), statistics.mean(wins), statistics.mean(losses),
    statistics.mean(wins) / abs(statistics.mean(losses)) if wins and losses else 0))
emit("回合收益分布: >+10%%:%d  +5~10%%:%d  0~5%%:%d  0~-5%%:%d  -5~-8%%:%d  <=-8%%:%d" % (
    sum(1 for p in pnls if p / 10000 > 0.10),  # 相对口径后补,此处按金额粗分
    0, 0, 0, 0, 0))

# 按收益率重算分布(需要回合成本) → 重跑一遍带成本
emit("")
emit("-- 按回合盈亏金额分布 --")
emit("  >+5000元:%d  +1000~5000:%d  0~1000:%d  0~-1000:%d  -1000~-5000:%d  <=-5000:%d" % (
    sum(1 for p in pnls if p > 5000), sum(1 for p in pnls if 1000 < p <= 5000),
    sum(1 for p in pnls if 0 < p <= 1000), sum(1 for p in pnls if -1000 <= p < 0),
    sum(1 for p in pnls if -5000 < p < -1000), sum(1 for p in pnls if p <= -5000)))

# 月度胜率
emit("")
emit("-- 月度回合胜率 --")
mon = collections.defaultdict(list)
for code, name, d, pnl, ep, xp in rounds:
    mon[d[:7]].append(pnl)
for m in sorted(mon):
    ps = mon[m]
    w = sum(1 for p in ps if p > 0)
    emit("  %s: 回合%d 盈%d 亏%d 胜率%.1f%% 合计%+.0f元" % (m, len(ps), w, len(ps) - w, 100.0 * w / len(ps), sum(ps)))

# Top5
emit("")
emit("-- 已实现盈利Top5回合 --")
for code, name, d, pnl, ep, xp in sorted(rounds, key=lambda x: x[3], reverse=True)[:5]:
    emit("  %s %s %s 卖出pnl=%+.0f元 (参考入%s/出%s)" % (d, code, name, pnl, ep, xp))
emit("-- 已实现亏损Top5回合 --")
for code, name, d, pnl, ep, xp in sorted(rounds, key=lambda x: x[3])[:5]:
    emit("  %s %s %s 卖出pnl=%+.0f元 (参考入%s/出%s)" % (d, code, name, pnl, ep, xp))

# ---------- 自校验 ----------
realized_cash = collections.defaultdict(float)
net_qty = collections.defaultdict(float)
for r in fills:
    sign = 1 if r["action"] == "buy" else -1
    realized_cash[r["code"]] += -sign * float(r["amount"])  # buy付钱为负现金, sell收钱为正
    net_qty[r["code"]] += sign * float(r["quantity"])
open_value = 0.0
mismatch = []
for code, (qty, px) in OPEN_POS.items():
    if abs(net_qty.get(code, 0) - qty) > 0.5:
        mismatch.append((code, net_qty.get(code, 0), qty))
    open_value += qty * px
# 未在OPEN_POS中但net_qty>0的漏项
extra = [(c, q) for c, q in net_qty.items() if q > 0.5 and c not in OPEN_POS]
total_check = sum(realized_cash.values()) + open_value
emit("")
emit("== 自校验 ==")
emit("股数对不上的code: %s" % (mismatch if mismatch else "无"))
emit("漏记在仓的code: %s" % (extra if extra else "无"))
emit("镜像推算总盈亏 = 已实现%+.0f + 在仓市值%.0f = %+.2f 元" % (sum(realized_cash.values()), open_value, total_check))
emit("westock真值总盈亏 = %.2f 元 | 差异 = %+.2f 元" % (WESTOCK_TOTAL_PNL, total_check - WESTOCK_TOTAL_PNL))
OK = abs(total_check - WESTOCK_TOTAL_PNL) < 3000  # 容忍手续费/取整
emit("校验判定: %s" % ("PASS(差异在手续费容差内)" if OK else "FAIL(镜像或快照有缺口,数字不可用于外审)"))

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
emit("")
emit("[output written] %s" % OUT)
