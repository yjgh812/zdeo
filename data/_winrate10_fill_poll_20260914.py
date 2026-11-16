# -*- coding: utf-8 -*-
"""胜率Top10 挂单成交盘中轮询(一次性, 20260914): 每10分钟查open_orders未决委托的成交, 新成交→toast+日志. 15:05后退出."""
import sys, os, json, time, subprocess
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
BASE = r'D:\zcodesj'
OO = os.path.join(BASE, 'data', 'open_orders.jsonl')
LOG = os.path.join(BASE, 'data', 'winrate10_fill_poll.log')
NOTIFY = os.path.join(BASE, 'notify.py')
END = '15:05'
INTERVAL = 600

def now_hm():
    import datetime
    return datetime.datetime.now().strftime('%H:%M')

def toast(title, content, level='info'):
    try:
        subprocess.run([sys.executable, NOTIFY, 'send', '--title', title, '--content', content, '--level', level], timeout=30)
    except Exception:
        pass

def load_open():
    """(code -> 挂单价) 仅 status=OPEN 的最新记录"""
    latest = {}
    with open(OO, encoding='utf-8') as f:
        for line in f:
            try:
                d = json.loads(line)
                code = (d.get('code') or '').replace('sz', '').replace('sh', '')
                if d.get('status') == 'OPEN':
                    latest[code] = float(d.get('price') or 0)
            except Exception:
                continue
    return latest

def check_fills():
    r = subprocess.run([sys.executable, os.path.join(BASE, 'fill_confirm_monitor.py')],
                       capture_output=True, text=True, timeout=90, encoding='utf-8', errors='replace')
    filled = {}
    try:
        i = r.stdout.find('{')
        d = json.loads(r.stdout[i:r.stdout.rfind('}')+1])
        filled = d.get('filled_codes', {})
    except Exception:
        pass
    return filled, r.stdout

seen = set()
logf = open(LOG, 'a', encoding='utf-8')
while True:
    hm = now_hm()
    if hm >= END:
        print(f'[{hm}] 收盘轮询结束', flush=True)
        logf.write(f'[{hm}] 结束\n'); logf.flush()
        break
    filled, raw = check_fills()
    new = {k: v for k, v in filled.items() if k not in seen}
    ts = f'[{hm}] 在途{8-len(filled)} 成交{len(filled)} 新增{list(new.items())}'
    print(ts, flush=True)
    logf.write(ts + '\n'); logf.flush()
    if new:
        seen.update(new)
        toast('胜率Top10成交', f'新成交: {new}', 'info')
    time.sleep(INTERVAL)
logf.close()
