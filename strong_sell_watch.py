# -*- coding: utf-8 -*-
"""候选乙强中卖机械化·持仓哨提示层（提请制，零交易权）
对应 PREREG_STRONG_SELL_WATCH_20261008，主人2026-10-08令「到卖出时需要提示」批准。
与 exit_gate_watch.py(PD-036④)/stock_zones_watch.py 同族：提请制=命中弹toast待主人裁决，
禁止任何自动卖出/触碰trade_executor/撤单改单。
信号口径与 reports/wyckoff_full_study_20261007.md 第二波研究逐字一致（T-1口径零前视）：
  hi_stall  = close>=H60*0.95 & vr>=2.0 & pct<=1.0
  utad_loud = high>H20 & close<H20 & close>=H60*0.9 & vr>=1.5
  nd        = 阳线 & vr<=0.7 & body<=1%昨收 & close>=H60*0.9
2026-10-09扩fantan_escape（起航S2事件研究口径，PREREG_QIHANG_SELL_WATCH_20261009）：
  fantan_escape  = 近5日内有连板断板日 且 今日反抽(主板≥+8%/创业板20200824后≥+18%)
                   （反抽池R+10中位-5.1~-7.5%/胜率≈35%，反抽位是卖不是买）
  口径源=reports/douyin_qihang_youzi_verify_20261003.md §11/§12；涨停阈值同研究(主板9.8/创业19.7)
🔴勘误(10/9全量测试)：同日曾加duanban(连板断板)信号，与exit_gate_watch.py S1(PD-036④,2026-10-07
  已挂0745第8步)完全同语义同证据(-15.28%)=重复owner，已删除——断板预警唯一owner=exit_gate_watch；
  fantan_escape保留：口径=事件研究窄口径(须连板断板池)，与exit_gate S2(20日回撤≥15%宽口径)互补。
2026-10-09扩第三信号（TB爆量退出执行触发版，PREREG_VOLSPIKE_TOP_WATCH_20261009）：
  volspike_top   = vr120>=1.5 & ret20>=20%（量/前120日均量 且 20日涨幅，高位爆量=派发）
                   主顶同日捕获65-72%(53万样本)；次级注记pre5_vr>=1.5=25-30天高量区间型
  口径源=reports/tb_master_playbook_20261003.md + topbottom_feature_lib_20261003.md
2026-10-14扩第四信号（段起失效候选证伪跟随，PREREG_SEGFAIL_WATCH_20261014）：
  segfail        = 持仓在近15份universe_startup_scan池json存在锚(最新在池期px与off_start)
                   → 段起价=px/(1+off_start/100)，最新收<段起价×0.97即报
                   （口径=低位启动呈报自带失效闸"距段起-3%即失效"的持仓跟随）
  锚超45自然日=SKIP；无锚=SKIP(少报不误报)；前向验证=603960单案例(按规则次日退出-4.5% vs 实际-9.18%)
🔴诚实层：提示文案内嵌削弱证据（报告§13.1）——hi_stall持仓域历史反号(med20+3.17)，
  提示仅为警示呈报，非独立卖出依据。duanban/fantan_escape=全市场事件池证据，
  本持仓域尚无独立验证；duanban与trend_checker走坏判定语义重叠=提示层冗余哨非新增卖出权。
  volspike_top🔴新买低位票爆量=主升确认不卖（卖出三件套/A池退出三臂-1.9pp），
  本哨语义=高位获利老票的派发警示，是否适用由主人按持仓语境裁决。
持仓真源=westock MCP实时；本件读 data/holdings_codes_latest.json 批量快照（snapshot_at注记）。
幂等台账 data/strong_sell_watch_ledger.jsonl（键=signal_date+code+tag）。

用法: python strong_sell_watch.py            # 扫最新交易日
      python strong_sell_watch.py --date 20260930
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HOLDINGS = Path(r"D:\zcodesj\data\holdings_codes_latest.json")
LEDGER = Path(r"D:\zcodesj\data\strong_sell_watch_ledger.jsonl")
BRIEF = Path(r"D:\zcodesj\data\strong_sell_watch_latest.txt")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"  # 2026-10-13 切wb唯一真源(zc副本停更10/09已废, SOP一百三十二)

HONESTY = {
    "hi_stall": "持仓域历史反号(med20+3.17,强票滞涨=洗盘居多),仅警示非卖出依据",
    "utad_loud": "持仓域历史弱负(med20-0.94),派发警示呈报",
    "nd": "持仓域历史弱负(med20-0.98),需求缺失警示呈报",
    "fantan_escape": "全市场反抽池R+10中位-5.1~-7.5%/胜率35%,反抽位是卖不是买;本持仓域无独立验证;与exit_gate S2宽口径互补",
    "volspike_top": "高位爆量=派发(53万样本主顶同日捕获65-72%);🔴新买低位票爆量=主升确认不卖,仅对高位获利老票警示",
    "segfail": "单案例前向验证(603960按规则次日退出-4.5%vs实际-9.18%);段起价由呈报px/off_start反推有舍入;锚过期/无锚不报=少报不误报;提示层非卖出依据",
}


SCAN_REPORT_DIR = Path(r"D:\zcodesj\reports")
ANCHOR_SCAN_WINDOW = 15   # 回溯扫描池json份数
ANCHOR_MAX_AGE_DAYS = 45  # 锚过期=SKIP(段起语义2-40交易日)


def load_segfail_anchors(sig_date: int) -> dict:
    """段起锚={code:(池期YYYYMMDD, 段起价)}: 最近15份universe_startup_scan json中该票最新一次在池记录。
    段起价=px/(1+off_start/100)(呈报池字段反推)；锚超45自然日丢弃=陈锚不报少报不误报。"""
    files = sorted(SCAN_REPORT_DIR.glob("universe_startup_scan_????????.json"),
                   reverse=True)[:ANCHOR_SCAN_WINDOW]
    anchors = {}
    for fp in files:
        digits = "".join(ch for ch in fp.stem if ch.isdigit())
        if len(digits) != 8:
            continue
        pool_date = int(digits)
        try:
            hits = json.loads(fp.read_text(encoding="utf-8")).get("hits", [])
        except Exception:
            continue
        for h in hits:
            code = str(h.get("code", "")).zfill(6)
            if code in anchors:
                continue
            px, off = h.get("px"), h.get("off_start")
            if not px or px <= 0 or off is None or off <= -100:
                continue
            anchors[code] = (pool_date, px / (1.0 + off / 100.0))
    cut = int((datetime.strptime(str(sig_date), "%Y%m%d")
               - timedelta(days=ANCHOR_MAX_AGE_DAYS)).strftime("%Y%m%d"))
    return {c: (d, s) for c, (d, s) in anchors.items() if d >= cut}


def load_holdings():
    d = json.loads(HOLDINGS.read_text(encoding="utf-8"))
    return d.get("positions", []), d.get("snapshot_at", "?")


def _zt_thr(code6: str, dt: int) -> float:
    """涨停判定阈值, 与起航事件研究同口径(主板9.8/创业板20200824后19.7); ST涨停5%漏计=少计不误计。"""
    if code6.startswith("300") and dt >= 20200824:
        return 19.7
    return 9.8


def _reb_thr(code6: str, dt: int) -> float:
    """反抽日阈值, 与起航S2同口径(主板8.0/创业板20200824后18.0)。"""
    if code6.startswith("300") and dt >= 20200824:
        return 18.0
    return 8.0


def scan_signals(code: str, df: pd.DataFrame, sig_date: int):
    """对单票在sig_date(最新可用交易日)判定五信号, 返回[tag, detail]列表。口径=研究第二波+起航S1/S2。"""
    if len(df) < 80:
        return []
    g = df.reset_index(drop=True)
    o = g["open"].values; h = g["high"].values; c = g["close"].values
    v = g["volume"].values.astype(float); d = g["date"].values.astype(int)
    n = len(g)
    idx = int(np.where(d == sig_date)[0][0])
    i = idx
    if i < 60:
        return []
    ma_v = pd.Series(v).rolling(20).mean().shift(1).values
    H20 = pd.Series(h).rolling(20).max().shift(1).values
    H60 = pd.Series(h).rolling(60).max().shift(1).values
    with np.errstate(invalid="ignore", divide="ignore"):
        vr = v / ma_v
    if not np.isfinite(vr[i]):
        return []
    pct_arr = np.full(n, np.nan)
    pct_arr[1:] = (c[1:] / c[:-1] - 1) * 100
    pct = pct_arr[i]
    zt = np.array([bool(np.isfinite(pct_arr[k])) and pct_arr[k] >= _zt_thr(code, int(d[k]))
                   for k in range(n)])
    stk = np.zeros(n, dtype=int)
    for k in range(n):
        stk[k] = (stk[k - 1] + 1 if k > 0 else 1) if zt[k] else 0
    out = []
    # 起航S2: 断板后5日内反抽(≥8%/创业≥18%), 反抽位是卖不是买
    # 🔴勘误: duanban(连板断板)分支已删除——与exit_gate_watch S1完全同语义(断板预警唯一owner=exit_gate)
    if np.isfinite(pct) and pct >= _reb_thr(code, int(d[i])):
        for j in range(max(1, i - 5), i):
            if stk[j - 1] >= 2 and not zt[j]:
                out.append(("fantan_escape",
                            f"断板(距{i - j}日)后反抽 涨幅{pct:+.1f}% 量比{vr[i]:.1f}x"))
                break
    # TB爆量退出执行触发版: vr120>=1.5 且 ret20>=20% (高位爆量=派发; 需121根以上数据)
    if i >= 120:
        ma120 = pd.Series(v).rolling(120).mean().shift(1).values
        with np.errstate(invalid="ignore", divide="ignore"):
            vr120 = v / ma120
        ret20 = (c[i] / c[i - 20] - 1) * 100 if c[i - 20] > 0 else float("nan")
        if np.isfinite(vr120[i]) and vr120[i] >= 1.5 and np.isfinite(ret20) and ret20 >= 20.0:
            pre5 = float(np.nanmean(vr120[max(0, i - 5):i]))
            zone = " 高量区间型(pre5_vr>=1.5)" if pre5 >= 1.5 else ""
            out.append(("volspike_top",
                        f"爆量派发 vr120={vr120[i]:.1f}x ret20={ret20:+.1f}%{zone}"))
    if np.isfinite(H60[i]) and c[i] >= H60[i] * 0.95 and vr[i] >= 2.0 and pct <= 1.0:
        out.append(("hi_stall", f"收盘{c[i]:.2f}距60日高<5% 量比{vr[i]:.1f}x 涨幅{pct:+.1f}%"))
    if (np.isfinite(H20[i]) and np.isfinite(H60[i]) and h[i] > H20[i]
            and c[i] < H20[i] and c[i] >= H60[i] * 0.9 and vr[i] >= 1.5):
        out.append(("utad_loud", f"冲高破20日高后收盘回落 收盘{c[i]:.2f} 量比{vr[i]:.1f}x"))
    if (c[i] > o[i] and vr[i] <= 0.7 and (c[i] - o[i]) <= 0.01 * c[i - 1]
            and np.isfinite(H60[i]) and c[i] >= H60[i] * 0.9):
        out.append(("nd", f"缩量窄阳 量比{vr[i]:.2f}x 实体{(c[i]-o[i])/c[i-1]*100:.2f}%"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="信号日期YYYYMMDD, 默认最新可用交易日")
    args = ap.parse_args()
    positions, snap_at = load_holdings()
    if not positions:
        print("[strong_sell_watch] 持仓快照为空, 跳过")
        return
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = con.cursor()
    if args.date:
        sig_date = int(args.date)
    else:
        sig_date = int(cur.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
    done = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done.add((r["signal_date"], r["code"], r["tag"]))
            except Exception:
                pass
    anchors = load_segfail_anchors(sig_date)
    hits = []
    new_rows = []
    for pos in positions:
        code = str(pos.get("code", ""))          # 带前缀 sz003006
        name = pos.get("name", code)
        bare = code[-6:]
        df = pd.read_sql("SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq "
                         "WHERE code=? AND date<=? ORDER BY date",
                         con, params=(bare, sig_date))
        for tag, detail in scan_signals(bare, df, sig_date):
            key = (sig_date, bare, tag)
            if key in done:
                continue
            hits.append((bare, name, tag, detail))
            new_rows.append({"signal_date": sig_date, "code": bare, "name": name,
                             "tag": tag, "detail": detail,
                             "qty": pos.get("qty"), "snapshot_at": snap_at,
                             "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        # 2026-10-14 段起失效哨(PREREG_SEGFAIL_WATCH_20261014, 呈请2): 候选证伪跟随提请
        anchor = anchors.get(bare)
        if anchor and not df.empty and int(df["date"].iloc[-1]) == sig_date:
            anchor_date, seg_px = anchor
            c_last = float(df["close"].iloc[-1])
            thr = seg_px * 0.97
            if c_last < thr and (sig_date, bare, "segfail") not in done:
                detail = (f"段起失效 收{c_last:.2f}<段起{seg_px:.2f}x0.97={thr:.2f}"
                          f"(锚{anchor_date})")
                hits.append((bare, name, "segfail", detail))
                new_rows.append({"signal_date": sig_date, "code": bare, "name": name,
                                 "tag": "segfail", "detail": detail,
                                 "qty": pos.get("qty"), "snapshot_at": snap_at,
                                 "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    con.close()
    if new_rows:
        with open(LEDGER, "a", encoding="utf-8") as fp:
            for r in new_rows:
                fp.write(json.dumps(r, ensure_ascii=False) + "\n")
    lines = [f"[strong_sell_watch] 信号日={sig_date} 持仓={len(positions)}(快照{snap_at}) 新命中={len(hits)}"]
    for bare, name, tag, detail in hits:
        lines.append(f"  {name}({bare}) {tag}: {detail} | {HONESTY[tag]}")
    if hits:
        title = f"强中卖哨: {len(hits)}笔持仓卖侧信号(提请制)"
        content = "\n".join(f"{n}({c}) {t}: {dt}\n  注: {HONESTY[t]}\n  是否减仓由主人裁决,系统不自动卖出"
                            for c, n, t, dt in hits)
        try:
            import subprocess
            r = subprocess.run(
                [sys.executable, str(Path(r"D:\zcodesj") / "notify.py"), "send",
                 "--title", title, "--content", content,
                 "--level", "warn", "--channel", "toast"],
                capture_output=True, text=True, timeout=60, cwd=r"D:\zcodesj")
            lines.append(f"  [toast rc={r.returncode}]")
        except Exception as e:
            lines.append(f"  [toast失败:{e}]")
    else:
        lines.append("  无命中静默属正常")
    BRIEF.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
