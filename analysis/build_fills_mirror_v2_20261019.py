# -*- coding: utf-8 -*-
"""B1 成交镜像 v2(窗口版 8/25→10/19) + 自校验 + 回合统计 · 2026-10-19 「继续」令
数据源: data/fills_tail_hand_20261019.json (westock MCP 已成交记录, 202条)
弃用说明: fills_afull_20260827.json 混入已撤单(无status字段)且代码带前缀, 判不可用。
自校验: ①33只在仓股数逐一核对 ②负库存=白名单(窗口前建仓基础,16只)逐一核对
统计仅计窗口内完整回合(买卖都在窗口内); 总盈亏对拍需全史镜像, 窗口版不做该检查(声明)。
输出: data/fills_mirror_v2.json + analysis/b1_mirror_stats_20261019_out.txt
"""
import json
import sys
import statistics
import collections

sys.stdout.reconfigure(encoding="utf-8")

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
# 窗口(8/25)前已建仓、窗口内只有卖出的基础股数(据8/26前MCP记录核对)
PRE_WINDOW_BASIS = {
    "002936": 8000, "300077": 2500, "601169": 3800, "000990": 1500,
    "300445": 600, "603626": 700, "301487": 900, "600470": 2400,
    "600984": 2400, "603327": 1500, "300128": 3000, "000690": 2400,
    "301656": 700, "601229": 1200, "000983": 1600, "600719": 1500,
}

lines = []


def emit(s=""):
    lines.append(s)
    print(s)


d = json.load(open(r"D:\zcodesj\data\fills_tail_hand_20261019.json", encoding="utf-8"))
fills = []
for r in d["records"]:
    fills.append({
        "code": str(r["code"]), "name": r["name"], "action": r["action"],
        "quantity": float(r["quantity"]), "price": float(r["price"]),
        "amount": float(r["amount"]), "filled_at": r["filled_at"],
    })
fills.sort(key=lambda r: r["filled_at"])
emit("== 窗口镜像: %d 条已成交 (8/25→10/19), 数据源=westock MCP ==" % len(fills))

# ---------- 自校验① 在仓股数 ----------
net_qty = collections.defaultdict(float)
for r in fills:
    sign = 1 if r["action"] == "buy" else -1
    net_qty[r["code"]] += sign * r["quantity"]
mismatch = [(c, net_qty.get(c, 0), q) for c, (q, _) in OPEN_POS.items()
            if abs(net_qty.get(c, 0) - q) > 0.5]
extra = []
for c, q in net_qty.items():
    if abs(q) <= 0.5:
        continue
    if c in OPEN_POS:
        continue
    if c in PRE_WINDOW_BASIS and abs(q + PRE_WINDOW_BASIS[c]) <= 0.5:
        continue
    extra.append((c, round(q)))
emit("自校验① 在仓股数不匹配: %s" % (mismatch if mismatch else "无 ✓"))
emit("自校验①b 净持仓不在33只在仓名单: %s" % (extra if extra else "无 ✓"))

# ---------- FIFO + 自校验② 负库存白名单 ----------
rounds = []
shortfall_rows = []
for code in sorted({r["code"] for r in fills}):
    rs = [r for r in fills if r["code"] == code]
    name = rs[0]["name"]
    buy_q = collections.deque()
    for r in rs:
        if r["action"] == "buy":
            buy_q.append([r["quantity"], r["amount"] / r["quantity"]])
        else:
            remain, cost_alloc, qty_alloc = r["quantity"], 0.0, 0.0
            while remain > 1e-6 and buy_q:
                h = buy_q[0]
                take = min(remain, h[0])
                cost_alloc += h[1] * take
                qty_alloc += take
                h[0] -= take
                remain -= take
                if h[0] <= 1e-6:
                    buy_q.popleft()
            complete = remain <= 0.5
            if not complete:
                base = PRE_WINDOW_BASIS.get(code)
                ok = base is not None and remain <= base + 0.5
                shortfall_rows.append((code, name, r["filled_at"][:10], int(remain), "白名单✓" if ok else "❌异常"))
                if not ok:
                    continue
            avg_cost = cost_alloc / qty_alloc if qty_alloc > 0 else r["price"]
            rounds.append((code, name, r["filled_at"][:10], r["amount"] - cost_alloc,
                           avg_cost, r["price"], qty_alloc, complete))

whitelist_bad = [s for s in shortfall_rows if s[4] != "白名单✓"]
emit("自校验② 窗口前建仓卖出: %d 笔全部命中白名单" % len([s for s in shortfall_rows if s[4] == "白名单✓"]))
emit("自校验②b 白名单外负库存(真缺口): %s" % (whitelist_bad if whitelist_bad else "无 ✓"))
VALID = (not mismatch) and (not extra) and (not whitelist_bad)
emit("窗口镜像有效性判定: %s" % ("PASS——可统计窗口内完整回合" if VALID else "FAIL"))
emit("声明: 全史总盈亏对拍(=-1492.06)需7/22-8/26干净fills, 窗口版不做该检查(afull快照混撤单已弃用)")
emit("")

complete = [r for r in rounds if r[7]]
incomplete = [r for r in rounds if not r[7]]
pnls = [r[3] for r in complete]
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p <= 0]
emit("== 回合统计(窗口内完整回合 %d 个; 窗口前建仓回合 %d 个已剔除) ==" % (len(complete), len(incomplete)))
emit("已实现胜率 %.1f%% (盈利%d/亏损%d)" % (100.0 * len(wins) / len(complete), len(wins), len(losses)))
emit("窗口内已实现合计 %+.0f 元 | 平均盈 %+.0f | 平均亏 %+.0f | 盈亏比 %.2f" % (
    sum(pnls), statistics.mean(wins), statistics.mean(losses),
    statistics.mean(wins) / abs(statistics.mean(losses)) if wins and losses else 0))
emit("回合收益(金额): >+5000:%d  +1000~5000:%d  0~1000:%d  0~-1000:%d  -1000~-5000:%d  <=-5000:%d" % (
    sum(1 for p in pnls if p > 5000), sum(1 for p in pnls if 1000 < p <= 5000),
    sum(1 for p in pnls if 0 < p <= 1000), sum(1 for p in pnls if -1000 <= p < 0),
    sum(1 for p in pnls if -5000 < p < -1000), sum(1 for p in pnls if p <= -5000)))
emit("")
emit("-- 盈利Top10(窗口内完整回合) --")
for r in sorted(complete, key=lambda x: x[3], reverse=True)[:10]:
    emit("  %s %s %s %+.0f元 (出价%s)" % (r[2], r[0], r[1], r[3], r[5]))
emit("-- 亏损Top10(窗口内完整回合) --")
for r in sorted(complete, key=lambda x: x[3])[:10]:
    emit("  %s %s %s %+.0f元 (出价%s)" % (r[2], r[0], r[1], r[3], r[5]))
emit("")
emit("-- 窗口内完整回合全清单 (日期|代码|名称|盈亏元|成本→卖价|收益率%%) --")
for r in sorted(complete, key=lambda x: x[2]):
    ret = (r[5] - r[4]) / r[4] * 100 if r[4] else 0
    emit("  %s|%s|%s|%+.0f|%s→%s|%+.1f%%" % (r[2], r[0], r[1], r[3], round(r[4], 3), r[5], ret))

# ---------- B5 右尾贡献度(窗口口径) ----------
gross_profit = sum(p for p in pnls if p > 0)
if gross_profit > 0 and wins:
    sp = sorted(wins, reverse=True)
    k = max(1, int(round(len(sp) * 0.05)))
    emit("")
    emit("== B5 右尾贡献度(窗口口径, 8/25→10/19) ==")
    emit("总盈利 %+.0f | Top5%%盈利回合(k=%d)占比 %.1f%% | 最大单回合占总盈利 %.1f%%" % (
        gross_profit, k, 100.0 * sum(sp[:k]) / gross_profit, 100.0 * max(wins) / gross_profit))

# ---------- A2 止损滑点估算 ----------
emit("")
emit("== A2 止损滑点首期(估算: 完整回合收益≤-7%%视为止损执行; 名义触发线=加权成本×0.92) ==")
slip_rows = []
for r in complete:
    code, name, dt, pnl, avg_cost, sell_price, qty, _ = r
    ret = (sell_price - avg_cost) / avg_cost
    if ret <= -0.07 and avg_cost > 0:
        slip = (sell_price - avg_cost * 0.92) / (avg_cost * 0.92)
        slip_rows.append((dt, code, name, ret * 100, slip * 100))
slip_rows.sort()
if slip_rows:
    emit("止损类回合 %d 笔 | 相对名义触发线偏离 中位 %+.2f%% 均值 %+.2f%% (负=优于触发线)" % (
        len(slip_rows), statistics.median([s[4] for s in slip_rows]),
        statistics.mean([s[4] for s in slip_rows])))
    for row in slip_rows:
        emit("  %s %s %s 回合收益%.1f%% 偏离%+.2f%%" % row)
else:
    emit("窗口内无完整止损类回合")

with open(r"D:\zcodesj\data\fills_mirror_v2.json", "w", encoding="utf-8") as f:
    json.dump({"built_at": "2026-10-19", "scope": "2026-08-25→10-19", "valid": VALID,
               "n": len(fills), "fills": fills}, f, ensure_ascii=False)
with open(r"D:\zcodesj\analysis\b1_mirror_stats_20261019_out.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
emit("")
emit("[镜像] data/fills_mirror_v2.json  [统计] analysis/b1_mirror_stats_20261019_out.txt")
