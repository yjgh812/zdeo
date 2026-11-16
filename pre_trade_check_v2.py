#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""买入前硬约束校验器 v2.1（屠龙标准 v1.0 六道闸门 R1-R18 全量物化 + R19解禁回避，fail-closed）。

2026-09-10 闸门退役记录: R1/R14-R18 结构性空转六闸下线(executor 无对应参数输入、
trade_state 市场字段无生产写入者、全史 REJECT=0 虚假防线面)——判定逻辑块已退役
(见主函数内退役标记注释), CLI 参数保留供研究脚本兼容; 追高拦截由 R-S5/R31/R32/R7
承担, 崩盘/极端结构由 R25 承担。档案=SOP 2026-09-10(三)条。

2026-08-15 v2.1: 新增 R19 解禁回避——买入检查时若该股未来20个自然日内有解禁事件且
  解禁市值占流通市值>3% → BLOCK（数据源 event_calendar.db，event_data_tools.py --refresh 维护；
  解禁表不存在或该股无数据时 PASS 并显式注明"无解禁数据"，fail-open显式标注）。
  注：旧R19锤子线/R20 V型反转已于8/14移出到技术分析模块，R19编号复用。

背景：2026-08-13 早盘 50 笔订单绕过闸门裸奔，实证暴露原脚本三大缺口：
  1. admit 闸门 PROVEN_EDGE_SOURCES 为空 → 严格执行则任何票都买不了（含手册合法的
     mainline_radar），设计矛盾导致实践中被整体架空；
  2. 缺 R3 批量买/R4 防御蓝筹/R5 振幅/R6 收高/R8 分化日 五项手册闸门；
  3. 无重复买入检查 → 高德红外(sz002414) 9:34/9:36 双单 7200 股事故。

v2.0 与 trade_state.py（当日状态）配合，实现手册第6章全部闸门的物理落地。

用法（完整参数示例）：
    python pre_trade_check_v2.py --code 002414 --name 高德红外 --price 14.09 \
        --prev-close 13.99 --pct-60d 0.35 --holdings 44 --total-cash 1019909 \
        --source mainline_radar --amount 50000 \
        --close 14.09 --high 14.20 --low 13.90 --amplitude-40d 0.32

退出码: 0=通过 / 2=拒绝(硬约束)  (20261011批5: 旧文档"3=参数不足"从无实现, 漂移修正)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

# ===== 持仓加仓检查：实时持仓源（8/20 TCL科技裸奔加仓案加固）=====
try:
    sys.path.insert(0, r"D:/workbuddyspace")
    from westock_client import WeStockClient as _WeStockClient
    _HELD_CACHE = None
except Exception:
    _WeStockClient = None

# 唯一配置源：D:\zcodesj\_common.py（8/23 从 skill 目录迁入项目真源，消除生产对 skill 副本依赖）
sys.path.insert(0, r"D:/zcodesj")
from _common import (  # noqa: E402
    MAX_PRICE, FIXED_STOP,
    norm_code, get_price_limit, limit_prices, board_of,
    PROVEN_EDGE_SOURCES, EXPERIMENTAL_SOURCES, RETIRED_SOURCES, OBSERVE_SOURCES,  # 2026-08-24 枚举收敛; 10-31 +赛马观察名单
    METHOD_ADMISSION_INTERIM_SOURCES,  # 2026-11-03 暂行方法准入(主人令七领跑组)
)

# ===== 当日状态跟踪（R3/R8/R13 状态源）=====
sys.path.insert(0, r"D:/zcodesj")
try:
    from trade_state import load_state  # noqa: E402
except Exception:  # 状态模块不可用时 fail-closed 由参数兜底
    load_state = None

# ===== admit 闸门信号源（2026-08-13 v2 修复白名单空矛盾）=====
# 2026-08-24 枚举收敛: PROVEN/EXPERIMENTAL/RETIRED 三集合唯一真源 = _common.py，
# 本文件与 strategy_gate / guarded_trade_executor 统一 import，禁止本地字面量
# （历史漂移两次致事故: bp_ep 缝隙 REJECT、full_market G12 unknown）。
# 手册认可的生产信号源（core-satellite 三层）：
#   核心40%: bp_ep_core(bp+ep季度)  卫星20%: turnover_mvp(换手率+最小方差)
#   机动40%: mainline_radar(主线雷达) / pattern_match(范式匹配)
#   manual_confirmed: 主人盘前确认的手工单（须前一交易日收盘后确认）

# [已移除] R4 防御蓝筹行业黑名单 — 主人8/14裁决：行业不该拦截
# [已移除] 最低仓位门槛 — 主人8/14裁决：迷你单非违规，保留不影响胜率

# ===== R25 大盘择时闸（2026-08-23 S-9: 崩盘日禁止买入）=====
# 8/19系统性风险日实证: 上证-2.40%+创业板-6.26% 买入3笔全被套(-3.4%~-6.8%)
# 触发条件: 上证跌幅>2% 且 创业板跌幅>4% → REJECT(止损紧急单除外)
MARKET_CRASH_SH_THRESHOLD = -2.0   # 上证跌幅阈值
MARKET_CRASH_CYB_THRESHOLD = -4.0  # 创业板跌幅阈值

# ===== P2 弱开局窗口闸（2026-10-13 主人「按推荐继续」批准 S3, 呈请=reports/admit_source_pruning_proposal_20261013.md）=====
# 证据: 10/8 十二笔与 10/12 四笔均为开盘30分钟抢跑(当日即套, 横店东磁一日往返v2.2即BEARISH);
#       卖侧孪生=10/9 跳空限价预案研究(开盘急杀时段纪律缺口的卖侧镜像)。
# 触发: 深证成指或创业板指当日涨跌幅 ≤-0.5% 且 时刻∈[09:30,10:30] → REJECT(豁免=--owner-approve-p2);
#       ≤-0.2% → WARN 提示。取数=qt.gtimg.cn 自包含直连(Sentinel 同族), 失败 fail-open+WARN
#       (hardening 闸不因数据通道阻断交易; 硬防线仍=S-5/R闸/v2.2走坏链)。10:30 后不判(错过窗口=行为问题非闸问题)。
P2_WEAK_OPEN_THRESHOLD = -0.5   # 百分数口径(与R25指数参数一致): -0.5 = -0.5%
P2_SOFT_WARN_THRESHOLD = -0.2
P2_WINDOW_START = "09:30"
P2_WINDOW_END = "10:30"

# ===== R26/R27 名单限制（2026-09-24 主人令「全面废除TopN名单限制」整体退役）=====
# 演进: 2026-08-23 S-7 落地名单制 → 2026-09-21 裁决降级 WARN 对照 → 2026-09-24 终局废除。
# 本闸不再有任何名单衍生判定: R26 名单内外对照(原 WARN)与 R27 名单价偏离(原 REJECT)全部退役;
# 防溢价追买职责由名单无关的 R27b 昨收溢价 WARN + R28 hotspot 追价 REJECT + R31/R32 位置闸承担。
# 白名单文件仍由 09:10 cron 生成, 仅作盘面参考产出, 非任何闸门输入;
# 准入职责=admit 闸门信号源清单(PROVEN∪EXPERIMENTAL, 未知/退役源仍拒)。

# S-4 买入时间窗集成已按主人 8/14 裁决废除（全天可交易），check_buy_window 集成已删除；本注记供 test_s_hardening 反向验证。
# ===== R27/R28 成交价守卫常量（2026-08-26 全维度自省 E33/E34 收口）=====
R27_PRICE_DEVIATION_MAX = 0.03   # 【已退役 2026-09-24】名单价偏离闸随名单制废除, 常量仅留档不再消费
R28_HOTSPOT_CHASE_MAX = 0.02     # hotspot 源执行价高于信号日收盘价2% → 拒绝（湘潭电化/盟固利买入即跌案例）
# 2026-08-28 取证(8月159笔配对): 全源"溢价>3%"组 T+1 均值 -0.12% 不劣于 ≤3%组(-0.70%),
# >3% 硬闸判据未达成→不上闸; >5% 子组 -1.11%(n=20) 弱负 → 仅提示性 WARN(建设机械600984 8/26 +5.6%案)
R27B_PREMIUM_WARN = 0.05
# 2026-08-28 R30a(E63/E79): 当日同行业新买上限(数据=trade_records 当日 BUY.sector, executor 写入)
R30_SAME_DAY_SECTOR_CAP = 4
# ===== R31 个股偏离度（K3 立项, 2026-08-28）=====
# 证据: 2016-2026 qfq 全史 1064万截面观测——ext20>15% → +5d -0.67%/+20d -0.76%;
#   >20% → +5d -1.00%/+20d -1.55%(胜率37%); >30% → +5d -1.31%/+20d -2.92%(胜率34%)。
# 深跌桶(ext20≤-10%)为均值回归反弹的另一逻辑(+1.06%/+5d), 本闸不碰。
# 数据源=qfq库(唯一价格源); 计划层(tier减半)在白名单, 本闸=执行层(当日追价由执行价触发)。
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
R31_EXT_WARN = 0.15    # ≥15% → WARN（建议 tier 减半）
R31_EXT_REJECT = 0.20  # ≥20% → REJECT（追高负期望）
# ===== R32 高位动量禁买（O-B 改写提案, 2026-08-29 主人"继续"授权落地）=====
# 证据: reports/OB_OD_REPLAY_20260829.md 预注册回放——8/14~8/28 全台账 105 笔买入×qfq,
#   chg20(T-1收盘/20交易日前收盘-1)>30% 组 n=15 全负、胜率 0%(早盘 -3.84%/非早盘 -9.16%),
#   对照组 +0.5~0.8%; 非早盘更差 = 问题在"高位"本身而非时段(原 O-B"仅早盘禁买"窄化)。
# 维度: 20日涨幅动量, 与 R31 ext20(价格位置)互补; T-1 时点计算无前视; 全时段全源 REJECT。
R32_CHG20_REJECT = 0.30
# 2026-08-29 P1-3(胜率计划 R-20260829-01, 复用 R32 数据链防重复建设): 高位派发日接货 WARN 档。
# chg20 从 ≥30% 回落至 15%~30% 带内(未触发 REJECT) 且 当日执行价跌幅 ≤-3% → 主升浪末端
# 派发嫌疑, WARN + 建议 tier 减半(非阻断, 与 R31/K3 WARN 同口径; 计划层减半在白名单)。
# 证据: 8/24 双鹭药业(20日振幅35.6%/跌停日买入-12.8%)/贝瑞基因-14.0%/北陆药业-14.9%/
# 建设机械(当日-5.7%→止损-12.0%)四笔深亏; 反例洛阳钼业+6.5% → WARN 起步, 两周反事实后再议升级。
R32_CHG20_WARN_LO = 0.15
R32_DAYDROP_WARN = -0.03

# ===== R-S5 不追高风格闸（2026-09-05 主人裁决 A; STYLE-GATE-STAGE5+FULLHIST 证据链）=====
# 证据: STAGE5 570日度截面(2024-2026) mom20≤+2% 闸 NW t=2.69/三年全正/执行级真实费用档+9.2pp年;
#       FULLHIST 2016-2023 补测 8/10 年正(2020/21 核心资产牛反例轻微)→硬约束+regime哨兵(SOP 9/5版)。
# 适用: 会话选股源(manual_confirmed/mass_scan/full_market) REJECT(裁决 A 硬约束,
#       例外=--owner-approve-rs5 显式特批降 WARN 留痕); 生产名单源(bp_ep/master_hist 等)
#       WARN(B 臂纸面对照, 生产语义不动, 9/30 A/B 首验后裁决是否升级)。
# 数据: 与 R32 同源 qfq(T-1 收盘/20 交易日前收盘-1), 无前视; 不足 21 根 fail-open。
RS5_MOM_REJECT = 0.02
RS5_SESSION_SOURCES = {"manual_confirmed", "mass_scan", "full_market"}

# ===== 白名单 A/B 实验（已终结, 2026-09-24 主人令「全面废除TopN名单限制」终局裁决）=====
# 2026-09-08 实验原案: A臂=现行名单制照跑; B臂=source=ab_freeroute 名单外放行(WARN)+R-S5走WARN。
# 终局: 不待 10/09 期满评估, 名单限制整体废除——全宇宙系统化源=唯一生产路径, ab_freeroute 保留为
# 合法记账源(_common.EXPERIMENTAL_SOURCES), A/B 分臂语义作废; 配置 data/ab_whitelist_experiment.json
# 已落终局档案(enabled=false, 不得按旧语义回退复辟 R26 名单制)。

# ===== R3/R8 批量买上限 =====
MAX_BUY_PER_DAY = 100           # 提示性上限(非硬约束)：主人8/14裁决单日新开无上限；设为100确保不误拦
MAX_BUY_PER_DAY_DIVERGENT = 2   # 分化日（板块宽度<20%）
DIVERGENT_BREADTH = 0.20

# ===== R14-R18 新增常量（8/13复盘经验库 TL-MKT-FAIL-009/010, OPT-004）=====
BOARD_COLLAPSE_THRESHOLD_YESTERDAY = 6   # 昨日最高板≥6
BOARD_COLLAPSE_THRESHOLD_TODAY = 5       # 今日最高板≤5 → 退潮确认
CHASE_BOARD_MIN = 3                       # R14: 禁追连板的最低板数
SEAL_BREAK_MAX = 3                        # R15: 炸板≥3次 → 封板质量过滤
HIGH_RISK_TURNOVER = 0.20                 # R16: 换手>20% + 3板以上 → HIGH_RISK
EXTREME_LIMIT_UP_MIN = 30                 # R18: 涨停≥30只(8/14复核50→30,放宽触发)
EXTREME_ADVANCE_RATIO_MAX = 0.25          # R18: 上涨家数占比<25%
SECTOR_LIMIT_UP_MIN = 5                   # R18: 极端结构行情要求板块涨停≥5只

# ===== R19 解禁回避常量（2026-08-15 P0事件数据工具落地）=====
# 数据源: D:\zcodesj\event_calendar.db restricted_release 表（event_data_tools.py --refresh 维护，
# 主源 akshare stock_restricted_release_detail_em，含"占解禁前流通市值比例"字段）。
# 注意: 旧R19锤子线/R20 V型反转已于8/14移出到技术分析模块，R19编号复用为解禁回避。
EVENT_CALENDAR_DB = r"D:\zcodesj\event_calendar.db"
R19_LOOKAHEAD_DAYS = 20                   # 未来20个自然日
R19_RELEASE_RATIO_BLOCK = 0.03            # 解禁市值占流通市值>3% → BLOCK
R19_RELEASE_RATIO_WARN = 0.01             # 1%~3% → WARN 关注

# ==== R22 双口径资金闸 (2026-08-18 主人指令: 建EM口径本地逐日累积库,双口径硬闸) ====
# 指令原文: "需要把EM口径终审做成MCP可执行环节挂进pre_trade_check，或建EM口径本地逐日累积库"
# 实现路线: 本地逐日累积库(D:/zcodesj/data/em_flow_snapshot.db) + 同花顺5日排行(akshare) + vipdoc停板标记
# 闸门逻辑: EM_5d与THS_5d方向相反→REJECT; 双负→WARN; 同正→PASS(含停板日→WARN标注拆逐日)
# 库由每日22:35 cron (prompt名: 22:35双口径采集)自动刷新
R22_EM_FLOW_DB = r"D:\zcodesj\data\em_flow_snapshot.db"
R22_STALE_DAYS = 2  # 数据超过2个交易日视为过期

# ==== R24 板块高潮禁追 (2026-08-21 主人批准, 模拟盘改进方案v2 M2) ====
# 规则: 申万L1行业指数单日涨幅≥5% → 该板块高潮后首个交易日禁买（高潮不追）。
# 实证: 8/18农林牧渔(+11.83%)追入批次5/5止损; 8/20医药生物(30+涨停)次日接力山河药辅-6.3%。
# 数据源: reports/sector_block_snapshot_*.json（sector_climax.py 生成, 离线/幂等）。
# 语义: 快照取"最近完整交易日"（T-1）高潮板块 → 禁买作用于今日=高潮后首个可买日;
#       当日盘中板块异动由 R1/R7 个股级闸门兜底。缺失/过期快照 → fail-open 显式标注。
R24_SNAP_GLOB = r"D:\zcodesj\reports\sector_block_snapshot_*.json"
R24_SNAP_STALE_DAYS = 4  # 快照超过4个自然日视为过期 → fail-open

# ---- R33 资金活跃度负面清单（2026-09-24 主人令「接入」提前提产; prereg=data/prereg_activity_excl_20260924.json 指纹f65996d1e1a73085）----
# 证据: 小石cn-daily 2020-2026, 换手率前10%组负向7/7年(h5测试IC-0.070 t-8.3; 流动性中性化-0.048 t-6.2),
#       月频剔除D10成本后样本外+25.2pp年化; ⚠️影子未满(60信号日,期满约2026-12), 影子照跑, FAIL即提请撤闸。
#       数据链: activity_excl_shadow_track.py 每日23:20刷新(data/activity_excl_shadow_hits.jsonl)。
#       已知口径: 换手率源滞后~25交易日(最近可用截面建清单, lag_days在账本)。
# fail-open: 账本缺失/信号日>7自然日 → 显式WARN不阻断(数据链断不冻结交易); clear 不产出口径。
ACT_EXCL_HITS_JSONL = r"D:\zcodesj\data\activity_excl_shadow_hits.jsonl"
ACT_EXCL_STALE_DAYS = 7


def check_r24_high_climax(sector: str) -> dict:
    """R24 板块高潮禁追 — 读最新快照, sector∈高潮板块 → reject。

    返回 status: reject/warn(板块未知)/no_snapshot/fail_open/clear
    """
    import glob
    if not sector or not sector.strip():
        return {"status": "no_sector",
                "note": "未提供 --sector(申万L1行业名), 无法执行R24板块高潮判定 → fail-open标注"}
    try:
        files = sorted(glob.glob(R24_SNAP_GLOB))
        if not files:
            return {"status": "no_snapshot", "note": "无板块高潮快照(sector_climax.py未跑) → fail-open标注"}
        with open(files[-1], encoding="utf-8") as f:
            snap = json.load(f)
    except Exception as e:
        return {"status": "fail_open", "note": f"快照读取失败({type(e).__name__}) → fail-open标注"}

    if not snap.get("ok"):
        return {"status": "fail_open", "note": f"快照标记fail-open: {snap.get('note','')}"}

    from datetime import date as _date
    asof = str(snap.get("asof_date", ""))
    if asof:
        try:
            stale = (_date.today() - _date.fromisoformat(f"{asof[:4]}-{asof[4:6]}-{asof[6:]}")).days
            if stale > R24_SNAP_STALE_DAYS:
                return {"status": "fail_open", "note": f"快照{asof}距今{stale}天>过期线 → fail-open标注"}
        except Exception:
            pass

    blocked = snap.get("blocked_sectors") or []
    if not blocked:
        return {"status": "clear", "note": "无≥5%高潮板块"}
    names = {b.get("name") for b in blocked}
    if sector in names:
        b = next(x for x in blocked if x.get("name") == sector)
        return {"status": "reject", "blocked": b,
                "note": f"板块高潮禁追: {sector} 单日+{b.get('pct')}%(数据日{b.get('trade_date')})"
                        " → 高潮后首个交易日禁买(8/18农业/8/20医药实证, 回踩MA5/10企稳可重审)"}
    return {"status": "clear", "note": f"{sector} 不在高潮禁买板块中"}


def check_r33_activity_excl(code6: str) -> dict:
    """R33 资金活跃度负面清单 — 影子账本最近信号日的换手率前10%禁买名单（2026-09-24 主人令接入）。
    只读本地 jsonl 无网络请求; clear 不产出; fail-open 显式标注（同 R19/R24 口径, 防PASS_WITH_WARN通胀）。"""
    try:
        c6 = str(code6).strip()[-6:]
        if not os.path.exists(ACT_EXCL_HITS_JSONL):
            return {"status": "no_data", "note": "账本文件缺失(activity_excl_shadow_hits.jsonl)"}
        last = ""
        with open(ACT_EXCL_HITS_JSONL, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if not last:
            return {"status": "no_data", "note": "账本为空(影子未产生信号)"}
        e = json.loads(last)
        sig_days = (datetime.now() - datetime.strptime(e["signal_date"], "%Y-%m-%d")).days
        if sig_days > ACT_EXCL_STALE_DAYS:
            return {"status": "stale",
                    "note": f"最近信号日{e['signal_date']}距今{sig_days}天>{ACT_EXCL_STALE_DAYS}天未刷新, 清单停用(fail-open)"}
        excl = {x for x in (e.get("excl_codes") or "").split(",") if x}
        if c6 in excl:
            return {"status": "reject",
                    "note": f"{c6} 在换手率前10%禁买清单(信号日{e['signal_date']}, 截面滞后{e.get('lag_days')}交易日, "
                            f"共{e.get('n_excl')}只; 回测7/7年负向; 影子期满前提产, prereg f65996d1e1a73085)"}
        return {"status": "clear"}
    except Exception as _e:
        return {"status": "no_data", "note": f"读取异常{type(_e).__name__} → fail-open不阻断"}


def check_r19_restricted(code: str, days: int = R19_LOOKAHEAD_DAYS) -> dict:
    """R19解禁回避数据查询（只读event_calendar.db，不做网络请求，保证闸门判定速度）。

    返回 status:
      hit           窗口内有解禁且占流通市值比例>R19_RELEASE_RATIO_BLOCK → 拒绝
      warn          窗口内有解禁且比例在1%~3% → 关注
      clear         窗口内有解禁但比例≤1% → 通过
      ratio_missing 窗口内有解禁但比例字段缺失 → fail-open（显式标注）
      no_data       解禁表不存在/库不存在/该股无解禁记录 → fail-open（显式标注"无解禁数据"）
    """
    from datetime import date as _date, timedelta as _timedelta
    start = _date.today().isoformat()
    end = (_date.today() + _timedelta(days=days)).isoformat()
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{EVENT_CALENDAR_DB}?mode=ro", uri=True)
        cur = con.execute(
            "SELECT release_date, release_type, release_market_value, pct_of_float_mv, "
            "release_shares FROM restricted_release "
            "WHERE code=? AND release_date>=? AND release_date<=? ORDER BY release_date",
            (code, start, end),
        )
        cols = ["release_date", "release_type", "release_market_value", "pct_of_float_mv", "release_shares"]
        events = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
    except Exception as e:
        return {"status": "no_data", "events": [], "note": f"解禁库不可用({type(e).__name__}) → fail-open"}

    if not events:
        return {"status": "no_data", "events": [],
                "note": f"解禁表无该股记录（窗口{start}~{end}） → 无解禁数据, fail-open"}

    with_ratio = [e for e in events if e["pct_of_float_mv"] is not None]
    if not with_ratio:
        return {"status": "ratio_missing", "events": events,
                "note": "窗口内有解禁事件但缺占流通市值比例字段 → fail-open"}
    worst = max(with_ratio, key=lambda e: e["pct_of_float_mv"])
    note_worst = (f"窗口内最大解禁 {worst['release_date']} {worst['release_type']} "
                  f"占流通{worst['pct_of_float_mv'] * 100:.2f}%")
    if worst["pct_of_float_mv"] > R19_RELEASE_RATIO_BLOCK:
        return {"status": "hit", "events": events, "worst": worst, "note": note_worst}
    if worst["pct_of_float_mv"] > R19_RELEASE_RATIO_WARN:
        return {"status": "warn", "events": events, "worst": worst, "note": note_worst}
    return {"status": "clear", "events": events, "worst": worst, "note": note_worst}


def check_r22_dual_caliber(code: str) -> dict:
    """R22双口径资金闸 — 读本地累积库(EM_5d vs THS_5d), 方向相反→REJECT, 双负→WARN"""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{R22_EM_FLOW_DB}?mode=ro", uri=True)
        row = con.execute(
            "SELECT em_5d, ths_5d, limit_days_5, trade_date FROM flow_snapshot "
            "WHERE code=? ORDER BY trade_date DESC LIMIT 1",
            (code,)
        ).fetchone()
        con.close()
    except Exception as e:
        return {"status": "no_data", "note": f"双口径库不可用({type(e).__name__}) → fail-open"}
    
    if not row:
        return {"status": "no_data", "note": f"{code} 无双口径快照数据(需先跑22:35采集) → fail-open"}
    
    em5, ths5, ld5, trade_date = row
    em5 = em5 or 0
    ths5 = ths5 or 0
    
    # 数据新鲜度(2026-09-10: 自然日→交易日口径——R22_STALE_DAYS 注释即「2个交易日」,
    # 原实现用自然日: 周一查上周五快照被误判 3 天 stale fail-open; 交易日口径以 qfq
    # 日历为锚, 落后 >2 个交易日才判 stale; 快照比 qfq 新(盘中当日)按 fresh)
    try:
        import sqlite3 as _sq
        from datetime import date as _d
        td = _d.fromisoformat(trade_date)
        con = _sq.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        qmax = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        if qmax:
            cnt = con.execute(
                "SELECT COUNT(DISTINCT date) FROM stock_daily_meta_qfq WHERE date>? AND date<=?",
                (td.strftime("%Y%m%d"), str(qmax))).fetchone()[0]
            con.close()
            if cnt > R22_STALE_DAYS:
                return {"status": "stale",
                        "note": f"双口径数据落后{cnt}个交易日>{R22_STALE_DAYS} → fail-open"}
        else:
            con.close()
    except (TypeError, ValueError) as e:
        print(f"[R22] 双口径 trade_date 解析失败, 新鲜度检查跳过(fail-open): trade_date={trade_date!r}, err={e}")
    except Exception as e:
        print(f"[R22] 交易日历查询失败, 新鲜度检查跳过(fail-open): {type(e).__name__}: {e}")
    
    signs_opposite = (em5 > 0 and ths5 < 0) or (em5 < 0 and ths5 > 0)
    
    if signs_opposite:
        note = (f"EM5日={em5:+,.0f} THS5日={ths5:+,.0f} → 方向相反!")
        if ld5:
            note += f" 近5日含{ld5}个停板日(算法争议区),核心差集中在停板日,建议拆逐日"
        return {"status": "reject", "note": note, "em5": em5, "ths5": ths5, "ld5": ld5}
    
    if em5 <= 0 and ths5 <= 0:
        note = f"双口径均负: EM5日={em5:+,.0f} THS5日={ths5:+,.0f} → 资金弱势"
        return {"status": "warn", "note": note, "em5": em5, "ths5": ths5, "ld5": ld5}
    
    # 双口径同正: PASS (若含停板日附加warning)
    note = f"双口径同正: EM5日={em5:+,.0f} THS5日={ths5:+,.0f}"
    if ld5:
        note += f" ⚠️近5日含{ld5}个停板日(算法争议区,须拆逐日核实)"
    return {"status": "pass" if not ld5 else "warn",
            "note": note, "em5": em5, "ths5": ths5, "ld5": ld5}


def log_precheck(args, result: dict) -> None:
    """闸门判定无条件落库 attribution.db guard_precheck_log（--no-log 跳过）。

    naked_trade_audit.py 盘后对账的数据源A：当日每笔 BUY 成交须能在本表中找到
    同 code 且 verdict != 'REJECT' 的 precheck 记录，否则判为裸奔单。
    """
    if getattr(args, "no_log", False):
        return
    try:
        from datetime import datetime
        import sqlite3
        # 2026-09-14 B3(gate_audit_20260914 §六): fail-open 数据缺失标注统一加
        # "[fail-open]" 前缀, 与真实风险 WARN 区分——统计消费方可 startswith 过滤,
        # 免受"→ fail-open标注"/"[fail-open显式标注]"等写法漂移影响。
        warns = ["[fail-open]" + w if ("fail-open" in w and not w.startswith("[fail-open]"))
                 else w for w in result.get("warnings", [])]
        con = sqlite3.connect(r"D:\zcodesj\attribution\attribution.db")
        con.execute(
            "INSERT INTO guard_precheck_log "
            "(ts, trade_date, code, name, price, source, verdict, rejects, warnings) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"),
             datetime.now().strftime("%Y%m%d"),
             result.get("code", ""), result.get("name") or None,
             getattr(args, "price", None), result.get("source", ""),
             result.get("verdict", ""),
             json.dumps(result.get("rejects", []), ensure_ascii=False),
             json.dumps(warns, ensure_ascii=False)),
        )
        con.commit()
        con.close()
    except Exception as e:
        print(f"[WARN] guard_precheck_log 落库失败(不阻塞判定): {type(e).__name__} {e}",
              file=sys.stderr)


def is_chasing_high(pct_60d: float | None, is_limit_up_buy: bool, late_stage: bool) -> bool | None:
    """追高三条件必须同时满足。缺参数返回 None（无法判定）。"""
    if pct_60d is None:
        return None
    return bool(pct_60d > 0.70 and not is_limit_up_buy and late_stage)


def _held_codes() -> set[str]:
    """实时持仓代码集（8/20 TCL科技裸奔加仓案加固：加仓=买入必过闸门）。
    拉取失败/库不可用时返回空集 → 闸门对持仓判定 fail-open，但 admit/R13 等其余闸门仍独立生效。
    """
    global _HELD_CACHE
    if _WeStockClient is None:
        return set()
    if _HELD_CACHE is not None:
        return _HELD_CACHE
    try:
        c = _WeStockClient()
        pos = c.positions()
        held = {str(p.get("code", "")).lower() for p in pos.get("data", {}).get("positions", [])}
        _HELD_CACHE = held
        return held
    except Exception:
        return set()


def check_r31_extension(code6: str, price: float | None, owner_approved: bool = False) -> dict:
    """R31 个股偏离度（K3）：ext20 = (执行价 − MA20)/MA20，qfq 库最近20个交易日收盘。

    数据缺失/price 缺失/收盘不足 → fail-open 显式标注（与 R19/R22/R30a 同口径）。
    返回 status: reject / warn / clear / fail_open。
    """
    if price is None or price <= 0:
        return {"status": "fail_open",
                "note": f"未提供有效 --price({price})，无法计算 ext20 → fail-open标注"}
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date <= ? "
            "ORDER BY date DESC LIMIT 20", (code6, datetime.now().strftime("%Y%m%d"))).fetchall()
        con.close()
        closes = [r[0] for r in rows if r[0] is not None]
        if len(closes) < 15:
            return {"status": "fail_open",
                    "note": f"qfq 有效收盘不足20日(n={len(closes)}) → fail-open标注"}
        ma20 = sum(closes) / len(closes)
        if ma20 <= 0:
            return {"status": "fail_open", "note": "MA20<=0 → fail-open标注"}
        ext20 = (price - ma20) / ma20
        if ext20 >= R31_EXT_REJECT:
            if owner_approved:
                # 2026-09-08 主人裁决开豁免口(追价买强势股逐笔特批): REJECT降WARN留痕
                return {"status": "warn", "ext20": ext20, "ma20": ma20,
                        "note": f"执行价{price} vs MA20={ma20:.2f} 偏离{ext20*100:.1f}% "
                                f"≥ {R31_EXT_REJECT*100:.0f}% → 追高负期望; "
                                f"--owner-approve-r31 主人特批豁免(降WARN留痕, 9/8裁决)"}
            return {"status": "reject", "ext20": ext20, "ma20": ma20,
                    "note": f"执行价{price} vs MA20={ma20:.2f} 偏离{ext20*100:.1f}% "
                            f"≥ {R31_EXT_REJECT*100:.0f}% → 追高负期望, 拒绝(K3)"}
        if ext20 >= R31_EXT_WARN:
            return {"status": "warn", "ext20": ext20, "ma20": ma20,
                    "note": f"执行价{price} vs MA20={ma20:.2f} 偏离{ext20*100:.1f}% "
                            f"≥ {R31_EXT_WARN*100:.0f}% → 建议 tier 减半(K3)"}
        return {"status": "clear", "ext20": ext20, "ma20": ma20,
                "note": f"ext20={ext20*100:+.1f}% 未超阈值"}
    except Exception as e:
        return {"status": "fail_open", "note": f"qfq 读取失败({type(e).__name__}) → fail-open标注"}


# ===== R37 情绪档择时门（2026-10-07 主人「模拟盘实战验证授权」批准, PD-034 layer3 激活）=====
# 证据: reports/timing_state_machine_20261007.md 全市场裸检验四档全单调(强档f5胜率68.6%/弱档30.8%,
# 分年8/8正差含2022熊, 与深位首板/区域引擎/馒头对照构成第四条独立同向证据链)。
# 语义: T-1涨停家数(qfq全市场>=9.5%, 与PD-025阈值族及择时状态机同口径) <40 → 弱档买入 REJECT;
# 40-60 → WARN(谨慎限购提示不阻断); >=61 → clear。授权范围=模拟盘实战验证(practice_190),
# 真盘(如有)另走授权。数据缺失 fail-open(不阻塞买入, 显式标注)。
R37_WEAK_ZT_MAX = 39      # <40家 = 弱档
R37_MID_ZT_MIN = 40       # 40-60 = 中档(WARN)
R37_STRONG_ZT_MIN = 61    # >=61 = 强档(clear)
R37_WEAK_F20_MED = -1.46  # 弱档f20中位pp(证据存档)


def check_r37_emotion_gear(owner_approved: bool = False) -> dict:
    """R37 情绪档择时门（PD-034 layer3, 2026-10-07 主人批准模拟盘实战验证）:

    昨日(=qfq最新交易日, 盘中当日数据未入库故天然为T-1口径)全市场涨停家数
    (change_pct>=9.5%, 与择时状态机/裸检验同口径) <40 → REJECT;
    40-60 → WARN 谨慎限购; >=61 → clear。与回放判据一致无前视;
    qfq 不可达 → fail-open 显式标注。
    owner_approved=True → 弱档REJECT降WARN留痕(2026-10-15落地, 与ddgate/r38/r39
    同构: 默认关不弱化任何真实下单闸, 仅供l0冒烟/单测确定性; 闸本体不变)。
    """
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        row = con.execute(
            "SELECT MAX(date) FROM stock_daily_meta_qfq "
            "WHERE code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%'").fetchone()
        last_date = row[0] if row else None
        if not last_date:
            con.close()
            return {"status": "fail_open", "note": "qfq 无数据 → fail-open标注"}
        zt_cnt = con.execute(
            "SELECT COUNT(*) FROM stock_daily_meta_qfq "
            "WHERE date=? AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%') "
            "AND change_pct>=9.5", (last_date,)).fetchone()[0]
        con.close()
        if zt_cnt <= R37_WEAK_ZT_MAX:
            if owner_approved:
                return {"status": "warn", "zt_cnt": zt_cnt, "date": str(last_date),
                        "note": f"R37情绪档[owner-approve]: 弱档({last_date}涨停{zt_cnt}家<"
                                f"{R37_WEAK_ZT_MAX+1}) 主人特批放行(WARN留痕, 逆门控开仓风险自知)"}
            return {"status": "reject", "zt_cnt": zt_cnt, "date": str(last_date),
                    "note": f"弱档禁买: {last_date}涨停{zt_cnt}家<{R37_WEAK_ZT_MAX+1} "
                            f"(裸检验弱档f20中位{R37_WEAK_F20_MED}pp/胜率30.8%, PD-034主人"
                            f"2026-10-07模拟盘授权) → 逆风日不新开仓; 特批 --owner-approve-r37"}
        if zt_cnt < R37_STRONG_ZT_MIN:
            return {"status": "warn", "zt_cnt": zt_cnt, "date": str(last_date),
                    "note": f"中档谨慎限购: {last_date}涨停{zt_cnt}家 40-60 "
                            f"(裸检验中档f5胜率45.3%=等权无优势) → 仅已验证最强信号出手"}
        return {"status": "clear", "zt_cnt": zt_cnt, "date": str(last_date),
                "note": f"强档可买: {last_date}涨停{zt_cnt}家>=61 "
                        f"(裸检验强档f5胜率68.6%地板)"}
    except Exception as e:
        return {"status": "fail_open", "note": f"qfq 读取失败({type(e).__name__}) → fail-open标注"}


# ===== R38 期望闸 + R39 落刀闸（2026-10-08 主人授权「修复、优化、迭代」落地）=====
# 缺口证据: 10/8 预注册12只仪表赛前判定全部「零/负期望占用候选」, 当日 3涨9跌(均值-1.31%)且
# A组-R组=+1.47pp 与仪表预测(≈+1.74pp)方向精确命中——判别器对, 但判定结果无闸消费(报告层空转);
# 同日蓝丰生化真实低开-5.1%(5.56/5.86)无任何个股级跳空护栏可拦(PD-037=账户级已实现亏损闸,
# 非个股级)。档案=reports/premarket12_reflection_20261008.md。
GAUGE_GLOB = os.path.join("data", "forward_up_prob_gauge_*.json")
PROBE_PATH = os.path.join("data", "market_state_probe.json")
R38_WEAK_STATES = {"LOW_VOL"}     # 弱档集合(负期望仅在弱档升REJECT, 其余档WARN)
R38_STALE_DAYS = 10               # gauge特征日距今日历天上限(过期=期望判定不具时效)
R39_GAP_PCT = -3.0                # 开盘跳空低开阈值(蓝丰-5.12%案)
R39_DROP_PCT = -5.0               # 现价较昨收日内深跌阈值


def _latest_gauge():
    """最新 forward_up_prob_gauge 缓存 {asof, cells}; 无文件/损坏返回 None(调用方 fail-open标注)"""
    import glob
    files = sorted(glob.glob(GAUGE_GLOB))
    if not files:
        return None
    try:
        with open(files[-1], encoding="utf-8") as f:
            d = json.load(f)
        return {"asof": str(d.get("asof_feature_date", "")), "cells": d.get("cells", {}),
                "file": files[-1]}
    except Exception:
        return None


def _market_state():
    """读 market_state_probe.json → {state, stale, asof}; 不可得返回 state=None/stale=True"""
    try:
        with open(PROBE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        asof = str(d.get("asof", ""))
        age = (datetime.now() - datetime.strptime(asof, "%Y%m%d")).days if asof else 9999
        return {"state": d.get("state"), "stale": age > R38_STALE_DAYS, "asof": asof}
    except Exception:
        return {"state": None, "stale": True, "asof": ""}


def _gauge_feats(code6: str, asof: str):
    """单票 pos250/chg5(口径镜像 forward_up_prob_gauge.main: 250日窗min120/chg5=pct_change(5)),
    取 asof 当日行(与缓存cells特征日一致); 次新(<61行)/停牌/库缺返回 None"""
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        try:
            rows = con.execute(
                "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date",
                (code6, asof)).fetchall()
        finally:
            con.close()
    except Exception:
        return None
    if len(rows) < 61 or str(rows[-1][0]) != asof:
        return None
    closes = [float(r[1]) for r in rows]
    chg5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else None
    w = closes[-250:]
    if len(w) < 120 or max(w) <= min(w):
        pos250 = None
    else:
        pos250 = (closes[-1] - min(w)) / (max(w) - min(w)) * 100
    return {"pos250": pos250, "chg5": chg5}


def _r38_classify(pos250, chg5):
    """组分类唯一真源=forward_up_prob_gauge.classify(禁本地镜像漂移); 不可用返回 None"""
    try:
        from types import SimpleNamespace
        from forward_up_prob_gauge import classify
        return classify(SimpleNamespace(pos250=pos250, chg5=chg5))
    except Exception:
        return None


def check_r38_expectancy(code6: str, owner_approved: bool = False,
                         gauge=None, state_info=None, feats=None) -> dict:
    """R38 期望闸: gauge组级负期望 × LOW_VOL弱档 → REJECT; 负期望×非弱档 → WARN; 非负 → PASS。

    缺口: 期望判定此前停在呈报层无闸消费(10/8 12只零/负期望照买, 当日-1.31%应证)。
    无缓存/特征日过期/停牌库缺次新/组样本不足 → fail_open标注(研究仪表缺证据不硬拦)。
    --owner-approve-r38 特批降WARN留痕(默认关)。注入 gauge/state_info/feats 供单测。
    """
    g = gauge if gauge is not None else _latest_gauge()
    if not g or not g.get("asof"):
        return {"status": "fail_open", "note": "无forward_up_prob_gauge缓存, 期望未判定 → fail-open标注"}
    try:
        age = (datetime.now() - datetime.strptime(g["asof"], "%Y%m%d")).days
    except ValueError:
        return {"status": "fail_open", "note": f"gauge特征日格式异常({g.get('asof')}) → fail-open标注"}
    if age > R38_STALE_DAYS:
        return {"status": "fail_open",
                "note": f"gauge特征日{g['asof']}距今{age}天>{R38_STALE_DAYS}, 期望判定过期 → fail-open标注"}
    st = state_info if state_info is not None else _market_state()
    ft = feats if feats is not None else _gauge_feats(code6, g["asof"])
    if not ft or ft.get("pos250") is None or ft.get("chg5") is None:
        return {"status": "fail_open",
                "note": f"gauge特征日{g['asof']}该股停牌/库缺/次新, 不可分类 → fail-open标注"}
    grp = _r38_classify(ft["pos250"], ft["chg5"])
    if grp is None:
        return {"status": "fail_open", "note": "组分类器(forward_up_prob_gauge.classify)不可用 → fail-open标注"}
    cell = (g.get("cells") or {}).get(grp) or {}
    med = cell.get("t20e_med_pct")
    if med is None or not cell.get("sample_ok20"):
        return {"status": "fail_open",
                "note": f"{grp} 组样本不足(sample_ok20={cell.get('sample_ok20')}), 期望不可判 → fail-open标注"}
    tag = f"{grp} T20E中位{med:+.2f}% (gauge特征日{g['asof']}, 250交易日窗口)"
    if med >= 0:
        return {"status": "pass", "note": f"期望非负: {tag}"}
    state_name = st.get("state") or "未知"
    if st.get("state") in R38_WEAK_STATES and not st.get("stale"):
        if owner_approved:
            return {"status": "warn",
                    "note": f"R38期望闸[owner-approve]: 弱档({state_name})×{tag}<0 负期望域, 主人特批放行(WARN留痕)"}
        return {"status": "reject",
                "note": f"LOW_VOL弱档 × {tag}<0 负期望域 → 拒绝(10/8预注册12只同型: 判定零/负期望仍买入, "
                        f"当日3涨9跌均值-1.31%); 弱档重拳违第零问; 特批 --owner-approve-r38"}
    return {"status": "warn",
            "note": f"市场状态={state_name}(非弱档/探针过期asof={st.get('asof')}), {tag}<0 → 负期望提示"}


def _tencent_real_quote(code6: str):
    """腾讯公开行情(真实市场口径, 与daily_chain_update同一源): {current, prev_close, open}; 失败抛异常"""
    import urllib.request
    sym = ("sh" if code6.startswith("6") else "sz") + code6
    req = urllib.request.Request("http://qt.gtimg.cn/q=" + sym,
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        line = r.read().decode("gbk", errors="ignore")
    vals = line.split("=", 1)[1].strip().strip('"').split("~")
    return {"current": float(vals[3]), "prev_close": float(vals[4]), "open": float(vals[5])}


def check_r39_fallingknife(code6: str, owner_approved: bool = False,
                           quote_fetch=None) -> dict:
    """R39 落刀闸: 个股开盘跳空≤-3% 或 现价较昨收深跌≤-5% → REJECT(腾讯真实行情口径)。

    缺口: 10/8蓝丰生化真实低开-5.1%(5.56/5.86)系统无个股级跳空护栏(PD-037为账户级),
    且模拟盘报价与真实开盘脱节(≈5.74 vs 5.56)——真实市场级跳空此前无法被任何闸感知。
    行情不可得/字段缺失 → fail_open标注(R28同构)。--owner-approve-r39 特批降WARN留痕。
    quote_fetch 注入供单测(缺省=腾讯实时)。
    """
    fetch = quote_fetch or _tencent_real_quote
    try:
        q = fetch(code6)
    except Exception as e:
        return {"status": "fail_open",
                "note": f"真实行情不可得({type(e).__name__}), 跳空/深跌无法校验 → fail-open标注"}
    cur = float(q.get("current") or 0.0)
    prev = float(q.get("prev_close") or 0.0)
    opn = float(q.get("open") or 0.0)
    if prev <= 0 or (opn <= 0 and cur <= 0):
        return {"status": "fail_open",
                "note": "行情字段缺失(非交易时段?), 跳空/深跌无法校验 → fail-open标注"}
    parts = []
    if opn > 0:
        gap = (opn / prev - 1) * 100
        if gap <= R39_GAP_PCT:
            parts.append(f"开盘跳空{gap:+.2f}%≤{R39_GAP_PCT}%")
    if cur > 0:
        dd = (cur / prev - 1) * 100
        if dd <= R39_DROP_PCT:
            parts.append(f"现价较昨收{dd:+.2f}%≤{R39_DROP_PCT}%")
    if not parts:
        return {"status": "pass",
                "note": f"跳空/深跌正常(open={opn}, cur={cur}, prev={prev})"}
    if owner_approved:
        return {"status": "warn",
                "note": f"R39落刀闸[owner-approve]: {'; '.join(parts)}, 主人特批放行(WARN留痕)"}
    return {"status": "reject",
            "note": f"{'; '.join(parts)} → 拒绝(落刀禁接, 蓝丰生化(002513) 10/8真实低开-5.12%漏网教训); "
                    f"特批 --owner-approve-r39"}


# ===== R41 主线对齐闸（2026-10-08 第三项落地, WARN-only 提请制）=====
# 缺口证据: 10/8 12只与当日真实主线(电池+资源能源)零相关, 主线票(医药IT国新健康)恰为全组唯一
# 收红+2.60%——主线对齐有判别力但此前只做呈报加分不做任何消费。方向层合同自述
# 「收敛视野+排除逆风, 非追热点非过滤器」(data/direction_layer_latest.json usage) → 本闸 WARN-only
# 提请制不拦截; 升REJECT须主人先定义低位批例外条款(低位箱体选股与主线跟随相位错配, 天然非主线)。
DIRECTION_LAYER_PATH = os.path.join("data", "direction_layer_latest.json")
R41_STALE_DAYS = 10                # 方向层target_date距今日历天上限

# ===== R44 ST状态硬闸（2026-10-26 联创光电漏网教训, 主人令「现在就修复优化」）=====
# 根因: 选股层用精确日匹配(code,ASOF)查st_daily, 10/23数据不完整(94/4608行)致漏。
# 修复: 本闸独立查最新st_daily记录(不看ASOF, 只看该股最新一条), is_st=1→REJECT。
# 架构位置: R44=生产闸门层独立ST防线(选股层过滤漏网时的最后兜底)。
ST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "st_history.db")


def check_r44_st_status(code: str) -> dict:
    """R44 ST状态硬闸: 查st_daily该股最新记录, is_st=1 → REJECT(非WARN, ST股禁买铁律)."""
    try:
        import sqlite3 as _sq3
        _digits = "".join(c for c in str(code) if c.isdigit())
        code6 = _digits[-6:] if len(_digits) >= 6 else _digits
        con = _sq3.connect(f"file:{ST_DB_PATH}?mode=ro", uri=True)
        row = con.execute(
            "SELECT is_st, date FROM st_daily WHERE code LIKE ? "
            "ORDER BY date DESC LIMIT 1",
            (f"%{code6}%",)).fetchone()
        con.close()
        if not row:
            return {"status": "no_data", "note": "st_daily无记录(fail-open, 可能新股)"}
        if row[0] == 1:
            return {"status": "reject",
                    "note": f"ST股(st_daily最新{row[1]} is_st=1)→ 禁买(宪法铁律); "
                            f"选股层漏网兜底(联创光电600363教训20261026)"}
        return {"status": "clear", "note": f"非ST(最新{row[1]} is_st=0)"}
    except Exception as e:
        return {"status": "no_data", "note": f"st_daily查询异常(fail-open): {e}"}


# ===== R42 商誉高位排雷WARN（2026-10-25 主人令「排雷入R闸」, 15项prereg唯一正产出）=====
# 依据: prereg ce9575c9 E3, 商誉/净资产>=50% → 20日t20e平均超额-4.42pp/8年负(n=586)
# 定位: WARN级=信息增强不拦截(排雷标记呈报主人决定, 升REJECT须主人裁决)
GOODWILL_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "event_calendar.db")
R42_GOODWILL_THRESHOLD = 0.50      # 商誉/净资产≥50%


def check_r42_goodwill_high(code: str) -> dict:
    """R42 商誉高位排雷WARN: 最近报告期 gw_to_net_assets >= 0.50 → WARN."""
    try:
        import sqlite3 as _sq3
        con = _sq3.connect(f"file:{GOODWILL_DB}?mode=ro", uri=True)
        row = con.execute(
            "SELECT gw_to_net_assets, report_date FROM goodwill_stock_hist "
            "WHERE code=? ORDER BY report_date DESC LIMIT 1",
            (code[-6:],)).fetchone()
        con.close()
        if not row or row[0] is None:
            return {"status": "no_data", "note": "商誉数据缺失(fail-open)"}
        # 报告期新鲜度 — 20261025修: 末期距今>600天(约6个报告期)推定商誉已出清,
        # 陈旧记录(天海防务2017实证)报WARN属误报; fail-open标注不判。
        try:
            from datetime import datetime as _dt
            age_days = (_dt.now() - _dt.strptime(str(row[1]), "%Y%m%d")).days
        except Exception:
            age_days = 0
        if age_days > 600:
            return {"status": "no_data",
                    "note": f"商誉数据陈旧(末期{row[1]}, 距今{age_days}天, 推定已减值出清, fail-open)"}
        ratio = float(row[0])
        if ratio >= R42_GOODWILL_THRESHOLD:
            return {"status": "warn",
                    "note": f"商誉/净资产={ratio:.1%}≥50% 排雷标记: 20日均超额-4.42pp/8年负"
                            f"(报告期{row[1]}, prereg E3 20261025)"}
        return {"status": "clear", "note": f"商誉/净资产={ratio:.1%}<50%"}
    except Exception as e:
        return {"status": "no_data", "note": f"商誉查询异常(fail-open): {e}"}


# ===== R43 大宗异常排雷WARN（2026-10-25 主人令「排雷入R闸」）=====
# 依据: prereg E5, 深折≤-10% → -6.64pp/11年全负; 高溢价≥+5% → -3.38pp
# 数据: data/block_trade_stage_2016_2026.csv(akshare stock_dzjy_mrtj, 需日更)
# 定位: WARN级=信息增强不拦截
BLOCK_TRADE_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "block_trade_stage_2016_2026.csv")
R43_LOOKBACK_DAYS = 5              # 近5个交易日
R43_DEEP_DISCOUNT = -0.10          # 深折阈值
R43_HIGH_PREMIUM = 0.05            # 高溢价阈值


def check_r43_block_trade_anomaly(code: str) -> dict:
    """R43 大宗异常排雷WARN: 近5交易日折溢率≤-10%或≥+5% → WARN."""
    try:
        import pandas as _pd
        if not os.path.isfile(BLOCK_TRADE_CSV):
            return {"status": "no_data", "note": "大宗数据文件缺失(fail-open)"}
        bt = _pd.read_csv(BLOCK_TRADE_CSV, dtype={"证券代码": str})
        if len(bt) == 0:
            return {"status": "no_data", "note": "大宗数据为空(fail-open)"}
        # 数据新鲜度锚=CSV最新交易日(陈旧>15天则fail-open不判)
        dmax = _pd.to_datetime(bt["交易日期"]).max()
        if (_pd.Timestamp.now().normalize() - dmax).days > 15:
            return {"status": "no_data", "note": f"大宗数据过期(最新{dmax.date()}, fail-open)"}
        # 近5交易日窗口(日历10天缓冲覆盖节假日) — 20261025修: 原实现取末行无日期过滤,
        # 陈年大宗(龙源2017/阳普2023实证)误报WARN; 语义回归"近5交易日"。
        cutoff = dmax - _pd.Timedelta(days=10)
        dt_all = _pd.to_datetime(bt["交易日期"])
        bt_code = bt[(bt["证券代码"].str[-6:] == code[-6:]) & (dt_all >= cutoff)]
        if len(bt_code) == 0:
            return {"status": "clear", "note": "无近期(5交易日内)大宗交易"}
        latest = bt_code.iloc[-1]
        disc = _pd.to_numeric(latest.get("折溢率"), errors="coerce")
        if _pd.isna(disc):
            return {"status": "no_data", "note": "折溢率缺失(fail-open)"}
        trade_date = str(latest.get("交易日期", ""))
        if disc <= R43_DEEP_DISCOUNT:
            return {"status": "warn",
                    "note": f"大宗深折={disc:.1%}≤-10% 排雷标记: -6.64pp/11年全负"
                            f"({trade_date}, prereg E5 20261025)"}
        if disc >= R43_HIGH_PREMIUM:
            return {"status": "warn",
                    "note": f"大宗高溢价={disc:.1%}≥+5% 排雷标记: -3.38pp"
                            f"({trade_date}, prereg E5 20261025)"}
        return {"status": "clear", "note": f"大宗折溢率={disc:.1%}正常({trade_date})"}
    except Exception as e:
        return {"status": "no_data", "note": f"大宗查询异常(fail-open): {e}"}



def check_r41_mainline(sector: str | None, layer=None) -> dict:
    """R41 主线对齐闸(WARN-only): 候选申万L1板块 ∉ 方向层C1量能主线集 → WARN提请, 不拦截。

    数据源=data/direction_layer_latest.json(每日晨链生成, source1_mainline.c1_volume)。
    合同依据=方向层usage自述「非过滤器」→ 本闸零交易权; sector未传/层缺失/过期/C1空 → fail_open标注。
    注入 layer 供单测。
    """
    if not sector:
        return {"status": "fail_open", "note": "未提供--sector(申万L1), 主线对齐无法校验 → fail-open标注(R24同口径)"}
    d = layer
    if d is None:
        try:
            with open(DIRECTION_LAYER_PATH, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return {"status": "fail_open", "note": "方向层direction_layer_latest.json不可得, 主线对齐无法校验 → fail-open标注"}
    tdate = str(d.get("target_date", ""))
    try:
        age = (datetime.now() - datetime.strptime(tdate, "%Y%m%d")).days
    except ValueError:
        return {"status": "fail_open", "note": "方向层target_date格式异常 → fail-open标注"}
    if age > R41_STALE_DAYS:
        return {"status": "fail_open",
                "note": f"方向层target_date={tdate}距今{age}天>{R41_STALE_DAYS}, 主线基准过期 → fail-open标注"}
    mainline = (d.get("source1_mainline") or {}).get("c1_volume") or []
    if not mainline:
        return {"status": "fail_open",
                "note": f"C1量能主线集为空(方向层{tdate}, 量能未聚焦), 对齐无基准 → fail-open标注"}
    if sector in mainline:
        return {"status": "pass",
                "note": f"主线对齐: {sector} ∈ C1主线{mainline}(方向层{tdate})"}
    return {"status": "warn",
            "note": f"{sector} ∉ C1量能主线{mainline}(方向层{tdate}) → 非主线候选提示(合同=视野非过滤器, "
                    f"不拦截); 弱市非主线=轮动兑现风险自知; 升REJECT须主人定义低位批例外条款"}


def check_r32_chgmom(code6: str, price: float | None = None, prev_close: float | None = None) -> dict:
    """R32 高位动量禁买（O-B）：chg20 = T-1 qfq 收盘 / 20 个交易日前收盘 − 1 ≥ 30% → REJECT。

    纯历史序列判定无需执行价；date < 当日 严格取 T-1（与回放判据一致，无前视）；
    qfq 不足 21 根/读取失败 → fail-open 显式标注（与 R31 同口径）。
    P1-3(2026-08-29): 增派发分支——chg20 带内回落 + 当日跌 ≥3% → WARN(高位派发日接货)。
    """
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date < ? "
            "ORDER BY date DESC LIMIT 21",
            (code6, datetime.now().strftime("%Y%m%d"))).fetchall()
        con.close()
        closes = [r[0] for r in rows if r[0] is not None]  # 新 -> 旧
        if len(closes) < 21 or not closes[20] or closes[20] <= 0:
            return {"status": "fail_open",
                    "note": f"qfq 有效收盘不足21日(n={len(closes)}) → fail-open标注"}
        chg20 = closes[0] / closes[20] - 1  # closes[0]=T-1, closes[20]=20个交易日前
        if chg20 >= R32_CHG20_REJECT:
            # 2026-09-08 主人裁决「涨停的拦在外面说明我们还是不对」: 执行层REJECT同步撤销降WARN
            # (与白名单层R32标注化同链; 证据档案=R32_RECHECK_20260908.md 低胜率高尾部)
            return {"status": "warn", "chg20": chg20,
                    "note": f"前20日涨幅 {chg20*100:+.1f}% ≥ {R32_CHG20_REJECT*100:.0f}% "
                            f"(T-1={closes[0]:.2f}/20日前={closes[20]:.2f}) → 高位动量WARN留痕(9/8裁决撤销禁买)"}
        # P1-3 派发分支: 高位回落带内 + 当日大跌 = 主升浪末端派发日接货(WARN, 建议 tier 减半)
        if (R32_CHG20_WARN_LO <= chg20 < R32_CHG20_REJECT
                and price is not None and prev_close is not None and prev_close > 0):
            day_drop = (price - prev_close) / prev_close
            if day_drop <= R32_DAYDROP_WARN:
                return {"status": "warn", "chg20": chg20, "day_drop": day_drop,
                        "note": f"高位派发嫌疑: 前20日涨幅{chg20*100:+.1f}%回落带内"
                                f"({R32_CHG20_WARN_LO*100:.0f}%~{R32_CHG20_REJECT*100:.0f}%) 且当日"
                                f"{day_drop*100:.1f}% ≤ {R32_DAYDROP_WARN*100:.0f}% → 建议 tier 减半"
                                f"(P1-3, 8/24双鹭/贝瑞/北陆教训; 反例存在故 WARN 不阻断)"}
        return {"status": "clear", "chg20": chg20,
                "note": f"chg20={chg20*100:+.1f}% 未超阈值"}
    except Exception as e:
        return {"status": "fail_open", "note": f"qfq 读取失败({type(e).__name__}) → fail-open标注"}


R30B_CLUSTER_CAP = 0.30  # PD-024③(2026-10-23主人「继续」批准落闸, 承PD-024②): 单一申万L1持仓市值占比上限25%→30%。
# 证据=全域回测六轮R6剂量-反应(reports/backtest_fullhist_2016_2026_20261022/report_final_v2):
# 25%帽对bp_ep类行业篮子binary拦截整月部署(-6.0pp/年, 帽25%→+7.8% vs 帽≥30%四档逐字节相同+13.78%),
# 30%完全恢复且横盘/动量域簇极少超30%(零影响)。


def check_r30b_cluster(code, sector="", notional=None, total_cash=None,
                       owner_approve=False) -> dict:
    """R30b 组合级行业占比闸（PD-024③ 2026-10-23 落闸 25→30, 承 PD-024②; R30a 管当日新买维度, 本闸管存量维度）:

    候选所属申万L1的当前持仓市值(含拟买入名义) / 账户总资产 > 30% → 新买入 REJECT
    （存量不动, 不涉卖出——超限簇只封"再加"不逼"减仓"）。
    数据源: westock 实时持仓(唯一真值, positions 行含 currentPrice) + portfolio.totalAssets;
    行业映射: stock_sector_sw1(dragon_quant_meta.db, 与 R30a/O10 同源)。
    历史注记: 白名单层旧 R30b 执行点随 9/24 名单制废除消亡(9/8 电力设备 27.8% 剔 84 实证),
    本闸为其 R 闸层转世。westock/映射取数失败 → fail-open WARN(与 R25/R30a 同口径);
    --owner-approve-r30b 特批 → 降 WARN 留痕(默认关)。
    """
    def _d6(s) -> str:
        return "".join(ch for ch in str(s or "") if ch.isdigit()).zfill(6)[-6:]

    def _pref(c6: str) -> str:
        return ("sh" if c6.startswith(("600", "601", "603", "605", "688", "689"))
                else ("bj" if c6.startswith(("8", "920", "430", "4")) else "sz"))

    try:
        import sqlite3
        c6 = _d6(code)
        mcon = sqlite3.connect(
            r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro",
            uri=True, timeout=10)
        sec_map = {r[0]: (r[1] or "") for r in mcon.execute(
            "SELECT code, sector_name FROM stock_sector_sw1")}
        mcon.close()
        sec = (sector or "").strip() or sec_map.get(_pref(c6) + c6, "")
        if not sec:
            return {"status": "fail_open",
                    "note": "行业映射缺失(无--sector且stock_sector_sw1无该股) → fail-open标注"}
        if r"D:\workbuddyspace" not in sys.path:
            sys.path.insert(0, r"D:\workbuddyspace")
        from westock_client import WeStockClient
        ws = WeStockClient()
        pos = ((ws.positions().get("data") or {}).get("positions")) or []
        if total_cash:
            total = float(total_cash)
        else:
            # westock 合同坑(R25同族): 实测三层嵌套 data.portfolio.totalAssets——多口径兼容读取
            pf = ws.portfolio() or {}
            inner = ((pf.get("data") or {}).get("portfolio")) or pf.get("data") or pf
            total = float(inner.get("totalAssets") or inner.get("total_assets") or 0)
        if total <= 0:
            return {"status": "fail_open", "note": "账户总资产不可得(westock) → fail-open标注"}
        cluster_mv, n_in = 0.0, 0
        for p in pos:
            pc6 = _d6(p.get("code", ""))
            if not pc6 or sec_map.get(_pref(pc6) + pc6) != sec:
                continue
            px = float(p.get("currentPrice") or p.get("current_price") or p.get("lastPrice") or 0)
            q = float(p.get("quantity") or p.get("qty") or 0)
            if px > 0 and q > 0:
                cluster_mv += px * q
                n_in += 1
        notional_v = float(notional or 0)
        ratio_pre = cluster_mv / total
        ratio_post = (cluster_mv + notional_v) / total if notional_v > 0 else ratio_pre
        note = (f"申万L1[{sec}]持仓{n_in}只市值{cluster_mv:,.0f} / 总资产{total:,.0f} = {ratio_pre * 100:.1f}%"
                + (f"(含拟买{notional_v:,.0f}后{ratio_post * 100:.1f}%)" if notional_v > 0 else ""))
        if ratio_post > R30B_CLUSTER_CAP:
            if owner_approve:
                return {"status": "warn", "note": note + f" → 超上限{R30B_CLUSTER_CAP * 100:.0f}%但主人特批豁免, WARN留痕"}
            return {"status": "reject",
                    "note": note + f" → 超上限{R30B_CLUSTER_CAP * 100:.0f}% → 新买入REJECT(存量不动, PD-024③)"}
        if ratio_pre > 0.20:
            return {"status": "warn", "note": note + f" → 接近{R30B_CLUSTER_CAP * 100:.0f}%上限, 提示控制同簇加仓"}
        return {"status": "pass", "note": note}
    except Exception as e:
        return {"status": "fail_open",
                "note": f"数据读取失败({type(e).__name__}: {e}) → fail-open标注(与R25/R30a同口径)"}


R34_PULSE_INFLOW_MIN = 1_000_000_000  # PD-018②(10/2批准WARN档): T-1板块脉冲流入阈值10亿(天地在线案板块+18.76亿)
R35_RESELL_MIN_GAP_MIN = 30  # PD-018③(10/2批准REJECT硬闸): 卖后同日回补最小间隔分钟
R36_POS60 = 80   # PD-030②(10/3主人「全部落地」批准WARN档/影子): 60日区间位置阈值
R36_VOLR = 0.7   # 量/前5日均量<0.7=缩量(T-1口径)
R36_GENE10 = 1   # 近10日涨停(change_pct>=9.5)次数>=1=股性激活


def check_r34_pulse_nextday(code, sector="") -> dict:
    """R34 脉冲次日资金提示（PD-018② 2026-10-02 主人「继续」批准 WARN 档）:

    候选板块 T-1 净流入≥10亿(脉冲) → 次日买入 WARN「防一日游,须当日资金转正确认」。
    数据源=em_flow_snapshot.db(22:35采集,T-1收盘口径)按 stock_sector_sw1 聚合;
    当日实时资金本地不可得 → WARN 明示"未验证维度"(诚实口径; 单案例证据不足硬闸,R27b 同构,
    样本积累后评估升级)。天地在线002995案: 9/22广告营销+18.76亿脉冲→9/23买入即遇板块-3.18%净流出1.65亿。
    """
    try:
        import sqlite3
        c6 = "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(6)[-6:]
        pref = ("sh" if c6.startswith(("600", "601", "603", "605", "688", "689"))
                else ("bj" if c6.startswith(("8", "920", "430", "4")) else "sz"))
        mcon = sqlite3.connect(
            r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro",
            uri=True, timeout=10)
        sec = (sector or "").strip() or ""
        if not sec:
            row = mcon.execute("SELECT sector_name FROM stock_sector_sw1 WHERE code=?",
                               (pref + c6,)).fetchone()
            sec = row[0] if row and row[0] else ""
        if not sec:
            return {"status": "fail_open", "note": "行业映射缺失 → fail-open标注"}
        fcon = sqlite3.connect(r"file:D:\zcodesj\data\em_flow_snapshot.db?mode=ro",
                               uri=True, timeout=10)
        d = fcon.execute("SELECT MAX(trade_date) FROM flow_snapshot").fetchone()[0]
        if not d:
            return {"status": "fail_open", "note": "em_flow_snapshot 空 → fail-open标注"}
        rows = fcon.execute("SELECT code, em_today FROM flow_snapshot WHERE trade_date=?",
                            (d,)).fetchall()
        fcon.close()
        sec_map = {r[0]: (r[1] or "") for r in mcon.execute(
            "SELECT code, sector_name FROM stock_sector_sw1")}
        mcon.close()
        sec_sum, n = 0.0, 0
        for c, em in rows:
            # flow_snapshot.code=裸六位, sec_map键=带前缀(R30a/R30b同源)——必须归一化前缀再匹配,
            # 否则恒不命中=板块流入恒0静默失效(单测10/2抓出, 银行E63 sector列同族)
            c6f = "".join(ch for ch in str(c or "") if ch.isdigit()).zfill(6)[-6:]
            pf = ("sh" if c6f.startswith(("600", "601", "603", "605", "688", "689"))
                  else ("bj" if c6f.startswith(("8", "920", "430", "4")) else "sz"))
            if sec_map.get(pf + c6f) == sec and em is not None:
                sec_sum += float(em)
                n += 1
        if sec_sum >= R34_PULSE_INFLOW_MIN:
            return {"status": "warn",
                    "note": f"板块[{sec}]T-1({d})净流入{sec_sum / 1e8:.2f}亿≥10亿=脉冲次日买入 → "
                            f"防一日游,须当日资金转正确认(实时资金本地不可得,未验证维度;天地在线案同型)"}
        return {"status": "clear", "note": f"板块[{sec}]T-1({d})净流入{sec_sum / 1e8:.2f}亿 <10亿, 非脉冲"}
    except Exception as e:
        return {"status": "fail_open",
                "note": f"数据读取失败({type(e).__name__}: {e}) → fail-open标注"}


def check_r35_resell_cooldown(code, price=None, owner_approve=False, now=None) -> dict:
    """R35 卖后同日回补冷却闸（PD-018③ 2026-10-02 主人「继续」批准 REJECT 硬闸）:

    同码当日已有 SELL 成交 → 回补须「间隔≥30分钟 且 回补价<卖价」, 否则 REJECT
    （深圳瑞捷300977案: 9/23 13:53卖20.89→14:25买回20.82, 当日+11%违规追+反向T——
    已发生违规的机制化封锁; --owner-approve-r35 特批降 WARN, 同构默认关）。
    成交真源=attribution.trade_records(与纪律看板同源); created_at/价格缺失 → fail-open标注。
    """
    try:
        import sqlite3
        from datetime import datetime as _dt
        c6 = "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(6)[-6:]
        con = sqlite3.connect(r"file:D:\zcodesj\attribution\attribution.db", timeout=10, uri=True)
        rows = con.execute(
            "SELECT price, created_at FROM trade_records "
            "WHERE trade_date=? AND action='SELL' AND code LIKE ?",
            (_dt.now().strftime("%Y%m%d") if now is None else now.strftime("%Y%m%d"),
             f"%{c6}")).fetchall()
        con.close()
        parsed = []
        for p, ca in rows:
            try:
                parsed.append((float(p), _dt.fromisoformat(str(ca))))
            except Exception:
                continue
        if not rows:
            return {"status": "clear", "note": "当日无同码卖出记录"}
        if not parsed:
            return {"status": "fail_open", "note": "当日有卖出但created_at/价格不可解析 → fail-open标注"}
        last_px, last_t = max(parsed, key=lambda x: x[1])
        now_v = now or _dt.now()
        gap_min = (now_v - last_t).total_seconds() / 60.0
        if price is None:
            return {"status": "fail_open",
                    "note": f"当日{last_t.strftime('%H:%M')}已卖@{last_px} 但未提供买入价 → fail-open标注"}
        ok_gap = gap_min >= R35_RESELL_MIN_GAP_MIN
        ok_px = price < last_px
        if ok_gap and ok_px:
            return {"status": "clear",
                    "note": f"回补合规: 距卖{gap_min:.0f}分钟≥30 且 买价{price}<卖价{last_px}"}
        why = []
        if not ok_gap:
            why.append(f"距卖仅{gap_min:.0f}分钟<30")
        if not ok_px:
            why.append(f"买价{price}≥卖价{last_px}(反向T)")
        note = f"当日{last_t.strftime('%H:%M')}已卖@{last_px}, " + "∧".join(why)
        if owner_approve:
            return {"status": "warn", "note": note + " → 主人特批豁免, WARN留痕"}
        return {"status": "reject", "note": note + " → 回补冷却闸拒绝(PD-018③)"}
    except Exception as e:
        return {"status": "fail_open",
                "note": f"数据读取失败({type(e).__name__}: {e}) → fail-open标注"}


def check_r36_highpos_shrink(code6: str) -> dict:
    """R36 高位缩量接货提示（PD-030② 2026-10-03 主人「全部落地」批准 WARN 档/影子）:

    三条件合取（判定仅用 T-1 及更早完成 Bar, 盘中无前视）:
    pos60>=80(60日区间上沿) ∧ volr<0.7(量<前5日均量七成=缩量) ∧ 近10日涨停>=1(股性激活)
    → WARN「接盘陷阱」, 非阻断(R34 同口径)。
    证据=t4_highpos_shrink_study_20261003(490万根qfq, 2x2因子隔离): 合取种群 fwd20 中位
    -3.62%/win40.1%(单因子不足, 仅三合取成立); 她案力星/达实打板后-38%~-45% 同型;
    原话=两个小馒头「高位缩量, 介入容易接盘」。分年 5 年 4 负(2025 强势年唯一例外, 如实)。
    影子判据(PD-030② 预钉死): 命中种群 fwd20 中位<=-2% 且 win<=42%, >=60 交易日判读, 不利即废。
    """
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT date,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
            "WHERE code=? AND date < ? ORDER BY date DESC LIMIT 60",
            (code6, datetime.now().strftime("%Y%m%d"))).fetchall()
        con.close()
        if len(rows) < 60:
            return {"status": "fail_open",
                    "note": f"qfq有效Bar不足60(n={len(rows)}) → fail-open标注"}
        w = list(reversed(rows))  # 旧->新60根, 末根=T-1(最新完成Bar)
        hi = max(x[1] for x in w)
        lo = min(x[2] for x in w)
        if hi <= lo:
            return {"status": "fail_open", "note": "60日区间无效 → fail-open标注"}
        c_t1 = w[-1][3]
        pos60 = (c_t1 - lo) / (hi - lo) * 100
        vols = [x[4] for x in w]
        vol5 = sum(vols[-6:-1]) / 5.0
        if not vol5:
            return {"status": "fail_open", "note": "前5日均量无效 → fail-open标注"}
        volr = vols[-1] / vol5
        chgs = [x[5] for x in w[-10:]]
        gene10 = sum(1 for x in chgs if x is not None and x >= 9.5)
        if pos60 >= R36_POS60 and volr < R36_VOLR and gene10 >= R36_GENE10:
            return {"status": "warn", "pos60": pos60, "volr": volr, "gene10": gene10,
                    "note": f"高位缩量接货三合取: pos60={pos60:.0f}≥{R36_POS60} ∧ "
                            f"volr={volr:.2f}<{R36_VOLR} ∧ 近10日涨停{gene10}次≥{R36_GENE10} → "
                            f"接盘陷阱WARN(PD-030②影子; 种群fwd20中位-3.62%/win40.1%, "
                            f"她案力星/达实同型)"}
        return {"status": "clear", "pos60": pos60, "volr": volr, "gene10": gene10,
                "note": f"三合取未齐(pos60={pos60:.0f}/volr={volr:.2f}/基因{gene10})"}
    except Exception as e:
        return {"status": "fail_open",
                "note": f"qfq读取失败({type(e).__name__}: {e}) → fail-open标注"}


def check_rs5_momgate(code6: str, source: str, owner_approved: bool = False) -> dict:
    """R-S5 不追高风格闸（2026-09-05 主人裁决 A）：mom20(T-1收盘/20交易日前收盘-1) > +2% →

    会话选股源(manual_confirmed/mass_scan/full_market) REJECT——裁决 A 硬约束
    (STYLE-GATE-STAGE5: NW t=2.69/三年全正/执行级+9.2pp年; FULLHIST 8/10年正+regime哨兵);
    例外=--owner-approve-rs5 显式特批 → 降 WARN 并留痕(SOP: 特批须主人显式批准)。
    生产名单源(bp_ep/master_hist 等) → WARN(B 臂纸面对照, 生产语义不动, 9/30 A/B 首验后裁决)。
    qfq 不足 21 根/读取失败 → fail-open WARN(与 R31/R32 同口径)。
    """
    src = (source or "").strip()
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date < ? "
            "ORDER BY date DESC LIMIT 21",
            (code6, datetime.now().strftime("%Y%m%d"))).fetchall()
        con.close()
        closes = [r[0] for r in rows if r[0] is not None]
        if len(closes) < 21 or not closes[20] or closes[20] <= 0:
            return {"status": "fail_open",
                    "note": f"qfq 有效收盘不足21日(n={len(closes)}) → fail-open标注"}
        mom20 = closes[0] / closes[20] - 1
        if mom20 <= RS5_MOM_REJECT:
            return {"status": "clear", "mom20": mom20,
                    "note": f"mom20={mom20*100:+.1f}% ≤ +2% 不追高闸通过"}
        hit = f"mom20={mom20*100:+.1f}% > +2% 不追高闸(STYLE-GATE-STAGE5)"
        # 2026-09-08 主人裁决「涨停的拦在外面说明我们还是不对」: 会话源 REJECT 分支撤销,
        # 全源统一 WARN 留痕(强势股不再因 mom20 被拒; 尾部风险由留痕+仓位管理承接);
        # --owner-approve-rs5 旗标保留兼容(不再必要)。
        return {"status": "warn", "mom20": mom20,
                "note": f"{hit} → WARN留痕(9/8主人裁决撤销REJECT分支)"}
    except Exception as e:
        return {"status": "fail_open", "note": f"qfq 读取失败({type(e).__name__}) → fail-open标注"}


def check(args) -> dict:
    """R1-R18 全量闸门 + R19 解禁回避 + R22 双口径资金闸（2026-08-18 热修误截断后恢复重建）。

    R22 段：读本地逐日累积库 em_flow_snapshot.db（22:35 cron 采集维护），
    EM与THS双口径方向相反 → REJECT；双负 → WARN；同正 → PASS（含停板日 → WARN 标注）。
    """
    code = norm_code(args.code)
    rejects: list[str] = []
    warns: list[str] = []
    board = board_of(code)
    if load_state is None:
        # 20261011批4: 防线模块导入失败此前零留痕(R13重复买入闸/R8当日状态静默失效)。
        # 保持不阻断(executor 台账查重可部分兜底)但必须响亮留痕。
        print("⚠️ [R13/R8] trade_state 模块不可用 → 重复买入/当日状态防线降级(导入链请巡检)",
              file=sys.stderr)
        state = {}
    else:
        state = load_state()

    # ---- 铁律1: 板块排除（科创板/北交所永不参与）----
    if board in ("科创板", "北交所"):
        rejects.append(f"铁律: {board}({code}) 在排除清单中")
    if board == "未知":
        rejects.append(f"铁律: 代码 {code} 无法识别板块 → fail-closed 拒绝")
    if args.is_st:
        rejects.append("铁律: ST/*ST 一律不碰")
    # ---- ST/板块自动检测（2026-09-08 主人口径「ST股不做，科创板不做」; 白名单A/B实验配套）----
    # 此前 ST 仅靠 --is-st 手工传参: B臂(ab_freeroute)名单豁免绕过白名单 name_ok 过滤后
    # ST 形同虚设——改为名称库自动检测, 对全源生效(白名单内已滤不变; manual 亦不可碰 ST)。
    _auto_c = args.code.strip().lower()
    for _p in ("sh", "sz", "bj"):
        if _auto_c.startswith(_p) and len(_auto_c) > 2 and _auto_c[2:3].isdigit():
            _auto_c = _auto_c[2:]
            break
    _auto6 = _auto_c.zfill(6)
    if _auto6.startswith(("688", "8", "4", "92")):
        rejects.append("铁律: 科创板/北交所不在交易范围(自动检测)")
    else:
        try:
            from stock_name_lookup import get_name as _gn
            _nm = _gn(_auto6) or ""
            if "ST" in _nm.upper():
                rejects.append(f"铁律: ST/*ST 一律不碰(自动检测: {_auto6} {_nm})")
        except Exception:
            pass  # 名称库不可用 fail-open, --is-st 手工参数仍在

    # ---- 铁律2: 价格上限（主人8/13裁定30元上限；10/9主人特批准入逐笔豁免旗）----
    if args.price is not None and args.price > MAX_PRICE:
        if getattr(args, "owner_approve_price_cap", False):
            warns.append(f"S-3价格铁律[owner-approve]: 股价 {args.price} > {MAX_PRICE} 元上限, "
                         f"主人特批放行(WARN留痕)")
        else:
            rejects.append(f"铁律: 股价 {args.price} > {MAX_PRICE} 元上限(中恒电气44.77案例)")

    # ---- 铁律3: 持仓上限 [2026-10-26 主人令废除] ----
    # 「从此不再限定模拟盘持仓的持股数量」——原 ≤50 只硬拒拆除(_common.MAX_HOLDINGS 同废);
    # --holdings 参数保留仅作信息透传, 不再参与任何判定。

    # ---- admit 闸门: 信号源准入（8/13修复白名单空矛盾，PROVEN_EDGE_SOURCES 已配置）----
    # 2026-11-03 暂行方法准入前置闸(主人令七领跑组暂行, 与 executor _BUY_SRC_ALLOWED 双闸同源):
    # 仅 METHOD_ADMISSION_INTERIM_SOURCES 七组+manual_confirmed 可过, 其余已登记源一律影子观察(拒单)。
    src = (args.source or "").strip()
    tag = "normal"
    if not src:
        rejects.append("admit闸门: 未声明信号源 source → 拒绝")
    elif src != "manual_confirmed" and src not in METHOD_ADMISSION_INTERIM_SOURCES:
        rejects.append(f"admit闸门: 暂行方法准入(2026-11-03主人令七领跑组暂行)不含 {src} "
                       f"→ 拒绝(该源信号转入影子观察, 复审点11/26赛马首考)")
    elif src in RETIRED_SOURCES:
        rejects.append(f"admit闸门: 信号源 {src} 已证伪/退役 → 拒绝")
    elif src in OBSERVE_SOURCES:
        rejects.append(f"admit闸门: 信号源 {src} 赛马观察名单(停止新开仓, 降风险非证伪) → 拒绝")
    elif src in PROVEN_EDGE_SOURCES:
        pass
    elif src in EXPERIMENTAL_SOURCES:
        tag = "experiment"
        warns.append(f"信号源 {src} 处于验证中 → 强制 tag=experiment（源分级标记；仓位权重上限已按 2026-10-26 主人令废除）")
    else:
        rejects.append(f"admit闸门: 信号源 {src} 不在白名单且非已知实验源 → 拒绝")

    # ---- R26 名单对照 / R27 名单价偏离（2026-09-24 主人令「全面废除TopN名单限制」退役）----
    # 原两块逻辑(名单内外 WARN 对照 + 执行价 vs 名单价>3% REJECT)整体移除——白名单不再是任何
    # 判定的输入。防溢价追买由 R27b(昨收溢价 WARN)/R28(hotspot 追价 REJECT)/R31(ext20)/R32(chg20)
    # 名单无关闸独立承担; 准入由 admit 闸门信号源判定。档案: SOP 2026-09-24 条目。

    # ---- R28 hotspot 当日禁追价（E34: 湘潭电化/盟固利 8/25 追价买入即跌, 2026-08-26收口）----
    if src == "hotspot_breakout":
        if args.price is not None and args.prev_close is not None and args.prev_close > 0:
            _chase = (args.price - args.prev_close) / args.prev_close
            if _chase > R28_HOTSPOT_CHASE_MAX:
                rejects.append(f"R28 hotspot追价: 执行价{args.price} vs 信号日收盘{args.prev_close} "
                               f"高出{_chase*100:.1f}% > {R28_HOTSPOT_CHASE_MAX*100:.0f}% → 禁追价, 拒绝")
        else:
            warns.append("R28 hotspot追价: 未提供 --price/--prev-close(信号日收盘), 无法校验追价 → fail-open标注")

    # ---- R27b 昨收溢价提示（2026-08-28, 与R28互补: 无专属追价闸的源防追价）----
    # 取证: >3% 硬闸判据未达成(8月溢价>3%组 T+1 均值-0.12% 不劣于低溢价组) → 不上硬闸;
    # >5% 子组弱负 → WARN 提示, 不改变 verdict（建设机械600984 8/26 高开+5.6% 追入案）
    if src != "hotspot_breakout" and args.price is not None and args.prev_close is not None and args.prev_close > 0:
        _prem = (args.price - args.prev_close) / args.prev_close
        if _prem > R27B_PREMIUM_WARN:
            warns.append(f"R27b昨收溢价: 执行价{args.price} vs 昨收{args.prev_close} "
                         f"高{_prem*100:.1f}% > {R27B_PREMIUM_WARN*100:.0f}% → 追价提示"
                         f"(建设机械600984 8/26 +5.6% 案同型)")

    # ---- R31 个股偏离度（K3, 2026-08-28 立项：qfq 全史1064万截面 ext20 阈值单调恶化证据）----
    r31 = check_r31_extension(code, args.price,
                              owner_approved=bool(getattr(args, "owner_approve_r31", False)))
    if r31["status"] == "reject":
        rejects.append(f"R31偏离度: {r31['note']}")
    elif r31["status"] == "warn":
        warns.append(f"R31偏离度: {r31['note']}")
    elif r31["status"] == "fail_open":
        warns.append(f"R31偏离度[{r31['status']}]: {r31['note']}")

    # ---- R32 高位动量禁买（O-B, 2026-08-29：chg20≥30% 全时段全源 REJECT，涨幅动量维度）----
    r32 = check_r32_chgmom(code, price=args.price, prev_close=args.prev_close)
    if r32["status"] == "reject":
        rejects.append(f"R32高位动量: {r32['note']}")
    elif r32["status"] in ("warn", "fail_open"):
        # 2026-09-10: 原实现 warn 一律误标「P1-3派发」——chg20≥30% 主分支(9/8 裁决降WARN)
        # 与派发分支共用 warn 状态, 9/30 A/B 首验归因口径污染; 以 day_drop 键区分子类型
        _sub = ("fail_open" if r32["status"] == "fail_open"
                else ("P1-3派发" if "day_drop" in r32 else "高位动量"))
        warns.append(f"R32高位动量[{_sub}]: {r32['note']}")

    # ---- R37 情绪档择时门（PD-034 layer3, 2026-10-07 主人模拟盘实战验证授权）----
    r37 = check_r37_emotion_gear(owner_approved=bool(getattr(args, "owner_approve_r37", False)))
    if r37["status"] == "reject":
        rejects.append(f"R37情绪档: {r37['note']}")
    elif r37["status"] in ("warn", "fail_open"):
        warns.append(f"R37情绪档[{r37['status']}]: {r37['note']}")

    # ---- R38 期望闸（2026-10-08 主人授权落地: gauge组级负期望×弱档=REJECT, 判定结果进买入资格链）----
    r38 = check_r38_expectancy(code, owner_approved=bool(getattr(args, "owner_approve_r38", False)))
    if r38["status"] == "reject":
        rejects.append(f"R38期望闸: {r38['note']}")
    elif r38["status"] in ("warn", "fail_open"):
        warns.append(f"R38期望闸[{r38['status']}]: {r38['note']}")

    # ---- R39 落刀闸（2026-10-08: 个股开盘跳空≤-3%/现价深跌≤-5%, 腾讯真实行情源, 非模拟盘报价）----
    r39 = check_r39_fallingknife(code, owner_approved=bool(getattr(args, "owner_approve_r39", False)))
    if r39["status"] == "reject":
        rejects.append(f"R39落刀闸: {r39['note']}")
    elif r39["status"] in ("warn", "fail_open"):
        warns.append(f"R39落刀闸[{r39['status']}]: {r39['note']}")

    # ---- R41 主线对齐闸（2026-10-08 WARN-only提请制: 方向层C1主线集, 合同=视野非过滤器不拦截）----
    r41 = check_r41_mainline(getattr(args, "sector", None))
    if r41["status"] == "reject":
        rejects.append(f"R41主线对齐: {r41['note']}")
    elif r41["status"] in ("warn", "fail_open"):
        warns.append(f"R41主线对齐[{r41['status']}]: {r41['note']}")

    # ---- R-S5 不追高风格闸（2026-09-05 主人裁决 A：mom20>+2% 会话源 REJECT/生产源 WARN）----
    rs5 = check_rs5_momgate(code, getattr(args, "source", "") or "",
                            owner_approved=bool(getattr(args, "owner_approve_rs5", False)))
    if rs5["status"] == "reject":
        rejects.append(f"R-S5不追高: {rs5['note']}")
    elif rs5["status"] in ("warn", "fail_open"):
        warns.append(f"R-S5不追高[{rs5['status']}]: {rs5['note']}")

    # ---- R1 追高（60日分位维度）——2026-09-10 退役(结构性空转下线) ----
    # 退役依据: executor(唯一买入入口)无 --late-stage/--limit-up-buy 参数、计划 JSON 无对应
    # 字段, 三条件判定(60日分位>70%+非涨停价买+主升末端)自 8/22 生产切换以来累计 0 次命中,
    # 仅产出"未提供 --pct-60d"噪音 WARN; 追高拦截由 R-S5/R31/R32/R7 有证据闸承担
    # (R6 证据研究 2026-09-10 同批: 无证据硬闸降级或下线)。--pct-60d 等参数保留供研究脚本
    # 兼容, 不再参与判定。git bb42eb9 前档案=SOP 2026-09-10(三)条。

    # ---- R3 批量买入（当日已买只数，trade_state 载体；提示口径，硬上限在 execution_guard）----
    buy_count = int(state.get("buy_count", 0) or 0)
    if buy_count > MAX_BUY_PER_DAY:
        rejects.append(f"R3: 当日累计买入{buy_count}只 > {MAX_BUY_PER_DAY} 上限 → 拒绝")
    elif buy_count >= 4:
        warns.append(f"R3批量买提示: 当日已买{buy_count}只(>3)，建议控制批量")

    # ---- R30a 行业集中度: 当日同行业新买上限（E63/E79, 2026-08-28 落闸; 2026-08-28 修复）----
    # 组合级占比上限(R30b)=check_r30b_cluster 本文件 R 闸层(2026-10-01 PD-024② 转实, 原 09:10 白名单层
    # 执行点随名单制废除消亡); 本闸管"当日新买"维度。2026-08-28 修复: 原实现按 trade_records.sector 列计数, 而该列
    # 8/26 起全空(executor 未落 a.sector)→ 银行7只E63场景恒欠计为0形同虚设; 改为行业映射
    # 唯一真源 stock_sector_sw1(dragon_quant_meta.db, 与 O10/R-20260827-03 同源) 推导, 对
    # 当日每笔 BUY 反查行业再计数; 双约束: 同行业≥4只 或 (当日总量≥4 且 同行业占比>25%) → REJECT。
    sector_arg = (args.sector or "").strip()
    try:
        import sqlite3 as _sq
        _con = _sq.connect(r"D:\zcodesj\attribution\attribution.db", timeout=10)
        _today_buys = [r[0] for r in _con.execute(
            "SELECT code FROM trade_records WHERE trade_date=? AND action='BUY'",
            (datetime.now().strftime("%Y%m%d"),))]
        _con.close()
        _mcon = _sq.connect(
            r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro",
            uri=True, timeout=10)
        def _sector_of(c: str) -> str:
            c6 = "".join(ch for ch in c if ch.isdigit()).zfill(6)[-6:]
            pref = ("sh" if c6.startswith(("600", "601", "603", "605", "688", "689"))
                    else ("bj" if c6.startswith(("8", "920", "430", "4")) else "sz"))
            r = _mcon.execute("SELECT sector_name FROM stock_sector_sw1 WHERE code=?",
                              (pref + c6,)).fetchone()
            return (r[0] if r and r[0] else "")
        _target_sector = sector_arg or _sector_of(code)
        if _target_sector:
            _same = sum(1 for c in _today_buys
                        if "".join(ch for ch in c if ch.isdigit())[-6:] !=
                        "".join(ch for ch in code if ch.isdigit())[-6:]
                        and _sector_of(c) == _target_sector)
            _mcon.close()  # 2026-08-29 修复: 原 close 在 _sector_of 首次调用前, 已关闭连接 execute
                           # → ProgrammingError 恒 fail-open(银行7只E63场景形同虚设复发); 移至末次调用后
            _ratio = _same / len(_today_buys) if _today_buys else 0.0
            if _same >= R30_SAME_DAY_SECTOR_CAP or (
                    len(_today_buys) >= 4 and _ratio > 0.25):
                # 2026-09-10: 主人特批豁免口(与R-S5/R6/R31同构)——逐笔特批降WARN留痕, 默认关
                if getattr(args, "owner_approve_r30a", False):
                    warns.append(
                        f"R30a行业集中度[owner-approve]: 当日同行业({_target_sector})已新买{_same}只"
                        f"(占比{_ratio*100:.0f}%/总量{len(_today_buys)}) "
                        f"≥ 上限{R30_SAME_DAY_SECTOR_CAP}只或>25% → 主人特批豁免, WARN留痕")
                else:
                    rejects.append(
                        f"R30a行业集中度: 当日同行业({_target_sector})已新买{_same}只"
                        f"(占比{_ratio*100:.0f}%/总量{len(_today_buys)}) "
                        f"≥ 上限{R30_SAME_DAY_SECTOR_CAP}只或>25% → 拒绝(E79)")
            elif _same >= 3:
                warns.append(f"R30a行业集中度: 当日同行业({_target_sector})已新买{_same}只, 接近上限")
        else:
            _mcon.close()
            warns.append("R30a行业集中度: 行业映射缺失(无--sector且stock_sector_sw1无该股) → fail-open标注")
    except Exception as _e:
        warns.append(f"R30a行业集中度: 数据读取失败({type(_e).__name__}) → fail-open标注")

    # ---- R30b 组合级行业占比上限 30%（PD-024③ 2026-10-23 落闸: 25→30, 证据=全域回测R6剂量-反应; R30a 管当日新买维度, 本闸管存量维度）----
    _r30b = check_r30b_cluster(code, sector_arg, getattr(args, "amount", None),
                               getattr(args, "total_cash", None),
                               owner_approve=bool(getattr(args, "owner_approve_r30b", False)))
    if _r30b["status"] == "reject":
        rejects.append(f"R30b组合行业占比: {_r30b['note']}")
    elif _r30b["status"] in ("warn", "fail_open"):
        warns.append(f"R30b组合行业占比[{_r30b['status']}]: {_r30b['note']}")

    # ---- R34 脉冲次日资金提示（PD-018② 2026-10-02 批准 WARN 档; 单例证据不足硬闸, R27b 同构）----
    _r34 = check_r34_pulse_nextday(code, sector_arg)
    if _r34["status"] in ("warn", "fail_open"):
        warns.append(f"R34脉冲次日资金[{_r34['status']}]: {_r34['note']}")

    # ---- R35 卖后同日回补冷却闸（PD-018③ 2026-10-02 批准 REJECT 硬闸; 瑞捷反向T违规机制化封锁）----
    _r35 = check_r35_resell_cooldown(code, getattr(args, "price", None),
                                     owner_approve=bool(getattr(args, "owner_approve_r35", False)))
    if _r35["status"] == "reject":
        rejects.append(f"R35回补冷却闸: {_r35['note']}")
    elif _r35["status"] in ("warn", "fail_open"):
        warns.append(f"R35回补冷却闸[{_r35['status']}]: {_r35['note']}")

    # ---- R36 高位缩量接货提示（PD-030② 2026-10-03 主人「全部落地」批准 WARN 档/影子; 非阻断）----
    _r36 = check_r36_highpos_shrink(code)
    if _r36["status"] in ("warn", "fail_open"):
        warns.append(f"R36高位缩量[{_r36['status']}]: {_r36['note']}")

    # ---- DD_GATE 回撤门控: 防御/清仓档冻结新买入（2026-09-15 胜率提升五方案·方案3, 主人令"五个方案都干"落闸）----
    # 数据源 = data/ddprotect_gate_state.json（schtasks DDProtectGate_0800 每日 08:00 刷新, hyst2% 规则）。
    # 语义: gate 含"防御"/"清仓" → 新买入 REJECT（实证: 本周两笔最大错误买入[9/11糖链加仓/9/15弘信]均为防御/弱档日逆势）;
    # 豁免旗标 --owner-approve-ddgate 降 WARN 留痕（与 R-S5/R6/R30a 完全同构, 默认关）;
    # state 缺失/日期非今日/解析失败 → fail-open 显式标注（与 R19/R22/R30a 同口径）。
    # 只拦 BUY 新开仓; 卖出退出（S-5 止损/趋势走坏/主人指令）不经本检查, 不受本闸影响。
    try:
        import json as _json
        _dd_path = r"D:\zcodesj\data\ddprotect_gate_state.json"
        with open(_dd_path, encoding="utf-8") as _f:
            _dd = _json.load(_f)
        _dd_gate = str(_dd.get("gate") or "")
        # 新鲜度判定用 computed_at(运行时间戳, 08:00任务用T-1收盘数据故 date 字段=昨日); 兜底 date
        _dd_date = (str(_dd.get("computed_at") or "")[:10]
                    or str(_dd.get("date") or ""))
        if _dd_date != datetime.now().strftime("%Y-%m-%d"):
            warns.append(f"DD_GATE回撤门控: state日期{_dd_date}非今日 → fail-open标注(DDProtectGate_0800每日刷新)")
        elif ("防御" in _dd_gate) or ("清" in _dd_gate):
            if getattr(args, "owner_approve_ddgate", False):
                warns.append(
                    f"DD_GATE回撤门控[owner-approve]: 当前gate={_dd_gate} → 主人特批豁免, WARN留痕(逆门控开仓风险自知)")
            else:
                rejects.append(
                    f"DD_GATE回撤门控: 当前gate={_dd_gate}(指数跌破MA20回撤保护期) → 新买入拒绝; "
                    f"主人特批可用 --owner-approve-ddgate; 止损/卖出退出不受本闸影响")
        else:
            warns.append(f"DD_GATE回撤门控: 当前gate={_dd_gate} → 允许新买入")
    except Exception as _e:
        warns.append(f"DD_GATE回撤门控: state读取失败({type(_e).__name__}) → fail-open标注")

    # ---- R5 振幅（8/14十年回测WRONG → 仅提示不拦截）----
    if args.amplitude_40d is not None and args.amplitude_40d > 0.50:
        warns.append(f"R5振幅: 40日振幅{args.amplitude_40d*100:.1f}% > 50% → 高波动风险（8/14复核十年回测仅提示）")

    # ---- R6 收在高点（2026-09-10 证据研究降 WARN: 全史 983 万行 T+1 开盘入场事件研究,
    # pos≥0.7 组 fwd5 超额仅 -0.075pp(t≈-0.91)/fwd10 -0.125pp(t≈-0.34), 五分位虽单调
    # (Q0+0.47%→Q4+0.24%)但效应量微弱, 达不到 REJECT 级证据(R31 ext20 -1.00%/胜率37%
    # 才配硬闸); 9/10 00:45 手工批 8/9 被拦与效应量严重不匹配=过度拦截 → 降 WARN 留痕,
    # 与 9/8 R32 降 WARN 同构; 证据档案=data/_r6_caliber_evidence_20260910.json;
    # intent=low_suck 低吸豁免保留) ----
    if (args.close is not None and args.high is not None and args.low is not None
            and args.high > args.low and (args.intent or "") != "low_suck"):
        pos = (args.close - args.low) / (args.high - args.low)
        if pos >= 0.70:
            warns.append(f"R6收在高点: 价格{args.close} 位于快照区间{pos*100:.0f}%（≥70%; "
                         f"盘前快照=T-1区间/盘中=当日区间）→ 追高形态WARN留痕"
                         f"(2026-09-10证据研究降级, 证据弱于R31)")

    # ---- R7 超买/涨停价追入（8/12宏桥/达实案例：涨停封板买入T+1无安全边际）----
    r7_price_gain = None
    if args.price is not None and args.prev_close is not None and args.prev_close > 0:
        lim = get_price_limit(code, args.is_st)
        r7_price_gain = (args.price - args.prev_close) / args.prev_close
        if r7_price_gain >= lim * 0.95:
            rejects.append(
                f"R7超买拦截(涨停价追入): 买入价{args.price} vs 昨收{args.prev_close}，"
                f"涨幅{r7_price_gain*100:.1f}% ≥ 涨停线{lim*100:.0f}%的95% → 拒绝 "
                f"(涨停封板买入T+1无安全边际；8/12宏桥/达实案例)"
            )
        elif r7_price_gain >= lim * 0.80:
            rejects.append(
                f"R7超买拦截(高位追入): 买入价涨幅{r7_price_gain*100:.1f}% ≥ 涨停线{lim*100:.0f}%的80%分位 → 拒绝 "
                f"(接近涨停，安全边际不足)"
            )

    # ---- R8 分化日（板块宽度<20%，当日新开仓限2只）----
    breadth = args.sector_breadth if args.sector_breadth is not None else state.get("sector_breadth")
    if breadth is not None and breadth < DIVERGENT_BREADTH:
        warns.append(f"R8分化日: 板块宽度{breadth*100:.1f}% < 20% → 分化日, 当日新开仓限2只")

    # ---- R10 单笔金额上限 [2026-10-26 主人令废除] ----
    # 「不再限定模拟盘持仓的仓位大小」——单票≤20%总资产硬拒拆除(gen_gate_registry 登记RETIRED);
    # G10(strategy_gate) 维持 8/17 主人裁决的纯提示位不受影响。

    # ---- R13 重复买入（8/13 高德红外 9:34/9:36 双单7200股事故防线）----
    if code in (state.get("bought_codes") or []) and not getattr(args, "allow_add_position", False):
        rejects.append("R13重复买入: 该标的本日已买入过, 重复下单违规(高德红外案例) → 拒绝")

    # ---- R23 持仓加仓需显式确认（8/20 TCL科技裸奔加仓案：加仓=买入必过闸门）----
    # R13 只查"当日已买"，不识别"长期已持仓"的加仓。此处用 westock 实时持仓判定：
    # 已持仓标的若无 --allow-add-position 显式声明加仓意图 → REJECT（fail-closed 语义）。
    # 拉取失败/无库时返回空集 → 该条不触发（fail-open 标注），但 admit/R13 等仍独立把关。
    if not getattr(args, "allow_add_position", False) and not getattr(args, "skip_position_check", False):
        held = _held_codes()
        if held:
            _code_l = str(code).lower()
            if any(_code_l in h or h in _code_l for h in held):
                rejects.append(
                    f"R23持仓加仓: {code} 当前在持仓中, 加仓须显式 --allow-add-position 确认"
                    "（8/20 TCL科技案: 加仓未过闸门当日浮亏-1.78%）→ 拒绝")

    # ---- R14-R18 结构性空转六闸(连板/炸板/换手/退潮/极端结构)——2026-09-10 退役(下线) ----
    # 退役依据(2026-09-10 四域审计实锤): executor 无 --consecutive-boards/--seal-break-count/
    # --turnover-rate 参数, trade_state 市场字段(market_env/highest_board_*/limit_up_count/
    # advance_ratio)全项目无生产写入者, R14/R15/R16/R17 累计 REJECT=0 次(guard_precheck_log
    # 全史), R18 自 8/14 旧语义后 0 次——六闸为虚假防线面, 违反"禁止虚假防线"宪法原则。
    # 保护意图由现役有证据闸覆盖: 崩盘/极端结构→R25(2026-09-10 键名修复恢复); 追高/连板
    # →R-S5/R31/R32/R7; 集中度→R30a。CLI 参数保留供研究脚本(backtest_gates_vs_no_gates/
    # 回归测试)兼容, 不再参与判定。档案=SOP 2026-09-10(三)条 + git bb42eb9。

    # ---- R25 大盘择时闸（2026-08-23 S-9: 崩盘日禁止买入）----
    sh_pct = getattr(args, "sh_index_pct", None)
    cyb_pct = getattr(args, "cyb_index_pct", None)
    if sh_pct is not None and cyb_pct is not None:
        if sh_pct <= MARKET_CRASH_SH_THRESHOLD and cyb_pct <= MARKET_CRASH_CYB_THRESHOLD:
            rejects.append(
                f"R25大盘择时: 上证{sh_pct:+.2f}%≤{MARKET_CRASH_SH_THRESHOLD}% 且 "
                f"创业板{cyb_pct:+.2f}%≤{MARKET_CRASH_CYB_THRESHOLD}% → 系统性风险日禁止买入"
            )
        elif sh_pct <= MARKET_CRASH_SH_THRESHOLD or cyb_pct <= MARKET_CRASH_CYB_THRESHOLD:
            warns.append(
                f"R25大盘择时提示: 上证{sh_pct:+.2f}%/创业板{cyb_pct:+.2f}% → "
                f"单指数跌超阈值, 谨慎买入"
            )

    # ---- P2 弱开局窗口闸（2026-10-13 S3 落地, 主人「按推荐继续」批准）----
    p2_sz = getattr(args, "szse_index_pct", None)
    p2_cyb = getattr(args, "cyb_index_pct", None)
    if p2_sz is None and p2_cyb is None:
        p2_sz, p2_cyb = _fetch_szse_cyb_pct()  # 自取数; 失败=None → skip_nodata(fail-open)
    p2_v = p2_weak_open_verdict(p2_sz, p2_cyb, datetime.now().time(),
                                getattr(args, "owner_approve_p2", False))
    if p2_v == "REJECT":
        rejects.append(
            f"P2弱开局窗口: 深成{p2_sz:+.2f}%/创业板{p2_cyb:+.2f}% ≤{P2_WEAK_OPEN_THRESHOLD:.1f}% "
            f"且未过{P2_WINDOW_END} → 弱开局禁抢跑(豁免=--owner-approve-p2)")
    elif p2_v == "WARN":
        warns.append(
            f"P2弱开局提示: 深成{p2_sz:+.2f}%/创业板{p2_cyb:+.2f}% 偏弱开局(<{P2_WINDOW_END}), 谨慎买入")
    elif p2_v in ("skip_nodata",):
        warns.append("P2弱开局窗口[fail-open显式标注]: 指数取数失败 → 本闸不判定(通道巡检=health_check)")
    elif p2_v == "skip_owner":
        warns.append("P2弱开局窗口: 主人特批豁免留痕")

    # ---- R19 解禁回避（独立查询函数，无数据 fail-open 显式标注）----
    r19 = check_r19_restricted(code)
    if r19["status"] == "hit":
        w = r19.get("worst", {})
        ratio = (w.get("pct_of_float_mv") or 0) * 100
        rejects.append(f"R19解禁回避: 未来20日内解禁市值占流通{ratio:.1f}% > 3% → 拒绝")
    elif r19["status"] == "warn":
        warns.append(f"R19解禁关注: {r19['note']}")
    elif r19["status"] in ("no_data", "ratio_missing"):
        warns.append(f"R19解禁检查[fail-open显式标注]: {r19['note']} → PASS（数据由 event_data_tools.py --refresh 维护）")
    # 2026-09-10: clear(窗口内有解禁但比例极低=真通过)原被 else 分支混入 WARN 且误标
    # fail-open → PASS_WITH_WARN 通胀; clear 不入 warnings

    # ---- R22 双口径资金闸（EM vs 同花顺，方向相反→REJECT；无双口径不输出"资金强"结论）----
    r22 = check_r22_dual_caliber(code)
    if r22["status"] == "reject":
        rejects.append(f"R22双口径闸: {r22['note']}")
    elif r22["status"] in ("warn", "stale"):
        warns.append(f"R22双口径闸: {r22['note']}")
    elif r22["status"] == "no_data":
        warns.append(f"R22双口径闸[fail-open显式标注]: {r22['note']}")

    # ---- R24 板块高潮禁追（申万L1单日≥5% → 高潮后首个交易日禁买；缺数据fail-open显式标注）----
    r24 = check_r24_high_climax(getattr(args, "sector", "") or "")
    if r24["status"] == "reject":
        rejects.append(f"R24板块高潮禁追: {r24['note']}")
    elif r24["status"] in ("no_sector", "no_snapshot", "fail_open"):
        warns.append(f"R24板块高潮禁追[{r24['status']}]: {r24['note']}")
    # 2026-09-10: clear(板块非高潮=真通过)原被 else 分支计入 WARN → PASS_WITH_WARN 通胀,
    # 真警告被淹没; clear 不入 warnings

    # ---- R33 资金活跃度负面清单（2026-09-24 主人令「接入」; 影子未满提前提产, FAIL即提请撤闸）----
    r33 = check_r33_activity_excl(code)
    if r33["status"] == "reject":
        rejects.append(f"R33活跃度负面清单: {r33['note']}")
    elif r33["status"] in ("stale", "no_data"):
        warns.append(f"R33活跃度负面清单[{r33['status']}]: {r33['note']}")
    # clear(不在清单)=真通过, 不入warnings(同R19/R24防通胀口径)

    # ---- R42 商誉高位排雷WARN（2026-10-25 主人令「排雷入R闸」; WARN级不拦截）----
    r42 = check_r42_goodwill_high(code)
    if r42["status"] == "warn":
        warns.append(f"R42商誉高位排雷: {r42['note']}")
    # clear/no_data 不入warnings(防通胀)

    # ---- R43 大宗异常排雷WARN（2026-10-25 主人令「排雷入R闸」; WARN级不拦截）----
    r43 = check_r43_block_trade_anomaly(code)
    if r43["status"] == "warn":
        warns.append(f"R43大宗异常排雷: {r43['note']}")
    # clear/no_data 不入warnings(防通胀)

    # ---- R44 ST状态硬闸（2026-10-26 联创光电漏网修复; REJECT级=ST股禁买铁律）----
    r44 = check_r44_st_status(code)
    if r44["status"] == "reject":
        rejects.append(f"R44ST状态硬闸: {r44['note']}")
    # clear/no_data 不入任何列表(fail-open)

    # ---- 仓位与风险预算 [2026-10-26 主人令废除] ----
    # 原 sizing=按权重帽(正常源20%/实验源10%)×总资产折算建议股数, 属仓位大小限定, 整块拆除;
    # 下单金额/股数由 executor 侧显式 --amount/--volume 决定(无默认 sizing)。
    # 止损参考价不受影响: -8% 硬止损(FIXED_STOP)由 stop_loss_executor 链独立承载。
    sizing = {}

    # ---- 涨跌停参考价 ----
    lim_info = {}
    if args.prev_close:
        up, down = limit_prices(code, args.prev_close, args.is_st)
        lim_info = {
            "limit_pct": get_price_limit(code, args.is_st),
            "limit_up_price": up,
            "limit_down_price": down,
        }

    # 2026-08-29 强制列3反对理由（文章借鉴：策略批判者角色，非阻断，仅补充证据供人工/复盘参考）
    # 2026-08-29 当日修复: 原首两条把 bool(chasing)/标量(r7_price_gain) 误当 dict——
    # .get() 必 AttributeError 使闸门整体崩溃, executor 侧所有带 price/prev_close 的买入
    # 被判"闸门异常REJECT"(阻断级, 实测复现于山金国际 dry-run); 且闸门 args 无 20日/5日
    # 涨幅入参, 这两条 objection 本无数据支撑, 按反臆想原则删除(追高已有 R7/K3 正式拦截)。
    # 2026-09-10: chasing_high 输出字段随 R1 退役移除(全仓无消费者, grep 实证)。
    objections = []
    # 20261011批5: tier=0.25x 死分支删除——sizing 从不含 tier 键(降档机制移除后残留,
    # 分支永假); agent审计实证。
    if lim_info and lim_info.get("limit_up_price") and args.price:
        if args.price >= lim_info["limit_up_price"] * 0.98:
            objections.append("接近涨停价（≥98%），排队不可控")
    if len(objections) < 3:
        objections.append(f"当前仅{len(objections)}条反对理由，需人工补充")

    return {
        "code": code,
        "name": args.name or "(未提供名称-违反真实数据原则,必须补齐)",
        "board": board,
        "source": src,
        "tag": tag,
        "verdict": "REJECT" if rejects else ("PASS_WITH_WARN" if warns else "PASS"),
        "rejects": rejects,
        "warnings": warns,
        "objections": objections,  # 强制列3反对理由（非阻断，补充证据）
        "r7_price_gain": r7_price_gain,
        "sizing": sizing,
        "price_limit": lim_info,
    }


def p2_weak_open_verdict(p2_sz, p2_cyb, now_t, owner_approved):
    """P2 弱开局窗口纯判定(可单测): 返回 skip_owner/skip_window/skip_nodata/REJECT/WARN/pass"""
    if owner_approved:
        return "skip_owner"
    import datetime as _dt
    if not (_dt.time.fromisoformat(P2_WINDOW_START) <= now_t <= _dt.time.fromisoformat(P2_WINDOW_END)):
        return "skip_window"
    if p2_sz is None or p2_cyb is None:
        return "skip_nodata"
    worst = min(p2_sz, p2_cyb)
    if worst <= P2_WEAK_OPEN_THRESHOLD:
        return "REJECT"
    if worst <= P2_SOFT_WARN_THRESHOLD:
        return "WARN"
    return "pass"


def _fetch_szse_cyb_pct():
    """qt.gtimg.cn 自包含直连取 深证成指(sz399001)/创业板指(sz399006) 当日涨跌幅%; 失败返回(None,None)"""
    try:
        import urllib.request as _ur
        out = {}
        for key, code in (("szse", "sz399001"), ("cyb", "sz399006")):
            req = _ur.Request(f"https://qt.gtimg.cn/q={code}", headers={"Accept": "*/*"})
            with _ur.urlopen(req, timeout=5) as r:
                raw = r.read().decode("gbk", errors="replace")
            fields = raw.split("~")
            cur, prev = float(fields[3]), float(fields[4])
            out[key] = (cur / prev - 1) * 100 if prev else None
        return out.get("szse"), out.get("cyb")
    except Exception as e:
        print(f"[WARN] P2指数取数失败(fail-open): {e}", file=sys.stderr)
        return None, None


def main() -> int:
    p = argparse.ArgumentParser(description="屠龙买入前硬约束校验 v2 (fail-closed, R1-R18+R19+R22)")
    p.add_argument("--code", required=True, help="6位股票代码")
    p.add_argument("--name", default="", help="股票名称(铁律:必须补齐)")
    p.add_argument("--price", type=float, help="拟买入价")
    p.add_argument("--prev-close", type=float, help="昨收价")
    p.add_argument("--pct-60d", type=float, help="当前价60日分位, 0~1")
    p.add_argument("--holdings", type=int, help="当前持仓只数")
    p.add_argument("--total-cash", type=float, help="账户总资产")
    p.add_argument("--source", default="", help="信号源(admit白名单)")
    p.add_argument("--owner-approve-rs5", action="store_true",
                   help="R-S5不追高闸主人特批豁免(会话源REJECT降WARN留痕, 默认关)")
    p.add_argument("--owner-approve-r31", action="store_true",
                   help="R31买价偏离主人特批豁免(2026-09-08主人裁决开豁免口: 追价买强势股逐笔特批; "
                        "ext20>=20%% REJECT降WARN留痕, 默认关)")
    p.add_argument("--owner-approve-r6", action="store_true",
                   help="R6收在高点主人特批豁免(2026-09-10证据研究后R6已降WARN, 本旗标保留兼容, 无REJECT可豁免)")
    p.add_argument("--owner-approve-r30a", action="store_true",
                   help="R30a行业集中度主人特批豁免(2026-09-10主人裁决开豁免口: 同行业日新买上限逐笔特批; "
                        "REJECT降WARN留痕, 默认关)")
    p.add_argument("--owner-approve-r30b", action="store_true",
                   help="R30b组合行业占比主人特批豁免(PD-024③ 2026-10-23落闸: 单一申万L1持仓市值占比>30%%封新买; "
                        "REJECT降WARN留痕, 默认关)")
    p.add_argument("--owner-approve-r35", action="store_true",
                   help="R35卖后同日回补冷却闸主人特批豁免(PD-018③ 2026-10-02转实: 间隔≥30分钟且回补价<卖价; "
                        "REJECT降WARN留痕, 默认关)")
    p.add_argument("--owner-approve-ddgate", action="store_true",
                   help="DD_GATE回撤门控主人特批豁免(2026-09-15胜率提升五方案·方案3落闸: 防御/清仓档冻结新买入; "
                        "REJECT降WARN留痕, 默认关; 止损/卖出退出不受影响)")
    p.add_argument("--owner-approve-r37", action="store_true",
                   help="R37情绪档择时门主人特批豁免(2026-10-15落地: 弱档(涨停<40家)REJECT降WARN留痕, "
                        "默认关; PD-034弱档禁买闸本体不变, 供l0冒烟/单测确定性; ddgate/r38/r39同构先例)")
    p.add_argument("--owner-approve-r38", action="store_true",
                   help="R38期望闸主人特批豁免(2026-10-08落地: LOW_VOL弱档×gauge组级负期望T20E中位<0 "
                        "REJECT降WARN留痕, 默认关; 档案=reports/premarket12_reflection_20261008.md)")
    p.add_argument("--owner-approve-r39", action="store_true",
                   help="R39落刀闸主人特批豁免(2026-10-08落地: 个股开盘跳空≤-3%%或现价较昨收深跌≤-5%%真实行情口径 "
                        "REJECT降WARN留痕, 默认关; 蓝丰生化10/8低开-5.1%%漏网教训)")
    p.add_argument("--owner-approve-price-cap", action="store_true",
                   help="S-3价格铁律主人特批豁免(2026-10-09主人批准: 股价>30元上限逐笔特批 "
                        "REJECT降WARN留痕, 默认关; 8/13中恒电气44.77案例铁律本体不变)")
    p.add_argument("--amount", type=float, help="拟买入金额")
    p.add_argument("--close", type=float, help="当日收盘价(R6)")
    p.add_argument("--high", type=float, help="当日最高价(R6)")
    p.add_argument("--low", type=float, help="当日最低价(R6)")
    p.add_argument("--amplitude-40d", type=float, help="40日振幅(R5)")
    p.add_argument("--sector-breadth", type=float, help="当日板块宽度(R8, 默认读trade_state)")
    # 以下 R1/R14-R18 参数 2026-09-10 随闸退役(结构性空转下线), 保留仅为研究脚本兼容, 不再参与判定
    p.add_argument("--space-leader-limit-down", action="store_true", help="空间龙今日跌停(R14, 已退役)")
    p.add_argument("--consecutive-boards", type=int, help="当前连板数(R14/R16, 已退役)")
    p.add_argument("--seal-break-count", type=int, help="当日炸板次数(R15, 已退役)")
    p.add_argument("--turnover-rate", type=float, help="换手率 0~1 (R16, 已退役)")
    p.add_argument("--highest-board-yesterday", type=int, help="昨日最高连板(R17, 已退役)")
    p.add_argument("--highest-board-today", type=int, help="今日最高连板(R17, 已退役)")
    p.add_argument("--market-env", default="", help="市场环境标签(R18, 已退役)")
    p.add_argument("--sh-index-pct", type=float, help="上证指数当日涨跌幅%%(R25大盘择时)")
    p.add_argument("--cyb-index-pct", type=float, help="创业板指当日涨跌幅%%(R25大盘择时)")
    p.add_argument("--szse-index-pct", type=float, help="深证成指当日涨跌幅%%(P2弱开局; 缺省自动qt.gtimg.cn取数)")
    p.add_argument("--owner-approve-p2", action="store_true",
                   help="P2弱开局窗口主人特批豁免(2026-10-13落地: 深成/创业板≤-0.5%%且<10:30 "
                        "REJECT降skip留痕, 默认关; 呈请=reports/admit_source_pruning_proposal_20261013.md)")
    p.add_argument("--limit-up-count", type=int, help="今日涨停数(R18自动判定, 已退役)")
    p.add_argument("--advance-ratio", type=float, help="上涨家数占比(R18自动判定, 已退役)")
    p.add_argument("--sector-limit-up-count", type=int, help="板块涨停数(R18, 已退役)")
    p.add_argument("--sector", default="", help="该股所属申万L1行业名(R24板块高潮禁追)")
    p.add_argument("--is-st", action="store_true")
    p.add_argument("--intent", default="", help="买入意图: low_suck等(R6低吸豁免)")
    p.add_argument("--limit-up-buy", action="store_true", help="以涨停价买入(打板,R1)")
    p.add_argument("--late-stage", action="store_true", help="主升浪末端(R1)")
    p.add_argument("--allow-add-position", action="store_true", help="允许对已持仓标的加仓(R13/R23豁免)")
    p.add_argument("--skip-position-check", action="store_true", help="跳过R23实时持仓加仓检查(测试用)")
    p.add_argument("--skip-whitelist", action="store_true",
                   help="【已无操作】R26名单对照随名单制废除(2026-09-24)退役, 参数保留仅为调用方兼容")
    p.add_argument("--skip-time-window", action="store_true", help="兼容占位(全天可交易, 时间窗口已废)")
    p.add_argument("--no-log", action="store_true", help="跳过 guard_precheck_log 落库(测试用)")
    a = p.parse_args()

    # O08(2026-09-03, R19 KeyError家族根治面): 检查内任何异常此前直接崩出 main →
    # guard_precheck_log 零行(修复日实测全表695行0条"闸门异常"), 历史误拦笔数不可知。
    # 兜底=崩溃也产出合法JSON + "闸门异常"标记行落库(fail-closed 语义不变, fail 留痕)。
    try:
        r = check(a)
    except Exception as _e:
        import traceback
        r = {"code": a.code, "name": a.name or "", "board": "", "source": (a.source or ""),
             "tag": "", "verdict": "REJECT",
             "rejects": [f"闸门异常(主流程兜底): {type(_e).__name__}: {_e}"],
             "warnings": [],
             "objections": [f"闸门异常崩溃堆叠尾迹: {traceback.format_exc()[-280:]}"],
             "gate_exception": True}
    log_precheck(a, r)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 2 if r["verdict"] == "REJECT" else 0


if __name__ == "__main__":
    raise SystemExit(main())


