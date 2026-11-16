# -*- coding: utf-8 -*-
"""持仓量价风险快照（2026-09-19 落地常驻件, 主人"按推荐继续"批准）

流程: westock实时持仓(WeStockClient) -> selection_preflight_check.py --codes 批量(子进程)
      -> 解析查四量价段 -> 与实时浮盈亏对照 -> 分层风险报告 + 🔴级toast
分层: A空头排列走坏候选 / B赢家乖离风险(>10%) / C逼近-8%止损(浮亏<=-6.5%) / D健康
定位: 只读提请层零交易权; 卖出裁决权在主人(三元裁决); S-5止损由铁律链自动执行。
调度: schtasks HoldingsRiskScan_1510 工作日15:10 (收盘后, qfq未日更则用T-1并标注)。
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
WS_PATH = r"D:\workbuddyspace"
sys.path.insert(0, ROOT)
sys.path.insert(0, WS_PATH)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_MD = os.path.join(ROOT, "reports", f"holdings_risk_scan_{datetime.now().strftime('%Y%m%d')}.md")


def toast(msg: str) -> None:
    try:  # 仅本机 toast, 照 9/8 裁决无外部推送
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"New-BurntToastNotification -Text '{msg}'"], capture_output=True, timeout=15)
    except Exception:
        pass


def load_positions() -> list[dict]:
    from westock_client import WeStockClient
    resp = WeStockClient().call("portfolio_paper_positions", {})
    data = resp.get("data") or {}
    pos = data.get("positions") or []
    if not pos:
        raise RuntimeError("westock 持仓返回空(通道异常或零持仓)")
    out = []
    for p in pos:
        # 9/21修复: qty=0残留行(当日已清仓)剔除——否则已卖票混入常态层伪画像
        try:
            if int(float(p.get("quantity") or 0)) <= 0:
                continue
        except (TypeError, ValueError):
            pass
        out.append({
            "code": p["code"][-6:], "name": p["name"],
            "profitRate": float(p.get("profitRate") or 0),
            "profit": float(p.get("profit") or 0),
            "mv": float(p.get("marketValue") or 0),
        })
    return out


def run_preflight(codes: list[str]) -> str:
    """子进程跑 preflight, 返回报告路径(最新一份)"""
    before = set(os.listdir(os.path.join(ROOT, "reports")))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "selection_preflight_check.py"),
                        "--codes", ",".join(codes)], capture_output=True, text=True,
                       cwd=ROOT, timeout=600, encoding="utf-8", errors="replace")
    if r.returncode not in (0, 2):  # PASS=0 / INCOMPLETE=2 都是报告产物; 其他=崩溃
        raise RuntimeError(f"preflight 崩溃 exit={r.returncode}: {(r.stderr or '')[-200:]}")
    after = set(os.listdir(os.path.join(ROOT, "reports")))
    newf = sorted(after - before)
    if not newf:
        # 回退最新 preflight 文件
        cands = [f for f in after if f.startswith("selection_preflight_")]
        newf = [max(cands)]
    return os.path.join(ROOT, "reports", newf[-1])


def parse_chg4(report_path: str) -> dict[str, dict]:
    """解析查四段: code -> {align, off, flag_warn}"""
    txt = open(report_path, encoding="utf-8").read()
    i = txt.find("查四")
    seg = txt[i:] if i >= 0 else ""
    out = {}
    for m in re.finditer(r"- (\d{6}) (\S+)（数据日[^\n]*\n(.*?)(?=\n- \d{6}|\n##)", seg, re.S):
        code, block = m.group(1), m.group(3)
        tim = next((l for l in block.split("\n") if "择时" in l), "")
        m2 = re.search(r"(多头|空头)\(MA20[^\n]*距MA20 ([+-][\d.]+)%", tim)
        if m2:
            out[code] = {"align": m2.group(1), "off": float(m2.group(2)),
                         "warn": "🟡" in tim or "🔴" in tim}
    return out


def blowoff_scan(codes: list[str]) -> dict[str, dict]:
    """爆量末段扫描(提案④, 2026-10-01 主人「测试后按推荐继续」批准, 呈现级减仓提示)。
    口径(六轮D实验 n=24,935 vs 60,000 冻结): vr120=vma5/vma120>=1.5 ∧ ret20>=20%
    → f5中位-2.27% vs 对照-0.21%(win差-8pp); ×prem<2% 升级「出货确认档」(f10 -1.58%)。
    只读提请, 非自动卖出; 卖出裁决权在主人。"""
    import sqlite3
    con = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)  # 2026-10-13 切wb唯一真源(按code逐只查, SOP一百三十二)
    out = {}
    for code in codes:
        rows = con.execute(
            "SELECT date, close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 130",
            (code,)).fetchall()
        con_rows = list(reversed(rows))
        if len(con_rows) < 125:
            continue
        closes = [r[1] for r in con_rows]
        vols = [r[2] for r in con_rows]
        vma5 = sum(vols[-5:]) / 5
        vma120 = sum(vols[-120:]) / 120
        vr = (vma5 / vma120) if vma120 else None
        ret20 = (closes[-1] / closes[-21] - 1.0) if closes[-21] else None
        if vr is None or ret20 is None:
            continue
        out[code] = {"vr120": round(vr, 2), "ret20_pct": round(ret20 * 100, 1),
                     "blowoff": bool(vr >= 1.5 and ret20 >= 0.20)}
    con.close()
    return out


def load_prem_median() -> float | None:
    """当日昨涨停溢价(prem_seal_daily owner 口径, 字段 prem_seal=均值百分数), 失败返回 None(降级不阻断)。"""
    try:
        j = json.load(open(os.path.join(ROOT, "reports", "prem_seal_latest.json"), encoding="utf-8"))
        v = j.get("prem_seal")
        if isinstance(v, (int, float)):
            return float(v)
        d = j.get("data") or {}
        v = d.get("prem_seal")
        if isinstance(v, (int, float)):
            return float(v)
    except Exception:
        pass
    return None


def main() -> int:
    try:
        positions = load_positions()
    except Exception as e:
        print(f"[HOLDSCAN-FAIL] 持仓通道失败: {e}")
        return 2
    codes = [p["code"] for p in positions]
    try:
        report = run_preflight(codes)
        ch4 = parse_chg4(report)
    except Exception as e:
        print(f"[HOLDSCAN-FAIL] preflight 失败: {e}")
        return 2

    A, B, C, D, NA = [], [], [], [], []
    # 波段引擎融合(2026-09-19): 每只持仓追加波段状态(顶部/底部得分)
    wave_state = {}
    for p in positions:
        try:
            r = subprocess.run([sys.executable, os.path.join(ROOT, "wave_feature_engine.py"),
                                "--code", p["code"], "--last", "1"],
                               capture_output=True, text=True, timeout=60,
                               encoding="utf-8", errors="replace")
            for line in (r.stdout or "").splitlines():
                if "底部区域特征强" in line or "顶部区域特征强" in line or "中段" in line:
                    wave_state[p["code"]] = line.split("|")[-1].strip()
                    break
        except Exception:
            pass
    # 组合波段相位(2026-09-21 进化周期15: 知识落地审计遗漏①接线, 知识源C3相位量能路径)
    phase_rows, phase_sum = [], None
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "swing_phase_probe.py")],
                           capture_output=True, text=True, timeout=180,
                           encoding="utf-8", errors="replace")
        pj = os.path.join(ROOT, "data", "swing_phase_holdings_latest.json")
        if r.returncode == 0 and os.path.exists(pj):
            pd_ = json.load(open(pj, encoding="utf-8"))
            phase_sum = pd_.get("summary")
            phase_rows = [x for x in pd_.get("records", []) if "err" not in x]
    except Exception:
        pass

    for p in positions:
        q = ch4.get(p["code"])
        if not q:
            NA.append(p)
            continue
        wv = wave_state.get(p["code"], "")
        dual = " ｜🔴双方法确认" if ("顶部区域特征强" in wv and abs(q["off"]) > 10 and p["profitRate"] > 10) else ""
        item = (p["code"], p["name"], q["align"], q["off"], p["profitRate"], wv, dual)
        if q["align"] == "空头":
            A.append(item)
        elif abs(q["off"]) > 10 and p["profitRate"] > 10:
            B.append(item)
        elif p["profitRate"] <= -6.5:
            C.append(item)
        else:
            D.append(item)

    L = [f"# 持仓量价风险快照（{datetime.now().strftime('%Y-%m-%d %H:%M')}·自动常驻件·含波段引擎）",
         "",
         f"> 持仓 {len(positions)} 只 ｜ 底层: {os.path.basename(report)} + wave_feature_engine(顶部/底部得分) ｜ 只读提请, 卖出裁决权在主人, S-5 铁律自动",
         ""]
    # 大盘前置(2026-09-19 主人指令书: 大盘点位作为前置条件)
    try:
        dc = [f for f in os.listdir(os.path.join(ROOT, 'reports')) if f.startswith('direction_census_')]
        if dc:
            latest = sorted(dc)[-1]
            for line in open(os.path.join(ROOT, 'reports', latest), encoding='utf-8').read().splitlines()[:8]:
                if '大盘前置' in line or '5年区间' in line:
                    L.append(line)
            L.append("")
    except Exception:
        pass
    def sec(title, items, note):
        L.append(f"## {title}（{len(items)} 只）")
        L.append(note)
        for code, name, align, off, pr, wv, dual in sorted(items, key=lambda x: x[4]):
            L.append(f"- {code} {name}｜{align}｜乖离 {off:+.1f}%｜浮盈亏 {pr:+.2f}%｜波段: {wv or '?'}{dual}")
        L.append("")
    sec("🔴 A 空头排列走坏候选", A, "> 趋势走坏提请的量价佐证面（trend_checker/主人裁决）")
    sec("🟠 B 赢家高乖离风险（乖离>10% 且浮盈>10%）", B, "> 利润回吐风险标注; 移动止盈已实证否决, 持有观望为纪律选择")
    sec("🟡 C 逼近 -8% 止损线（浮亏≤-6.5%）", C, "> S-5 自动链盯防, 无需裁决")
    sec("🟢 D 常态持仓", D, "")
    if phase_sum:
        L.append(f"## 组合波段相位（{phase_sum['n_ok']}/{phase_sum['n_total']} 只解析）")
        L.append(f"> {phase_sum['decl']} ｜ 上涨段 {phase_sum['up_segments']} 只(末段爆量特征 "
                 f"{phase_sum['up_end_vol']} 只"
                 + (f"/{phase_sum['up_end_pct']}%" if phase_sum['up_end_pct'] is not None else "")
                 + f") ｜ 下跌段 {phase_sum['down_segments']} 只(缩量衰竭 {phase_sum['down_exhaust']} 只)")
        ends = [x for x in phase_rows if x.get("flag") == "END"]
        if ends:
            L.append("- 🔴 末段爆量特征: " + "、".join(
                f"{x['code']}{x['name']}(涨段{x['seg_chg']:+.0f}%/vr{x['vr_now']:.1f})" for x in ends))
        L.append("")
    if NA:
        L.append(f"## ⚪ 量价解析失败 {len(NA)} 只")
        L.append(" ｜ ".join(f"{p['code']}{p['name']}" for p in NA))
        L.append("")
    # E 爆量末段减仓提示(提案④, 呈现级): vr120>=1.5 ∧ ret20>=20%; prem<2% 升级出货确认档
    try:
        bo = blowoff_scan(codes)
    except Exception as e:
        bo = {}
        print(f"[HOLDSCAN-WARN] blowoff_scan 失败(降级跳过E层): {e}")
    prem_med = load_prem_median()
    prem_weak = prem_med is not None and prem_med < 2.0
    name_of = {p["code"]: p["name"] for p in positions}
    bo_hits = [(c, v) for c, v in bo.items() if v["blowoff"]]
    if bo_hits:
        L.append(f"## 🟣 E 爆量末段减仓提示（{len(bo_hits)} 只, 提案④·呈现级）")
        prem_txt = (f"{prem_med:+.2f}%(<2% → 出货确认档)" if prem_weak else
                    f"{prem_med:+.2f}%(≥2% → 承接观察档)" if prem_med is not None else "N/A(未分级)")
        L.append(f"> 依据六轮D实验(n=24,935): 爆量末段后5日中位-2.27% vs 对照-0.21%; 当前昨涨停溢价(prem_seal_daily均值口径)={prem_txt}。"
                 f"只读提请非自动卖出; 卖出裁决权在主人。")
        for c, v in sorted(bo_hits, key=lambda x: -x[1]["vr120"]):
            tag = "🔴出货确认档" if prem_weak else "🟡承接观察档"
            L.append(f"- {c} {name_of.get(c, '')}｜vr120={v['vr120']}｜ret20={v['ret20_pct']:+.1f}%｜{tag}")
        L.append("")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print(f"[HOLDSCAN-OK] {OUT_MD} | A={len(A)} B={len(B)} C={len(C)} D={len(D)} NA={len(NA)} E={len(bo_hits)}")
    # 🔴级提醒: 空头+高乖离叠加(反弹末端) 或 双重逼近止损
    crit = [x for x in A if abs(x[3]) > 3] + [x for x in C if x[4] <= -7.5]
    if crit:
        toast(f"持仓风险: {len(crit)}只🔴级(空头+乖离/贴止损) 详 {os.path.basename(OUT_MD)}")
        print(f"[HOLDSCAN-CRIT] {len(crit)}只🔴级")
    if bo_hits and prem_weak:
        toast(f"爆量×溢价弱: {len(bo_hits)}只出货确认档(提案④提示) 详 {os.path.basename(OUT_MD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
