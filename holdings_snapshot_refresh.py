# -*- coding: utf-8 -*-
"""
holdings_snapshot_refresh.py — 持仓清单快照生成器（2026-10-13 运维轮实锤断供后重建）
背景: data/holdings_codes_latest.json 自 2026-10-08 19:35 起无生成者(旧写入件随旧链退役),
      哨族四读者(strong_sell_watch/exit_gate_watch/stock_zones_watch/stock_dossier)集体吃陈旧快照。
合同:
  输入  westock MCP portfolio_paper_positions (唯一真值源, 只读零交易权)
  输出  data/holdings_codes_latest.json  schema: source/snapshot_at/note/positions[{code,name,qty}]
  幂等  snapshot_at+positions 与现文件一致则 SKIP 不重写
  fail-closed  MCP 不可用/结构缺失 → notifications.log 一行 + exit 1, 不触碰旧文件(宁可陈旧不可伪造)
  空仓合法  positions=[] 照写(真空仓不是错误)
调度: stock_dossier_run.cmd 链尾(2330 dossier 后); 铁律同族: 真值以 westock MCP 实时为准。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace")
try:  # schtasks 子进程 stdout 为 GBK，防中文 print 崩溃（westock_reconcile 同族先例）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(r"D:\zcodesj")
OUT = ROOT / "data" / "holdings_codes_latest.json"
NOTIFY = ROOT / "data" / "notifications.log"


def _notify(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [holdings_snapshot] {msg}"
    try:
        with open(NOTIFY, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)


def fetch_positions() -> list[dict]:
    from westock_client import WeStockClient
    resp = WeStockClient().call("portfolio_paper_positions", {})
    if not resp or not resp.get("ok"):
        raise RuntimeError(f"westock positions 不可用: {str(resp)[:200]}")
    data = resp.get("data") or {}
    rows = data.get("positions")
    if not isinstance(rows, list):
        raise RuntimeError(f"westock positions 结构缺失: {str(list(data.keys()))[:120]}")
    out = []
    for r in rows:
        code = str(r.get("code", "")).strip().lower()
        if len(code) != 8 or not code[:2] in ("sh", "sz") or not code[2:].isdigit():
            raise RuntimeError(f"异常代码拒绝写入快照: {code!r} (fail-closed)")
        out.append({
            "code": code,
            "name": str(r.get("name", "")),
            "qty": int(r.get("quantity") or 0),
        })
    return out


def main() -> int:
    try:
        positions = fetch_positions()
    except Exception as e:
        _notify(f"FAIL {type(e).__name__}: {e}; 旧快照保留未覆盖")
        return 1

    snapshot = {
        "source": "westock MCP portfolio_paper_positions (holdings_snapshot_refresh.py)",
        "snapshot_at": datetime.now().strftime("%Y-%m-%d"),
        "note": "仅持仓清单三要素(code/name/qty); 真值以westock MCP实时为准; 价格盈亏不入快照(哨族信号用qfq库行情)",
        "positions": positions,
    }
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
        if (old.get("snapshot_at") == snapshot["snapshot_at"]
                and old.get("positions") == positions):
            print(f"[SKIP] 快照已为 {snapshot['snapshot_at']} 持仓{len(positions)}只, 幂等跳过")
            return 0
    except FileNotFoundError:
        pass
    except Exception:
        pass  # 旧文件损坏不阻断重写

    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 快照刷新 snapshot_at={snapshot['snapshot_at']} 持仓{len(positions)}只 -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
