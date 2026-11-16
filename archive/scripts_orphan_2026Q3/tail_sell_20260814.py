#!/usr/bin/env python3
"""尾盘卖出执行脚本 — 2026-08-14 14:45-14:55 分批卖出"""
import json
import logging
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, r"D:\workbuddyspace")
from westock_client import WeStockClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(r"D:\zcodesj\reports\tail_sell_20260814.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SELL_ORDERS = [
    # (code, name, exchange, quantity, reason)
    ("sz002364", "中恒电气", "SZ", 1300, "价格44元>30元铁律"),
    ("sz000815", "美利云", "SZ", 3200, "亏损-11.64%超止损"),
    ("sh600186", "莲花控股", "SH", 4400, "亏损-8.96%超止损"),
    ("sh600711", "盛屯矿业", "SH", 4100, "趋势待核验(原YTD理由已废弃-铁律)"),
    ("sz300170", "汉得信息", "SZ", 3400, "趋势待核验(原YTD理由已废弃-铁律)"),
    ("sz300711", "广哈通信", "SZ", 3300, "趋势待核验(原YTD理由已废弃-铁律)"),
    ("sz003006", "百亚股份", "SZ", 3600, "趋势待核验(原YTD理由已废弃-铁律)"),
    ("sh600338", "西藏珠峰", "SH", 3600, "趋势待核验(原YTD理由已废弃-铁律)"),
]

def get_current_price(ws, code):
    """获取实时价格"""
    try:
        q = ws.call("data_quote", {"code": code})
        d = q["data"][code]
        return float(d.get("price", 0))
    except Exception as e:
        logger.error(f"获取{code}价格失败: {e}")
        return None

def place_sell(ws, code, price, quantity, name):
    """提交卖出委托"""
    try:
        result = ws.call("portfolio_paper_trade", {
            "code": code,
            "price": price,
            "quantity": quantity,
            "direction": "sell"
        })
        if result.get("ok"):
            data = result.get("data", {})
            order_id = data.get("orderId", "N/A")
            logger.info(f"✅ {name}({code}) 卖出委托成功: {quantity}股@{price}元 orderId={order_id}")
            return True
        else:
            logger.error(f"❌ {name}({code}) 卖出失败: {json.dumps(result.get('error', result), ensure_ascii=False)}")
            return False
    except Exception as e:
        logger.error(f"❌ {name}({code}) 卖出异常: {e}")
        return False

def wait_until(target_hour, target_min, tolerance_s=30):
    """等待到目标时间（时:分）"""
    while True:
        now = datetime.now()
        if now.hour > target_hour or (now.hour == target_hour and now.minute > target_min):
            logger.info(f"已过目标时间 {target_hour:02d}:{target_min:02d}，立即执行")
            return
        if now.hour == target_hour and now.minute == target_min:
            logger.info(f"到达目标时间 {target_hour:02d}:{target_min:02d}，开始执行")
            return
        remaining = (target_hour - now.hour) * 3600 + (target_min - now.minute) * 60 - now.second
        if remaining > 0 and remaining % 60 == 0:
            logger.info(f"距 {target_hour:02d}:{target_min:02d} 还有 {remaining//60} 分钟...")
        time.sleep(5)

def main():
    logger.info("=" * 60)
    logger.info("尾盘卖出执行开始")
    logger.info(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"卖出计划: {len(SELL_ORDERS)} 只")
    for code, name, _, qty, reason in SELL_ORDERS:
        logger.info(f"  - {name}({code}): {qty}股 [{reason}]")

    # 等待到14:45
    wait_until(14, 45)

    ws = WeStockClient()

    successes = 0
    failures = 0

    for i, (code, name, exchange, qty, reason) in enumerate(SELL_ORDERS):
        logger.info(f"\n--- [{i+1}/{len(SELL_ORDERS)}] {name}({code}) ---")

        # 获取实时价格
        price = get_current_price(ws, code)
        if price is None:
            logger.error(f"跳过{name}，无法获取价格")
            failures += 1
            continue

        logger.info(f"当前价格: {price}元, 卖出数量: {qty}股")

        # 用当前价格作为限价卖出（确保能成交，用略低于当前价）
        sell_price = round(price * 0.995, 2)  # 略低0.5%以确保成交
        if sell_price <= 0:
            sell_price = price

        ok = place_sell(ws, code, sell_price, qty, name)
        if ok:
            successes += 1
        else:
            failures += 1

        # 每分钟卖1只，防止同一分钟批量砸盘 ✓
        if i < len(SELL_ORDERS) - 1:
            logger.info(f"等待1分钟后再卖下一只...")
            time.sleep(60)

    logger.info("\n" + "=" * 60)
    logger.info(f"卖出执行完成: 成功{successes}只, 失败{failures}只")
    logger.info(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 卖出后查账户
    logger.info("\n--- 卖出后账户状态 ---")
    try:
        pf = ws.portfolio()
        pos = ws.positions()
        logger.info(f"总资产: ¥{pf['data']['portfolio']['totalAssets']}")
        logger.info(f"可用现金: ¥{pf['data']['portfolio']['availableCash']}")
        logger.info(f"持仓数: {pos['data']['total']}")
    except Exception as e:
        logger.error(f"查账户失败: {e}")

if __name__ == "__main__":
    main()