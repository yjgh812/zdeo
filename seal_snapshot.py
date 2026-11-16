# -*- coding: utf-8 -*-
"""
seal_snapshot.py — 涨停封单快照 + 真封板率/假涨停修正 (移植自 TSP 五档封单修正思想)
=====================================================================================
来源: TSP depth_service 五档封单修正 — "价格涨停" ≠ "真封板":
      fake_up(伪封板/尾盘偷袭板: 收在涨停价但封单极小, 随时开板风险)
适配: 屠龙系统, 腾讯实时接口(qt.gtimg.cn, 收盘后五档定格仍有效, 盘后可补抓),
      涨停判定用 price_limits.py 唯一口径。

口径:
  涨停(价格口径) : close >= 涨停价(price_limits, half-up + ST/切换日 + 板块)
  真封板 sealed   : 涨停 且 买一价==涨停价 且 封单额 >= FAKE_AMOUNT_TOL(默认300万)
  伪封板 fake     : 涨停 但 封单额 < FAKE_AMOUNT_TOL (尾盘偷袭/弱封板)
  炸板 broken     : 最高价 >= 涨停价 但 close < 涨停价
  真封板率        : 真封板 / (真封板 + 伪封板 + 炸板)

用法:
  python seal_snapshot.py                    # 抓当前快照(交易日14:55-15:05最佳, 盘后亦可)
  python seal_snapshot.py --date 20260814    # 指定归属交易日(默认主库最新交易日)
  python seal_snapshot.py --fake-tol 500     # 伪封板阈值调为500万
输出: 汇总 + Top封单榜; 全量写 attribution.db seal_snapshot 表(幂等 REPLACE)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from price_limits import is_limit_up, limit_price, price_limit_pct, is_risk_warning_name  # noqa: E402

ATTR_DB = Path(r"D:\zcodesj\attribution\attribution.db")  # 2026-08-24 双库统一:指向唯一真源
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
FAKE_AMOUNT_TOL = 3_000_000  # 封单额低于此值判"伪封板"(元)
BATCH = 60                    # 腾讯批量每请求股票数
SLEEP = 0.12                  # 请求间隔(秒), ~90次请求全A

TBL = """
CREATE TABLE IF NOT EXISTS seal_snapshot (
    date TEXT NOT NULL, code TEXT NOT NULL, name TEXT,
    price REAL, prev_close REAL, limit_price REAL, pct REAL,
    sealed INTEGER, fake INTEGER, broken INTEGER,
    sealed_vol_hand INTEGER, sealed_amount REAL, snap_time TEXT,
    source TEXT DEFAULT 'live',
    PRIMARY KEY (date, code)
)"""


def _tencent_suffix(code: str) -> str:
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def fetch_batch(codes: list[str]) -> list[dict]:
    """拉一批腾讯快照 → 行 dict(字段: code,name,price,prev,high,buy1,buy1_vol,pct)。"""
    q = ",".join(_tencent_suffix(c) for c in codes)
    url = f"https://qt.gtimg.cn/q={q}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
    out = []
    for seg in raw.strip().split(";"):
        seg = seg.strip()
        if "=" not in seg:
            continue
        f = seg.split("=")[1].strip('"').split("~")
        if len(f) < 40:
            continue
        try:
            out.append({
                "code": f[2], "name": f[1], "price": float(f[3] or 0),
                "prev": float(f[4] or 0), "vol": float(f[6] or 0),
                "buy1": float(f[9] or 0), "buy1_vol": float(f[10] or 0),
                "high": float(f[33] or 0), "pct": float(f[32] or 0),
            })
        except (ValueError, IndexError):
            continue
    return out


def universe() -> list[str]:
    """主库最新交易日有行情的沪深A股(北交所腾讯不支持, 排除)。"""
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    d = con.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0]
    codes = [r[0] for r in con.execute(
        "SELECT DISTINCT code FROM stock_daily_hsj WHERE date=? AND vol>0", (d,)).fetchall()]
    con.close()
    codes = [c for c in codes if c.startswith(("60", "00", "30", "68"))]
    return sorted(codes)


def seal_quality(trade_date: str) -> dict:
    """读 seal_snapshot 表，返回某归属日的封板质量读数（供 vibe_card 消费，只读）。
    无数据 → {'ok': False}，绝不静默填数。"""
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT sealed, fake, broken, sealed_amount, code, name FROM seal_snapshot WHERE date=?",
            (trade_date,)).fetchall()
        con.close()
    except Exception as e:  # pragma: no cover
        return {"ok": False, "reason": str(e)}
    if not rows:
        return {"ok": False, "reason": f"seal_snapshot 无 {trade_date} 数据（14:57 cron 是否成功？）"}
    sealed = sum(1 for r in rows if r[0])
    fake = sum(1 for r in rows if r[1])
    broken = sum(1 for r in rows if r[2])
    total = sealed + fake + broken
    true_rate = sealed / total * 100 if total else 0.0
    tops = sorted([r for r in rows if r[0]], key=lambda r: -(r[3] or 0))[:3]
    top3 = [{"code": r[4], "name": r[5], "amount": round((r[3] or 0) / 1e8, 2)} for r in tops]
    return {"ok": True, "date": trade_date, "n_limit": sealed + fake,
            "n_sealed": sealed, "n_fake": fake, "n_broken": broken,
            "true_seal_rate_pct": round(true_rate, 1), "seal_top3": top3}


def main() -> None:
    ap = argparse.ArgumentParser(description="涨停封单快照+真封板率(腾讯五档)")
    ap.add_argument("--date", help="归属交易日 YYYYMMDD(默认主库最新)")
    ap.add_argument("--fake-tol", type=float, default=FAKE_AMOUNT_TOL / 1e4,
                    help="伪封板封单额阈值(万元, 默认300)")
    ap.add_argument("--force", action="store_true",
                    help="非当日实拍态强覆已有 live 行(危险, 默认拒绝)")
    a = ap.parse_args()

    con0 = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    # 归属日修复(8/22)：14:57 抓的是"今天"的实时五档，但 hsj 当天数据要等晚班才入库，
    # 用 MAX(hsj) 会把实时数据错标成上一交易日并反复覆盖同一天。
    # 正确口径：工作日(周一~五)运行 → 归属日=今天；周末/节假日或手动跑 → 归属日=hsj 最新交易日。
    if a.date:
        trade_date = a.date
    elif date.today().weekday() < 5:
        _today_s = date.today().strftime("%Y%m%d")
        # 2026-08-24 修复: 节假日工作日休市时曾把上一交易日快照写入幽灵日期行;
        # 与 hsj 交易日历核验, 非交易日回退 MAX(date)
        if con0.execute("SELECT 1 FROM stock_daily_hsj WHERE date=? LIMIT 1", (_today_s,)).fetchone():
            trade_date = _today_s
        else:
            trade_date = str(con0.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0])
    else:
        trade_date = str(con0.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0])
    con0.close()
    # 2026-09-23 覆写守卫: rebuild 态(非盘中窗口)或归属日≠今天时, 禁止覆盖已有 live 实拍行。
    # 实证链: ①9/23 凌晨 03:45 重跑以旧判定(<15:30 即 live)身份把 9/22 实拍 93 行(7真封)
    # 覆成 110 行(30真封)——隔夜五档非收盘定格态, 已从 backup 恢复; ②8/14(隔夜)/8/17(次日)
    # /8/21(次日) 历史行同族失真(真封率读数 73%/6.5%/73% 已证伪剔除)。真封率口径只认
    # 当日 14:55~15:10 实拍。--force 显式强覆。
    _hm = datetime.now().hour * 100 + datetime.now().minute
    src = "live" if 925 <= _hm < 1530 else "rebuild"  # 修正E54盲区: 凌晨(00:00-09:15)非盘中
    _live_ok = (src == "live" and trade_date == date.today().strftime("%Y%m%d"))
    if not _live_ok:
        try:
            _g = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
            _n = _g.execute("SELECT COUNT(*) FROM seal_snapshot WHERE date=? AND source='live'",
                            (trade_date,)).fetchone()[0]
            _g.close()
        except Exception:
            _n = 0
        if _n and not a.force:
            print(f"[seal] ⛔ {trade_date} 已有 {_n} 行 live 实拍, 当前为非当日实拍态"
                  f"(src={src}) → 拒绝覆盖(--force 可强覆)。")
            return 0
        if _n and a.force:
            print(f"[warn] --force 强覆 {trade_date} 的 {_n} 行 live 实拍(危险操作已执行)")
    td = date(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:]))
    tol_yuan = a.fake_tol * 1e4

    codes = universe()
    print(f"[seal] universe={len(codes)} 归属交易日={trade_date} 阈值={a.fake_tol:.0f}万")

    rows: list[dict] = []
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        try:
            rows.extend(fetch_batch(batch))
        except Exception as e:
            print(f"[warn] 批次 {i//BATCH} 拉取失败: {e}")
        time.sleep(SLEEP)
        if (i // BATCH) % 20 == 0:
            print(f"[seal] {i + len(batch)}/{len(codes)} 已拉 {len(rows)}", flush=True)
    print(f"[seal] 拉取完成 {len(rows)} 条")
    if not rows:
        print("[seal] ❌ 拉取 0 条（腾讯接口失败/被限流）→ 未写库，退出码=1 供 cron 上报")
        return 1

    n_limit = n_sealed = n_fake = n_broken = 0
    recs = []
    snap_time = time.strftime("%Y-%m-%d %H:%M:%S")
    # 2026-08-27 E54: provenance 落列——盘中实拍='live', 其余='rebuild'(降级参考), 防止晋级率
    # 类研究把重建行当盘中实拍消费(8/26 23:59 重建行教训)。src 判定已上移至覆写守卫处
    # (2026-09-23 修正凌晨盲区: live 窗口收紧为 09:25~15:30, 隔夜冻结态不再是 live)。
    if src == "rebuild":
        print(f"[warn] 非盘中窗口运行: 本批行将标记 source=rebuild(封单额失真降级参考)")
    for r in rows:
        if r["prev"] <= 0 or r["price"] <= 0:
            continue
        lp = limit_price(r["prev"], price_limit_pct(
            r["code"], td, is_risk_warning=is_risk_warning_name(r["name"])))
        limit_hit = is_limit_up(r["code"], r["prev"], r["price"], td, name=r["name"])
        touched_high = r["high"] >= lp - 0.005
        sealed = fake = broken = 0
        sealed_vol = sealed_amt = 0
        if limit_hit:
            n_limit += 1
            # 真封板: 买一挂在涨停价且有量
            if r["buy1"] >= lp - 0.005 and r["buy1_vol"] > 0:
                sealed_vol = int(r["buy1_vol"])
                sealed_amt = sealed_vol * 100 * lp
                if sealed_amt >= tol_yuan:
                    sealed, n_sealed = 1, n_sealed + 1
                else:
                    fake, n_fake = 1, n_fake + 1
            else:
                # 收在涨停价但买一不在涨停价(已开板回封瞬间/数据延迟) → 按伪封板
                fake, n_fake = 1, n_fake + 1
        elif touched_high:
            broken, n_broken = 1, n_broken + 1
        if limit_hit or touched_high:
            recs.append((trade_date, r["code"], r["name"], r["price"], r["prev"], lp,
                         r["pct"], sealed, fake, broken, sealed_vol, sealed_amt, snap_time, src))

    con = sqlite3.connect(str(ATTR_DB), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(TBL)
    _cols = {row[1] for row in con.execute("PRAGMA table_info(seal_snapshot)")}
    if "source" not in _cols:
        con.execute("ALTER TABLE seal_snapshot ADD COLUMN source TEXT DEFAULT 'live'")
        con.commit()
    con.executemany(
        "INSERT OR REPLACE INTO seal_snapshot "
        "(date,code,name,price,prev_close,limit_price,pct,sealed,fake,broken,"
        "sealed_vol_hand,sealed_amount,snap_time,source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", recs)
    con.commit()
    con.close()

    total_touch = n_sealed + n_fake + n_broken
    price_rate = n_limit / total_touch * 100 if total_touch else 0
    true_rate = n_sealed / total_touch * 100 if total_touch else 0
    # 2026-08-24 P2修复: 实证推翻 docstring"收盘后五档定格仍有效"假设——21:00 vs 22:00
    # 两次盘后抓取真封板 34→5 只严重冲突(腾讯收盘后五档封单量随时间失真)；
    # 收盘后(>=15:30)运行的结果仅为盘中 14:57 定时抓取的降级参考，强制标注。
    now_hm = datetime.now().hour * 100 + datetime.now().minute
    if now_hm >= 1530:
        print("  ⚠️ 盘后时段(>=15:30)抓取: 腾讯五档封单量已失真, 真封板/伪封板/封单额仅供参考,"
              " 勿据此下情绪结论; 以盘中 14:57 定时快照为准")
    print(f"\n=== {trade_date} 封单快照汇总 ===")
    print(f"  涨停(价格口径): {n_limit}  真封板: {n_sealed}  伪封板: {n_fake}  炸板: {n_broken}")
    print(f"  封板率(价格口径) = {n_limit}/{total_touch} = {price_rate:.1f}%")
    print(f"  封板率(真封单)   = {n_sealed}/{total_touch} = {true_rate:.1f}%   ← 剔除伪封板后")
    top = sorted([r for r in recs if r[7]], key=lambda r: -r[11])[:10]
    if top:
        print("\n  封单额Top10(真封板):")
        for rec in top:
            print(f"    {rec[1]} {rec[2]:<8} 封单 {rec[11]/1e8:.2f}亿  涨停价 {rec[5]:.2f}")
    fakes = [r for r in recs if r[8]]
    if fakes:
        print(f"\n  伪封板名单({len(fakes)}只, 封单<{a.fake_tol:.0f}万):")
        for rec in fakes:
            print(f"    {rec[1]} {rec[2]:<8} 封单 {rec[11]/1e4:.0f}万")


if __name__ == "__main__":
    main()
