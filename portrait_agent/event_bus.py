# -*- coding: utf-8 -*-
"""轻量消息总线(计划书§2.2步骤2): C1_PORTRAIT_CMD / C2_PORTRAIT_RESULT / C3_PORTRAIT_ALERT.
单机实现: 进程内订阅回调 + 频道消息JSONL留痕(portrait_agent/logs/bus_{channel}.jsonl).
后续可替换为真分布式总线而不改上层调用(接口稳定: subscribe/publish)."""
import json, os, threading, time

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
_LOCK = threading.Lock()
_SUBS = {ch: [] for ch in ('C1_PORTRAIT_CMD', 'C2_PORTRAIT_RESULT', 'C3_PORTRAIT_ALERT')}

def subscribe(channel, handler):
    """注册订阅者; handler(msg:dict)."""
    assert channel in _SUBS, f'未知频道 {channel}'
    _SUBS[channel].append(handler)
    return len(_SUBS[channel]) - 1

def publish(channel, msg):
    """发布消息到频道: 逐个回调(异常隔离不中断), 全量JSONL留痕; 返回送达数."""
    assert channel in _SUBS, f'未知频道 {channel}'
    msg = {'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'channel': channel, **msg}
    with _LOCK:
        with open(os.path.join(LOG_DIR, f'bus_{channel}.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(msg, ensure_ascii=False, default=str) + '\n')
    delivered = 0
    for h in _SUBS[channel]:
        try:
            h(msg); delivered += 1
        except Exception as e:
            _alert(f'订阅者异常@{channel}: {e}')
    return delivered

def _alert(text):
    with _LOCK:
        with open(os.path.join(LOG_DIR, 'bus_C3_PORTRAIT_ALERT.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
                                'channel': 'C3_PORTRAIT_ALERT', 'level': 'error', 'text': text},
                               ensure_ascii=False) + '\n')

def self_test():
    """计划书§2.2步骤2校验: 收发100条测试消息, 丢包率=0. 用独立自测频道, 不污染C1审计日志."""
    global _SUBS
    _SUBS['C1_PORTRAIT_CMD__selftest'] = []
    got = []
    subscribe('C1_PORTRAIT_CMD__selftest', lambda m: got.append(m['seq']))
    sent = 100
    for i in range(sent):
        publish('C1_PORTRAIT_CMD__selftest', {'seq': i, 'kind': 'selftest'})
    lost = sent - len([g for g in got if g in range(sent)])
    # 清理自测日志文件, 保持 logs/ 只有真实业务消息
    try: os.remove(os.path.join(LOG_DIR, 'bus_C1_PORTRAIT_CMD__selftest.jsonl'))
    except OSError: pass
    return {'sent': sent, 'received': len(got), 'lost': lost, 'pass': lost == 0}

if __name__ == '__main__':
    print(self_test())
