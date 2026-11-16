# -*- coding: utf-8 -*-
"""胜率Top10 重下(20260914紧急): 首批8单因westock会话部分老化全部静默失败(ckv key failed, executor未检order_resp的bug),
闸门已过(干跑8/10 PASS_WITH_WARN在案), 本脚本仅重新提交+每次检查order_resp+失败重建客户端自愈(记忆先例:重连×14速).
价格: ws.quote实时现价. 台账: open_orders已有OPEN行, 成交后fill_confirm回填trade_records."""
import sys, time, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\workbuddyspace')
from westock_client import WeStockClient

TARGETS = [
    ("603328", "依顿电子"), ("002966", "苏州银行"), ("601328", "交通银行"),
    ("603637", "镇海股份"), ("002966b", "苏州银行b"),  # 占位防重复——实际用下方列表
]
TARGETS = [
    ("603328", "依顿电子"), ("002966", "苏州银行"), ("601328", "交通银行"),
    ("603637", "镇海股份"), ("002942", "新农股份"), ("300214", "日科化学"),
    ("601077", "渝农商行"), ("300154", "瑞凌股份"),
]
AMOUNT = 20000

ws = WeStockClient()
results = []
for code, name in TARGETS:
    ok = False
    for attempt in range(3):
        try:
            q = ws.quote(code)
            # quote 返回结构探测
            price = None
            if isinstance(q, dict):
                data = q.get('data', q)
                if isinstance(data, dict):
                    price = data.get('price') or data.get('current') or data.get('last')
                    if price is None and isinstance(data.get('raw'), str):
                        price = None
            if not price:
                print(f"{code}: quote无价格({str(q)[:80]}), 跳过本次"); break
            price = float(price)
            vol = int(AMOUNT / price // 100) * 100
            if vol <= 0:
                print(f"{code}: 现价{price}超出2万预算(不足100股), 跳过"); break
            r = ws.buy(code, price, vol)
            raw = str(r)
            if '失败' in raw or 'ckv' in raw:
                print(f"  {code} attempt{attempt+1}: 提交失败({raw[:60]}) → 重建客户端重试")
                ws = WeStockClient(); time.sleep(2)
                continue
            print(f"  {code} {name}: ✅提交成功 @{price} x{vol} | {raw[:80]}")
            results.append({'code': code, 'name': name, 'price': price, 'vol': vol, 'resp': r})
            ok = True
            break
        except Exception as e:
            print(f"  {code} attempt{attempt+1} 异常: {e} → 重建客户端")
            ws = WeStockClient(); time.sleep(2)
    if not ok:
        results.append({'code': code, 'name': name, 'error': '3次重试均失败'})
json.dump(results, open(r'D:\zcodesj\data\_winrate10_resubmit_20260914.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=str)
okn = sum(1 for r in results if 'error' not in r)
print(f"\n重下完成: {okn}/{len(TARGETS)} 提交成功; 落盘 data/_winrate10_resubmit_20260914.json")
