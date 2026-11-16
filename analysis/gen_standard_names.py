# -*- coding: utf-8 -*-
"""gen_standard_names.py — 标准组名总表生成器(主人令20261102「名称不统一混乱」根治)。
单一出处=_common.SOURCE_CN_NAMES(71源注册名)+group_dictionary(人话定义);
输出 data/group_names_standard.json = 唯一标准名清单 + 近似家族区分表;
守卫: SOURCE_CN_NAMES 与字典键不一致处显式列出。"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
from _common import SOURCE_CN_NAMES

dic = json.load(open(r"D:\zcodesj\data\group_dictionary.json", encoding="utf-8"))
gd = dic["groups"]

# 唯一标准名 = SOURCE_CN_NAMES 的中文名(汇报唯一合法写法)
std = []
for src, cn in sorted(SOURCE_CN_NAMES.items()):
    std.append({"source": src, "standard_name": cn,
                "definition": gd.get(src, "(定义待补)")})

# 近似家族检测(标准名前8字相同的组归为一家, 汇报时须写全名+家族区分)
fam = {}
for s in std:
    key = s["standard_name"][:8]
    fam.setdefault(key, []).append(s["standard_name"])
families = {k: v for k, v in fam.items() if len(v) > 1}

# 语义家族(中文名不同但同源/易混的组, 人工标注——主人令「名称不统一混乱」根治)
SEMANTIC_FAMILIES = {
    "bp_ep价值家族": {
        "members": ["bp_ep核心底仓", "价值引擎(扩容批)", "bp_ep周滚动(扩容批)", "复活·bp_ep周滚动", "复活·深度价值"],
        "区分": "同宗的'便宜+质量'价值选股, 区别只在选股周期与代际: 核心底仓=季度核心仓; 价值引擎(扩容批)=当日扩容批; bp_ep周滚动(扩容批)=本周扩容批; 复活·bp_ep周滚动=老周批; 复活·深度价值=老深度批"},
    "突破家族": {
        "members": ["突破共振", "突破共振(扩容批)", "无共振突破对照", "盘前底部放量突破十条件复刻"],
        "区分": "都买'突破'形态: 突破共振=老批(创20日新高+行业共振); (扩容批)=今日扩容新票; 无共振突破对照=不看行业的对照实验组; 盘前十条件=9/2盘前十大条件复刻"},
    "蓄势家族": {
        "members": ["满弦蓄势", "满弦蓄势(扩容批)", "蓄势双优", "蓄势双优(扩容批)", "蓄势右侧确认"],
        "区分": "都是'横盘蓄势待突破': 满弦蓄势=老批; (扩容批)=新票; 蓄势双优=满弦池里量价双优子集; (扩容批)=新票; 蓄势右侧确认=向右突破确认后才买"},
    "探底低吸家族": {
        "members": ["复活·探底缩量企稳", "探底缩量企稳(扩容批)", "超跌探底样本臂", "组合C抄底复刻"],
        "区分": "都买'跌透了'的票: 复活·探底=10/28复活批(澳洋健康+50%所在); (扩容批)=今日扩容新票; 超跌探底样本臂=跌最深样本; 组合C抄底=深跌磨底+量能转暖"},
    "ML家族": {
        "members": ["ML胜率排名", "ML胜率排名复刻"],
        "区分": "ML胜率排名=老批(外部App名单); ML胜率排名复刻=系统自建复刻版(脱离App依赖)"},
    "量化排名第一家族": {
        "members": ["量化综合排名第一(原版·池内历史战绩裁决)", "量化综合排名第一(重建版)"],
        "区分": "原版=10/21建行用的池内历史战绩裁决(主人令已找回); 重建版=口径混淆的全市场扫描版(已搁置禁用)"},
    "银行价值易混组": {
        "members": ["手动确认批", "主人App手动补录"],
        "区分": "手动确认批=会话内主人令买入(建行+10%在此); 主人App手动补录=主人手机App直接下单(奥飞娱乐-10.65%在此)"},
}
families_out = {k: v["members"] + ["【区分】" + v["区分"]] for k, v in SEMANTIC_FAMILIES.items()}
families.update(families_out)

out = {"updated": "2026-11-02", "rule": "汇报唯一合法组名=standard_name, 禁止截断/缩写/换叫法; 同家族多组汇报时必须附家族区分说明",
       # 2026-11-03 修复: decree 字段在 11/2 扩容5新组重新生成时被生成器丢失(T2守卫抓出)——
       # 命名法令2026-01=永久生效(主人令 b0dcc5c7), 生成器必须携带, 不得靠手工补。
       "decree": {"decree_no": "命名法令2026-01", "effective": "永久",
                  "terms": "禁止截断缩写换叫法; 新增方法先定名后上岗"},
       "count": len(std), "names": std, "similar_families": families}
path = os.path.realpath(r"D:\zcodesj\data\group_names_standard.json")
json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("标准名清单: %d 组 -> data/group_names_standard.json" % len(std))
print()
print("近似家族(字符串前缀):")
for k, v in sorted(fam.items()):
    if len(v) > 1:
        print("  [%s] %s" % (k, " | ".join(v)))
print()
print("语义家族(中文名不同但同源, 汇报必附区分):")
for k, v in SEMANTIC_FAMILIES.items():
    print("  [%s] %s" % (k, " | ".join(v["members"])))
