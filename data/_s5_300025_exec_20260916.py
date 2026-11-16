# -*- coding: utf-8 -*-
"""300025 华星创业 S-5紧急前置卖出 一次性执行件(20260916 09:31 schtasks 触发)
主人 2026-09-15 晚指令"现在执行": 浮亏已破-8%锚(实盘参考-8.3%), 消除隔夜缺口敞口。
走 trade_executor 唯一入口, 交易时段硬闸 09:30 后放行, 价格取 09:31 实时。
日志: data/_s5_300025_exec_20260916.log; 幂等性由 executor 当日未了结委托拦截兜底。
"""
import subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')

CMD = [
    sys.executable, r'D:\zcodesj\trade_executor.py', 'sell',
    '--code', '300025', '--name', '华星创业',
    '--reason', '止损S-5紧急前置(主人9/15晚指令现在执行;实亏-8.3%已破-8%锚)',
    '--is-stop-loss', '--owner-emergency', '--confirmed', '--execute',
]

if __name__ == '__main__':
    print('[S5-300025] start', flush=True)
    r = subprocess.run(CMD, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    print(r.stdout[-4000:] if r.stdout else '', flush=True)
    print(r.stderr[-2000:] if r.stderr else '', flush=True)
    print(f'[S5-300025] exit={r.returncode}', flush=True)
