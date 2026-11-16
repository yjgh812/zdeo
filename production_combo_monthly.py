# -*- coding: utf-8 -*-
"""
生产组合月度调仓追踪脚本
=========================
v1.4 (2026-08-29): 纯 bp+ep_core 单腿 —— ml_signal 退役(权重=0)
  依据: round11/11b 三臂归因, ml 腿历史 +216% 为训练窗重叠样本内成绩, 真 WF 价值≈零
  (证据: reports/round11b_leakctl_20260829/ROUND11B_REPORT.md + wfa_trials 两条 FAIL)
- 每月1日运行: 生成最新信号 → 比对账本持仓 → 输出调仓建议
- 账本: tulong_production_paper.db
- 信号源: dragon_quant.db 最新日期
- 只生成建议，不自动下单（需主人确认后执行）

用法: python production_combo_monthly.py [--dry-run] [--apply]
"""
import sqlite3, sys, json
from datetime import datetime
import pandas as pd

# 2026-09-10: ml_scores_staleness_guard 下沉 _common 单一真源(日频白名单与月度调仓共用),
# 此处改为 re-export 保持既有调用点不变。
sys.path.insert(0, r"D:\zcodesj")
from _common import ml_scores_staleness_guard  # noqa: F401

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
LEDGER_DB = r"D:\zcodesj\tulong_production_paper.db"
CFG = json.load(open(r"D:\zcodesj\tulong_production_config.json", encoding="utf-8"))
M3_PREREG = r"D:\zcodesj\data\prereg_m3_trail_20260902.json"
FALSIFIED_RULES = r"D:\zcodesj\data\falsified_rules.json"

CAP = 100000

def get_ml_leg_overrides():
    """M-3 单旗复活守卫 (2026-09-03 接线; 证据链=8/28 PREREG PASS + 9/2 techgrid PASS
    a41fbe252a3af5b7641a + 9/3 净值级全史 A/B PASS)。

    ml 权重>0 时强制校验 M-3 门禁链唯一真源:
      - data/prereg_m3_trail_20260902.json: status=completed 且 result.verdict=PASS
      - data/falsified_rules.json rules.M-3: 存在且未被标 falsified
    任一不满足 -> 返回 None (fail-closed, ml 信号拒绝, 禁止裸 ml 放行)。

    注册原文「本结果仅在 round12 复活条款触发时生效」——本守卫只保证复活时登记参数
    (trailing 10%/hold 20) 与门槛不丢; 复活触发(round12 WF)与权重翻转属主人裁决域。
    """
    try:
        pr = json.load(open(M3_PREREG, encoding="utf-8"))
        if pr.get("status") != "completed" or pr.get("result", {}).get("verdict") != "PASS":
            print(f"[M-3守卫] prereg status={pr.get('status')!r} verdict={pr.get('result',{}).get('verdict')!r} 非 PASS/completed, fail-closed")
            return None
        fr = json.load(open(FALSIFIED_RULES, encoding="utf-8")).get("rules", {}).get("M-3") or {}
        if fr.get("status") == "falsified":
            print("[M-3守卫] registry M-3 已标 falsified, fail-closed")
            return None
        r12 = fr.get("round12_revival_gate") or {}
        if r12.get("verdict") != "PASS":
            # 主人显式复活分支 (2026-09-04): round12 FAIL 结论保留不篡改, 主人裁决=宪法唯一owner覆盖
            ov = fr.get("owner_revival") or {}
            if ov.get("status") == "revived":
                print(f"[M-3守卫] round12 verdict={r12.get('verdict')!r}(FAIL原文保留) 但主人显式复活登记生效"
                      f"({ov.get('date')} by {ov.get('by')}: {ov.get('directive')}) — 放行, 2026-11底回退复核点在册")
            else:
                print(f"[M-3守卫] round12 复活门禁 verdict={r12.get('verdict')!r} 非 PASS"
                      f"({r12.get('fail_reason', '未运行')}) 且无主人复活登记 -> ml 复活条款已关闭, fail-closed")
                return None
    except Exception as e:
        print(f"[M-3守卫] 真源读取失败({type(e).__name__}: {e}), fail-closed")
        return None
    fixed = (pr.get("grid", {}) or {}).get("fixed", {}) or {}
    return {
        "prereg_id": pr.get("id"),
        "trailing_stop_pct": 0.10,   # criterion.adopt 登记生产格 (trail=10, hold=20)
        "max_holding_bars": 20,
        "trail_activation_pct": fixed.get("trail_activation_pct", 10),
        "stop_loss_pct": fixed.get("stop_loss_pct", 0.08),
        "gate_fingerprint": "a41fbe252a3af5b7641a",
        "equity_ab_report": "reports/tulong_ab_m3_trail_equity_20260903/CONCLUSION.md",
    }

def get_regime_weights(cfg):
    """S3 环境自适应组合权重 (2026-08-26 v1.3 灰度).

    T-1 上证指数收盘 vs MA250(250日, min_periods=100) 定牛熊 -> 选权重档。
    enabled=False / 数据不足 / 查询异常 -> 回退 fallback(静态60/40)。
    消费方仅本脚本(月度采样: 调仓日定权重, 持有期内不变)。
    """
    rw = (cfg.get("production_combo") or {}).get("regime_weights") or {}
    fb = dict(rw.get("fallback") or {"bp+ep_core": 0.60, "ml_signal": 0.40})
    if not rw.get("enabled"):
        return dict(cfg["production_combo"]["weights"]), "static(config)"
    try:
        conn = sqlite3.connect(MAIN_DB)
        rows = conn.execute(
            "SELECT date, close FROM index_daily WHERE index_code='000001' "
            "AND date<=? ORDER BY date DESC LIMIT 260",
            (int(datetime.now().strftime("%Y%m%d")),)).fetchall()
        conn.close()
        closes = [r[1] for r in rows][::-1]
        if len(closes) < 100 or not closes[-1]:
            return fb, "fallback(指数数据不足100日)"
        ma250 = sum(closes[-250:]) / min(250, len(closes))
        last_date, last_close = rows[0][0], rows[0][1]
        regime = "bull" if last_close > ma250 else "bear"
        return dict(rw[regime]), f"{regime}(T-1={last_date} close={last_close:.0f} MA250={ma250:.0f})"
    except Exception as e:
        return fb, f"fallback({type(e).__name__}: {e})"

def norm(code):
    """6位代码 -> 交易代码"""
    if code.startswith(("6", "9")): return "sh" + code
    return "sz" + code

def get_latest_signals():
    """从 dragon_quant.db 取两个策略最新信号 (bp_ep 走 PIT 完整读取)"""
    import sys as _sys
    _sys.path.insert(0, r"D:\zcodesj\multifactor")
    import factor_loader
    conn = sqlite3.connect(MAIN_DB)
    si = conn.execute("SELECT code,name,is_st FROM stock_info").fetchall()
    names = {r[0]: r[1] for r in si}
    ok = {r[0] for r in si if (r[2] in (0, "0", None)) and not r[0].startswith(("688","689","8","4","92","43"))}

    # bp+ep (PIT完整读取, 2026-08-24切换, 修复报告季中安全过滤清空候选; 选股日=qfq价格真源)
    d = factor_loader.get_recent_qfq_date() or factor_loader.get_recent_trade_date(0)
    _ep = factor_loader.load_pit_factors("ep", start=d, end=d)
    _bp = factor_loader.load_pit_factors("bp", start=d, end=d)
    fund = _ep.merge(_bp, on=["code", "date"], suffixes=("_ep", "_bp"))
    fund = fund.rename(columns={"factor_value_ep": "ep", "factor_value_bp": "bp"})
    closes = factor_loader.get_close_map(d)
    fund["close"] = fund["code"].map(closes)
    fund = fund[fund["code"].isin(ok)].copy()
    fund = fund[(fund["ep"] > 0) & (fund["bp"] > 0)].dropna(subset=["ep", "bp", "close"]).copy()
    fund = fund[fund["close"] <= 30].copy()
    if len(fund) >= 20:
        fund["score"] = (fund["ep"].rank(pct=True) + fund["bp"].rank(pct=True)) / 2
        fund_top = fund.nlargest(20, "score")
        bp_top = [{
            "code": r["code"], "name": names.get(r["code"], ""), "close": float(r["close"]),
            "score": float(r["score"]), "source": "bp+ep_core", "signal_date": str(d)
        } for _, r in fund_top.iterrows()]
    else:
        # 2026-09-10: 候选不足 20 只时原静默空列表会制造「清仓建议」噪音(月末 PIT
        # 数据缺口场景), 显式告警并如实空(建议层不编造)。
        print(f"[WARN] bp+ep 候选池不足: 过滤后仅 {len(fund)} 只(<20) → bp_top 为空, "
              f"请核查 PIT 数据({d}) 完整性后再 --apply")
        bp_top = []

    # ml
    d2 = conn.execute("SELECT MAX(date) FROM stock_ml_scores_v22").fetchone()[0]
    _lvl, _msg = ml_scores_staleness_guard(d2, apply_mode=("--apply" in sys.argv),
                                           allow_stale=("--allow-stale-ml" in sys.argv))
    print(f"[ml新鲜度] {_msg}")
    if _lvl == "fail" and not ("--allow-stale-ml" in sys.argv):
        print("[FAIL] ml 分数陈旧, fail-closed 拒绝生成建议 (修复: python ml_scores_v22_incremental.py; 豁免: --allow-stale-ml)")
        sys.exit(2)
    ml = conn.execute("SELECT code,ml_prob FROM stock_ml_scores_v22 WHERE date=?", (d2,)).fetchall()
    ml = [r for r in ml if r[0] in ok and r[1] is not None]
    ml_top = [{
        "code": r[0], "name": names.get(r[0], ""),
        # 2026-09-10: close 改从 qfq 收盘映射取真值(此前恒 None, --apply 记账用 10 元
        # 占位价=宪法禁固定值冒充, 记账成本失真)
        "close": closes.get(r[0]),
        "score": r[1], "source": "ml_signal", "signal_date": d2
    } for r in sorted(ml, key=lambda x: -x[1])[:20]]
    conn.close()
    return bp_top, ml_top

def main():
    apply = "--apply" in sys.argv
    print(f"=== 生产组合月度调仓追踪 {datetime.now().strftime('%Y-%m-%d')} ===")
    WEIGHTS, regime_tag = get_regime_weights(CFG)
    print(f"组合权重: bp+ep {WEIGHTS['bp+ep_core']:.0%} / ml {WEIGHTS['ml_signal']:.0%}  [{regime_tag}]")
    bp_top, ml_top = get_latest_signals()
    if WEIGHTS.get("ml_signal", 0) <= 0:
        # v1.4 (2026-08-29): ml 腿退役。忽略 ml 信号 → 既有 ml 持仓自然落入建议卖出、不再产生 ml 买入
        if ml_top:
            print(f"[v1.4] ml_signal 权重=0(退役), 忽略 {len(ml_top)} 只 ml 信号")
        ml_top = []
    else:
        # M-3 单旗复活守卫 (2026-09-03): 权重>0 必须先过 M-3 门禁链校验, fail-closed
        ml_overrides = get_ml_leg_overrides()
        if ml_overrides is None:
            print("[M-3] ml 权重>0 但门禁链校验失败 -> fail-closed, ml 信号拒绝")
            ml_top = []
        else:
            print(f"[M-3] ml 腿复活参数已携带: trailing={ml_overrides['trailing_stop_pct']:.0%}"
                  f"(激活阈{ml_overrides['trail_activation_pct']:.0%}内锁)/hold={ml_overrides['max_holding_bars']}bars"
                  f"/止损{ml_overrides['stop_loss_pct']:.0%} | prereg={ml_overrides['prereg_id']}"
                  f" 指纹={ml_overrides['gate_fingerprint']}")
            print("[M-3] 复活前置清单: ①round12 WF 触发(注册条款) ②日线 trailing 监控器补建(月频调仓无法执行日内 trailing) "
                  "③启用仍须过 R1-R18 闸门+名单制 ④2026-11 底回退条款复核点")
    signals = bp_top + ml_top
    print(f"信号: bp+ep {len(bp_top)}只 + ml {len(ml_top)}只")

    # 读取账本
    ldb = sqlite3.connect(LEDGER_DB)
    cash = ldb.execute("SELECT cash FROM portfolio ORDER BY id DESC LIMIT 1").fetchone()[0]
    positions = ldb.execute("SELECT code,name,shares,cost,source,weight FROM positions").fetchall()
    pos_codes = {p[0]: p for p in positions}
    print(f"账本: 现金 {cash:,.0f} | 持仓 {len(positions)}只")

    # 比对: 持仓中不在新信号清单的 = 建议卖出; 信号中未持仓的 = 建议买入
    sig_codes = {s["code"] for s in signals}
    suggest_sell = [p for p in positions if p[0] not in sig_codes]
    suggest_buy = [s for s in signals if s["code"] not in pos_codes]

    print(f"\n建议卖出 {len(suggest_sell)} 只(持仓不在最新信号):")
    for p in suggest_sell:
        print(f"  {p[0]} {p[1]} 成本{p[2]:.2f} ({p[4]})")
    print(f"\n建议买入 {len(suggest_buy)} 只(信号新进):")
    for s in suggest_buy:
        print(f"  {s['code']} {s['name']} score={s['score']:.4f} ({s['source']})")

    # 记录信号快照
    ldb.executemany("INSERT OR IGNORE INTO signal_snapshots(snap_date,code,name,source,score,close,status) VALUES(?,?,?,?,?,?,?)",
        [(datetime.now().strftime('%Y-%m-%d'), s["code"], s["name"], s["source"], s["score"], s["close"], "pending") for s in signals])
    ldb.commit()

    if apply:
        # --apply 模式: 执行持仓同步(仅记录调仓流水, 不真实下单)
        for p in suggest_sell:
            ldb.execute("DELETE FROM positions WHERE code=?", (p[0],))
            ldb.execute("INSERT INTO trades(ts,code,name,side,price,shares,amount,source,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec='seconds'), p[0], p[1], "sell", p[2], p[3], p[2]*p[3], p[4], "monthly_rebalance"))
        for s in suggest_buy:
            price = s["close"]
            # 2026-09-10: 无 qfq 收盘价 → 跳过记账并显式告警(禁 10 元占位价冒充,
            # 宪法§7 生产路径禁止固定值)
            if not price or float(price) <= 0:
                print(f"[WARN] {s['code']} {s['name']} 无 qfq 收盘价, 跳过账本记账(不编造价格)")
                continue
            price = float(price)
            target = CAP * WEIGHTS[s["source"]] / len([x for x in signals if x["source"]==s["source"]])
            shares = int(target / price / 100) * 100
            if shares >= 100:
                ldb.execute("INSERT OR REPLACE INTO positions(code,name,shares,cost,source,weight,buy_date,signal_date) VALUES(?,?,?,?,?,?,?,?)",
                    (s["code"], s["name"], shares, price, s["source"], WEIGHTS[s["source"]], datetime.now().strftime('%Y-%m-%d'), s["signal_date"]))
                ldb.execute("INSERT INTO trades(ts,code,name,side,price,shares,amount,source,reason) VALUES(?,?,?,?,?,?,?,?,?)",
                    (datetime.now().isoformat(timespec='seconds'), s["code"], s["name"], "buy", price, shares, price*shares, s["source"], "monthly_rebalance"))
        cash_new = cash - sum(p[2]*p[3] for p in suggest_sell)  # 简化: 不重算买入占用
        print("\n--apply: 账本持仓已同步(模拟调仓记录)")
    else:
        print("\n(未执行 --apply, 仅生成建议。确认后加 --apply 同步账本)")

    ldb.close()

if __name__ == "__main__":
    main()