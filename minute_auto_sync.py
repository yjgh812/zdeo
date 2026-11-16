#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""minute_auto_sync.py - minute DB auto-sync scheduler (schtasks evening windows).

Trigger layer only: freshness check -> compute since -> subprocess call to the
existing owner minute_aggregate_backfill.py -> independent DB recheck.
Skip when vipdoc not newer than DB (repeat runs cost seconds, never a failure).
Success = DB four freqs MAX(date) >= vipdoc latest (data truth, not child exit
code - backfill exit 2 includes known delisted-stock noise).
Failure channel: local toast + notifications.log only (no external push, 9/8 ruling).
Schedule: MinuteAutoSync_Evening = daily 17:30/19:30/21:30 (2h apart, no overlap).
Usage: python minute_auto_sync.py [--dry-run]
"""
import datetime
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.append(ROOT)

VIPDOC_REF = ("000001", "600519")
FREQS = ["1m", "5m", "30m", "60m"]
TARGET_DB = r"D:\workbuddyspace\factor_engine\data\tulong_phase1_minute.db"
BACKFILL = os.path.join(ROOT, "minute_aggregate_backfill.py")
OWN_LOG = r"D:\zcodesj\reports\minute_auto_sync.log"
NTF_LOG = r"D:\zcodesj\data\notifications.log"
BACKFILL_TIMEOUT_SEC = 5400


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(OWN_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def toast(msg):
    """local toast only (9/8 ruling: no external push)."""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "New-BurntToastNotification -Text '%s'" % msg],
            capture_output=True, timeout=15)
    except Exception:
        pass


def vipdoc_latest_date():
    from tdx_reader import read_min1, read_min5
    latest = 0
    for code in VIPDOC_REF:
        for fn in (read_min1, read_min5):
            try:
                rows = fn(code=code)
            except Exception as e:
                raise RuntimeError("vipdoc ref %s read failed: %r" % (code, e))
            if rows:
                latest = max(latest, max(int(r["date"]) for r in rows))
    if latest == 0:
        raise RuntimeError("vipdoc ref stocks empty, cannot judge freshness")
    return latest


def db_freq_latest(conn):
    out = {}
    for f in FREQS:
        row = conn.execute(
            "SELECT MAX(date) FROM stock_minute WHERE freq = ?", (f,)).fetchone()
        out[f] = int(row[0]) if row and row[0] is not None else 0
    return out


def next_day(d):
    dt = datetime.date(d // 10000, d // 100 % 100, d % 100) + datetime.timedelta(days=1)
    return dt.year * 10000 + dt.month * 100 + dt.day


def notify_fail(reason):
    try:
        with open(NTF_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] minute_auto_sync FAIL: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), reason))
    except OSError:
        pass
    toast("MinuteAutoSync FAIL: minute db did not catch up, see reports/minute_auto_sync.log")
    log("FAIL: %s" % reason)


def main():
    if "--dry-run" in sys.argv:
        dry = True
    else:
        dry = False

    try:
        vip_latest = vipdoc_latest_date()
    except RuntimeError as e:
        notify_fail(str(e))
        return 2

    conn = sqlite3.connect("file:%s?mode=ro" % TARGET_DB, uri=True)
    db = db_freq_latest(conn)
    conn.close()
    db_min = min(db.values())

    log("vipdoc_latest=%d db_latest=%s db_min=%d" % (vip_latest, db, db_min))

    if vip_latest <= db_min:
        log("already fresh, skip (no backfill, not a failure)")
        return 0

    since = next_day(db_min)
    cmd = [sys.executable, BACKFILL, "--since", str(since), "--freqs"] + FREQS
    log("decision: backfill since=%d (vipdoc=%d), cmd: %s" % (since, vip_latest, " ".join(cmd)))
    if dry:
        log("[DRY-RUN] not executing")
        return 0

    try:
        proc = subprocess.run(cmd, timeout=BACKFILL_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        notify_fail("backfill subprocess timeout >%ds since=%d" % (BACKFILL_TIMEOUT_SEC, since))
        return 2

    conn = sqlite3.connect("file:%s?mode=ro" % TARGET_DB, uri=True)
    db_after = db_freq_latest(conn)
    conn.close()
    lag = {f: v for f, v in db_after.items() if v < vip_latest}
    if lag:
        notify_fail("recheck not caught up: vipdoc=%d lagging=%s rc=%d" % (vip_latest, lag, proc.returncode))
        return 2

    log("sync done: db_latest=%s backfill_rc=%d%s" % (
        db_after, proc.returncode, " (known delisted noise)" if proc.returncode else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
