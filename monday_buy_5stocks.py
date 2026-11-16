# -*- coding: utf-8 -*-
"""周一09:28 v5.2终选股挂单买入（主人 2026-09-19 深夜指令; 2026-09-21 主人裁决修复重写）

修复记录（9/21 双事故闭环）:
  1. 09:28 静默空转事故: 旧版 ①source虽为v52_select但当时admit闸门不认(9/21已入PROVEN)
     ②漏--execute(dry-run永不下单) ③DD_GATE防御档无特批即REJECT——三重拦截5/5全拒,
     日志09:28已可见但无人读。新版: 显式--execute + 执行后逐票核验 + REJECT即toast。
  2. 现金对账缺失事故: 计划需求28.4万 vs 可用现金11.3万(覆盖率39.7%), 「半仓/1/3仓」
     从未对现金核算。新版: 执行前读westock可用现金(唯一真值), 按计划金额权重等比缩量
     (股数取整百), 覆盖率<100%在汇总中显式标注。
  3. 名单制废止(主人9/21裁决): source=v52_select为合法全宇宙生产源, 名单外仅WARN对照。

安全设计:
  - DD_GATE防御档不在本脚本硬编码豁免——防御档REJECT时toast告警, 特批权留给主人会话。
  - --dry 测试模式: 走全部闸门判定但不带--execute(永不下单); 测交易脚本必须先--dry。
  - 加仓防护: 当日已持仓标的再买由executor加仓闸拦截(9/21实测兜底有效), 脚本不绕。
  - 持仓/现金以westock实时为唯一真值; 每票逐笔单独执行。
"""
from __future__ import annotations
import json, os, subprocess, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
sys.path.insert(0, r"D:\workbuddyspace")  # westock_client.py 所在地

# 周度买清单=显式交付物: 每周由周度选股流程生成 data/weekly_buy_list.json 覆盖。
# 脚本不再硬编码清单(9/21隐患修复: 硬编码已执行清单+每周触发=旧票复买风险)。
WEEKLY_LIST = os.path.join(ROOT, "data", "weekly_buy_list.json")


def load_weekly_list() -> tuple[list, str]:
    """返回(清单, 状态)。清单过期/缺失/为空 → 状态非ok, 全SKIP+toast。"""
    import datetime
    try:
        d = json.load(open(WEEKLY_LIST, encoding="utf-8"))
    except Exception:
        return [], "清单文件缺失"
    cur = f"{datetime.date.today().isocalendar()[0]}-W{datetime.date.today().isocalendar()[1]:02d}"
    if d.get("week") != cur:
        return [], f"清单周标签过期(文件={d.get('week')}, 本周={cur})"
    stocks = [(s["code"], s["name"], s["sector"], int(s["plan_vol"]), s.get("source", "v52_select"))
              for s in d.get("stocks", []) if s.get("code") and s.get("plan_vol")]
    if not stocks:
        return [], "清单为空(本周无计划买入或未生成)"
    return stocks, "ok"

LOG = os.path.join(ROOT, "data", "monday_buy.log")


def get_realtime_price(code: str) -> float | None:
    """实时价(腾讯源)。返回None=无法取价则跳过该票。"""
    import urllib.request
    try:
        pre = "sh" if code.startswith(("6", "9")) else "sz"
        url = f"http://qt.gtimg.cn/q={pre}{code}"
        req = urllib.request.Request(url)
        req.add_header("Referer", "http://gtimg.cn")
        data = urllib.request.urlopen(req, timeout=10).read().decode("gbk", errors="replace")
        f = data.split("=")[1].strip('"\n').split("~")
        if len(f) > 3 and float(f[3]) > 0:
            return float(f[3])
    except Exception:
        pass
    return None


def get_available_cash() -> float | None:
    """可用现金唯一真值=westock实时(ws.portfolio())。失败返回None(不缩量+告警)。"""
    try:
        from westock_client import WeStockClient
        port = WeStockClient().portfolio()
        cash = port["data"]["portfolio"]["availableCash"]
        return float(cash) if cash else None
    except Exception:
        return None


def scaled_volumes(cash: float | None) -> tuple[dict, dict, float]:
    """现金对账等比缩量: 预算=现金*0.98, 按计划金额占比分配, 股数取整百。
    返回(股数map, 实时价map, 现金/计划覆盖率)。现金None或足额→原计划股数。"""
    prices = {c: get_realtime_price(c) for c, *_ in STOCKS}
    plan_cost = {c: v * (prices[c] or 0) for c, _, _, v, _ in STOCKS}
    total_plan = sum(plan_cost.values())
    vols = {c: v for c, _, _, v, _ in STOCKS}
    if not cash or not total_plan or cash >= total_plan:
        return vols, prices, (cash / total_plan) if (cash and total_plan) else 1.0
    budget = cash * 0.98
    for c, _, _, _, _ in STOCKS:
        px = prices[c]
        vols[c] = int(budget * plan_cost[c] / total_plan / px // 100 * 100) if px else 0
    return vols, prices, cash / total_plan


def run_buy(code, name, sector, volume, source, limit_price, execute: bool) -> dict:
    """逐笔走executor, 解析JSON结果(executor stdout 末尾为纯JSON)。"""
    cmd = [sys.executable, os.path.join(ROOT, "trade_executor.py"), "buy",
           "--code", code, "--name", name, "--price", str(limit_price),
           "--volume", str(volume), "--source", source, "--sector", sector]
    if execute:
        cmd.append("--execute")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                       cwd=ROOT, encoding="utf-8", errors="replace")
    out = (r.stdout or "")
    try:
        j, _ = json.JSONDecoder().raw_decode(out[out.find("{"):])
    except Exception:
        j = {"executed": False, "gate_verdict": "PARSE_FAIL",
             "gate_rejects": [(out.strip() or r.stderr or "executor无输出")[-160:]]}
    return j


def toast(title: str, content: str) -> None:
    try:
        from notify import send_toast
        send_toast(title, content)
    except Exception:
        pass


def main() -> None:
    execute = "--dry" not in sys.argv
    tag = "DRY" if not execute else "EXECUTE"
    lines = [f"===== MondayBuy[{tag}] {time.strftime('%Y-%m-%d %H:%M:%S')} ====="]
    STOCKS, list_status = load_weekly_list()
    if list_status != "ok":
        lines.append(f"[SKIP-ALL] 周度清单不可用: {list_status} → 本周不自动买入, 请更新 {WEEKLY_LIST}")
        if execute:
            toast("MondayBuy清单缺失", list_status)
        text = chr(10).join(lines)
        # 2026-09-22: cmd层>>重定向句柄与本进程open("a")并发会Errno13——兜底降级stdout
        # (stdout本身被重定向进同一日志, 信息零丢失)
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(text + chr(10))
        except OSError as e:
            text += f"{chr(10)}[WARN] 日志写入失败({e})——仅stdout可见"
        print(text)
        return
    cash = get_available_cash()
    vols, prices, coverage = scaled_volumes(cash)
    if cash is not None:
        lines.append(f"[现金对账] 可用现金{cash:,.0f} 覆盖率{coverage*100:.0f}%"
                     + ("（足额）" if coverage >= 1.0 else "（等比缩量生效）"))
    else:
        lines.append("[现金对账] westock现金不可得 → 按计划股数执行(风险自知)")
    for code, name, sector, plan_vol, source in STOCKS:
        vol = vols.get(code, 0)
        px = prices.get(code)
        if not px or vol < 100:
            lines.append(f"[SKIP] {code} {name}: "
                         + ("实时价缺失" if not px else f"缩量股数{vol}<100"))
            continue
        limit_price = round(px * 1.005, 2)
        lines.append(f"[BUY] {code} {name} 限价{limit_price} 股数{vol}(计划{plan_vol})")
        j = run_buy(code, name, sector, vol, source, limit_price, execute)
        if j.get("executed"):
            oid = j.get("order_resp", {}).get("data", {}).get("orderId", "?")
            lines.append(f"  → 已挂单: {oid}")
        else:
            rejects = "; ".join(str(x)[:80] for x in (j.get("gate_rejects") or []))
            lines.append(f"  → REJECT: {j.get('gate_verdict')} {rejects}")
            if execute:
                toast("MondayBuy拦截告警", f"{code} {name}: {rejects[:120]}")
        time.sleep(2)
    lines.append("===== 汇总结束 =====")
    text = "\n".join(lines)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError as e:
        print(f"[WARN] 日志写入失败({e})——仅stdout可见")
    print(text)


if __name__ == "__main__":
    main()
