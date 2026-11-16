# -*- coding: utf-8 -*-
"""fwd_shadow 夜链首跑验收哨（一次性 schtasks 23:50 触发，只读零交易权）：
检查 [fwd_shadow] 是否已被今晚 22:00 夜链在链执行（state mtime + ledger md5 对基准）。
verdict 三态：PASS(在链跑成且md5一致) / DRIFT(跑成但md5不同,如实呈报差异) / MISS(链未触达,非阻塞明晚重试)。
"""
import hashlib
import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STATE = r"D:/zcodesj/data/main_combo_fwd_shadow_state_latest.json"
LEDGER = r"D:/zcodesj/data/main_combo_fwd_shadow_ledger.jsonl"
LOG = r"D:/zcodesj/tmp/verify_fwdfirstrun_2350.log"
BASE_MD5 = "24c47fb5a57024d89261cad4d8bb4bae"
TONIGHT_2200 = datetime(2026, 10, 18, 22, 0, 0)

lines = []
verdict = "MISS"
detail = ""
try:
    st_mtime = os.path.getmtime(STATE)
    st_dt = datetime.fromtimestamp(st_mtime)
    age_ok = st_dt >= TONIGHT_2200
    md5 = hashlib.md5(open(LEDGER, "rb").read()).hexdigest()
    state = json.load(open(STATE, encoding="utf-8"))
    combo = state.get("combo", {})
    in_chain = age_ok
    if in_chain and md5 == BASE_MD5:
        verdict = "PASS"
        detail = f"在链跑成且 md5 与基准一致（state mtime={st_dt:%H:%M:%S}）"
    elif in_chain:
        verdict = "DRIFT"
        detail = f"在链跑成但 md5 与基准不同（{md5}）——如实呈报差异，不臆测原因"
    else:
        verdict = "MISS"
        detail = f"state mtime={st_dt:%Y-%m-%d %H:%M} 早于今晚 22:00——夜链未触达 fwd_shadow（非阻塞，明晚幂等重试）"
    lines.append(f"state_mtime={st_dt:%Y-%m-%d %H:%M:%S}")
    lines.append(f"ledger_md5={md5}")
    lines.append(f"combo_ret_pct={combo.get('ret_pct')}")
    lines.append(f"in_chain={in_chain}")
except OSError as e:
    detail = f"产物文件缺失: {e}"

lines.insert(0, f"verdict={verdict}")
lines.insert(1, f"detail={detail}")
lines.insert(2, f"checked_at={datetime.now():%Y-%m-%d %H:%M:%S}")
out = "\n".join(lines) + "\n"
# 20261019 降级加固: 23:50哨实跑遇log被独占锁(PermissionError致verdict未落盘),
# 电池编排器capture stdout可读print兜底, 故log写失败不再抛错
try:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(out)
except OSError as e:
    print(f"[warn] log append failed: {e}; verdict stdout-only")
print(out)
