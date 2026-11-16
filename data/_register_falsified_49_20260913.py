# -*- coding: utf-8 -*-
"""falsified_rules 登记第49条: Y-REALIZABLE-LABEL-VARIANT (锚在rules闭括号前插入, 防落顶层坑)."""
import json, io, sys
sys.stdout.reconfigure(encoding='utf-8')

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(io.open(P, encoding='utf-8'))
if 'Y-REALIZABLE-LABEL-VARIANT' in d['rules']:
    print('已存在, 幂等跳过'); sys.exit(0)

d['rules']['Y-REALIZABLE-LABEL-VARIANT'] = {
    "rule": "Y-REALIZABLE-LABEL-VARIANT",
    "date": "2026-09-13",
    "prereg": "PREREG_PORTRAIT_REALIZABLE_20260913.md (判据先于实验冻结: ①test段RankIC>=0.03且Top20-base5>=0 ②分段稳定性>=2/3 ③构造稳健性旁证Top10h10/Top50h5)",
    "summary": "标签层重构假设判死: y_realizable变体(标签=entry T+2开盘→+5日收盘>=3%, 标签45万行与执行函数对拍438项零差异)训练正常(AUC 0.632/0.604/gap0.028/正样本率25.1%/best_iter=3)但测试段(20260429起, 72截面)三项判据全FAIL——Top20/h5超额中位-0.78pp(正占比29%)/IC(h10)-0.063/旁证Top10h10-2.54pp与Top50h5-0.82pp全负/分段非负1-2; 对照臂v1.0.0_short同段同口径-0.06pp/IC-0.153——标签重做后IC方向改善约0.09但仍不过线且Top20/正占比更差. 分层观察: 创业板rankIC+0.077/科创板+0.107为正在, 主板+0.012(74%样本)拖累混合IC. 机理=画像v1特征面(28价量特征=高动量/高换手/高位型)是负向主因标签是次因; 触发本prereg死刑条款=画像v1特征面在2026-04-30起市场环境下无正排序能力(环境依赖实证: 8月恢复段两模型均+1.5pp, valid段mid正超额——特征面无条件alpha不成立, 系regime-dependent). 后续若继续须换特征面(新数据源/新因子族)=重新立项范围, 非本变体修复面.",
    "verdict": "FAIL_ALL_3_CRITERIA_FEATURE_SURFACE_REQUIRED",
    "scope": "画像系统short系标签层变体(prod_tag=None零生产接触已验: v1.0.0_short/mid时间戳仍9/5); v1特征面无条件alpha判死, 环境依赖正能力记录在案; 数据资产realizable标签列(parquet 450万行)保留供后续消费"
}
d['version'] = '2026-09-13'
json.dump(d, io.open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
# 防落顶层自检
d2 = json.load(io.open(P, encoding='utf-8'))
assert 'Y-REALIZABLE-LABEL-VARIANT' in d2['rules'], '插入位置错误: 未落在rules内'
assert 'Y-REALIZABLE-LABEL-VARIANT' not in d2, '插入位置错误: 落在顶层'
print(f"登记完成: rules数={len(d2['rules'])} (第{len(d2['rules'])}条=Y-REALIZABLE-LABEL-VARIANT); 顶层自检通过")
