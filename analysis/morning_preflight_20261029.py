# -*- coding: utf-8 -*-
"""morning_preflight_20261029.py — 晨间执行链预检(每交易日09:00, 防哑火)

检查: ①7个执行链任务State=Ready(PowerShell权威判定) ②三批plan在位(.json或.executed)
③qfq库新鲜度(最新日=昨日或今日) ④py_run.cmd存在 ⑤持仓快照存在
输出: data/morning_preflight_latest.log (PASS/FAIL逐项)
"""
import os, sys, json, sqlite3, subprocess, time, datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
lines = []
fails = []

TASKS = ["ManXianBuy_0925", "ShuangYouBuy_0928", "SwingBosBuy_0938",
         "SwingDipBuy_0925", "SwingDipScan_1545", "SwingBosScan_1555",
         "RevivalXushiWatch_0931", "StopLoss_Morning_0935",
         "ExecutionPrecheck_0900", "Sentinel_1430_StopLossChain",
         "StopLoss_Intraday_1430", "RevivalExecAll_0923", "RevivalRest_1022",
         "RevivalRebuy_Now", "RevivalMomentum_0923"]
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-ScheduledTask | Where-Object {$_.TaskName -match '^(ManXian|ShuangYou|SwingBos|SwingDip|Revival|StopLoss|Execution|Sentinel)'} | Select-Object TaskName,State | ConvertTo-Json"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
try:
    tasks = json.loads(r.stdout)
    if isinstance(tasks, dict):
        tasks = [tasks]
    tmap = {t["TaskName"]: t["State"] for t in tasks}
except Exception:
    tmap = {}
    fails.append("powershell任务枚举失败")
for t in TASKS:
    st = tmap.get(t)
    ok = (st in (3, "3", "Ready"))   # PowerShell枚举: 3=Ready
    lines.append(f"[{'PASS' if ok else 'FAIL'}] task {t}: state={st}")
    if not ok:
        fails.append(f"task {t}={st}")
    lines.append(f"[{'PASS' if ok else 'FAIL'}] task {t}: state={st}" + ("" if ok else " (非Ready)"))
    if not ok:
        fails.append(f"task {t}={st}")

for p in (r"data/manxian5_plan_20261029.json", r"data/shuangyou5_plan_20261029.json",
          r"data/swing_bos_plan_latest.json"):
    ok = os.path.exists(p) or os.path.exists(p.replace(".json", ".executed"))
    lines.append(f"[{'PASS' if ok else 'FAIL'}] plan {os.path.basename(p)}")
    if not ok:
        fails.append(f"plan缺失 {p}")

con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
last = int(con.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0])
con.close()
today = int(time.strftime("%Y%m%d"))
yest = int((datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d"))
ok = last >= yest
lines.append(f"[{'PASS' if ok else 'FAIL'}] qfq新鲜度: 最新{last} (要求>={yest})")
if not ok:
    fails.append(f"qfq过期 last={last}")

ok = os.path.exists(os.path.join(ROOT, "py_run.cmd"))
lines.append(f"[{'PASS' if ok else 'FAIL'}] py_run.cmd")
ok2 = os.path.exists(os.path.join(ROOT, "data", "positions_latest.json"))
lines.append(f"[{'PASS' if ok2 else 'FAIL'}] positions_latest.json")
if not ok:
    fails.append("py_run.cmd缺失")
if not ok2:
    fails.append("持仓快照缺失")

verdict = "PASS" if not fails else f"FAIL({len(fails)}项)"
lines.insert(0, f"=== 晨间预检 {time.strftime('%Y-%m-%d %H:%M')} → {verdict} ===")
for f in fails:
    lines.append(f"  ⚠️ {f}")
out = os.path.join(ROOT, "data", "morning_preflight_latest.log")
open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines))
