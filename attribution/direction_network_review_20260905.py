# -*- coding: utf-8 -*-
"""
研究方向网络复盘（华泰AutoResearch借鉴项①，纯只读分析，零生产改动）
节点 = 2026-08-15 ~ 2026-09-05 全部已评估研究方向（真源= data/falsified_rules.json + data/wfa_preregistrations.jsonl + reports/PREREG_*/ *_RESULT 档案）
边   = 证伪档案 disposition 中明文记载的转移/互证/续批关系（不做臆测连线）
输出 = reports/DIRECTION_NETWORK_REVIEW_20260905.png + attribution/direction_network_data_20260905.json
"""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import FancyArrowPatch
from datetime import datetime
from collections import defaultdict

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

# (id, date, family, verdict, label)
NODES = [
    ("KRONOS_0815",   "2026-08-15", "ML/模型层",       "falsified",       "Kronos零样本#1"),
    ("KRONOS_0818",   "2026-08-18", "ML/模型层",       "falsified",       "Kronos零样本#2"),
    ("S4_ATR",        "2026-08-28", "止损/退出层",     "falsified",       "ATR全局止损"),
    ("S5_TIMESTOP",   "2026-08-28", "止损/退出层",     "scope_restricted", "时间止损"),
    ("HOTSPOT",       "2026-08-28", "动量/追热族",     "retired",         "hotspot退役"),
    ("M1_SECTORCAP",  "2026-08-29", "组合构造层",      "falsified",       "板块集中度cap"),
    ("SMC",           "2026-08-29", "动量/追热族",     "retired",         "smc退役(前视)"),
    ("P2B_SECTORMOM", "2026-08-29", "动量/追热族",     "falsified",       "板块动量双证伪"),
    ("BT_FAIL005",    "2026-08-29", "动量/追热族",     "falsified",       "BT-FAIL005追确认"),
    ("BPEP_TRENDUP",  "2026-08-31", "估值/价值层",     "prereg",          "bpep风格prereg"),
    ("QYOY3",         "2026-09-01", "估值/价值层",     "prereg",          "营收利润prereg×3"),
    ("CLASSICAL_PAT", "2026-09-02", "动量/追热族",     "falsified",       "经典形态全证伪"),
    ("ROUND_BOTTOM",  "2026-09-02", "动量/追热族",     "falsified",       "圆弧底=动量载体(正交)"),
    ("S3_TOPN",       "2026-09-02", "组合构造层",      "falsified",       "TopN15孤峰"),
    ("M3_TECHGRID",   "2026-09-02", "止损/退出层",     "pass",            "techgrid PASS"),
    ("DROE_ROA",      "2026-09-02", "估值/价值层",     "observation",     "droe/roa观察"),
    ("ROUND12_ML",    "2026-09-03", "ML/模型层",       "falsified",       "round12复活FAIL"),
    ("M3_EQUITY_AB",  "2026-09-03", "止损/退出层",     "pass",            "净值级A/B PASS"),
    ("E1_NTOP",       "2026-09-03", "组合构造层",      "noise_eq",        "n值三臂噪声等价"),
    ("B4_TILT",       "2026-09-04", "估值/价值层",     "falsified",       "tilt池内零增量"),
    ("F3_AMEAN",      "2026-09-04", "估值/价值层",     "pass_candidate",  "F3均额IC双PASS"),
    ("B6_TOP20",      "2026-09-04", "估值/价值层",     "noise_eq",        "B6噪声等价"),
    ("RANKLOSS_S1",   "2026-09-04", "ML/模型层",       "observation",     "排序损失s1"),
    ("RANKLOSS_S2",   "2026-09-04", "ML/模型层",       "pass_candidate",  "排序损失s2"),
    ("REGIME_ADAPT",  "2026-09-04", "ML/模型层",       "falsified",       "regime训练FAIL"),
    ("KRONOS_0904",   "2026-09-04", "ML/模型层",       "falsified",       "Kronos#3"),
    ("MAINLINE_S1",   "2026-09-04", "动量/追热族",     "weak",            "涨幅榜主线WEAK"),
    ("MAINLINE_S2",   "2026-09-04", "动量/追热族",     "falsified",       "涨停主线证伪"),
    ("S2B_LOWPOS",    "2026-09-04", "动量/追热族",     "falsified",       "低位补涨证伪"),
    ("SKILLSWEEP1",   "2026-09-04", "外部skill族",     "falsified",       "skill批1 4/5包证伪"),
    ("MACD_FLOW",     "2026-09-04", "动量/追热族",     "falsified",       "macd_flow动量载体"),
    ("DIP_STAGE2",    "2026-09-04", "超卖/反转族",     "falsified",       "dip动量暴露"),
    ("STYLE_GATE_S4", "2026-09-04", "风格/不追高闸",   "weak",            "三闸gate FAIL"),
    ("M3_REVIVAL",    "2026-09-04", "止损/退出层",     "adopted_owner",   "M-3复活(owner)"),
    ("SKILLSWEEP2",   "2026-09-05", "外部skill族",     "falsified",       "skill批2零真超额"),
    ("PANIC_TIMING",  "2026-09-05", "超卖/反转族",     "falsified",       "恐慌日双FAIL"),
    ("S3_REOPEN",     "2026-09-05", "ML/模型层",       "falsified",       "S3重开重测FAIL"),
    ("CHIP_3GATE",    "2026-09-05", "筹码族",          "falsified",       "筹码池内0/48格"),
    ("MOM_GATE_FH",   "2026-09-05", "风格/不追高闸",   "observation",     "不追高闸8/10年正"),
    ("STYLE_GATE_S5", "2026-09-05", "风格/不追高闸",   "adopted_owner",   "升SOP(owner)"),
    ("RANKLOSS_W1",   "2026-09-05", "ML/模型层",       "falsified",       "WIRE-1交易层FAIL"),
    ("EXT_CHIP",      "2026-09-05", "外部材料源",      "source",          "华泰筹码材料"),
    ("PORTRAIT",      "2026-09-05", "owner新线",       "active",          "Portrait影子15%"),
    ("TS_CURRICULUM", "2026-09-05", "owner新线",       "knowledge",       "时序26臂"),
]

# (from, to, label)  仅收录证伪/预注册档案中明文记载的关系
EDGES = [
    ("KRONOS_0815",  "KRONOS_0818",  "同线复测"),
    ("KRONOS_0818",  "KRONOS_0904",  "同线复测"),
    ("HOTSPOT",      "MAINLINE_S2",  "追热族互相印证"),
    ("ROUND_BOTTOM", "MACD_FLOW",    "动量载体同族"),
    ("BT_FAIL005",   "MACD_FLOW",    "追确认同族"),
    ("MAINLINE_S1",  "MAINLINE_S2",  "stage1→2"),
    ("MAINLINE_S2",  "S2B_LOWPOS",   "唯一活口→组合终证"),
    ("SKILLSWEEP1",  "DIP_STAGE2",   "批1活口续批(owner)"),
    ("DIP_STAGE2",   "SKILLSWEEP2",  "活口清零→全量补测"),
    ("SKILLSWEEP2",  "PANIC_TIMING", "超卖族=时机载体→立项(owner)"),
    ("RANKLOSS_S1",  "RANKLOSS_S2",  "stage1→2"),
    ("RANKLOSS_S2",  "RANKLOSS_W1",  "接线评估(owner裁决A)"),
    ("M3_TECHGRID",  "M3_EQUITY_AB", "techgrid→净值级"),
    ("M3_EQUITY_AB", "M3_REVIVAL",   "三重门禁→复活"),
    ("ROUND12_ML",   "M3_REVIVAL",   "owner覆盖(FAIL原文保留)"),
    ("ROUND12_ML",   "S3_REOPEN",    "L-1条款:旧腿虚高假说重测"),
    ("REGIME_ADAPT", "S3_REOPEN",    "regime层互证"),
    ("STYLE_GATE_S4","STYLE_GATE_S5","同线功效增强"),
    ("STYLE_GATE_S5","MOM_GATE_FH",  "主人纠偏→十年补测"),
    ("B4_TILT",      "B6_TOP20",     "池内增量问题延伸"),
    ("EXT_CHIP",     "CHIP_3GATE",   "外部材料触发prereg"),
]

FAMILIES = [
    "估值/价值层", "风格/不追高闸", "止损/退出层", "组合构造层",
    "ML/模型层", "超卖/反转族", "动量/追热族", "外部skill族",
    "筹码族", "owner新线", "外部材料源",
]
FAMILY_Y = {f: len(FAMILIES) - 1 - i for i, f in enumerate(FAMILIES)}

VCOLOR = {
    "falsified": "#d9534f", "retired": "#9e9e9e", "scope_restricted": "#f0ad4e",
    "weak": "#f0ad4e", "observation": "#f0ad4e", "noise_eq": "#f0ad4e",
    "pass": "#5bc0de", "pass_candidate": "#5bc0de",
    "adopted_owner": "#2e9e4f", "active": "#2e9e4f",
    "prereg": "#8e6fbf", "knowledge": "#3b5163", "source": "#ffffff",
}
VLABEL = {
    "falsified": "证伪", "retired": "退役", "scope_restricted": "限scope", "weak": "弱信号",
    "observation": "观察", "noise_eq": "噪声等价", "pass": "PASS", "pass_candidate": "PASS_CAND",
    "adopted_owner": "owner采纳", "active": "在役(owner开启)", "prereg": "已prereg待测",
    "knowledge": "知识沉淀", "source": "外部材料源",
}

node_map = {n[0]: n for n in NODES}
d = lambda s: mdates.date2num(datetime.strptime(s, "%Y-%m-%d"))

# 同(日,族)碰撞组：组内 x 均匀偏移 + 标签上下交错
groups = defaultdict(list)
for n in NODES:
    groups[(n[1], n[2])].append(n)
POS = {}
LABOFF = {}
TIER = [13, -16, 28, -30]  # 按族全局四档交错, 同档邻间距>=2天
for fam in FAMILIES:
    fam_ns = sorted([n for n in NODES if n[2] == fam], key=lambda t: t[1])
    for gi, n in enumerate(fam_ns):
        same = [m for m in fam_ns if m[1] == n[1]]
        j = same.index(n)
        k = len(same)
        off = (j - (k - 1) / 2) * 0.78  # 同日碰撞组内x偏移(天)
        POS[n[0]] = (d(n[1]) + off, FAMILY_Y[fam])
        LABOFF[n[0]] = TIER[gi % 4]

fig = plt.figure(figsize=(20, 10.2), facecolor="white")
gs = fig.add_gridspec(1, 2, width_ratios=[2.95, 1], wspace=0.05)
ax = fig.add_subplot(gs[0, 0])
ax2 = fig.add_subplot(gs[0, 1])
fig.subplots_adjust(left=0.055, right=0.985, top=0.93, bottom=0.085)

x_min, x_max = d("2026-08-13"), d("2026-09-07")
for f, y in FAMILY_Y.items():
    ax.axhspan(y - 0.44, y + 0.44, color="#f5f6f8" if y % 2 else "#ffffff", zorder=0)
    ax.text(x_min - 0.35, y, f, ha="right", va="center", fontsize=11,
            fontweight="bold" if f in ("估值/价值层", "风格/不追高闸", "止损/退出层") else "normal",
            color="#333333")

for a, b, lab in EDGES:
    (xa, ya), (xb, yb) = POS[a], POS[b]
    arr = FancyArrowPatch((xa, ya), (xb, yb), connectionstyle="arc3,rad=0.16",
                          arrowstyle="-|>", mutation_scale=11, color="#7a8ba0",
                          lw=1.1, alpha=0.8, zorder=2)
    ax.add_patch(arr)
    ax.text((xa + xb) / 2, (ya + yb) / 2 + 0.14, lab, fontsize=6.2,
            color="#5a6c80", ha="center", zorder=2,
            bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.7))

for fid, dt, fam, vd, lab in NODES:
    x, y = POS[fid]
    face = VCOLOR[vd]
    edge = "#555555" if vd == "source" else "none"
    ax.scatter([x], [y], s=300 if vd != "source" else 240, c=face,
               edgecolors=edge, linewidths=1.2, zorder=3)
    up = LABOFF[fid] > 0
    ax.annotate(lab, (x, y), xytext=(0, LABOFF[fid]), textcoords="offset points",
                ha="center", va="bottom" if up else "top",
                fontsize=6.7, color="#1a1a1a", zorder=4,
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.65))
    if vd not in ("falsified", "retired"):  # 多数派证伪/退役由颜色表达, 副标只标少数派
        ax.annotate(VLABEL[vd], (x, y), xytext=(0, LABOFF[fid] + (12 if up else -12)),
                    textcoords="offset points", ha="center", va="bottom" if up else "top",
                    fontsize=6.0, color="#777777", zorder=4)

ax.set_xlim(x_min - 1.9, x_max + 0.3)
ax.set_ylim(-0.75, len(FAMILIES) - 0.25)
ax.xaxis_date()
ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
ax.tick_params(axis="x", labelsize=9.5)
ax.set_yticks([])
for s in ("top", "right", "left"):
    ax.spines[s].set_visible(False)
ax.set_title("屠龙研究方向网络复盘（华泰借鉴项①）· 2026-08-15 ~ 09-05 · 节点=评估方向 边=档案明文记载的转移/互证",
             fontsize=13.5, fontweight="bold", pad=12, loc="left")

# ---- 右栏: 族×判决堆积 ----
fam_nodes = {f: [n for n in NODES if n[2] == f] for f in FAMILIES}
order = [f for f in FAMILIES if fam_nodes[f]]
bar_groups = [
    ("证伪/退役", {"falsified", "retired"}, "#d9534f"),
    ("观察/弱/噪声/限scope", {"scope_restricted", "weak", "observation", "noise_eq"}, "#f0ad4e"),
    ("PASS/候选", {"pass", "pass_candidate"}, "#5bc0de"),
    ("采纳/在役", {"adopted_owner", "active"}, "#2e9e4f"),
    ("待测/沉淀/源", {"prereg", "knowledge", "source"}, "#8e6fbf"),
]
ys = list(range(len(order)))
left = [0] * len(order)
for gname, vset, color in bar_groups:
    vals = [sum(1 for n in fam_nodes[f] if n[3] in vset) for f in order]
    ax2.barh(ys, vals, left=left, color=color, label=gname, height=0.6)
    for y, v, l in zip(ys, vals, left):
        if v:
            ax2.text(l + v / 2, y, str(v), ha="center", va="center",
                     fontsize=8.5, color="white", fontweight="bold")
    left = [l + v for l, v in zip(left, vals)]
ax2.set_yticks(ys)
ax2.set_yticklabels(order, fontsize=10)
ax2.invert_yaxis()
ax2.set_xlabel("方向数", fontsize=9.5)
ax2.legend(fontsize=8.2, loc="lower right", framealpha=0.92)
ax2.set_title("族×判决分布", fontsize=12, fontweight="bold")
for s in ("top", "right"):
    ax2.spines[s].set_visible(False)

n_total = len(NODES)
n_fals = sum(1 for n in NODES if n[3] in ("falsified", "retired"))
anchors = (
    "五个收敛锚（多方向判决归并到同一机理）:  "
    "①动量载体/追确认负选择×5（圆弧底·macd_flow·dip·BT-FAIL005·追涨族）   "
    "②全截面α≠池内α×3（换手tilt·筹码3GATE·B6）   "
    "③IC赢交易层输×3（hotspot·v23·排序损失W1）\n"
    "④同日两端负选择/价值层正区×3（追涨·超卖·恐慌日→bp_ep中间层）   "
    "⑤样本内虚高/验证集衰减×3（round11·S3重开·华泰研报自身亦自证）\n"
    f"共{n_total}方向: 证伪/退役{n_fals} · 观察/弱/噪声/限scope 8 · PASS/候选4 · owner采纳2+在役新线1 · 待测prereg 2条(覆盖4文件)"
    "  —— 华泰「围绕少数核心问题收敛」标准下形态成立, 收敛对象=证伪机理而非修改方向"
)
fig.text(0.055, 0.012, anchors, fontsize=8.6, va="bottom", ha="left", color="#2c3e50",
         bbox=dict(boxstyle="round,pad=0.45", fc="#f4f7fa", ec="#b8c8d8"))
fig.text(0.985, 0.018,
         "数据真源: data/falsified_rules.json + data/wfa_preregistrations.jsonl + reports/PREREG_*/ *_RESULT | 只读分析零生产改动 | 2026-09-05",
         ha="right", fontsize=7.5, color="#888888")

out_png = "reports/DIRECTION_NETWORK_REVIEW_20260905.png"
plt.savefig(out_png, dpi=150, facecolor="white")
plt.close()

out_json = {
    "generated": "2026-09-05",
    "purpose": "华泰AutoResearch借鉴项①方向网络复盘(只读)",
    "nodes": [
        {"id": i, "date": dt, "family": f, "verdict": v, "label": lab}
        for i, dt, f, v, lab in NODES
    ],
    "edges": [
        {"from": a, "to": b, "label": lab} for a, b, lab in EDGES
    ],
    "convergence_anchors": {
        "动量载体/追确认负选择": ["ROUND_BOTTOM", "MACD_FLOW", "DIP_STAGE2", "BT_FAIL005", "SKILLSWEEP2追涨族"],
        "全截面α≠池内α": ["B4_TILT", "CHIP_3GATE", "B6_TOP20"],
        "IC赢交易层输": ["hotspot参数预筛", "v23冒烟", "RANKLOSS_W1"],
        "同日两端负选择/中间价值层正区": ["SKILLSWEEP2追涨", "SKILLSWEEP2超卖", "PANIC_TIMING"],
        "样本内虚高/验证集衰减": ["ROUND11(ml)", "S3_REOPEN", "华泰研报自证(外部旁证)"],
    },
    "metrics": {
        "total_directions": n_total,
        "falsified_or_retired": n_fals,
        "adopted_owner": 2,
        "active_owner_line": 1,
        "observation_weak_noise": 8,
        "pass_or_candidate": 4,
        "prereg_entries": 2,
        "documented_edges": len(EDGES),
    },
}
with open("attribution/direction_network_data_20260905.json", "w", encoding="utf-8") as f:
    json.dump(out_json, f, ensure_ascii=False, indent=1)

print("PNG:", out_png)
print("JSON: attribution/direction_network_data_20260905.json")
print("nodes:", n_total, "edges:", len(EDGES), "falsified/retired:", n_fals)
