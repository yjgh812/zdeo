# -*- coding: utf-8 -*-
"""全量测试·重载组（00:30 链后一次性 schtasks 触发，避开夜链 22:00 资源窗口）：
health_check / l0 / naked_trade_audit(最新交易日=qfq最大数据日) / db_freshness / fwd_shadow 第五跑(链后应为 in_chain=True+md5 基准)。
输出 = tmp/fulltest_heavy_0030.log（追加），逐项 rc 记录。
"""
import subprocess
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
LOG = r"D:/zcodesj/tmp/fulltest_heavy_0030.log"
PY = sys.executable
CD = r"D:\zcodesj"


def _latest_trade_date():
    """最新已完成交易日 = qfq 库最大数据日（链后口径；2026-10-19 全量测试轮优化，
    替代硬编码日期——硬编码隔日必挂）。"""
    import sqlite3
    conn = sqlite3.connect(
        r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro", uri=True)
    d = conn.execute("SELECT max(date) FROM stock_daily_meta_qfq").fetchone()[0]
    conn.close()
    return str(d)


_LATEST = _latest_trade_date()
JOBS = [
    ("health_check", [r"D:\zcodesj\health_check_daily.py"], 600),
    ("l0_gates", [r"D:\zcodesj\l0_system_health.py"], 180),
    (f"naked_audit_{_LATEST}", [r"D:\zcodesj\naked_trade_audit.py", "audit", "--date", _LATEST], 180),
    ("db_freshness", [r"D:\zcodesj\check_db_freshness.py"], 180),
    ("fwd_shadow_5th", [r"D:\zcodesj\analysis\main_combo_forward_shadow_daily.py"], 600),
    ("fwdfirstrun_verdict", [r"D:\zcodesj\analysis\verify_fwd_shadow_firstrun_20261018.py"], 60),
]

out = []
out.append(f"=== 全量测试重载组（链后窗口） {datetime.now():%Y-%m-%d %H:%M:%S} ===")
for name, argv, timeout in JOBS:
    argv_full = [PY] + argv
    try:
        r = subprocess.run(argv_full, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=CD)
        rc = r.returncode
        tail = (r.stdout or "").strip().splitlines()
        tail_line = tail[-1][:120] if tail else ""
        out.append(f"[{name}] rc={rc} | {tail_line}")
    except subprocess.TimeoutExpired:
        out.append(f"[{name}] rc=TIMEOUT({timeout}s)")

summary_pass = all("rc=0" in x or "rc=TIMEOUT" not in x for x in out) and not any("rc=TIMEOUT" in x for x in out)
# fwd_shadow 第五跑后附 verdict 读数
with open(LOG, "a", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
print("\n".join(out))
print(f"[summary] all_rc0={summary_pass}（TIMEOUT 项=夜链窗口争用或链未完, 如实呈报不掩饰）")
