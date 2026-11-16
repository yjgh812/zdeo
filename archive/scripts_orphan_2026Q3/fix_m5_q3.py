import os

p = "/d/zcodesj/m5_impulse_blocker.py"
c = open(p, "r", encoding="utf-8").read()

# Q3 should skip for sell actions
old_q3 = """    # Q3: 在交易作战清单上？
    if plan_codes:
        q3_pass = code6 in plan_codes
        question_results["Q3_on_plan"] = {
            "pass": q3_pass,
            "detail": f"{'在' if q3_pass else '不在'}今日作战清单",
        }
        if not q3_pass:
            reasons.append(f"❌ Q3: {code6} 不在交易作战清单上 → 清单上没有的不买 S-6")"""

new_q3 = """    # Q3: 在交易作战清单上？（仅买入检查）
    if plan_codes and action == "buy":
        q3_pass = code6 in plan_codes
        question_results["Q3_on_plan"] = {
            "pass": q3_pass,
            "detail": f"{'在' if q3_pass else '不在'}今日作战清单",
        }
        if not q3_pass:
            reasons.append(f"❌ Q3: {code6} 不在交易作战清单上 → 清单上没有的不买 S-6")
    elif action == "sell":
        question_results["Q3_on_plan"] = {
            "pass": True,
            "detail": "卖出操作不检查作战清单",
        }"""

if old_q3 in c:
    c = c.replace(old_q3, new_q3)
    open(p, "w", encoding="utf-8").write(c)
    print("OK: Q3 now only blocks BUY, allows SELL")
else:
    print("SKIP: old Q3 pattern not found")
    idx = c.find("Q3")
    if idx >= 0:
        print(c[idx:idx+300])
