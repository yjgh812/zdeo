# -*- coding: utf-8 -*-
"""屠龙系统·个股买卖区域精细化研判引擎 v1 (20261003)
主人提示词七步流程落地: 环境校验→六维扫描→四级买卖区域→假信号鉴别→触发点→仓位映射→报告
宪法融合(重要):
  1) 止损统一 -8% (S-5铁律优先于提示词5-7%)
  2) 突破确认区按9/30裁决降级: 禁突破日追买, 突破仅标记, 等回踩确认才触发买点
  3) 报告数字三态: 已验证(引用真实研究) / 待回测N/A / 设计值(提示词阈值,未经回测校准)
  4) 画像匹配分为9/19波段画像知识的近似实现(非原文模型)
数据: data/dragon_quant_qfq.db (裸6位code) | 板块: data/stock_chain_cache.json | 大盘: baostock上证
用法: python stock_zones_agent.py 003006 [更多代码...]
"""
import sys, json, sqlite3, math
from pathlib import Path

ROOT = Path(r'D:\zcodesj')
DB = Path(r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db')  # 2026-10-13 切wb唯一真源(zc副本停更10/09已废, SOP一百三十二)
CHAIN = ROOT / 'data' / 'stock_chain_cache.json'

# ---------------- 数据层 ----------------

def load_bars(code, days=250):
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    cur = conn.cursor()
    cur.execute("SELECT date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT ?", (code, days))
    rows = cur.fetchall()[::-1]
    conn.close()
    if not rows:
        raise SystemExit(f'❌ {code} qfq库无数据(注意裸6位代码)')
    return rows  # (date,open,high,low,close,vol,chg)

def load_index_sh(days=60):
    """上证环境; baostock失败返回None(报告标N/A)"""
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            bs.logout(); return None
        import datetime as _dt
        end = _dt.date.today().strftime('%Y-%m-%d')
        start = (_dt.date.today() - _dt.timedelta(days=110)).strftime('%Y-%m-%d')
        rs = bs.query_history_k_data_plus('sh.000001', 'date,close,pctChg', start_date=start, end_date=end, frequency='d', adjustflag='3')
        out = []
        while rs.error_code == '0' and rs.next():
            d, c, p = rs.get_row_data()
            out.append((d, float(c), float(p or 0)))
        bs.logout()
        return out
    except Exception:
        return None

def load_sector_tier(code):
    """板块主线tier快照(v1.5): data/sector_tier_latest.json <- fund.feature_industry_momentum最新日横截面
    用法基线: 主线=卖信号可信禁低吸追高; 退潮=卖信号失真少卖, 买形态相对好但须放量止跌前置"""
    try:
        d = json.loads((ROOT / 'data' / 'sector_tier_latest.json').read_text(encoding='utf-8'))
        ind = d.get('stock_ind', {}).get(code)
        if not ind:
            return None, None, d.get('date')
        for t in d.get('tiers', []):
            if t['industry'] == ind:
                cn = {'lead': '主线', 'mid': '中性', 'lag': '退潮'}[t['tier']]
                return f"{ind}({cn}, ret20 {t['ret20']:+.1f}%)", t['tier'], d.get('date')
        return ind, None, d.get('date')
    except Exception:
        return None, None, None

def load_sector(code):
    try:
        key = ('sz' if code[0] in '03' else 'sh') + code
        d = json.loads(CHAIN.read_text(encoding='utf-8')).get('stocks', {}).get(key, {})
        nodes = d.get('nodes') or []
        themes = d.get('themes') or []
        if nodes or themes:
            return '/'.join(nodes[:2]) + (f' [{"/".join(themes[:2])}]' if themes else '')
        return None
    except Exception:
        return None

# ---------------- 指标层 ----------------

def ma(vals, n, i):
    if i + 1 < n:
        return None
    return sum(vals[i-n+1:i+1]) / n

def classify_vol_ratio(vr):
    if vr is None: return 'N/A'
    if vr < 0.5: return '地量'
    if vr < 0.8: return '缩量'
    if vr <= 1.2: return '平量'
    if vr <= 3.0: return '放量'
    return '天量'

def tri(x, lo, peak, hi):
    """三角形隶属度0-1: 峰值在peak"""
    if x is None: return 0.0
    if lo <= x <= peak: return (x - lo) / (peak - lo) if peak > lo else 1.0
    if peak < x <= hi: return (hi - x) / (hi - peak) if hi > peak else 1.0
    return 0.0

def bottom_score(rows, i):
    """底部画像匹配分(9/19波段画像近似): 量比~0.65/距高~-36%/破MA20~-10%"""
    closes = [r[4] for r in rows]; vols = [r[5] or 0 for r in rows]
    hi60 = max(closes[max(0, i-59):i+1]); c = closes[i]
    ma20v = ma(closes, 20, i)
    drawdown = c / hi60 - 1 if hi60 else 0
    v20 = ma(vols, 20, i)
    vr = vols[i] / v20 if v20 else None
    dev = (c / ma20v - 1) if ma20v else 0
    s = (tri(vr, 0.35, 0.65, 0.95) + tri(drawdown, -0.55, -0.36, -0.18) + tri(dev, -0.20, -0.10, -0.03)) / 3 * 100
    return round(s)

def top_score(rows, i):
    """顶部画像匹配分(9/19近似): 量比~1.26/距低~+55%/乖离~+12%"""
    closes = [r[4] for r in rows]; vols = [r[5] or 0 for r in rows]
    lo60 = min(closes[max(0, i-59):i+1]); c = closes[i]
    ma20v = ma(closes, 20, i)
    rise = c / lo60 - 1 if lo60 else 0
    v20 = ma(vols, 20, i)
    vr = vols[i] / v20 if v20 else None
    dev = (c / ma20v - 1) if ma20v else 0
    s = (tri(vr, 0.9, 1.26, 1.8) + tri(rise, 0.3, 0.55, 0.9) + tri(dev, 0.05, 0.12, 0.22)) / 3 * 100
    return round(s)

def chip_profile(rows, i, buckets=20):
    """筹码近似: 60日成交量按收盘价分布 -> (峰价, 获利盘比例)"""
    seg = rows[max(0, i-59):i+1]
    lo = min(r[4] for r in seg); hi = max(r[4] for r in seg)
    if hi <= lo: return None, None
    w = [0.0]*buckets
    for r in seg:
        k = min(buckets-1, int((r[4]-lo)/(hi-lo)*buckets))
        w[k] += (r[5] or 0)
    tot = sum(w)
    if tot <= 0: return None, None
    peak_k = w.index(max(w))
    peak_px = lo + (peak_k + 0.5) / buckets * (hi - lo)
    cur_k = int((rows[i][4]-lo)/(hi-lo)*buckets)
    below = sum(w[:cur_k+1]) / tot
    return peak_px, below * 100

# ---------------- 区域引擎 ----------------

def analyze(code, name=''):
    rows = load_bars(code)
    i = len(rows) - 1
    dates = [r[0] for r in rows]
    opens = [r[1] for r in rows]; highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]; closes = [r[4] for r in rows]
    vols = [r[5] or 0 for r in rows]; chgs = [r[6] or 0 for r in rows]
    n = len(rows); c = closes[i]; d0 = dates[i]
    ma5, ma10, ma20, ma60 = (ma(closes, k, i) for k in (5, 10, 20, 60))
    ma20_arr = [ma(closes, 20, j) for j in range(n)]
    v20 = ma(vols, 20, i); vr = vols[i]/v20 if v20 else None
    # MACD(12,26,9)
    ema12 = closes[0]; ema26 = closes[0]; dea = 0.0; macd_hist = None
    for j, x in enumerate(closes):
        ema12 = ema12*11/13 + x*2/13
        ema26 = ema26*25/27 + x*2/27
        dif = ema12 - ema26
        dea = dea*8/10 + dif*2/10
        macd_hist = 2*(dif-dea)
    hi60, lo60 = max(closes[max(0,i-59):i+1]), min(closes[max(0,i-59):i+1])
    hi20 = max(highs[max(0,i-19):i+1]); lo20 = min(lows[max(0,i-19):i+1])
    pos60 = (c - lo60)/(hi60-lo60)*100 if hi60 > lo60 else 50
    pos120 = (c - min(closes[max(0,i-119):i+1]))/(max(closes[max(0,i-119):i+1])-min(closes[max(0,i-119):i+1]))*100 if len(closes) >= 60 else pos60
    dev20 = c/ma20 - 1 if ma20 else 0
    bscore, tscore = bottom_score(rows, i), top_score(rows, i)
    chip_peak, profit_pct = chip_profile(rows, i)
    # 趋势
    trend = ('多头' if ma5 and ma10 and ma20 and ma5 > ma10 > ma20 else
             '空头' if ma5 and ma10 and ma20 and ma5 < ma10 < ma20 else '震荡')
    above20 = ma20 and c > ma20
    # 近20日量价背离: 价创20日新高但量能峰值衰减
    div = False
    if i >= 20 and c >= hi20 * 0.995:
        peak_v_now = max(vols[i-5:i+1]); peak_v_prior = max(vols[i-20:i-5])
        div = peak_v_prior > 0 and peak_v_now < peak_v_prior * 0.7
    # 大盘环境
    idx = load_index_sh()
    mkt_regime, mkt_note = 'N/A', '指数数据不可用'
    if idx:
        ic = [x[1] for x in idx]
        ima20 = sum(ic[-20:])/20
        mkt_regime = '偏强' if ic[-1] > ima20 and ic[-1] > ic[-20] else ('偏弱' if ic[-1] < ima20 and ic[-1] < ic[-5] else '震荡')
        mkt_note = f'上证{ic[-1]:.0f} vs MA20 {ima20:.0f}, 20日{"上行" if ic[-1]>ic[-20] else "下行"}'
    sector = load_sector(code)
    sec_tier_desc, sec_tier, _ = load_sector_tier(code)
    mkt_coef = {'偏强': 1.0, '震荡': 0.8, '偏弱': 0.5}.get(mkt_regime, 0.8)
    # ---- 买入区域判定 ----
    shrink2 = all(vols[j] < 0.8*(ma(vols,20,j) or 1e9) for j in range(max(0,i-1), i+1))
    no_new_low = lows[i] >= lo20 * 0.995
    bull_bar = closes[i] > opens[i] and (highs[i]-max(opens[i],closes[i])) < (min(opens[i],closes[i])-lows[i])*1.5 and closes[i] > ma5 if ma5 else False
    zone1 = (bscore >= 55 and shrink2 and no_new_low and above20 is not None and not above20)  # 低吸区(分阈值按近似模型降为55,标注)
    breakout = c > hi20 * 0.995 and vr and vr >= 1.5
    zone2_marked = breakout  # 9/30融合: 突破仅标记
    pullback = (trend == '多头' and ma20 and lo20 >= ma20*0.97 and vr and vr < 0.8 and above20)
    zone3 = pullback
    chase = (dev20 >= 0.20 or (ma5 and c/ma5-1 >= 0.25)) or (tscore >= 50 and vr and vr > 1.2 and chgs[i] < 2) or div
    zone4 = chase
    # ---- 卖出区域判定 ----
    stall = vr and vr > 1.2 and chgs[i] < 2 or (highs[i]/c - 1 > 0.03 and vr and vr > 1.2)
    sell1_cnt = sum([tscore >= 45, bool(stall), div, chip_peak is not None and c > chip_peak*1.15])
    sell1 = sell1_cnt >= 2
    below_ma5_2d = all(closes[j] < (ma(closes,5,j) or 1e9) for j in range(i-1, i+1)) if i >= 1 else False
    sell2 = below_ma5_2d and macd_hist is not None and macd_hist < 0
    sell3 = (ma20 and c < ma20 and i >= 1 and closes[i-1] < (ma20_arr[i-1] or 1e9) and vr and vr > 1.2) or (ma60 and c < ma60 and trend == '空头')
    no_stop = (bscore >= 45 and vr and vr < 0.8 and not bull_bar and (ma5 and c < ma5) and trend != '多头')
    # 仓位映射
    base = {'zone1': 15, 'zone3': 8}.get('zone1' if zone1 else ('zone3' if zone3 else None), 0)
    pos_sug = 0
    action, in_zone = '观望/持有', '中性观察区'
    cur_state = []
    if zone1: cur_state.append('接近黄金低吸条件')
    if zone2_marked: cur_state.append('突破已标记(9/30裁决:禁追,等回踩确认)')
    if zone3: cur_state.append('回踩加仓条件')
    if zone4: cur_state.append('⚠️追高风险区')
    if sell1: cur_state.append('⚠️止盈兑现条件≥2项'); action, in_zone = '考虑减仓1/2-2/3', '止盈兑现区'
    if sell2: cur_state.append('⚠️短期趋势走坏'); action, in_zone = '减仓至1/3', '趋势走坏减仓区'
    if sell3: cur_state.append('🔴破位清仓条件'); action, in_zone = '无条件清仓(走pre_sell流程)', '破位清仓区'
    if no_stop and not (sell1 or sell2 or sell3): cur_state.append('⚠️疑似下跌中继'); action, in_zone = '禁止抄底', '下跌中继观望区'
    if cur_state and in_zone == '中性观察区' and zone4: in_zone = '追高风险区'
    stop_line = c * 0.92  # S-5 -8%
    name = name or code
    L = []
    L.append('━'*47)
    L.append(f'📌 屠龙系统·个股买卖区域研判 v1 | {code} {name}  (数据至{d0})')
    L.append('━'*47)
    L.append('【当前状态全景】')
    L.append(f'• 大级别位置: 60日位置{pos60:.0f}% / 120日{pos120:.0f}% | 底部画像匹配分:{bscore} 顶部:{tscore} (9/19画像近似)')
    L.append(f'• 趋势: {trend} (MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} MA60={ma60:.2f}) | 收盘{c:.2f} 乖离MA20 {dev20*100:+.1f}%')
    L.append(f'• 量能: {classify_vol_ratio(vr)} (量比{vr:.2f}) | 量价背离检测: {"⚠️顶背离" if div else "未见"}')
    L.append(f'• 筹码(近似): 60日筹码峰≈{chip_peak:.2f} 获利盘≈{profit_pct:.0f}% | MACD柱: {"红" if macd_hist and macd_hist>0 else "绿"}')
    L.append(f'• 关键支撑: MA20={ma20:.2f} / 60日低={lo60:.2f} / 筹码峰={chip_peak:.2f}')
    L.append(f'• 关键压力: 20日高={hi20:.2f} / 60日高={hi60:.2f}')
    tier_tip = {'lead': '[v4基线:主线=卖信号可信禁低吸追高]', 'lag': '[v4基线:退潮=卖信号失真少卖,买形态相对好须放量止跌前置]', 'mid': ''}.get(sec_tier, '')
    L.append(f'• 板块: {sector or "N/A"} | 行业主线: {sec_tier_desc or "N/A(快照universe待扩展)"} {tier_tip}')
    L.append(f'• 大盘: {mkt_regime}({mkt_note}) 环境系数x{mkt_coef} [v2基线: 强市禁低吸/弱市少卖——走坏破位中继信号弱市全失真]')
    L.append('【买入区域判定】')
    L.append(f'1.黄金低吸区: {"接近命中" if zone1 else "未命中"} (条件: 底部分≥55+连续缩量+不创新低+MA20下方) [基线: med-1.85%全环境负; ⚠️强市最毒-5.63%win36.6, 弱市相对最不坏-0.86%——与「中性以上才低吸」直觉相反]')
    L.append(f'2.突破确认区: {"🔴已标记→按9/30裁决禁追,等回踩确认" if zone2_marked else "未触发"} (量≥1.5x且创20日高) [基线: 回踩确认med-2.31%; 突破日追买-1.07%已证伪(9/30)]')
    L.append(f'3.回踩加仓区: {"命中(仅持仓加仓)" if zone3 else "未命中"} (多头+缩量回踩+MA20上方) [基线: med-1.63%全环境负, 仅作已有持仓的观察标记]')
    L.append(f'4.追高风险区: {"⚠️命中" if zone4 else "未命中"} (乖离≥20%或顶部信号或背离) [基线: med-9.00%win26%三环境全稳健——全体系最强禁令]')
    L.append('【卖出区域判定】')
    L.append(f'1.止盈兑现区: {"⚠️命中(≥2项)" if sell1 else "未命中"} (顶部分/放量滞涨/背离/筹码乖离 计{sell1_cnt}项) [基线: T+20 med-1.40%; v4板块: 主线-2.23%正确性2倍于退潮-0.97%——主线该卖] [操作:减仓1/2-2/3·待主人裁决]')
    L.append(f'2.趋势走坏区: {"⚠️命中" if sell2 else "未命中"} (2日收于MA5下+MACD绿) [基线: 强市-2.01%正确/弱市+0.40%失真; v4板块: 主线-1.28%正确/退潮-0.23%无信息——主线该卖退潮勿动]')
    L.append(f'3.破位清仓区: {"🔴命中" if sell3 else "未命中"} (放量2日破MA20/结构破坏) [⚠️环境分化: 强市-1.01%正确/弱市+1.96%衰竭陷阱; v4板块: 主线+0.29%尚可/退潮+1.36%=最大卖飞陷阱——退潮破位勿恐慌; 离场以S-5成本口径-8%为主,走pre_sell流程]')
    L.append(f'4.下跌中继: {"⚠️疑似" if no_stop else "未命中"} (底部分中上但无止跌信号) [基线: 强市-1.97%禁抄底正确/弱市+0.62%可放宽; v4板块: 主线-0.87%禁抄底正确/退潮+0.18%可试探——退潮+放量止跌才试]')
    L.append('【假信号预警】')
    warn = []
    if zone2_marked: warn.append('假突破: 需次日不回落+板块同步, 单日脉冲脉冲放量则失效(回踩确认前置)')
    if no_stop: warn.append('下跌中继: 缩量但无止跌结构, 反弹受MA5压制')
    if sell1: warn.append('空中加油鉴别: 若缩量回调+支撑不破+主线仍在则非见顶, 减仓后留底仓')
    L.append('• ' + ('; '.join(warn) if warn else '当前无高优先级假信号风险'))
    L.append('【今日操作建议】')
    L.append(f'• 现价{c:.2f} 处于【{in_zone}】→ {action}')
    if cur_state: L.append('• 状态明细: ' + '; '.join(cur_state))
    L.append(f'• 风控铁律: S-5止损线 {stop_line:.2f} (-8%铁律, 优先于任何区域判定)')
    L.append('【数字三态声明】')
    L.append('• 八区基线(v1.1全样本): Z1低吸med-1.85%/Z2回踩确认-2.31%/Z3回踩加仓-1.63%/Z4追高-9.00%win26(禁令最强)/S1止盈-1.40/S2走坏-0.48/S3破位+0.94卖飞警示/S4中继-0.12')
    L.append('• v1.2环境矩阵(walk-forward): Z1强市-5.63%最毒弱市-0.86%/S3强市-1.01%正确弱市+1.96%衰竭陷阱/S2弱市+0.40%失真/S4弱市+0.62%可放宽——「行情自适应」已实证: 弱市少卖(走坏/破位信号失真), 强市禁低吸')
    L.append('• 结论: 裸形态区域无一达到提示词目标胜率, alpha不在形态在配对(入场×出场×择时×筛选); 买区定位=候选标记层非独立信号')
    L.append('• v1.3板块矩阵(tmp/zones_backtest_v3): 四买区主线-退潮中位差全负(Z1 -2.00/Z2 -1.54/Z3 -1.26pp)——铁律6「主线买入胜率高15-20pp」证伪且方向相反; Z4追高三档全-9%无差异')
    L.append('• v1.4卖区板块矩阵(tmp/zones_backtest_v4,n=426万): 四卖区主线-退潮差全负(-1.05~-1.26pp)=主线卖出更正确(S1主线-2.23%正确性2倍)/退潮卖出易卖飞(S3退潮+1.36%最大陷阱)——与买区反向构成对称: 主线=定价延续区(买形态无边际/卖形态有效), 退潮=折价修复区(买形态相对好/卖信号失真)')
    L.append('• 合成操作论: 弱市/退潮→少卖可试低位(放量止跌前置); 强市/主线→卖信号可信禁低吸追高')
    L.append('• 待回测v5: 逐年细化/阈值校准')
    L.append('• 零交易权: 本报告为研判参考, 买入须过pre_trade_check_v2, 卖出须主人裁决')
    L.append('━'*47)
    return '\n'.join(L)

if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    from stock_name_lookup import get_name
    for code in sys.argv[1:]:
        print(analyze(code, name=get_name(code) or ''))
        print()
