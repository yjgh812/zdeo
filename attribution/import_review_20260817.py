# -*- coding: utf-8 -*-
"""
2026-08-17 深度复盘结论入库
涨停范式库(limit_up_patterns) + 大跌风险库(limit_down_risks)
归因基于当日复盘: 财联社电报/东财涨停池/主力资金/横盘突破验证
严格遵守: 仅当日收盘前可得信息, 无未来函数
"""
import sqlite3, json, time
from datetime import datetime

DB = r'D:\zcodesj\attribution\attribution.db'
TRADE_DATE = '20260817'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 防重复: 若当日已入库则跳过
n_exist = cur.execute('SELECT COUNT(*) FROM limit_up_patterns WHERE trade_date=?', (TRADE_DATE,)).fetchone()[0]
if n_exist > 0:
    print(f'[跳过] 涨停范式库当日已有 {n_exist} 条')
else:
    zt = json.load(open('D:/zcodesj/tmp/market_zt_20260817.json', encoding='utf-8'))['zt_pool']
    # 引入横盘突破验证结果
    brk = json.load(open('D:/zcodesj/tmp/breakout_check_20260817.json', encoding='utf-8'))
    brk_map = {r['c']: r for r in brk if r.get('ok')}

    def board_of(code):
        if code.startswith('688'): return '科创板'
        if code.startswith(('8','4','920')): return '北交所'
        if code.startswith('30'): return '创业板'
        if code.startswith('6'): return '沪市主板'
        return '深市主板'

    def classify(code, name, hybk, lbc, fbt, amount):
        """基于今日复盘主线归因(盘前可得信息)"""
        # 半导体链关键词(存储/封测/CPO/PCB/材料/设备/设计/玻璃基板/磷化铟/电子化学品)
        SEMI = ['电子化学','半导体','元件','光学光电','通信设备','电子','化学制品','塑料','其他电子',
                '硅料','印制电路','电子元器件']
        if hybk in ('半导体','电子化学','元件','通信设备','其他电子') or name in (
            '通富微电','兴森科技','太辰光','有研新材','华正新材','中石科技','金禄电子','沃格光电',
            '中瓷电子','豫金刚石','天洋新材','聚和材料','苏州天脉','电连技术','中远通','亚翔集成',
            '卓郎智能','宏昌电子','博敏电子','科翔股份','胜宏科技','沪电股份','深南电路','生益科技',
            '中电港','江化微','雅克科技','上海新阳','鼎龙股份','安集科技','中微公司','北方华创','拓荆科技'):
            return '半导体链','半导体产业共振(美股存储暴涨+PCB涨价+CPO量产)','板块共振','消息驱动'
        AGRI = ['农产品加','养殖业','种子','种植','粮食','牧渔','食品','农业']
        if hybk in ('农产品加','养殖业') or name in ('农发种业','金健米业','京粮控股','罗牛山','天山生物',
            '神农种业','敦煌种业','深粮控股','秋乐种业','康农种业'):
            return '农业链','粮食危机预警(摩根大通)+生猪涨价+国内粮食安全政策','板块共振','消息驱动'
        MED = ['化学制药','医疗器械','医药商业','医疗服务','中药','生物制品']
        if hybk in ('化学制药','医疗器械','医药商业') or name in ('澳洋健康','誉衡药业','华森制药','神奇制药',
            '圣达生物','海泰新光','戴维医疗','百合股份','太龙药业','冀衡医药','开开实业','亚太药业'):
            return '医药链','创新药License-out超1100亿美元+CXO全球回暖','板块共振','消息驱动'
        CONSUME = ['一般零售','饮料乳品','食品','商业']
        if hybk == '一般零售' or name in ('一鸣食品','桂发祥','阳光乳业','供销大集','利群股份','益民集团',
            '哈森股份','深深房A','亚振家居','百合股份','黑芝麻'):
            return '大消费链','国新办8/18县域消费发布会预期+十五五消费规划60万亿','政策催化','预期驱动'
        ROBOT = ['通用设备','专用设备','汽车零部','自动化']
        if name in ('日盈电子','征和工业','北自科技','中原内配','正裕工业','越剑智能','合锻智能','京城股份',
            '襄阳轴承','劲旅环境','宇环数控','绿的谐波','鸣志电器','拓斯达'):
            return '机器人链','人形机器人运动会8/22+宇树8/19上市+湖南机器人产业政策','事件驱动','预期驱动'
        POWER = ['电网设备','电源','电力']
        if hybk == '电网设备' or name in ('风范股份','神马电力','思源电气','柘中股份','顺钠股份','伊戈尔',
            '金冠电气','韶能股份'):
            return '电网设备链','新型电力系统十五五规划5万亿投资','政策催化','消息驱动'
        return '其他','个股独立逻辑/题材脉冲','独立行情','资金驱动'

    def seal_status_of(fbt, lbt, zbc, amount):
        fb = str(fbt).zfill(6)
        lb = str(lbt).zfill(6)
        if fb == lb and fb <= '092510':
            return f'一字板{amount/1e8:.1f}亿'
        hrs = f'{fb[:2]}:{fb[2:4]}'
        return f'首封{hrs} 封板{amount/1e8:.1f}亿'

    def score_of(fbt, main):
        fb = str(fbt).zfill(6)
        hm = int(fb[:2])*60 + int(fb[2:4])
        base = 6.0
        if main in ('半导体链','农业链','医药链','大消费链','机器人链','电网设备链'):
            base = 7.0
        if hm <= 600: base += 1.5   # 10:00前
        elif hm <= 660: base += 0.5 # 11:00前
        elif hm <= 870: base -= 1.0
        else: base -= 2.0           # 尾盘
        return round(max(2.0, min(9.5, base)), 1)

    rows = []
    for p in zt:
        code, name = p['c'], p['n']
        hybk = p.get('hybk') or ''
        fbt = p.get('fbt')
        lbc = p.get('lbc', 1)
        amount = p.get('amount', 0)
        main, driver, sec_attr, prim_attr = classify(code, name, hybk, lbc, fbt, amount)
        # 横盘突破标签
        br = brk_map.get(code)
        tag_sub = ''
        if br and br.get('is_breakout'):
            tag_sub = '横盘放量突破'
        ft = json.dumps({"main": main.replace('链',''), "sub": sec_attr, "emotion": "HOT",
                         "board_dominant": main in ('半导体链','大消费链'), "idio_dominant": main=='其他',
                         "conflict": False, "driver": driver, "extra": tag_sub}, ensure_ascii=False)
        rows.append((
            TRADE_DATE, code, name.replace(' ',''), hybk, board_of(code), lbc,
            seal_status_of(fbt, p.get('lbt'), p.get('zbc',0), amount),
            str(fbt).zfill(6), amount, prim_attr, sec_attr, driver,
            score_of(fbt, main), ft, 'review_v2',
            1 if main in ('半导体链','农业链','医药链','大消费链','电网设备链') else 0,
            f'主线:{main}; 驱动:{driver[:40]}'
        ))
    cur.executemany('''INSERT INTO limit_up_patterns
        (trade_date, code, name, industry, board, limit_count, seal_status, first_limit_time,
         turnover, primary_attribution, secondary_attribution, core_driver,
         predictability_score, feature_tags, pattern_type, info_available_pre_open, strategy_value)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
    conn.commit()
    print(f'[OK] 涨停范式入库 {len(rows)} 条(含科创/北交标的外代码)')

# ============ 大跌风险库 ============
n_risk = cur.execute('SELECT COUNT(*) FROM limit_down_risks WHERE trade_date=?', (TRADE_DATE,)).fetchone()[0]
if n_risk > 0:
    print(f'[跳过] 风险库当日已有 {n_risk} 条')
else:
    risks = [
        (TRADE_DATE, '000802', '北京文化', '影视院线', '深市主板', 'stock_drop',
         '资金退潮', '高位兑现', '全市场唯一跌停;当日无重大利空公告,影视传媒无催化+短线资金离场',
         4.0, json.dumps(["资金退潮","高位兑现","题材无催化"], ensure_ascii=False), 0,
         '跌停共性:无重大利空时的资金退潮型跌停,规避无催化冷门题材'),
        (TRADE_DATE, 'ALL', '通用方法论:高位X天Y板核查', None, None, 'methodology_rule',
         '监管核查风险', '短期爆炒触发核查',
         '爱丽家居11连板+185%被交易所核查后8/18复牌;一鸣食品15天10板/风范股份11天7板同型风险',
         8.0, json.dumps(["HIGH_BOARD_VERIFY", "X天Y板>=8天", "核查停牌", "严禁追高"], ensure_ascii=False), 1,
         'X天Y板天数阈值>=8天触发核查预警,纳入禁追硬闸门;来源=2026-08-17爱丽家居案例'),
        (TRADE_DATE, 'ALL', '通用方法论:昨日涨停主力兑现', None, None, 'methodology_rule',
         '短线兑现', '打板资金次日离场',
         '8/17昨日涨停板块主力净流出33.76亿,昨日63只涨停33%收跌(-6.8%成都路桥);连板一进二失败率高位',
         7.0, json.dumps(["ZT_BOARD_OUTFLOW", "昨日涨停板块净流出", "一进二谨慎"], ensure_ascii=False), 1,
         '次日追涨需先查昨日涨停板块主力净流出;工具:东财板块资金接口,来源=2026-08-17复盘'),
        (TRADE_DATE, '600519', '贵州茅台', '白酒', '沪市主板', 'stock_drop',
         '财报利空', '业绩不及预期',
         '中报H1净利445亿同比-1.95%,Q2净利172.7亿环比-36%;失守1300整数关,主力净流出9.24亿',
         7.5, json.dumps(["SMI_REPORT_DOWN", "Q2环比-36%", "白酒权重利空"], ensure_ascii=False), 1,
         '财报低于预期权重股规避;来源=2026-08-17中报'),
        (TRADE_DATE, 'ALL', '通用方法论:光模块高位兑现', None, None, 'methodology_rule',
         '高低切换', '高位龙头资金流出',
         '8/17新易盛净卖出超15亿/中际旭创资金流出,资金转向太辰光等中低位CPO;高位光模块兑现风险',
         6.0, json.dumps(["OPTICAL_HIGH_SELL", "龙头流出", "内部高低切"], ensure_ascii=False), 1,
         '同板块内龙头流出时优先低位补涨;来源=2026-08-17复盘'),
    ]
    cur.executemany('''INSERT INTO limit_down_risks
        (trade_date, code, name, industry, board, drop_type, primary_attribution,
         secondary_attribution, avoidability_score, risk_tags, info_available_pre_open, strategy_value)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', risks)
    conn.commit()
    print(f'[OK] 大跌风险库入库 {len(risks)} 条')
conn.close()
print('完成:', datetime.now().strftime('%H:%M:%S'))
