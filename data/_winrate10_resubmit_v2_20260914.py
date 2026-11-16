# -*- coding: utf-8 -*-
"""胜率Top10 重下v2(20260914): 首批全败于westock服务端'get ckv key failed'(证券主档间歇缺失, 测试实证:
002966@0.05提交成功而603328/601866同刻失败=按股间歇, 非会话/价格/我方bug).
策略: 限价=周五收盘×1.02(保成交), 逐只提交, ckv失败→等30s重试(主档可能刷新), 最多3轮; 每次检查order_resp(修executor静默吞错).
executor闸门已过(干跑记录), 本脚本仅重提交; open_orders已有OPEN行(假OPEN=首提失败留痕), 成交后fill_confirm回填.
**测试单002966@0.05x100(5元,永不成交)待清**: 需order_id, 从westock委托列表取, 见尾注."""
import sys, time, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\workbuddyspace')
from westock_client import WeStockClient

TARGETS = [  # code, name, 周五收盘(20260911)
    ("603328", "依顿电子", 12.77), ("002966", "苏州银行", 9.30),
    ("601328", "交通银行", 7.48), ("603637", "镇海股份", 13.71),
    ("002942", "新农股份", 16.92), ("300214", "日科化学", 10.09),
    ("601077", "渝农商行", 6.70), ("300154", "瑞凌股份", 12.63),
]
AMOUNT = 20000
ROUNDS = 3
GAP = 30

out = {"rounds": [], "final": {}}
pending = {c: (n, px) for c, n, px in TARGETS}
ws = WeStockClient()

for rd in range(1, ROUNDS + 1):
    if not pending:
        break
    print(f"--- 第{rd}轮 ({len(pending)}只) ---", flush=True)
    done_now = []
    for code, (name, px_close) in list(pending.items()):
        limit = round(px_close * 1.02, 2)
        vol = int(AMOUNT / limit // 100) * 100
        if vol <= 0:
            print(f"  {code}: 预算不足100股, 剔除"); del pending[code]; continue
        try:
            r = ws.buy(code, limit, vol)
        except Exception as e:
            print(f"  {code} 异常: {e} → 重建客户端"); ws = WeStockClient(); time.sleep(3)
            continue
        raw = str(r)
        if '失败' in raw or 'ckv' in raw:
            print(f"  {code} {name}: ckv失败({raw[:70]}) — 30s后下轮重试", flush=True)
            time.sleep(GAP)
            continue
        print(f"  {code} {name}: ✅提交成功 限价{limit} x{vol} | {raw[:70]}", flush=True)
        out['final'][code] = {'name': name, 'limit': limit, 'vol': vol, 'resp': r}
        done_now.append(code)
        time.sleep(1.5)
    for c in done_now:
        del pending[c]

out['pending_after'] = {c: v[0] for c, v in pending.items()}
json.dump(out, open(r'D:\zcodesj\data\_winrate10_resubmit_v2_20260914.json', 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, default=str)
print(f"\n最终: 提交成功{len(out['final'])}/8, 未成{list(out['pending_after'].keys()) or '无'}")
print("⚠️ 尾注: 测试单002966@0.05x100待清(需westock委托列表取order_id, 5元无害)")
