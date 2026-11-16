#!/usr/bin/env python3
"""趋势判定标准化工具 v2.2 — MA5/10/20/60 + ADX regime + MACD零轴 + BIAS + 唐奇安突破

v2.2.2 提请落盘（2026-09-23，S16 前向落地）: 判定 BEARISH（走坏成立）时向
data/trend_request_ledger.jsonl 追加提请时间戳行（同日同码幂等; 判定逻辑零改动）,
用于测量「提请→实际卖出」延迟。判定层其余口径不变, 仍为只读判定件。

v2.2 升级（2026-09-07 晚，主人「趋势判断有误，自查自纠，修复优化」令）——三处判定缺陷修复:
  1. 连续2日内置判定: 旧版仅时点判定，"2日不收回"(8/12基线)靠人工跨数据源拼接
     (qfq前复权 vs 腾讯不复权，口径不统一)→现 judge_day 纯函数对最近两个已确认交易
     日分别判定，BEARISH 须连续2日同判；首日走坏输出 BEARISH_D1 待确认档
  2. 破位幅度过滤: BEARISH 增加 depth=(MA20-close)/MA20>=1%（外部共识有效破位幅度
     原则），贴线缠绕(<1%)不再误判走坏，降为 WEAK+贴线标注
  3. 防未来函数 date 比较健壮化: 接口 date 兼容 '2026-09-07'/'20260907' 两种格式
  自测: python trend_checker.py --selftest（judge_day 合成序列用例，不联网）

v2.0 升级（2026-08-14，主流量化趋势判断方法调研落地）:
  1. ADX(14) 趋势强度开关: ADX>25 趋势态 / ADX<20 震荡态（CTA标准）
  2. MACD(12,26,9) 零轴定方向: DIF>0 多头域（方向确认）
  3. BIAS(6) 乖离率: ±8% 超买超卖提示
  4. 唐奇安通道: 收盘>前20日最高 = 突破信号（海龟规则）

防未来函数: 判定基准=最新交易日的前一日（盘中数据未确认时不用当日K线）
输出兼容性: 保留 "收盘: x | MA5=x MA10=x MA20=x MA60=x" 格式（guarded_trade_executor 正则解析依赖）

v2.1 升级（2026-08-15，TA-Lib 替换手工指标计算）:
  1. calc_macd → talib.MACD() 替代手工 EMA 迭代
  2. calc_adx  → talib.ADX() + talib.PLUS_DI() + talib.MINUS_DI() 替代手工 Wilder平滑
  3. 消除手工实现潜在 bug（Wilder 平滑曾漏除 p 放大 14 倍）

用法:
    python trend_checker.py --code sh600711
    python trend_checker.py --codes sh600711,sz300170,sz300711

v2.2.1 数据源进化（2026-09-22，主人「现在就干/继续」令+9/20 自行进化授权）:
  get_kline 改本地 vipdoc 优先（零配额零Key），陈旧>10日历日或文件缺失才回落
  westock 原路径（限频退避保留）；判定逻辑零改动。治本 9/21-9/22 westock
  日配额型限频两日 41/42 只失败问题。对账与菜单详见
  reports/trendchecker_batch_source_evidence_20260922.md。
"""
import json, sys, os, struct, time, urllib.request
import datetime
import numpy as np
import talib

URL = 'https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp'

# ── S16 前向落地(2026-09-23): 走坏提请时间戳台账 ──
# 背景: 「提请→实际卖出」延迟无法测量(trade_records 无提请时间戳)。
# 对策: 判定走坏成立(BEARISH)时向 data/trend_request_ledger.jsonl 追加一行;
# BEARISH_D1 只观察不落提请(其语义=不构成卖出理由); 落盘失败仅告警不阻断晨链。
TREND_LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 'data', 'trend_request_ledger.jsonl')


def append_trend_request_ledger(code, name='', reason='',
                                ledger_path=TREND_LEDGER_PATH, request_date=None,
                                v2_confirm=None, v2_action=None,
                                v2_gap_pct=None, v2_ma20_slope5=None):
    """走坏提请落盘(同日同 code 幂等)。返回 True=新追加 / False=当日已存在跳过。

    行格式: {"request_date":"YYYYMMDD","code":"6位裸码","name":"",
             "action":"sell_request","reason":""}
    B1(2026-10-10): v2_action 非空时附写 v2_confirm/v2_action/v2_gap_pct/v2_ma20_slope5
    四字段(消费端=走坏执行链分流 A/B 组; 字段缺失=fail-open 按现行执行)。旧行不回填。
    读失败不阻断(宁重复勿丢失), 只影响幂等; 判定主流程零依赖本函数。
    """
    c6 = (code or '')[-6:]  # 兼容 sh600519/600519 两种入参, 台账统一 6 位裸码
    rd = request_date or datetime.date.today().strftime('%Y%m%d')
    try:
        if os.path.exists(ledger_path):
            with open(ledger_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get('request_date') == rd and d.get('code') == c6:
                        return False
    except OSError:
        pass  # 读失败不阻断: 继续追加, 宁可重复不可丢失提请时间戳
    rec = {'request_date': rd, 'code': c6, 'name': name or '',
           'action': 'sell_request', 'reason': reason or ''}
    if v2_action is not None:
        rec.update({'v2_confirm': v2_confirm, 'v2_action': v2_action,
                    'v2_gap_pct': v2_gap_pct, 'v2_ma20_slope5': v2_ma20_slope5})
    d_dir = os.path.dirname(ledger_path)
    if d_dir and not os.path.isdir(d_dir):
        os.makedirs(d_dir, exist_ok=True)
    with open(ledger_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    return True


def _load_token() -> str:
    """从 config.json 读 westock Bearer token(2026-09-10 修复: 原硬编码 token 违反
    宪法§7 密钥条款, 且一经轮换/失效即全量"无K线数据"——9/9 事故的现成复演路径)。
    读取链与 stop_loss_executor._fetch_positions_via_mcp 完全同构:
    config.json → 环境变量 WESTOCK_TOKEN → 空(调用方 fail-loud)。"""
    token = ""
    try:
        cfg = json.load(open(r"C:\Users\yujie\.zcode\cli\config.json", encoding="utf-8"))
        ws = cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
        token = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
    except OSError:
        pass
    if not token:
        token = os.environ.get("WESTOCK_TOKEN", "")
    return token


def call_mcp(name, args, mid=1):
    token = _load_token()
    if not token:
        raise RuntimeError("westock token 缺失(config.json/westock-mcp/headers.Authorization "
                           "或 WESTOCK_TOKEN 均无)——拒绝静默退化")
    payload = {'jsonrpc': '2.0', 'id': mid, 'method': 'tools/call',
               'params': {'name': name, 'arguments': args}}
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers={
        'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read().decode())

def get_kline(code, limit=90, _retries=3):
    # westock data_kline 合同(2026-09-09 复核): code 需带 sh/sz 前缀, 参数名 code/period/limit;
    # 旧合同(codes 裸代码 + ktype)已失效且静默返回空 nodes(fail-silent), 2026-09-07 后生效
    # 2026-09-22 限频退避: 接口级限频实测(9/21-9/22 连续两日 41/42 只失败=42连发触发长窗口限频,
    # 100s 不恢复疑按小时/日配额), 加调用间隔+限频识别退避; 若为日配额型退避仍失败则 fail-loud,
    # 治本需换批量/本地源(呈报裁决)
    c = code if code[:2] in ('sh', 'sz', 'bj') else (
        'sh' + code if code.startswith(('6', '5', '9')) else 'sz' + code)
    # ── 2026-09-22 方案甲(主人授权系统进化): 本地vipdoc优先, westock降为断供兜底 ──
    # 依据: ①零配额零Key(9/21-9/22 westock日配额型限频两日41/42失败的治本)
    #       ②判定层零改动: 盘中防未来函数本就以T-1为基准(check_trend L180+),
    #         本地日线在全部时段满足判定需求; 15:00-17:30(vipdoc傍晚更新前)最新bar
    #         为T-1属可接受降级 ③对账: 9/22 双样本 tdx日收=westock实时(002379 17.10/601226 6.75)
    try:
        local = _vipdoc_kline(c, limit)
        if local:
            age = (datetime.date.today()
                   - datetime.datetime.strptime(local[-1]['date'], '%Y%m%d').date()).days
            if age <= _LOCAL_STALE_DAYS:
                return local
            print(f'  [local-stale] vipdoc 末bar {local[-1]["date"]} 超{_LOCAL_STALE_DAYS}日 -> 回落 westock')
    except (OSError, struct.error) as _e:
        print(f'  [local-miss] vipdoc 不可用({_e}) -> 回落 westock')
    args = {'code': c, 'period': 'day', 'limit': limit}
    last_err = ''
    for attempt in range(_retries + 1):
        time.sleep(0.8)  # 调用间隔, 降低42只连发速率
        r = call_mcp('data_kline', args)
        text = r.get('result', {}).get('content', [{}])[0].get('text', '')
        if '限频' in text or '限流' in text:
            last_err = text[:80]
            if attempt < _retries:
                time.sleep(60)  # 限频退避
            continue
        if not text or not text.lstrip().startswith(('{', '[')):
            # 2026-09-10 晚加固: 空体/非JSON 此前触发裸 JSONDecodeError 掀掉整轮体检(44只跑到第9只崩)
            raise RuntimeError(f'westock data_kline 返回非JSON(空体或错误页, {len(text)} 字符): '
                               f'{text[:80]!r} —— 疑似断供/限流/合同漂移')
        data = json.loads(text)
        nodes = data.get('data', {}).get('nodes', [])
        return nodes[::-1]  # 升序
    # 限频穷尽 → fallback 本地 vipdoc(2026-09-22): 同为不复权日线口径, judge 层防未来函数逻辑不变
    print('  [fallback] westock data_kline 限频 -> 本地 vipdoc 日线')
    try:
        return _vipdoc_kline(c, limit)
    except Exception as e:
        raise RuntimeError(f'westock data_kline 限频退避{_retries}次仍失败({last_err[:60]!r})'
                           f'且 vipdoc fallback 失败: {e}') from e

_VIPDOC_LDAY = 'D:/anzhuang/tdx/vipdoc'
_LOCAL_STALE_DAYS = 10  # 本地源陈旧上限(日历日,覆盖国庆+中秋连休极值); 超限视为vipdoc断供回落westock

def _vipdoc_kline(code_prefixed, limit=90):
    # 通达信本地日线(不复权,零限频零Key); 32B/条 <IIIIIfII> 价格×100,
    # 2026-09-22 与 tdx-local MCP 逐字段核对一致(sh603230 三日 amt/vol 全对上)
    mkt, code6 = code_prefixed[:2], code_prefixed[2:]
    path = os.path.join(_VIPDOC_LDAY, mkt, 'lday', f'{mkt}{code6}.day')
    raw = open(path, 'rb').read()
    n = len(raw) // 32
    if n == 0:
        raise RuntimeError(f'vipdoc 日线空文件: {path}')
    out = []
    for i in range(max(0, n - limit), n):
        d, o, h, l, c, _amt, _vol, _r = struct.unpack_from('<IIIIIfII', raw, i * 32)
        out.append({'date': str(d), 'open': o / 100, 'high': h / 100,
                    'low': l / 100, 'last': c / 100})
    return out  # 升序

def ma(vals, k):
    return sum(vals[-k:]) / k if len(vals) >= k else None

def calc_macd(closes):
    """MACD(12,26,9) via TA-Lib，返回最新 DIF/DEA/柱"""
    arr = np.array(closes, dtype=float)
    if len(arr) < 35:
        return None, None, None
    dif, dea, hist = talib.MACD(arr, fastperiod=12, slowperiod=26, signalperiod=9)
    return float(dif[-1]), float(dea[-1]), float(hist[-1])

def calc_adx(highs, lows, closes, period=14):
    """ADX(14) via TA-Lib，返回最新 ADX/+DI/-DI"""
    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)
    if len(c) < period * 2 + 2:
        return None, None, None
    adx = talib.ADX(h, l, c, timeperiod=period)
    pdi = talib.PLUS_DI(h, l, c, timeperiod=period)
    mdi = talib.MINUS_DI(h, l, c, timeperiod=period)
    return float(adx[-1]), float(pdi[-1]), float(mdi[-1])

BEAR_DEPTH_PCT = 1.0  # v2.2 有效破位幅度阈值（外部共识幅度原则，过滤贴线噪声）

# 2026-10-10 B1(主人「继续」令批准 owner_decisions_20261012 B1): v2 边缘票确认滤网固化。
# 依据: tmp/trend_v2_replay_20261009.py 14.5万信号回放(reports/trend_v2_verification_20261009.md)
#   A组(确认达标): 距MA20 ≤-3% 或 MA20五日斜率 ≤-0.05 → 维持 T+1 开盘卖(真走坏, 判定有效)
#   B组(贴线边缘): 未达确认强度 → T+1收盘收回MA20则不卖; 未收回则 T+2 开盘卖
# 回放: B组卖飞>3%率 5.4%→约0.4%(滤94%尾部), 代价=87%未收回子集平均晚一天卖低0.5%。
# 诚实边界: A组反转票(v2拦不住, 10/9久远银海斜率-0.092/天地在线距-4.2%均A组)属
# 「判定为真卖早可接受」主人裁决范畴; S-5硬止损不受v2影响永远即时执行。
V2_CONFIRM_GAP = -0.03    # 收盘距MA20 比例阈值(c/m20-1 ≤ -3% = A组)
V2_CONFIRM_SLOPE5 = -0.05  # MA20五日斜率(m20_now-m20_5ago)阈值(≤-0.05 = A组)


def v2_confirm_grade(c, m20_now, m20_5ago):
    """v2确认强度分级(纯函数, 可单测; 口径与回放脚本逐位一致)。

    返回 (grade, detail):
      'A_CONFIRMED' — 距MA20≤-3% 或 MA20五日斜率≤-0.05, 维持T+1开盘卖;
      'B_EDGE'      — 贴线边缘, T+1收盘收回MA20则不卖, 未收回T+2开盘卖;
      None          — 数据不足(m20序列<26根), fail-open 不分级不阻断主判定。
    """
    try:
        cf = float(c)
        m_now = float(m20_now)
        m_5ago = float(m20_5ago)
    except (TypeError, ValueError):
        return None, None
    if m_now <= 0:
        return None, None
    gap = cf / m_now - 1.0
    slope5 = m_now - m_5ago
    confirmed = (gap <= V2_CONFIRM_GAP) or (slope5 <= V2_CONFIRM_SLOPE5)
    grade = 'A_CONFIRMED' if confirmed else 'B_EDGE'
    return grade, {'gap_pct': round(gap * 100, 2), 'ma20_slope5': round(slope5, 3),
                   'v2_action': ('sell_t1_open' if confirmed else 'hold_reclaim_check')}


def judge_day(seq, end_idx):
    """对升序收盘序列 seq 的 [0..end_idx] 窗口做趋势时点判定（纯函数，连续2日判定与 --selftest 复用）。

    走坏(bear) = 收盘<MA20 且 MA5<MA10<MA20 且 破位幅度 depth>=BEAR_DEPTH_PCT。
    数据不足 60 根（算不出 MA60）返回 None。
    """
    win = seq[:end_idx + 1]
    if len(win) < 60:
        return None
    c = win[-1]
    m5 = sum(win[-5:]) / 5
    m10 = sum(win[-10:]) / 10
    m20 = sum(win[-20:]) / 20
    m60 = sum(win[-60:]) / 60
    below20 = c < m20
    depth = (m20 - c) / m20 * 100 if below20 else 0.0
    return {'c': c, 'ma5': m5, 'ma10': m10, 'ma20': m20, 'ma60': m60,
            'below20': below20, 'arr_bear': m5 < m10 < m20, 'depth': depth,
            'bear': below20 and (m5 < m10 < m20) and depth >= BEAR_DEPTH_PCT}

def check_trend(code, name=''):
    """趋势判定（三层：regime强度→方向确认→结构信号）"""
    nodes = get_kline(code)
    if not nodes:
        return {'code': code, 'error': '无K线数据'}

    closes = [n['last'] for n in nodes]
    highs = [n['high'] for n in nodes]
    lows = [n['low'] for n in nodes]

    # 防未来函数: 盘中(15:00前)最新K线未收盘不可用→基准=前一日; 收盘后→基准=最新日
    # v2.2: date 归一化比较（接口返回可能为 '2026-09-07' 或 '20260907'）
    import datetime
    _t = datetime.date.today()
    today_strs = {_t.isoformat(), _t.strftime('%Y%m%d')}
    if datetime.datetime.now().strftime('%H:%M') < '15:00' and nodes[-1]['date'] in today_strs:
        pre_c, pre_h, pre_l = closes[:-1], highs[:-1], lows[:-1]
    else:
        pre_c, pre_h, pre_l = closes, highs, lows

    c = pre_c[-1]
    ma5 = ma(pre_c, 5); ma10 = ma(pre_c, 10); ma20 = ma(pre_c, 20); ma60 = ma(pre_c, 60)

    # ── 第1层: ADX 趋势强度开关 ──
    adx, pdi, mdi = calc_adx(pre_h, pre_l, pre_c)
    if adx is None:
        regime = 'INSUFFICIENT'
        regime_label = '数据不足(ADX需28+日)'
    elif adx > 25:
        regime = 'TREND'
        regime_label = f'趋势态(ADX={adx:.1f}>25)'
    elif adx < 20:
        regime = 'RANGE'
        regime_label = f'震荡态(ADX={adx:.1f}<20,趋势信号降权)'
    else:
        regime = 'TRANSITION'
        regime_label = f'过渡区(ADX={adx:.1f},20-25)'

    # ── 第2层: MACD 零轴方向 ──
    dif, dea, hist = calc_macd(pre_c)
    if dif > 0:
        macd_dir = 'BULL' if dif > dea else 'BULL_CALM'
        macd_label = f'多头域(DIF={dif:+.3f}零轴上方)'
        if dif < dea:
            macd_label += '但DIF<DEA(红柱转弱)'
    else:
        macd_dir = 'BEAR' if dif < dea else 'BEAR_CALM'
        macd_label = f'空头域(DIF={dif:+.3f}零轴下方)'

    # ── 第3层: BIAS + 唐奇安 ──
    ma6 = ma(pre_c, 6)
    bias6 = (c - ma6) / ma6 * 100 if ma6 else None
    prior_high20 = max(pre_h[-20:])
    donchian_break = c > prior_high20

    # 20/60日区间位置
    win20 = pre_c[-20:]
    pos20 = (c - min(win20)) / (max(win20) - min(win20)) * 100 if max(win20) > min(win20) else 50
    win60 = pre_c[-60:]
    pos60 = (c - min(win60)) / (max(win60) - min(win60)) * 100 if max(win60) > min(win60) else 50

    # ── v2.2 连续2日+幅度确认 ──
    d1 = judge_day(pre_c, len(pre_c) - 1)
    d2 = judge_day(pre_c, len(pre_c) - 2)
    bear_confirmed = bool(d1 and d2 and d1['bear'] and d2['bear'])
    bear_d1 = bool(d1 and d1['bear']) and not bear_confirmed
    line_hug = bool(d1 and d1['below20'] and d1['arr_bear'] and d1['depth'] < BEAR_DEPTH_PCT)

    # ── B1(2026-10-10): v2确认强度分级(仅对走坏判定附加; 数据不足fail-open不分级) ──
    v2_confirm, v2_detail = None, None
    if len(pre_c) >= 25:
        _m20_now = sum(pre_c[-20:]) / 20.0
        _m20_5ago = sum(pre_c[-25:-5]) / 20.0
        v2_confirm, v2_detail = v2_confirm_grade(c, _m20_now, _m20_5ago)

    # ── 主判定（MA排列，regime限定；v2.2 走坏须连续2日同判+破位幅度≥1%） ──
    above_all = all(x is not None and c > x for x in [ma5, ma10, ma20, ma60])
    below_ma20 = ma20 is not None and c < ma20

    if above_all:
        if regime == 'TREND':
            verdict, label = 'STRONG', '✅ 强多头（站上所有均线+趋势态，禁止用弱势/走坏判定）'
        else:
            verdict, label = 'STRONG', '✅ 强多头（站上所有均线，但震荡市需降权）'
    elif c > ma5 > ma10 > ma20:
        verdict, label = 'BULLISH', '🟢 多头排列'
    elif bear_confirmed:
        verdict, label = 'BEARISH', '🔴 空头排列（趋势走坏成立·连续2日+破位≥1%）'
    elif bear_d1:
        verdict, label = 'BEARISH_D1', '🟠 走坏第1日（连续2日未确认，只观察，不构成卖出理由）'
    elif below_ma20:
        label_w = '🟠 20日线下方（偏弱）'
        if line_hug:
            label_w += '·贴线跌破(幅度<1%防噪声)'
        verdict, label = 'WEAK', label_w
    else:
        verdict, label = 'NEUTRAL', '🟡 缠绕/中性'

    # 极端信号附加
    signals = []
    if below_ma20 and ma60 is not None and c > ma60:
        signals.append('MA60未破(中期趋势线仍站上,走坏语义降权)')
    if donchian_break:
        signals.append('唐奇安20日突破')
    if bias6 is not None and bias6 > 8:
        signals.append(f'BIAS6超买(+{bias6:.1f}%)')
    elif bias6 is not None and bias6 < -8:
        signals.append(f'BIAS6超卖({bias6:.1f}%)')
    if adx is not None and adx > 40:
        signals.append('ADX极强(>40)')

    return {
        'code': code, 'name': name,
        'close': c, 'ma5': round(ma5, 2) if ma5 else None,
        'ma10': round(ma10, 2) if ma10 else None,
        'ma20': round(ma20, 2) if ma20 else None,
        'ma60': round(ma60, 2) if ma60 else None,
        'pos20_pct': round(pos20, 1), 'pos60_pct': round(pos60, 1),
        'adx': round(adx, 1) if adx is not None else None,
        'pdi': round(pdi, 1) if pdi is not None else None,
        'mdi': round(mdi, 1) if mdi is not None else None,
        'regime': regime, 'regime_label': regime_label,
        'dif': round(dif, 3), 'dea': round(dea, 3), 'macd_dir': macd_dir, 'macd_label': macd_label,
        'bias6': round(bias6, 1) if bias6 is not None else None,
        'donchian_break': donchian_break,
        'signals': signals,
        'verdict': verdict, 'label': label,
        'bear_confirmed_2d': bear_confirmed,
        'bear_d1': bear_d1,
        'bear_depth_pct': round(d1['depth'], 2) if d1 else None,
        # B1(2026-10-10): v2确认强度——走坏判定(含D1)时附; 消费端(走坏执行链)按
        # v2_action 处理: sell_t1_open=维持T+1开盘卖 / hold_reclaim_check=T+1收盘
        # 收回MA20不卖未收回T+2开盘卖。非走坏verdict恒None。
        'v2_confirm': v2_confirm if (bear_confirmed or bear_d1) else None,
        'v2_action': (v2_detail or {}).get('v2_action') if (bear_confirmed or bear_d1) else None,
        'v2_gap_pct': (v2_detail or {}).get('gap_pct') if (bear_confirmed or bear_d1) else None,
        'v2_ma20_slope5': (v2_detail or {}).get('ma20_slope5') if (bear_confirmed or bear_d1) else None,
        'ytd_forbidden': 'YTD不可作为趋势判定依据（铁律）',
    }

def _selftest():
    """judge_day 合成序列自测（不联网）"""
    def lin(n, drift):
        return [10.0 + drift * i for i in range(n)]

    cases = []
    # C1 强多头: 90根线性上行 → 末日站上全部均线，非 bear
    seq = lin(90, 0.05)
    d1 = judge_day(seq, len(seq) - 1)
    cases.append(('strong_bull', bool(d1 and not d1['below20'] and not d1['arr_bear']), d1))

    # C2 连续2日深破: 末10日显式递跌(跌穿MA20且空头排列) → 两日均 bear → BEARISH 成立
    seq = lin(90, 0.05)
    seq[80:] = [14.0, 13.7, 13.4, 13.1, 12.8, 12.5, 12.2, 11.9, 11.6, 11.3]
    d1 = judge_day(seq, len(seq) - 1)
    d2 = judge_day(seq, len(seq) - 2)
    cases.append(('bear_confirmed_2d', bool(d1 and d2 and d1['bear'] and d2['bear']), (d1, d2)))

    # C3 走坏第1日: 前9日贴MA20上缘缓跌(前日破位幅度<1%未确认) + 末日深破 → d1 bear, d2 非 bear → BEARISH_D1
    seq = lin(90, 0.05)
    seq[80:89] = [13.95, 13.9, 13.85, 13.8, 13.78, 13.76, 13.74, 13.72, 13.7]
    seq[89] = 13.0
    d1 = judge_day(seq, len(seq) - 1)
    d2 = judge_day(seq, len(seq) - 2)
    cases.append(('bear_d1_only', bool(d1 and d1['bear'] and d2 is not None and not d2['bear']), (d1, d2)))

    # C4 贴线跌破: 末日收在 MA20 下方 0.5% → depth<1% 非 bear（防噪声）
    seq = lin(90, 0.05)
    seq[-1] = (sum(seq[-20:]) / 20) * 0.995
    d1 = judge_day(seq, len(seq) - 1)
    cases.append(('line_hug_not_bear', bool(d1 and d1['below20'] and d1['depth'] < BEAR_DEPTH_PCT and not d1['bear']), d1))

    # C5 数据不足: 50根 → None
    cases.append(('insufficient_returns_none', judge_day(lin(50, 0.05), 49) is None, None))

    all_ok = True
    for name, ok, detail in cases:
        print(f"  {'PASS' if ok else 'FAIL'} {name}")
        if not ok:
            all_ok = False
            print(f"    detail: {detail}")
    print(f"selftest {'ALL PASS' if all_ok else 'FAILED'}")
    return 0 if all_ok else 2

def main():
    import argparse
    p = argparse.ArgumentParser(description='趋势判定标准化工具 v2.2（ADX+MACD+BIAS+唐奇安+连续2日走坏确认）')
    p.add_argument('--code', help='单只代码')
    p.add_argument('--codes', help='多只代码(逗号分隔)')
    p.add_argument('--selftest', action='store_true', help='judge_day 合成序列自测(不联网)')
    a = p.parse_args()

    if a.selftest:
        return _selftest()

    codes = []
    if a.code:
        codes = [a.code]
    elif a.codes:
        codes = [c.strip() for c in a.codes.split(',')]

    if not codes:
        print('需指定 --code 或 --codes')
        return 1

    errors, results = [], []
    for c in codes:
        print(f"\n=== {c} ===")
        try:
            r = check_trend(c)
        except Exception as e:
            # 2026-09-10 晚加固: 取数异常纳入既有 fail-loud 记账，不再裸泄掀掉整轮
            r = {'error': f'{type(e).__name__}: {e}'}
        if 'error' in r:
            print(f"  ❌ {r['error']}")
            errors.append((c, r['error']))
            continue
        results.append(r)
        # S16 前向落地: 走坏成立(BEARISH)提请落台账; BEARISH_D1 只观察不落;
        # 落盘失败仅告警, 不阻断晨链判定输出
        if r.get('verdict') == 'BEARISH':
            try:
                _nm = r.get('name') or ''
                if not _nm:
                    try:
                        from stock_name_lookup import get_name as _gn
                        _nm = _gn(c[-6:]) or ''
                    except Exception:
                        _nm = ''
                _new = append_trend_request_ledger(c, _nm, r.get('label') or '',
                                                   ledger_path=TREND_LEDGER_PATH,
                                                   v2_confirm=r.get('v2_confirm'),
                                                   v2_action=r.get('v2_action'),
                                                   v2_gap_pct=r.get('v2_gap_pct'),
                                                   v2_ma20_slope5=r.get('v2_ma20_slope5'))
                print(('  [提请落盘] data/trend_request_ledger.jsonl' if _new
                       else '  [提请台账] 当日已存在, 幂等跳过'))
            except Exception as _e:
                print(f'  [警告] 提请台账写入失败(不阻断判定): {_e}')
        print(f"  收盘: {r.get('close')} | MA5={r.get('ma5')} MA10={r.get('ma10')} MA20={r.get('ma20')} MA60={r.get('ma60')}")
        print(f"  ADX: {r.get('regime_label')} (+DI={r.get('pdi')} -DI={r.get('mdi')})")
        print(f"  MACD: {r.get('macd_label')} | DEA={r.get('dea')} | BIAS6={r.get('bias6')}% | 唐奇安突破={'是' if r.get('donchian_break') else '否'}")
        print(f"  20日位置: {r.get('pos20_pct')}% | 60日位置: {r.get('pos60_pct')}%")
        if r.get('signals'):
            print(f"  信号: {'; '.join(r['signals'])}")
        print(f"  判定: {r.get('label')}")

    # fail-loud（2026-09-09 教训：westock data_kline 合同漂移+fail-silent，体检静默空转两天）
    n_err, n_tot = len(errors), len(codes)
    if n_err and (n_err >= 3 or n_err >= 0.5 * n_tot):
        msg = f'trend_checker 数据源疑似退化: {n_err}/{n_tot} 只无K线数据(如全部"无K线数据"=上游合同漂移/断供), 趋势走坏体检不可信'
        print(f'\n⚠️ {msg}')
        try:
            import subprocess
            subprocess.run([sys.executable, 'notify.py', 'send',
                            '--title', '🔴trend_checker数据源退化告警', '--content', msg,
                            '--level', 'error'], timeout=60, cwd=os.path.dirname(os.path.abspath(__file__)) or '.')
        except Exception as e:
            print(f'notify 发送失败: {e}')
        return 2
    return 0

if __name__ == '__main__':
    raise SystemExit(main())