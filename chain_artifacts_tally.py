#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_artifacts_tally.py — 22:00 链产物台账对账 + 断点自愈（E77/E62, 2026-08-28 准则45物化）

背景：22:00 十三合一连续多日靠人工接管（8/26/8/27），且 automation_runs 台账与产物
不一致（8/27 槽位 skipped 但产物存在于 23:30 接管窗口）——"有产物≠链健康"。

本脚本在下一 cron 槽（02:00 凌晨二合一 步0）检测【上一工作日】22:00 链是否跑全：
  产物检查：discipline / naked_audit / pending_review / qfq 已到该日 / factor_daily_ic 已结算到前一交易日
  台账对账：当日 automation_runs 有无 dispatched+succeeded；产物在而无成功记录 = 人工接管(E77 显式标红)
  --resume ：对缺失的幂等断点续跑（daily_chain_update / factor_daily_ic / discipline_stats / fill_confirm expire）

用法：python chain_artifacts_tally.py [--date 20260827] [--resume]
退出码：0=全齐 / 1=缺失未续 / 2=续跑后仍有缺失
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import os
import sqlite3
import subprocess
import sys

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT_DIR = r"D:\zcodesj\reports"
TASKS_IDX = max(
    # 2026-09-22 晚修复(知微终检): C盘重装workspace键迁移, 宿主活注册表=用户级路径,
    # D盘旧副本runs停在9/21 22:45。取runs最新者=活源, 同款盲区第三例。
    (p for p in (r"C:\Users\Administrator\.zcode\v2\tasks-index.sqlite",
                 r"D:\zcodesj\.zcode\v2\tasks-index.sqlite") if os.path.exists(p)),
    key=lambda _p: (lambda _c: (_c.execute(
        "SELECT COALESCE(MAX(created_at),0) FROM automation_runs").fetchone()[0], _c.close())[0])(
        sqlite3.connect("file:%s?mode=ro" % _p, uri=True)),
    default=r"D:\zcodesj\.zcode\v2\tasks-index.sqlite",
)
CHAIN_AUTOMATION_PREFIX = "462607b7"  # 旧22:00链ID(已删)
# 2026-09-17: 22:00/22:45/23:30 重建+新建链 ID 纳入对账(旧前缀连败4日后9/16删旧建新, 见SOP(十八)⑨)
# 2026-09-22: 9/22午后重建新世代纳入(22:00=344c5d2e/22:45=3d9a4bbc, 旧世代见 tmp/full_eval_20260922/id_mapping_old2new.json)
CHAIN_AUTOMATION_IDS = ["462607b7", "8ec2726e", "a329c879", "03b31bcf", "39781b61", "344c5d2e", "3d9a4bbc"]


def _qfq_max() -> str | None:
    try:
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=20)
        r = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()
        con.close()
        return r[0] if r and r[0] else None
    except Exception:
        return None


def _qfq_max_le(date_str: str) -> str | None:
    try:
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=20)
        r = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date<?", (date_str,)).fetchone()
        con.close()
        return r[0] if r and r[0] else None
    except Exception:
        return None


def _ic_latest() -> str | None:
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=10)
        r = con.execute("SELECT MAX(date) FROM factor_daily_ic").fetchone()
        con.close()
        return r[0] if r and r[0] else None
    except Exception:
        return None


def _seal_rows(date_str: str) -> int:
    """O10(2026-09-03): 14:57 封单快照当日行数——盘中产物, 缺失不可事后重建(9/2 实证缺段)。"""
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=10)
        n = con.execute("SELECT COUNT(*) FROM seal_snapshot WHERE date=?", (date_str,)).fetchone()[0]
        con.close()
        return int(n or 0)
    except Exception:
        return 0


def _runs_for_date(date_str: str) -> list[dict]:
    try:
        con = sqlite3.connect(f"file:{TASKS_IDX}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT scheduled_at, dispatch_status, outcome FROM automation_runs "
            "WHERE (" + " OR ".join(["automation_id LIKE ?"] * len(CHAIN_AUTOMATION_IDS)) + ")",
            tuple("%" + pfx + "%" for pfx in CHAIN_AUTOMATION_IDS)).fetchall()
        con.close()
    except Exception:
        return []
    out = []
    for sch, st, oc in rows:
        try:
            dt = datetime.datetime.fromtimestamp(
                int(sch) / 1000.0, datetime.timezone(datetime.timedelta(hours=8)))
        except Exception:
            continue
        if dt.strftime("%Y%m%d") == date_str:
            out.append({"status": st or "", "outcome": oc or ""})
    return out


def _supervisor_completed(date_str: str) -> bool:
    """2026-10-23: schtasks/守护通道(Signal_Cron_V4_2330→daily_signal_cron)完成证据。
    自动化通道 10/15 死亡后链改走 schtasks(22:30)/守护(23:30), automation_runs 台账
    自然无记录——E77 需辨识『通道迁移』而非误判人工接管。
    判据: supervisor 日志存在启动行(日期=T)且完成行(时间∈[T 20:00, T+1 08:00],
    容纳跨午夜管线; 10/22 夜实锤 23:30:01 启动/00:19:53 完成)。"""
    log_path = os.path.join(REPORT_DIR, "_daily_signal_cron.log")
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return False
    try:
        t_next = (datetime.datetime.strptime(date_str, "%Y%m%d")
                  + datetime.timedelta(days=1)).strftime("%Y%m%d")
    except ValueError:
        return False
    started = completed = False
    for ln in lines:
        m = re.match(r"\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\]", ln)
        if not m:
            continue
        d, hm = m.group(1).replace("-", ""), m.group(2)
        if "每日收盘后信号管线启动" in ln and d == date_str:
            started = True
        if "每日管线完成" in ln and ((d == date_str and hm >= "20:00:00")
                                     or (d == t_next and hm <= "08:00:00")):
            completed = True
    return started and completed


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=1800,
                           env=dict(os.environ, PYTHONUTF8="1"))
        tail = (p.stdout or "")[-300:].strip()
        return p.returncode, tail
    except Exception as e:
        return 1, f"运行异常: {type(e).__name__} {e}"


def _resume(checks: dict, date_str: str) -> dict:
    """对缺失幂等断点续跑（不伪造产物）。"""
    action_log: dict[str, str] = {}
    if not checks.get("qfq_dated"):
        rc, tail = _run([sys.executable, r"D:\zcodesj\daily_chain_update.py"])
        action_log["qfq/daily_chain_update"] = f"rc={rc} {tail[:120]}"
        checks["qfq_dated"] = (_qfq_max() or "") >= date_str
    if not checks.get("ic_settleable"):
        rc, tail = _run([sys.executable, r"D:\zcodesj\factor_daily_ic.py", "--days", "5"])
        action_log["factor_daily_ic"] = f"rc={rc} {tail[:120]}"
        prev = _qfq_max_le(date_str)
        checks["ic_settleable"] = prev is not None and (_ic_latest() or "") >= prev
    if not checks.get("discipline"):
        rc, tail = _run([sys.executable, r"D:\zcodesj\discipline_stats.py", "--date", date_str])
        action_log["discipline_stats"] = f"rc={rc} {tail[:120]}"
        checks["discipline"] = os.path.exists(os.path.join(REPORT_DIR, f"discipline_{date_str}.md"))
    if not checks.get("pending_review"):
        rc, tail = _run([sys.executable, r"D:\zcodesj\fill_confirm_monitor.py", "--expire", date_str])
        action_log["fill_confirm --expire"] = f"rc={rc} {tail[:120]}"
        checks["pending_review"] = os.path.exists(os.path.join(REPORT_DIR, f"pending_review_{date_str}.md"))
    return action_log


def main() -> int:
    ap = argparse.ArgumentParser(description="22:00链产物台账对账+断点自愈(E77/E62)")
    ap.add_argument("--date", default=None)
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    # 2026-09-11 Mimosa 审计加固: --date 进报告路径拼接, 未校验可路径穿越
    if a.date and not re.fullmatch(r"\d{8}", str(a.date)):
        print(f"[FAIL-CLOSED] --date 非法(须YYYYMMDD): {a.date!r}")
        return 2
    # 2026-09-29 修假日误报(9/28 22:50裸跑默认查到周日20260927): 默认日期由"自然日昨日"
    # 改为"qfq日历回退到上一交易日"——昨日无bar则继续回退, 假日/周末不再对空产物误报
    if a.date:
        date_str = a.date
    else:
        try:
            _c = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True, timeout=60)
            _y = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d")
            _row = _c.execute("SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date<=?", (_y,)).fetchone()
            _c.close()
            date_str = str(_row[0]) if _row and _row[0] else _y
        except Exception as _e:
            print(f"[WARN] 交易日回退失败, 回落自然日昨日: {_e}")
            date_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d")

    checks = {
        "discipline": os.path.exists(os.path.join(REPORT_DIR, f"discipline_{date_str}.md")),
        "naked_audit": os.path.exists(os.path.join(REPORT_DIR, f"naked_audit_{date_str}.md")),
        "pending_review": os.path.exists(os.path.join(REPORT_DIR, f"pending_review_{date_str}.md")),
        # 2026-08-28 曾检查: 当日买入白名单是否存在(09:10链产物缺失显式标红)。
        # 【已退役 2026-09-24】主人令「全面废除TopN名单限制」白名单本体停机
        # (tulong_buy_whitelist 生成守卫拒生成+当日产物删), 白名单文件自 20260924 起
        # 永久不存在=预期态, 本检查若保留将夜夜假红, 故整项移除不再对账。
        "qfq_dated": (_qfq_max() or "") >= date_str,
        # O10(2026-09-03, 9/2晚22:45复盘解读段+seal缺段无报警教训): 22:45 IDX12 复盘产物
        # (sector_review_{date}.md + daily_review_{date}.json, SOP L37 产物合同) +
        # 14:57 封单快照行数。非数据链产物, 缺失不可 resume 只报缺失; 周末无链活动日不强制。
        "sector_review": os.path.exists(os.path.join(REPORT_DIR, f"sector_review_{date_str}.md")),
        "daily_review_json": os.path.exists(os.path.join(REPORT_DIR, f"daily_review_{date_str}.json")),
        "seal_snapshot": _seal_rows(date_str) > 0,
    }
    prev = _qfq_max_le(date_str)
    checks["ic_settleable"] = prev is not None and (_ic_latest() or "") >= prev

    runs = _runs_for_date(date_str)
    ok_runs = [r for r in runs if r.get("status") == "dispatched" and r.get("outcome") == "succeeded"]
    # 2026-10-23: 通道迁移辨识——schtasks/守护通道(23:30 supervisor)完成也算链真实成功;
    # 两者皆无且产物在 = E77 真失真(自动化通道复活后本回退自动失效, 台账仍是第一证据源)。
    supervisor_ok = _supervisor_completed(date_str)
    checks["runs_succeeded"] = len(ok_runs) > 0 or supervisor_ok
    artifacts_any = checks["discipline"] or checks["naked_audit"] or checks["pending_review"]
    manual_takeover = artifacts_any and not checks["runs_succeeded"]  # E77: 产物在而两通道均无成功记录

    print(f"[chain_tally] {date_str} 检查: {json.dumps(checks, ensure_ascii=False)}")
    print(f"[chain_tally] automation_runs={len(runs)}条 成功={len(ok_runs)}条"
          + (" supervisor通道完成" if supervisor_ok else "")
          + (" | 🔴E77人工接管/台账失真: 产物在但自动化与schtasks两通道均无成功记录" if manual_takeover else ""))

    action_log = {}
    # O10: 复盘/封单三产物仅在有链活动的交易日强制(周末默认昨日会误报), 且缺失不可 resume。
    _NONRESUMABLE = ("sector_review", "daily_review_json", "seal_snapshot")
    _chain_active = (checks.get("discipline") or checks.get("naked_audit")
                     or checks.get("pending_review"))
    missing = [k for k, v in checks.items() if not v and k not in ("runs_succeeded",)
               and not (k in _NONRESUMABLE and not _chain_active)]
    if missing and a.resume:
        # 2026-09-10 晚修复: 原消息把不可续跑项(seal_snapshot 等当日限定产物)也列进
        # "续跑"清单, 易误读为已回填（且盘后五档无法复原，强补=伪造数据）。
        _resumable = [k for k in missing if k not in _NONRESUMABLE]
        _observe_only = [k for k in missing if k in _NONRESUMABLE]
        print(f"[chain_tally] --resume 续跑缺失断点: {_resumable or '无'}"
              + (f" | 仅报缺失(不可续跑·当日限定产物): {_observe_only}" if _observe_only else ""))
        action_log = _resume(checks, date_str)
        missing = [k for k, v in checks.items() if not v and k not in ("runs_succeeded",)
                   and not (k in _NONRESUMABLE and not _chain_active)]

    # 报告落盘（幂等覆盖）
    lines = [
        f"# 22:00链产物台账对账 {date_str}（E77/准则45）\n",
        f"> 生成 {datetime.datetime.now().isoformat(timespec='seconds')}\n",
        "| 检查项 | 状态 |\n|---|---|\n",
    ]
    for k, v in checks.items():
        tag = "✅" if v else ("⚠️ 缺失" if k != "runs_succeeded" else "🔴 无成功记录")
        lines.append(f"| {k} | {tag} |\n")
    if manual_takeover:
        lines.append(f"\n> 🔴 **E77 人工接管判定**: 当日产物存在但自动化台账(automation_runs)与"
                     f"schtasks/守护通道(supervisor 日志)均无成功记录——"
                     f"链真实运行状态与台账不一致，'部分自愈'口径禁止使用。\n")
    if action_log:
        lines.append("\n## --resume 续跑结果\n")
        for k, v in action_log.items():
            lines.append(f"- {k}: {v}\n")
    lines.append("\n" + ("✅ 全齐" if not missing and not manual_takeover else
                         ("续跑后仍有缺失" if missing else "产物齐但台账失真(人工接管)")) + "\n")
    with open(os.path.join(REPORT_DIR, f"chain_tally_{date_str}.md"), "w", encoding="utf-8") as f:
        f.writelines(lines)

    # B1(2026-09-03): 缺失/E77 台账失真即 notify 显性上报——把静默失败变显性
    # (9/1-9/2 两晚 22:00 链静默中断无告警教训; 仅链活动交易日上报, 真假日不扰)
    if (missing or manual_takeover) and (checks.get("runs_succeeded") or artifacts_any):
        _title = ("🔴E77台账失真(人工接管)" if manual_takeover else "🔴22:00链产物缺失")
        _body = (f"{date_str} 缺失={missing or '无(纯台账失真)'}; "
                 f"明细见 {os.path.join(REPORT_DIR, f'chain_tally_{date_str}.md')}")
        try:
            subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              "notify.py"), "send",
                 "--title", _title, "--content", _body],
                timeout=30, capture_output=True)
        except Exception as _ne:
            print(f"[chain_tally] notify 发送失败(不阻断对账): {type(_ne).__name__}: {_ne}")

    if missing:
        return 2 if action_log else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
