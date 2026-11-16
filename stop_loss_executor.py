#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屠龙系统·止损硬自动执行器 v1.0（8/13全维度自省 S-5）

========== 背景 ==========
8/13 实证：陇神戎发-12.9%（超-8%线4天未割，多亏-24,924）+ 沃森生物-9.2%
（多亏-18,184），合计多亏-43,108 元，占总亏损 84.7%。

根因：止损规则写在了手册里、记忆里、cron里，但实际没有任何自动执行机制。
14:30 cron 虽然挂了但只输出报告不生成委托 → 依赖人去看报告后再手动操作 → 跳过。

========== S-5 修复 ==========
本脚本由 14:30 cron 自动调用，流程：
1. 从 westock MCP 拉当前所有持仓（自动获取可用数据）
2. 逐只计算成本价 vs 现价 → 亏损%
3. 亏损 ≤ -8% 的标的自动生成卖出指令（不可拒绝）
4. 写入 trade_state + 输出执行简报
5. 触发-8%止损线的标的立即生成卖出指令（主人8/14裁决：任何时间可卖出，不等待尾盘窗口）

========== 用法 ==========
    # 正常执行（14:30 cron 调用）
    python stop_loss_executor.py --execute

    # dry-run 模式（只报告，不下单，10:00/13:30 cron 调用）
    python stop_loss_executor.py --dry-run

    # 指定日期回溯（复盘用）
    python stop_loss_executor.py --dry-run --date 20260813
"""
from __future__ import annotations

import argparse
import subprocess
import json
import os
import sys
import os
import sqlite3
from datetime import datetime

# 2026-09-18 GBK坑修复(9/14同款先例): schtasks 子进程按 GBK 控制台解码, emoji(⚡/🔍)必崩;
# 此前仅交互会话调用未暴露, 挂 StopLoss_Morning_0935 后实弹崩在 banner 行
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 数据源 = westock MCP 直连(2026-08-20修复: 原 westockdata import 死代码从不生效, 已移除;
# 持仓/盈亏/资产一律从实时 MCP 获取, 见 _fetch_positions_via_mcp)


# ===== 常量 =====
STOP_LOSS_THRESHOLD = -0.08  # 卖出三铁律-8%不变(2026-08-22主人确认); S2(6.5%)仅限回测引擎口径预提, 不落实盘
# 移动止盈(波动止盈)提示口径 — 主人令 2026-11-07「止盈不是硬止盈, 是波动止盈: 最高点下跌8%就止盈, 让利润奔跑」
#   + 同日补充「稳健 vs 博弹性, 都要有, 但是要标注」→ 双线并存, 每次提示标注各自状态。
# 🔒稳健线 TRAIL_STEADY_PP=8pp  : 早落袋, 实证胜率90.7%/中位+6.98%（样本97只）
# 🚀博弹性线 TRAIL_AGGRESSIVE_PP=10pp: 让大牛股跑远, 实证均值+8.34%(最优)/胜率84.5%
#   （10pp 同时是 PREREG 指纹 a41fbe 的回测/结算口径, 故博弹性线天然与历史实验可比）
# TRAIL_ACTIVATE_PCT: 峰值浮盈达该 % 才激活跟踪, 防止刚买入的票被误判成"止盈"
# 两线都只提示, 卖多少仍须主人决定; 触稳健线即入列, 并同时显示博弹性线是否已触发。
TRAIL_STEADY_PP = 8.0
TRAIL_AGGRESSIVE_PP = 10.0
TRAIL_ACTIVATE_PCT = 10.0
TRAIL_DRAWDOWN_PP = TRAIL_STEADY_PP   # 兼容旧引用(原硬编码10pp, 现稳健线8pp)
# TIME_BASED_STOP/MIN_HOLD_DAYS 已删(2026-09-07主人令): 时间型退出已裁决全停用(9/7), 二者为当年注入残留且全库零引用
# SELL_WINDOW removed per owner        # 14:45 尾盘开始
SELL_WINDOW_START_MIN = 45
SELL_WINDOW_END_HOUR = 14          # 14:55 尾盘结束
SELL_WINDOW_END_MIN = 55
REPORT_DIR = r"D:\zcodesj\reports"


def norm_code(code: str) -> str:
    """标准化股票代码（去前缀、6位补零）。"""
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6) if c.isdigit() else c


def _fetch_positions_via_mcp() -> list[dict] | None:
    """直连 westock HTTP MCP 拉实时持仓(2026-08-20修复: 原 westockdata 模块从未可import,
    导致 S-5 止损执行器永远取不到持仓; 现改为脚本自包含直连 config.json 中的 Bearer token)。
    返回 list[dict] 或 None。"""
    import json as _json
    import urllib.request as _ur
    url = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
    token = ""
    try:
        # 20261011 triage: 硬编码双用户名路径(yujie)改 USERPROFILE 运行时解析——
        # 两份 config.json 当前字节全同(yujie 疑为镜像), 但 9/22 workspace 迁移已有
        # 路径失效先例, 单点用户名硬编码属脆性。
        _cfgp = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\Administrator"),
                             ".zcode", "cli", "config.json")
        cfg = _json.load(open(_cfgp, encoding="utf-8"))
        ws = cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
        token = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
    except OSError:
        pass
    if not token:
        token = os.environ.get("WESTOCK_TOKEN", "")
    if not token:
        return None

    def rpc(payload):
        body = _json.dumps(payload).encode()
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = _ur.Request(url, data=body, headers=h, method="POST")
        with _ur.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8")

    try:
        rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "tulong-stoploss", "version": "1.0"}}})
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        raw = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "portfolio_paper_positions", "arguments": {}}})
        for line in raw.splitlines():
            if line.startswith("data:") and line.strip() != "data:":
                d = _json.loads(line[5:].strip())
                if "result" in d:
                    for c in d["result"].get("content", []):
                        if c.get("type") == "text":
                            obj = _json.loads(c["text"])
                            data = obj.get("data", {})
                            out = []
                            for p in data.get("positions", []):
                                out.append({
                                    "code": norm_code(p.get("code", "")),
                                    "name": p.get("name", ""),
                                    "cost_price": float(p.get("costPrice") or 0),
                                    "current_price": float(p.get("currentPrice") or 0),
                                    "quantity": int(float(p.get("quantity") or 0)),
                                    "availableQty": int(float(p.get("availableQty") or 0)),
                                    "float_pnl_pct": float(p.get("profitRate") or 0),
                                })
                            return out  # 2026-08-24 修复: 空仓([])≠故障, 响应到达即返回
    except Exception:
        return None
    return None


def get_positions() -> list[dict]:
    """获取当前所有持仓。

    优先直连 westock MCP 实时接口，失败时尝试本地数据库兜底。
    返回 list[dict] 每项含 code/name/cost_price/current_price/quantity/float_pnl_pct。
    """
    # 1) MCP 直连(2026-08-20修复: 原 westockdata import 死代码从不生效)
    mcp_pos = _fetch_positions_via_mcp()
    if mcp_pos is not None:
        # 2026-09-10 合同漂移 fail-closed: 字段改名/结构变化时解析行会全零 →
        # check_stop_loss_positions 静默跳过 → "无止损触发"假成功 exit 0(9/9
        # trend_checker MCP 合同漂移同族防御, 扫描+哨兵+兜底三层全瞎是实盘亏损面)。
        # 2026-09-10 续(S3): 有效行判定补 cost_price>0——costPrice 缺失即失去成本锚,
        # -8% 判定方向全错, 必须与 quantity/current_price 同列为有效行必要条件。
        _valid = [p for p in mcp_pos
                  if (p.get("quantity") or 0) > 0 and (p.get("current_price") or 0) > 0
                  and (p.get("cost_price") or 0) > 0]
        if not _valid:
            print("❌ westock MCP 持仓解析异常: 返回行但有效行0(quantity>0且current_price>0"
                  "且cost_price>0)——疑似合同漂移(costPrice 缺失=-8%止损失去成本锚), "
                  "止损扫描中止, 拒绝假成功", file=sys.stderr)
            sys.exit(3)
        # profitRate 交叉校验: MCP 自带盈亏率 vs cost/current 重算, 偏差>1pp 留痕预警
        # (profitRate 为 0 视为未提供跳过——解析层 0 值无法区分"未提供"与"恰为0")
        for p in mcp_pos:
            fr = p.get("float_pnl_pct") or 0.0
            cst, cur = p.get("cost_price") or 0.0, p.get("current_price") or 0.0
            if fr and cst and cur:
                calc = (cur - cst) / cst * 100
                if abs(float(fr) - calc) > 1.0:
                    print(f"[WARN] 盈亏交叉校验偏差: {p.get('code')} MCP利润{float(fr):.2f}% "
                          f"vs 重算{calc:.2f}% → 口径需人工复核", file=sys.stderr)
        return mcp_pos
    # 2026-08-25 O2 fail-closed: MCP 明确失败(返回None)时禁止静默走 position_history
    # 兜底——该表无 quantity 列, quantity=0 的假持仓会被止损扫描全部跳过,
    # 造成"无止损触发"假成功(违反宪法§9 结构化错误返回)。真无持仓时 MCP 返回[]。
    print("❌ westock MCP 持仓获取失败(token/网络)——止损扫描中止, 拒绝假成功", file=sys.stderr)
    sys.exit(3)


def _partial_sell_cost_overlay(held: dict[str, int]) -> tuple[dict[str, float], list[dict]]:
    """PD-040（2026-10-16「继续」令定谳）：部分卖出剩仓的 S-5 成本口径=原始买入成本。

    背景（10/16 第三轮全维自省 E22）：westock 对部分卖出持仓会把已实现亏损摊入剩余
    costPrice（博汇纸业 6000@4.91 卖 3000 后显示成本 5.246），显示口径重复计入已实现
    亏损；S-5 语义=未实现亏损止损 → 对"当前持仓纪元内有过卖出"的代码，改用 westock
    成交回报重建的原始买入成本（纪元=持仓归零后重新起算；无卖出代码显示口径无失真，
    不动）。重建不全（回报窗口缺早期买入/纪元净持仓对不上实持）→ 该码保底显示口径并
    如实注记，宁可保守不造数。
    返回 ({code6: 原始成本}, 注记列表)；westock 拉取失败=({}, 全局失败注记)。
    """
    notes: list[dict] = []
    try:
        # 2026-10-29 PD-040数据源切换(主人「继续」令=批准, 三段式): westock recent-500窗口
        # 结构性截断(服务端分页损坏, SOP一百八十三⑥)随日单量增长侵蚀纪元覆盖→10/29实锤
        # (奥飞原始买单被挤出窗口→静默回退显示成本→误触发SELL_ALL 9300, 幸收盘清算自动撤单)。
        # 改读本地 trade_records 台账(对账真源, 全量成交无窗口上限, 覆盖20260722起;
        # 更早纪元沿用既有"重建不全保底显示口径+注记"语义)。奥飞回归: 10/21 17800@9.40+
        # 10/23 10000@9.04 → 均值9.2705与晨班逐位一致。
        con_pd = sqlite3.connect(r"file:D:\zcodesj\attribution\attribution.db?mode=ro", uri=True)
        records = con_pd.execute(
            "SELECT code, action, price, volume, created_at FROM trade_records "
            "WHERE action IN ('BUY', 'SELL') ORDER BY trade_date, created_at, id"
        ).fetchall()
        con_pd.close()
    except Exception as e:  # noqa: BLE001 — 数据源失败=全员保底显示口径（现行为）
        notes.append({"code": "-", "name": "全部", "basis": "westock_displayed",
                      "note": f"成交回报拉取失败({type(e).__name__}: {e})——全员保底显示口径"})
        return {}, notes
    per: dict[str, list[dict]] = {}
    for code, action, price, volume, created_at in records:
        c = norm_code(code)
        per.setdefault(c, []).append({
            "dir": str(action or "").upper(),
            "price": float(price or 0),
            "qty": int(float(volume or 0)),
            "at": str(created_at or ""),
            "status": "已成交",
        })
    out: dict[str, float] = {}
    for c, fills in per.items():
        held_qty = held.get(c, 0)
        if held_qty <= 0:
            continue
        fills.sort(key=lambda x: x["at"])
        pos = amt = qacc = sells = 0
        for f in fills:
            if f["status"] not in ("已成交", "部分成交", "成交"):
                continue
            if f["dir"] == "BUY":
                pos += f["qty"]
                amt += f["price"] * f["qty"]
                qacc += f["qty"]
            elif f["dir"] == "SELL":
                pos -= f["qty"]
                sells += 1
                if pos <= 0:
                    pos = amt = qacc = sells = 0  # 归零重开纪元
        if sells == 0 or pos <= 0 or qacc <= 0:
            continue  # 纪元内无卖出(显示口径无失真)或已清仓
        if abs(pos - held_qty) > max(2, held_qty * 0.02):
            notes.append({"code": c, "name": "", "basis": "westock_displayed",
                          "note": f"纪元重建对不上账(回报净持仓{pos}≠实持{held_qty})——保底显示口径"})
            continue
        out[c] = round(amt / qacc, 4)
    return out, notes


def check_stop_loss_positions(positions: list[dict],
                              notes_out: list[dict] | None = None) -> list[dict]:
    """扫描持仓，找出触发-8%止损的标的。

    返回 list[dict] 每项含完整止损信息，按亏损幅度降序排列（最紧急在前）。
    PD-040（2026-10-16）：部分卖出剩仓以原始买入成本判线（见 _partial_sell_cost_overlay），
    触发行带 cost_basis/displayed_pnl_pct 双口径字段；notes_out 回传口径注记（含未触发
    的覆盖码），供报告"S-5 口径注记"节与 stdout 留痕。
    """
    held = {p.get("code", ""): int(p.get("quantity") or 0) for p in positions}
    overlay, ovl_notes = _partial_sell_cost_overlay(held)
    if notes_out is not None:
        notes_out.extend(ovl_notes)
    triggers = []
    for pos in positions:
        cost = pos.get("cost_price", 0)
        current = pos.get("current_price", 0)
        qty = pos.get("quantity", 0)
        code = pos.get("code", "")
        name = pos.get("name", "")

        if not cost or not current or not qty:
            continue

        # PD-040: 部分卖出剩仓改用原始买入成本判线（显示口径摊入已实现亏损属重复计数）
        cost_basis = "westock_displayed"
        displayed_pnl_pct = None
        if code in overlay:
            displayed_pnl_pct = round((current - cost) / cost * 100, 2)
            cost = overlay[code]
            cost_basis = "original_fill_cost(PD-040)"

        # 计算浮亏百分比
        if cost > 0:
            pnl_pct = (current - cost) / cost
        else:
            pnl_pct = 0.0

        pnl_amount = (current - cost) * qty

        if pnl_pct <= STOP_LOSS_THRESHOLD:
            triggers.append({
                "code": code,
                "name": name,
                "cost_price": round(cost, 4),
                "current_price": round(current, 4),
                "quantity": qty,
                # 2026-09-10: 透传可卖数量(T+1), 卖单数量上限=availableQty, 防超卖
                "available_qty": pos.get("availableQty", qty),
                "pnl_pct": round(pnl_pct * 100, 2),
                "pnl_amount": round(pnl_amount, 2),
                "cost_basis": cost_basis,
                "displayed_pnl_pct": displayed_pnl_pct,
                "stop_loss_exceeded_by": round((abs(pnl_pct) - abs(STOP_LOSS_THRESHOLD)) * 100, 2),
            })
        elif notes_out is not None and code in overlay:
            # 原始口径未破线的部分卖出剩仓：显式注记"不触发"，防两口径并存再起争议（E22）
            notes_out.append({
                "code": code, "name": name, "basis": cost_basis,
                "original_cost": round(cost, 4),
                "pnl_original": round(pnl_pct * 100, 2),
                "pnl_displayed": displayed_pnl_pct,
                "note": f"原始口径未破线(线={round(cost*(1+STOP_LOSS_THRESHOLD), 4)})，"
                        f"不触发；显示口径因摊损虚高仅留痕",
            })
    
    # 按亏损幅度降序
    triggers.sort(key=lambda x: x["pnl_pct"])
    return triggers


def generate_sell_instructions(triggers: list[dict]) -> list[dict]:
    """为止损标的生成卖出指令。
    
    规则：
    - 现价 > 成本价时：卖一半（仍在趋势中，只减仓不止损）
    - 现价 <= 成本价时：全清
    """
    instructions = []
    for t in triggers:
        # 2026-09-10: 卖单数量以 availableQty 为上限(T+1 可卖), 防超卖; 无可卖则跳过并留痕
        avail = int(t.get("available_qty") or 0)
        if avail <= 0:
            print(f"⚠️ {t['code']} {t['name']}: availableQty=0(当日无可卖), 跳过止损派发",
                  file=sys.stderr)
            continue
        if t["current_price"] > t["cost_price"]:
            # 现价高于成本（虽触发止损但其实是回撤），减半
            sell_qty = min(avail, t["quantity"]) // 2
            if sell_qty < 100:
                sell_qty = min(avail, t["quantity"])  # 不足1手则全清
            action = "SELL_HALF"
        else:
            sell_qty = min(avail, t["quantity"])
            action = "SELL_ALL"
        
        instructions.append({
            "code": t["code"],
            "name": t["name"],
            "action": action,
            "sell_quantity": sell_qty,
            "current_price": t["current_price"],
            "cost_price": t["cost_price"],
            "pnl_pct": t["pnl_pct"],
            "pnl_amount": t["pnl_amount"],
            "reason": f"自动止损: 亏损{t['pnl_pct']}% 超-8%线{t['stop_loss_exceeded_by']}pp",
        })
    
    return instructions


def is_in_sell_window() -> bool:
    """是否在卖出窗口内(2026-08-20修复: 主人8/14裁决废除尾盘锁,全天可操作,恒True)。
    原实现引用已删的 SELL_WINDOW_START_HOUR 常量致 NameError,报告生成崩溃。"""
    return True


def scan_trailing_hints(positions: list[dict]) -> list[dict]:
    """v1.4 trailing 移动止盈提示检测（2026-08-28 新增，纯提示性，不生成卖出指令）。

    依据生产配置 v1.4：ml_signal 侧 trailing_stop_pct=0.10（浮盈>=10%后自峰值回撤10pp 了结）。
    本函数只做检测+提示（C8 先例：risk_hints 提示性不改变 verdict）——宪法铁律"卖多少必须问主人"，
    绝不自动执行；bp_ep/其他来源标注 NO_APPLY（bp_ep 侧 trailing 已回测证伪 -1.6%）。

    口径：峰值/现价均在 qfq 复权空间（以买入日 qfq 收盘为基准），规避持有期除权导致的原始价失真；
    现价为 qfq 最新收盘（T-1），盘中价 fresher 的差异如实标注。
    数据缺失（无买入日/qfq 无数据）→ 跳过该持仓（fail-safe，不猜）。
    """
    QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
    ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
    hints = []
    try:
        qfq = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
        attr = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    except sqlite3.Error as e:
        print(f"⚠️ trailing 提示检测数据源不可用: {e}", file=sys.stderr)
        return hints

    for pos in positions:
        code = pos.get("code", "")
        bare = code[-6:] if len(code) >= 6 else ""
        if not bare:
            continue
        # 买入日 = trade_records 该码最近一笔 BUY
        row = attr.execute(
            "SELECT trade_date FROM trade_records WHERE action='BUY' AND code=? "
            "ORDER BY id DESC LIMIT 1", (code,)).fetchone()
        if not row:
            continue
        entry_date = str(row[0])
        closes = qfq.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date",
            (bare, entry_date)).fetchall()
        if len(closes) < 2:
            continue
        entry_px = float(closes[0][0])
        if entry_px <= 0:
            continue
        peak_px = max(float(c[0]) for c in closes)
        cur_px = float(closes[-1][0])
        peak_pnl = (peak_px / entry_px - 1) * 100
        cur_pnl = (cur_px / entry_px - 1) * 100
        # 主人令 2026-11-07「不是硬止盈是波动止盈」+「稳健 vs 博弹性, 都要有, 但是要标注」:
        # 双线并存 —— 🔒稳健线8pp(早落袋, 实证胜率90.7%) / 🚀博弹性线10pp(让牛股跑, 实证均值+8.34%最优)。
        # 触稳健线即入列(更早), 同时标注博弹性线是否也已触发, 由主人选模式。
        if peak_pnl >= TRAIL_ACTIVATE_PCT:
            _dd = peak_pnl - cur_pnl
            hit_steady = _dd >= TRAIL_STEADY_PP
            hit_aggr = _dd >= TRAIL_AGGRESSIVE_PP
            if hit_steady or hit_aggr:
                _src = str(pos.get("source", "") or pos.get("_source", "") or "")
                _evidence = ("ml腿有历史正证据(M-3 trail10 净值级A/B +38.39pp)"
                             if "ml" in _src.lower()
                             else "该来源无 trailing 正证据(bp_ep侧曾回测-1.6%), 仅提示须人工判断")
                if hit_aggr:
                    _mode = "🚀博弹性(10pp)已触发"
                else:
                    _mode = "🔒稳健(8pp)已触发·博弹性(10pp)未触发"
                hints.append({
                    "code": code, "name": pos.get("name", ""), "source": _src,
                    "entry_date": entry_date,
                    "peak_pnl_pct": round(peak_pnl, 2),
                    "cur_pnl_pct": round(cur_pnl, 2),
                    "drawdown_pp": round(_dd, 2),
                    "hit_steady": hit_steady, "hit_aggressive": hit_aggr,
                    "mode": _mode,
                    "applicable": "MANUAL_CONFIRM(只提示不自动卖, 主人裁决)",
                    "evidence": _evidence,
                })
    qfq.close()
    attr.close()
    hints.sort(key=lambda x: x["drawdown_pp"], reverse=True)
    return hints


def output_report(
    triggers: list[dict],
    instructions: list[dict],
    dry_run: bool,
    date_str: str,
    trail_hints: list[dict] | None = None,
    cost_notes: list[dict] | None = None,
) -> str:
    """输出止损检查报告。"""
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"stop_loss_scan_{date_str}.md")
    
    lines = [
        f"# 止损自动扫描报告 {date_str}\n",
        f"**模式**: {'🔍 DRY-RUN（仅报告，不下单）' if dry_run else '⚡ 自动执行'}\n",
        f"**执行时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        f"**止损线**: {STOP_LOSS_THRESHOLD*100:.0f}%\n",
        f"**触发标的**: {len(triggers)} 只\n",
        f"**在尾盘窗口内**: {'✅ 是' if is_in_sell_window() else '❌ 否'}\n",
        "\n---\n",
    ]
    
    if not triggers:
        lines.append("✅ **无标的触发止损线**，继续正常持有。\n")
    else:
        lines.append(f"## 止损触发清单（按紧急度排列）\n\n")
        lines.append("| 代码 | 名称 | 成本价 | 现价 | 持仓 | 亏损% | 亏损金额 | 超线pp | 卖出数量 | 操作 |\n")
        lines.append("|------|------|--------|------|------|-------|----------|--------|----------|------|\n")
        for t, inst in zip(triggers, instructions):
            lines.append(
                f"| {t['code']} | {t['name']} | {t['cost_price']} | {t['current_price']} "
                f"| {t['quantity']} | **{t['pnl_pct']}%** | {t['pnl_amount']}元 "
                f"| {t['stop_loss_exceeded_by']}pp | {inst['sell_quantity']} | {inst['action']} |\n"
            )
        
        lines.append("\n### 卖出理由\n")
        for inst in instructions:
            lines.append(f"- **{inst['code']} {inst['name']}**: {inst['reason']}\n")
    
    if trail_hints:
        lines.append("\n---\n")
        lines.append("## 移动止盈提示（波动止盈·双线并存·纯提示不自动执行）\n")
        lines.append("> 主人令 2026-11-07：**不是硬止盈，是波动止盈**。浮盈达 "
                     f"{TRAIL_ACTIVATE_PCT:.0f}% 激活跟踪后，两条线同时对每只持仓计算：")
        lines.append(f"> · 🔒**稳健线 {TRAIL_STEADY_PP:.0f}pp** —— 早落袋，实证胜率 90.7%、中位 +6.98%（样本97只）")
        lines.append(f"> · 🚀**博弹性线 {TRAIL_AGGRESSIVE_PP:.0f}pp** —— 让大牛股跑远，实证均值 +8.34%（最优）、胜率 84.5%")
        lines.append("> 差异仅 0.39pp：稳健优先保胜率，博弹性优先吃右尾。上涨期间两条线都不动，只抬跟踪位=让利润奔跑。")
        lines.append("> 触**稳健线即入列**（更早），并标注博弹性线是否也已触发，由主人选模式。"
                     "卖多少仍须主人确认（宪法铁律），本段绝不自动卖。")
        lines.append("> 口径：qfq 复权空间（买入日收盘为基准），峰值为持仓期最高收盘。"
                     "证据标注：ml 腿有正证据（M-3 trail10 +38.39pp）；其他来源无正证据（bp_ep 侧曾 -1.6%），须人工判断。\n\n")
        lines.append("| 代码 | 名称 | 来源 | 买入日 | 峰值浮盈% | 当前浮盈% | 回撤pp | 模式 | 证据 | 适用性 |\n")
        lines.append("|------|------|------|--------|----------|----------|--------|------|------|--------|\n")
        for h in trail_hints:
            lines.append(
                f"| {h['code']} | {h['name']} | {h.get('source', '') or '-'} | {h['entry_date']} "
                f"| {h['peak_pnl_pct']}% | {h['cur_pnl_pct']}% | **{h['drawdown_pp']}pp** "
                f"| {h.get('mode', '')} | {h.get('evidence', '')} | {h['applicable']} |\n"
            )
        lines.append("\n**处置**：确认后走 trade_executor sell（须 --confirmed/主人特批），本工具绝不自动卖。\n")
    
    if cost_notes:
        lines.append("\n---\n")
        lines.append("## S-5 口径注记（PD-040，2026-10-16「继续」令定谳：部分卖出剩仓=原始买入成本）\n")
        lines.append("> westock 显示成本会把已实现亏损摊入剩余持仓（重复计数），仅对持仓纪元内"
                     "有过卖出的代码启用原始成本覆盖；无卖出代码显示口径无失真不动；重建不全保底"
                     "显示口径并如实注记。\n\n")
        lines.append("| 代码 | 名称 | 口径 | 原始成本 | 原始口径浮亏% | 显示口径浮亏% | 说明 |\n")
        lines.append("|------|------|------|----------|--------------|--------------|------|\n")
        for n in cost_notes:
            lines.append(
                f"| {n.get('code')} | {n.get('name', '')} | {n.get('basis')} "
                f"| {n.get('original_cost', '-')} | {n.get('pnl_original', '-')} "
                f"| {n.get('pnl_displayed', '-')} | {n.get('note', '')} |\n"
            )

    if not is_in_sell_window() and not dry_run:
        lines.append("\n---\n")
        lines.append("ℹ️ **当前不在尾盘窗口(14:45-14:55)**——主人8/14裁决任何时间可卖出，止损指令应立即执行，无需等待窗口。\n")
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    # 2026-10-31 补丁④(主人批准): 盘中断面留痕——当日多次扫描会覆盖同名报告
    # (10/30事故: 14:30盘中版被19:30重扫覆盖, 审计证据丢失)。规范文件名保留
    # (Sentinel_1430新鲜度检查依赖精确文件名), 另落一份带时分后缀的不可变快照副本。
    try:
        import time as _time
        _snap = os.path.join(REPORT_DIR,
                             f"stop_loss_scan_{date_str}_{_time.strftime('%H%M')}.md")
        with open(_snap, "w", encoding="utf-8") as f:
            f.writelines(lines)
        print(f"📄 盘中断面快照: {_snap}")
    except Exception:
        pass  # 快照失败不影响主报告与执行链

    print(f"📄 报告已输出: {report_path}")
    return report_path


def update_trade_state(instructions: list[dict]) -> None:
    """将卖出指令写入 trade_state，供执行器立即执行。"""
    try:
        from trade_state import load_state, save_state, norm_code
        state = load_state()
        state.setdefault("stop_loss_instructions", [])
        
        # 幂等写入：同一标的已存在则不重复追加
        existing_codes = {i["code"] for i in state["stop_loss_instructions"]}
        for inst in instructions:
            if inst["code"] not in existing_codes:
                state["stop_loss_instructions"].append(inst)
                state["sell_count"] = state.get("sell_count", 0) + 1
        
        save_state(state)
        print(f"✅ 止损指令已写入 trade_state: {len(instructions)} 条")
    except Exception as e:
        print(f"⚠️ trade_state 写入失败: {e}", file=sys.stderr)


def _load_open_sell_codes() -> set[str]:
    """读 open_orders.jsonl 中未了结的 SELL 委托代码(OPEN/PARTIAL)。

    2026-09-10 S-5 重复派发守卫: 14:30 链首单限价未成交(PENDING)时下一轮重扫仍会
    触发 → 对同一持仓再挂全量卖单 = 双委托超卖/双记(上海银行 9/7 撤+重挂双记同族)。
    派发前按码跳过已有未了结卖单的标的。断档陈化行(如 9/7/9/8 OPEN)同样拦截,
    由 fill_confirm_monitor cmd_expire 负责过期标注。
    """
    codes: set[str] = set()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "open_orders.jsonl")
    if not os.path.exists(path):
        return codes
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    r = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if r.get("action") == "SELL" and r.get("status") in ("OPEN", "PARTIAL"):
                    c = str(r.get("code") or "")
                    codes.add(c[-6:] if c else "")
    except OSError:
        pass
    return codes


def _attribution_has_sell(code: str, date_str: str) -> bool:
    """归因库当日 SELL 成交存在性检查（2026-09-23 宏桥002379 rc=1 假失败免疫）。

    9/22 事故：报文层 GBK 双崩致 rc=1，实单 09:41:15 已成交（复盘=s5_rc1_postmortem_20260923.md）。
    成交判定只认 westock+attribution.db（数据真相源裁决）；归因库 INSERT 仅在持仓差验证
    sold>0 后发生，故"归因库有当日 SELL 行"= 成交铁证。检查自身异常时返回 False（保持 ❌，
    不引入假阳性 OK）。
    """
    try:
        conn = sqlite3.connect(r"D:\zcodesj\attribution\attribution.db")
        try:
            row = conn.execute(
                "SELECT 1 FROM trade_records WHERE action='SELL' AND trade_date=? "
                "AND code LIKE ? LIMIT 1",
                (date_str, f"%{str(code)[-6:]}")).fetchone()
        finally:
            conn.close()
        return row is not None
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser(description="屠龙系统止损硬自动执行器 S-5")
    p.add_argument("--execute", action="store_true", help="执行模式（实际生成卖出指令）")
    p.add_argument("--dry-run", action="store_true", help="DRY-RUN 模式（仅报告不下单）")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="日期 YYYYMMDD（默认今日）")
    a = p.parse_args()
    
    if not a.execute and not a.dry_run:
        a.dry_run = True  # 默认 dry-run 安全
    
    date_str = a.date
    
    print(f"{'='*60}")
    print(f"  屠龙止损执行器 S-5 | {'🔍 DRY-RUN' if a.dry_run else '⚡ 执行模式'} | {date_str}")
    print(f"{'='*60}")
    
    # Step 1: 获取持仓
    print("\n📡 获取当前持仓...")
    positions = get_positions()
    if not positions:
        print("❌ 无法获取持仓数据，终止。")
        return 3
    print(f"   共 {len(positions)} 只持仓")
    
    # Step 2: 扫描止损
    print("\n🔍 扫描止损触发标的...")
    _cost_notes: list[dict] = []
    triggers = check_stop_loss_positions(positions, _cost_notes)
    # 2026-09-10 重复派发守卫: 已有未了结卖单的标的跳过(见 _load_open_sell_codes)
    _pending = _load_open_sell_codes()
    if _pending:
        _skipped = [t for t in triggers if t["code"] in _pending]
        if _skipped:
            for t in _skipped:
                print(f"   ⏭️  {t['code']} {t['name']}: 已有未了结卖出委托(OPEN/PARTIAL), 跳过重复派发")
            triggers = [t for t in triggers if t["code"] not in _pending]
    print(f"   触发 -8% 止损线: {len(triggers)} 只")
    for t in triggers:
        print(f"   ⚠️  {t['code']} {t['name']}: 亏损 {t['pnl_pct']}% (超线 {t['stop_loss_exceeded_by']}pp)"
              + (f" [口径={t.get('cost_basis')}, 显示口径{t.get('displayed_pnl_pct')}%]"
                 if t.get("cost_basis", "westock_displayed") != "westock_displayed" else ""))
    for n in _cost_notes:
        print(f"   📎 口径注记: {n.get('code')} {n.get('basis')} — {n.get('note', '')}")

    # v1.4 trailing 移动止盈提示检测（纯提示性）
    trail_hints = scan_trailing_hints(positions)
    if trail_hints:
        print(f"   📉 trailing 移动止盈提示(v1.4, 提示性): {len(trail_hints)} 只")
    
    if not triggers:
        print("✅ 无止损触发，无需操作。")
    
    # Step 3: 生成卖出指令
    instructions = generate_sell_instructions(triggers)
    
    # Step 4: 输出报告
    report_path = output_report(triggers, instructions, dry_run=a.dry_run, date_str=date_str,
                                trail_hints=trail_hints, cost_notes=_cost_notes)
    
    # Step 5: 写入 trade_state（供执行器立即执行）
    if not a.dry_run and instructions:
        update_trade_state(instructions)

    # Step 6: 执行卖出（2026-08-24 债务修复——闭环"S-5触发即执行不等待"铁律）
    # 旧实现只写 trade_state.stop_loss_instructions 指令文件，全项目无消费者，-8% 止损
    # 停留在报告层。现 --execute 模式逐条调用 trade_executor sell --is-stop-loss（仍走
    # pre_sell_check C1-C9 双层防线，非裸卖；--is-stop-loss 豁免 --confirmed 与时间窗）。
    n_ok = 0
    if not a.dry_run and instructions:
        import time
        print("\n⚡ Step 6: 通过 trade_executor 执行止损卖出...")
        # 2026-10-31 可观测性补丁(主人批准): 10/30事故15/15全失败且子进程stderr被丢弃,
        # 根因证据丢失 → ①失败时stderr/stdout落盘 ②卖出间隔4s(限频主嫌疑对冲)
        # ③0成功toast告警 ④全失败退出码2。不动任何交易语义/闸门/确认逻辑。
        stderr_log = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  "data", f"stop_loss_child_stderr_{date_str}.log")

        def _log_child_failure(code: str, name: str, r_or_err) -> None:
            try:
                with open(stderr_log, "a", encoding="utf-8") as f:
                    if isinstance(r_or_err, Exception):
                        f.write(f"[{code} {name}] EXCEPTION "
                                f"{type(r_or_err).__name__}: {r_or_err}\n\n")
                    else:
                        f.write(f"[{code} {name}] rc={r_or_err.returncode}\n"
                                f"--stdout--\n{(r_or_err.stdout or '')[-500:]}\n"
                                f"--stderr--\n{(r_or_err.stderr or '')[-2000:]}\n\n")
            except Exception:
                pass

        for idx, inst in enumerate(instructions):
            if idx:
                time.sleep(4)  # ②限频对冲: 10/30十五连发0成功 vs 10/29单笔成功的主嫌疑
            code, name = inst["code"], inst["name"]
            # 2026-11-04 主人批准(A2): 止损首笔挂价 = 扫描时现价×0.99 让价1% —— 急跌中按现价
            # 限价单无人接(11/2实测17只撤旧重挂滑点-6,800元=账户总盈利23%), 低于市价必成交,
            # 让价上限1%有界可审计; F1市价下限(市价×0.99)天然兼容。触发线/数量/范围不变。
            qty = int(inst["sell_quantity"])
            price = round(float(inst["current_price"]) * 0.99, 2)
            reason = f"S-5止损({inst['action']}) 亏损{inst['pnl_pct']}%"
            cmd = [sys.executable, r"D:\zcodesj\trade_executor.py", "sell",
                   "--code", code, "--name", name, "--price", str(price),
                   "--volume", str(qty), "--reason", reason,
                   "--execute", "--is-stop-loss"]
            try:
                # 2026-09-22: 子进程强制 PYTHONUTF8=1 —— 新系统(非UTF-8 Beta)子进程GBK输出
                # 会被utf-8解码崩掉(当日宏桥控股002379 S-5实弹事故), GBK坑族同源
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", timeout=120, shell=False,
                                   env=dict(os.environ, PYTHONUTF8="1"))
                ok = r.returncode == 0
                if not ok and _attribution_has_sell(code, date_str):
                    # 2026-09-23 免疫: rc≠0 但归因库已有当日成交=报文层假失败(9/22宏桥事故同型),
                    # 实单已成交, 按成功计; stdout 为空属预期(崩在打印层)。
                    print(f"   ⚠️→✅ {code} {name}: rc={r.returncode} 但归因库已有当日SELL成交"
                          f"=报文层假失败, 实单已成交")
                    ok = True
                if not ok:
                    # ①10/30补丁: 失败必须留下stderr证据(当日15/15失败仅打印空尾巴)
                    _log_child_failure(code, name, r)
                    print(f"   ❌ {code} {name}: rc={r.returncode} "
                          f"stderr尾={(r.stderr or '')[-160:].strip()}")
                else:
                    print(f"   ✅ {code} {name}: {(r.stdout or '')[-200:].strip()}")
                n_ok += 1 if ok else 0
            except Exception as e:
                _log_child_failure(code, name, e)
                print(f"   ❌ {code} {name}: 执行异常 {e}")
        print(f"   止损执行完成: {n_ok}/{len(instructions)} 成功")
        # ③10/30补丁: 全失败熔断告警(修复exit1无消费者缺陷) — 本机toast通道, 非手机推送
        if n_ok == 0:
            try:
                from notify import send_toast
                send_toast("屠龙止损链0成功",
                           f"Step6全失败 {n_ok}/{len(instructions)}, "
                           f"明细: data/stop_loss_child_stderr_{date_str}.log")
            except Exception:
                pass

    print(f"\n{'='*60}")
    print(f"  完成。{'触发标的需执行止损' if triggers else '无操作'}")
    print(f"{'='*60}")

    # 2026-09-10 修复: 原 `return 1 if triggers else 0` 无法区分「触发且执行成功」与
    # 「执行失败」, 且 dry-run 报告有触发也返 1 污染调用方判定。新语义:
    #   0 = 正常完成(无触发, 或 dry-run 报告, 或执行模式全部成功)
    #   1 = 执行模式有触发但部分失败(n_ok<总数) → 需人工跟进
    #   2 = 执行模式有触发但全部失败(n_ok=0) → 疑似执行链系统性故障, 最高优先人工跟进
    #       (2026-10-31补丁④, 10/30事故15/15全失败仅exit1无消费者)
    #   3 = 持仓获取/解析失败(既有 fail-closed, get_positions 内 exit)
    if a.dry_run:
        return 0
    if instructions:
        if n_ok == 0:
            return 2
        return 0 if n_ok >= len(instructions) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())