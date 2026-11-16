# -*- coding: utf-8 -*-
"""qfq 副本同步器 · 已退役 (2026-10-12 单库合并, SOP一百三十一)

裁决(已两次定谳): 10/12(SOP131)双库合并曾裁 zc=唯一真源; 10/13(SOP132)翻转定谳——
run_qfq 用自身 wb 常量链直写 wb 未切, 实证 wb=活体主库 → **单一真源 = wb
(workbuddyspace/factor_engine/data/dragon_quant_qfq.db, 读+写合一)**; zc 旧副本已退役,
data\\dragon_quant_qfq.db 路径禁止任何回置(违规即 check_db_freshness 白名单巡检 ❌)。
退役原因: 增量 UPSERT 无法传播全量重建的历史重定基准(2016 起除权价/补行),
实测漂移 20.8 万行+2016-2019 除权价差 1-2%(SOP一百三十)——双库+增量同步=结构性漂移缺陷。

保留本文件仅因: ①SRC/DST 常量被 analysis/gen_data_locations.py 解析生成数据位置文档;
②审计 trail(重构前的主从架构考古, 含 2026-10-12 并行会话的 alert 通道完善)。
main() 已改为退役守卫: 任何残留调用 fail-loud(exit 2), 提示摘除调用方。

历史(考古): zcodesj/data 副本原为 2026-09-06 vipdoc 一次性重建的静态库, 断更于 20260904
被巡检盲区暴露后, 2026-09-10 接线本同步器做主从增量 UPSERT(>60 日差集告警退出)。
"""
import sys

sys.stdout.reconfigure(encoding='utf-8')

# 常量保留供 analysis/gen_data_locations.py 文本解析(数据位置文档自动生成)
SRC = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'   # 唯一真源(2026-10-13 SOP132 定谳, 读+写合一)
DST = r'D:\zcodesj\data\dragon_quant_qfq.db'                        # 已退役旧路径(禁止回置; 常量保留仅供 gen_data_locations 解析)


def main():
    print('RETIRED qfq_replica_sync: qfq 已合并为单一真源=wb 路径(2026-10-13 SOP132 '
          '翻转定谳, 取代 10/12 zc 裁决), 本同步器退役——若见此告警说明仍有残留调用方'
          '未摘除, 请按 analysis/gen_data_locations.py 与 daily_chain_update.py 的注释排查。')
    return 2


if __name__ == '__main__':
    rc = main()
    sys.exit(rc)
