# -*- coding: utf-8 -*-
"""退学炒股(blog/1035186)跟帖图片批量下载: data/txgf_reply_image_index_20261006.json
_max.png 原图直下, 失败回退 _760w; 输出 reports/txgf_charts_20261006/jiaogedan/
(管线与 zmck_image_download_20261006.py 同构, 仅换源索引与输出目录)
"""
import json
import time
import urllib.request
from pathlib import Path

IDX = Path(r"D:\zcodesj\data\txgf_reply_image_index_20261006.json")
OUT = Path(r"D:\zcodesj\reports\txgf_charts_20261006\jiaogedan")
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://www.tgb.cn/"}

idx = json.loads(IDX.read_text(encoding="utf-8"))
ok = miss = 0
miss_log = []
for row in idx["rows"]:
    for i, url in enumerate(row.get("imgs", []), 1):
        alt = url.replace("_max.png", "_760w.png")
        ext = "jpg" if ".jpg" in url or ".jpeg" in url else "png"
        name = f"{row['post']}_{row['floor']}_{i}.{ext}"
        dest = OUT / name
        if dest.exists():
            ok += 1
            continue
        got = None
        for candidate in (url, alt):
            try:
                req = urllib.request.Request(candidate, headers=UA)
                with urllib.request.urlopen(req, timeout=20) as r:
                    if r.status == 200:
                        got = r.read()
                        break
            except Exception:
                continue
        if got:
            dest.write_bytes(got)
            ok += 1
        else:
            miss += 1
            miss_log.append({"post": row["post"], "floor": row["floor"], "url": url})
        time.sleep(0.3)

print(f"ok={ok} miss={miss}")
if miss_log:
    (OUT / "_missing.json").write_text(
        json.dumps(miss_log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"missing list -> {OUT / '_missing.json'}")
