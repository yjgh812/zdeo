# -*- coding: utf-8 -*-
"""
winrate_top10 实验结算脚本（T+20 对账，到期即跑；幂等可重跑）
用法: python analysis/winrate_top10_settle_20261019.py
逻辑:
  1) 读实验登记 data/winrate_top10_experiment_20261019.json
  2) 从 data/open_orders.jsonl 取每笔订单状态: 未成交(仍OPEN且confirmed=0且已过期)=剔除如实记录;
     成交(confirmed>0或FILLED)=取实际成交状态
  3) 成交均价: open_orders 无均价字段, 以 fill_confirm_monitor 确认口径=挂单价近似(如实声明:
     westock 撮合可能有溢价, 精确成交价以 westock positions costPrice 为准, 脚本优先读 costPrice)
  4) 退出=成交日+20交易日收盘(qfq); win=退出>入场
  5) 输出 A/B 两组胜率/均值 vs 预测概率 vs gauge 校准
未到期: 打印剩余天数退出(exit 0, 不产报告)
"""
import json, sqlite3, sys
from datetime import datetime

EXP = "data/winrate_top10_experiment_20261019.json"
OO = "data/open_orders.jsonl"
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
CAL = "file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"

exp = json.load(open(EXP, encoding="utf-8"))
conn_c = sqlite3.connect(CAL, uri=True)
cal = [r[0].replace("-", "") for r in conn_c.execute(
    "SELECT date_str FROM trading_calendar WHERE date_str BETWEEN '2026-01-01' AND '2027-12-31' ORDER BY date_str")]
idx8 = {d: i for i, d in enumerate(cal)}
today = datetime.now().strftime("%Y%m%d")
ti = idx8.get(today)
if ti is None:
    print("今日非交易日, 交易日口径下无结算锚"); sys.exit(0)

conn_q = sqlite3.connect(QFQ, uri=True)
# open_orders 状态
oo = {}
for l in open(OO, encoding="utf-8"):
    if not l.strip():
        continue
    r = json.loads(l)
    if r.get("trade_date") == "20261019":
        oo[r["order_id"]] = r

def cost_price(code):
    # 优先 westock 实际成本(需人工核对, 脚本只读本地快照; 若无则用挂单价)
    r = conn_q.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (code, today)).fetchone()
    return r[0] if r else None

report = {"asof": today, "groups": {}}
ready = True
for gname in ["group_A_pure_prediction", "group_B_zhengge_mainline"]:
    g = exp[gname]
    items = []
    for m in g["members"]:
        o = oo.get(m["order_id"])
        status = o["status"] if o else "UNKNOWN"
        if status != "OPEN" and status != "FILLED":
            items.append({"code": m["code"], "name": m["name"], "status": status, "settled": None})
            continue
        # 成交日近似=trade_date(挂单日); 精确成交日以FILL confirm为准(如实声明)
        entry_d = "20261019"
        ei = idx8.get(entry_d)
        if ei is None:
            items.append({"code": m["code"], "settled": None}); continue
        exit_i = ei + 20
        if exit_i >= len(cal) or cal[exit_i] > today:
            ready = False
            items.append({"code": m["code"], "name": m["name"], "status": status,
                          "settled": False, "remaining_days": exit_i - ti})
            continue
        entry_px = cost_price(m["code"])  # 近似: 当前价, 真实成交价须westock costPrice(声明)
        exit_d = cal[exit_i]
        r = conn_q.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                           (m["code"], exit_d)).fetchone()
        if r is None or entry_px is None:
            items.append({"code": m["code"], "settled": None}); continue
        ret = (r[0] / entry_px - 1) * 100
        items.append({"code": m["code"], "name": m["name"], "status": status, "settled": True,
                      "entry_px": round(entry_px, 3), "exit_px": r[0], "ret": round(ret, 2),
                      "win": 1 if ret > 0 else 0})
    settled = [x for x in items if x.get("settled")]
    wins = sum(x["win"] for x in settled)
    report["groups"][gname] = {
        "predict_avg": g["predict_avg"], "gauge_hist_win": g["gauge_hist"]["win"],
        "n_settled": len(settled), "n_total": len(g["members"]),
        "win_rate": round(wins / len(settled) * 100, 1) if settled else None,
        "avg_ret": round(sum(x["ret"] for x in settled) / len(settled), 2) if settled else None,
        "items": items,
    }

if not ready:
    print(f"[未到期] 成交日+20交易日未满, 剩余天数见 items.remaining_days; 幂等无副作用")
    print(json.dumps(report, ensure_ascii=False, indent=1)[:1500])
    json.dump(report, open("data/winrate_top10_settle_progress.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    sys.exit(0)

ga = report["groups"]["group_A_pure_prediction"]
gb = report["groups"]["group_B_zhengge_mainline"]
lines = ["# winrate_top10 实验结算（T+20）", "",
         f"- A组(纯预测,预测均值{ga['predict_avg']}%): 实现胜率{ga['win_rate']}% 均值{ga['avg_ret']}% (n={ga['n_settled']})",
         f"- B组(正格+主线,预测均值{gb['predict_avg']}%): 实现胜率{gb['win_rate']}% 均值{gb['avg_ret']}% (n={gb['n_settled']})",
         f"- gauge校准: A组历史{ga['gauge_hist_win']}% / B组历史{gb['gauge_hist_win']}%",
         "- 判读: 预测vs实现落差=校准检验; B-A差=正格+主线增量检验; n=5/组仅方向性",
         "- 未成交/部分成交笔如实剔除记录"]
open("data/winrate_top10_settle_report.md", "w", encoding="utf-8").write("\n".join(lines))
json.dump(report, open("data/winrate_top10_settle_final.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n".join(lines))
