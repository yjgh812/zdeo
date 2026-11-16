# -*- coding: utf-8 -*-
"""全宇宙低位启动扫描器（2026-09-23 主人令"全宇宙选5只胜率高空间大"）

复用 mainline_startup_scan.py 的赢家画像v3全过滤口径:
  价格2-30 + 多头排列(MA20>MA60) + pos250<50(一年低位) + 主升段起(一年低点后首个放量大阳
  量>=2×MA20量且涨>=3%) + 启动前60日跌>=25% + 缩量动态阈值 + 距段起-3%~+17% + 启动2-40日
  + 三年分位<55% + 距三年低<80% + 排雷(S5/S6/量位/乖离)
差异: universe=全市场非ST非退市(不限主线), LOW_VOL下主线过滤反向(v5.3 FAIL两次互证), 全宇宙正当。
2026-10-14 业绩趋势闸(呈请1,克来机电603960复盘反哺): factor_pit PIT零前视, E1转亏/E2归母同比降>30%/
  E3营收连续两年负增长 → 不入池(剔除名单入呈报头与json.fund_gate); USCAN_FUND_GATE=0停用; 缺PIT不误杀。
输出: reports/universe_startup_scan_YYYYMMDD.{md,json} 只读呈报件零交易权。
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from stock_name_lookup import get_name

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
RAW_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"
FUND_PIT_DB = r"file:D:/zcodesj/factor_pit.db?mode=ro"


def load_fund_trend(pit_cut: int) -> tuple[dict, int]:
    """PIT业绩趋势闸(2026-10-14呈请1落地): 业绩持续恶化的票不入池。
    口径: E1转亏=最新期eps<0且(期末半/年报或连续两期为负) | E2大降=最新期与去年同期eps均>0且同比降>30%
          | E3营收负=最近两个年报期sps均同比下降。
    数据源=factor_pit.db stock_factors_fundamental_pit_v2, pit_avail_date<=pit_cut 零前视;
    eps/sps为列内归一口径(仅取同票比值与符号), 与真实降幅勾稽: 603960 年报eps 2024→2025 -49.6%
    (真实归母-49.9%)、年报sps -14.8%(真实营收-15.1%)、2026H1 eps<0(真实转亏)。
    无PIT记录=不标旗不误杀; 返回({code:flag}, PIT覆盖票数)。"""
    conn = sqlite3.connect(FUND_PIT_DB, uri=True)
    try:
        rows = conn.execute(
            "SELECT code, report_date, eps, sps FROM stock_factors_fundamental_pit_v2 "
            "WHERE pit_avail_date<=? ORDER BY code, report_date DESC", (pit_cut,)).fetchall()
    finally:
        conn.close()
    per: dict = {}
    for code, rd, eps, sps in rows:
        per.setdefault(code, []).append((rd, eps, sps))
    flags: dict = {}
    for code, series in per.items():
        seen: set = set()
        seq = []
        for rd, eps, sps in series:
            if rd in seen:
                continue
            seen.add(rd)
            seq.append((rd, eps, sps))
        flag = ''
        if seq and seq[0][1] is not None and seq[0][1] < 0:
            semiannual = seq[0][0] % 10000 in (630, 1231)
            two_neg = len(seq) >= 2 and seq[1][1] is not None and seq[1][1] < 0
            if semiannual or two_neg:
                flag = 'E1转亏'
        if not flag and len(seq) >= 5:
            e0 = seq[0][1]
            yoy = next((e for rd, e, _ in seq[1:] if rd == seq[0][0] - 10000), None)
            if e0 and yoy and e0 > 0 and yoy > 0 and e0 / yoy - 1 < -0.30:
                flag = 'E2大降'
        if not flag and len(seq) >= 3:
            annual = [(rd, s) for rd, _, s in seq if rd % 10000 == 1231 and s]
            neg = 0
            for i in range(len(annual) - 1):
                if annual[i][0] - annual[i + 1][0] != 10000 or annual[i + 1][1] <= 0:
                    break
                if annual[i][1] / annual[i + 1][1] - 1 < 0:
                    neg += 1
                else:
                    break
            if neg >= 2:
                flag = 'E3营收连续两年负'
        if flag:
            flags[code] = flag
    return flags, len(per)


def main() -> int:
    # 2026-10-08 新鲜度闸: 夜链(22:00)更新前手动重扫会静默用旧bar(当日晚间踩坑实锤)——终端横幅+报告头双标注
    _probe = sqlite3.connect(QFQ_DB, uri=True)
    qfq_max = _probe.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    _probe.close()
    _today = datetime.now().strftime("%Y%m%d")
    if qfq_max != _today:
        print(f"🔴 [数据新鲜度] qfq库最新bar={qfq_max} ≠ 今日{_today}——本扫描基于{qfq_max}收盘数据, 非当日口径")
    else:
        print(f"✅ [数据新鲜度] qfq库最新bar={qfq_max}")
    # 2026-10-14 业绩趋势闸装载(基准=qfq最新bar日, 与价格数据同基准零前视)
    fund_cut = int(qfq_max)
    fund_flags: dict = {}
    fund_cov = 0
    fund_note = ''
    if os.environ.get("USCAN_FUND_GATE") == "0":
        fund_note = '停用(USCAN_FUND_GATE=0)'
    else:
        try:
            fund_flags, fund_cov = load_fund_trend(fund_cut)
        except Exception as e:
            fund_note = f'factor_pit不可用({e})→本轮放行不标旗'
            fund_flags, fund_cov = {}, 0
    fund_kick: list = []
    raw = sqlite3.connect(RAW_DB, uri=True)
    members = {c: (i or '') for c, i in
               raw.execute("SELECT code, industry FROM stock_info WHERE instrument_type='stock'").fetchall()}
    raw.close()

    db = sqlite3.connect(QFQ_DB, uri=True)
    cur = db.cursor()
    ms_state = '?'
    try:
        ms_state = json.load(open(os.path.join(ROOT, "data", "market_state_probe.json"), encoding="utf-8")).get('state', '?')
    except Exception:
        pass
    rows = []
    n_scanned = n_fail = 0
    for code, ind in members.items():
        n_scanned += 1
        nm = get_name(code) or ''
        if 'ST' in nm.upper() or '退' in nm:
            continue
        # 2026-09-24 主人令: 选股宇宙=A股全市场, 排除科创板(688/689)/北交所(8/4开头)/B股(9开头)
        if str(code).startswith(('688', '689', '8', '4', '9')):
            continue
        r = cur.execute("SELECT close, high, low, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 780",
                        (code,)).fetchall()
        if len(r) < 400:
            continue
        def f3(x):
            try: return float(x)
            except Exception: return None
        closes = [f3(x[0]) for x in r]; vols = [f3(x[3]) for x in r]
        if any(v is None for v in closes[:300]): continue
        vols = [v if v is not None else 0 for v in vols]
        c = closes[0]
        if not c or c > 30 or c < 2:
            continue
        ma20 = sum(closes[:20]) / 20
        ma60 = sum(closes[:60]) / 60
        if ma20 <= ma60:
            n_fail += 1
            continue
        if len(closes) < 250:
            continue
        win = closes[:250]
        hi, lo = max(win), min(win)
        pos250 = 100 * (c - lo) / (hi - lo) if hi > lo else 50
        if pos250 >= 50:
            continue
        lo_i = closes[:250].index(lo)
        start_i = None
        for i in range(lo_i, -1, -1):
            win20 = vols[i:i+20]
            if len(win20) < 20: break
            v20 = sum(win20) / 20
            if i+1 < len(closes) and closes[i+1] and vols[i] >= 2 * v20 and (closes[i]/closes[i+1] - 1) >= 0.03:
                start_i = i
                break
        if start_i is None:
            continue
        pre60 = closes[start_i+1:start_i+61] if start_i+61 <= len(closes) else closes[start_i+1:]
        if len(pre60) >= 40:
            pre_dd = (min(pre60)/max(pre60) - 1) * 100
            if pre_dd > -25:
                continue
            v250 = sum(vols[:250]) / 250 if len(vols) >= 250 else sum(vols) / len(vols)
            pre20v = sum(vols[start_i+1:start_i+21]) / 20 if start_i+21 <= len(vols) else v250
            pre_shrink = pre20v / v250 if v250 else 1
            shrink_cap = 1.56 if ms_state == 'LOW_VOL' else 0.95
            if pre_shrink > shrink_cap:
                continue
        else:
            pre_dd, pre_shrink = 0, 1.0
        start_vr = vols[start_i] / (sum(vols[start_i+1:start_i+21])/20 if start_i+21 <= len(vols) and sum(vols[start_i+1:start_i+21]) else 1)
        vol_flag = '⚠暴量启动(预期下调)' if start_vr > 3 else ''
        off_start = (c / closes[start_i] - 1) * 100 if closes[start_i] else 999
        if off_start > 17 or off_start < -3:
            continue
        days_since = start_i
        if days_since > 40:
            continue
        yr3 = closes[:750] if len(closes) >= 750 else closes
        hi3, lo3 = max(yr3), min(yr3)
        pos3 = 100 * (c - lo3) / (hi3 - lo3) if hi3 > lo3 else 50
        off_lo3 = (c / lo3 - 1) * 100 if lo3 else 999
        if pos3 >= 55 or off_lo3 > 80:
            continue
        off = (c / ma20 - 1) * 100
        vs = sorted(vols[:250])
        vrank = 100 * sum(1 for v in vs if v <= vols[0]) / len(vs)
        flags = []
        if pos250 >= 90 and vols[0] >= max(vols[:250]):
            flags.append('S6')
        chg1 = (c / closes[1] - 1) * 100 if closes[1] else 0
        if pos250 >= 80 and vols[0] >= 1.5 * (sum(vols[:20]) / 20) and chg1 <= 1:
            flags.append('S5')
        if vrank >= 80:
            flags.append('量位')
        if off > 3:
            flags.append('乖离')
        if flags:
            continue
        ff = fund_flags.get(code)
        if ff:
            fund_kick.append(f"{code} {nm} {ff}")
            continue
        up_v = dn_v = 0.0; up_n = dn_n = 0
        for i in range(20):
            ch = closes[i] - closes[i + 1]
            if ch > 0: up_v += vols[i]; up_n += 1
            elif ch < 0: dn_v += vols[i]; dn_n += 1
        vp = (up_v / up_n) / (dn_v / dn_n) if up_n and dn_n and dn_v else 1.0
        ret20 = (c / closes[20] - 1) * 100 if closes[20] else 0
        # 2026-09-28 事件研究接入(观测标记): legacy_strong=9/15强势池口径(MA5>MA10>MA20+close>MA20+ret20>0)
        # 仅标注不参与排序/过滤; pool3三因(涨停基因∧创新高∧pos250>=80)与本扫描器低位语义互斥故不标
        ma5s = sum(closes[:5]) / 5
        ma10s = sum(closes[:10]) / 10
        legacy_strong = int(ma5s > ma10s > ma20 and c > ma20 and ret20 > 0)
        rows.append({'code': code, 'name': nm, 'ind': ind, 'px': c,
                     'pos250': round(pos250), 'off': round(off, 1), 'vp': round(vp, 2),
                     'ret20': round(ret20, 1), 'vrank': round(vrank),
                     'off_start': round(off_start, 1), 'days': days_since, 'pos3': round(pos3),
                     'off_lo3': round(off_lo3, 1), 'pre_dd': round(pre_dd, 1), 'vflag': vol_flag,
                     'legacy_strong': legacy_strong})
    # 排序: 空间大=pos250/pos3小+距段起小。2026-09-24 主人批A案(RULING_MEMO_VP_20260924): 移除vp加分——
    # 十年28327事件分层实证vp>=1.3层聚簇t=-1.32(负), 旧加分把历史期望更差层前置; vp仅留展示列不参与排序
    rows.sort(key=lambda r: (r['pos250'] + r['pos3']) / 2 - min(r['off_start'], 10) * 0.5)
    out_md = os.path.join(ROOT, "reports", f"universe_startup_scan_{datetime.now().strftime('%Y%m%d')}.md")
    out_json = os.path.join(ROOT, "reports", f"universe_startup_scan_{datetime.now().strftime('%Y%m%d')}.json")
    def load_portrait_prob():
        """画像概率呈现层(唯一对接=data/portrait_feed.json, owner=portrait_feed.py; 失败→(None,err))。
        零拦截: 只供呈现标注不参与排序/过滤(PD-026④, 同构mainline_startup_scan.load_portrait_pct)。
        池语义: 打分池=feature_engine 剔ST/close≤20元/amt20≥500万(计划书§1.2§2.2); 票价>20元=池外非缺失。"""
        try:
            pf = json.load(open(os.path.join(ROOT, "data", "portrait_feed.json"), encoding="utf-8"))
            if pf.get("unavailable"):
                return None, f"unavailable:{pf.get('unavailable')}"
            sc = {str(k).zfill(6): (round(float(v.get('prob', 0)), 4), round(float(v.get('pct', 0)), 1))
                  for k, v in (pf.get('scores_short') or {}).items() if isinstance(v, dict)}
            if not sc:
                return None, "scores_short为空"
            return {'scores': sc, 'date': pf.get('snapshot_date', '?')}, ''
        except Exception as e:
            return None, f'{e}'

    pt, pterr = load_portrait_prob()
    POOL_CAP = 20.0  # =portrait_agent/feature_engine.py PRICE_CAP 真源(计划书§1.2)
    for r in rows:
        v = pt['scores'].get(r['code']) if pt else None
        if v is not None:
            r['prob'], r['pct'] = v
            r['prob_status'] = ''
        elif r['px'] > POOL_CAP:
            r['prob'] = r['pct'] = None
            r['prob_status'] = '池外'
        else:
            r['prob'] = r['pct'] = None
            r['prob_status'] = '缺分异常'
    json.dump({'scanned': n_scanned, 'state': ms_state,
               'fund_gate': {'cut': fund_cut, 'covered': fund_cov, 'kicked': fund_kick,
                             'note': fund_note},
               'hits': rows}, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    with open(out_md, 'w', encoding='utf-8') as f:
        f.write(f"# 全宇宙低位启动扫描（{datetime.now().strftime('%Y-%m-%d %H:%M')}·只读呈报·赢家画像v3·数据基准={qfq_max}）\n\n")
        if qfq_max != _today:
            f.write(f"> 🔴 数据新鲜度: qfq最新bar={qfq_max}≠今日{_today}——本报告为{qfq_max}基准的过期口径, 勿当当日截面使用\n")
        f.write(f"> universe={n_scanned} | 市场状态={ms_state} | 命中={len(rows)} | 启动前缩量阈值cap={'1.56' if ms_state=='LOW_VOL' else '0.95'} | 打分=纯位置分(vp加分已于2026-09-24移除,裁决RULING_MEMO_VP_A案)\n")
        f.write("> 口径: 低位(pos250<50,pos3<55,距三年低<80) + 主升段起(距段起-3%~+17%,2-40日) + 多头排列 + 排雷(S5/S6/量位/乖离/业绩E1-E3)\n")
        if fund_note:
            f.write(f"> 业绩趋势闸(PIT): {fund_note}\n")
        else:
            _kick_note = '; '.join(fund_kick[:6]) + ('…' if len(fund_kick) > 6 else '')
            f.write(f"> 业绩趋势闸(PIT零前视,基准{fund_cut},覆盖{fund_cov}票): 剔除{len(fund_kick)}只 {_kick_note}\n")
        f.write(f"> 画像概率: {(pt['date'] + ' feed快照·呈现级零拦截') if pt else ('不可用(' + pterr + ')→列空')} | 池外=价>20元(设计池边界, 非缺陷)\n")
        if ms_state == 'LOW_VOL':
            f.write("> LOW_VOL 第零问警示: 仅供研究, 买入须主人明示\n\n")
        else:
            f.write("> 仅供研究呈报(零交易权), 买入须主人明示并走 trade_executor 双层防线\n\n")
        f.write("| 代码 | 名称 | 行业 | 价(qfq) | pos250 | pos3 | 距段起% | 启动N日 | 量价vp | ret20 | 量位 | 强池 | 概率 | 分位 | 备注 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for r in rows:
            prob_cell = f"{r['prob']:.4f}" if r['prob'] is not None else r['prob_status']
            pct_cell = f"{r['pct']}%" if r['pct'] is not None else ''
            f.write(f"| {r['code']} | {r['name']} | {r['ind']} | {r['px']} | {r['pos250']}% | {r['pos3']}% | {r['off_start']:+}% | {r['days']} | {r['vp']} | {r['ret20']}% | {r['vrank']} | {r['legacy_strong']} | {prob_cell} | {pct_cell} | {r['vflag']} |\n")
    print(f"[USCAN-OK] scanned={n_scanned} hits={len(rows)} -> {out_md}")
    for r in rows[:25]:
        print(r['code'], r['name'], r['ind'], 'px', r['px'], 'pos250', r['pos250'], 'pos3', r['pos3'], 'off_start', r['off_start'], 'days', r['days'], 'vp', r['vp'], r['vflag'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
