# -*- coding: utf-8 -*-
"""主线低位启动扫描器（2026-09-19 落地·主人"以胜率以盈利为前提继续"授权）

定位: 四层选股结构的正向出口——第零问通过时, 主动扫描"主线行业内低位+启动确认+排雷全过"的票。
形态依据: 六赢家归因(内蒙新华603230/大连热电600719=主线内低位埋伏后长进大涨群体)
        + 9/18解剖(主线票大涨密度4倍于全场, 低位组前5日-0.8%=主线外补涨等不来)。
流程: C1量能主线行业 → stock_info成分 → qfq逐票(pos250/MA结构/乖离/量分位/S5S6雷) →
     过滤(低位<45%+多头排列+排雷+价<30) → 按量价配合度排序 → Top10 呈报清单
边界: 只读选股呈报件零交易权; 情绪主线(复盘记录)与量能主线(C1)不一致时以两者并集人工核对;
     LOW_VOL 状态下输出第零问警示(清单仅供研究备战, 不构成买入指令)。
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
OUT_MD = os.path.join(ROOT, "reports", f"mainline_startup_scan_{datetime.now().strftime('%Y%m%d')}.md")


# 申万/量能库行业名 → 通达信行业关键词桥(2026-09-19; 匹配不到时显著报警, 禁止静默归零)
SW_TO_TDX = {
    '建筑材料': ['水泥', '玻璃', '其他建材', '装修装饰'],
    '建筑装饰': ['建筑工程', '装修装饰', '工程机械'],
    '银行': ['银行'], '半导体': ['半导体', '元器件'], '电子': ['电子', '元器件', '光学光电子'],
    '电力设备': ['电力设备', '电气设备', '电网设备', '光伏'], '医药生物': ['医药', '医疗', '生物制'],
    '食品饮料': ['食品', '饮料', '白酒', '酿酒'], '计算机': ['计算机', '软件', 'IT'],
    '通信': ['通信'], '煤炭': ['煤炭'], '石油石化': ['石油', '石化', '炼化'],
    '有色金属': ['有色', '铝', '铜', '铅锌', '黄金', '小金属', '金属新'],
    '钢铁': ['钢铁', '钢'], '基础化工': ['化工', '化学', '化纤', '塑料'],
    '机械设备': ['机械', '机床', '工程机械'], '国防军工': ['军工', '航天', '航空', '兵装'],
    '汽车': ['汽车', '汽配', '乘用', '商用'], '家用电器': ['家电', '白色家', '小家电'],
    '农林牧渔': ['农业', '饲料', '种植', '养殖', '牧渔', '渔业'],
    '传媒': ['传媒', '出版', '影视', '广告', '游戏'],
    '房地产': ['房地产', '地产'], '交通运输': ['交通', '航运', '港口', '铁路', '公路', '物流'],
    '公用事业': ['电力', '燃气', '供气', '水务', '环保'], '非银金融': ['证券', '保险', '信托', '多元金融'],
}


def expand_c1(c1_list, tdx_inds):
    """C1 行业名 → 通达信行业名集合(别名桥+子串双向)"""
    out, missed = set(), []
    for c1n in c1_list:
        kws = SW_TO_TDX.get(c1n, []) + [c1n]
        hit = False
        for t in tdx_inds:
            if any(k in t or t in k for k in kws):
                out.add(t); hit = True
        if not hit:
            missed.append(c1n)
    return out, missed


# 扫描自身排序键(唯一owner; 画像软权重影子实验 import 同键对比, 禁复制)
BASE_SORT_KEY = lambda x: (not (5 <= x['off_start'] <= 10), -x['vp'], abs(x['off_start'] - 7.5))


def load_portrait_pct():
    """画像呈现层读取(唯一对接=data/portrait_feed.json, owner=portrait_feed.py; 失败→(None, err))。
    零拦截: 只供呈现标注, 不参与排序/过滤(PD-026④/S8同构); 软权重升级须过 prereg 影子三门+主人批准。"""
    try:
        pf = json.load(open(os.path.join(ROOT, "data", "portrait_feed.json"), encoding="utf-8"))
        if pf.get("unavailable"):
            return None, f"unavailable:{pf.get('unavailable')}"
        pct = {str(k).zfill(6): float(v.get('pct', 0)) if isinstance(v, dict) else float(v)
               for k, v in (pf.get('scores_short') or {}).items()}
        return {'pct': pct, 'date': pf.get('snapshot_date', '?')}, ''
    except Exception as e:
        return None, f'{e}'


def scan_candidates():
    """六层过滤扫描核心(纯计算, 抽取自原main 2026-10-09): 返回 (rows全幸存者, ctx)。
    呈报(main)与画像软权重影子实验共用本函数——扫描语义单一owner, 影子import禁复制。"""
    # 2026-10-08 新鲜度闸: 夜链(22:00)更新前手动重扫会静默用旧bar(当日晚间踩坑实锤)——终端横幅+报告头双标注
    _probe = sqlite3.connect(QFQ_DB, uri=True)
    qfq_max = _probe.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    _probe.close()
    _today = datetime.now().strftime("%Y%m%d")
    if qfq_max != _today:
        print(f"🔴 [数据新鲜度] qfq库最新bar={qfq_max} ≠ 今日{_today}——本扫描基于{qfq_max}收盘数据, 非当日口径")
    else:
        print(f"✅ [数据新鲜度] qfq库最新bar={qfq_max}")
    # 1) 主线行业
    try:
        iv = json.load(open(os.path.join(ROOT, "data", "industry_volume_state_latest.json"), encoding="utf-8"))
        c1 = [str(x) for x in iv.get("c1_industries", [])]
        c1_date = iv.get("date")
    except Exception as e:
        raise RuntimeError(f'行业量能状态不可读: {e}') from e
    if not c1:
        raise RuntimeError('C1 主线为空')
    # 2) 主线成分(别名桥)
    raw = sqlite3.connect(RAW_DB, uri=True)
    ind_of = {}
    tdx_inds = set()
    for c, i in raw.execute("SELECT code, industry FROM stock_info WHERE instrument_type='stock'").fetchall():
        if i:
            ind_of[c] = i
            tdx_inds.add(i)
    raw.close()
    tdx_hit, missed = expand_c1(c1, tdx_inds)
    if missed:
        print(f"[MLSCAN-WARN] C1 行业别名桥未命中(检查命名): {missed} | 通达信共{len(tdx_inds)}类")
    members = {c: i for c, i in ind_of.items() if i in tdx_hit}
    # 3) 逐票扫描
    db = sqlite3.connect(QFQ_DB, uri=True)
    cur = db.cursor()
    ms_state = '?'
    try:
        ms_state = json.load(open(os.path.join(ROOT, "data", "market_state_probe.json"), encoding="utf-8")).get('state', '?')
    except Exception:
        pass
    rows = []
    for code, ind in members.items():
        nm = get_name(code) or ''
        if 'ST' in nm.upper() or '退' in nm:  # 宇宙规则: 剔 ST/退市
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
        if not c or c > 30 or c < 2:  # 价格铁律
            continue
        ma20 = sum(closes[:20]) / 20
        ma60 = sum(closes[:60]) / 60
        if ma20 <= ma60:  # 启动确认: 多头排列
            continue
        if len(closes) < 250:
            continue
        win = closes[:250]
        hi, lo = max(win), min(win)
        pos250 = 100 * (c - lo) / (hi - lo) if hi > lo else 50
        if pos250 >= 50:  # 赢家画像: 一年低位
            continue
        # ===== 赢家波段锚(2026-09-19 波段解剖结论) =====
        # 主升段起点 = 一年低点后首个放量大阳(量>=2×MA20量 且 涨>=3%), 数据倒序(0=最新)
        lo_i = closes[:250].index(lo)
        start_i = None
        for i in range(lo_i, -1, -1):  # 从低点向今天扫, 首个放量大阳=段起
            win20 = vols[i:i+20]
            if len(win20) < 20: break
            v20 = sum(win20) / 20
            if i+1 < len(closes) and closes[i+1] and vols[i] >= 2 * v20 and (closes[i]/closes[i+1] - 1) >= 0.03:
                start_i = i
                break
        if start_i is None:
            continue  # 无主升启动信号
        # ===== 12.85万段统计规范(2026-09-19 全市场采集): 大波段启动前画像 =====
        # 启动前60日跌幅>=25%(大波段中位-28%,5年恒定条件) + 缩量阈值分年分位数动态校准(方案B, 主人9/19裁决)
        # 分位数表=data/wave_shrink_by_year_20260919.json: 熊/结构年P75=0.80-0.90 / 牛/普跌博弈年P75=1.49-1.69
        # 年型映射(避免日历年依赖): LOW_VOL普跌型->1.56(2026型) / 熊底型->0.90 / 其他->0.95
        pre60 = closes[start_i+1:start_i+61] if start_i+61 <= len(closes) else closes[start_i+1:]
        if len(pre60) >= 40:
            pre_dd = (min(pre60)/max(pre60) - 1) * 100
            if pre_dd > -25:
                continue
            v250 = sum(vols[:250]) / 250 if len(vols) >= 250 else sum(vols) / len(vols)
            pre20v = sum(vols[start_i+1:start_i+21]) / 20 if start_i+21 <= len(vols) else v250
            pre_shrink = pre20v / v250 if v250 else 1
            shrink_cap = 1.56 if ms_state == 'LOW_VOL' else 0.95  # 方案B动态阈值(5年分年P75)
            if pre_shrink > shrink_cap:
                continue
        else:
            pre_dd, pre_shrink = 0, 1.0
        # 暴量启动标注(统计: 暴量启动多为小波段, 预期下调)
        start_vr = vols[start_i] / (sum(vols[start_i+1:start_i+21])/20 if start_i+21 <= len(vols) and sum(vols[start_i+1:start_i+21]) else 1)
        vol_flag = '⚠暴量启动(预期下调)' if start_vr > 3 else ''
        off_start = (c / closes[start_i] - 1) * 100 if closes[start_i] else 999
        if off_start > 17 or off_start < -3:  # 铁律: 距段起-3%~+17%(负超3%=启动失败回落票)
            continue
        days_since = start_i  # 倒序索引=距今天数
        if days_since > 40:  # 启动后第2-28日(放宽到40)
            continue
        # 三年维度: 三年分位<55% + 距三年低<80%
        yr3 = closes[:750] if len(closes) >= 750 else closes
        hi3, lo3 = max(yr3), min(yr3)
        pos3 = 100 * (c - lo3) / (hi3 - lo3) if hi3 > lo3 else 50
        off_lo3 = (c / lo3 - 1) * 100 if lo3 else 999
        if pos3 >= 55 or off_lo3 > 80:
            continue
        off = (c / ma20 - 1) * 100
        vs = sorted(vols[:250])
        vrank = 100 * sum(1 for v in vs if v <= vols[0]) / len(vs)
        # 排雷: S6高位天量/S5高位放量滞涨/量分位>=80
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
        # 量价配合度(涨日均量/跌日均量)
        up_v = dn_v = 0.0; up_n = dn_n = 0
        for i in range(20):
            ch = closes[i] - closes[i + 1]
            if ch > 0: up_v += vols[i]; up_n += 1
            elif ch < 0: dn_v += vols[i]; dn_n += 1
        vp = (up_v / up_n) / (dn_v / dn_n) if up_n and dn_n and dn_v else 1.0
        ret20 = (c / closes[20] - 1) * 100 if closes[20] else 0
        rows.append({'code': code, 'name': nm, 'ind': ind, 'px': c,
                     'pos250': round(pos250), 'off': round(off, 1), 'vp': round(vp, 2),
                     'ret20': round(ret20, 1), 'vrank': round(vrank),
                     'off_start': round(off_start, 1), 'days': days_since, 'pos3': round(pos3), 'vflag': vol_flag})
    db.close()
    rows.sort(key=BASE_SORT_KEY)
    return rows, {'qfq_max': qfq_max, 'c1': c1, 'c1_date': c1_date,
                  'members_n': len(members), 'ms_state': ms_state, 'missed': missed}


def main() -> int:
    try:
        rows, ctx = scan_candidates()
    except RuntimeError as e:
        if 'C1 主线为空' in str(e):
            # 上游行业量能状态健康但当日无主线=有效空结果, 非故障(SOP168⑨定性):
            # 写最小md保持日报契约 + rc0, 避免健康检查把空主线日误计为任务失败
            md = os.path.join(ROOT, "reports",
                              f"mainline_startup_scan_{datetime.now().strftime('%Y%m%d')}.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write(f"# 主线赢家波段扫描（{datetime.now().strftime('%Y-%m-%d %H:%M')}·只读选股呈报件·赢家画像v3）")
                f.write("\n\n")
                f.write("> C1 量能主线为空（上游行业量能状态健康，当日无主线行业）——非故障空结果。\n")
            print("[MLSCAN-EMPTY] C1 主线为空(上游健康, 已写最小md, rc=0)")
            return 0
        print(f"[MLSCAN-FAIL] {e}")
        return 2
    pt, pt_err = load_portrait_pct()
    from portrait_feed import winner5_tags_view
    w5, w5_err = winner5_tags_view()
    top = rows[:12]

    L = [f"# 主线赢家波段扫描（{datetime.now().strftime('%Y-%m-%d %H:%M')}·只读选股呈报件·赢家画像v3）",
         "",
         f"> C1 量能主线: {'、'.join(ctx['c1'][:6])}（数据日 {ctx['c1_date']}）｜主线成分 {ctx['members_n']} 只｜"
         f"过滤: 深跌后主升启动(放量大阳)＋**距段起<17%铁律**＋启动2-40日内＋三年分位<55%＋距三年低<80%＋多头＋排雷｜命中 {len(rows)} 只",
         f"> 画像来源: 8只盈利票波段解剖(百亚三波衰竭/双底确认/主升段起点+17%内买入=六赢家无一例外)",
         f"> 市场状态: **{ctx['ms_state']}**" + ("——🔴第零问不通过('能涨'系统性不可得), 本清单仅供研究备战, 买入须主人明示" if ctx['ms_state'] == 'LOW_VOL' else ""),
         "> 同源族提示(20260921补测n=4954): pos250/pos3(三年分位)/距三年低 三位置指标链式同源(ρ=0.767/0.782)——多指标同时低位≈同一族信息, 勿读作多重独立确认",
         (f"> 画像分位: portrait_feed快照 {pt['date']}·呈现级零拦截(PD-026④), 不参与排序不过滤; "
          f"软权重影子=PREREG_PORTRAIT_SOFTWEIGHT_SHADOW_20261009 积累中"
          + (f"；五维标签 winner5 asof {w5['asof']}(仅标注)" if w5 else f"；五维标签不可用({w5_err})")
          if pt else
          f"> 画像分位: 不可用({pt_err})——按无画像呈报(fail-soft不阻断呈现)"),
         "",
         "| 代码 | 名称 | 行业 | 价(qfq) | 段起距% | 启动第N日 | 量价配合 | pos250 | 三年分位 | 量分位 | 画像分位 | 标注 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in top:
        os_ = t['off_start']
        opt_tag = '⭐最优介入' if 5 <= os_ <= 10 else ('偏早(确认不足)' if os_ < 5 else '偏晚(鱼尾)')
        ptv = pt['pct'].get(t['code']) if pt else None
        w5tag = w5['tags'].get(t['code'], '') if w5 else ''
        pt_cell = (f"{ptv:.0f}" if ptv is not None else "—") + (f"｜{w5tag}" if w5tag else "")
        L.append(f"| {t['code']} | {t['name']} | {t['ind']} | {t['px']:.2f} | **{t['off_start']:+.1f}%**{opt_tag} | 第{t['days']}日 "
                 f"| {t['vp']}× | {t['pos250']}% | {t['pos3']}% | {t['vrank']:.0f} | {pt_cell} | {t.get('vflag','')} |")
    L += ["",
          "## 使用语义",
          "1. 本清单=第零问+主线漏斗+低位启动形态的机械出口, 呈报仍须过 preflight 四查与主人三问裁决;",
          "2. 情绪主线(每日复盘记录)与 C1 量能主线不一致时, 以并集人工核对后再定;",
          "3. 零交易权; 任何买入走 executor。",
          "4. 画像分位列=呈现级标注零拦截(不排序不过滤); 画像升实权须过 prereg 影子三门+主人批准。"]
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print(f"[MLSCAN-OK] {OUT_MD} | 主线成分={ctx['members_n']} 命中={len(rows)} Top10 | 画像快照={pt['date'] if pt else 'N/A'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
