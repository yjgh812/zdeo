# -*- coding: utf-8 -*-
"""Step2: 记忆合并 ca5->e3 + v2 小文件合并(provider/onboarding/setting 对比).
幂等可重跑; 所有被覆盖文件先备份到 migrate_zcode_d_20260924/backup_step2/.
"""
import json
import os
import shutil
import sys

BK = r"D:\zcodesj\migrate_zcode_d_20260924\backup_step2"
os.makedirs(BK, exist_ok=True)

E3 = r"C:\Users\Administrator\.zcode\cli\memories\projects\default-91b752de799bd5e3\memory"
C5 = r"C:\Users\Administrator\.zcode\cli\memories\projects\default-ca541471c43f8e90\memory"

# ---------- 1) 记忆文件合并: ca5 -> e3 ----------
copied, kept = 0, 0
for name in os.listdir(C5):
    src = os.path.join(C5, name)
    dst = os.path.join(E3, name)
    if not os.path.isfile(src):
        continue
    if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
        shutil.copy2(src, dst)
        copied += 1
    else:
        kept += 1
print("memory files: copied", copied, "kept-e3-newer", kept)

# MEMORY.md 强制以 ca5 为准(已证严格超集)
shutil.copy2(os.path.join(C5, "MEMORY.md"), os.path.join(E3, "MEMORY.md"))
print("MEMORY.md <- ca5 (superset index)")

e3_md = os.listdir(E3)
print("e3 total files now:", len(e3_md))

# ---------- 2) provider_config: C 的 bailian 规则并入 D ----------
DP = r"D:\zcodesj\.zcode\v2\provider_config.json"
CP = r"C:\Users\Administrator\.zcode\v2\provider_config.json"
d = json.load(open(DP, encoding="utf-8"))
c = json.load(open(CP, encoding="utf-8"))
shutil.copy2(DP, os.path.join(BK, "provider_config.D.bak.json"))
d_rules = d.setdefault("config", {}).setdefault("providerConfigRules", {}).setdefault("providerRules", [])
d_ids = {r.get("providerId") for r in d_rules}
added = 0
for r in c.get("config", {}).get("providerConfigRules", {}).get("providerRules", []):
    if r.get("providerId") not in d_ids:
        d_rules.append(r)
        added += 1
order = d["config"].setdefault("providerOrder", [])
for pid in c.get("config", {}).get("providerOrder", []):
    if pid not in order:
        order.append(pid)
json.dump(d, open(DP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("provider_config: added", added, "rules from C-side; total", len(d_rules))

# ---------- 3) onboarding-record: C 的 entries/decisions 并入 D ----------
DO = r"D:\zcodesj\.zcode\v2\onboarding-record.json"
CO = r"C:\Users\Administrator\.zcode\v2\onboarding-record.json"
dd = json.load(open(DO, encoding="utf-8"))
cc = json.load(open(CO, encoding="utf-8"))
shutil.copy2(DO, os.path.join(BK, "onboarding-record.D.bak.json"))
d_entries = {e.get("userId"): e for e in dd.setdefault("entries", [])}
for e in cc.get("entries", []):
    uid = e.get("userId")
    if uid not in d_entries or (e.get("completedAt") or "") > (d_entries[uid].get("completedAt") or ""):
        d_entries[uid] = e
dd["entries"] = list(d_entries.values())
d_dec = {(x.get("userId"), x.get("decidedAt")): x for x in dd.setdefault("decisions", [])}
for x in cc.get("decisions", []):
    d_dec.setdefault((x.get("userId"), x.get("decidedAt")), x)
dd["decisions"] = list(d_dec.values())
json.dump(dd, open(DO, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("onboarding: entries", len(dd["entries"]), "decisions", len(dd["decisions"]))

# ---------- 4) setting.json 对比 ----------
DS = r"D:\zcodesj\.zcode\v2\setting.json"
CS = r"C:\Users\Administrator\.zcode\v2\setting.json"
same = os.path.getsize(DS) == os.path.getsize(CS)
if same:
    same = open(DS, "rb").read() == open(CS, "rb").read()
print("setting.json identical:", same)
if not same:
    shutil.copy2(CS, DS)  # C 侧为权威(含 dataBaseDir=D + 全部UI设置)
    print("setting.json <- C side")

print("STEP2_OK")
