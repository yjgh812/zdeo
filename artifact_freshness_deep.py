# -*- coding: utf-8 -*-
"""artifact_freshness_deep.py · 任务产物新鲜度深检（周度常驻, 2026-09-13 自省轮产物）.

第二层防线: schtasks 失败探针(health check_schtasks_failures)查 rc; 本工具查产物 mtime——
"任务在跑但产物停更"的静默失效(BreakoutScout 2026-09-10~0913 停摆4天先例: rc=1 但日志/台账停更无人知).
调度: schtasks ArtifactFreshness_SUN2030 周日 20:30 (ASCII cmd 启动器), 异常 toast+日志, 全新鲜静默.

口径: 日更容3-5天(含周末) / 周度容12天 / 月度容40天. 清单变更时直接改本文件 CHECKS.
"""
import os, sys, datetime, subprocess

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "data", "artifact_freshness_log.txt")
NOW = datetime.datetime.now()

CHECKS = [
    ("PortraitAgent_2250", r"D:\zcodesj\portrait_agent\reports\portrait_*.json", 12),  # 2026-10-19修: 原写死portrait_20260911.json永不更新=永久假警报; V10降频后报告节奏不定期, 用周度容12天(glob取最新)
    ("PortraitAgent_shadow", r"D:\zcodesj\portrait_agent\models\shadow_state.json", 3),
    ("PortraitFeed_2250", r"D:\zcodesj\data\portrait_feed.json", 3),
    ("BreakoutScout_2250", r"D:\zcodesj\data\breakout_scout_track.jsonl", 3),
    ("LeaderWatch_2245", r"D:\zcodesj\data\leader_watchlist.json", 4),
    ("PreregWatch_0845", r"D:\zcodesj\data\prereg_watch_*.json", 4),
    ("GarchShadow_0850", r"D:\zcodesj\data\garch_shadow.csv", 4),
    ("NewsPit_2130", r"D:\zcodesj\data\news_pit\*\news_*.jsonl", 3),  # 2026-10-19修: 原写死202609月份目录, 跨月即永久假警报(顶层仅月份目录, 指目录会产物缺失); 改跨月glob取最新
    ("WestockReconcile_1640", r"D:\zcodesj\data\westock_reconcile.log", 4),
    ("GateWeeklyWinrate", r"D:\zcodesj\.gate_logs\weekly_winrate_cron.log", 12),
    # 2026-10-18 扩面(干令): regime断流根因PD-012两任务共产物 + 下午族宿主离线断流 + 夜链fwd_shadow
    ("RegimeGateShadow_2110", r"D:\zcodesj\data\regime_gate_shadow.jsonl", 3),
    ("StopLoss_Morning_0935", r"D:\zcodesj\reports\stop_loss_scan_*.md", 3),
    ("StopLoss_Schtask_1430", r"D:\zcodesj\data\stop_loss_1430_schtask.log", 3),
    ("SealSnapshot_1515", r"D:\zcodesj\data\seal_snapshot_schtask.log", 3),
    ("NewsSentiment_1535", r"D:\zcodesj\data\news_sentiment_shadow_ledger.jsonl", 3),
    ("FwdShadow_chain", r"D:\zcodesj\data\main_combo_fwd_shadow_state_latest.json", 3),
]


def notify(title, content, level="error"):
    try:
        subprocess.run([sys.executable, os.path.join(HERE, "notify.py"), "send",
                        "--title", title, "--content", content, "--level", level], timeout=60)
    except Exception as e:
        print(f"[freshness] notify 发送失败: {e}")


def age_days(p):
    if not os.path.exists(p):
        return None
    return (NOW - datetime.datetime.fromtimestamp(os.path.getmtime(p))).total_seconds() / 86400


def latest_for(path):
    """目录取最新文件; glob 取最新匹配"""
    if "*" in path:
        import glob as _g
        fs = _g.glob(path)
        return max(fs, key=os.path.getmtime) if fs else None
    if os.path.isdir(path):
        fs = [os.path.join(path, f) for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        return max(fs, key=os.path.getmtime) if fs else None
    return path if os.path.exists(path) else None


def main() -> int:
    lines = [f"=== 产物新鲜度深检 {NOW:%Y-%m-%d %H:%M} ==="]
    alerts = []
    for task, path, tol in CHECKS:
        latest = latest_for(path)
        if latest is None:
            alerts.append(f"{task}(产物缺失: {path})")
            lines.append(f"  X {task:26s} 产物不存在: {path}")
            continue
        a = age_days(latest)
        nm = os.path.basename(latest)
        if a is not None and a > tol:
            alerts.append(f"{task}({nm} 停更{a:.1f}天)")
            lines.append(f"  ! {task:26s} {nm} 停更 {a:.1f} 天 (容{tol}天)")
        else:
            lines.append(f"  . {task:26s} {nm} {a:.1f}天前")
    verdict = f"异常 {len(alerts)} 项" + (": " + "; ".join(alerts) if alerts else " — 全部新鲜")
    lines.append(verdict)
    out = "\n".join(lines)
    print(out)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(out + "\n\n")
    except Exception:
        pass
    if alerts:
        notify("产物新鲜度深检异常", verdict[:500], "error")
    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
