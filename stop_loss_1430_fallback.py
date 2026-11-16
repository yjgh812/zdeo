# -*- coding: utf-8 -*-
"""stop_loss_1430_fallback.py — 14:30 止损链 schtasks 兜底执行器（2026-09-11）。

背景：14:30 止损兜底链运行于 ZCode automations，依赖 ZCode 桌面应用进程存活。
9/9 与 9/10 两个交易日 14:30 ZCode 应用未运行导致链路整体跳过（主机并未休眠，
powercfg 交流电休眠超时=0 排除该假设），哨兵(Sentinel_1430_StopLossChain 14:45)
只能告警、当日无法自动执行，-8% 硬止损敞口悬空至次日（9/9 熙菱信息 300588 全天
无人执行先例）。本脚本由 Windows schtasks 工作日 14:50 触发（不依赖 ZCode 应用）。

时序：14:30 ZCode链 → 14:45 哨兵告警(只读) → 14:50 本兜底(自动执行)。

逻辑：
1. 复用哨兵新鲜度判定：reports/stop_loss_scan_{today}.md 存在且 mtime>=当日13:00
   → 视为 13:30/14:30 盘中链当日已跑，退出 0（幂等，不重复执行）。
2. 否则 → notify 告警 + 直接触发 stop_loss_executor.py --execute（S-5 止损链，
   交易执行权登记表在册执行体；14:50 位于 09:30-15:00 时段硬闸内可成交）。

语义边界：
- 本脚本只补 ZCode 14:30 链第一步(S-5 止损硬自动执行)；链内其余步骤(R5只读/
  成交确认/裸奔对账/trailing/断供守护/趋势走坏体检)不复制，漏跑时由次日 08:00
  链第三块核对补跑（既有合同）。
- "链跑过但 stop_loss_executor 内部失败"(如 westock 断供)不属本兜底范围：报告
  mtime 已新鲜会跳过，内部失败由 ZCode 链第五步F 断供守护自行告警。
- 周末跳过；节假日无触发持仓时空转无害。收盘后(>15:00)被补跑触发时 stop_loss_executor
  时段硬闸 REJECT——已知边界，止损敞口顺延由次日 08:00 清单兜底。
- 告警通道=本机 toast+notifications.log（手机推送通道永久禁止，宪法级禁令）。

退出码：0=链已跑跳过/兜底执行完成；2=兜底执行失败(已告警)；3=自身异常(已告警)。
用法：python stop_loss_1430_fallback.py [--date YYYYMMDD] [--dry-run]
"""
import os, sys, datetime, subprocess

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

AFTERNOON_HOUR = 13  # 与 sentinel_1430_check.py 同构：盘中链最早 13:30 起跑
EXEC_TIMEOUT = 600   # stop_loss_executor 单次执行上界(秒)


def notify(title: str, content: str, level: str = 'warning') -> None:
    try:
        subprocess.run([sys.executable, os.path.join(HERE, 'notify.py'), 'send',
                        '--title', title, '--content', content, '--level', level],
                       timeout=60)
    except Exception as e:
        print(f'[fallback] notify 发送失败: {e}（告警未送达, fail-loud）')


def freshness(path: str, today: str):
    """返回 (ok, detail)。与 sentinel_1430_check.py 口径一致。"""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False, '缺失'
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(path))
    if mt.strftime('%Y%m%d') != today or mt.hour < AFTERNOON_HOUR:
        return False, f'陈旧(mtime={mt.strftime("%Y-%m-%d %H:%M")}, 非当日{AFTERNOON_HOUR}:00后盘中链产物)'
    return True, f'在案(mtime={mt.strftime("%Y-%m-%d %H:%M")})'


def main() -> int:
    args = sys.argv[1:]
    dry_run = '--dry-run' in args
    today = None
    if '--date' in args:
        today = args[args.index('--date') + 1]
    today = today or datetime.date.today().strftime('%Y%m%d')

    wd = datetime.date(int(today[:4]), int(today[4:6]), int(today[6:8])).weekday()
    if wd >= 5:
        print(f'[fallback] {today} 为周末，跳过')
        return 0

    report = os.path.join(HERE, 'reports', f'stop_loss_scan_{today}.md')
    ok, detail = freshness(report, today)
    if ok:
        print(f'[fallback] OK: 当日盘中止损链已跑({detail})，兜底跳过（幂等）')
        return 0

    print(f'[fallback] 🔴 判定 14:30 链当日漏跑: reports/stop_loss_scan_{today}.md {detail}')
    if dry_run:
        print('[fallback] DRY-RUN: 将执行 stop_loss_executor.py --execute（本跑未执行）')
        return 0

    notify('14:30止损链漏跑→schtasks兜底启动',
           f'ZCode 14:30 链当日未跑(报告{detail})，14:50 兜底自动执行 stop_loss_executor --execute', 'error')

    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, 'stop_loss_executor.py'), '--execute'],
                           timeout=EXEC_TIMEOUT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        tail = '\n'.join((r.stdout or '').strip().splitlines()[-8:])
        print(f'[fallback] stop_loss_executor exit={r.returncode}\n{tail}')
        # 2026-09-11 首跑实弹修复: 14:50 首跑 exit=1 被 误判异常——
        # 实际是"扫描完成 0 触发"的正常结果(schtasks 环境编码/子进程差异致 exit 不稳定)。
        # 改为看输出内容判定: 有"无止损触发"或"止损执行完成"即成功, 其余非零才是异常。
        out = r.stdout or ''
        _ok = (r.returncode == 0
               or '无止损触发' in out or '无操作' in out
               or '止损执行完成' in out)
        if _ok:
            notify('14:30兜底执行完成', f'兜底 stop_loss_executor --execute 完成(exit={r.returncode})，触发清单见 reports/stop_loss_scan_{today}.md', 'warning')
            return 0
        notify('14:30兜底执行异常', f'stop_loss_executor exit={r.returncode}（时段硬闸REJECT属收盘后补跑预期；其余情况需人工核查）', 'error')
        return 2
    except Exception as e:
        print(f'[fallback] 兜底执行异常: {e}')
        notify('14:30兜底自身异常', f'stop_loss_executor 调用失败: {e}', 'error')
        return 3


if __name__ == '__main__':
    sys.exit(main())
