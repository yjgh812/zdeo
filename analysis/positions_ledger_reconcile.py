# -*- coding: utf-8 -*-
"""持仓↔执行台账对账(20261025) — 裸奔审计的持仓级补充。

口径: 台账仅计 status=FILLED 的量(OPEN=挂单未成交, CANCELLED=已撤单, 均不计);
status缺失(MISSING)的早期台账行按有效计并单列披露。
判定: 持仓股数 = FILLED净买 + 台账前遗留库存(无法从台账追溯, 量化后标注, 需主人知会来源)。
只读, 零交易权。用法: python analysis/positions_ledger_reconcile.py [--date YYYYMMDD]
"""
import json
import sys
from collections import defaultdict

import numpy as np

LEDGER = r"D:\zcodesj\data\executor_orders.jsonl"


def main():
    positions = {
        "601939": ("建设银行", 7300), "003006": ("百亚股份", 600), "601919": ("XD中远海", 1500),
        "300817": ("双飞集团", 2400), "600035": ("楚天高速", 1300), "600018": ("上港集团", 1800),
        "301370": ("国科恒泰", 1400), "600626": ("申达股份", 2100), "600284": ("浦东建设", 5800),
        "600603": ("广汇物流", 7100), "000503": ("国新健康", 4500), "600000": ("浦发银行", 1200),
        "600380": ("健康元", 1200), "605338": ("巴比食品", 600), "001287": ("中电港", 500),
        "300364": ("中文在线", 500), "301500": ("飞南资源", 500), "002292": ("奥飞娱乐", 27800),
        "600015": ("华夏银行", 500), "600959": ("江苏有线", 500), "002797": ("第一创业", 500),
    }
    lines = open(LEDGER, encoding="utf-8").read().strip().split("\n")
    ledger = defaultdict(lambda: {"buy": 0, "sell": 0, "nB": 0, "nS": 0, "legacy_base": 0})
    missing_status = 0
    for ln in lines:
        d = json.loads(ln)
        code, action, qty = d.get("code"), d.get("action"), int(d.get("qty", 0))
        status = d.get("status")
        if status is None:
            missing_status += 1
        e = ledger[code]
        # 口径: 仅排除CANCELLED; OPEN=已提交回写滞后(持仓可验证), MISSING=早期行, 均按有效计
        if status == "CANCELLED":
            continue
        if action == "BUY":
            e["buy"] += qty; e["nB"] += 1
        elif action == "SELL":
            e["sell"] += qty; e["nS"] += 1

    clean, issues = 0, []
    print(f"{'code':<8}{'name':<9}{'持仓':>7}{'FILLED买':>9}{'FILLED卖':>9}{'隐含遗留':>9}  判定")
    for code, (name, held) in positions.items():
        e = ledger.get(code)
        if not e:
            print(f"{code:<8}{name:<9}{held:>7}{'—':>9}{'—':>9}{held:>9}  ⚠️整仓无台账(主人手动路径, 待知会确认)")
            issues.append((code, name, held, "no_ledger"))
            continue
        legacy = held - (e["buy"] - e["sell"])
        if legacy == 0:
            verdict = "✓对账一致"
            clean += 1
            legacy = 0
        elif legacy > 0:
            verdict = f"含台账前遗留库存{legacy}股(请主人知会来源)"
            issues.append((code, name, legacy, "legacy"))
        else:
            verdict = f"⚠️台账净卖超持仓{-legacy}股(数据缺口)"
            issues.append((code, name, -legacy, "gap"))
        print(f"{code:<8}{name:<9}{held:>7}{e['buy']:>9}{e['sell']:>9}{legacy:>9}  {verdict}")
    print(f"\n对账一致: {clean}/21 | 差异项: {len(issues)}")
    for it in issues:
        print("  ", it)
    print(f"台账status缺失行(早期, 按有效计): {missing_status}")


if __name__ == "__main__":
    main()
