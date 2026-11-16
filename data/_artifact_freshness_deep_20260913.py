# -*- coding: utf-8 -*-
"""任务产物新鲜度深检(一次性): rc检查之外的第二层——任务"跑了"但产物没更新=静默失效.
对关键任务逐个核对其核心产物的 mtime, 与预期频率对比(日更容3天含周末/周度容10天/月度容40天).
BreakoutScout先例: rc=1+日志停4天, 但若只看"任务在跑"会漏掉; 产物mtime是更硬的证据."""
import os, sys, datetime
sys.stdout.reconfigure(encoding='utf-8')

NOW = datetime.datetime.now()
def age_days(p):
    if not os.path.exists(p):
        return None
    return (NOW - datetime.datetime.fromtimestamp(os.path.getmtime(p))).total_seconds() / 86400

# (任务, 产物, 预期频率, 容忍天数)
CHECKS = [
    ('PortraitAgent_2250', r'D:\zcodesj\portrait_agent\reports\portrait_20260911.json', '日更', 3),
    ('PortraitAgent_shadow', r'D:\zcodesj\portrait_agent\models\shadow_state.json', '日更', 3),
    ('PortraitFeed(2250链尾)', r'D:\zcodesj\data\portrait_feed.json', '日更', 3),
    ('BreakoutScout_2250', r'D:\zcodesj\data\breakout_scout_track.jsonl', '日更', 3),
    ('BreakoutScout_log', r'D:\zcodesj\data\breakout_scout_schtask.log', '日更', 3),
    ('LeaderWatch_2245', r'D:\zcodesj\data\leader_watchlist.json', '日更', 4),
    ('PreregWatch_0845', r'D:\zcodesj\data\prereg_watch_*.json', '日更', 4),
    ('GarchShadow_0850', r'D:\zcodesj\data\garch_shadow.csv', '日更', 4),
    ('NewsPit_2130', r'D:\zcodesj\data\news_pit\202609', '日更', 3),
    ('MissedCost_Fri2320', r'D:\zcodesj\reports', '周度', 12),
    ('S2B_Shadow_Fri2340', r'D:\zcodesj\data', '周度', 12),
    ('VIPDOCPROBE_FRI2100', r'D:\zcodesj\.gate_logs', '周度', 12),
    ('GateWeeklyWinrate', r'D:\zcodesj\.gate_logs\weekly_winrate_cron.log', '周度', 12),
    ('WestockReconcile_1640', r'D:\zcodesj\data\westock_reconcile.log', '日更', 4),
    ('FACTORDECAY_0109', r'D:\zcodesj\reports', '月度', 40),
    ('PullbackAlert_0915', r'D:\zcodesj\reports', '日更', 5),
    ('RISKALERT_0915', r'D:\zcodesj\reports', '日更', 5),
]

print(f"=== 产物新鲜度深检 ({NOW:%Y-%m-%d %H:%M}) ===")
alerts = []
for task, path, freq, tol in CHECKS:
    if '*' in path:
        import glob as _g
        fs = _g.glob(path)
        if not fs:
            alerts.append((task, path, None, freq)); print(f"  ❌ {task:28s} glob无匹配: {path}"); continue
        latest = max(fs, key=os.path.getmtime)
        a = age_days(latest); nm = os.path.basename(latest)
    elif os.path.isdir(path):
        # 目录取最新文件
        fs = [os.path.join(path, f) for f in os.listdir(path)]
        fs = [f for f in fs if os.path.isfile(f)]
        if not fs:
            alerts.append((task, path, None, freq)); print(f"  ⚠️ {task:28s} 目录空"); continue
        latest = max(fs, key=os.path.getmtime)
        a = age_days(latest)
        nm = os.path.basename(latest)
    else:
        a = age_days(path)
        nm = os.path.basename(path)
    if a is None:
        alerts.append((task, path, None, freq))
        print(f"  ❌ {task:28s} 产物不存在: {path}")
    elif a > tol:
        alerts.append((task, path, a, freq))
        print(f"  🔴 {task:28s} {nm} 停更 {a:.1f} 天 (预期{freq}/容{tol}天)")
    else:
        print(f"  ✅ {task:28s} {nm} {a:.1f}天前")
print(f"\n异常 {len(alerts)} 项" + (": " + "; ".join(f"{t}({a:.1f}天)" if a else f"{t}(缺失)" for t, _, a, _ in alerts) if alerts else " — 全部新鲜"))
