#!/usr/bin/env python3
"""查找金十数据 API 端点 - 分析 JS chunk 中的 API URL"""
import httpx, re, asyncio

async def find_api():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # Get the main JS chunk
        js_urls = [
            "https://www.jin10.com/new/js/index.bd62166d.js",
            "https://www.jin10.com/new/js/chunk-vendors.be0cfea6.js",
            "https://www.jin10.com/new/js/chunk-components.26f30523.js",
        ]
        for js_url in js_urls:
            try:
                resp = await client.get(js_url, headers=headers)
                if resp.status_code != 200:
                    continue
                print(f"\n=== {js_url.split('/')[-1]} (len={len(resp.text)}) ===")
                js = resp.text
                
                # Method 1: Find API URLs directly
                pattern1 = re.compile(r'["\'](https?://[^"\']{10,200})["\']')
                for m in pattern1.finditer(js):
                    url = m.group(1)
                    if any(x in url.lower() for x in ["api", "calendar", "flash", "jin10", "/data/", "data."]):
                        print(f"  URL: {url}")
                
                # Method 2: Relative paths with /api/ or /data/
                pattern2 = re.compile(r'["\'](/api/[^"\']{5,100})["\']')
                for m in pattern2.finditer(js):
                    print(f"  PATH: https://www.jin10.com{m.group(1)}")
                    
            except Exception as e:
                print(f"  Error: {e}")

asyncio.run(find_api())