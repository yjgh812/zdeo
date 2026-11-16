# -*- coding: utf-8 -*-
"""falsified_rules 登记: FIRST-BOARD-CHASE-NO-EDGE (锚在rules内插入, 幂等+落位自检, 模板=_register_falsified_49)."""
import json, io, sys
sys.stdout.reconfigure(encoding='utf-8')

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(io.open(P, encoding='utf-8'))
if 'FIRST-BOARD-CHASE-NO-EDGE' in d['rules']:
    print('已存在, 幂等跳过'); sys.exit(0)

d['rules']['FIRST-BOARD-CHASE-NO-EDGE'] = {
    "rule": "FIRST-BOARD-CHASE-NO-EDGE",
    "date": "2026-10-03",
    "prereg": "侦察级非prereg(先例:爆量对称PIT侦察不入prereg队列); 口径冻结于报告§12先于统计输出(reports/douyin_qihang_youzi_verify_20261003.md)",
    "summary": "首板次开追买(含温和放量带)判死: 事件=首板(T涨停且前5日无板)×缩量整理(T-1量<0.8×前20日均量)×量比四带, 入场=T+1开盘(剔高开≥9%), n≈2.8万可执行事件(qfq全市场2016-2026,4939票). 量比四带T+20无止损中位无差(-3.50/-3.68/-3.46/-3.25%)=「1.3~2.6x温和放大」零选股增量; 带-8%止损(收盘判定)后各带T+20中位≈-7.5~-8.4%(贴止损线)胜率31~33%=与紧止损结构性冲突(PEAD同构第二例); 唯一微正=低位置(pos<0.3)弱量比组med+0.83%/mean+2.48%=彩票型右尾(低位埋伏孵龙头,呼应9/18百亚右尾实锤)不立项. 起因=抖音涅槃重升案自点案例(盘龙3/20十日+150%/海汽5/24两日+17.2%)案例法幸存者右尾, 全市场基率证伪——其真实alpha在六情绪择时层非形态残件. 追涨类买点第四次同构证伪(≥6%胜率5.6%(9/17)→pos60-80胜率3.3%(9/16)→裸技术位零边际(9/30)→本次).",
    "verdict": "FAIL_NO_EDGE_VOLUME_BAND_FAIL_WITH_STOP",
    "scope": "侦察级零生产接触; 卖点侧反向增强=S1断板即顶(n=109,799单调)/S2反抽不改变趋势(n=6,202 r10中位-5.1~-7.5%胜率35%)/S3爆量先兆(T+1毒性倍增)三件套统计闭环, 支撑trend_checker走坏判定与爆量退出的证据面, 不改任何生产代码"
}
d['version'] = '2026-10-03'
json.dump(d, io.open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(io.open(P, encoding='utf-8'))
assert 'FIRST-BOARD-CHASE-NO-EDGE' in d2['rules'], '插入位置错误: 未落在rules内'
assert 'FIRST-BOARD-CHASE-NO-EDGE' not in d2, '插入位置错误: 落在顶层'
print(f"登记完成: rules数={len(d2['rules'])} (最新=FIRST-BOARD-CHASE-NO-EDGE); 顶层自检通过")
