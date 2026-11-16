# -*- coding: utf-8 -*-
"""存量集中度+止损带拥挤+悬置时效监控（R19 影子版, 2026-09-20 落地）

来源: 第三期全维度自省 T1/T2/B2/B5 条款 (reports/deep_self_review_full_spectrum_20260920.md)
定位: 只读影子层, 零交易权零拦截; 输出预警与提请, 裁决权在主人。
  - T1 存量集中度: 单票 >15% 预警 / >20% 提请减仓决策; 同申万(THS)一级行业簇 >15% 预警
  - T2 止损带拥挤: 浮亏 ∈(-8%,-5%] 计数 ≥3 触发组合级预案提请(含跌至-8%增量亏损测算); 已破-8%单列
  - B2 悬置时效: 读结构化真源 data/pending_decisions_ledger.jsonl(open 项 aging>sla → OVERDUE 升级行;
    口径与权威检测器 pending_decisions_aging.py 一致, 本件为盘前第二入口; PENDING_DECISIONS.md 仅人类可读视图)
数据: westock 实时持仓(唯一真值) + industry_map_ths(workbuddyspace 基本面库, 只读) + qfq 库交易日序
阈值: 影子期常量置顶, 转拦截链前冻结(prereg)。
2026-09-20 数学学习落地(math_knowledge_full_study_20260920): 新增第五节组合结构数学段——
  有效持仓 1/HHI / 等相关近似组合σ(ρ=0.3常态 vs 0.7压力) / 前三大 RC 方差贡献;
  公式 owner = tulong_math_toolkit.py, 只读观察不改任何阈值(转拦截阈值与 PD-013 一并裁决)。
2026-09-20 数学落地第二轮: 新增第六节流动性 ILLIQ 段(Amihud 2002 = |涨跌幅|/成交额亿元)——
  监控持仓卖出执行风险(高ILLIQ=单位成交额推动价格变动大=冲击成本高) + 近5日>3×20日均值
  流动性恶化标记; 无绝对阈值锚(外部用法为横截面因子), 影子期组合内相对排序观察。
验收: 实弹首跑日志 + reports 产物 + 首日应命中 PD-01(弘信电子26%)与止损带6只。
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
WS_PATH = r"D:\workbuddyspace"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PENDING_LEDGER = os.path.join(ROOT, "data", "pending_decisions_ledger.jsonl")

# R19 影子期阈值(转拦截前冻结)
SINGLE_WARN = 15.0   # 单票市值占比 % 预警线
SINGLE_ESC = 20.0    # 单票占比 % 提请线
CLUSTER_WARN = 15.0  # 同一级行业簇占比 % 预警线
BAND_LO, BAND_HI = -8.0, -5.0   # 止损拥挤带 (浮亏%)
BAND_MIN_N = 3        # 带内最少只数触发组合预案
# PD-013 裁决(主人 2026-09-20 "跑满3日周三终裁"): 冻结阈值草案作影子期判读标准——
# 单票权重>20% 提请线(=SINGLE_ESC 既有) + RC@ρ0.3>40% 观察线(数学段打标); 买入增量闸 REJECT 语义周三定。
RC_OBS_DRAFT = 40.0   # RC@ρ0.3 观察线草案 %（PD-013 终裁前仅标注不拦截）

sys.path.insert(0, ROOT)
sys.path.insert(0, WS_PATH)
from tulong_math_toolkit import effective_positions, equicor_risk_shares, portfolio_vol_equicor
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
OUT_MD = os.path.join(ROOT, "reports", f"holdings_concentration_{datetime.now().strftime('%Y%m%d')}.md")


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
    return [{
        "code": p["code"][-6:], "name": p["name"],
        "qty": float(p.get("quantity") or 0),
        "cost": float(p.get("costPrice") or 0),
        "price": float(p.get("currentPrice") or 0),
        "profit": float(p.get("profit") or 0),
        "pr": float(p.get("profitRate") or 0),
        "mv": float(p.get("marketValue") or 0),
    } for p in pos]


def load_industry(codes: list[str]) -> dict[str, str]:
    """code -> THS 一级行业(三级串取首段); 库缺失/未覆盖显式标 NA, 不伪造"""
    out = {c: "NA" for c in codes}
    try:
        con = sqlite3.connect("file:" + FUND_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=30)
        for code, ind in con.execute(
                "SELECT code, industry FROM industry_map_ths"):
            if code in out and ind:
                out[code] = ind.split("-")[0]
        con.close()
    except Exception as e:
        print(f"[CONC-WARN] 行业库读取失败, 簇分析降级: {e}")
    return out


def load_trade_days(n: int = 15) -> list[str]:
    """最近 n 个交易日(yyyymmdd, 升序), qfq 库取浦发银行 600000 日期序; 失败返回空(时效检查降级)"""
    try:
        con = sqlite3.connect("file:" + QFQ_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=60)
        rows = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE code='600000' ORDER BY date DESC LIMIT ?", (n,))]
        con.close()
        return sorted(str(r) for r in rows)
    except Exception as e:
        print(f"[CONC-WARN] 交易日序读取失败, 悬置时效降级为自然日: {e}")
        return []


def qfq_vol20(codes: list[str]) -> dict[str, float]:
    """code -> 近20交易日日σ(小数)。qfq 主库 change_pct(百分数)/100; 样本<10 → NaN(不伪造);
    库失败 → 全 NaN(调用方降级)。"""
    nan_map = {c: float("nan") for c in codes}
    if not codes:
        return {}
    try:
        con = sqlite3.connect("file:" + QFQ_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=60)
        rows = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE code='600000' ORDER BY date DESC LIMIT 25")]
        if not rows:
            con.close()
            return nan_map
        dmin = min(rows)
        ph = ",".join("?" * len(codes))
        acc: dict[str, list[float]] = {c: [] for c in codes}
        for code, pct in con.execute(
                f"SELECT code, change_pct FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>=? ORDER BY date",
                tuple(codes) + (dmin,)):
            if pct is not None and code in acc:
                acc[code].append(float(pct) / 100.0)
        con.close()
        out = {}
        for c, xs in acc.items():
            if len(xs) >= 10:
                m = sum(xs) / len(xs)
                out[c] = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
            else:
                out[c] = float("nan")
        return out
    except Exception as e:
        print(f"[CONC-WARN] qfq σ20 读取失败, 数学段降级: {e}")
        return nan_map


def qfq_illiq20(codes: list[str]) -> dict[str, dict]:
    """code -> {illiq20, recent5, spike}: Amihud ILLIQ(%/亿元)=mean(|change_pct|/成交额亿元),
    近20交易日; spike=近5日均值>3×20日均值(流动性恶化)。amount 单位自动探测(元/千元);
    样本<10 → NaN; 库失败 → 全 NaN(调用方降级)。"""
    nan_map = {c: {"illiq20": float("nan"), "recent5": float("nan"), "spike": False} for c in codes}
    if not codes:
        return {}
    try:
        con = sqlite3.connect("file:" + QFQ_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=60)
        rows = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE code='600000' ORDER BY date DESC LIMIT 25")]
        if not rows:
            con.close()
            return nan_map
        dmin = min(rows)
        ph = ",".join("?" * len(codes))
        acc: dict[str, list[tuple[float, float]]] = {c: [] for c in codes}
        for code, pct, amt in con.execute(
                f"SELECT code, change_pct, amount FROM stock_daily_meta_qfq "
                f"WHERE code IN ({ph}) AND date>=? ORDER BY date", tuple(codes) + (dmin,)):
            if code in acc and pct is not None and amt:
                acc[code].append((abs(float(pct)), float(amt)))
        con.close()
        all_amt = sorted(a for v in acc.values() for _, a in v)
        med_amt = all_amt[len(all_amt) // 2] if all_amt else 0.0
        if med_amt > 1e7:      # amount 单位=元 → 换算亿元
            unit_div = 1e8
        elif med_amt > 1e4:    # amount 单位=千元 → 换算亿元
            unit_div = 1e5
        else:
            print(f"[CONC-WARN] amount 单位无法探测(中位={med_amt}), ILLIQ 段降级")
            return nan_map
        out = {}
        for c, xs in acc.items():
            if len(xs) >= 10:
                ill = [p / (a / unit_div) for p, a in xs]          # %/亿
                ill20 = sum(ill) / len(ill)
                rec5 = sum(ill[-5:]) / min(5, len(ill))
                out[c] = {"illiq20": ill20, "recent5": rec5,
                          "spike": ill20 > 0 and rec5 > 3 * ill20}
            else:
                out[c] = {"illiq20": float("nan"), "recent5": float("nan"), "spike": False}
        return out
    except Exception as e:
        print(f"[CONC-WARN] qfq ILLIQ 读取失败, 流动性段降级: {e}")
        return nan_map


def pending_escalations() -> list[str]:
    """读结构化真源 pending_decisions_ledger.jsonl, 返回 OVERDUE 升级行。
    aging 口径与 pending_decisions_aging.py(权威检测器, schtasks PendingAging_1545)一致:
    raised_date 之后(不含当日)的已收盘交易日数 > sla_trading_days → OVERDUE。"""
    if not os.path.exists(PENDING_LEDGER):
        return ["(pending_decisions_ledger.jsonl 不存在, B2 时效检查不可用)"]
    days = load_trade_days(30)
    today = datetime.now().strftime("%Y%m%d")
    max_closed = days[-1] if days else today  # qfq 已收盘口径; 无交易日序降级用今日
    lines: list[str] = []
    for line in open(PENDING_LEDGER, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("status") != "open":
            continue
        raised = str(d.get("raised_date", "")).replace("-", "")
        sla = d.get("sla_trading_days")
        if not raised or not isinstance(sla, int):
            continue
        if days:
            n_after = sum(1 for x in days if raised < x <= max_closed)
            overdue = n_after > sla
            label = f"{n_after}/{sla} 交易日"
        else:
            n_natural = (datetime.now() - datetime.strptime(raised, "%Y%m%d")).days
            overdue = n_natural > sla
            label = f"{n_natural} 自然日/{sla}(降级口径)"
        if overdue:
            lines.append(f"- 🔴 {d['id']} OVERDUE({label})：{d['title'][:80]}")
    return lines or ["- 全部在时效内"]


def main() -> int:
    try:
        positions = load_positions()
    except Exception as e:
        print(f"[CONC-FAIL] 持仓通道失败: {e}")
        return 2
    total_mv = sum(p["mv"] for p in positions)
    if total_mv <= 0:
        print("[CONC-FAIL] 持仓总市值≤0, 数据异常")
        return 2
    ind = load_industry([p["code"] for p in positions])

    # T1a 单票集中度
    singles = sorted(((p["mv"] / total_mv * 100, p) for p in positions), key=lambda x: -x[0])
    esc_single = [(pct, p) for pct, p in singles if pct > SINGLE_ESC]
    warn_single = [(pct, p) for pct, p in singles if SINGLE_WARN < pct <= SINGLE_ESC]

    # T1b 行业簇
    clusters: dict[str, dict] = {}
    for p in positions:
        g = clusters.setdefault(ind[p["code"]], {"mv": 0.0, "n": 0, "names": []})
        g["mv"] += p["mv"]; g["n"] += 1; g["names"].append(f"{p['name']}")
    cluster_rows = sorted(((g["mv"] / total_mv * 100, k, g) for k, g in clusters.items()
                           if k != "NA"), key=lambda x: -x[0])
    warn_cluster = [(pct, k, g) for pct, k, g in cluster_rows if pct > CLUSTER_WARN]

    # T2 止损带拥挤
    broken = [p for p in positions if p["pr"] <= BAND_LO]                 # 已破-8%(S-5域)
    band = [p for p in positions if BAND_LO < p["pr"] <= BAND_HI]         # 拥挤带
    def extra_loss_to_stop(p: dict) -> float:
        """从当前浮亏率再跌至-8%的增量亏损(元), 按 mv 比例折算"""
        cur = 1 + p["pr"] / 100.0
        return p["mv"] * (1 - 0.92 / cur) if cur > 0.92 else 0.0
    band_extra = sum(extra_loss_to_stop(p) for p in band)
    band_mv = sum(p["mv"] for p in band)
    crowd = len(band) >= BAND_MIN_N

    # B2 悬置时效
    esc_pending = pending_escalations()

    # ---- 五、组合结构数学段（9/20 数学学习落地, 只读观察, 不改阈值; 失败降级不阻断）----
    math_lines: list[str] = []
    n_eff: float | None = None
    sig03a = sig07a = 0.0
    try:
        vols = qfq_vol20([p["code"] for p in positions])
        valid = sorted(v for v in vols.values() if v == v)
        med = valid[len(valid) // 2] if valid else 0.02
        wts, sigmas, used_med = [], [], 0
        for pct, p in singles:  # singles 按占比降序
            v = vols.get(p["code"], float("nan"))
            if v != v:
                v = med
                used_med += 1
            wts.append(pct / 100.0)
            sigmas.append(v)
        n_eff = effective_positions(wts)
        d03 = portfolio_vol_equicor(wts, sigmas, 0.3)
        d07 = portfolio_vol_equicor(wts, sigmas, 0.7)
        sig03a, sig07a = d03 * 250 ** 0.5 * 100, d07 * 250 ** 0.5 * 100
        rc03 = equicor_risk_shares(wts, sigmas, 0.3)
        rc07 = equicor_risk_shares(wts, sigmas, 0.7)
        med_sig = sorted(sigmas)[len(sigmas) // 2] * 100
        math_lines = [
            "## 五、组合结构数学段（9/20 数学学习落地·只读观察, 阈值不涉）",
            f"- 观察线草案(PD-013 周三终裁): 单票权重>{SINGLE_ESC:.0f}% 提请 + **RC@ρ0.3>{RC_OBS_DRAFT:.0f}% 观察**",
            f"- **有效持仓 N_eff = 1/HHI ≈ {n_eff:.1f} 只**（名义 {len(positions)} 只——名义分散被集中度稀释后的真实风险宽度）",
            f"- 个股日σ20 中位 {med_sig:.2f}%｜样本<10 用中位回填 {used_med} 只（降级标注, 停牌/次新常态）",
            f"- 等相关近似组合年化σ：ρ=0.3（常态）≈ **{sig03a:.1f}%** → ρ=0.7（压力情景）≈ **{sig07a:.1f}%**"
            f"（危机期相关性聚升贡献 +{sig07a - sig03a:.1f}pct——组合波动的真正变量是 ρ 不是票数）",
            "- 前三大持仓方差贡献份额（RC, 等相关近似; RC>权重=风险超名义占比, ρ越低惩罚越重）:",
        ]
        for i in range(min(3, len(singles))):
            pct, p = singles[i]
            rc_flag = f" 🔴超RC观察线草案({RC_OBS_DRAFT:.0f}%)" if rc03[i] * 100 > RC_OBS_DRAFT else ""
            math_lines.append(
                f"  - {p['code']} {p['name']}｜权重 {pct:.1f}%｜RC@ρ0.3 **{rc03[i] * 100:.1f}%**｜RC@ρ0.7 {rc07[i] * 100:.1f}%{rc_flag}")
        math_lines.append("- 语义: 数学层第二意见, 不新增预警/提请; RC 转拦截阈值与 PD-013 一并裁决")
    except Exception as e:
        math_lines = [f"## 五、组合结构数学段：降级不可用（{e}）"]

    # ---- 六、流动性 ILLIQ 段（数学落地第二轮·只读观察; 失败降级不阻断）----
    illiq_lines: list[str] = []
    n_spike = 0
    try:
        ill = qfq_illiq20([p["code"] for p in positions])
        ranked = sorted(
            ((ill[p["code"]]["illiq20"], p) for p in positions
             if ill.get(p["code"]) and ill[p["code"]]["illiq20"] == ill[p["code"]]["illiq20"]),
            key=lambda x: -x[0])
        n_spike = sum(1 for p in positions
                      if ill.get(p["code"], {}).get("spike"))
        illiq_lines = [
            "## 六、流动性 ILLIQ 段（Amihud |涨跌幅|/成交额亿元, 数学落地第二轮·只读观察）",
            f"- ILLIQ 高 = 单位成交额推动的价格变动大 = 卖出冲击成本高（止损带/大额减仓的执行风险面）",
        ]
        for v, p in ranked[:5]:
            flag = " 🔴近5日流动性恶化(>3×20日均值)" if ill[p["code"]]["spike"] else ""
            illiq_lines.append(
                f"  - {p['code']} {p['name']}｜ILLIQ20 {v:.2f} %/亿｜近5日 {ill[p['code']]['recent5']:.2f}{flag}")
        illiq_lines.append(
            f"- 流动性恶化标记 {n_spike} 只｜无绝对阈值锚(外部用法为横截面因子), "
            f"影子期组合内相对排序观察, 转拦截另行裁决")
    except Exception as e:
        illiq_lines = [f"## 六、流动性 ILLIQ 段：降级不可用（{e}）"]

    # ---- 报告 ----
    L = [f"# 存量集中度+止损带拥挤+悬置时效监控（R19 影子·{datetime.now().strftime('%Y-%m-%d %H:%M')}）",
         "",
         f"> 持仓 {len(positions)} 只 ｜ 总市值 {total_mv:,.0f} 元 ｜ 只读影子零拦截 ｜ 阈值: 单票>{SINGLE_WARN}%预警/> {SINGLE_ESC}%提请, 簇>{CLUSTER_WARN}%, 带({BAND_LO}~{BAND_HI}%]≥{BAND_MIN_N}只 ｜ 裁决权在主人",
         ""]
    L.append(f"## 一、单票集中度（T1a）")
    for pct, p in singles[:5]:
        flag = "🔴提请减仓决策" if pct > SINGLE_ESC else ("🟡预警" if pct > SINGLE_WARN else "")
        if flag or pct > 8:
            L.append(f"- {p['code']} {p['name']}｜占比 {pct:.1f}%｜浮盈亏 {p['pr']:+.2f}%｜市值 {p['mv']:,.0f}｜{flag}")
    L.append("")
    L.append(f"## 二、行业簇（T1b, THS 一级）")
    for pct, k, g in cluster_rows[:6]:
        flag = "🟡簇预警" if pct > CLUSTER_WARN else ""
        L.append(f"- {k}｜{g['n']} 只｜占比 {pct:.1f}%｜{flag}｜成员: {'、'.join(g['names'][:8])}{'…' if g['n'] > 8 else ''}")
    na_n = clusters.get("NA", {}).get("n", 0)
    if na_n:
        L.append(f"- ⚠️ 行业未覆盖 {na_n} 只（industry_map_ths 缺失, 簇分析存在盲区）")
    L.append("")
    L.append(f"## 三、止损带拥挤（T2, B5）")
    if broken:
        L.append(f"**已破 -8% 线（S-5 链域, {len(broken)} 只）:**")
        for p in broken:
            L.append(f"- {p['code']} {p['name']}｜{p['pr']:+.2f}%｜市值 {p['mv']:,.0f}")
    L.append(f"**拥挤带 (-8%~-5%], {len(band)} 只, 合计市值 {band_mv:,.0f} 元（{band_mv/total_mv*100:.1f}%）"
             f"{' 🔴≥3只触发组合级预案提请' if crowd else ''}:**")
    for p in sorted(band, key=lambda x: x["pr"]):
        L.append(f"- {p['code']} {p['name']}｜{p['pr']:+.2f}%｜市值 {p['mv']:,.0f}｜再跌至-8%增量亏损 ≈{extra_loss_to_stop(p):,.0f} 元")
    if crowd:
        L.append(f"- **组合预案输入**: 6 只若全部打到 -8%, 增量亏损合计 ≈{band_extra:,.0f} 元（占组合 {band_extra/total_mv*100:.2f}%）")
    L.append("")
    L.append(f"## 四、悬置决策时效（B2, PENDING_DECISIONS.md）")
    L.extend(esc_pending)
    L.append("")
    L.extend(math_lines)
    L.append("")
    L.extend(illiq_lines)
    L.append("")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))

    crit = len(esc_single) > 0 or crowd
    print(f"[CONC-OK] {os.path.basename(OUT_MD)} | 单票超提请线={len(esc_single)} 预警={len(warn_single)} "
          f"| 簇预警={len(warn_cluster)} | 破线={len(broken)} 带内={len(band)}{' CROWD' if crowd else ''} "
          f"| 悬置超期={sum(1 for x in esc_pending if '🔴' in x)} | 行业NA={na_n} "
          f"| 数学段: N_eff={n_eff and round(n_eff, 1)} σp年@ρ0.3={sig03a:.1f}%/@ρ0.7={sig07a:.1f}% "
          f"| ILLIQ恶化={n_spike}")
    if crit:
        toast(f"存量风控影子: 单票超线{len(esc_single)} 止损带拥挤{len(band)}只 详 {os.path.basename(OUT_MD)}")
        print(f"[CONC-CRIT] 影子级🔴: " + "; ".join(f"{p['code']}{p['name']}{pct:.1f}%" for pct, p in esc_single))
    return 0


if __name__ == "__main__":
    sys.exit(main())
