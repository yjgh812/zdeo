# -*- coding: utf-8 -*-
"""轻量只读测试组（全量测试·夜链运行窗口适用版）：秒级只读项，不与夜链争用。"""
import glob
import hashlib
import json
import re
import sqlite3
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    mark = "PASS" if cond else "FAIL"
    if not cond:
        ok_all = False
    print(f"[{mark}] {name} {detail}")


# L1 名称校验（战备单; 2026-10-19 全量测试轮优化=取最新 dated 战备单, 免日期硬编码隔日必挂）
_battle = sorted(glob.glob(r"D:\zcodesj\reports\preopen_battle_refresh_*.md"))
r = subprocess.run(
    [sys.executable, r"D:\zcodesj\verify_stock_names.py", _battle[-1] if _battle else r"D:\zcodesj\reports\preopen_battle_refresh_20261019.md"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
)
l1 = "名称校验通过" in (r.stdout or "")
check("L1 名称校验(战备单)", l1, (r.stdout or "").strip().splitlines()[-1][:40] if r.stdout else "")

# L2 prereg
d = json.load(open(r"D:/zcodesj/data/PREREG_MAIN_COMBO_FORWARD_SHADOW_DRAFT_20261018.json", encoding="utf-8"))
check("L2 prereg", d.get("status") == "ACTIVE" and d.get("params", {}).get("activation_anchor") == "2026-10-01",
      f"status={d.get('status')} anchor={d.get('params', {}).get('activation_anchor')}")

# L3 晨报十九项(20261018 PD-048仓位锚第0段, 1005173b升十九) + 第17步完整
conn = sqlite3.connect(r"D:/zcodesj/.zcode/v2/tasks-index.sqlite")
p = conn.execute("SELECT prompt FROM automations WHERE automation_id LIKE '%505b77d4%'").fetchone()[0]
conn.close()
steps = sorted(set(int(m) for m in re.findall(r"(?:^|\n)(\d{1,2})\. ", p, re.M)))
check("L3 晨报", steps == list(range(0, 19)) and "17. 主组合前向机械影子臂日更简报" in p and "PD-048" in p,
      f"steps={steps}")

# L4 automation 活性
conn = sqlite3.connect(r"D:/zcodesj/.zcode/v2/tasks-index.sqlite")
n = conn.execute("SELECT COUNT(*) FROM automations WHERE enabled=1 AND lifecycle_status='active'").fetchone()[0]
conn.close()
check("L4 automation active", n >= 18, f"{n} 条")

# L5 fwd_shadow ledger md5 对基准
md5 = hashlib.md5(open(r"D:/zcodesj/data/main_combo_fwd_shadow_ledger.jsonl", "rb").read()).hexdigest()
# 20261019: 基准锚跨午夜翻转为10/19锚d62f64d3(夜链00:42在链跑成+两单跑逐位一致定谳合法);
# 本哨仅对qfq静态窗有效, 数据日更新(周一22:00链后)预期变, 届时按当日重算更新
check("L5 fwd_shadow md5(静态窗基准)", md5 in ("24c47fb5a57024d89261cad4d8bb4bae", "d62f64d3a520739079a70a1b6a75ad3a"), md5)

# L6 全家桶编译
import py_compile  # noqa: E402
scripts = [
    r"D:/zcodesj/analysis/portrait_ic_readout_20261018.py",
    r"D:/zcodesj/analysis/ml_incremental_ic_study_20261018.py",
    r"D:/zcodesj/analysis/verify_fwd_shadow_firstrun_20261018.py",
    r"D:/zcodesj/analysis/observation_studies_20261018.py",
    r"D:/zcodesj/analysis/alpha_verification_study_20261018.py",
    r"D:/zcodesj/analysis/monthly_shadow_sentry.py",
    r"D:/zcodesj/analysis/main_combo_forward_shadow_daily.py",
    r"D:/zcodesj/analysis/pnl_distribution_audit_20261017.py",
    r"D:/zcodesj/analysis/fwd_shadow_repro_compare_20261018.py",
    r"D:/zcodesj/stop_loss_executor.py",
    r"D:/zcodesj/daily_chain_update.py",
]
bad = []
for s in scripts:
    try:
        py_compile.compile(s, doraise=True)
    except Exception as ex:  # noqa: BLE001
        bad.append((s, str(ex)))
check("L6 全家桶 py_compile(11 脚本)", not bad, bad if bad else "11/11")

print()
print("轻量组结论:", "ALL PASS" if ok_all else "存在 FAIL")
sys.exit(0 if ok_all else 3)
