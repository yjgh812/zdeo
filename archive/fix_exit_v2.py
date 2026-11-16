import os

p = "D:/zcodesj/stop_loss_executor.py"
c = open(p, "r", encoding="utf-8").read()

# 1. Add time-based stop constants after STOP_LOSS_THRESHOLD
old_const = "STOP_LOSS_THRESHOLD = -0.08        # -8% 止损线"
new_const = old_const + '\nTIME_BASED_STOP_THRESHOLD = -0.05   # -5% 时间止损线\nMIN_HOLDING_DAYS = 5                # 最小持有天数(交易日)'
c = c.replace(old_const, new_const)

# 2. Remove references to S-2 tail window (no longer a hard rule)
c = c.replace("仅在标准交易窗口内执行（尊重 S-2 尾盘窗口规则）", "可在任意交易时段执行")
c = c.replace("SELL_WINDOW_START_HOUR = 14", "# SELL_WINDOW removed per owner ruling")

# 3. Update the check_stop_loss_positions function to add time-based check
old_check = """def check_stop_loss_positions(positions: list[dict]) -> list[dict]:
    \"\"\"扫描持仓，找出触发-8%止损的标的。
    
    返回 list[dict] 每项含完整止损信息，按亏损幅度降序排列（最紧急在前）。
    \"\"\"
    triggers = []
    for pos in positions:
        cost = pos.get("cost_price", 0)
        current = pos.get("current_price", 0)
        qty = pos.get("quantity", 0)
        code = pos.get("code", "")
        name = pos.get("name", "")
        
        if not cost or not current or not qty:
            continue
        
        # 计算浮亏百分比
        if cost > 0:
            pnl_pct = (current - cost) / cost
        else:
            pnl_pct = 0.0
        
        pnl_amount = (current - cost) * qty
        
        if pnl_pct <= STOP_LOSS_THRESHOLD:
            triggers.append({
                "code": code,
                "name": name,
                "cost_price": round(cost, 4),
                "current_price": round(current, 4),
                "quantity": qty,
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_amount": round(pnl_amount, 2),
                "stop_loss_exceeded_by": round((abs(pnl_pct) - abs(STOP_LOSS_THRESHOLD)) * 100, 2),
            })
    
    # 按亏损幅度降序
    triggers.sort(key=lambda x: x["pnl_pct"])
    return triggers"""

new_check = """def check_stop_loss_positions(positions: list[dict]) -> list[dict]:
    \"\"\"扫描持仓，找出触发止损的标的（双止损机制）。
    
    【A】硬止损：-8%止损 → 自动触发不可拒绝
    【B】时间止损：持仓>5天 + 亏损>-5% → 自动触发
    
    返回 list[dict] 按紧急度降序排列。
    \"\"\"
    triggers = []
    for pos in positions:
        cost = pos.get("cost_price", 0)
        current = pos.get("current_price", 0)
        qty = pos.get("quantity", 0)
        code = pos.get("code", "")
        name = pos.get("name", "")
        hold_days = pos.get("holding_days", 0)  # 持仓天数
        
        if not cost or not current or not qty:
            continue
        
        if cost > 0:
            pnl_pct = (current - cost) / cost
        else:
            pnl_pct = 0.0
        
        pnl_amount = (current - cost) * qty
        
        # 【A】硬止损 -8%
        if pnl_pct <= STOP_LOSS_THRESHOLD:
            triggers.append({
                "code": code,
                "name": name,
                "cost_price": round(cost, 4),
                "current_price": round(current, 4),
                "quantity": qty,
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_amount": round(pnl_amount, 2),
                "stop_type": "HARD_STOP",
                "reason": f"硬止损: 亏损{round(pnl_pct*100,2)}% ≤ -8%",
                "urgency": "HIGH",
            })
        
        # 【B】时间止损：持仓>5天 + 亏损>-5%
        elif hold_days >= MIN_HOLDING_DAYS and pnl_pct <= TIME_BASED_STOP_THRESHOLD:
            triggers.append({
                "code": code,
                "name": name,
                "cost_price": round(cost, 4),
                "current_price": round(current, 4),
                "quantity": qty,
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_amount": round(pnl_amount, 2),
                "stop_type": "TIME_STOP",
                "reason": f"时间止损: 持{hold_days}天亏损{round(pnl_pct*100,2)}% ≤ -5%",
                "urgency": "MEDIUM",
            })
    
    # 按紧急度+亏损幅度降序
    triggers.sort(key=lambda x: (0 if x.get("urgency") == "HIGH" else 1, x["pnl_pct"]))
    return triggers"""

c = c.replace(old_check, new_check)

# 4. Update generate_sell_instructions to include stop_type
old_gen = """def generate_sell_instructions(triggers: list[dict]) -> list[dict]:
    \"\"\"为止损标的生成卖出指令。
    
    规则：
    - 现价 > 成本价时：卖一半（仍在趋势中，只减仓不止损）
    - 现价 <= 成本价时：全清
    \"\"\"
    instructions = []
    for t in triggers:
        if t["current_price"] > t["cost_price"]:
            # 现价高于成本（虽触发止损但其实是回撤），减半
            sell_qty = t["quantity"] // 2
            if sell_qty < 100:
                sell_qty = t["quantity"]  # 不足1手则全清
            action = "SELL_HALF"
        else:
            sell_qty = t["quantity"]
            action = "SELL_ALL"
        
        instructions.append({
            "code": t["code"],
            "name": t["name"],
            "action": action,
            "sell_quantity": sell_qty,
            "current_price": t["current_price"],
            "cost_price": t["cost_price"],
            "pnl_pct": t["pnl_pct"],
            "pnl_amount": t["pnl_amount"],
            "reason": f"自动止损: 亏损{t['pnl_pct']}% 超-8%线{t['stop_loss_exceeded_by']}pp",
        })
    
    return instructions"""

new_gen = """def generate_sell_instructions(triggers: list[dict]) -> list[dict]:
    \"\"\"为止损标的生成卖出指令。
    
    规则：
    - 硬止损(-8%) → 全清（不可拒绝）
    - 时间止损(>5天+>-5%) → 全清（自动执行）
    - 现价 > 成本价（回撤非亏损）→ 减半
    \"\"\"
    instructions = []
    for t in triggers:
        if t.get("stop_type") == "TIME_STOP":
            # 时间止损：全清
            sell_qty = t["quantity"]
            action = "SELL_ALL"
            reason_text = f"时间止损: 持{5}天+亏损{t['pnl_pct']}%"
        elif t["current_price"] > t["cost_price"]:
            # 现价高于成本（虽触发止损但其实是回撤），减半
            sell_qty = t["quantity"] // 2
            if sell_qty < 100:
                sell_qty = t["quantity"]
            action = "SELL_HALF"
            reason_text = f"硬止损(回撤): 亏损{t['pnl_pct']}%"
        else:
            sell_qty = t["quantity"]
            action = "SELL_ALL"
            reason_text = f"硬止损: 亏损{t['pnl_pct']}%"
        
        instructions.append({
            "code": t["code"],
            "name": t["name"],
            "action": action,
            "sell_quantity": sell_qty,
            "current_price": t["current_price"],
            "cost_price": t["cost_price"],
            "pnl_pct": t["pnl_pct"],
            "pnl_amount": t["pnl_amount"],
            "stop_type": t.get("stop_type", "HARD_STOP"),
            "reason": reason_text,
        })
    
    return instructions"""

c = c.replace(old_gen, new_gen)

# 5. Update output_report to show stop type
c = c.replace("止损自动扫描报告", "双止损扫描报告（硬止损-8% + 时间止损>5天+-5%）")
c = c.replace("| 代码 | 名称 | 成本价 | 现价 | 持仓 | 亏损% | 亏损金额 | 超线pp | 卖出数量 | 操作 |",
              "| 代码 | 名称 | 成本价 | 现价 | 持仓 | 亏损% | 类型 | 卖出数量 | 操作 |")
c = c.replace('|------|------|--------|------|------|-------|----------|--------|----------|------|',
              '|------|------|--------|------|------|-------|------|----------|------|')

c = c.replace(
    'f"| {t['code']} | {t['name']} | {t['cost_price']} | {t['current_price']} "\n                f"| {t['quantity']} | **{t['pnl_pct']}%** | {t['pnl_amount']}元 "\n                f"| {t['stop_loss_exceeded_by']}pp | {inst['sell_quantity']} | {inst['action']} |"',
    'f"| {t['code']} | {t['name']} | {t['cost_price']} | {t['current_price']} "\n                f"| {t['quantity']} | **{t['pnl_pct']}%** | {t.get(\\"stop_type\\",\\"HARD\\")} "\n                f"| {inst['sell_quantity']} | {inst['action']} |"'
)

c = c.replace("（8/13全维度自省实证）", "（双止损: -8%硬止损 + >5天+-5%时间止损）")

# 6. Remove old tail window references in comments
c = c.replace("（8/13全维度自省实证）", "（双止损机制）")

open(p, "w", encoding="utf-8").write(c)
print("方案C 双止损机制已落地")
print("  - 硬止损: -8% 自动执行")
print("  - 时间止损: 持仓>5天 + 亏损>-5% → 自动清仓")
