# -*- coding: utf-8 -*-
"""屠龙研究回测标准模板 v1 (2026-10-10, 自省P0产出)
来源: 10/9 深夜四轮研究(突破分批/盈侧重放/限价预案/主线双指标)的教训固化
      —— 三次实现bug(量价列索引/基准初值/heredoc编码)均靠断言才抓到

用法:
    from analysis.backtest_template_std import *
    # 1) 数据加载(标准口径)
    uni = load_universe()                      # 屠龙宇宙4526只(剔ST/科创/北交所)
    ind_map = load_industry_map(uni)           # 通达信L1映射(可选)
    # 2) 断言(发布前必过, 任一失败=结果作废)
    assert_section_median_zero(excess_list)    # 截面超额中位≈0
    assert_sample_size(n, minimum=20)          # 样本量下限
    assert_yearly_coverage(by_year)            # 分年覆盖
    # 3) 已知陷阱清单见 TRAPS(勿重踩)

铁律引用: 宪法§10(回测必须qfq+next-bar+PIT) / 配对检验铁律(买卖同锁) /
         证伪档案引用制(重测X先查falsified_rules.json) / 元规则(大数字漏损≠可回收)
"""
import sqlite3, os, json
from collections import defaultdict

# ============ 标准数据源路径 ============
DB_QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'  # 唯一价格源(前复权; 2026-10-13 切wb真源, SOP一百三十二)
UNI_JSON = r'D:\zcodesj\data\universe_portrait_latest.json'
DB_FUND = r'D:\workbuddyspace\databases\fundamental_data.db'  # a-share-local fund库(industry_map=通达信L1)
DB_ATTR = r'D:\zcodesj\attribution\attribution.db'
DB_ZT = r'D:\zcodesj\data\zt_pool_history.db'
VIPDOC = r'D:\anzhuang\tdx\vipdoc'                        # 未复权交叉验证源

# ============ 已知陷阱清单(TRAPS, 10/9实测踩过) ============
TRAPS = {
    'qfq列索引': 'qfq行tuple=(date,open,high,low,close,vol,amount)视select而定——close不一定[2]! 按select列名数索引, 写完先sanity打印一行',
    '台账code前缀': 'trade_records/westock code带sh/sz前缀(sh600984), qfq/zt_pool/画像=裸码——统一[-6:]',
    'industry_map后缀': 'fund库industry_map.ts_code带.SZ/.SH后缀——split(".")[0]后再JOIN',
    'westock限频': 'westock data_quote/data_technical批量调用易限频——本地vipdoc/qfq优先, MCP只做小批量',
    'heredoc编码': 'Bash heredoc内嵌中文SQL会损坏——SQL写.py文件再执行, 禁open(p,w).write(entry+read())单表达式',
    '复权基准混用': '台账实价÷qfq复权价会被除权扭曲——比例口径验证须用vipdoc未复权交叉(限价预案案例)',
    '基准初值': '累计收益基准初值必须1.0(乘法)/0(加法)——初值0乘法恒0(主线双指标案例)',
    '截面断言': '全截面partition的各组超额中位全负=必有实现bug(免费的正确性断言,先跑断言再看结论)',
}

# ============ 标准加载器 ============
def load_universe(exclude_prefix=('688', '689', '4', '8', '9')):
    """屠龙冻结宇宙(画像4526只, 已剔ST/科创/北交所) + 代码前缀双保险"""
    d = json.load(open(UNI_JSON, encoding='utf-8'))
    uni = set()
    for r in d['rows']:
        c = str(r['code'])
        if c[:3] in exclude_prefix or c[0] in '489':
            continue
        uni.add(c)
    return uni

def qfq_stream(uni, date_from='20150601'):
    """流式逐股产出(免大内存); yield (code, rows) rows=[(date,open,high,low,close,vol,amount)]"""
    c = sqlite3.connect('file:' + DB_QFQ.replace('\\', '/') + '?mode=ro', uri=True)
    cur = c.execute("select code,date,open,high,low,close,volume,amount from stock_daily_meta_qfq "
                    "where date>=? order by code,date", (date_from,))
    cur_code, buf = None, []
    for code, date, o, h, l, cl, v, a in cur:
        if code not in uni:
            continue
        if code != cur_code:
            if cur_code is not None:
                yield cur_code, buf
            cur_code, buf = code, []
        buf.append((date, o, h, l, cl, v, a))
    if cur_code:
        yield cur_code, buf
    c.close()

def ro(path):
    return sqlite3.connect('file:' + path.replace('\\', '/') + '?mode=ro', uri=True)

# ============ 三断言(发布前必过) ============
def assert_section_median_zero(excess_list, tol_pp=0.15, name='截面超额'):
    """断言1: 全截面超额中位≈0(分组partition时四组合计必须≈0)——抓基准/索引/初值类bug"""
    if not excess_list:
        raise AssertionError(f'[{name}] 空样本')
    s = sorted(excess_list)
    med = s[len(s) // 2] * 100
    assert abs(med) <= tol_pp, f'[{name}] 中位{med:.3f}pp超容差±{tol_pp}pp —— 实现必有bug, 结果作废'

def assert_sample_size(n, minimum=20, name='样本'):
    """断言2: 样本量下限——不足时结论只能登记观察, 禁立项/禁否决"""
    assert n >= minimum, f'[{name}] n={n} < {minimum} —— 只可登记observation, 不可下裁决性结论(如实呈现)'

def assert_yearly_coverage(by_year, min_years=5, name='分年'):
    """断言3: 分年覆盖——防单一年份主导的伪稳定"""
    assert len(by_year) >= min_years, f'[{name}] 仅覆盖{len(by_year)}年 < {min_years} —— 警惕单段市况拟合'

def sanity_print(rows_one, cols_desc):
    """首行sanity打印(接TRAPS['qfq列索引']): 加载后先打印一行人工核对列语义"""
    print('[sanity]', dict(zip(cols_desc, rows_one)))

# ============ 标准口径常量 ============
NEXT_BAR = '信号用T日收盘, 买卖执行T+1开盘(next-bar)'
SAME_LOCK = '配对检验: 入场与出场规则必须同锁, 单侧证伪会漏配对alpha'
STOP_S5 = '-8%硬止损同锁: close<=加权成本*0.92 -> 次日open清(事件出场保留实际路径)'
NO_LOOKAHEAD = '无前视检查点: 任何触发只用<=T日数据; 重放窗口=entry次日起'
FALSIFIED_FIRST = '立项前查证伪库(data/falsified_rules.json): 重测X必须引用档案确认条件不同'
BIG_NUMBER_RULE = '元规则: 大数字漏损≠可回收——无前视重放验证可回收性后再立项(8.1pp回吐/突破追入两例)'
SEMANTIC_PRECHECK = ('语义方向预检(2026-10-18 DeepSeek审计V2固化): 新信号"确认"逻辑若属放量/突破/加速族, '
                     '默认负先验(语义反转假说跨6域零反例)——立项须先引用证伪库对应条目并给出与已证伪条件不同的论证, 否则不予立案')

ML_LEG_FOOTNOTE = ('ml腿 +Y% 为训练窗重叠样本内口径, 真walk-forward价值8.6年仅+3~12%(round11b实证)——'
                   '组合/单腿汇报禁裸引ml腿数字, 缺注脚呈报按伪造证据风险处理(材料①§7铁律, 2026-10-18 DeepSeek审计V1固化)')

def combo_report_line(combo_ret, bp_ep_ret, ml_ret):
    """组合回测汇报标准行(强制内嵌ml腿样本内注脚, 防口径混用误导)"""
    return (f'组合 {combo_ret}（bp_ep腿 {bp_ep_ret} 为walk-forward口径; '
            f'ml腿 {ml_ret} 为样本内口径, 真OOS贡献+3~12%）')

if __name__ == '__main__':
    print('屠龙研究回测标准模板 v1')
    print('TRAPS:', len(TRAPS), '条 | 断言3件套 + sanity打印 | 口径常量7条(含SEMANTIC_PRECHECK/ML_LEG_FOOTNOTE)')
    uni = load_universe()
    print('宇宙自检:', len(uni), '只 (期望≈4526)')
    it = qfq_stream(list(uni)[:1])
    code, rows = next(it)
    sanity_print(rows[0], ['date', 'open', 'high', 'low', 'close', 'vol', 'amount'])
    print('骨架自检 PASS')
