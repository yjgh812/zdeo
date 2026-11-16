# -*- coding: utf-8 -*-
"""sold_rebuy_pilot.py — 深贴-8%回补·模拟盘实战管理器 v1 (2026-09-29).

PREREG_SOLD_REBUY_DEEP8_20260929 (指纹c52fdf8b888c8b32) 三门全过后, 主人令「运用到实战」
=信号权授予(模拟盘practice_190)。灰度源=sold_rebuy_deep8(EXPERIMENTAL档, precheck强制
experiment标签: 单票≤10%/实验票总权重≤10%)。

v1参数(灰度围栏, 主人可调):
  种群=attribution近20交易日出场的自家卖单(原初卖飞用例); 每笔1.5万元; 并发(挂单+持仓)<=5;
  限价=sell*0.92 且<=30元(宪法§10-3); 20交易日未成交→RPC撤单; 成交后第10交易日收盘前卖出。
执行: 买入/卖出全部走 trade_executor 唯一入口(R闸全跑, REJECT即跳过记录不重试)。
取价/持仓/撤单=westock HTTP MCP 直连(self-contained, 模式同stop_loss_executor)。
时间护栏: 默认仅交易时段09:30-14:55执行(--force豁免供测试); --plan=干跑零动作。
用法: python sold_rebuy_pilot.py --plan | --execute [--force]
"""
import os, sys, json, sqlite3, subprocess, argparse, datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

BASE = r'D:\zcodesj'
ATTR_URI = r'file:D:\zcodesj\attribution\attribution.db?mode=ro'
QFQ_URI = r'file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro'
STATE_F = os.path.join(BASE, 'data', 'sold_rebuy_pilot_state.json')
TRADE_EXE = os.path.join(BASE, 'trade_executor.py')
SOURCE = 'sold_rebuy_deep8'
PER_TRADE = 15000.0
MAX_CONCURRENT = 5
PRICE_CAP = 30.0
VALID_DAYS = 20
HOLD_DAYS = 10
ZONE = 0.92
EXEC_EXE = os.path.join(BASE, 'trade_executor.py')
ORDERS_LOG = os.path.join(BASE, 'data', 'executor_orders.jsonl')


def norm_date(s):
    s = str(s).replace('-', '').strip()
    return s[:8] if len(s) >= 8 else ''


def load_state():
    if os.path.exists(STATE_F):
        return json.load(open(STATE_F, encoding='utf-8'))
    return {'orders': {}}


def save_state(st):
    json.dump(st, open(STATE_F, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def qfq_con():
    return sqlite3.connect(QFQ_URI, uri=True, timeout=60)


def tds_between(con, d1, d2):
    """(d1, d2] 的交易日列表"""
    return [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>? AND date<=? ORDER BY date",
        (d1, d2)).fetchall()]


def rpc_positions_and_cancel():
    """westock HTTP MCP 直连: 返回(持仓list, cancel_fn)。模式同stop_loss_executor自包含直连。"""
    import urllib.request as _ur
    url = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
    token = ""
    # 2026-09-29 修: 读取顺序对齐 stop_loss_executor 实战验证路径(yujie config 优先——
    # 09:40 首跑 5 单全 REJECT 教训: Administrator config 与 yujie config 的 token 不同步,
    # 旧 token 401 而实战链路全通)。重签自动化写哪个文件, 实战链路就读哪个。
    for cfgp in (r"C:\Users\yujie\.zcode\cli\config.json",
                 r"C:\Users\Administrator\.zcode\cli\config.json"):
        try:
            cfg = json.load(open(cfgp, encoding="utf-8"))
            ws = cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
            token = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
            if token:
                break
        except OSError:
            continue
    if not token:
        token = os.environ.get("WESTOCK_TOKEN", "")
    if not token:
        return None, None

    def rpc(payload):
        body = json.dumps(payload).encode()
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = _ur.Request(url, data=body, headers=h, method="POST")
        with _ur.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8")

    def _unwrap(raw):
        for line in raw.splitlines():
            if line.startswith("data:") and line.strip() != "data:":
                d = json.loads(line[5:].strip())
                if "result" in d:
                    for c in d["result"].get("content", []):
                        if c.get("type") == "text":
                            return json.loads(c["text"])
        return None

    def positions():
        try:
            rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "sold-rebuy-pilot", "version": "1.0"}}})
            rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            raw = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "portfolio_paper_positions", "arguments": {}}})
            obj = _unwrap(raw)
            data = (obj or {}).get("data", {})
            out = []
            for p in data.get("positions", []):
                q = int(float(p.get("quantity") or 0))
                if q > 0:
                    out.append({"code": str(p.get("code", "")).replace("sh", "").replace("sz", "").zfill(6),
                                "name": p.get("name", ""), "qty": q,
                                "price": float(p.get("currentPrice") or 0)})
            return out
        except Exception as e:
            print(f"[WARN] 持仓RPC失败: {e}", file=sys.stderr)
            return None

    def cancel(order_id):
        try:
            raw = rpc({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
                "name": "portfolio_paper_cancel", "arguments": {"order_id": order_id}}})
            return _unwrap(raw) is not None
        except Exception as e:
            print(f"[WARN] 撤单RPC失败 {order_id}: {e}", file=sys.stderr)
            return False

    return positions(), cancel


def industry_l1(code):
    try:
        c = sqlite3.connect(r'file:D:\workbuddyspace\databases\fundamental_data.db?mode=ro', uri=True)
        row = c.execute("SELECT industry FROM industry_map_ths WHERE code=?", (code,)).fetchone()
        c.close()
        return (row[0].split('-')[0] if row and row[0] else '未知')
    except Exception:
        return '未知'


def run_executor(args_list, timeout=300):
    try:
        r = subprocess.run([sys.executable, '-u', TRADE_EXE] + args_list,
                           capture_output=True, text=True, timeout=timeout,
                           cwd=BASE, encoding='utf-8', errors='replace')
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return 99, f'{type(e).__name__}: {e}'


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--plan', action='store_true', help='干跑: 只打印拟动作, 零执行')
    g.add_argument('--execute', action='store_true', help='实战: 真实下单/撤单/卖出(模拟盘)')
    ap.add_argument('--force', action='store_true', help='豁免交易时段护栏(测试用)')
    a = ap.parse_args()

    now = datetime.datetime.now()
    if not a.force and not (datetime.time(9, 30) <= now.time() <= datetime.time(14, 55)):
        print(f'[pilot] 非交易时段 {now:%H:%M} (护栏09:30-14:55, --force豁免); 本次仅--plan可跑')
        if not a.plan:
            return 0

    st = load_state()
    con = qfq_con()
    attr = sqlite3.connect(ATTR_URI, uri=True, timeout=60)
    today = now.strftime('%Y%m%d')
    # 交易日护栏(9/30补, 复用trade_day_guard单一真源): 假日automation照常触发, 休市日全跳过;
    # 日历不可用=None=fail-open继续执行(本护栏绝不误伤交易日; 注意qfq日历晨间只到昨日, 不可用作判定)
    from trade_day_guard import is_trading_day
    if is_trading_day(today) is False and not a.force:
        print(f'[pilot] {today} 休市日(trade_day_guard) → 全跳过')
        con.close()
        return 0
    sells = pd.read_sql_query(
        "SELECT code,name,trade_date,action,price FROM trade_records "
        "WHERE action IN ('SELL','PARTIAL_SELL')", attr)
    attr.close()
    sells['code'] = sells['code'].astype(str).str.replace(r'^(sh|sz|bj)', '', regex=True).str.zfill(6)
    sells['d'] = sells['trade_date'].map(norm_date)
    sells = sells.dropna(subset=['price'])
    sells = sells[(sells['d'] >= '20260101') & (sells['price'] > 0)]
    # 近20交易日卖出 = 卖日在 (今日-40自然日, 今日] 粗筛后按qfq交易日精确计
    sells = sells[sells['d'] >= (datetime.date.today() - datetime.timedelta(days=40)).strftime('%Y%m%d')]
    latest = sells.sort_values('d').drop_duplicates(subset=['code'], keep='last')
    con.close()

    positions, cancel_fn = rpc_positions_and_cancel()
    held = {p['code'] for p in positions} if positions is not None else None
    if held is None:
        print('[pilot] FAIL 取不到westock持仓(fail-closed, 不下单不卖出不撤单)')
        return 3
    pos_map = {p['code']: p for p in positions}

    st_orders = st.setdefault('orders', {})
    # pending→filled 状态转移: 挂单码出现在westock持仓=已成交
    for code, o in list(st_orders.items()):
        if o.get('status') == 'pending' and code in pos_map:
            o['status'] = 'filled'
            o['fill_date'] = today
            o['fill_price'] = pos_map[code]['price']
            print(f"[pilot] 成交确认 {code} {o.get('name','')} → filled @约{o['fill_price']}")
    # westock日终自动撤对账(2026-09-30实锤: 未成交限价单15:32系统撤, 无隔夜单):
    # 隔日pending必为死单 → 标day_expired并允许重新评估(仍过企稳门+R闸);
    # owner令「仅撤」的cancelled状态不受影响, 永不复挂
    for code, o in list(st_orders.items()):
        if o.get('status') == 'pending' and o.get('placed_at') and o['placed_at'] != today:
            o['status'] = 'day_expired'
            o['day_expired_at'] = today
            print(f"[pilot] 日终死单 {code} {o.get('name','')} 挂于{o['placed_at']} "
                  f"(westock收盘已自动撤) → 转重新评估")
    # 并发占用 = pending + filled(未退出)
    active = [c for c, o in st_orders.items() if o.get('status') in ('pending', 'filled')]
    slots = max(0, MAX_CONCURRENT - len(active))

    plan_lines = []
    # ---------- 1) 到期撤单 ----------
    con = qfq_con()
    for code, o in list(st_orders.items()):
        if o.get('status') != 'pending':
            continue
        n_tds = len(tds_between(con, o['sell_date'], today))
        if n_tds >= VALID_DAYS:
            plan_lines.append(f"撤单 {code} {o.get('name','')} order={o.get('order_id','')} (挂单{VALID_DAYS}交易日未成交)")
            if a.execute:
                if o.get('order_id') and cancel_fn:
                    ok = cancel_fn(o['order_id'])
                    o['status'] = 'expired' if ok else 'expired_cancel_fail'
                    print(f"[pilot] 撤单 {code}: {'OK' if ok else 'RPC失败→标expired_cancel_fail(需MCP手动)'}")
                else:
                    o['status'] = 'expired'
            else:
                print(f"[plan] {plan_lines[-1]}")
    # ---------- 2) 到期卖出 ----------
    con2 = qfq_con()
    for code, o in list(st_orders.items()):
        if o.get('status') != 'filled':
            continue
        n_tds = len(tds_between(con2, o['fill_date'], today))
        p = pos_map.get(code)
        if n_tds >= HOLD_DAYS and p:
            price = round(p['price'] * 0.995, 2)
            plan_lines.append(f"卖出 {code} {p['name']} {p['qty']}股 @{price} (回补满{HOLD_DAYS}交易日, "
                              f"盈亏{(p['price']/o['fill_price']-1)*100:+.1f}%)")
            if a.execute:
                rc, out = run_executor(['sell', '--code', code, '--name', p['name'],
                                        '--source', SOURCE, '--price', str(price),
                                        '--volume', str(p['qty']),
                                        '--reason', 'DEEP8回补10日到期', '--confirmed', '--execute'])
                ok = (rc == 0)
                o['status'] = 'exited' if ok else 'exit_fail'
                o['exit_rc'] = rc
                print(f"[pilot] 卖出 {code}: rc={rc} {'OK' if ok else out[-300:]}")
            else:
                print(f"[plan] {plan_lines[-1]}")
        elif n_tds >= HOLD_DAYS and not p:
            o['status'] = 'gone'  # 已不在持仓(可能被其他链卖出), 关闭跟踪
    con2.close()
    # ---------- 3) 新回补挂单 ----------
    active = [c for c, o in st_orders.items() if o.get('status') in ('pending', 'filled')]
    slots = max(0, MAX_CONCURRENT - len(active))
    cand = []
    for _, s in latest.iterrows():
        code = s['code']
        if (code in st_orders and st_orders[code].get('status') != 'day_expired') \
                or code in held or code in active:
            continue
        if s['d'] == today:
            continue  # 当日卖出→westock仍挂qty=0→R23必拒; 次日本脚本自动拾取(T+1回转守卫)
        if s['name'] and 'ST' in str(s['name']).upper():
            continue
        if code[:3] not in ('600', '601', '603', '605', '000', '001', '002', '003', '300', '301', '302'):
            continue
        limit = round(float(s['price']) * ZONE, 2)
        if limit > PRICE_CAP or limit < 1.0:
            continue
        # ---- 企稳确认门槛(根本修复: 不是所有深贴都该买) ----
        # 检查最近 5 根 bar: 股价是否企稳(非持续新低)+量能是否萎缩(卖压衰竭)
        # 任一不达标 → 跳过(还在跌的票不接刀)
        px_chk = pd.read_sql_query(
            "SELECT date,close,low,volume FROM stock_daily_meta_qfq "
            "WHERE code=? AND date>? ORDER BY date", con, params=(code, s['d']))
        if len(px_chk) < 2:
            continue
        cc = px_chk['close'].astype(float).to_numpy()
        ll = px_chk['low'].astype(float).to_numpy()
        vv = px_chk['volume'].astype(float).to_numpy()
        sell_vol = float(con.execute(
            "SELECT volume FROM stock_daily_meta_qfq WHERE code=? AND date=?",
            (code, s['d'])).fetchone()[0] or 1)
        # 条件1: 近5日未创卖后新低(价格止跌)
        min_low_5d = float(np.min(ll[-5:])) if len(ll) >= 5 else float(ll.min())
        low_all = float(np.min(ll))
        no_new_low = min_low_5d >= low_all
        # 条件2: 量能萎缩(近3日均量 < 卖日量×0.7)
        vol_recent = float(np.mean(vv[-3:])) if len(vv) >= 3 else 0
        vol_dry = vol_recent < sell_vol * 0.7
        # 条件3: 最新收盘 > 前一日收盘(首次反弹信号)
        last_up = len(cc) >= 2 and cc[-1] > cc[-2]
        stab_score = sum([no_new_low, vol_dry, last_up])
        if stab_score < 2:
            reason = f"企稳{stab_score}/3(新低{'✓' if no_new_low else '✗'} 缩量{'✓' if vol_dry else '✗'} 反弹{'✓' if last_up else '✗'})"
            print(f"[skip] {s['name']}({code}) {reason} → 还在跌,不接刀")
            continue
        vol = int(PER_TRADE / limit / 100) * 100
        if vol < 100:
            continue
        cand.append({'code': code, 'name': s['name'], 'sell_date': s['d'], 'sell_price': float(s['price']),
                     'limit': limit, 'vol': vol})
    cand.sort(key=lambda x: x['sell_date'], reverse=True)  # 最新卖出优先占并发槽
    if a.execute and slots > 0:
        for c_ in cand[:slots]:
            l1 = industry_l1(c_['code'])
            print(f"[pilot] 买入挂单 {c_['name']}({c_['code']}) {c_['vol']}股 @{c_['limit']} "
                  f"(卖价{c_['sell_price']}×0.92, 源={SOURCE}, L1={l1}) ...")
            rc, out = run_executor(['buy', '--code', c_['code'], '--name', c_['name'],
                                    '--source', SOURCE, '--sector', l1,
                                    '--price', str(c_['limit']), '--volume', str(c_['vol']),
                                    '--execute'], timeout=420)
            order_id = None
            try:
                for line in reversed(open(ORDERS_LOG, encoding='utf-8').read().splitlines()):
                    o_ = json.loads(line)
                    if o_.get('code') == c_['code'] and o_.get('trade_date') == today \
                            and o_.get('action') == 'BUY':
                        order_id = o_.get('order_id')
                        break
            except Exception:
                pass
            status = 'pending' if rc == 0 else 'rejected'
            st['orders'][c_['code']] = {
                'status': status, 'sell_date': c_['sell_date'], 'sell_price': c_['sell_price'],
                'limit_price': c_['limit'], 'volume': c_['vol'], 'placed_at': today,
                'order_id': order_id, 'rc': rc,
                'note': (out.strip().splitlines() or [''])[-1][:160] if rc != 0 else ''}
            print(f"[pilot] 结果 rc={rc} → {status} order={order_id}"
                  + ('' if rc == 0 else f" | {st['orders'][c_['code']]['note']}"))
    else:
        for c_ in cand[:max(0, slots) if a.execute else 5]:
            print(f"[plan] 拟挂回补单 {c_['name']}({c_['code']}) {c_['vol']}股 @{c_['limit']} "
                  f"(卖{c_['sell_date']} @{c_['sell_price']})")
        if len(cand) > slots and a.execute:
            print(f"[pilot] 并发满({MAX_CONCURRENT}), 其余{len(cand)-slots}只候选本轮跳过")

    save_state(st)
    n_pending = sum(1 for o in st['orders'].values() if o['status'] == 'pending')
    n_filled = sum(1 for o in st['orders'].values() if o['status'] == 'filled')
    print(f"\n[pilot] 状态: 挂单{n_pending} 在场{n_filled} 历史{len(st['orders'])-n_pending-n_filled} "
          f"| 模式={'EXECUTE' if a.execute else 'PLAN'} | state={STATE_F}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
