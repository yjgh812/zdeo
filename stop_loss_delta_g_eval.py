# -*- coding: utf-8 -*-
"""stop_loss_delta_g_eval.py —— S-5(-8%)止损几何期望研究件（只读, 零交易权）

来源: reports/math_knowledge_full_study_20260920.md 域3计算2（主人令"运用到屠龙系统"落地）
数学: Δg = g_stop − g_full（对数收益口径）; 止损有益 ⇔ E[Δg]>0 ⇔ 左尾截断收益>鞭打成本。
      公式 owner = tulong_math_toolkit.stop_loss_delta_g; 本件只做取样与聚合。
样本: ①attribution trade_records 全历史 BUY→SELL 闭合片段 ②当前持仓(executor_orders 首笔BUY锚,
      与 sell_exit_20d_shadow 同源同锚)右删失开放片段。锚=入场日 qfq 收盘(影子内部一致口径)。
双口径: close(收盘击穿触发,保守) / low(低点击穿按止损价成交,近似限价单; 未计磁吸滑点)。
聚合: 均值/中位 Δg + Wilson 95%CI + Newey-West t。输出 data/stop_loss_dg_eval_*.json + reports md。
已知口径局限(诚实声明, 结论必须带): 无再入场/无T+1跳空建模/低点成交价=stop为乐观近似/
开放片段右删失存在存活偏差(未触发者占多数时 Δg 被拉向 0)/8月底 bootstrap 批锚点时刻近似。
用法: python stop_loss_delta_g_eval.py  退出码 0/2
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
from tulong_math_toolkit import (  # noqa: E402
    newey_west_tstat, stop_loss_delta_g, wilson_ci)
from sell_exit_20d_shadow import entry_dates, qfq_series  # noqa: E402  (复用唯一锚点/行情owner)

ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STOP = 0.92
OUT_JSON = os.path.join(ROOT, "data", f"stop_loss_dg_eval_{datetime.now().strftime('%Y%m%d')}.json")
OUT_MD = os.path.join(ROOT, "reports", f"stop_loss_delta_g_{datetime.now().strftime('%Y%m%d')}.md")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def closed_episodes() -> tuple[list[tuple[str, str, str]], list[str]]:
    """trade_records 全历史闭合片段 [(code, buy, sell)]; 同码多段支持; 数据缺口码单列。"""
    eps: list[tuple[str, str, str]] = []
    gaps: list[str] = []
    try:
        con = sqlite3.connect("file:" + ATTR_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=30)
        rows = con.execute(
            "SELECT code, action, trade_date FROM trade_records ORDER BY code, trade_date, rowid").fetchall()
        con.close()
    except Exception as e:
        print(f"[DG-WARN] trade_records 读取失败: {e}")
        return [], []
    open_ep: dict[str, str] = {}
    for code, action, td in rows:
        code, td = str(code)[-6:], str(td).replace("-", "")[:8]
        if action == "BUY" and code not in open_ep:
            open_ep[code] = td
        elif action == "SELL" and code in open_ep:
            eps.append((code, open_ep.pop(code), td))
    gaps = sorted(open_ep)  # 有BUY无SELL=数据缺口(历史曾持有现不在库), 不取样不伪造
    return eps, gaps


def position_codes() -> list[str]:
    try:
        from westock_client import WeStockClient
        sys.path.insert(0, r"D:\workbuddyspace")
        resp = WeStockClient().call("portfolio_paper_positions", {})
        pos = (resp.get("data") or {}).get("positions") or []
        return [str(p["code"])[-6:] for p in pos]
    except Exception as e:
        print(f"[DG-WARN] westock 持仓读取失败, 开放片段缺当前持仓校验: {e}")
        return []


def eval_path(series: list[tuple], buy: str, sell: str | None) -> tuple[list[float], list[float]] | None:
    """入场锚=首日收盘; 返回 (逐期简单收益, 逐期低点收益) 或 None(不可取样)"""
    idx0 = next((i for i, x in enumerate(series) if x[0] >= buy), None)
    if idx0 is None:
        return None
    if sell is not None:
        idx_end = max((i for i, x in enumerate(series) if x[0] <= sell), default=None)
        if idx_end is None or idx_end <= idx0:
            return None  # 同日买卖/无路径
    else:
        idx_end = len(series) - 1
    seg = series[idx0: idx_end + 1]
    if len(seg) < 3:
        return None
    rets = [seg[i][1] / seg[i - 1][1] - 1.0 for i in range(1, len(seg))]
    lows = [seg[i][2] / seg[i - 1][1] - 1.0 for i in range(1, len(seg))]
    return rets, lows


def agg(vals: list[float]) -> dict:
    n = len(vals)
    if n == 0:
        return {"n": 0}
    s = sorted(vals)
    pos = sum(1 for v in vals if v > 0)
    lo, hi = wilson_ci(pos, n)
    t = newey_west_tstat(vals) if n >= 5 else None
    return {"n": n, "mean": round(sum(vals) / n, 6), "median": round(s[n // 2], 6),
            "p_gt0": round(pos / n, 4), "wilson95": [round(lo, 4), round(hi, 4)],
            "nw_t": round(t, 3) if t is not None else None,
            "sum": round(sum(vals), 4)}


def main() -> int:
    eps_closed, gaps = closed_episodes()
    anchors = entry_dates()          # executor_orders 首笔 BUY(主) + trade_records(兜底), 与 exit20 同源
    held = set(position_codes())

    # 开放片段 = 当前持仓 ∩ 有锚; 闭合片段剔除当前持仓码避免双计(现行持仓码只用开放段)
    eps_open = sorted((c, anchors[c]) for c in held if anchors.get(c))
    eps_closed = [e for e in eps_closed if e[0] not in held]

    con = sqlite3.connect("file:" + QFQ_DB.replace("\\", "/") + "?mode=ro", uri=True, timeout=60)
    cache: dict[str, list[tuple]] = {}

    def series_of(code: str) -> list[tuple]:
        if code not in cache:
            cache[code] = qfq_series(con, code)
        return cache[code]

    rows: list[dict] = []
    skipped = 0
    for code, buy, sell in eps_closed:
        got = eval_path(series_of(code), buy, sell)
        if got is None:
            skipped += 1
            continue
        rets, lows = got
        dg_c, st_c = stop_loss_delta_g(rets, STOP)
        dg_l, st_l = stop_loss_delta_g(rets, STOP, lows)
        rows.append({"code": code, "type": "closed", "buy": buy, "sell": sell, "days": len(rets) + 1,
                     "dg_close": round(dg_c, 6), "stopped_close": st_c,
                     "dg_low": round(dg_l, 6), "stopped_low": st_l})
    for code, buy in eps_open:
        got = eval_path(series_of(code), buy, None)
        if got is None:
            skipped += 1
            continue
        rets, lows = got
        dg_c, st_c = stop_loss_delta_g(rets, STOP)
        dg_l, st_l = stop_loss_delta_g(rets, STOP, lows)
        rows.append({"code": code, "type": "open", "buy": buy, "sell": None, "days": len(rets) + 1,
                     "dg_close": round(dg_c, 6), "stopped_close": st_c,
                     "dg_low": round(dg_l, 6), "stopped_low": st_l})
    con.close()

    n_close = sum(1 for r in rows if r["type"] == "closed")
    n_open = len(rows) - n_close

    # 分层(数学落地第二轮; 纯描述性二次切分, 不设判据防数据窥探; 分层结论只作观察面)
    def stratify(key_fn):
        groups: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            groups[key_fn(r)].append(r["dg_low"])
        return {k: agg(v) for k, v in sorted(groups.items())}

    strata = {"by_year": stratify(lambda r: r["buy"][:4] + "(low口径)"),
              "by_type": stratify(lambda r: r["type"]),
              "by_hold": stratify(lambda r: "<=20d" if r["days"] <= 20 else ">20d")}

    out = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "stop": STOP,
        "sample": {"closed": n_close, "open_right_censored": n_open, "skipped_no_path": skipped,
                   "trade_records_gap_codes": gaps,
                   "held_without_anchor": sorted(c for c in held if not anchors.get(c))},
        "dg_close_all": agg([r["dg_close"] for r in rows]),
        "dg_low_all": agg([r["dg_low"] for r in rows]),
        "dg_close_stopped_only": agg([r["dg_close"] for r in rows if r["stopped_close"]]),
        "dg_low_stopped_only": agg([r["dg_low"] for r in rows if r["stopped_low"]]),
        "trigger_rate": {"close": round(sum(r["stopped_close"] for r in rows) / max(len(rows), 1), 4),
                         "low": round(sum(r["stopped_low"] for r in rows) / max(len(rows), 1), 4)},
        "strata_low": strata,
        "rows": rows,
        "caveats": ["无再入场建模", "无T+1跳空成交细节(低点口径成交价=stop为乐观近似, 磁吸/跌停卖不出未计)",
                    "开放片段右删失存活偏差(未触发者Δg=0拉向中性)", "8月底bootstrap批锚点时刻近似",
                    "qfq口径锚=入场日收盘(影子内部一致, 与成本价存在盘中偏差)"],
    }
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---- 报告(反臆想范式) ----
    L = [f"# S-5(-8%)止损几何期望研究：Δg = g_stop − g_full（{datetime.now().strftime('%Y-%m-%d')}）",
         "",
         f"> 来源 math_knowledge_full_study_20260920 域3计算2 ｜ 只读研究零交易权 ｜ 不改变 S-5 铁律",
         f"> 样本: 闭合片段 {n_close} + 开放(右删失) {n_open}, 跳过无路径 {skipped} ｜ 停={int(STOP * 100)}% ｜ "
         f"数据缺口码 {len(gaps)} 只(有BUY无SELL, 不取样) ｜ 持仓无锚 {len(out['sample']['held_without_anchor'])} 只",
         "",
         "## 数据",
         f"- 触发率: close口径 {out['trigger_rate']['close'] * 100:.1f}% / low口径 {out['trigger_rate']['low'] * 100:.1f}%",
         f"- Δg(close 全样本): {json.dumps(out['dg_close_all'], ensure_ascii=False)}",
         f"- Δg(low 全样本): {json.dumps(out['dg_low_all'], ensure_ascii=False)}",
         f"- Δg(close 仅触发者): {json.dumps(out['dg_close_stopped_only'], ensure_ascii=False)}",
         f"- Δg(low 仅触发者): {json.dumps(out['dg_low_stopped_only'], ensure_ascii=False)}",
         "",
         "## 分层（low 口径·纯描述性二次切分, 不设判据防数据窥探; 只作观察面）",
         f"- 按年份: {json.dumps(out['strata_low']['by_year'], ensure_ascii=False)}",
         f"- 按类型(闭合/开放): {json.dumps(out['strata_low']['by_type'], ensure_ascii=False)}",
         f"- 按持有期: {json.dumps(out['strata_low']['by_hold'], ensure_ascii=False)}",
         "",
         "## 口径局限(结论必须带)",
         *[f"- {c}" for c in out["caveats"]],
         "",
         "## 结论",
         "- 见 json 与终端输出; 判定 Δg 均值>0 且 Wilson95 下界>0.5 的触发占比才具方向性, "
         "NW t 仅作参考(零膨胀分布, 解释保守)。S-5 是否被量化支持以本文件为证据入口。",
    ]
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print(f"[DG-OK] 样本 闭合{n_close}+开放{n_open} 跳过{skipped} | 触发率 close "
          f"{out['trigger_rate']['close'] * 100:.1f}%/low {out['trigger_rate']['low'] * 100:.1f}%")
    for k in ("dg_close_all", "dg_low_all", "dg_close_stopped_only", "dg_low_stopped_only"):
        print(f"  {k}: {json.dumps(out[k], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
