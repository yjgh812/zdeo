# -*- coding: utf-8 -*-
"""falsified_rules 登记: ML-WINRATE-UP250-NO-EDGE (锚在rules内插入, 幂等+落位自检, 模板=_register_falsified_73)."""
import json, io, sys
sys.stdout.reconfigure(encoding='utf-8')

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(io.open(P, encoding='utf-8'))
if 'ML-WINRATE-UP250-NO-EDGE' in d['rules']:
    print('已存在, 幂等跳过'); sys.exit(0)

d['rules']['ML-WINRATE-UP250-NO-EDGE'] = {
    "rule": "ML-WINRATE-UP250-NO-EDGE",
    "date": "2026-11-05",
    "prereg": "非prereg(方法准入后验审计, 先例=主人令20261101盈利模式复现批的事后全历史验考)",
    "summary": (
        "ML胜率排名(原版ml_signal外部App名单+复刻ml_winrate_replica)判死: 方法本质=按个股过去250日"
        "上涨天数占比(≥55%,样本≥200)排名选票, 假设'过去爱涨→未来会涨'. 四路独立证据同向: "
        "①全历史可实现口径回测(2016-2026, 2411交易日, 主板+创业板, 剔科创/北证, 0.5-30元与复刻生成器一致, "
        "qfq收盘→收盘无未来函数, data/_ml_up250_backtest_20261105.py + _ml_up250_daily_check_20261105.csv): "
        "按占比十等分, 最高组D1后市5/10/20日全无优势(20日 D1 +0.40% vs 池 +0.53%), 十分位曲线平=不挑票; "
        "复刻条件(≥55%)后市跑输池 5日-0.20pp/10日-0.35pp/20日-0.53pp(越拿越差); 复刻Top7同样全负"
        "(-0.27/-0.45/-0.49pp); D1-D10差分年6正6负乱翻(2020/2023/2025反向), 无任何稳定窗口. "
        "②9/13可实现口径检验(data/_ml_signal_realizable_check_20260913.py): 外部App的ml_prob分数对后市5日"
        "RankIC中位+0.031/正占比51%≈零预测力, 最高分组超额+0.11pp≈0. "
        "③9/13稳定性测试(data/_top10_winrate_stability_20260913.py): 9/13名单10只逐半年胜率8/10不稳定(非S级). "
        "④实盘检验批(10/19主人令10只×500股): 6赚4不赚合计≈打平(登记表V7净-410元), 赚小亏大(飞南资源单笔-10.6%止损), "
        "且6只被PD-042清理在T+2卖出=考试被中断, 不能反证方法有效. "
        "盈亏比结构性差=胜率高≠赚钱(慢牛型票涨得慢, 一次-8%~-10%大亏抹掉多次小赚). "
        "处置: 原版ml_signal主人20261105裁决暂停新买入(已持中电港/中文在线不动); 复刻3只小仓"
        "(探路者/津荣天宇/中谷物流, 11/02各100股)主人20261105『继续』令批准清仓收摊(09:35定时执行); "
        "方法本体连同250日上涨占比/后验胜率排名变体, 未经主人再批+新证据(如重训模型)不得复活(宪法§10.7)."
    ),
    "verdict": "FAIL_NO_EDGE_HIGH_WINRATE_LOW_PAYOFF",
    "scope": "复盘级零生产接触: 原版已暂停/复刻已清仓, 准入表METHOD_ADMISSION_INTERIM_SOURCES不含ml系; "
             "副产品=stability/realizable两份9/13档案与本回测三件套归档于data/, 不改任何生产代码"
}
d['version'] = '2026-11-05'
json.dump(d, io.open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(io.open(P, 'r', encoding='utf-8'))
assert 'ML-WINRATE-UP250-NO-EDGE' in d2['rules'], '插入位置错误: 未落在rules内'
assert 'ML-WINRATE-UP250-NO-EDGE' not in d2, '插入位置错误: 落在顶层'
print(f"登记完成: rules数={len(d2['rules'])} (最新=ML-WINRATE-UP250-NO-EDGE); 顶层自检通过")
