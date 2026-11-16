#!/usr/bin/env python3
"""C-2 验证（只读研究脚本）：贴现单成交率/延迟的日内时段结构。
数据源：westock portfolio_paper_history（成交真值）；挂单时刻从 orderId 解码（1000156826+MMDDHHMM+序号）。
输出：按方向×挂单时段分桶的成交率与成交延迟分布。"""
import json, sys, os
sys.path.insert(0, r"D:\workbuddyspace")
from westock_client import WeStockClient
from datetime import datetime

c = WeStockClient()
records, offset = [], 0
while True:
    r = c.call("portfolio_paper_history", {"range": "recent", "limit": 50, "offset": offset})
    data = r.get("data") or {}
    batch = data.get("records") or []
    records.extend(batch)
    if not data.get("hasMore") or not batch:
        break
    offset += len(batch)
print(f"total records: {len(records)}")

def parse_order_time(order_id: str, filled_at: str) -> str | None:
    # orderId: "1000156826" + MMDDHHMM + suffix
    m = order_id[10:18]
    if len(m) != 8 or not m.isdigit():
        return None
    year = filled_at[:4]
    try:
        return datetime.strptime(year + m, "%Y%m%d%H%M")
    except ValueError:
        return None

rows = []
for r in records:
    ot = parse_order_time(r["orderId"], r["filledAt"])
    ft = datetime.strptime(r["filledAt"], "%Y-%m-%d %H:%M:%S")
    rows.append({
        "dir": r["direction"], "code": r["code"], "name": r["name"],
        "status": r["status"], "order_t": ot, "fill_t": ft,
        "price": float(r["price"]),
    })

def bucket(t):
    hm = t.hour * 60 + t.minute
    if hm < 9*60+30:   return "pre_open/竞价"
    if hm < 10*60:     return "09:30-10:00"
    if hm < 11*60:     return "10:00-11:00"
    if hm < 11*60+30:  return "11:00-11:30"
    if hm < 13*60+30:  return "13:00-13:30"
    if hm < 14*60:     return "13:30-14:00"
    if hm < 14*60+57:  return "14:00-14:57"
    return "14:57-15:00"

filled_status = ("已成交",)
def is_unfilled(s):  # 系统撤单=收盘清；未成交=状态即未成交
    return s in ("系统撤单", "未成交")

for d in ("buy", "sell"):
    sub = [r for r in rows if r["dir"] == d and r["order_t"]]
    print(f"\n===== {d.upper()} n={len(sub)} =====")
    print(f"{'时段':<14}{'n':>4}{'成交':>4}{'未成交':>5}{'主动撤':>5}{'成交率%':>7}{'延迟min中位':>10}{'延迟min均值':>10}")
    from statistics import median
    for b in ["pre_open/竞价","09:30-10:00","10:00-11:00","11:00-11:30","13:00-13:30","13:30-14:00","14:00-14:57","14:57-15:00"]:
        g = [r for r in sub if bucket(r["order_t"]) == b]
        if not g: continue
        nf = sum(1 for r in g if r["status"] in filled_status)
        nu = sum(1 for r in g if is_unfilled(r["status"]))
        nc = len(g) - nf - nu
        lats = [(r["fill_t"] - r["order_t"]).total_seconds()/60 for r in g if r["status"] in filled_status]
        med = f"{median(lats):.1f}" if lats else "-"
        avg = f"{sum(lats)/len(lats):.1f}" if lats else "-"
        print(f"{b:<14}{len(g):>4}{nf:>4}{nu:>5}{nc:>5}{nf/len(g)*100:>7.1f}{med:>10}{avg:>10}")

# 贴现纪律期(8/24起)买单单独看 + 按价格分层(低价股tick惩罚假设的间接检验)
print("\n===== BUY 8/24后 按价格分层 =====")
sub = [r for r in rows if r["dir"]=="buy" and r["order_t"] and r["order_t"] >= datetime(2026,8,24)]
for lo,hi,lab in [(0,6,"<6元"),(6,12,"6-12元"),(12,20,"12-20元"),(20,99,">=20元")]:
    g=[r for r in sub if lo<=r["price"]<hi]
    if not g: continue
    nf=sum(1 for r in g if r["status"] in filled_status); nu=sum(1 for r in g if is_unfilled(r["status"]))
    lats=[(r["fill_t"]-r["order_t"]).total_seconds()/60 for r in g if r["status"] in filled_status]
    med=f"{median(lats):.1f}" if lats else "-"
    print(f"{lab:<8} n={len(g):>3} 成交{nf:>3} 未成交{nu:>3} 成交率{nf/len(g)*100:>5.1f}% 延迟中位{med:>6}min")

# 保存原始解码数据供报告引用
with open(r"D:\zcodesj\data\_tmp_fill_timing_rows_20260830.json","w",encoding="utf-8") as f:
    json.dump([{**r,"order_t":r["order_t"].strftime("%Y-%m-%d %H:%M"),"fill_t":r["fill_t"].strftime("%Y-%m-%d %H:%M")} for r in rows if r["order_t"]], f, ensure_ascii=False, indent=1)
print("\nsaved data/_tmp_fill_timing_rows_20260830.json")
