# -*- coding: utf-8 -*-
r"""
update_eastmoney_quant_local.py — 东财量化本地库直跑更新（绕过 MCP 30s 超时）

背景：
1. 2026-09-23 上游重构：旧包 eastmoney-quant-mcp（import 期 mkdir bug）已由
   stock-analysis-mcp 取代（GitHub 源码本地化 D:\zcodesj\mcp_servers\stock-analysis-mcp），
   本脚本 9/24 起改接新包（旧包 npx 缓存已清，ModuleNotFoundError 见链日志）。
2. 历史数据在 D:\zcodesj\eastmoney_quant\{stock,sector}\stock_data.db/sector_data.db，
   新包路径解析默认落 Desktop，须 set_db_paths 显式下传（新包不读旧 EASTMONEY_* 环境变量）。
3. MCP 工具调用 30s 超时，跑不完 update_daily_all → 本脚本直接 asyncio 直跑。

日期口径：新包 _today() 每次调用时求值，当日收盘后运行自然对齐交易日，
无需旧版 _TODAY 补丁（该补丁针对 8/12 陈旧快照场景，已随旧包退役）。
target 参数保留用于链式调用兼容与日志头展示，不再参与写入日期。

用法：python update_eastmoney_quant_local.py [target_date]   默认=vipdoc最新交易日
"""
import asyncio
import struct
import sys


def latest_trading_date():
    """从通达信 vipdoc 日线取最新交易日 YYYYMMDD。"""
    try:
        path = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh600519.day"
        with open(path, "rb") as f:
            f.seek(-32, 2)
            rec = struct.unpack("<IIIIIfII", f.read(32))
        d = rec[0]
        return "%04d-%02d-%02d" % (d // 10000, d // 100 % 100, d % 100)
    except Exception:
        return None


if len(sys.argv) > 1:
    TARGET = sys.argv[1]
else:
    _auto = latest_trading_date()
    TARGET = _auto or "2026-08-14"   # 兜底:最近一次实测交易日
TARGET_ISO = f"{TARGET[:4]}-{TARGET[4:6]}-{TARGET[6:]}" if "-" not in TARGET else TARGET

sys.path.insert(0, r"D:\zcodesj\mcp_servers\stock-analysis-mcp\src")

from stock_analysis_mcp.data import sync                                          # noqa: E402
from stock_analysis_mcp.data.storage import (                                     # noqa: E402
    init_sector_db,
    init_stock_db,
    get_db_paths,
    set_db_paths,
)

# 路径显式下传: D 盘既有库(新包默认落 Desktop, 禁止)
set_db_paths(
    r"D:\zcodesj\eastmoney_quant\stock\stock_data.db",
    r"D:\zcodesj\eastmoney_quant\sector\sector_data.db",
)


async def main():
    print(f"目标日期(日志口径): {TARGET_ISO}")
    print(f"库路径: {get_db_paths()}")
    init_stock_db()  # 建/迁移缺失表（幂等, 旧库原地迁移）
    init_sector_db()
    # 2026-09-25 修复: 显式禁用包内"按今天是否交易日"跳过——本脚本由数据链显式传目标日,
    # 凌晨补跑昨日时包内陈旧日历会把交易日误判为非交易日→skipped早退→result缺stock_log键→KeyError。
    # 日志键一律 .get 容错, skipped/error 形状不再炸管道(上游包日历陈旧问题另行记录)。
    result = await sync.update_daily_all(include_sector_members=True, skip_non_trading_day=False)
    print("\n==== 股票更新 ====")
    for line in (result.get("stock_log") or []):
        print(" ", line)
    print("==== 板块更新 ====")
    for line in (result.get("sector_log") or []):
        print(" ", line)
    if result.get("status") == "skipped":
        print(" [skip]", {k: v for k, v in result.items() if k != "paths"})
    if result.get("error"):
        print(" [error]", result["error"])
    if result.get("warnings"):
        for w in result["warnings"]:
            print(" [warn]", w)


if __name__ == "__main__":
    asyncio.run(main())
