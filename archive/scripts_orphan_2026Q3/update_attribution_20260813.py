#!/usr/bin/env python3
"""2026-08-13 涨停范式 + 跌停风险 归因库更新"""
import sqlite3, json
from datetime import datetime

DB = "D:/zcodesj/attribution/attribution.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# === STEP 1: 清理错误的20260813数据（已在上一轮清理，这里幂等执行）===
cur.execute("DELETE FROM limit_up_patterns WHERE trade_date='20260813'")
print(f"[清理] limit_up_patterns 删除 {cur.rowcount} 条旧数据")

# === STEP 2: 插入跌停风险库 ===
limit_down_data = [
    ("003032", "传智教育", "教育", "前7连板跌停", "天地板", "高位兑现", "空间龙退潮", 8.5,
     json.dumps(["HIGH_BOARD_COLLAPSE", "7板跌停天地板", "情绪周期退潮", "教育题材炒作结束"], ensure_ascii=False),
     "前7连板高度过高+无持续基本面催化+百花医药断板信号", "空间龙断板=全市场退潮预警，禁止追高6板以上", now),
    ("002827", "高争民爆", "化学制品", "前4板跌停", "高位跌停", "高位兑现", "前期涨幅过大",
     7.5, json.dumps(["HIGH_PROFIT_TAKING", "4板后高位兑现", "民爆题材退潮", "无新催化"], ensure_ascii=False),
     "前期4连板涨幅过大+板块无新消息+高位放量", "3板以上个股严格执行阶梯止盈", now),
    ("603990", "麦迪科技", "软件开发", "高位跌停", "高位跌停", "资金出逃", "无基本面支撑",
     7.0, json.dumps(["SPECULATION_REVERSAL", "纯炒作反转", "软件开发退潮", "高位破位"], ensure_ascii=False),
     "前期涨幅靠情绪驱动+无业绩支撑+板块退潮", "无基本面支撑的纯炒作股不参与", now),
    ("000779", "甘咨询", "工程咨询", "高位跌停", "高位跌停", "板块退潮", "高位破位",
     6.5, json.dumps(["SECTOR_RETREAT", "工程咨询板块退潮", "高位技术破位", "获利盘出逃"], ensure_ascii=False),
     "工程咨询板块整体走弱+个股高位MACD死叉", "板块退潮时持有个股优先减仓", now),
]
cur.executemany("""INSERT INTO limit_down_risks
    (trade_date, code, name, industry, board, drop_type, primary_attribution, secondary_attribution,
     avoidability_score, risk_tags, info_available_pre_open, strategy_value, created_at)
    VALUES ('20260813', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", limit_down_data)
print(f"[入库] limit_down_risks 新增 {len(limit_down_data)} 条跌停风险")

# === STEP 3: 插入涨停范式（核心标的）===
limit_up_data = [
    # --- 医药主线（最强主线，13只涨停）---
    ("002437", "誉衡药业", "化学制药", 2, "缩量封板", "09:51", 22.9,
     "政策催化", "板块共振", "第12批集采稳临床反内卷+基药目录预期", 8.5,
     json.dumps(["POLICY_CATALYST", "SECTOR_RESONANCE", "MEDICAL_MAINLINE", "LOW_PRICE"], ensure_ascii=False),
     "中军加速", "集采政策8/13见报+誉衡2板缩量=资金共识", "主线中军，3板加速可持有", now),

    ("300404", "博济医药", "医疗服务", 1, "20cm首板", "09:57", 21.4,
     "政策催化", "超跌反弹", "医疗设备招采回暖+集采利空出尽", 8.0,
     json.dumps(["POLICY_CATALYST", "OVERSOLD_BOUNCE", "MEDICAL_MAINLINE", "20CM"], ensure_ascii=False),
     "主线首板", "7月招采数据回暖+医疗服务板块共振", "20cm弹性首板，分歧回调可低吸", now),

    ("301520", "万邦医药", "医疗服务", 1, "20cm烂板5次", "10:44", 49.3,
     "政策催化", "资金博弈", "医疗设备招采回暖+CRO修复", 7.0,
     json.dumps(["POLICY_CATALYST", "20CM", "MEDICAL_MAINLINE", "BROKEN_5_TIMES"], ensure_ascii=False),
     "主线首板", "集采转暖+盘口3次大笔买入", "炸板5次质量差，追高风险大", now),

    ("300534", "陇神戎发", "中药", 1, "20cm首板", "10:55", 34.1,
     "政策催化", "板块共振", "2026版基药目录中成药扩容48款", 8.0,
     json.dumps(["POLICY_CATALYST", "TCM_BASELIST", "MEDICAL_MAINLINE", "20CM"], ensure_ascii=False),
     "主线首板", "基药目录9月施行+中药板块扩散", "20cm中药首板，中期逻辑持续", now),

    ("002644", "佛慈制药", "中药", 1, "缩量封板", "10:36", 5.0,
     "政策催化", "板块共振", "基药目录中成药扩容+超跌低位", 7.5,
     json.dumps(["POLICY_CATALYST", "TCM_BASELIST", "MEDICAL_MAINLINE", "LOW_PRICE"], ensure_ascii=False),
     "主线首板", "中药板块扩散+低价低位补涨", "低价中药补涨，符合30元域", now),

    ("603998", "方盛制药", "中药", 1, "放量封板", "13:27", 9.1,
     "政策催化", "板块共振", "基药目录+中药扩散", 7.0,
     json.dumps(["POLICY_CATALYST", "TCM_BASELIST", "MEDICAL_MAINLINE"], ensure_ascii=False),
     "主线首板", "午后中药扩散涨停", "中药主线补涨", now),

    ("600613", "神奇制药", "化学制药", 1, "缩量封板", "10:04", 4.9,
     "政策催化", "板块共振", "集采稳临床利好成熟药企", 7.0,
     json.dumps(["POLICY_CATALYST", "MEDICAL_MAINLINE", "LOW_PRICE"], ensure_ascii=False),
     "主线首板", "集采政策利好+低价化学制药", "低价药企补涨", now),

    ("002219", "新里程", "医疗服务", 1, "放量封板", "10:02", 7.6,
     "政策催化", "板块共振", "医疗服务板块全面爆发", 7.0,
     json.dumps(["POLICY_CATALYST", "MEDICAL_MAINLINE", "LOW_PRICE"], ensure_ascii=False),
     "主线首板", "医疗服务板块共振+低价", "低价医疗服务补涨", now),

    ("002172", "澳洋健康", "医疗服务", 2, "缩量封板", "09:25", 3.0,
     "政策催化", "板块共振", "医疗服务持续+超跌反弹", 7.5,
     json.dumps(["POLICY_CATALYST", "MEDICAL_MAINLINE", "OVERSOLD_BOUNCE", "LOW_PRICE"], ensure_ascii=False),
     "中军加速", "医疗服务主线2板加速", "2板缩量=资金共识", now),

    ("301060", "兰卫医学", "医疗服务", 1, "20cm首板", "13:04", 10.7,
     "政策催化", "超跌反弹", "医疗设备招采回暖+ICL修复", 7.5,
     json.dumps(["POLICY_CATALYST", "OVERSOLD_BOUNCE", "MEDICAL_MAINLINE", "20CM"], ensure_ascii=False),
     "主线首板", "午后医疗服务扩散", "20cm弹性，分歧后观察", now),

    ("300937", "药易购", "医药商业", 1, "20cm首板", "11:13", 29.4,
     "政策催化", "资金博弈", "医药商业扩散+超跌反弹", 7.0,
     json.dumps(["POLICY_CATALYST", "MEDICAL_MAINLINE", "20CM", "OVERSOLD_BOUNCE"], ensure_ascii=False),
     "弹性首板", "医药商业扩散+资金选弹性", "20cm医药商业弹性", now),

    # --- 电力主线（季节性辅线，6只涨停）---
    ("601991", "大唐发电", "电力", 1, "巨量封单3.77亿", "13:45", 7.2,
     "产业链催化", "板块轮动", "用电负荷5次创新高+迎峰度夏", 8.5,
     json.dumps(["INDUSTRY_CATALYST", "POWER_PEAK", "WEIGHT_LEADER", "SEAL_377M"], ensure_ascii=False),
     "权重首板", "南方电网负荷2.78亿千瓦创新高", "火电龙头巨量封单=号召力强", now),

    ("600726", "华电能源", "电力", 1, "放量封板", "09:38", 1.9,
     "产业链催化", "板块轮动", "用电负荷创新高+火电估值修复", 7.5,
     json.dumps(["INDUSTRY_CATALYST", "POWER_PEAK", "LOW_PRICE"], ensure_ascii=False),
     "主线首板", "迎峰度夏+火电估值近三年低位", "火电板块跟风品种", now),

    ("600821", "金开新能", "电力", 1, "放量封板", "09:34", 4.0,
     "产业链催化", "板块轮动", "用电负荷+新能源发电", 7.0,
     json.dumps(["INDUSTRY_CATALYST", "POWER_PEAK", "NEW_ENERGY"], ensure_ascii=False),
     "主线首板", "电力板块扩散至新能源发电", "新能源发电跟风", now),

    ("000692", "惠天热电", "电力", 2, "放量封板", "09:36", 8.5,
     "产业链催化", "板块轮动", "迎峰度夏+热电需求", 7.5,
     json.dumps(["INDUSTRY_CATALYST", "POWER_PEAK", "LOW_PRICE"], ensure_ascii=False),
     "中军加速", "电力主线2板加速", "电力中军", now),

    ("603191", "望变电气", "电网设备", 2, "放量封板", "13:34", 11.9,
     "产业链催化", "板块共振", "电网投资+变压器景气", 7.5,
     json.dumps(["INDUSTRY_CATALYST", "GRID_EQUIP", "POWER_CHAIN"], ensure_ascii=False),
     "中军加速", "电网设备2板=电力链扩散", "电网设备中军", now),

    # --- 消费主线 ---
    ("605179", "一鸣食品", "饮料乳品", 4, "烂板开9次", "09:48", 6.5,
     "情绪驱动", "龙头抱团", "消费穿越龙+资金抱团", 7.0,
     json.dumps(["EMOTION_DRIVEN", "CONSUMER_LEADER", "BROKEN_9_TIMES", "HIGH_RISK"], ensure_ascii=False),
     "龙头加速", "消费穿越龙4板=抱团惯性", "烂板9次+天量分歧=见顶风险", now),

    ("002329", "皇氏集团", "饮料乳品", 4, "烂板开4次", "09:25", 24.3,
     "情绪驱动", "龙头抱团", "消费穿越龙+资金抱团", 6.5,
     json.dumps(["EMOTION_DRIVEN", "CONSUMER_LEADER", "BROKEN_4_TIMES", "TURNOVER_24PCT", "HIGH_RISK"], ensure_ascii=False),
     "龙头加速", "4板换手24%=极端分歧", "天量换手=见顶信号", now),

    # --- 连板龙头 ---
    ("603758", "秦安股份", "汽车零部件", 5, "一字封板", "09:25", 1.2,
     "龙头加速", "情绪驱动", "汽车零部件+空间龙", 8.0,
     json.dumps(["LEADER_ACCEL", "HIGHEST_BOARD", "AUTO_PARTS", "ONE_WORD_SEAL"], ensure_ascii=False),
     "龙头加速", "市场最高空间板5板=情绪锚定", "最高板=风向标，断板则退潮确认", now),

    ("600683", "京投发展", "房地产开发", 4, "一字1.6亿", "09:25", 0.25,
     "政策催化", "龙头抱团", "地产政策预期+资金抱团", 7.5,
     json.dumps(["POLICY_CATALYST", "RE_LEADER", "ONE_WORD_SEAL", "LOW_TURNOVER"], ensure_ascii=False),
     "龙头加速", "地产穿越龙4板一字", "独立行情，后排崩塌不参与", now),

    ("000802", "北京文化", "影视院线", 4, "缩量封板", "09:25", 19.0,
     "情绪驱动", "散点", "影视题材+游资抱团", 6.0,
     json.dumps(["EMOTION_DRIVEN", "SCATTERED", "FILM_THEME"], ensure_ascii=False),
     "散点涨停", "影视散点无板块效应", "散点不参与", now),

    # --- 通信主线 ---
    ("603236", "移远通信", "通信设备", 1, "一字封板", "09:25", 2.2,
     "产业链催化", "主线轮动", "算力网政策+通信模组龙头", 8.0,
     json.dumps(["INDUSTRY_CATALYST", "COMMS_LEADER", "ONE_WORD_SEAL", "COMPUTING_POWER"], ensure_ascii=False),
     "主线首板", "四川算力网政策+通信模组龙头一字", "通信龙头一字板质量最高", now),

    ("002396", "星网锐捷", "通信设备", 1, "烂板炸3次", "10:32", 14.7,
     "产业链催化", "主线扩散", "算力网政策+通信设备扩散", 6.0,
     json.dumps(["INDUSTRY_CATALYST", "BROKEN_3_TIMES", "POOR_SEAL", "COMPUTING_POWER"], ensure_ascii=False),
     "板块跟风", "通信扩散但封板质量差", "烂板不追", now),

    ("603118", "共进股份", "通信设备", 1, "烂板炸4次", "09:35", 13.9,
     "产业链催化", "主线扩散", "通信设备扩散+算力", 5.5,
     json.dumps(["INDUSTRY_CATALYST", "BROKEN_4_TIMES", "POOR_SEAL", "COMPUTING_POWER"], ensure_ascii=False),
     "板块跟风", "通信扩散炸板4次", "封板极差不追", now),

    # --- 地产/装修 ---
    ("603887", "城地香江", "IT服务", 3, "缩量封板", "09:25", 3.0,
     "题材驱动", "板块共振", "IDC算力+地产双概念", 7.0,
     json.dumps(["THEME_DRIVEN", "IDC_CONCEPT", "COMPUTING_POWER"], ensure_ascii=False),
     "中军加速", "算力+地产双概念3板", "3板缩量=资金共识", now),

    ("601886", "江河集团", "装修装饰", 2, "放量封板", "10:10", 4.0,
     "政策催化", "板块跟风", "地产链扩散+装修", 6.5,
     json.dumps(["POLICY_CATALYST", "DECORATION", "RE_CHAIN"], ensure_ascii=False),
     "次主线启动", "地产链装修扩散", "装修跟风地产", now),

    ("002081", "金螳螂", "装修装饰", 2, "缩量封板", "09:25", 1.0,
     "政策催化", "板块跟风", "装修龙头+地产链", 7.0,
     json.dumps(["POLICY_CATALYST", "DECORATION_LEADER", "RE_CHAIN", "LOW_PRICE"], ensure_ascii=False),
     "次主线加速", "装修龙头2板缩量", "装修中军，低价有安全边际", now),

    # --- 其他 ---
    ("600881", "亚泰集团", "综合", 3, "放量封板", "09:36", 8.9,
     "情绪驱动", "散点", "低价+重组预期+游资", 5.5,
     json.dumps(["EMOTION_DRIVEN", "SCATTERED", "LOW_PRICE", "RESTRUCTURE"], ensure_ascii=False),
     "散点涨停", "低价综合股3板，无明确板块", "散点低价不参与", now),

    ("300862", "蓝盾光电", "通用设备", 4, "20cm一字", "09:25", 6.9,
     "情绪驱动", "龙头抱团", "通用设备+游资抱团", 7.0,
     json.dumps(["EMOTION_DRIVEN", "LEADER_ACCEL", "20CM", "GENERAL_EQUIP"], ensure_ascii=False),
     "龙头加速", "20cm 4板=游资高度博弈", "高位20cm风险极大", now),

    ("000887", "中鼎股份", "汽车零部件", 2, "放量封板", "09:30", 5.9,
     "板块共振", "主线轮动", "汽车零部件+秦安跟风", 7.5,
     json.dumps(["SECTOR_RESONANCE", "AUTO_PARTS", "FOLLOWER"], ensure_ascii=False),
     "次主线加速", "汽车零部件跟风秦安股份", "主线跟风2板", now),

    ("000006", "深振业A", "房地产开发", 1, "放量封板", "13:27", 2.9,
     "政策催化", "板块跟风", "地产政策预期+深圳", 6.5,
     json.dumps(["POLICY_CATALYST", "REAL_ESTATE", "SHENZHEN"], ensure_ascii=False),
     "主线首板", "午后地产扩散", "地产后排跟风，板块割裂不追", now),
]

cur.executemany("""INSERT INTO limit_up_patterns
    (trade_date, code, name, industry, board, seal_status, first_limit_time, turnover,
     primary_attribution, secondary_attribution, core_driver, predictability_score,
     feature_tags, pattern_type, info_available_pre_open, strategy_value, created_at)
    VALUES ('20260813', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", limit_up_data)
print(f"[入库] limit_up_patterns 新增 {len(limit_up_data)} 条涨停范式")

conn.commit()

# === 验证 ===
cur.execute("SELECT COUNT(*) FROM limit_up_patterns WHERE trade_date='20260813'")
up_cnt = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM limit_down_risks WHERE trade_date='20260813'")
down_cnt = cur.fetchone()[0]
print(f"\n[验证] 20260813: 涨停范式={up_cnt}条, 跌停风险={down_cnt}条")

cur.execute("""SELECT primary_attribution, COUNT(*) as cnt FROM limit_up_patterns
    WHERE trade_date='20260813' GROUP BY primary_attribution ORDER BY cnt DESC""")
print("\n涨停归因分布:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}只")

cur.execute("""SELECT pattern_type, COUNT(*) as cnt FROM limit_up_patterns
    WHERE trade_date='20260813' GROUP BY pattern_type ORDER BY cnt DESC""")
print("\n涨停类型分布:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}只")

cur.execute("""SELECT code, name, risk_tags FROM limit_down_risks
    WHERE trade_date='20260813'""")
print("\n跌停风险库:")
for row in cur.fetchall():
    print(f"  {row[0]} {row[1]}: {row[2]}")

conn.close()
print("\n=== 归因库更新完成 ===")
