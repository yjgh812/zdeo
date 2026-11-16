# -*- coding: utf-8 -*-
"""
形态字典 v1 构建脚本 20261017（「现在干」令，形态字典立项的可执行前置件）
从 analysis/booklist_study_20261017/B_local.md 脚本直出钟杨《量价狙击》50 形态名（禁手抄），
逐形态附加语义反转预筛标注（依据=falsified_rules WYCKOFF-VOLUME-SEMANTIC-REVERSED amendments:
放量=分歧兑现(负)/缩量=惜售(仅低位正)/出清须收回确认/突破族判死六源），并声明机械化前置=纸书正文定义。
输出 data/volume_pattern_dictionary_v1.json；断言 50/50 名称全量无手抄。
"""
import json
import re
import sqlite3  # noqa: F401 (占位保持依赖显式)

SRC = r"D:\zcodesj\analysis\booklist_study_20261017\B_local.md"
OUT = r"D:\zcodesj\data\volume_pattern_dictionary_v1.json"

text = open(SRC, encoding="utf-8").read()
# 仅取《量价狙击》第6/7/8章区间内的形态行（预涨20/预跌20/黑马10），避免他书章节行混入
seg_start = text.index("第6章 预涨型量价形态实盘分析")
seg_end = text.index("附录（纸书")
seg = text[seg_start:seg_end]
pat = re.compile(r"^\s*- (\d)\.(\d{1,2}) (.+?)$", re.M)
forms = []
for m in pat.finditer(seg):
    ch, num, name = int(m.group(1)), int(m.group(2)), m.group(3).strip()
    if ch in (6, 7, 8):
        forms.append({"chapter": ch, "num": num, "name": name})
assert len(forms) == 50, f"形态名提取数={len(forms)} 非预期50"
assert sum(1 for f in forms if f["chapter"] == 6) == 20
assert sum(1 for f in forms if f["chapter"] == 7) == 20
assert sum(1 for f in forms if f["chapter"] == 8) == 10

# 语义反转预筛（方向预判词, 非结论; 依据=量价语义反转假说六域+突破族判死+VR-T1/T2/E3/E1）
PRESCREEN = {
    "冲高回落急速缩量": ("位置条件化", "冲高后回落+缩量=惜售/无人问津双义, 低位偏正高位偏负"),
    "低点盘中宽振不放量": ("中性偏正", "低位宽振缩量≈弹簧/洗盘语义族"),
    "振荡区间放缩量组合": ("位置条件化", "组合形态须拆解量能方向"),
    "进二退一式放缩量组合": ("位置条件化", "同上"),
    "“空中加油”倍增式放量": ("偏负预警", "高位放量=分歧兑现(E4高位爆量med20-3.39), 仅景气主导例外(新易盛型)"),
    "振荡区上方带量整理": ("位置条件化", "带量整理=分歧, 突破前积蓄与派发双义"),
    "水平区阻力点温和突破": ("偏负预警", "突破族判死六源(温和量亦无增量, E10四档全负)"),
    "头肩底右肩缩量整理": ("偏正候选", "低位缩量族(形态学前提须验证)"),
    "旗形整理的放缩量组合": ("位置条件化", "组合形态"),
    "振荡区多点支撑型量能": ("中性", "支撑结构叙事, 量能语义中性"),
    "放量回落强势整理": ("位置条件化", "放量回落=出清/出货双义, 低位偏正(E1跌格低位量增较优)"),
    "回踩阻力位缩量整理": ("偏正候选", "缩量回踩=惜售(低位/强趋势域, Z2s同族)"),
    "“仙人指路”放量线": ("偏负预警", "放量长上影≈hi_stall/utad族(威科夫卖侧两臂稳定负)"),
    "“红三兵”放量上涨": ("偏负预警", "连续放量上涨=分歧累积(bc_chase族med20-3.59)"),
    "“出水芙蓉”放量穿越线": ("偏负预警", "放量突破族判死(9/11九口径+E10+VR-T3)"),
    "递增式放量加速下跌": ("偏负预警", "放量下跌中位负(E1跌|量增|高-3.08全场最毒格)"),
    "堆量中跌回启动点": ("位置条件化", "堆量+回踩启动点=吸筹叙事, 须收回确认(sc_deep_loud同构)"),
    "放量突破筹码单峰区": ("偏负预警", "放量突破族判死(钟杨书内突破族正向语义与系统六源冲突最直接)"),
    "缩量回踩筹码密集区": ("偏正候选", "缩量回踩+支撑=惜售族(回踩带裁决许可域)"),
    "缩量振荡筹码快移动": ("中性", "筹码域语义, 日线不可测(筹码峰数据不可得声明)"),
    "递增式放量上涨": ("偏负预警", "递增放量上涨=分歧累积(与6.14同族, 7章预跌位与本系统证据一致)"),
    "递减式放量回调": ("位置条件化", "回调放量衰减=出清衰竭可能(低位偏正)"),
    "长影线巨量宽振": ("偏负预警", "巨量宽振长影=巨量长影族(E3高位巨量长阴med20-7.08方向)"),
    "阻力位脉冲式放量突破": ("偏负预警", "脉冲放量突破=分歧兑现(VR-T3脉冲档最负)"),
    "加速上涨的脉冲式放量": ("偏负预警", "同上, 加速段更毒(bc_chase 11/11全负)"),
    "上涨中的双阳放量": ("偏负预警", "连阳放量=分歧累积"),
    "振荡中的凹式量能": ("中性", "量能结构叙事"),
    "连续巨量式整理缓升": ("偏负预警", "巨量整理=高位分歧(VR-T1高位天量fwd20-3.20)"),
    "头肩顶右肩缩量整理": ("偏负预警", "顶部缩量反弹=无需求反弹(nd族-0.98同向)"),
    "放量长阴跌回启动点": ("偏负预警", "放量长阴=E3高位巨量长阴(-7.08); 跌回启动点含位置条件"),
    "“高台跳水”放量破位线": ("强负预警", "高位放量下跌=全场最毒组合(E1跌|量增|高med60-5.68/E3-7.08)"),
    "巨量推升中的低开小K线": ("偏负预警", "巨量推升后低开=T+1锁定盘抛压兑现(E8隔夜-0.35同向)"),
    "放量振荡中的缩量回升": ("位置条件化", "缩量回升=惜售(低位偏正)"),
    "水平区支撑位平量破位": ("偏负预警", "破位+量能无异=真跌破族(real_breakdown-1.40)"),
    "缩量下滑的圆弧形": ("偏负预警", "阴跌缩量=无人接盘(lo_dryup/阴跌缩量族, 非底部触发器)"),
    "缩量中的影线区": ("中性", "影线结构叙事"),
    "相对弱势的缩量运行": ("偏负预警", "弱+缩=无人问津(语义反转核心域)"),
    "高点放量筹码单峰": ("强负预警", "高位天量=VR-T1(fwd20-3.20/fwd60-4.91)+E4同向"),
    "盘整密集峰快速上移": ("中性", "筹码域日线不可测"),
    "回探筹码峰缩量滞涨": ("偏负预警", "缩量滞涨≈nd无需求族(med20-0.98, 9负2正)"),
    "振荡区间的放量收窄": ("中性", "黑马型叙事, 量能结构中性"),
    "堆积式突破低位缩量区": ("位置条件化", "突破族判死为事件级; 低位堆积=吸筹叙事须收回确认"),
    "快涨快跌的量能断层": ("偏负预警", "量能断层=分歧极值(ebb退潮先行族)"),
    "等高点后量小前量": ("偏正候选", "分时域A3实测后量小前量反优于接力(-0.44vs-0.72), 日线待验"),
    "强于大市的缩量整理": ("偏正候选", "RS+缩量整理=惜售族(威科夫九测试第7条+Z2s同族)"),
    "逆市温和放量上涨": ("偏正候选", "逆市独立放量=真实买盘介入(主线独苗族, 与大盘环境解耦)"),
    "箱体上沿的大幅缩量": ("偏正候选", "箱沿缩量=惜售(sos_range前提族方向但事件级弱)"),
    "涨停突破点放量振荡": ("位置条件化", "涨停日放量振荡≈炸板域(E2放量板负/缩量板正)"),
    "反复涨停的放量振荡": ("偏负预警", "连板放量=中板放量王道教条已证伪(-7.34)"),
    "缩量上穿筹码密集区": ("偏正候选", "缩量上穿=惜售穿越(供给枯竭的真语义, 低位域)"),
}

rows = []
import re as _re
PRESCREEN_NORM = {_re.sub(r'["“”]', "", k): v for k, v in PRESCREEN.items()}
for f in forms:
    name = f["name"]
    verdict = PRESCREEN_NORM.get(_re.sub(r'["“”]', "", name))
    assert verdict is not None, f"缺预筛标注: {name}"
    rows.append({
        "id": f"ZY-{f['chapter']}{f['num']:02d}",
        "source": "量价狙击(钟杨,清华2025)",
        "type": {6: "预涨型", 7: "预跌型", 8: "黑马型"}[f["chapter"]],
        "name": name,
        "mechanization_status": "blocked_需纸书正文定义",
        "semantic_reversal_prescreen": {"direction": verdict[0], "basis": verdict[1]},
        "prereg_ready": False,
    })

# 占位域（纸书/数据到位前不可机械化）
placeholders = [
    {"domain": "同花顺盘口挂单形态", "count_est": 47, "families": "上压盘8/下托盘8/夹板7/顶板7/成交单8/细节9",
     "status": "blocked_需Level2逐笔委托数据+纸书定义", "source": "同花顺盘口技法实战精要(王丽/鲁斌2025)"},
    {"domain": "炸板量能三维(位置×量能×形态)", "count_est": None, "families": "炸板位置/量能/后走势研判",
     "status": "blocked_需纸书定义(公开层仅'持有1-3天'一条)", "source": "龙头战法3(龙祺天2025)"},
    {"domain": "天量位置十态图谱", "count_est": 11, "families": "ch1 No.001-011(含天量突破失效/高位天量见顶已知)",
     "status": "blocked_需纸书定义", "source": "图解看盘100种图谱(刘文杰2026)"},
    {"domain": "利弗莫尔资金管理规则", "count_est": 8, "families": "利润一半落袋/金字塔加仓/绝不摊平等",
     "status": "observe_规则清单已提取(选载一手引文)", "source": "股票大作手操盘术(1940)"},
    {"domain": "缠论概念定义链", "count_est": 8, "families": "分型/笔/线段/中枢/走势/背驰/三类买卖点",
     "status": "frozen_三买判死/一二买存活, 缠论过滤族判死", "source": "缠论精解(鲍迪克)+108课骨架"},
]

out = {
    "name": "volume_pattern_dictionary",
    "version": "v1_20261017",
    "provenance": "形态名=analysis/booklist_study_20261017/B_local.md 脚本直出(50/50断言, 禁手抄); 预筛标注=量价语义反转假说(WYCKOFF-VOLUME-SEMANTIC-REVERSED amendments 2026-10-17)人工逐条附注, 方向预判非结论",
    "mechanization_gate": "任一形态升级 prereg_ready 的前置=①纸书/正版电子正文定义页(形态特征+市场含义+案例) ②主人点名立项 ③按语义反转预筛优先序逐形态机械化(qfq+next-bar+PIT+可成交约束)",
    "prescreen_legend": "偏正候选=低位缩量/惜售/承接族优先定义; 偏负预警=放量族/突破族预期负(验证目的=确证负向而非翻案); 强负预警=高位放量类; 位置条件化=双义须分层; 中性=叙事级",
    "forms_50": rows,
    "placeholder_domains": placeholders,
}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 读回核验
back = json.load(open(OUT, encoding="utf-8"))
assert len(back["forms_50"]) == 50
names_back = [r["name"] for r in back["forms_50"]]
assert len(set(names_back)) == 50
from collections import Counter
cnt = Counter(r["type"] for r in back["forms_50"])
print("字典v1 落盘:", OUT)
print("形态数:", len(back["forms_50"]), "| 分布:", dict(cnt))
dir_cnt = Counter(r["semantic_reversal_prescreen"]["direction"] for r in back["forms_50"])
print("预筛分布:", dict(dir_cnt))
print("占位域:", len(back["placeholder_domains"]))
