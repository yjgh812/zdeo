# -*- coding: utf-8 -*-
"""swing_m60_pull_20261029.py — 60分钟K批量拉取(东财公开接口, 不经上下文)"""
import os, sys, json, time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
samples = json.load(open("data/m60_sample_picks.json", encoding="utf-8"))
OUT = os.path.join(ROOT, "data", "m60_samples")
os.makedirs(OUT, exist_ok=True)

URL = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
       "symbol={sym}&scale=60&ma=no&datalen=500")
REF = "https://finance.sina.com.cn"

ok = fail = 0
for code, sd, ed, ret, days in samples:
    out = os.path.join(OUT, f"{code}.json")
    if os.path.exists(out):
        ok += 1
        continue
    sym = ("sh" if code.startswith(("6", "9")) else "sz") + code
    try:
        req = urllib.request.Request(URL.format(sym=sym), headers={"User-Agent": "Mozilla/5.0", "Referer": REF})
        with urllib.request.urlopen(req, timeout=15) as r:
            kl = json.loads(r.read().decode("utf-8"))
        bars = []
        for b in kl:
            # day(含时间), open, high, low, close, volume
            bars.append({"dt": b["day"], "o": float(b["open"]), "c": float(b["close"]),
                          "h": float(b["high"]), "l": float(b["low"]), "v": float(b["volume"])})
        json.dump({"code": code, "bars": bars}, open(out, "w", encoding="utf-8"))
        ok += 1
        print(f"{code}: {len(bars)}根 ({bars[0]['dt'][:10]}~{bars[-1]['dt'][:10]})", flush=True)
    except Exception as e:
        fail += 1
        print(f"{code}: FAIL {e}", flush=True)
    time.sleep(0.3)
print(f"\n完成: OK {ok} FAIL {fail}")
