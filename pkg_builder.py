# -*- coding: utf-8 -*-
"""
pkg_builder.py — 标准数据包生成器 v2.0
角色: 屠龙反臆想体系 三段式流水线 步骤1【数据读取Agent】= m1数据管家(只读权限)
职责: 从本地库/API拉原始值, 输出标准数据包JSON(含指纹/来源/时间戳/逐字段来源)
纪律: 本文件禁止任何预测/评分/强弱好坏定性逻辑; 允许的仅限四类机械操作:
      1.原始值直取  2.字段名映射(别名)  3.公开交易规则查表(涨停幅度->涨停价换算)
      4.纯机械布尔比较(price>=daily_limit)与列表计数(len)

v2.0 变更 (t23, 响应m6在t20验收的"封单/资金类论据可核面不足"):
  资金类: turnover_rate 换手率(westock原值; qfq表无此列, westock失败记null)
          amount 成交额(westock优先, qfq兜底)
  盘口类: buy_sell_ratio 委比(=westock wb_ratio, 仅字段名映射)
          inner_volume 内盘 / outer_volume 外盘(westock原值)
  涨停类: daily_limit 涨停价(westock price_ceiling原值优先; API缺失时按
          板块静态规则 prev_close*(1+幅度) 换算, round2, 属格式转换非分析)
          is_limit_up 是否触涨停(round(price,2)>=round(daily_limit,2) 纯机械布尔)
  概念类: concepts_count(concept_map该code条目数, 纯计数)
  可核性: 数据包新增 field_sources 逐字段来源标注
          ('westock_quote'/'qfq'/'concept_map'/'calc:limit_rule'/'calc:mechanical'/'calc:count')

数据源:
  1) qfq库       D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db 表 stock_daily_meta_qfq
                 (code纯6位, 取最新一日; 列: open/high/low/close/volume/amount/change_pct)
  2) concept_map D:/workbuddyspace/databases/fundamental_data.db          表 concept_map (name/concept静态映射)
  3) westock quote MCP data_quote 实时快照 (代码需sh/sz/bj前缀)

涨停幅度静态规则(交易所公开规则查表, 零判断):
  主板(60/00等)10% | 创业板(300/301/302)20% | 科创板(688/689)20%
  北交所(8/4/92开头)30% | name以ST类前缀开头5%(name可得时)

指标路由(零判断, 仅按可得性):
  - name/concept      -> concept_map优先, westock兜底(name)
  - 其余快照类指标    -> westock优先(当日实时), qfq兜底(库内最新日)
  - 派生字段          -> daily_limit/is_limit_up/concepts_count 按上述v2.0规则
  - sources参数可限定可用源集合

CLI:
  python pkg_builder.py --codes 600272 --indicators close,volume_ratio,change_pct,turnover_rate,amount,buy_sell_ratio,inner_volume,outer_volume,daily_limit,is_limit_up,concepts_count,name,concept
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime

# ---------------- 路径与常量 ----------------
QFQ_DB = 'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FUND_DB = 'D:/workbuddyspace/databases/fundamental_data.db'
PKG_DIR = 'D:/zcodesj/.gate_logs/packages'
sys.path.insert(0, 'D:/workbuddyspace')  # westock_client 所在地

QFQ_TABLE = 'stock_daily_meta_qfq'
QFQ_COLS = {'open', 'high', 'low', 'close', 'volume', 'amount', 'change_pct'}
CONCEPT_COLS = {'name', 'concept'}
# westock快照字段(与请求指标同名直取; 别名仅做字段名映射, 不做数值换算)
WESTOCK_ALIAS = {
    'close': 'price',
    'change_pct': 'change_percent',
    'buy_sell_ratio': 'wb_ratio',    # 委比: 仅名称映射, 值原样
    'daily_limit': 'price_ceiling',  # 涨停价: API原值优先, 缺失才按规则换算
}
WESTOCK_QUOTE_FIELDS = {
    'price', 'prev_close', 'open', 'high', 'low', 'volume', 'amount',
    'change', 'change_percent', 'turnover_rate', 'volume_ratio', 'range_pct',
    'avg_price', 'time', 'wb_ratio', 'outer_volume', 'inner_volume',
    'pe_ratio', 'pe_fwd', 'pe_lyr', 'pb_ratio', 'dividend_ratio_ttm',
    'total_market_cap', 'circulating_market_cap', 'total_shares', 'float_shares',
    'high_52week', 'low_52week', 'chg_5d', 'chg_10d', 'chg_20d', 'chg_60d',
    'chg_ytd', 'price_ceiling', 'price_floor', 'name',
}
# v2.0派生字段(依赖行内其它值/静态规则, 统一在直取字段之后解析)
DERIVED_FIELDS = {'daily_limit', 'is_limit_up', 'concepts_count'}
ALL_SOURCES = ['qfq', 'concept_map', 'westock_quote']

# ---------------- 只读连接(物理只读) ----------------
def _ro_conn(path):
    """以 mode=ro 只读模式打开sqlite, 体现数据读取Agent只读权限"""
    return sqlite3.connect('file:' + path.replace('\\', '/') + '?mode=ro', uri=True)


def _market_prefix(code):
    """纯6位代码 -> westock所需带市场前缀代码(仅格式变换, 非数据加工)"""
    if code.startswith(('6', '9')):
        return 'sh' + code
    if code.startswith(('4', '8')):
        return 'bj' + code
    return 'sz' + code


def _limit_up_pct(code, name=None):
    """按交易所公开静态规则查表涨停幅度(小数). 纯查表, 零判断零定性.
    主板10% / 创业板300,301,302 20% / 科创板688,689 20% / 北交所8,4,92 30% / ST类5%
    """
    if name and str(name).upper().startswith(('ST', '*ST', 'S*ST', 'SST')):
        return 0.05
    if code.startswith(('300', '301', '302', '688', '689')):
        return 0.20
    if code.startswith(('8', '4', '92')):
        return 0.30
    return 0.10


# ---------------- 各源取数(全部原样返回) ----------------
def _fetch_qfq_latest(code, ind):
    """qfq库该code最新一日的指标原值. 返回(值, 日期)"""
    conn = _ro_conn(QFQ_DB)
    try:
        row = conn.execute(
            'SELECT date, %s FROM %s WHERE code=? ORDER BY date DESC LIMIT 1' % (ind, QFQ_TABLE),
            (code,)).fetchone()
        if not row:
            return None, None
        return row[1], row[0]
    finally:
        conn.close()


def _fetch_concept(code, ind):
    """concept_map: name=股票名原值; concept=全部概念列表(仅排序, 无加工)"""
    conn = _ro_conn(FUND_DB)
    try:
        if ind == 'name':
            row = conn.execute(
                "SELECT name FROM concept_map WHERE code=? AND name!='' LIMIT 1",
                (code,)).fetchone()
            return row[0] if row else None
        rows = conn.execute(
            'SELECT DISTINCT concept FROM concept_map WHERE code=? ORDER BY concept',
            (code,)).fetchall()
        return [r[0] for r in rows] if rows else None
    finally:
        conn.close()


_WSC = None


def _westock_snapshot(code):
    """westock data_quote 实时快照原值(dict). 失败返回None"""
    global _WSC
    try:
        if _WSC is None:
            from westock_client import WeStockClient
            _WSC = WeStockClient()
        r = _WSC.quote(_market_prefix(code))
        data = r.get('data') or {}
        for v in data.values():
            return v
        return None
    except Exception as e:
        print('[warn] westock quote %s 失败: %s' % (code, str(e)[:120]), file=sys.stderr)
        return None


def _span(old, new):
    """合并时间范围(仅取min/max, 供sources元数据用)"""
    if old is None:
        return new
    a = min(old[0], new[0]) or new[0]
    b = max(old[1], new[1]) or new[1]
    return (a, b)


# ---------------- 核心函数 ----------------
def build_package(codes, indicators, sources=None):
    """生成标准数据包. 只读: 拉原始值/静态规则换算->装包->打指纹, 零定性.

    codes:      纯6位代码列表 ['600272', '002354']
    indicators: 指标列表, v2.0新增 turnover_rate/amount/buy_sell_ratio/inner_volume/
                outer_volume/daily_limit/is_limit_up/concepts_count
    sources:    可用源列表, 默认 ['qfq','concept_map','westock_quote']
    return:     标准数据包dict(含field_sources逐字段来源)
    """
    if sources is None:
        sources = list(ALL_SOURCES)
    now = datetime.now()
    pkg = {
        'package_id': 'pkg_' + now.strftime('%Y%m%d_%H%M%S'),
        'builder_version': '2.0',
        'fingerprint': None,  # 装完数据后回填
        'created_at': now.isoformat(timespec='seconds'),
        'sources': [],
        'raw_data': {},
        'field_sources': {},   # v2.0: 每code每字段的取数来源, 供m6逐项核对
        'notes': '纯原始值零加工; calc:*仅限静态规则换算/机械布尔/计数',
    }
    used = {'qfq': set(), 'concept_map': set(), 'westock_quote': set(), 'calc': set()}
    rng = {'qfq': None, 'concept_map': ('static', 'static'), 'westock_quote': None}

    for code in codes:
        code = str(code).strip()
        quote = _westock_snapshot(code) if 'westock_quote' in sources else None
        if quote and quote.get('time'):
            t = quote['time']
            rng['westock_quote'] = _span(rng['westock_quote'], (t, t))
        row = {}
        fsrc = {}
        # 静态规则换算daily_limit时需要的name(ST幅度判断; quote原值优先, concept_map兜底)
        need_rule_name = bool({'daily_limit', 'is_limit_up'} & set(indicators))
        rule_name = quote.get('name') if (need_rule_name and quote) else None
        if rule_name is None and need_rule_name and 'concept_map' in sources:
            rule_name = _fetch_concept(code, 'name')

        # ---- 第一遍: 直取型指标(concept_map -> westock -> qfq, 与v1.0一致) ----
        for ind in indicators:
            ind = str(ind).strip()
            if ind in DERIVED_FIELDS:
                continue
            val = None
            src = None
            # 1) concept_map(本地静态映射优先)
            if ind in CONCEPT_COLS and 'concept_map' in sources:
                val = _fetch_concept(code, ind)
                if val is not None:
                    used['concept_map'].add(ind)
                    src = 'concept_map'
            # 2) westock快照(当日实时, 同日一致性好)
            if val is None and 'westock_quote' in sources and quote:
                fld = WESTOCK_ALIAS.get(ind, ind)
                if fld in WESTOCK_QUOTE_FIELDS:
                    v = quote.get(fld)
                    if v is not None:
                        val = v
                        used['westock_quote'].add(ind)
                        src = 'westock_quote'
            # 3) qfq库兜底(库内最新交易日原值)
            if val is None and 'qfq' in sources and ind in QFQ_COLS:
                v, d = _fetch_qfq_latest(code, ind)
                if v is not None:
                    val = v
                    used['qfq'].add(ind)
                    rng['qfq'] = _span(rng['qfq'], (d, d))
                    src = 'qfq'
            row[ind] = val  # 三源均无 -> null(如实标注缺失, 不臆造)
            if src:
                fsrc[ind] = src

        # ---- 第二遍: 派生字段(v2.0; 全部机械操作) ----
        for ind in indicators:
            ind = str(ind).strip()
            if ind not in DERIVED_FIELDS:
                continue
            val = None
            src = None
            if ind == 'daily_limit':
                # a) API原值 price_ceiling(第一遍alias机制已尝试, 此处兜底再查一次)
                if quote and quote.get('price_ceiling') is not None:
                    val = quote['price_ceiling']
                    src = 'westock_quote'
                    used['westock_quote'].add('daily_limit')
                else:
                    # b) 静态规则换算: prev_close*(1+板块幅度), round2(格式转换非分析)
                    prev = quote.get('prev_close') if quote else None
                    if prev:
                        val = round(float(prev) * (1 + _limit_up_pct(code, rule_name)), 2)
                        src = 'calc:limit_rule'
                        used['calc'].add('daily_limit')
            elif ind == 'is_limit_up':
                # 纯机械布尔: round(price,2)>=round(daily_limit,2); 任一缺失->null
                price = quote.get('price') if quote else None
                dl = row.get('daily_limit')
                if price is not None and dl is not None:
                    val = round(float(price), 2) >= round(float(dl), 2)
                    src = 'calc:mechanical'
                    used['calc'].add('is_limit_up')
            elif ind == 'concepts_count':
                # 纯计数: concept_map该code概念条目数
                clist = row.get('concept') if 'concept' in row else None
                if clist is None and 'concept_map' in sources:
                    clist = _fetch_concept(code, 'concept')
                if clist is not None:
                    val = len(clist)
                    src = 'calc:count'
                    used['calc'].add('concepts_count')
                    if 'concept' not in used['concept_map']:
                        used['concept_map'].add('concepts_count')
            row[ind] = val
            if src:
                fsrc[ind] = src

        pkg['raw_data'][code] = row
        pkg['field_sources'][code] = fsrc

    # sources元数据(实际命中的源)
    if used['qfq']:
        pkg['sources'].append({'db': 'dragon_quant_qfq.db', 'table': QFQ_TABLE,
                               'time_range': list(rng['qfq'] or ('', '')),
                               'indicators': sorted(used['qfq'])})
    if used['concept_map']:
        pkg['sources'].append({'db': 'fundamental_data.db', 'table': 'concept_map',
                               'time_range': ['static', 'static'],
                               'indicators': sorted(used['concept_map'])})
    if used['westock_quote']:
        pkg['sources'].append({'db': 'westock_quote_api', 'table': 'data_quote(MCP)',
                               'time_range': list(rng['westock_quote'] or ('', '')),
                               'indicators': sorted(used['westock_quote'])})
    if used['calc']:
        pkg['sources'].append({'db': 'calc(static_rules)', 'table': 'limit_rule/mechanical/count',
                               'time_range': ['derived', 'derived'],
                               'indicators': sorted(used['calc']),
                               'note': '仅静态规则查表换算/机械布尔/计数, 无强弱判断'})

    # 指纹: sha256(code排序+指标排序后序列化)前16位
    canonical = json.dumps(pkg['raw_data'], sort_keys=True,
                           ensure_ascii=False, separators=(',', ':'))
    pkg['fingerprint'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]
    return pkg


def save_package(pkg, directory=PKG_DIR):
    """数据包落盘, 返回绝对路径"""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, pkg['package_id'] + '.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(pkg, f, ensure_ascii=False, indent=2)
    return path


# ---------------- CLI ----------------
def main():
    ap = argparse.ArgumentParser(description='标准数据包生成器v2.0(m1只读数据管家)')
    ap.add_argument('--codes', required=True, help='纯6位代码, 逗号分隔, 如 600272,002354')
    ap.add_argument('--indicators', required=True,
                    help='指标, 逗号分隔. v2.0新增: turnover_rate,amount,buy_sell_ratio,'
                         'inner_volume,outer_volume,daily_limit,is_limit_up,concepts_count')
    ap.add_argument('--sources', default=','.join(ALL_SOURCES),
                    help='可用源, 逗号分隔: qfq,concept_map,westock_quote')
    ap.add_argument('--save-dir', default=PKG_DIR, help='数据包落盘目录')
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(',') if c.strip()]
    indicators = [i.strip() for i in args.indicators.split(',') if i.strip()]
    sources = [s.strip() for s in args.sources.split(',') if s.strip()]

    pkg = build_package(codes, indicators, sources)
    path = save_package(pkg, args.save_dir)

    print(json.dumps(pkg, ensure_ascii=False, indent=2))
    print('---')
    print('已落盘: %s' % path)


if __name__ == '__main__':
    main()
