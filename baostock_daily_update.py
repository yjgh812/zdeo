"""baostock 备源日线增量更新（schtasks: TulongBaostockDaily2040, 每日 20:40）。

- 幂等：daily 表主键 code+date，INSERT OR REPLACE 可重跑
- end=今天（argv[1] 可覆盖，供测试）；周末直接退出；本地已到 end 当日则快速跳过
- 盘中不产当日K，20:40 时数据源已就绪；节假日周一会空跑一轮（可接受）
- 失败次日增量自动重补；崩溃以非零退出码暴露给 schtasks Last Result
用法：python baostock_daily_update.py [YYYY-MM-DD]
"""
import sys
import sqlite3
import logging
from datetime import date
from pathlib import Path

sys.path.insert(0, r"D:/workbuddyspace/tools/data_clients")
from baostock_client import BaoStockClient  # noqa: E402

DB_PATH = "D:/workbuddyspace/data/baostock/baostock_daily.db"
DEFAULT_START = "2026-09-01"
LOG_DIR = Path("D:/zcodesj/logs")


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # 看门狗：baostock 客户端 socket 无超时，服务端抖动时会永久挂起；
    # 2 小时强杀（退出码3）让 schtasks 暴露失败，次日增量自动重补。
    import os
    import threading

    def _watchdog():
        logging.getLogger().error("WATCHDOG fired after 7200s, hard exit")
        os._exit(3)

    t = threading.Timer(7200, _watchdog)
    t.daemon = True
    t.start()
    # 显式挂文件日志通道（baostock_client 导入时已 basicConfig 抢注 root handler）
    _fh = logging.FileHandler(LOG_DIR / "baostock_daily_update.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(_fh)
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().info("WATCHDOG armed: 7200s")
    end = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

    if date.today().weekday() >= 5:
        logging.info("SKIP weekend %s", date.today().isoformat())
        return 0

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    max_date = con.execute("SELECT MAX(date) FROM daily").fetchone()[0]
    con.close()
    if max_date and max_date >= end:
        logging.info("SKIP already fresh: max_date=%s >= end=%s", max_date, end)
        return 0

    cli = BaoStockClient()
    try:
        cli.login()
        succ, skip, fail = cli.download_universe_history(
            DEFAULT_START, end, DB_PATH, incremental=True, sleep=0.3)
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        max_date = con.execute("SELECT MAX(date) FROM daily").fetchone()[0]
        con.close()
        logging.info("DONE end=%s result=(succ=%s skip=%s fail=%s) max_date=%s",
                     end, succ, skip, fail, max_date)
        if fail:
            logging.warning("fail=%d, next run will backfill via incremental", fail)
        return 0
    except Exception:
        logging.exception("FATAL end=%s", end)
        return 1
    finally:
        try:
            cli.logout()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
