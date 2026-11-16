# -*- coding: utf-8 -*-
"""屠龙系统心跳 (2026-10-12 净增量项5, 主人「继续」令)
=====================================================================
目的: 会话失联可被外部感知 —— 每小时写 data/heartbeat.json, 内含关键产物
新鲜度快照。外部(另一台机器/人工/看板)只需读此文件即可判断系统是否活着、
哪个环节断了。通知通道单点(仅本地 toast)的最低成本补偿; 不触手机推送禁令。

设计:
- 只读扫描产物清单(mtime/行数/库内最新日期), 幂等覆盖写 heartbeat.json
- stale 判定 = 超过 expected_max_lag_hours; 阈值按产物性质给(节假日自然超龄,
  stale 只做呈现不做告警 —— 告警语义在 health_check_daily, 本件只供心跳)
- 永远 exit 0(心跳失败不该制造告警噪声; 文件不更新本身即信号)

用法: python heartbeat_hourly.py   (schtasks TulongHeartbeat_Hourly 每小时)
输出: data/heartbeat.json
"""
import datetime
import glob
import json
import os
import sqlite3
import sys

BASE = r"D:\zcodesj"
OUT = os.path.join(BASE, "data", "heartbeat.json")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

# 产物清单: (标签, 通配/路径, 期望最大滞后小时)
ARTIFACTS = [
    ("backup_log", "reports/backup_log.txt", 30),
    ("trailing_monitor_log", "reports/trailing_monitor_log.txt", 30),
    ("notifications_log", "data/notifications.log", 72),
    ("vibe_card", "data/vibe_card_latest.json", 30),
    ("timing_state_brief", "data/timing_state_brief_latest.txt", 30),
    ("prereg_registry", "reports/prereg_registry_latest.md", 1000),
    ("data_locations", "DATA_LOCATIONS.md", 1000),
    ("winner5_feed", "data/universe_portrait_winner5_latest.json", 30),
]


def latest_mtime(pattern):
    hits = sorted(glob.glob(os.path.join(BASE, pattern)), key=os.path.getmtime)
    if not hits:
        return None, None
    p = hits[-1]
    return p, os.path.getmtime(p)


def main():
    now = datetime.datetime.now()
    beats = {"last_beat": now.isoformat(timespec="seconds"), "artifacts": {}}
    # qfq 库内最新交易日(比 mtime 更贴近"数据新鲜度"); 含主从对比(2026-10-12 完善:
    # 副本=90+读侧脚本事实标准, 断更在既有监控面不可见——check_db_freshness 只查
    # 主库/sync 失败仅链日志, 心跳小时级兜底)
    try:
        c = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
        beats["qfq_max_date"] = str(c.execute(
            "SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
        c.close()
    except Exception as e:
        beats["qfq_max_date"] = "unavailable(%s)" % type(e).__name__
    try:
        c = sqlite3.connect(
            r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro", uri=True)  # 2026-10-13 切wb唯一真源(SOP一百三十二)
        replica_max = str(c.execute(
            "SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
        c.close()
        beats["qfq_replica_max_date"] = replica_max
        if beats["qfq_max_date"].isdigit() and replica_max.isdigit():
            beats["qfq_replica_lag_days"] = 0 if replica_max >= beats["qfq_max_date"] else -1
        else:
            beats["qfq_replica_lag_days"] = None
    except Exception as e:
        beats["qfq_replica_max_date"] = "unavailable(%s)" % type(e).__name__
        beats["qfq_replica_lag_days"] = None
    for label, pattern, max_lag in ARTIFACTS:
        p, mt = latest_mtime(pattern)
        if p is None:
            beats["artifacts"][label] = {"path": pattern, "status": "missing"}
            continue
        lag_h = round((now.timestamp() - mt) / 3600, 1)
        beats["artifacts"][label] = {
            "path": pattern,
            "mtime": datetime.datetime.fromtimestamp(mt).isoformat(timespec="seconds"),
            "lag_hours": lag_h,
            "stale": lag_h > max_lag,
        }
    beats["stale_count"] = sum(
        1 for v in beats["artifacts"].values() if isinstance(v, dict) and v.get("stale"))
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(beats, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    with open(OUT, encoding="utf-8") as f:
        back = json.load(f)
    assert back.get("last_beat"), "读回核验失败"
    n_art = len(back["artifacts"])
    print("[heartbeat] %s | 产物%d项 stale%d | qfq_max=%s" % (
        back["last_beat"], n_art, back["stale_count"], back.get("qfq_max_date")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
