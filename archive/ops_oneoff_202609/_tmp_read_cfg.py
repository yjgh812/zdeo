import json, re
raw = open(r'C:/Users/yujie/.zcode/cli/config.json', encoding='utf-8').read()
# 宽松解析：把连续反斜杠串归一为双反斜杠（合法JSON转义）
fixed = re.sub(r'\\+', lambda m: '\\\\', raw)
cfg = json.loads(fixed)
print('keys:', list(cfg.keys()))
for k in ('model', 'models', 'provider', 'providers', 'agents', 'plugins'):
    if k in cfg:
        print('---', k)
        print(json.dumps(cfg[k], ensure_ascii=False)[:600])
