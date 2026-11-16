# -*- coding: utf-8 -*-
"""
涨停股范式分类入库脚本
日期: 2026-08-13
将当日涨停股按 pattern_type / main_theme 分类写入 attribution.db -> limit_up_patterns 表

去重规则: 同一 code 只保留最重要分类(主线 > 次主线 > 观察线 > 散点)
"""
import sqlite3
import os
from collections import Counter

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────
DB_PATH = r"D:\workbuddyspace\factor_engine\data\attribution.db"
TRADE_DATE = "20260813"

# ──────────────────────────────────────────────
# 建表 SQL
# ──────────────────────────────────────────────
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS limit_up_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    continuous_board INTEGER,
    first_board_time TEXT,
    seal_amount REAL,
    pattern_type TEXT NOT NULL,
    main_theme TEXT NOT NULL,
    theme_rank TEXT NOT NULL,
    industry TEXT,
    seal_strength TEXT,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""

CREATE_INDEX_1 = "CREATE INDEX IF NOT EXISTS idx_lup_date ON limit_up_patterns(trade_date);"
CREATE_INDEX_2 = "CREATE INDEX IF NOT EXISTS idx_lup_code ON limit_up_patterns(code, trade_date);"

# ──────────────────────────────────────────────
# 数据定义
# 每条: (code, name, continuous_board, pattern_type, main_theme, theme_rank, industry, seal_strength, note)
# first_board_time / seal_amount 暂留 NULL
# ──────────────────────────────────────────────
RAW_DATA = [
    # ── 光通信一级主线 (main_theme=光通信, theme_rank=一级主线) ──
    ("688167", "炬光科技", 1, "龙头加速", "光通信", "一级主线", "半导体", "强", "20cm一字"),
    ("688635", "长进光子", 1, "主线首板", "光通信", "一级主线", "通信设备", "强", "20cm"),
    ("600105", "永鼎股份", 1, "主线首板", "光通信", "一级主线", "通信设备", "中", "尾盘回封"),
    ("002792", "通宇通讯", 1, "主线首板", "光通信", "一级主线", "通信设备", "中", "27%换手"),
    ("002491", "通鼎互联", 1, "板块跟风", "光通信", "一级主线", "通信设备", "中", ""),

    # ── 半导体扩散 (main_theme=光通信, theme_rank=一级主线) ──
    ("603690", "至纯科技", 1, "板块跟风", "光通信", "一级主线", "半导体", "中", ""),
    ("600641", "先导基电", 1, "板块跟风", "光通信", "一级主线", "半导体", "中", ""),

    # ── 地产链次主线 (main_theme=地产链, theme_rank=次主线) ──
    ("600683", "京投发展", 3, "龙头加速", "地产链", "次主线", "房地产开发", "强", "一字"),
    ("002146", "荣盛发展", 1, "次主线启动", "地产链", "次主线", "房地产开发", "中", "低价1.27"),
    ("600162", "香江控股", 2, "次主线启动", "地产链", "次主线", "房地产开发", "中", ""),
    ("600604", "市北高新", 1, "次主线启动", "地产链", "次主线", "房屋租赁", "中", ""),
    ("600266", "城建发展", 1, "次主线启动", "地产链", "次主线", "房地产开发", "中", ""),
    ("000736", "中交发展", 1, "次主线启动", "地产链", "次主线", "房地产服务", "中", ""),
    ("002244", "滨江集团", 1, "次主线启动", "地产链", "次主线", "房地产开发", "中", ""),
    ("601155", "新城控股", 1, "次主线启动", "地产链", "次主线", "房地产开发", "中", ""),
    ("601886", "江江集团", 1, "次主线启动", "地产链", "次主线", "装修装饰", "弱", "多次炸板"),
    ("002047", "宝鹰股份", 2, "次主线启动", "地产链", "次主线", "装修装饰", "中", ""),
    ("002081", "金螳螂", 1, "次主线启动", "地产链", "次主线", "装修装饰", "中", ""),

    # ── 医药医疗次主线 (main_theme=医药医疗, theme_rank=次主线) ──
    ("600721", "百花医药", 7, "龙头加速", "医药医疗", "次主线", "医疗服务", "强", "11炸尾盘回封"),
    ("603538", "美诺华", 1, "次主线启动", "医药医疗", "次主线", "化学制药", "中", ""),
    ("002437", "誉衡药业", 1, "次主线启动", "医药医疗", "次主线", "化学制药", "中", ""),
    ("600216", "浙江医药", 1, "次主线启动", "医药医疗", "次主线", "化学制药", "中", ""),
    ("002589", "瑞康医药", 1, "板块跟风", "医药医疗", "次主线", "医药商业", "中", ""),
    ("002172", "澳洋健康", 1, "次主线启动", "医药医疗", "次主线", "医疗服务", "弱", ""),
    ("301602", "超研股份", 1, "次主线启动", "医药医疗", "次主线", "医疗器械", "强", "20cm"),

    # ── 设备制造 (main_theme=设备制造, theme_rank=防御轮动) ──
    ("603758", "秦安股份", 4, "龙头加速", "设备制造", "防御轮动", "汽车零部件", "强", "一字"),
    ("002248", "华东数控", 3, "龙头加速", "设备制造", "防御轮动", "通用设备", "中", "7炸"),
    ("605286", "同力天启", 3, "龙头加速", "设备制造", "防御轮动", "专用设备", "强", ""),
    ("300862", "蓝盾光电", 3, "龙头加速", "设备制造", "防御轮动", "通用设备", "强", "20cm一字"),
    ("002031", "巨轮智能", 2, "次主线启动", "设备制造", "防御轮动", "专用设备", "中", ""),
    ("000410", "沈阳机床", 1, "次主线启动", "设备制造", "防御轮动", "通用设备", "中", ""),
    ("002520", "日发精机", 1, "次主线启动", "设备制造", "防御轮动", "通用设备", "中", ""),
    ("000887", "中鼎股份", 1, "次主线启动", "设备制造", "防御轮动", "汽车零部件", "中", ""),
    ("002870", "香山股份", 1, "次主线启动", "设备制造", "防御轮动", "汽车零部件", "弱", "5炸"),
    ("001260", "坤泰股份", 1, "次主线启动", "设备制造", "防御轮动", "汽车零部件", "中", ""),
    ("001696", "宗申动力", 1, "次主线启动", "设备制造", "防御轮动", "通用设备", "中", ""),
    ("600397", "江钨装备", 1, "次主线启动", "设备制造", "防御轮动", "专用设备", "中", ""),
    ("002164", "宁波东力", 1, "次主线启动", "设备制造", "防御轮动", "通用设备", "弱", ""),

    # ── IT/软件 (main_theme=IT软件, theme_rank=次主线扩散) ──
    ("002229", "鸿博股份", 2, "次主线启动", "IT软件", "次主线扩散", "IT服务", "中", ""),
    ("603887", "城地香江", 2, "次主线启动", "IT软件", "次主线扩散", "IT服务", "中", ""),
    ("600602", "云赛智联", 1, "次主线启动", "IT软件", "次主线扩散", "软件开发", "强", "一字"),
    ("603232", "格尔软件", 1, "次主线启动", "IT软件", "次主线扩散", "软件开发", "中", ""),
    ("301315", "威士顿", 1, "次主线启动", "IT软件", "次主线扩散", "软件开发", "强", "20cm"),
    ("002421", "达实智能", 1, "次主线启动", "IT软件", "次主线扩散", "IT服务", "中", ""),
    ("002178", "延华智能", 1, "板块跟风", "IT软件", "次主线扩散", "软件开发", "中", ""),

    # ── 消费食品 (main_theme=消费食品, theme_rank=防御轮动) ──
    ("002329", "皇氏集团", 3, "龙头加速", "消费食品", "防御轮动", "饮料乳品", "中", "4炸"),
    ("605179", "一鸣食品", 3, "龙头加速", "消费食品", "防御轮动", "饮料乳品", "中", ""),
    ("002582", "好想你", 1, "独立逻辑", "消费食品", "防御轮动", "休闲食品", "中", "低估值+业绩扭亏"),
    ("603696", "安记食品", 1, "板块跟风", "消费食品", "防御轮动", "调味发酵", "中", ""),
    ("603369", "今世缘", 1, "板块跟风", "消费食品", "防御轮动", "白酒", "弱", "2炸"),
    ("603102", "百合股份", 1, "板块跟风", "消费食品", "防御轮动", "食品加工", "弱", "2炸"),

    # ── 电网设备 (main_theme=电网设备, theme_rank=观察线) ──
    ("603191", "望变电气", 1, "次主线启动", "电网设备", "观察线", "电网设备", "中", ""),
    ("600379", "宝光股份", 1, "次主线启动", "电网设备", "观察线", "电网设备", "中", ""),
    ("601700", "风范股份", 1, "次主线启动", "电网设备", "观察线", "电网设备", "中", ""),
    ("000720", "新能泰山", 1, "次主线启动", "电网设备", "观察线", "电网设备", "弱", ""),

    # ── 化工有色 (main_theme=化工有色, theme_rank=观察线) ──
    ("002379", "宏桥控股", 1, "独立逻辑", "化工有色", "观察线", "工业金属", "中", "锗镓产业链"),
    ("600593", "德龙汇能", 1, "散点涨停", "化工有色", "观察线", "燃气", "中", ""),
    ("002562", "兄弟科技", 1, "散点涨停", "化工有色", "观察线", "化学制品", "中", ""),
    ("603330", "天洋新材", 1, "散点涨停", "化工有色", "观察线", "塑料", "中", ""),
    ("000936", "华西股份", 1, "散点涨停", "化工有色", "观察线", "化学纤维", "中", ""),
    ("002005", "德豪润达", 1, "散点涨停", "化工有色", "观察线", "小家电", "中", ""),
    # 本立科技: 原列301315(与威士顿冲突),实际301605,化学原料,归独立逻辑
    ("301605", "本立科技", 1, "独立逻辑", "化工有色", "观察线", "化学原料", "中", "实为化学原料,从消费修正"),

    # ── 散点涨停 (main_theme=其他, theme_rank=散点) ──
    # 金螳螂已在地产链, 美诺华已在医药, 百合股份已在消费 — 均跳过
    ("000802", "北京文化", 3, "散点涨停", "其他", "散点", "影视院线", "中", ""),
    ("002483", "润邦股份", 1, "散点涨停", "其他", "散点", "专用设备", "强", "一字"),
    ("002682", "龙洲股份", 1, "散点涨停", "其他", "散点", "物流", "中", ""),
    ("000665", "湖北广电", 1, "散点涨停", "其他", "散点", "电视广播", "弱", ""),
    ("000048", "京基智农", 1, "散点涨停", "其他", "散点", "养殖业", "弱", "4炸"),
    ("603076", "乐惠国际", 1, "散点涨停", "其他", "散点", "专用设备", "弱", ""),
    ("002357", "富临运业", 1, "散点涨停", "其他", "散点", "铁路公路", "中", ""),
    ("605289", "罗曼股份", 1, "散点涨停", "其他", "散点", "专业工程", "中", ""),
    ("603052", "可川科技", 1, "散点涨停", "其他", "散点", "消费电子", "中", ""),
    ("603289", "泰瑞机器", 1, "散点涨停", "其他", "散点", "专用设备", "中", ""),
    ("603466", "风语筑", 1, "散点涨停", "其他", "散点", "数字媒体", "中", ""),
    ("600857", "宁波中百", 1, "散点涨停", "其他", "散点", "一般零售", "弱", "4炸"),
    ("003032", "传智教育", 1, "散点涨停", "其他", "散点", "教育", "弱", "1炸"),
    ("920856", "浩淼科技", 1, "散点涨停", "其他", "散点", "专用设备", "弱", "3炸"),
    ("000692", "惠天热电", 1, "散点涨停", "其他", "散点", "电力", "弱", "4炸"),
    ("600881", "亚泰集团", 2, "散点涨停", "其他", "散点", "综合", "中", ""),
    ("603269", "海鸥股份", 1, "散点涨停", "其他", "散点", "通用设备", "弱", ""),
    ("001313", "粤海饲料", 1, "散点涨停", "其他", "散点", "饲料", "中", ""),
    ("000715", "中兴商业", 1, "散点涨停", "其他", "散点", "一般零售", "中", ""),
    ("603615", "茶花股份", 1, "散点涨停", "其他", "散点", "家居用品", "中", ""),
]


def dedup_by_code(rows):
    """按 code 去重,保留首次出现(即列表中更靠前的=更重要分类)。"""
    seen = {}
    result = []
    for r in rows:
        code = r[0]
        if code in seen:
            continue
        seen[code] = True
        result.append(r)
    return result


def main():
    # 确保目录存在
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. 检查表是否存在,不存在则创建
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='limit_up_patterns'")
    if cur.fetchone() is None:
        print("[INFO] 表 limit_up_patterns 不存在,正在创建...")
        cur.execute(CREATE_SQL)
        cur.execute(CREATE_INDEX_1)
        cur.execute(CREATE_INDEX_2)
        conn.commit()
        print("[OK] 表及索引创建完成")
    else:
        print("[INFO] 表 limit_up_patterns 已存在")
        # 打印现有列
        cur.execute("PRAGMA table_info(limit_up_patterns)")
        cols = [row[1] for row in cur.fetchall()]
        print(f"      现有列: {cols}")

    # 2. 去重
    data = dedup_by_code(RAW_DATA)
    print(f"[INFO] 原始 {len(RAW_DATA)} 条,去重后 {len(data)} 条")

    # 3. 删除当日旧记录(幂等)
    cur.execute("DELETE FROM limit_up_patterns WHERE trade_date = ?", (TRADE_DATE,))
    deleted = cur.rowcount
    if deleted > 0:
        print(f"[INFO] 已删除 trade_date={TRADE_DATE} 的旧记录 {deleted} 条")
    else:
        print(f"[INFO] 无 trade_date={TRADE_DATE} 的旧记录")

    # 4. 批量插入
    insert_sql = """
        INSERT INTO limit_up_patterns
            (trade_date, code, name, continuous_board, first_board_time,
             seal_amount, pattern_type, main_theme, theme_rank,
             industry, seal_strength, note)
        VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?)
    """
    rows_to_insert = [
        (TRADE_DATE, code, name, cb, pattern_type, main_theme, theme_rank,
         industry, seal_strength, note)
        for (code, name, cb, pattern_type, main_theme, theme_rank,
             industry, seal_strength, note) in data
    ]
    cur.executemany(insert_sql, rows_to_insert)
    inserted = cur.rowcount
    conn.commit()
    print(f"[OK] 成功插入 {inserted} 条记录")

    # 5. 统计输出
    print("\n" + "=" * 60)
    print(f"  涨停范式分类统计  trade_date={TRADE_DATE}")
    print("=" * 60)

    # 总数
    cur.execute(
        "SELECT COUNT(*) FROM limit_up_patterns WHERE trade_date = ?",
        (TRADE_DATE,),
    )
    total = cur.fetchone()[0]
    print(f"\n总条数: {total}")

    # 按 pattern_type 分布
    print("\n── pattern_type 分布 ──")
    cur.execute("""
        SELECT pattern_type, COUNT(*) as cnt
        FROM limit_up_patterns
        WHERE trade_date = ?
        GROUP BY pattern_type
        ORDER BY cnt DESC
    """, (TRADE_DATE,))
    for pt, cnt in cur.fetchall():
        print(f"  {pt:<12s}  {cnt:>3d} 只")

    # 按 main_theme 分布
    print("\n── main_theme 分布 ──")
    cur.execute("""
        SELECT main_theme, theme_rank, COUNT(*) as cnt
        FROM limit_up_patterns
        WHERE trade_date = ?
        GROUP BY main_theme, theme_rank
        ORDER BY cnt DESC
    """, (TRADE_DATE,))
    for mt, tr, cnt in cur.fetchall():
        print(f"  {mt:<10s}  [{tr}]  {cnt:>3d} 只")

    # 按 seal_strength 分布
    print("\n── seal_strength 分布 ──")
    cur.execute("""
        SELECT seal_strength, COUNT(*) as cnt
        FROM limit_up_patterns
        WHERE trade_date = ?
        GROUP BY seal_strength
        ORDER BY cnt DESC
    """, (TRADE_DATE,))
    for ss, cnt in cur.fetchall():
        print(f"  {ss:<6s}  {cnt:>3d} 只")

    # 连板梯队
    print("\n── 连板梯队 ──")
    cur.execute("""
        SELECT continuous_board, COUNT(*) as cnt
        FROM limit_up_patterns
        WHERE trade_date = ?
        GROUP BY continuous_board
        ORDER BY continuous_board DESC
    """, (TRADE_DATE,))
    for cb, cnt in cur.fetchall():
        print(f"  {cb} 连板  {cnt:>3d} 只")

    print("\n" + "=" * 60)
    print(f"[完成] 数据已写入 {DB_PATH}")
    print("=" * 60)

    conn.close()


if __name__ == "__main__":
    main()
