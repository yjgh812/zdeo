"""下载财联社JS bundle并定位签名算法"""
import requests
import re
import os

os.chdir(r"D:\zcodesj")
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}

files = {
    "app": "https://wwwjs.cls.cn/_next/static/chunks/pages/_app-24a8f9ff6e2013c1.js",
    "telegraph": "https://wwwjs.cls.cn/_next/static/chunks/pages/telegraph-65450a0ac40e3ed9.js",
    "main": "https://wwwjs.cls.cn/_next/static/chunks/main-dac5acdbc6ced465.js",
    "c8063": "https://wwwjs.cls.cn/_next/static/chunks/8063-931e4af6560c41c9.js",
    "c5088": "https://wwwjs.cls.cn/_next/static/chunks/5088-7d2792c68083466e.js",
}

js_contents = {}
for name, url in files.items():
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        js = resp.text
        js_contents[name] = js
        with open(f"cls_js_{name}.js", "w", encoding="utf-8") as f:
            f.write(js)
        print(f"✅ {name}: {len(js)}字符已保存")
    except Exception as e:
        print(f"❌ {name}: {e}")

# 在 app JS 中定位 sign= 的上下文
print("\n" + "=" * 70)
print("定位 sign 相关代码片段")
print("=" * 70)

for name, js in js_contents.items():
    # 找 sign 参数构造的位置
    for pattern in [r"sign\s*[:=]\s*[a-zA-Z_$][\w$]*", r"getSign|createSign|makeSign|calcSign"]:
        for m in re.finditer(pattern, js):
            start = max(0, m.start() - 300)
            end = min(len(js), m.end() + 300)
            snippet = js[start:end]
            # 只显示有意义的片段(含md5/sha/hash或参数拼接)
            if any(k in snippet.lower() for k in ["md5", "sha", "hash", "sort", "timestamp", "time"]):
                print(f"\n【{name}】@{m.start()}: ...{snippet}...")
                print("-" * 60)