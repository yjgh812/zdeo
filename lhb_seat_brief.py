#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
lhb_seat_brief.py — 龙虎榜席位速读（纯呈现层，零交易权）

定位：输入个股，速读其最近一次（或指定日）龙虎榜上榜明细与席位博弈格局。
源自外部「龙虎榜掘金」skill 的呈现框架移植（2026-10-19 主人批准的能力移植件），
数据不走云、全部走屠龙本地可审计真源。

数据源（全部本地只读）：
  1. D:\workbuddyspace\databases\fundamental_data.db
     - dragon_tiger_new    榜单层（上榜原因/涨跌幅/榜上买卖总额/净买率, owner=lhb_update.py）
     - lhb_seat_detail     席位明细（买卖TOP5, 2010起, trade_date 带横线）
  2. D:\zcodesj\attribution\attribution.db
     - lhb_seat_fingerprint 席位聚合档案（appearances/2026活跃/历史净额/hm_candidate游资标记）
  3. stock_name_lookup   名称唯一合法接口（代码必配名称铁律）

席位分类规则（呈现规则，非统计验证信号）：
  机构 = 席位名含"机构专用"；北向 = 含"沪股通/深股通/香港"；游资 = fingerprint.hm_candidate=1
  （剔除"中小投资者"散户集体席位，其非游资）；其余 = 普通营业部。

评级 A-E（呈现框架，阈值沿用外部 skill，未做本系统统计验证，不构成交易信号）：
  A = 机构+北向双净买 且 榜上净买率>5%；B = 净买 且 有机构或北向参与；
  C = 其余（中性）；D = 榜上净卖；E = 机构+北向双净卖 且 榜上净卖率>5%。

用法：
  python lhb_seat_brief.py --code 603259 [--date 20261016] [--history 5]
退出码：0=成功；2=该股无上榜记录或指定日未上榜
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
from stock_name_lookup import get_name  # noqa: E402

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")

DISCLAIMER = (
    "> ⚠️ 纯呈现零交易权：本报告仅为席位数据速读，不触发任何交易动作；"
    "评级 A-E 为规则化呈现框架（阈值沿用外部「龙虎榜掘金」skill，未做本系统统计验证），"
    "不构成信号或投资建议。数据=交易所 T+0 盘后龙虎榜，非实时。"
)


def fmt_yi(v):
    """元 → 亿/万 可读字符串；None→N/A"""
    if v is None:
        return "N/A"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if a >= 1e4:
        return f"{v / 1e4:.1f}万"
    return f"{v:.0f}"


def pct(v):
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def classify_seat(name: str, hm_candidate: bool) -> str:
    if "机构专用" in name:
        return "机构"
    if any(k in name for k in ("沪股通", "深股通", "香港")):
        return "北向"
    if hm_candidate and "中小投资者" not in name:
        # 档案 hm_candidate=1 = 高频大额上榜席位（游资候选口径），含外资投行/券商总部通道，
        # 直接标"游资"会误导，故呈现为"高频席位"
        return "高频席位"
    return "营业部"


def load_fingerprint():
    cn = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    rows = cn.execute(
        "SELECT seat_name, appearances, appearances_2026, total_net, first_seen, last_seen, hm_candidate "
        "FROM lhb_seat_fingerprint"
    ).fetchall()
    cn.close()
    return {r[0]: r[1:] for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True, help="6位股票代码, 如 603259")
    ap.add_argument("--date", default=None, help="交易日 YYYYMMDD, 缺省=该股最近一次上榜")
    ap.add_argument("--history", type=int, default=5, help="近N次上榜简表, 默认5")
    args = ap.parse_args()
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    code = args.code.strip()
    if not (code.isdigit() and len(code) == 6):
        print(f"[FAIL] --code 须为6位数字, 收到 {code!r}")
        sys.exit(2)
    name = get_name(code)
    if not name:
        print(f"[FAIL] 名称接口查无 {code}, 禁止输出")
        sys.exit(2)

    cn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    # 榜单层（dragon_tiger_new.trade_date=紧凑YYYYMMDD）
    rows = cn.execute(
        "SELECT trade_date, close, pct_change, turnover_rate, amount, l_sell, l_buy, "
        "l_amount, net_amount, net_rate, amount_rate, reason "
        "FROM dragon_tiger_new WHERE ts_code LIKE ? ORDER BY trade_date DESC",
        (code + "%",),
    ).fetchall()
    if not rows:
        print(f"[N/A] {name}({code}) 无龙虎榜上榜记录（lhb_update.py 库内）")
        sys.exit(2)
    if args.date:
        target = [r for r in rows if r[0] == args.date]
        if not target:
            print(f"[FAIL] {name}({code}) 在 {args.date} 未上榜; 最近一次上榜={rows[0][0]}, 请用该日或省略 --date")
            sys.exit(2)
    else:
        target = [rows[0]]
    dt = target[0][0]                              # '20261016'
    dt_dash = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"       # lhb_seat_detail 带横线格式

    # 席位明细层
    seats = cn.execute(
        "SELECT side, seat_rank, operate_dept_name, buy, sell, net, total_buy_ratio, total_sell_ratio "
        "FROM lhb_seat_detail WHERE ts_code LIKE ? AND trade_date=? ORDER BY side, seat_rank",
        (code + "%", dt_dash),
    ).fetchall()
    cn.close()
    if not seats:
        print(f"[FAIL] {name}({code}) {dt} 榜单存在但席位明细缺失（lhb_seat_detail 无行）")
        sys.exit(2)

    fp = load_fingerprint()

    def seat_row(r):
        side, rank, sname, buy, sell, net, br, sr = r
        sname = sname or "(名称缺失)"
        info = fp.get(sname)
        hm = bool(info and info[5])
        cls = classify_seat(sname, hm)
        portrait = (
            f"出现{info[0]}次/2026年{info[1]}次/历史净{fmt_yi(info[2])}" if info else "无档案"
        )
        return {"side": side, "rank": rank, "name": sname, "cls": cls, "buy": buy,
                "sell": sell, "net": net, "br": br, "sr": sr, "portrait": portrait}

    buy_seats = [seat_row(r) for r in seats if r[0] == "BUY"]
    sell_seats = [seat_row(r) for r in seats if r[0] == "SELL"]
    all_seats = buy_seats + sell_seats

    dtg = target[0]
    # dragon_tiger_new 数值列为 TEXT 存储, 统一转 float（None-safe）
    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    (trade_date, close, pct_chg, turn, amount, l_sell, l_buy, l_amount, net_amt, net_rate,
     amt_rate, reason) = (
        dtg[0], _f(dtg[1]), _f(dtg[2]), _f(dtg[3]), _f(dtg[4]), _f(dtg[5]),
        _f(dtg[6]), _f(dtg[7]), _f(dtg[8]), _f(dtg[9]), _f(dtg[10]), dtg[11])
    inst_net = sum(s["net"] for s in all_seats if s["cls"] == "机构")
    north_net = sum(s["net"] for s in all_seats if s["cls"] == "北向")
    hm_net = sum(s["net"] for s in all_seats if s["cls"] == "游资")
    base = l_amount if l_amount else None
    inst_ratio = pct(inst_net / base * 100) if base else "N/A"
    north_ratio = pct(north_net / base * 100) if base else "N/A"
    hm_ratio = pct(hm_net / base * 100) if base else "N/A"

    # 评级（A/E 强信号优先, 再 B/D, 剩余 C）
    if net_rate is None:
        rating, verdict = "N/A", "净买率字段缺失, 无法评级"
    elif inst_net > 0 and north_net > 0 and net_rate > 5:
        rating = "A"
        verdict = "机构与北向同向净买入且买方力量强, 席位构成偏多"
    elif inst_net < 0 and north_net < 0 and net_rate < -5:
        rating = "E"
        verdict = "机构与北向同向净卖出且卖方力量强, 席位构成偏空"
    elif net_rate > 0 and (inst_net > 0 or north_net > 0):
        rating = "B"
        verdict = "榜上净买入且有机构或北向参与, 偏多"
    elif net_rate < 0:
        rating = "D"
        verdict = "榜上净卖出, 偏空"
    else:
        rating = "C"
        verdict = "买卖力量均衡, 中性"

    def seat_table(ss):
        head = "|排名|营业部席位|分类|买入|卖出|净额|档案画像|"
        sep = "|---|---|---|---|---|---|---|"
        lines = [head, sep]
        for s in ss:
            # 东财明细源为单侧数据: BUY 行无卖出数, SELL 行无买入数
            buy_s = fmt_yi(s["buy"]) if s["side"] == "BUY" else "—"
            sell_s = fmt_yi(s["sell"]) if s["side"] == "SELL" else "—"
            lines.append(f"|{s['rank']}|{s['name']}|{s['cls']}|{buy_s}|"
                         f"{sell_s}|{fmt_yi(s['net'])}|{s['portrait']}|")
        return "\n".join(lines)

    L = []
    L.append(f"# 龙虎榜席位速读 · {name}({code}) [{dt}]")
    L.append("")
    L.append(DISCLAIMER)
    L.append("")
    L.append("## 上榜概况")
    L.append(f"- 上榜原因：{reason or 'N/A'}")
    L.append(f"- 收盘 {close} | 涨跌幅 {pct(pct_chg)} | 换手率 {pct(turn)}")
    L.append(f"- 当日成交额 {fmt_yi(amount)} | 榜上成交 {fmt_yi(l_amount)}（占当日 {pct(amt_rate)}）")
    L.append(f"- 榜上净买入 {fmt_yi(net_amt)}（净买率 {pct(net_rate)}）")
    L.append("")
    L.append("### 买入TOP5")
    L.append(seat_table(buy_seats))
    L.append("")
    L.append("### 卖出TOP5")
    L.append(seat_table(sell_seats))
    L.append("")
    L.append("## 力量评分与评级")
    L.append(f"- 机构净额 {fmt_yi(inst_net)}（参与度 {inst_ratio}）| "
             f"北向净额 {fmt_yi(north_net)}（参与度 {north_ratio}）| "
             f"高频席位净额 {fmt_yi(hm_net)}（参与度 {hm_ratio}，档案 hm_candidate 游资候选口径）")
    L.append(f"- 综合评级：**{rating}** — {verdict}")
    L.append("")
    hist = rows[: max(args.history, 1)]
    if len(hist) > 1:
        L.append("## 近期上榜（含本次）")
        L.append("|日期|涨跌幅|净买率|上榜原因|")
        L.append("|---|---|---|---|")
        for h in hist:
            L.append(f"|{h[0]}|{pct(h[2])}|{pct(h[9])}|{(h[11] or 'N/A')[:30]}|")
        L.append("")
    if "三日" in (reason or ""):
        L.append("⚠️ 本期为三日榜：明细为三日累计口径，注意「边拉边撤」与一日游资金混入可能。")
        L.append("")

    out = "\n".join(L)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rp = REPORT_DIR / f"LHB_SEAT_BRIEF_{code}_{dt}.md"
    rp.write_text(out, encoding="utf-8")
    print(out)
    print(f"\n[OK] 报告已写入 {rp}")


if __name__ == "__main__":
    main()
