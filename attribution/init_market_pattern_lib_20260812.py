#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初始化市场涨停范式库 + 大跌风险库，并入库 2026-08-12 样本。

设计依据：屠龙复盘SOP(主人2026-08-12确立)，字段标准化、可被Qlib读取。
标的池：沪深主板+创业板，剔除ST/科创/北交所/上市<60天次新。
杜绝未来函数：每条标注 info_available_pre_open(催化剂是否开盘前可获取)。
"""
from __future__ import annotations
import sqlite3, json, os

DB = os.path.join(os.path.dirname(__file__), "attribution.db")

DDL = [
    """CREATE TABLE IF NOT EXISTS limit_up_patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT,
        industry TEXT,
        board TEXT,
        limit_count INTEGER DEFAULT 1,
        seal_status TEXT,
        first_limit_time TEXT,
        turnover REAL,
        primary_attribution TEXT,
        secondary_attribution TEXT,
        core_driver TEXT,
        predictability_score REAL,
        feature_tags TEXT,
        pattern_type TEXT,
        info_available_pre_open INTEGER,
        strategy_value TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE TABLE IF NOT EXISTS limit_down_risks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT,
        industry TEXT,
        board TEXT,
        drop_type TEXT,
        primary_attribution TEXT,
        secondary_attribution TEXT,
        avoidability_score REAL,
        risk_tags TEXT,
        info_available_pre_open INTEGER,
        strategy_value TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )""",
]

# 2026-08-12 涨停范式（已过滤科创/北交所/ST，选取6大主线代表龙头）
# predictability: 9-10强外部催化盘前可知; 7-8公告+主线; 5-6跟随无独立催化; 3-4随机脉冲; 0-2突发
LIMIT_UP_0812 = [
    # 主线1: 光通信/CPO/AI算力 (催化=昨晚美股Lumentum+109%/韩股SK海力士+8%/英伟达路线澄清，全盘前可知)
    {"code":"600105","name":"永鼎股份","industry":"通信设备","board":"沪市主板","limit_count":1,"seal_status":"尾盘封","first_limit_time":"10:42","turnover":8165109800,"primary":"消息基本面","secondary":"光通信CPO","core_driver":"光芯片订单11.33亿+中报预增57-120%+昨晚Lumentum营收+109%外盘催化","predictability_score":9.0,"feature_tags":["光通信","CPO","中报预增","外盘催化","主力净流入板块"],"pattern_type":"可复用范式","info_pre":1,"strategy":"外部催化+业绩双共振，主线首日龙头，可作次日低吸候选"},
    {"code":"603228","name":"景旺电子","industry":"元件-PCB","board":"沪市主板","limit_count":1,"seal_status":"尾盘封","first_limit_time":"14:43","turnover":6144858100,"primary":"消息基本面","secondary":"AI算力PCB","core_driver":"800G/1.6T光模块PCB量产+通信业务1-4月+100%+英伟达路线澄清","predictability_score":8.5,"feature_tags":["AI算力","PCB","光模块","中报兑现"],"pattern_type":"可复用范式","info_pre":1,"strategy":"PCB主线业绩兑现型，中国银河研报8/9已提示"},
    {"code":"002792","name":"通宇通讯","industry":"通信设备","board":"深市主板","limit_count":1,"seal_status":"盘中封","first_limit_time":"10:51","turnover":3280191900,"primary":"消息基本面","secondary":"英伟达概念+卫星通信","core_driver":"拟3亿入股佳贤通信(英伟达CUDA生态)+韩国半导体出口+155%","predictability_score":8.0,"feature_tags":["英伟达概念","卫星通信","光模块","外盘催化"],"pattern_type":"可复用范式","info_pre":1,"strategy":"英伟达映射+并购催化双逻辑"},
    {"code":"002552","name":"宝鼎科技","industry":"元件-覆铜板","board":"深市主板","limit_count":3,"seal_status":"一字封/T字","first_limit_time":"09:46","turnover":2053271700,"primary":"消息基本面","secondary":"覆铜板+黄金+中报预增","core_driver":"中报预增468-560%(金宝电子扭亏+河西金矿)+PCB主线共振","predictability_score":8.5,"feature_tags":["中报预增","PCB","黄金","3连板"],"pattern_type":"可复用范式","info_pre":1,"strategy":"3连板高度，业绩+主线双击，连板梯队标杆"},
    # 主线2: 人形机器人 (催化=宇树科技IPO 8/11超预期，盘前可知)
    {"code":"002031","name":"巨轮智能","industry":"通用设备","board":"深市主板","limit_count":1,"seal_status":"开盘秒板","first_limit_time":"09:30","turnover":1351865000,"primary":"板块情绪","secondary":"人形机器人-RV减速器","core_driver":"宇树科技IPO定价超预期(8/11)+RV减速器国产替代","predictability_score":7.5,"feature_tags":["人形机器人","RV减速器","IPO催化","开盘秒板"],"pattern_type":"可复用范式","info_pre":1,"strategy":"事件驱动(宇树IPO)主线龙头，开盘秒板封板强"},
    {"code":"000887","name":"中鼎股份","industry":"汽车零部件","board":"深市主板","limit_count":1,"seal_status":"尾盘封","first_limit_time":"09:34","turnover":1184961100,"primary":"消息基本面","secondary":"人形机器人-谐波减速器","core_driver":"谐波减速器30万颗订单+液冷+机器人电子皮肤","predictability_score":7.0,"feature_tags":["人形机器人","谐波减速器","液冷","订单"],"pattern_type":"可复用范式","info_pre":1,"strategy":"订单兑现型机器人标的"},
    # 主线3: AI应用 (催化=上海软件十五五规划8/11盘后+教育AI叙事修复)
    {"code":"002421","name":"达实智能","industry":"IT服务","board":"深市主板","limit_count":1,"seal_status":"尾盘封","first_limit_time":"09:40","turnover":678819840,"primary":"消息基本面","secondary":"AI智能体+AIoT","core_driver":"AI智能体方案+新皇岗口岸(深圳APEC)+半年报减亏+上海AI政策","predictability_score":6.5,"feature_tags":["AI应用","智能体","半年报减亏","政策催化"],"pattern_type":"题材脉冲","info_pre":1,"strategy":"政策+题材共振，但业绩仅减亏，持续性存疑"},
    # 主线4: 地产链 (催化=北京楼市新政8/8落地)
    {"code":"002146","name":"荣盛发展","industry":"房地产开发","board":"深市主板","limit_count":1,"seal_status":"尾盘封","first_limit_time":"09:40","turnover":493880800,"primary":"消息基本面","secondary":"地产新政+债务重组","core_driver":"北京非京籍购房社保2年→1年(8/8)+债务重组+高管增持","predictability_score":6.0,"feature_tags":["地产新政","债务重组","高管增持","低价股"],"pattern_type":"可复用范式","info_pre":1,"strategy":"政策博弈型，北京新政窗口期"},
    # 主线5: 大消费/影视 (催化=暑期档破86亿+龙餐馆8/11上映评分9.8)
    {"code":"002582","name":"好想你","industry":"食品饮料","board":"深市主板","limit_count":1,"seal_status":"尾盘封","first_limit_time":"09:39","turnover":497224660,"primary":"消息基本面","secondary":"中报预增+红枣龙头","core_driver":"中报预增37-39倍(参股鸣鸣很忙公允价值)+红枣龙头+大消费","predictability_score":8.5,"feature_tags":["中报预增39倍","低估值","大消费","业绩拐点"],"pattern_type":"可复用范式","info_pre":1,"strategy":"业绩拐点+低估值，本日被模拟盘卖飞涨停(第3次)，典型可复用范式"},
    {"code":"000802","name":"北京文化","industry":"影视","board":"深市主板","limit_count":1,"seal_status":"开盘封","first_limit_time":"09:25","turnover":662665200,"primary":"消息基本面","secondary":"暑期档-龙餐馆","core_driver":"参投《欢迎来龙餐馆》8/11上映首日2.3亿+评分9.8+预测35亿","predictability_score":7.5,"feature_tags":["影视","票房催化","暑期档","集合竞价封板"],"pattern_type":"可复用范式","info_pre":1,"strategy":"票房事件驱动，集合竞价封板=预期一致"},
    # 主线6: 有色/资源 (碳酸锂站上15万+铝价上涨)
    {"code":"002379","name":"宏桥控股","industry":"有色金属-铝","board":"深市主板","limit_count":1,"seal_status":"尾盘封","first_limit_time":"09:55","turnover":1486559700,"primary":"消息基本面","secondary":"铝全产业链+中报预增","core_driver":"中报预增70-81%(电解铝涨价)+拟定增120亿+铝全产业链","predictability_score":8.0,"feature_tags":["中报预增","有色铝","定增","全产业链"],"pattern_type":"可复用范式","info_pre":1,"strategy":"业绩+商品价格双驱，本日被模拟盘涨停价追入(违背R7)"},
    {"code":"603399","name":"永杉锂业","industry":"有色金属-锂","board":"沪市主板","limit_count":1,"seal_status":"炸板回封","first_limit_time":"10:04","turnover":2076480100,"primary":"消息基本面","secondary":"碳酸锂涨价+中报扭亏","core_driver":"碳酸锂主力站上15万(+3.18%)+中报扭亏1.6-2.3亿","predictability_score":7.0,"feature_tags":["碳酸锂","中报扭亏","商品价格驱动"],"pattern_type":"可复用范式","info_pre":1,"strategy":"商品价格+业绩双击"},
    # 独立催化
    {"code":"600881","name":"亚泰集团","industry":"建材-综合","board":"沪市主板","limit_count":1,"seal_status":"尾盘封","first_limit_time":"13:52","turnover":755997410,"primary":"消息基本面","secondary":"预重整+国资","core_driver":"预重整招募重整投资人+长春国资+持东北证券30.81%","predictability_score":5.5,"feature_tags":["预重整","国资","低位低价"],"pattern_type":"题材脉冲","info_pre":1,"strategy":"重整博弈型，事件驱动持续性待验"},
]

# 2026-08-12 大跌风险（今日无真实跌停，记录持仓下跌+闪跌案例）
LIMIT_DOWN_0812 = [
    {"code":"000571","name":"阳光股份","industry":"房地产","board":"深市主板","drop_type":"闪跌触及跌停","primary":"资金面","secondary":"流动性冲击","avoidability_score":3.0,"risk_tags":["早盘闪跌","流动性差","快速开板"],"info_pre":0,"strategy":"9:30-9:32短暂触及跌停后秒回，无基本面恶化，属流动性冲击，不可预判"},
    {"code":"600644","name":"乐山电力","industry":"电力","board":"沪市主板","drop_type":"持仓下跌-1.52%","primary":"板块情绪","secondary":"电力板块退潮","avoidability_score":6.0,"risk_tags":["板块资金流出","PE268倍过高","防御板块被弃"],"info_pre":1,"strategy":"电力板块主力净流出-6.66亿，资金切向科技，PE268倍估值过高是前置风险"},
    {"code":"300274","name":"阳光电源","industry":"新能源-光伏","board":"创业板","drop_type":"持仓下跌-2.12%","primary":"板块情绪","secondary":"新能源分化","avoidability_score":5.5,"risk_tags":["新能源分化","光伏承压","高价股"],"info_pre":1,"strategy":"新能源板块分化，光伏逆变器承压，高价股波动大"},
    {"code":"300363","name":"博腾股份","industry":"医疗服务-CRO","board":"创业板","drop_type":"持仓下跌-1.19%","primary":"板块情绪","secondary":"医疗服务板块最弱","avoidability_score":6.5,"risk_tags":["医疗服务-4.10%主力流出","CRO板块退潮"],"info_pre":1,"strategy":"医疗服务板块主力净流出-19.32亿(全市场最弱)，板块系统性弱势"},
]


def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    for ddl in DDL:
        cur.execute(ddl)

    # 清理今日重复（幂等）
    cur.execute("DELETE FROM limit_up_patterns WHERE trade_date='20260812'")
    cur.execute("DELETE FROM limit_down_risks WHERE trade_date='20260812'")

    n_up = 0
    for r in LIMIT_UP_0812:
        cur.execute("""INSERT INTO limit_up_patterns
            (trade_date,code,name,industry,board,limit_count,seal_status,first_limit_time,turnover,
             primary_attribution,secondary_attribution,core_driver,predictability_score,
             feature_tags,pattern_type,info_available_pre_open,strategy_value)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("20260812", r["code"], r["name"], r["industry"], r["board"], r["limit_count"],
             r["seal_status"], r["first_limit_time"], r["turnover"],
             r["primary"], r["secondary"], r["core_driver"], r["predictability_score"],
             json.dumps(r["feature_tags"], ensure_ascii=False),
             r["pattern_type"], r["info_pre"], r["strategy"]))
        n_up += 1

    n_dn = 0
    for r in LIMIT_DOWN_0812:
        cur.execute("""INSERT INTO limit_down_risks
            (trade_date,code,name,industry,board,drop_type,primary_attribution,secondary_attribution,
             avoidability_score,risk_tags,info_available_pre_open,strategy_value)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("20260812", r["code"], r["name"], r["industry"], r["board"], r["drop_type"],
             r["primary"], r["secondary"], r["avoidability_score"],
             json.dumps(r["risk_tags"], ensure_ascii=False),
             r["info_pre"], r["strategy"]))
        n_dn += 1

    conn.commit()

    # 统计简报
    cur.execute("SELECT COUNT(*) FROM limit_up_patterns")
    total_up = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM limit_down_risks")
    total_dn = cur.fetchone()[0]
    cur.execute("""SELECT pattern_type, COUNT(*), ROUND(AVG(predictability_score),1)
                   FROM limit_up_patterns WHERE trade_date='20260812' GROUP BY pattern_type""")
    print("=== 2026-08-12 涨停范式入库统计 ===")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]}条, 平均可预判性{row[2]}分")
    cur.execute("""SELECT secondary_attribution, COUNT(*) FROM limit_up_patterns
                   WHERE trade_date='20260812' GROUP BY secondary_attribution ORDER BY COUNT(*) DESC""")
    print("=== 二级归因频次 ===")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]}条")

    print(f"\n今日新增: 涨停范式{n_up}条(库累计{total_up}), 大跌风险{n_dn}条(库累计{total_dn})")
    conn.close()


if __name__ == "__main__":
    main()
