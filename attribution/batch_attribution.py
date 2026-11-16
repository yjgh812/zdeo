"""
屠龙批量归因引擎 v1
对 attribution.db 中的已平仓交易逐笔标准化归因
归因框架：选股逻辑/交易执行/市场环境/风控规则（亏损）+ 能力/运气（盈利）
红线：基于交易时点可得信息，禁用未来数据；能力运气客观分离
"""
import sqlite3
import json
from datetime import datetime

DB_PATH = r'D:\zcodesj\attribution\attribution.db'


class BatchAttributionEngine:
    """批量归因引擎：对每笔已平仓交易执行标准化归因分类"""

    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

    def get_closed_positions(self):
        """获取所有已平仓交易"""
        self.cursor.execute('''
            SELECT * FROM position_history
            WHERE status IN ('CLOSED', 'PARTIAL_CLOSED')
            ORDER BY entry_date
        ''')
        return self.cursor.fetchall()

    def analyze_market_context(self, entry_date, exit_date):
        """
        分析交易期间的市场环境（基于交易时点可得信息）
        7/22-7/28: 高位震荡期（多笔T+1止损）
        7/29-8/3: 风格切换期（蓝筹→成长）
        8/4+ : unknown（2026-09-08 O-N1: 启发式表仅维护至8/3, 此后不伪造环境标签）
        """
        # 2026-09-08 O-N1: 兼容两种日期格式(closed_loop_sync 新行=20260729 紧凑格式)
        entry = datetime.strptime(str(entry_date).replace('-', ''), "%Y%m%d")
        exit_d = datetime.strptime(str(exit_date).replace('-', ''), "%Y%m%d")

        if entry <= datetime(2026, 7, 28):
            return "高位震荡", "7月下旬市场震荡，板块快速轮动，追高风险大"
        elif entry <= datetime(2026, 8, 3):
            return "风格切换", "月末风格切换期，红利蓝筹与成长股跷跷板"
        else:
            # 反臆想§9: 环境启发式未覆盖期间一律 unknown, 禁止用过期"退潮期"标签冒充
            return "unknown", "启发式市场环境表未覆盖该期间(仅维护至2026-08-03), 如需精确环境标注须扩充表"

    def attribute_loss(self, pos):
        """
        亏损交易归因（4维度）
        基于可量化的交易特征自动分类
        """
        pnl_pct = pos['pnl_pct']
        holding_days = pos['holding_days']
        pnl_amount = pos['pnl_amount']
        sector = pos['sector'] or ''
        market_env, market_desc = self.analyze_market_context(pos['entry_date'], pos['exit_date'])

        # ===== 归因规则（按优先级，可叠加）=====
        attributions = []
        tags = []

        # 规则1: 持仓≤1天 + 亏损 = T+1短线追高（交易执行类）
        if holding_days <= 1 and pnl_pct < -0.5:
            attributions.append({
                'primary': 'execution',
                'secondary': '追高买入(T+1即砍)',
                'controllability': 8,
                'contribution': 40,
                'root_cause': f"持仓仅{holding_days}天即亏损{pnl_pct:.1f}%，属于T+1追高后次日割肉。买入时点未经充分验证（非AB竞价确认），开盘即扫货导致入场点位过高。",
                'lesson': 'T+1追高买入是最高频亏损模式。应严格执行AB竞价确认路径，禁止开盘1分钟内无差别扫货。',
                'rule': 'R6: 仅AB竞价确认买入，禁止盘中即兴操作'
            })
            tags.append('T+1追高')
            tags.append('短线割肉')

        # 规则2: 亏损>8% = 止损不力（交易执行类）
        if pnl_pct <= -8:
            attributions.append({
                'primary': 'execution',
                'secondary': '止损执行不到位',
                'controllability': 9,
                'contribution': 35,
                'root_cause': f"亏损{pnl_pct:.1f}%远超-8%固定止损线，说明未执行止损规则或入场价已偏离安全区间，安全垫不足。",
                'lesson': '单笔亏损>8%是止损纪律崩塌的铁证。-8%固定止损是铁律，任何理由不得绕过。',
                'rule': 'R1: -8%固定止损无条件执行'
            })
            tags.append('止损缺失')
            tags.append('重亏案例')

        # 规则3: 亏损3-8% + 持仓≤2天 = 选股逻辑缺陷（追高+形态误判）
        elif pnl_pct <= -3 and holding_days <= 2:
            attributions.append({
                'primary': 'selection',
                'secondary': '形态误判(假突破/追高)',
                'controllability': 7,
                'contribution': 35,
                'root_cause': f"短期持仓亏损{pnl_pct:.1f}%，属于买入时形态判断失误——可能是假突破、放量滞涨或弱势股补跌。选股时未充分验证趋势有效性。",
                'lesson': '短期亏损3-8%说明买入时点判断有误。需加强形态有效性过滤（量价共振确认、20日趋势>0硬过滤）。',
                'rule': 'R7: 20日趋势>0硬过滤 + 量价共振确认'
            })
            tags.append('形态误判')

        # 规则4: 银行/电力/红利蓝筹 + 小幅亏损 = 风格错配（板块错配）
        if sector in ('银行', '电力', '煤炭', '铁路', '通信') and pnl_pct < 0:
            attributions.append({
                'primary': 'selection',
                'secondary': '板块错配(防御股非主线)',
                'controllability': 5,
                'contribution': 25,
                'root_cause': f"在{market_env}期买入{sector}板块，该板块非当日主线，属于防御性配置但时机错误。蓝筹股波动小但也不产生超额收益。",
                'lesson': '红利蓝筹非进攻型标的，退潮期可防守但不适合短线T+1操作。',
                'rule': 'R8: 短线操作限定主线板块内标的'
            })
            tags.append('板块错配')
            tags.append('蓝筹短线化')

        # 规则5: 多笔同日买入 = 仓位管理问题（风控规则类）
        attributions.append({
            'primary': 'risk',
            'secondary': '仓位分散过度(同日多票)',
            'controllability': 6,
            'contribution': 20,
            'root_cause': f"属于同日批量建仓的组成部分，单票仓位分散导致无法聚焦主线，且稀释了盈利标的的贡献。",
            'lesson': '同日批量买入10+只票导致过度分散，应聚焦2-3只主线标的。',
            'rule': 'R9: 单日新开仓≤3只，持仓总数≤20'
        })

        # 规则6: 市场环境贡献（不可控部分）
        attributions.append({
            'primary': 'market',
            'secondary': f'{market_env}(环境不利)',
            'controllability': 2,
            'contribution': 15,
            'root_cause': f"{market_desc}。市场环境属于不可控因素，但策略应在退潮期主动降低仓位。",
            'lesson': '退潮期/震荡期应降低仓位，不应满仓扫货。',
            'rule': 'R10: 退潮期仓位≤50%'
        })

        return attributions, tags, market_env

    def attribute_profit(self, pos):
        """
        盈利交易归因（能力型 vs 运气型）
        """
        pnl_pct = pos['pnl_pct']
        holding_days = pos['holding_days']
        pnl_amount = pos['pnl_amount']
        code = pos['code']
        name = pos['name']
        sector = pos['sector'] or ''
        market_env, market_desc = self.analyze_market_context(pos['entry_date'], pos['exit_date'])

        attributions = []
        tags = []
        ability_luck = 'mixed'  # 默认混合型

        # 赚钱逻辑分类
        if pnl_pct >= 8 and holding_days >= 5:
            # 大幅盈利+持仓较长 = 可能是能力型（趋势捕获）
            attributions.append({
                'primary': 'ability',
                'secondary': '趋势捕获(持仓获利)',
                'controllability': 7,
                'contribution': 50,
                'root_cause': f"持仓{holding_days}天获利{pnl_pct:.1f}%，属于趋势型盈利。但需验证是策略能力还是个股独立行情。",
                'lesson': '趋势型盈利可复用，但需确认不是单票偶然。',
                'rule': '保持趋势跟踪能力'
            })
            tags.append('趋势盈利')
            ability_luck = 'ability'

        elif pnl_pct >= 2 and holding_days >= 5:
            # 中等盈利+持仓较长 = 板块红利（可能运气）
            attributions.append({
                'primary': 'luck',
                'secondary': '板块联动(被动上涨)',
                'controllability': 3,
                'contribution': 40,
                'root_cause': f"持仓{holding_days}天获利{pnl_pct:.1f}%，属于{sector}板块整体上涨的被动收益，非选股能力。",
                'lesson': '板块联动收益不可复用，不应归功于策略能力。',
                'rule': '区分alpha和beta收益'
            })
            tags.append('板块红利')
            ability_luck = 'luck'

        elif holding_days <= 1:
            # T+1即获利 = 短线博弈（运气成分高）
            attributions.append({
                'primary': 'luck',
                'secondary': '短线偶然(运气型)',
                'controllability': 2,
                'contribution': 50,
                'root_cause': f"T+1即获利{pnl_pct:.1f}%，属于短线偶然波动，与策略能力关系不大。",
                'lesson': 'T+1偶然盈利不可作为策略有效性的依据。',
                'rule': '不因T+1偶然盈利高估策略能力'
            })
            tags.append('短线偶然')
            ability_luck = 'luck'

        else:
            # 其他盈利
            attributions.append({
                'primary': 'mixed',
                'secondary': '混合型(能力+运气)',
                'controllability': 5,
                'contribution': 100,
                'root_cause': f"获利{pnl_pct:.1f}%，无法明确区分能力与运气贡献。",
                'lesson': '需更多样本验证。',
                'rule': '持续追踪'
            })
            tags.append('混合型')

        return attributions, tags, market_env

    def run_batch_attribution(self):
        """执行批量归因"""
        positions = self.get_closed_positions()

        # 清空旧归因（purge保护2026-08-12：只删本次处理的position_id对应的归因，不全删）
        pos_ids = [p['id'] for p in positions] if positions else []
        if pos_ids:
            placeholders = ",".join("?" * len(pos_ids))
            self.cursor.execute(f"DELETE FROM trade_attribution WHERE position_id IN ({placeholders})", pos_ids)
        # 不再全表DELETE，保留历史归因

        results = []
        for pos in positions:
            if pos['pnl_amount'] is None:
                # E7 批量关单留痕行(20261015): 金额/明细不可考, 反臆想禁伪造盈亏方向,
                # 落结构化占位归因(数值字段留空→闸门侧 SUM/AVG 自动剔除), 防 DELETE 清档后历史断档
                self.cursor.execute('''
                    INSERT INTO trade_attribution
                    (position_id, code, name, pnl_pct, pnl_amount,
                     primary_attribution, secondary_attribution, controllability_score,
                     pnl_contribution, root_cause, lesson, optimization_rule,
                     scenario_tags, ability_luck_verdict, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'westock')
                ''', (
                    pos['id'], pos['code'], pos['name'], None, None,
                    'data_unavailable', '盈亏金额不可考(关单留痕)', None, 0,
                    pos['exit_reason'] or '历史明细不可考，未复原盈亏',
                    '数据不可考行不参与胜率与盈亏归因统计；上游明细补全后可重新归因',
                    'E7: 关单不伪造数据',
                    json.dumps(['数据不可考'], ensure_ascii=False), 'unknown'
                ))
                results.append({
                    'code': pos['code'], 'name': pos['name'],
                    'pnl_pct': None, 'pnl_amount': None,
                    'holding_days': pos['holding_days'], 'is_profit': None,
                    'primary': 'data_unavailable', 'secondary': '盈亏金额不可考(关单留痕)',
                    'controllability': None, 'ability_luck': 'unknown',
                    'tags': ['数据不可考'], 'market_env': 'unknown'
                })
                continue
            is_profit = pos['pnl_amount'] > 0

            if is_profit:
                attrs, tags, market_env = self.attribute_profit(pos)
            else:
                attrs, tags, market_env = self.attribute_loss(pos)

            # 取主导归因（contribution最高的）
            primary_attr = max(attrs, key=lambda x: x['contribution'])

            # 能力运气裁决
            if is_profit:
                ability_luck = primary_attr['primary']  # ability/luck/mixed
            else:
                ability_luck = 'ability'  # 亏损=能力缺陷

            # 写入DB
            self.cursor.execute('''
                INSERT INTO trade_attribution
                (position_id, code, name, pnl_pct, pnl_amount,
                 primary_attribution, secondary_attribution, controllability_score,
                 pnl_contribution, root_cause, lesson, optimization_rule,
                 scenario_tags, ability_luck_verdict, data_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'westock')
            ''', (
                pos['id'], pos['code'], pos['name'], pos['pnl_pct'], pos['pnl_amount'],
                primary_attr['primary'], primary_attr['secondary'], primary_attr['controllability'],
                primary_attr['contribution'], primary_attr['root_cause'], primary_attr['lesson'],
                primary_attr['rule'], json.dumps(tags, ensure_ascii=False), ability_luck
            ))

            results.append({
                'code': pos['code'],
                'name': pos['name'],
                'pnl_pct': pos['pnl_pct'],
                'pnl_amount': pos['pnl_amount'],
                'holding_days': pos['holding_days'],
                'is_profit': is_profit,
                'primary': primary_attr['primary'],
                'secondary': primary_attr['secondary'],
                'controllability': primary_attr['controllability'],
                'ability_luck': ability_luck,
                'tags': tags,
                'market_env': market_env
            })

        self.conn.commit()
        return results

    def get_summary_stats(self, results):
        """统计归因分布"""
        total = len(results)
        wins = [r for r in results if r['is_profit'] is True]
        losses = [r for r in results if r['is_profit'] is False]
        unavailable = [r for r in results if r['is_profit'] is None]
        classified = len(wins) + len(losses)

        # 亏损归因分布
        loss_by_primary = {}
        loss_amount_by_primary = {}
        for r in losses:
            p = r['primary']
            loss_by_primary[p] = loss_by_primary.get(p, 0) + 1
            loss_amount_by_primary[p] = loss_amount_by_primary.get(p, 0) + abs(r['pnl_amount'])

        # 盈利能力运气分布
        profit_by_type = {}
        profit_amount_by_type = {}
        for r in wins:
            t = r['ability_luck']
            profit_by_type[t] = profit_by_type.get(t, 0) + 1
            profit_amount_by_type[t] = profit_amount_by_type.get(t, 0) + r['pnl_amount']

        # 场景标签频次
        tag_freq = {}
        for r in results:
            for t in r['tags']:
                tag_freq[t] = tag_freq.get(t, 0) + 1

        return {
            'total': total,
            'wins': len(wins),
            'losses': len(losses),
            'unavailable': len(unavailable),
            'win_rate': len(wins) / classified * 100 if classified else 0,
            'loss_by_primary': loss_by_primary,
            'loss_amount_by_primary': loss_amount_by_primary,
            'profit_by_type': profit_by_type,
            'profit_amount_by_type': profit_amount_by_type,
            'tag_freq': dict(sorted(tag_freq.items(), key=lambda x: -x[1])),
            'avg_controllability': sum(r['controllability'] for r in losses) / len(losses) if losses else 0
        }


def main():
    engine = BatchAttributionEngine()
    results = engine.run_batch_attribution()
    stats = engine.get_summary_stats(results)
    engine.conn.close()

    print(f"\n{'='*60}")
    print(f"屠龙批量归因引擎 — 归因结果汇总")
    print(f"{'='*60}")
    print(f"\n总笔数: {stats['total']} | 盈利: {stats['wins']} | 亏损: {stats['losses']} | 胜率: {stats['win_rate']:.1f}%")
    if stats.get('unavailable'):
        print(f"[注] 数据不可考 {stats['unavailable']} 笔(关单留痕, 不计入胜率/盈亏统计)")

    print(f"\n--- 亏损归因分布（按笔数）---")
    for k, v in sorted(stats['loss_by_primary'].items(), key=lambda x: -x[1]):
        amt = stats['loss_amount_by_primary'].get(k, 0)
        print(f"  {k:12s}: {v:3d}笔 ({v/stats['losses']*100:.0f}%) | 亏损金额 ¥{amt:,.0f} ({amt/sum(stats['loss_amount_by_primary'].values())*100:.0f}%)")

    print(f"\n--- 盈利归因分布（能力vs运气）---")
    for k, v in stats['profit_by_type'].items():
        amt = stats['profit_amount_by_type'].get(k, 0)
        label = {'ability': '能力型', 'luck': '运气型', 'mixed': '混合型'}.get(k, k)
        print(f"  {label:6s}: {v}笔 | 盈利金额 +¥{amt:,.0f}")

    print(f"\n--- 高频场景标签TOP10 ---")
    for i, (tag, freq) in enumerate(list(stats['tag_freq'].items())[:10]):
        print(f"  #{i+1} {tag}: {freq}次")

    print(f"\n亏损交易平均可控程度: {stats['avg_controllability']:.1f}/10 (越高=策略可优化空间越大)")

    print(f"\n[OK] 归因结果已写入 trade_attribution 表 ({len(results)}笔)")


if __name__ == '__main__':
    main()
