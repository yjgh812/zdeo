# -*- coding: utf-8 -*-
"""选股呈报前置三查检查器（2026-09-18 主人纠偏落地件）

背景: 主人 9/18 指出「每日复盘的情况和盘前消息面，选股的时候并没有考虑这些」——
山金国际推荐因漏查美联储三年首次加息被撤回。本工具把前置三查固化为可执行流程件。

三查（缺项禁止呈报）:
  查一 复盘层: 市场状态探针 + 最新每日复盘报告新鲜度 + 行业主线量能状态
  查二 宏观事件层: 最近快讯按宏观关键词过滤（加息/降息/议息/央行/关税/地缘等）
  查三 个股消息面: 候选逐只拉最近新闻标题 + 位置分位（qfq）+ 名称

用法:
  python selection_preflight_check.py --codes 000933,002246
  python selection_preflight_check.py            # 只跑查一查二
输出: reports/selection_preflight_YYYYMMDD_HHMM.md + 控制台摘要
定位: 只读呈报合规层，不拦截不下单（与闸门族解耦）；与 9/17 证伪的三问检查单
（判别规则）不同，本件是流程合规（呈报完整性）。
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

MACRO_KW = ('加息', '降息', '议息', '美联储', '央行', 'LPR', '降准', 'MLF', '逆回购',
            '关税', '制裁', '断供', '地缘', '冲突', '战争', '局势', '政策', '国务院',
            '证监会', '印花税', '汇率', '通胀', 'CPI', 'PPI', 'GDP')

# qfq 权威主库(2026-09-19切: 原连 ROOT/data 副本滞后1日——9/19实证603230乖离+24.8%@副本9/17 vs +34.4%@主库9/18,
# 执行时点雷基于旧价会低估; 与 bp_ep_exec_brief.py QFQ_DB 同源对齐)
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

FAILS = []  # 结构化缺项


def sec(title):
    return '\n## ' + title + '\n'


def main():
    import socket
    socket.setdefaulttimeout(15)  # 全局网络超时, 覆盖查二/查三全部 akshare 请求, 防挂起
    ap = argparse.ArgumentParser()
    ap.add_argument('--codes', default='', help='候选代码,逗号分隔')
    ap.add_argument('--type', default='bp_ep', choices=['bp_ep', 'momentum'],
                    help='信号类型: bp_ep=基本面调仓(乖离/量分位雷适用,2016-2026跨期7/7与9/11验证); '
                         'momentum=动量类(两雷不适用,ml臂跨期FAIL)')
    args = ap.parse_args()
    codes = [c.strip() for c in args.codes.split(',') if c.strip()]
    C1_LIST = []
    today = datetime.now().strftime('%Y%m%d')
    out_path = os.path.join(ROOT, 'reports',
                            f"selection_preflight_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    L = [f"# 选股呈报前置三查报告（{datetime.now().strftime('%Y-%m-%d %H:%M')}）",
         '', '> 生成: selection_preflight_check.py ｜ 规则: 三段齐全才可呈报, 任何缺项=禁止呈报并补查', '']

    # ---------- 查一 复盘层 ----------
    L.append(sec('查一 · 复盘层（市场状态/最新复盘/主线量能）'))
    ms = {}
    try:
        ms = json.load(open(os.path.join(ROOT, 'data', 'market_state_probe.json'), encoding='utf-8'))
        fresh = '✅' if ms.get('date') == today or ms.get('asof') >= datetime.now().strftime('%Y%m%d') else \
                f"⚠️ 非最新(数据日 {ms.get('date')}, 基准 {ms.get('asof')})"
        L.append(f"- 市场状态探针: **{ms.get('state')}**（前值 {ms.get('prev_state')}, pos250中位 {ms.get('pos250_med')}, "
                 f"换手能量 {ms.get('turn_energy_5v20')}, 生成 {ms.get('generated')}）{fresh}")
        if ms.get('state') == 'LOW_VOL':
            L.append('  - 🔴 **第零问不通过（2026-09-19 自省落地）：LOW_VOL 弱市全市场上涨占比约 29%/中位约 -3%，'
                     '"能涨"系统性不可得——默认动作=不买/降频；任何买入呈报须附主人明示的强买理由**')
    except Exception as e:
        FAILS.append(f'查一-市场状态探针: {e}')
        L.append(f"- 市场状态探针: ❌ 不可用（{e}）")
    try:
        revs = sorted(glob.glob(os.path.join(ROOT, 'reports', 'daily_review*.md')) +
                      glob.glob(os.path.join(ROOT, 'reports', 'a_share_daily_review*.md')),
                      key=os.path.getmtime, reverse=True)
        if revs:
            r0 = revs[0]
            age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(r0))).days
            tag = '✅' if age <= 1 else f'⚠️ 已 {age} 天未更新（22:00 链产物?）'
            L.append(f"- 最新每日复盘: `{os.path.basename(r0)}` {tag}")
        else:
            FAILS.append('查一-每日复盘: 无文件')
            L.append('- 最新每日复盘: ❌ 未找到')
    except Exception as e:
        FAILS.append(f'查一-每日复盘: {e}')
    try:
        iv = json.load(open(os.path.join(ROOT, 'data', 'industry_volume_state_latest.json'), encoding='utf-8'))
        c1 = iv.get('c1_industries', [])
        C1_LIST = [str(x) for x in c1]
        L.append(f"- 行业量能主线(C1): {('、'.join(map(str, c1[:6])) if c1 else '无')}（数据日 {iv.get('date')}）")
    except Exception as e:
        C1_LIST = []
        L.append(f"- 行业量能状态: ❌ 不可用（{e}）")

    # ---------- 查二 宏观事件层 ----------
    L.append(sec('查二 · 宏观事件层（最近快讯·宏观关键词过滤）'))
    got = 0
    try:
        import socket
        socket.setdefaulttimeout(15)  # 网络源统一超时, 防挂起
        import akshare as ak
        hits = []
        src_used = None
        for src_name, fetch in (
            ('新浪全球快讯', lambda: ak.stock_info_global_sina()),
            ('财联社电报', lambda: ak.stock_info_global_cls()),
            ('东财全球快讯', lambda: ak.stock_info_global_em()),
        ):
            try:
                df = fetch()
                if df is None or not len(df):
                    continue
                for _, row in df.head(200).iterrows():
                    txt = ' '.join(str(v) for v in row.values)
                    if any(k in txt for k in MACRO_KW):
                        t = str(row.iloc[0]) if len(row) else ''
                        h = str(row.iloc[1]) if len(row) > 1 else txt[:60]
                        hits.append(f'[{t}] {h}')
                        if len(hits) >= 15:
                            break
                src_used = src_name
                if hits:
                    break
            except Exception:
                continue
        got = len(hits)
        if hits:
            L.extend([f'- {h}' for h in hits])
            L.append(f'\n（数据源: {src_used}, 共 {got} 条命中；呈报前逐条评估对候选品种的方向性影响。'
                     f'注意: 快讯流只含正在发生的消息——昨夜已落地决议类大事（如 FOMC 结果）以查一引用的最新复盘为准）')
        else:
            L.append(f'- 三源均无宏观关键词命中（尝试源均失败或平静窗口）——⚠️ 若为源失败须人工补查宏观事件后呈报')
    except Exception as e:
        FAILS.append(f'查二-宏观快讯: {e}')
        L.append(f'- ❌ 快讯源全部不可用（{e}）——呈报前须人工补查宏观事件，禁止跳过')

    # ---------- 查三 个股消息面 ----------
    L.append(sec('查三 · 个股消息面（候选逐只）'))
    if not codes:
        L.append('-（未提供 --codes，本段跳过；带候选运行时逐只输出）')
    else:
        from stock_name_lookup import get_name
        conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
        cur = conn.cursor()
        # 主线对照(2026-09-19 主人令提升选股落地): 通达信行业 vs 量能C1主线
        try:
            rawdb = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro", uri=True)
            ind_of = dict(rawdb.execute("SELECT code, industry FROM stock_info WHERE instrument_type='stock'").fetchall())
            rawdb.close()
        except Exception:
            ind_of = {}
        import warnings
        warnings.filterwarnings('ignore')
        # 画像分数呈现级标注(PD-026④同构: 零拦截权纯信息, S8同构; 10/2主人令全面检查后补接线)
        # 数据源=data/portrait_feed.json(portrait_feed.py 唯一对接owner, 每日全市场3100+只)
        # A/B首验: fwd5 +2.36pp 短期正(达标) / fwd10 -3.42pp 中期反号 → 仅呈现不拦截
        pf_scores = {}
        _pf_path = os.path.join(ROOT, 'data', 'portrait_feed.json')
        try:
            _pf = json.load(open(_pf_path, encoding='utf-8'))
            _pf_date = _pf.get('snapshot_date', '?')
            for k, v in (_pf.get('scores_short') or {}).items():
                if isinstance(v, dict) and 'prob' in v:
                    pf_scores[str(k).zfill(6)] = (float(v['prob']), float(v.get('pct', 0)), _pf_date)
                elif isinstance(v, (int, float)):
                    pf_scores[str(k).zfill(6)] = (float(v), 50.0, _pf_date)
            _pf_n = len(pf_scores)
            for k, v in (_pf.get('scores_mid') or {}).items():
                if isinstance(v, dict) and 'prob' in v:
                    k6 = str(k).zfill(6)
                    if k6 in pf_scores and len(pf_scores[k6]) == 3:
                        pf_scores[k6] = pf_scores[k6] + (float(v['prob']), float(v.get('pct', 0)))
            _pf_p25 = sorted(pf_scores.values(), key=lambda x: x[0], reverse=True)[int(_pf_n * 0.25)][0] if _pf_n > 10 else None
        except Exception:
            _pf_date = '?'
        for code in codes:
            c6 = code[-6:].zfill(6)
            name = get_name(c6) or '(名称未命中)'
            ind = ind_of.get(c6, '?')
            # 别名桥匹配(与 mainline_startup_scan.SW_TO_TDX 同源; 直连子串失败时走桥)
            c1hit = any(str(ind) in str(x) or str(x) in str(ind) for x in C1_LIST)
            if not c1hit and ind != '?':
                try:
                    from mainline_startup_scan import SW_TO_TDX
                    for c1n in C1_LIST:
                        kws = SW_TO_TDX.get(c1n, []) + [c1n]
                        if any(k in str(ind) or str(ind) in k for k in kws):
                            c1hit = True
                            break
                except Exception:
                    pass
            ml_tag = '✅C1量能主线命中' if c1hit else '⚠️主线外(主线票大涨密度4倍于全场,主线外票=补涨等待型)'
            L.append(f'\n### {c6} {name}｜行业: {ind}｜{ml_tag}')
            # 画像分数标注(呈现级, PD-026④/S8同构零拦截)
            pf_hit = pf_scores.get(c6)
            if pf_hit:
                pf_score, pf_pct, pf_dt = pf_hit[0], pf_hit[1], pf_hit[2]
                pf_tag = f'画像short={pf_score:.4f}(前{pf_pct:.0f}%)'
                if len(pf_hit) >= 5:
                    pf_tag += f' mid={pf_hit[3]:.4f}(前{pf_hit[4]:.0f}%)'
                pf_tag += f' [{pf_dt}]'
                if _pf_p25 is not None and pf_score < _pf_p25:
                    pf_tag += ' 🟡末quartile(短期排序有增量但中期反号,仅参考)'
                L.append(f'- 画像: {pf_tag}')
            try:
                r = cur.execute('SELECT date, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 250',
                                (c6,)).fetchall()
                closes = [x[1] for x in r]
                if len(closes) >= 60:
                    c = closes[0]
                    def pos(cl):
                        hi, lo = max(cl), min(cl)
                        return round(100 * (c - lo) / (hi - lo)) if hi > lo else 50
                    L.append(f"- 位置: pos250={pos(closes[:250])}% pos60={pos(closes[:60])}% 收盘 {c:.2f}(qfq复权价,有分红史的票与行情软件实际价略有差异) （数据日 {r[0][0]}）")
            except Exception as e:
                FAILS.append(f'查三-{c6}位置: {e}')
                L.append(f'- 位置: ❌ qfq 不可用（{e}）')
            try:
                ndf = ak.stock_news_em(symbol=c6)
                if ndf is not None and len(ndf):
                    rows = [(str(nr.get('发布时间', '')), str(nr.get('新闻标题', '')))
                            for _, nr in ndf.head(20).iterrows()]
                    rows.sort(reverse=True)  # 按时间倒序
                    shown = 0
                    for t, title in rows:
                        # 财报/公告类硬信息豁免时间过滤
                        is_hard = any(k in title for k in ('中报', '年报', '季报', '净利润', '业绩', '公告', '中标', '合同'))
                        # 剔除万金油名单类条目(对个股无信息量)
                        if any(k in title for k in ('站上年线', '站上半年线', '社保基金', '筹码连续集中', '融资客青睐', '资金流入榜', '筹码集中股')):
                            continue
                        if not is_hard:
                            import re
                            m = re.match(r'(20\d\d-\d\d-\d\d)', t)
                            if m:
                                from datetime import date
                                try:
                                    nd = date.fromisoformat(m.group(1))
                                    if (date.today() - nd).days > 14:
                                        continue
                                except ValueError:
                                    pass
                        L.append(f"- [{t}] {title}")
                        shown += 1
                        if shown >= 5:
                            break
                    if shown == 0:
                        L.append('- 新闻: 最近无有效个股新闻（名单类已滤除）')
                else:
                    L.append('- 新闻: 近期无个股新闻')
            except Exception as e:
                FAILS.append(f'查三-{c6}新闻: {e}')
                L.append(f'- 新闻: ❌ 源不可用（{e}）——人工补查后再呈报')
        conn.cursor().close()
        conn.close()

    # ---------- 查四 量价择时层 v2（量价关系体系+换手分位雷+趋势质量+风控参数） ----------
    L.append(sec('查四 · 量价择时层 v2（排雷 / 量价四象限 / 换手分位 / 趋势质量 / 风控参数）'))
    L.append('> 同源族提示(数学审计20260920): ret20/bias60/pos60 同属动量-位置族(ρ至0.884, 三维实一维)——'
             '本层 ret20(回踩判定)与位置行 pos60 同现时按 1 族独立信息计, 勿叠加读作多重确认。')
    if not codes:
        L.append('-（未提供 --codes，本段跳过；带候选运行时逐只输出）')
    else:
        from stock_name_lookup import get_name as _gn
        conn4 = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
        cur4 = conn4.cursor()
        for code in codes:
            c6 = code[-6:].zfill(6)
            name = _gn(c6) or '(名称未命中)'
            try:
                r = cur4.execute('SELECT date, close, high, low, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 260',
                                 (c6,)).fetchall()
                if len(r) < 70:
                    L.append(f'- {c6} {name}: ❌ qfq 数据不足（{len(r)} 行）')
                    continue
                dates = [x[0] for x in r]; closes = [x[1] for x in r]
                highs = [x[2] for x in r]; lows = [x[3] for x in r]; vols = [x[4] for x in r]
                c = closes[0]
                ma20 = sum(closes[:20]) / 20
                ma60 = sum(closes[:60]) / 60
                ma20v = sum(vols[:20]) / 20
                v_today = vols[0]
                v250max = max(vols[:250]) if len(vols) >= 250 else max(vols)
                pos250 = round(100 * (c - min(closes[:250])) / (max(closes[:250]) - min(closes[:250]))) \
                    if len(closes) >= 250 and max(closes[:250]) > min(closes[:250]) else None
                chg1 = round((c / closes[1] - 1) * 100, 2) if closes[1] else None

                # --- 排雷组 ---
                flags = []
                if pos250 is not None and pos250 >= 90 and v_today >= v250max:
                    flags.append('🔴S6高位天量(fwd20实测-3.2%/胜率33%)')
                if pos250 is not None and pos250 >= 80 and v_today >= 1.5 * ma20v and (chg1 is not None and chg1 <= 1):
                    flags.append('🟡S5高位放量滞涨(-1.2pp)')
                # S8 昨涨停追入排雷(PD-026④, 主人2026-10-01「按推荐继续」批准, 呈现级非拦截):
                # 昨日收盘涨停(精确涨停价口径)→今日追入警示。证据=四分型fwd20全负-0.6~-3.1%,
                # 一字板+1.84%系买不到假象; 与6%禁追终裁互补(6%管当日,S8管昨涨停次日)。
                if len(closes) >= 3 and closes[2]:
                    _lim8 = 0.20 if str(c6).startswith(('300', '301')) else 0.10
                    if closes[1] >= round(closes[2] * (1 + _lim8), 2) - 0.005:
                        flags.append('🟡S8昨涨停追入排雷(四分型fwd20全负-0.6~-3.1%;呈现级标注非拦截,PD-026④)')
                # 换手分位雷: 量在近250日的分位(流通股本不变时≈换手分位)——8-9月实证>=80分位=86%亏损来源
                v_rank = 0
                if len(vols) >= 60:
                    v_sorted = sorted(vols[:250] if len(vols) >= 250 else vols)
                    v_rank = round(100 * sum(1 for v in v_sorted if v <= v_today) / len(v_sorted))
                    if v_rank >= 80:
                        # 分信号(2026-09-19跨期验证同乖离雷: bp_ep臂9/11年负向PASS/ml臂3/7 FAIL不成立)
                        if args.type == 'bp_ep':
                            flags.append(f'🔴当日量{v_rank}分位(bp_ep实证雷:8-9月86%亏损+回测9/11年负向双验证;ml臂跨期不成立)')
                        else:
                            flags.append(f'🟡当日量{v_rank}分位(动量类参考:ml臂跨期3/7不成立,8-9月混合样本112笔86%亏损警示)')
                elif v_today >= 1.5 * ma20v:
                    flags.append('🟡当日量≥1.5×MA20(短窗分位不可得,以倍数警示)')

                # --- 量价四象限(近20日): 涨日均量/跌日均量 ---
                up_v, dn_v, up_n, dn_n = 0.0, 0.0, 0, 0
                for i in range(min(20, len(closes) - 1)):
                    chg = closes[i] - closes[i + 1]
                    if chg > 0:
                        up_v += vols[i]; up_n += 1
                    elif chg < 0:
                        dn_v += vols[i]; dn_n += 1
                vp_ratio = round((up_v / up_n) / (dn_v / dn_n), 2) if up_n and dn_n and dn_v else None
                if vp_ratio is None:
                    vp_txt = '单边市(无对应方向日)'
                elif vp_ratio >= 1.2:
                    vp_txt = f'配合({vp_ratio}× 涨日量>跌日量=健康)'
                elif vp_ratio <= 0.8:
                    vp_txt = f'背离({vp_ratio}× 涨日缩量/跌日放量=虚涨风险)'
                else:
                    vp_txt = f'中性({vp_ratio}×)'

                # --- MA结构 + 乖离 + 回踩买点(对齐 pullback_buy_alert 口径) ---
                v5 = sum(vols[:5]) / 5
                vol_state = '放量(≥1.5×)' if v5 >= 1.5 * ma20v else ('缩量(≤0.7×)' if v5 <= 0.7 * ma20v else '常态')
                ma_align = '多头(MA20>MA60)' if ma20 > ma60 else '空头(MA20<MA60)'
                off_ma20 = round((c / ma20 - 1) * 100, 1)
                pb = ''
                try:
                    ret20 = (c / closes[20] - 1) if len(closes) >= 21 and closes[20] else None
                    ma20_5ago = sum(closes[5:25]) / 20 if len(closes) >= 45 else None
                    if ret20 is not None and ma20_5ago:
                        # 口径=pullback_buy_alert.py: ret20>=8% + MA20走平/上行 + |close/MA20-1|<=3%
                        # 注意: 规则库S3实证"缩量回踩无超额"——回踩命中=非雷亦无统计优势, 不构成买入理由, 仅择时参考
                        if ret20 >= 0.08 and ma20 >= ma20_5ago * 0.999 and abs(c / ma20 - 1) <= 0.03:
                            pb = ' ｜ 🎯回踩买点命中(pullback口径,S3实证无超额仅择时参考)'
                        elif ret20 >= 0.08 and abs(c / ma20 - 1) <= 0.08:
                            pb = f' ｜ 接近回踩区(乖离{off_ma20:+.1f}%,贴线±3%外)'
                except Exception:
                    pass
                # 乖离实证雷(分信号类型: 2026-09-19 跨期验证 bp_ep臂7/7年PASS/ml臂FAIL——仅基本面调仓类适用)
                if off_ma20 > 3:
                    if args.type == 'bp_ep':
                        pb += ' ｜ 🟡乖离>+3%实证雷(bp_ep类:本地148笔14.9%+回测7/7年负向双验证;ml动量类不适用)'
                    else:
                        pb += ' ｜ 注: 乖离>+3%——动量类策略本性,ml臂跨期不成立不标雷'
                elif off_ma20 < -3:
                    pb += ' ｜ 注: 破线组bp_ep臂55.4%优于贴线(回测354笔)+实盘62.5%(n16)——回撤票非趋势票'

                # --- 60日趋势质量: 涨幅 × 近30/前30量能变化 ---
                chg60 = round((c / closes[59] - 1) * 100, 1) if len(closes) >= 60 and closes[59] else None
                v_recent30 = sum(vols[:30]) / 30
                v_prior30 = sum(vols[30:60]) / 30
                v_trend = '带量' if v_recent30 >= 1.15 * v_prior30 else ('失量' if v_recent30 <= 0.85 * v_prior30 else '平量')

                # --- 风控参数: ATR14% + 止损锚(近20日低点 与 -8%价取高者) ---
                trs = []
                for i in range(min(14, len(closes) - 1)):
                    tr = max(highs[i] - lows[i], abs(highs[i] - closes[i + 1]), abs(lows[i] - closes[i + 1]))
                    trs.append(tr)
                atr_pct = round(100 * (sum(trs) / len(trs)) / c, 1) if trs and c else None
                lo20 = min(lows[:20])
                stop_anchor = max(lo20, round(c * 0.92, 2))
                stop_pct = round(100 * (stop_anchor / c - 1), 1)

                L.append(f"- {c6} {name}（数据日 {dates[0]}）:")
                L.append(f"  - 排雷: {' ｜ '.join(flags) if flags else '无命中（S5/S6/量分位均安全）'}")
                L.append(f"  - 量价关系: {vp_txt} ｜ 量能5日/MA20={vol_state} ｜ 60日{('+' + str(chg60)) if chg60 is not None else '?'}%为{v_trend}（注:四象限/失量本地241笔判别力未证实,描述层）")
                L.append(f"  - 择时: {ma_align} ｜ 距MA20 {off_ma20:+.1f}% ｜ pos250={pos250}%{pb}")
                L.append(f"  - 风控: ATR14={atr_pct}%/日 ｜ 建议止损锚 {stop_anchor}（{stop_pct}%，取20日低点与-8%孰高）")
            except Exception as e:
                FAILS.append(f'查四-{c6}: {e}')
                L.append(f'- {c6} {name}: ❌ 量价层计算失败（{e}）')
        cur4.close(); conn4.close()

    # ---------- 查五 同态历史概率层（状态向量→查351万日历史胜率） ----------
    def f3(x):
        try: return float(x)
        except Exception: return None

    L.append(sec('查五 · 同态历史概率层（4维状态向量 → 351万日历史同态胜率）'))
    if not codes:
        L.append('-（未提供 --codes，本段跳过）')
    else:
        try:
            from state_history_lookup import compute_state_vector, lookup_state, _load_table
            table = _load_table()
            conn5 = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
            cur5 = conn5.cursor()
            for code in codes:
                c6 = code[-6:].zfill(6)
                nm5 = get_name(c6) or '(?)'
                try:
                    h = cur5.execute('SELECT date, close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 780',
                                     (c6,)).fetchall()
                    rows5 = [(d, f3(c2), f3(v)) for d, c2, v in h if f3(c2) is not None]
                    if len(rows5) < 300:
                        L.append(f'- {c6} {nm5}: ❌ 数据不足({len(rows5)}行)')
                        continue
                    cl5 = [r[1] for r in rows5]; vl5 = [r[2] or 0 for r in rows5]
                    avg5 = sum(vl5) / len(vl5)
                    state_str, detail = compute_state_vector(cl5, vl5, avg5)
                    info = lookup_state(state_str)
                    trend_s = '多头' if detail['trend'] else '空头'
                    dim_s = f"trend={detail['trend']} shrink={detail['shrink']} pos250={detail['pos250']} r20={detail['r20']}"
                    if info:
                        wr = info['win_rate']; mn = info['mean_fwd20']; nn = info['n']
                        emoji = '🟢' if wr >= 50 else ('🟡' if wr >= 45 else '🔴')
                        L.append(f"- {c6} {nm5}: {emoji} 状态[{state_str}] 同态n={nn:,} **胜率{wr}%** fwd20均值{mn:+.2f}% ｜ {dim_s}")
                    else:
                        L.append(f"- {c6} {nm5}: 状态[{state_str}] ⚠️同态样本不足(n<30)")
                except Exception as e:
                    FAILS.append(f'查五-{c6}: {e}')
                    L.append(f'- {c6} {nm5}: ❌ 同态概率计算失败（{e}）')
            cur5.close(); conn5.close()
        except Exception as e:
            FAILS.append(f'查五: {e}')
            L.append(f'- ❌ 同态概率层初始化失败（{e}）')

    # ---------- 总判定 ----------
    ok1 = not any(x.startswith('查一') for x in FAILS)
    ok2 = not any(x.startswith('查二') for x in FAILS)
    ok3 = not codes or not any(x.startswith('查三') for x in FAILS)
    ok4 = not codes or not any(x.startswith('查四') for x in FAILS)
    ok5 = not codes or not any(x.startswith('查五') for x in FAILS)
    verdict = '✅ PREFLIGHT PASS（五段齐全, 可呈报）' if (ok1 and ok2 and ok3 and ok4 and ok5) else \
              '⛔ PREFLIGHT INCOMPLETE（存在缺项, 禁止呈报, 补查后重跑）'
    L.append(sec('总判定'))
    L.append(f'- 查一复盘层: {"✅" if ok1 else "❌"} ｜ 查二宏观层: {"✅" if ok2 else "❌"} ｜ 查三个股层: {"✅" if ok3 else "❌（未提供候选则跳过）"} ｜ 查四量价择时层: {"✅" if ok4 else "❌"} ｜ 查五同态概率层: {"✅" if ok5 else "❌"}')
    L.append(f'- **{verdict}**')
    if FAILS:
        L.append('- 缺项明细: ' + '; '.join(FAILS))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8').write('\n'.join(L))
    print('\n'.join(L[-6:]))
    print(f'\n[selection_preflight_check] 报告: {out_path}')
    sys.exit(0 if (ok1 and ok2 and ok3) else 2)


if __name__ == '__main__':
    main()
