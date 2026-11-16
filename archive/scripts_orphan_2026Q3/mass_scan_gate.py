# -*- coding: utf-8 -*-
"""mass_scan_gate.py — 全市场扫描 → 过闸门 → 输出通过者（双模式）

默认模式 (原逻辑): 新浪全市场涨幅2-9.5%、价≤30 → 量比≥1.2 → 过 strategy_gate
--dip-mode (2026-08-20 新增): 低吸分位模式
  本地 qfq 库算 60 日分位 → 冲高回落 + 低位分位(≤40%) → 纪律过滤 → 过 strategy_gate
  (移植 strong-stock-dip-scan 的确定性漏斗, 数据源改用屠龙本地 qfq 库, 零网络更稳)

用法:
  python mass_scan_gate.py                          # 默认模式(追涨候选)
  python mass_scan_gate.py --dip-mode --top 30      # 低吸分位模式
  python mass_scan_gate.py --dip-mode --price-cap 20
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"D:\zcodesj")

from gate_data_fetcher import get_gate_data

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"

# dip-mode 参数 (与 strong-stock-dip-scan scan_funnel 对齐)
PCT_GREEN = 0.40      # 低位绿灯阈值: 60日分位≤40%
PCT_RED = 0.70        # 高位红灯(剔除): 分位>70%
MIN_60D = 45          # 60日窗口最小K线数
PULLBACK_PCT = 0.15   # 从60日高点回落 ≥15% 才算"冲高回落"结构
MAX_TOP = 30          # dip候选最大数


def _load_mainlines() -> list[str]:
    """2026-08-24 接线: 从最新 daily_review_*.json 的 main_sectors 读当日主线板块，
    替代此前硬编码的 8/17 过期主线（污染 G8/D3 方向加分）。失败返回 []（中性不加分）。"""
    import glob as _g
    import json as _json
    import re as _re
    files = sorted(_g.glob(r"D:\zcodesj\reports\daily_review_*.json"))
    if not files:
        return []
    try:
        d = _json.load(open(files[-1], encoding="utf-8"))
        sectors = d.get("main_sectors") or []
        out = []
        for s in sectors:
            name = _re.split(r"[（(]", str(s))[0].strip()
            if name:
                out.append(name)
        return out[:6]
    except Exception:
        return []


MAINLINES: list[str] = _load_mainlines()  # 当日主线(数据源=最新daily_review; 失败空=中性不加分)

HELD_HARD = {'002414', '600721', '600267', '000735', '002167', '300077', '600726',
             '300017', '002119', '002378', '603881', '002491', '002104', '600186',
             '002185', '002322', '002194', '600719', '300655', '600776', '002724',
             '601991', '600667', '002418', '601609'}


def current_positions() -> set[str]:
    """主库 positions 表当前持仓(6位码)"""
    try:
        con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
        rows = [r[0] for r in con.execute(
            "SELECT code FROM positions WHERE quantity > 0").fetchall()]
        con.close()
        return {c[-6:] for c in rows}
    except Exception as e:
        # 2026-08-24 修复: 库不可用时静默回退25只旧快照冒充真值(既误剔旧票又不剔真持仓),
        # 违背宪法禁固定值冒充——诚实中止(主库positions当前健康,21只)
        raise SystemExit(f"[abort] positions 主库不可用, 拒用硬编码快照: {e}")


def load_stock_info() -> pd.DataFrame:
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    info = pd.read_sql_query(
        "SELECT code, name, is_st, industry, market FROM stock_info", con)
    con.close()
    # stock_info 可能同 code 多行(历史记录), 去重取最新一条
    info = info.sort_values("code").drop_duplicates("code", keep="last")
    # 排除混入的指数代码(如 上证全指 000047, market='其他')
    info = info[~info["market"].isin(["其他", ""])].copy()
    return info


def read_qfq_recent(days: int = 160) -> pd.DataFrame:
    """读取 qfq 库最近 N 个交易日的 code/date/close/high/low"""
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    df = pd.read_sql_query(
        f"SELECT code, date, close, high, low FROM stock_daily_meta_qfq "
        f"WHERE date >= (SELECT MIN(date) FROM ("
        f"  SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date DESC LIMIT {days}))",
        con)
    con.close()
    df["date"] = df["date"].astype(str)
    return df


def classify(code: str) -> str:
    """返回板类型: main / cyb(创业板) / star(科创) / bse(北交所) / other"""
    if code.startswith(("300", "301")):
        return "cyb"
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("8", "920", "430")):
        return "bse"
    if code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return "main"
    return "other"


def dip_funnel(df: pd.DataFrame, info: pd.DataFrame,
               price_cap: float = 30.0, max_top: int = MAX_TOP) -> list[dict]:
    """确定性漏斗: 60日分位 + 冲高回落 + 纪律过滤 → 低吸候选"""
    info_map = info.set_index("code").to_dict("index")
    # 只处理 stock_info 中的真实A股(排除 qfq 库混入的指数/基金/转债代码)
    stock_codes = set(info_map.keys())
    surv = []
    total = 0
    for code, g in df.groupby("code"):
        if code not in stock_codes:
            continue
        g = g.sort_values("date")
        if len(g) < MIN_60D:
            continue
        win = g.tail(60)
        lo = float(win["low"].min())
        hi = float(win["high"].max())
        cur = float(win["close"].iloc[-1])
        if hi <= lo or cur <= 0:
            continue
        pct = (cur - lo) / (hi - lo)
        hi_ago = float(win["high"].max())
        pull = 1 - cur / hi_ago if hi_ago else 0.0
        # 结构: 高位红灯剔除, 低位绿灯保留; 必须从高点明显回落(冲高回落)
        if pct > PCT_RED:
            continue
        if pct > PCT_GREEN:
            continue
        if pull < PULLBACK_PCT:
            continue
        total += 1
        # 纪律过滤 (注意: stock_info.is_st 是字符串 '0'/'1'/None, 不能用 bool())
        i = info_map.get(code, {})
        if str(i.get("is_st") or "0") == "1":
            continue
        board = classify(code)
        if board in ("star", "bse", "other"):
            continue
        if cur > price_cap or cur <= 0:
            continue
        surv.append({
            "code": code,
            "name": i.get("name", ""),
            "price": round(cur, 2),
            "pct_60": round(pct, 4),
            "pullback": round(pull, 4),
            "board": board,
        })
    surv.sort(key=lambda x: x["pct_60"])  # 分位越低越优先(安全边际越高)
    print(f"[dip] 结构命中(冲高回落+低位) {total} 只 → 纪律过滤后 {len(surv)} 只 → Top{min(max_top, len(surv))}")
    if not surv:
        # 诊断: 定位被哪一步全灭
        st_cnt = over_price = over_board = 0
        for code, g in df.groupby("code"):
            g = g.sort_values("date")
            if len(g) < MIN_60D:
                continue
            win = g.tail(60)
            lo, hi = float(win["low"].min()), float(win["high"].max())
            cur = float(win["close"].iloc[-1])
            if hi <= lo or cur <= 0:
                continue
            pct = (cur - lo) / (hi - lo)
            hi_ago = float(win["high"].max())
            pull = 1 - cur / hi_ago if hi_ago else 0.0
            if pct > PCT_RED or pct > PCT_GREEN or pull < PULLBACK_PCT:
                continue
            i = info_map.get(code, {})
            if str(i.get("is_st") or "0") == "1":
                st_cnt += 1
                continue
            board = classify(code)
            if board in ("star", "bse", "other"):
                over_board += 1
                continue
            if cur > price_cap or cur <= 0:
                over_price += 1
        print(f"[dip] 诊断: ST={st_cnt} 板型剔除={over_board} 超价={over_price} | price_cap={price_cap}")
    return surv[:max_top]


def run_gate(candidates_for_gate: list[dict], no_gate_state: bool = False) -> tuple[list, list]:
    """过 strategy_gate, 返回 (passed, rejected)"""
    if no_gate_state:
        # dip-mode 扫描: 逐候选独立评估, 质量闸门(G1-G18硬底线+评分)全量生效,
        # 但 G11冷却/G16级联 不累计(实盘连推防误伤; 扫描只判质量, 不污染实盘状态)
        import strategy_gate as sg
        results = []
        for cand in candidates_for_gate:
            r = sg.evaluate_candidate(cand, gate_state={})
            results.append(r)
        passed = [r for r in results if r.get("verdict") in ("PASS", "PASS_WITH_WARN")]
        rejected = [r for r in results if r.get("verdict") == "REJECT"]
        rejected.sort(key=lambda x: -len(x.get("hard_fails", [])))
        return passed, rejected

    cand_file = r"D:\zcodesj\.gate_logs\mass_gate_input.json"
    with open(cand_file, "w", encoding="utf-8") as f:
        json.dump(candidates_for_gate, f, ensure_ascii=False, indent=2)
    result = subprocess.run(
        [sys.executable, r"D:\zcodesj\strategy_gate.py", "--candidates", cand_file],
        capture_output=True, text=True, cwd=r"D:\zcodesj")
    output = result.stdout
    json_start = output.find("{")
    if json_start < 0:
        print("❌ 闸门输出解析失败")
        return [], []
    gate_result = json.loads(output[json_start:])
    passed = [r for r in gate_result.get("results", []) if r.get("verdict") == "PASS"]
    rejected = [r for r in gate_result.get("results", []) if r.get("verdict") == "REJECT"]
    rejected.sort(key=lambda x: -len(x.get("hard_fails", [])))
    return passed, rejected


def scan_default_mode(available_cash, total_assets, holdings):
    """原逻辑: 新浪全市场涨幅2-9.5%、价≤30 → 量比≥1.2 → 过闸门"""
    import requests
    url = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
    all_stocks = []
    for page in range(1, 6):
        params = {'page': page, 'num': 100, 'sort': 'changepercent', 'asc': 0, 'node': 'hs_a'}
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            if not data:
                break
            for item in data:
                code = item.get('code', '')
                price = float(item.get('trade', 0))
                chg = float(item.get('changepercent', 0))
                if 0 < price <= 30 and 2 <= chg < 9.5:
                    all_stocks.append(code)
        except Exception:
            break
        time.sleep(0.5)
    print(f"全市场初步候选: {len(all_stocks)}只")
    all_stocks = [c for c in all_stocks if c not in holdings]
    print(f"剔除持仓: {len(all_stocks)}只")

    print("\n=== gate_data_fetcher 逐批取数 ===")
    batch_size = 20
    all_data = {}
    for i in range(0, len(all_stocks), batch_size):
        batch = all_stocks[i:i + batch_size]
        try:
            data = get_gate_data(batch, source='full_market_scan',
                                 mainlines=MAINLINES)
            all_data.update(data)
        except Exception as e:
            print(f"  批次{i // batch_size + 1}失败: {e}")
        time.sleep(0.5)
    with_vr = {k: v for k, v in all_data.items() if v.get('volume_ratio') and v['volume_ratio'] >= 1.2}
    print(f"量比≥1.2: {len(with_vr)}只")

    candidates_for_gate = []
    for code, d in with_vr.items():
        candidates_for_gate.append({
            'code': code, 'name': d.get('name', ''), 'price': d.get('price', 0),
            'change_pct': d.get('change_pct', 0), 'volume_ratio': d.get('volume_ratio', 0),
            'hot_rank': d.get('hot_rank', 999), 'hot_tag': d.get('hot_tag', ''),
            'tags': d.get('tags', []), 'limit_up_streak': d.get('limit_up_streak', 0),
            'pct_20d': d.get('pct_20d'), 'avg_amplitude_20d': d.get('avg_amplitude_20d'),
            'daily_limit': d.get('daily_limit', 0), 'board_type': d.get('board_type', '主板'),
            'concept': d.get('concept', ''),
            'mainlines': MAINLINES,
            'source': 'full_market_scan', 'amount': available_cash,
            'available_cash': available_cash, 'total_assets': total_assets,
        })
    return candidates_for_gate


def scan_dip_mode(price_cap, max_top, available_cash, total_assets, holdings):
    """dip-mode: 本地qfq算60日分位 → 冲高回落低吸候选 → 过闸门"""
    print("=== [dip-mode] 低吸分位扫描 ===")
    df = read_qfq_recent(days=160)
    info = load_stock_info()
    print(f"[dip] qfq 面板 {df.shape[0]} 行, 股票 {df['code'].nunique()} 只")
    picks = dip_funnel(df, info, price_cap=price_cap, max_top=max_top)
    picks = [p for p in picks if p["code"] not in holdings]
    print(f"[dip] 剔除持仓后 {len(picks)} 只")

    if not picks:
        print("⚠️ 今日无低吸分位候选(全部高位或未回落)")
        return []

    # 逐批补全闸门字段
    print("\n=== gate_data_fetcher 补全数据 ===")
    all_data = {}
    batch_size = 20
    for i in range(0, len(picks), batch_size):
        batch = [p["code"] for p in picks[i:i + batch_size]]
        try:
            data = get_gate_data(batch, source='dip_mode',
                                 mainlines=MAINLINES)
            all_data.update(data)
        except Exception as e:
            print(f"  批次{i // batch_size + 1}失败: {e}")
        time.sleep(0.5)

    candidates_for_gate = []
    for p in picks:
        d = all_data.get(p["code"], {})
        candidates_for_gate.append({
            'code': p["code"], 'name': p.get('name') or d.get('name', ''),
            'price': p["price"], 'change_pct': d.get('change_pct'),
            'volume_ratio': d.get('volume_ratio'), 'hot_rank': d.get('hot_rank', 999),
            'hot_tag': d.get('hot_tag', ''), 'tags': d.get('tags', []),
            'limit_up_streak': d.get('limit_up_streak', 0),
            'pct_20d': d.get('pct_20d'), 'avg_amplitude_20d': d.get('avg_amplitude_20d'),
            'daily_limit': d.get('daily_limit', 0), 'board_type': d.get('board_type', '主板'),
            'concept': d.get('concept', ''),
            'mainlines': MAINLINES,
            'source': 'dip_mode', 'amount': available_cash,
            'available_cash': available_cash, 'total_assets': total_assets,
            # 低吸结构标注(透明可追溯)
            'pct_60': p["pct_60"], 'pullback': p["pullback"],
        })
    return candidates_for_gate


def main():
    ap = argparse.ArgumentParser(description="全市场扫描→过闸门(默认追涨 / --dip-mode 低吸)")
    ap.add_argument("--dip-mode", action="store_true", help="低吸分位模式(本地qfq算60日分位)")
    ap.add_argument("--top", type=int, default=MAX_TOP, help="dip候选最大数")
    ap.add_argument("--price-cap", type=float, default=30.0, help="价格上限(默认30)")
    a = ap.parse_args()

    # 资金/持仓: 从主库动态取, 失败回退硬编码
    holdings = current_positions()
    available_cash = 26000.0
    total_assets = 1007633.0
    try:
        con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
        row = con.execute("SELECT cash, total_assets FROM account_state ORDER BY date DESC LIMIT 1").fetchone()
        if row:
            available_cash, total_assets = float(row[0] or 26000.0), float(row[1] or 1007633.0)
        con.close()
    except Exception:
        pass

    if a.dip_mode:
        candidates = scan_dip_mode(a.price_cap, a.top, available_cash, total_assets, holdings)
    else:
        candidates = scan_default_mode(available_cash, total_assets, holdings)

    if not candidates:
        print("\n⚠️ 无候选进入闸门。")
        return 0

    print(f"\n=== 过闸门({len(candidates)}只) ===")
    # dip-mode 为盘前扫描, 用 --no-gate-state 避免污染实盘 G11/G16 计数
    passed, rejected = run_gate(candidates, no_gate_state=a.dip_mode)

    print(f"通过: {len(passed)}只")
    for p in passed:
        print(f"  ✅ {p.get('name')} {p.get('code')} ({p.get('score')}分)")
    print(f"拒绝: {len(rejected)}只")
    for r in rejected[:5]:
        print(f"  {r.get('name',''):<10} {r.get('code',''):<8} {r.get('score',0)}分 → {','.join(r.get('hard_fails', [])[:3])}")
    if passed:
        best = passed[0]
        print(f"\n🏆 最优: {best.get('name')} {best.get('code')}")
    else:
        print(f"\n⚠️ 闸门全部拒绝。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
