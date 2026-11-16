#!/usr/bin/env python3
"""2026-08-13 复盘关键教训录入 experience_library"""
import sqlite3, json
from datetime import datetime

DB = "D:/zcodesj/attribution/attribution.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 确认下一个序号
cur.execute("SELECT COUNT(*) FROM experience_library WHERE case_id LIKE 'TL-MKT-FAIL-%'")
fail_cnt = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM experience_library WHERE case_id LIKE 'TL-MKT-LU-%'")
lu_cnt = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM experience_library WHERE case_id LIKE 'TL-MKT-OPT-%'")
opt_cnt = cur.fetchone()[0]

entries = [
    # === 1. 失败场景：空间龙天地板退潮信号 ===
    {
        "library_type": "failure_scene",
        "case_id": f"TL-MKT-FAIL-{fail_cnt+1:03d}",
        "code": "003032",
        "name": "传智教育",
        "trade_link": "20260813 前7连板→当日跌停(天地板)",
        "pnl_summary": "前7板累计涨幅约+95%→8/13单日-10%跌停，高位追入者单日亏损-10%",
        "attribution_detail": "空间龙断板=情绪周期见顶信号。传智教育作为全市场最高板(7板)，8/13直接天地板跌停，同步百花医药(7板后)高位放量48%换手断板，秦安股份成为新最高板(5板)。空间高度从7板压缩至5板=退潮确认。",
        "controllability_score": 8.5,
        "core_facts": "1.传智教育前7板无持续基本面催化(教育题材纯炒作) 2.百花医药8/12已断板=预警信号 3.8/13高开低走全市场仅20%个股上涨 4.空间龙天地板历史规律：最高板从>=6降至<=5时，中位连板股次日溢价骤降",
        "lesson": "空间龙断板(尤其天地板)=全市场退潮的可靠领先指标。当最高连板从>=6板降至<=5板时，必须立即提升风控等级：禁止追高3板以上个股、减仓高位连板、只做低位首板。",
        "optimization_rule": json.dumps([
            {"rule": "新增风控规则BOARD_COLLAPSE_ALERT：当市场最高连板从前日>=6降至当日<=5时，自动触发风控升级", "landed": False},
            {"rule": "空间龙(最高板)跌停时，次日禁止开新仓追高连板股(3板以上)", "landed": False},
            {"rule": "pre_trade_check新增R13：若昨日最高板今日跌停，买入连板股直接拦截", "landed": False}
        ], ensure_ascii=False),
        "scenario_tags": "空间龙退潮|天地板|情绪周期见顶|传智教育|HIGH_BOARD_COLLAPSE|连板高度压缩",
        "verification_status": "pending",
    },

    # === 2. 失败场景：烂板追高风险 ===
    {
        "library_type": "failure_scene",
        "case_id": f"TL-MKT-FAIL-{fail_cnt+2:03d}",
        "code": "MULTI",
        "name": "共进股份/星网锐捷/皇氏集团(烂板群)",
        "trade_link": "20260813 多只烂板涨停股",
        "pnl_summary": "共进股份炸板4次、星网锐捷炸板3次、皇氏集团开板4次换手24%、风语筑28次封板未遂→追高者大概率次日低开被套",
        "attribution_detail": "炸板率37%环境中，封板质量极差的涨停股(炸板>=3次)次日溢价概率极低。今日36只炸板股+多只烂板回封=资金严重分歧，追入这些标的次日大概率亏损。",
        "controllability_score": 9.0,
        "core_facts": "1.今日炸板36只/炸板率36.7%(偏高) 2.共进股份/星网锐捷/风语筑等烂板回封 3.皇氏集团4板换手24%=极端分歧 4.历史规律：炸板>=3次的涨停股次日平均收益为负",
        "lesson": "封板质量是次日溢价的核心预测因子。炸板>=3次的涨停股不应追入，无论板块逻辑多强。封板质量差的本质是资金分歧=没有共识=次日大概率低开。",
        "optimization_rule": json.dumps([
            {"rule": "选股端新增封板质量过滤：炸板>=3次的涨停股排除出候选池", "landed": False},
            {"rule": "一字板/缩量秒板优先级>放量烂板，即使后者板块逻辑更强", "landed": False},
            {"rule": "换手率>20%的连板股(3板以上)标记为HIGH_RISK，触发卖出评估", "landed": False}
        ], ensure_ascii=False),
        "scenario_tags": "烂板|炸板率高|封板质量|资金分歧|POOR_SEAL|次日溢价低|追高风险",
        "verification_status": "pending",
    },

    # === 3. 涨停范式：医药政策催化三重共振 ===
    {
        "library_type": "profit_paradigm",
        "case_id": f"TL-MKT-LU-{lu_cnt+1:03d}",
        "code": "MULTI",
        "name": "医药主线(誉衡药业/博济医药/陇神戎发/万邦医药等13只涨停)",
        "trade_link": "20260813 医药板块13只涨停(含6只20cm)",
        "pnl_summary": "医药板块当日涨停密度最高：誉衡药业2板缩量、博济医药/万邦医药/陇神戎发/兰卫医学/药易购/南模生物6只20cm首板",
        "attribution_detail": "三重逻辑共振：1.第12批集采稳临床反内卷(8/13新闻周刊报道)=集采利空出尽 2.2026版基药目录9月1日施行(中成药扩容48款)=准入放量预期 3.7月医疗设备招采大幅回暖(CT+43%/MR+65%)=业绩修复。板块形成完整梯队：百花医药(7板高位锚定)+誉衡药业(2板中军)+博济医药等(20cm弹性)+佛慈制药/方盛制药(低位补涨)。",
        "controllability_score": 7.5,
        "core_facts": "1.集采政策边际转暖(反内卷+稳临床) 2.基药目录9月施行=中长期逻辑 3.招采数据回暖=业绩支撑 4.板块涨停13只含6只20cm=资金全面共识 5.梯队完整(龙头+中军+弹性+补涨)",
        "lesson": "政策催化三重共振(集采转暖+基药目录+招采回暖)是医药板块爆发的可靠范式。当多个独立政策/数据催化在同一时间窗口叠加+板块形成完整涨停梯队时，主线持续性高(>3天)。识别关键：催化消息可盘前获取+板块涨停密度>=10只+梯队完整。",
        "optimization_rule": json.dumps([
            {"rule": "新增涨停范式MEDICAL_POLICY_TRIPLE：集采政策+基药目录+招采数据三重催化共振", "landed": False},
            {"rule": "主线识别标准升级：板块涨停>=10只+梯队完整(龙头+中军+弹性+补涨)=高持续性主线", "landed": False},
            {"rule": "建立医药板块专属候选池：集采受益+基药目录新增+招采回暖三条线", "landed": False}
        ], ensure_ascii=False),
        "scenario_tags": "医药主线|政策催化|集采转暖|基药目录|招采回暖|三重共振|MEDICAL_MAINLINE|梯队完整",
        "verification_status": "pending",
    },

    # === 4. 涨停范式：电力季节性催化 ===
    {
        "library_type": "profit_paradigm",
        "case_id": f"TL-MKT-LU-{lu_cnt+2:03d}",
        "code": "601991",
        "name": "大唐发电(电力板块6只涨停)",
        "trade_link": "20260813 电力板块午后拉升",
        "pnl_summary": "大唐发电涨停封单3.77亿+华电能源/金开新能/惠天热电(2板)/望变电气(2板)等6只涨停",
        "attribution_detail": "南方电网负荷突破2.78亿千瓦年内第5次创新高+广东电网首破1.8亿千瓦+全国最高负荷15.51亿千瓦超历史极值。发改委预计今夏最高负荷达16亿千瓦(+9000万)。大唐发电作为火电龙头巨量封单=资金合力强，带动板块整体走强。长城证券判断火电龙头估值处近三年低位=安全边际。",
        "controllability_score": 7.0,
        "core_facts": "1.用电负荷连续创新高(可量化催化) 2.大唐发电封单3.77亿=权重号召力 3.火电估值近三年低位=安全边际 4.季节性窗口(迎峰度夏)但尾声临近",
        "lesson": "电力板块的 seasonal catal(迎峰度夏)是每年7-8月的可靠脉冲。权重龙头(大唐发电)巨量封单是板块启动的可靠信号。但需注意季节性窗口有限，8月下旬催化边际递减。参与方式：权重首板日买入跟风品种(华电能源等低价火电)，持有周期3-5天。",
        "optimization_rule": json.dumps([
            {"rule": "新增涨停范式POWER_PEAK_SEASON：用电负荷创新高+权重龙头封单>=3亿=板块启动信号", "landed": False},
            {"rule": "电力板块参与窗口：7月中旬-8月中旬，8月下旬催化递减需谨慎", "landed": False}
        ], ensure_ascii=False),
        "scenario_tags": "电力主线|季节性催化|用电负荷创新高|迎峰度夏|火电估值修复|大唐发电|POWER_PEAK",
        "verification_status": "pending",
    },

    # === 5. 优化日志：极端结构行情应对策略 ===
    {
        "library_type": "optimization_log",
        "case_id": f"TL-MKT-OPT-{opt_cnt+1:03d}",
        "code": None,
        "name": None,
        "trade_link": "20260813 全市场复盘优化",
        "pnl_summary": "当日指数全线跌0.5-1.1%但涨停62只，上涨家数仅20%=极端结构行情",
        "attribution_detail": "8/13市场呈现典型极端结构：指数放量下跌(2.55万亿+398亿)vs涨停62只。核心矛盾=权重拖累指数+资金集中攻击医药/电力少数板块。此环境下横盘突破策略的纯技术信号噪音增大(高开低走=假突破频发)，需提升板块共振权重、降低纯技术权重。",
        "controllability_score": 6.0,
        "core_facts": "1.指数全线跌+放量=弱势 2.涨停62>跌停4=情绪不差 3.上涨家数仅20%=赚钱效应极差 4.炸板率37%=追高容错率低 5.医药/电力涨停密度远超其他板块=资金集中",
        "lesson": "极端结构行情(指数跌+涨停多+上涨家数<25%)下，选股必须强制要求板块共振：个股必须属于当日涨停>=5只的板块方可入选。纯技术突破信号在此环境下假突破率高(高开低走)，应降低技术权重。最优策略=主线板块内低位回踩低吸，而非追高突破。",
        "optimization_rule": json.dumps([
            {"rule": "新增市场环境标签EXTREME_STRUCTURE：指数跌+涨停>=50+上涨家数<25%=极端结构行情", "landed": False},
            {"rule": "极端结构行情下选股权重调整：板块共振度权重+50%，纯技术突破权重-30%", "landed": False},
            {"rule": "极端结构行情下操作原则：只做主线(涨停>=10只板块)低位回踩，禁止追高突破", "landed": False},
            {"rule": "炸板率>30%时自动提升风控等级：打板需缩量一字，排除烂板", "landed": False}
        ], ensure_ascii=False),
        "scenario_tags": "极端结构行情|指数跌涨停多|板块共振|选股权重调整|EXTREME_STRUCTURE|高开低走|假突破",
        "verification_status": "pending",
    },
]

for e in entries:
    cur.execute("""INSERT INTO experience_library
        (library_type, case_id, code, name, trade_link, pnl_summary, attribution_detail,
         controllability_score, core_facts, lesson, optimization_rule, scenario_tags,
         verification_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (e["library_type"], e["case_id"], e["code"], e["name"], e["trade_link"],
         e["pnl_summary"], e["attribution_detail"], e["controllability_score"],
         e["core_facts"], e["lesson"], e["optimization_rule"], e["scenario_tags"],
         e["verification_status"], now))

conn.commit()

# === 验证 ===
cur.execute("SELECT COUNT(*) FROM experience_library")
total = cur.fetchone()[0]
print(f"[入库] experience_library 新增 {len(entries)} 条经验")
print(f"[总计] 经验库总量: {total} 条\n")

cur.execute("""SELECT id, library_type, case_id, substr(lesson, 1, 60) as lesson_short
    FROM experience_library ORDER BY id DESC LIMIT ?""", (len(entries),))
print("本次新增条目:")
for r in cur.fetchall():
    print(f"  [{r[0]}] {r[1]} | {r[2]}")
    print(f"    教训: {r[3]}...")

# 统计待落地的规则数
cur.execute("SELECT optimization_rule FROM experience_library ORDER BY id DESC LIMIT ?", (len(entries),))
total_rules = 0
for row in cur.fetchall():
    if row[0]:
        rules = json.loads(row[0])
        total_rules += sum(1 for r in rules if not r.get("landed", False))
print(f"\n[待落地规则] 共 {total_rules} 条优化规则待编码落地")

conn.close()
print("\n=== 经验库更新完成 ===")
