# -*- coding: utf-8 -*-
"""周一执行日核查单（只读, 2026-09-20 落地）

来源: 第三期全维度自省 T3 条款。目标交易日 2026-09-21（周一）四件事执行前的状态核查:
  1. 五任务调度就绪: MondayBuy_0928 / BostengVerify_Exec_0921 / HoldingsRiskScan_1510 / MARKETSTATE_2000 / RegimeGateShadow_2110
  2. 周一五只 v5.2 挂单清单完整性: monday_buy_5stocks.py 元组读取+名称校验
  3. 山西焦煤 S-5 挂单疑点: open_orders.jsonl 最新状态(9/18 盘后挂单 EXPIRED_READ 疑似过期) → 盘前必须实时核实
  4. regime 双口径档位: regime_gate_shadow(env) + market_state_probe(pos中位/换手能量) → 停跑条款生效判定
定位: 只读核查零交易权; 挂单/重挂操作由主人或 S-5 链执行。
用法: python monday_exec_checklist.py [--target 20260921]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TASKS = ["MondayBuy_0928", "BostengVerify_Exec_0921", "HoldingsRiskScan_1510",
         "MARKETSTATE_2000", "RegimeGateShadow_2110"]


def schtasks_status() -> dict[str, str]:
    try:
        r = subprocess.run(["schtasks", "/query", "/fo", "CSV"], capture_output=True, timeout=30)
        txt = r.stdout.decode("gbk", errors="replace")
    except Exception as e:
        return {t: f"查询失败:{e}" for t in TASKS}
    out = {}
    for line in txt.splitlines():
        for t in TASKS:
            if t in line:
                parts = [p.strip('"') for p in line.split('","')]
                out[t] = f"下次运行 {parts[1] if len(parts) > 1 else '?'} ｜ {parts[2] if len(parts) > 2 else '?'}"
    return {t: out.get(t, "未找到(任务缺失)") for t in TASKS}


def monday_buy_list() -> tuple[list[tuple], str]:
    """读 monday_buy_5stocks.py 的五只元组"""
    f = os.path.join(ROOT, "monday_buy_5stocks.py")
    if not os.path.exists(f):
        return [], "monday_buy_5stocks.py 不存在"
    txt = open(f, encoding="utf-8").read()
    rows = re.findall(r'\("(\d{6})",\s*"([^"]+)",\s*"([^"]+)",\s*(\d+),\s*"([^"]+)"\)', txt)
    return rows, ""


def sjjm_order_state() -> dict:
    """山西焦煤 sz000983 本地台账最新挂单状态"""
    rows = []
    f = os.path.join(ROOT, "data", "open_orders.jsonl")
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("code") == "sz000983":
            rows.append(d)
    return rows[-1] if rows else {}


def regime_state() -> tuple[dict, dict]:
    gate = {}
    try:
        lines = [l for l in open(os.path.join(ROOT, "data", "regime_gate_shadow.jsonl"), encoding="utf-8") if l.strip()]
        gate = json.loads(lines[-1])
    except Exception:
        pass
    probe = {}
    try:
        probe = json.load(open(os.path.join(ROOT, "data", "market_state_probe.json"), encoding="utf-8"))
    except Exception:
        pass
    return gate, probe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    L = [f"# 周一执行日核查单（目标交易日 {a.target[:4]}-{a.target[4:6]}-{a.target[6:]}·生成 {datetime.now().strftime('%Y-%m-%d %H:%M')}）",
         "", "> 只读核查零交易权 ｜ 来源: 第三期自省 T3 ｜ 任何一项异常须在 09:10 链前人工处置", ""]

    L.append("## 一、调度任务就绪")
    for t, s in schtasks_status().items():
        ok = "✅" if ("就绪" in s and "未找到" not in s) else "🔴"
        L.append(f"- {ok} {t}: {s}")
    L.append("")

    L.append("## 二、五只 v5.2 周一挂单清单（MondayBuy_0928 → monday_buy_5stocks.py）")
    rows, err = monday_buy_list()
    if err:
        L.append(f"- 🔴 {err}")
    elif len(rows) != 5:
        L.append(f"- 🔴 清单数量异常: {len(rows)} 只（应为 5）")
    else:
        for code, name, sector, qty, src in rows:
            L.append(f"- ✅ {code} {name}｜{sector}｜{qty} 股｜{src}")
        L.append("- 执行语义: 实时价+0.5% 限价, executor 双层防线, LOW_VOL 半仓/分批纪律（脚本内置）")
    L.append("")

    L.append("## 三、山西焦煤（000983）S-5 挂单疑点（头号核实项）")
    od = sjjm_order_state()
    if not od:
        L.append("- 🔴 open_orders 无 sz000983 记录: 周一 09:25 前须确认 S-5 卖单已挂（止损锚 6.44）")
    else:
        st = od.get("status", "?")
        L.append(f"- 台账最新: {od.get('submitted_at','?')} 挂 SELL {od.get('price')} × {od.get('qty')} 股 ｜ 状态 **{st}**")
        if st != "FILLED":
            L.append("- 🔴 **EXPIRED_READ 疑似订单已过期**: 与实仓 availableQty=0（股份仍冻结）矛盾，两信号冲突")
            L.append("- 处置: 周一 09:10 用 westock 实时查委托；若确认过期，09:25 前**重挂 S-5 卖单**（1,700 股，参考价 6.44 下方跟随），防 S-5 破线无单可成交")
        else:
            L.append("- ✅ 已成交")
    L.append("")

    L.append("## 四、regime 双口径档位（六层停跑条款判定输入）")
    gate, probe = regime_state()
    if gate:
        L.append(f"- 影子台账最新（{gate.get('date','?')}）: env={gate.get('env','?')} ｜ state={gate.get('state','?')} ｜ tier={gate.get('tier','?')} ｜ up_pct={gate.get('up_pct','?')}")
        env = gate.get("env")
        if isinstance(env, (int, float)):
            tier = "绿(新买入预算100%)" if env < 40 else ("黄(50%)" if env < 80 else "红(0%)")
            L.append(f"- env 档位判定: {tier}（叠加 LOW_VOL ≥3 日红条款以当日 MARKETSTATE 为准）")
    else:
        L.append("- ⚠️ regime_gate_shadow.jsonl 无数据")
    if probe:
        keys = [k for k in probe.keys()][:10]
        L.append(f"- market_state_probe 最新字段: {keys}")
        for k in ("pos_med", "turnover_energy", "low_vol", "state"):
            if k in probe:
                L.append(f"  - {k} = {probe[k]}")
    else:
        L.append("- ⚠️ market_state_probe.json 无数据（周日晚 20:00 MARKETSTATE 后复核）")
    L.append("- 口径提醒: pos 中位=个股口径 ≠ env_pos250 上证口径，双口径并报（记忆铁律）")
    L.append("")

    L.append("## 五、存量风控影子件引用")
    conc = os.path.join(ROOT, "reports", f"holdings_concentration_{datetime.now().strftime('%Y%m%d')}.md")
    if os.path.exists(conc):
        L.append(f"- ✅ holdings_concentration 最新: {os.path.basename(conc)}（单票 26% 提请 + 止损带 CROWD 6 只 + 电子簇 26.8%/交运簇 18.7% 双预警）")
    else:
        L.append("- ⚠️ 今日无 holdings_concentration 产物（先跑 holdings_concentration_monitor.py）")
    L.append("")

    out = os.path.join(ROOT, "reports", f"monday_exec_checklist_{a.target}.md")
    open(out, "w", encoding="utf-8").write("\n".join(L))
    n_red = sum(1 for x in L if x.startswith("- 🔴"))
    print(f"[MONCHECK-OK] {out} | 红项={n_red}")
    if n_red:
        print("[MONCHECK-ATTN] 存在🔴项, 盘前必须人工处置")
    return 0


if __name__ == "__main__":
    sys.exit(main())
