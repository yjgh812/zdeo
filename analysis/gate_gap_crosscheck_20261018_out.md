# R闸登记簿交叉核对(V7改, DeepSeek外部审计吸收项)

> 脚本 analysis/gate_gap_crosscheck_20261018.py 只读直出 | 登记簿真源=data/gate_registry.json(唯一owner=gen_gate_registry.py)
> 登记簿现状: ACTIVE 21 / RETIRED 10 / SHADOW 1 / UNKNOWN 0
> 本件零交易权, 禁写 data/ 合同; 补登裁决归 CURATED(owner=gen_gate_registry.py)

## 一、登记簿退役闸引证抽核(DeepSeek『废止须有裁决出处』关切)

| 闸 | 登记状态 | 裁决出处(登记簿原文截取) |
|---|---|---|
| R1 | RETIRED | SOP 2026-09-10 六闸退役（R1/R14-R18）；CLI 参数留兼容 |
| R14 | RETIRED | SOP 2026-09-10 六闸退役 |
| R15 | RETIRED | SOP 2026-09-10 六闸退役 |
| R16 | RETIRED | SOP 2026-09-10 六闸退役 |
| R17 | RETIRED | SOP 2026-09-10 六闸退役 |
| R18 | RETIRED | SOP 2026-09-10 六闸退役 |
| R20 | RETIRED | pre_trade_check_v2 头注 |
| R26 | RETIRED | 主人 2026-09-24 两道令 + AGENTS §10 注记 |
| R27 | RETIRED | 主人 2026-09-24 两道令 + AGENTS §10 注记 |
| R4 | RETIRED | pre_trade_check_v2 L72 [已移除] 标记 |

## 二、缺口编号呈证(登记簿 entries 未收录)

缺号 R2, R9, R11, R21, R29, R40 在主闸文件 gate 形态扫描(check_r<N>函数/分节注释/R<N>闸)均无定义痕迹；R12 命中均为迭代轮次命名(R12_M3=round12 ml 系列)同名异物非闸。**呈请**: 六号补登 CURATED『UNASSIGNED(无文档裁决, 诚实空白)』或确认永不使用，由主人/登记簿 owner 定；在补登前，任何『复活 R2/R9/R11/R21/R29/R40』提案须按宪法先立新指纹。

## 三、代码层漂移核对

- 代码 check_r* 函数族: 14 个 → R19, R22, R24, R30b, R31, R32, R33, R34, R35, R36, R37, R38, R39, R41
- 登记 ACTIVE/SHADOW 但主闸文件零引用(疑似失接): 无
- 代码新函数登记簿未收录(疑似新闸未登记): ['R36', 'R37', 'R38', 'R39', 'R41']
- 代码内退役标注共现行: 22 行 → R1×4; R14×3; R15×1; R16×1; R17×2; R18×4; R26×4; R27×1; R30b×1; R31×1

## 四、结论边界

- 登记簿状态以 CURATED 裁决为唯一真源, 本件静态扫描只做漂移哨; 两层一致=登记簿健康;
- 本件初版曾误建重复 owner 覆盖真源(A2 违规), 已当场恢复并整改为只读核对——教训: 建 registry 类工件前必先 grep data/ 同名合同。
