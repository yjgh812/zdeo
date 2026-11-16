# -*- coding: utf-8 -*-
"""sentinel_1430_check.py — 14:30 止损链漏跑哨兵（2026-09-09 事件驱动，主人『继续』授权加固）。

背景：9/9 14:30 止损链当日漏派发（lastRunAt 停前一日），8:00 链兜底核对"昨日任务"存在
当日漏跑盲区（最快次日 08:00 才发现且届时直跑撞时段硬闸），导致熙菱信息 300588 盘中破
-8% 全天无人执行。本哨兵 14:45 检查当日 14:30 链第一步产物（reports/stop_loss_scan_YYYYMMDD.md，
该报告无论是否触发止损都会生成），缺失即 notify error 告警——当日发现，不隔夜。

语义：只读+告警，零交易零自愈直跑（直跑属 8:00 链兜底条款，本哨兵不复制）。
用法：python sentinel_1430_check.py [--date YYYYMMDD]
退出码：0=报告在/周末跳过；2=报告缺失或陈旧（已告警）；3=哨兵自身异常（已告警 fail-closed）。

2026-09-10 加固（当日实锤假 OK）：原判定仅"文件存在且非空"→ 9/10 早间 03:23 的手动
dry-run 产物让 14:57 哨兵在 14:30 链实际被跳过（ZCode 应用未运行）的情况下误报 OK。
现改为**新鲜度判定**：文件存在且 mtime ≥ 当日 AFTERNOON_HOUR(13:00)，即 13:30/14:30
任一盘中链当日跑过才算在案；陈旧产物按"漏跑"告警并附 mtime 证据。
（节假日无盘中链 → 会误报，属已知边界；周末已由 weekday 判定跳过。）
"""
import os, sys, json, datetime
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

AFTERNOON_HOUR = 13  # 盘中链最早 13:30 起跑；13:00 前产物一律视为非当日盘中链证据


def freshness(path: str, today: str) -> tuple:
    """返回 (ok, detail)。ok=文件存在非空 且 mtime 落在 today 的 13:00 之后。"""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False, '缺失'
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(path))
    if mt.strftime('%Y%m%d') != today or mt.hour < AFTERNOON_HOUR:
        return False, f'陈旧(mtime={mt.strftime("%Y-%m-%d %H:%M")}, 非当日{AFTERNOON_HOUR}:00后盘中链产物)'
    return True, f'在案(mtime={mt.strftime("%Y-%m-%d %H:%M")})'


def main():
    ap_date = None
    if '--date' in sys.argv:
        ap_date = sys.argv[sys.argv.index('--date') + 1]
    today = ap_date or datetime.date.today().strftime('%Y%m%d')
    wd = datetime.date(int(today[:4]), int(today[4:6]), int(today[6:8])).weekday()
    if wd >= 5:
        print(f'[sentinel] {today} 为周末，跳过')
        return 0
    try:
        from trade_day_guard import is_trading_day
        _td = is_trading_day(today)
        if _td is False:
            print(f'[sentinel] {today} 为休市日(交易日历判定)，优雅跳过')
            return 0
        # _td is None → 日历不可用, fail-open继续原逻辑(止损哨兵宁误报不漏报)
    except Exception:
        pass  # 守卫自身异常不阻断哨兵
    report = os.path.join(HERE, 'reports', f'stop_loss_scan_{today}.md')
    ok, detail = freshness(report, today)
    if ok:
        print(f'[sentinel] OK: {report} {detail}（当日盘中止损链已跑）')
        return 0
    msg = f'14:30止损链疑似漏跑: reports/stop_loss_scan_{today}.md {detail}(哨兵14:45检出)。' \
          f'处置=人工或待次日8:00兜底直跑stop_loss_executor --execute; 若盘中已有破-8%持仓, ' \
          f'请立即手动直跑(09:30-15:00时段内)'
    print(f'[sentinel] 🔴 {msg}')
    try:
        import subprocess
        subprocess.run([sys.executable, os.path.join(HERE, 'notify.py'), 'send',
                        '--title', '🔴14:30止损链漏跑哨兵告警', '--content', msg,
                        '--level', 'error'], timeout=60)
        print('[sentinel] notify 已发送')
    except Exception as e:
        print(f'[sentinel] notify 发送失败: {e}（告警未送达, fail-loud）')
        return 3
    return 2


if __name__ == '__main__':
    sys.exit(main())
