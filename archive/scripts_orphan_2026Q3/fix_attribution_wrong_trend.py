import sqlite3, json
from datetime import datetime

DB = "D:/zcodesj/attribution/attribution.db"
con = sqlite3.connect(DB)
c = con.cursor()

# 4笔错误归因（8/14早盘卖出，理由"趋势走坏"但均线显示未走坏）
wrong_sells = [
    {
        "code": "sh600711", "name": "盛屯矿业", "sell_price": 11.55, "close": 11.70,
        "pnl_pct": 1.30, "pnl_amount": 615,
        "reason": "错误归因: 以YTD弱势为由判定'趋势走坏'卖出，实为MA5>MA10>MA20多头/中性，8/13收盘11.85>MA20(10.95)位置76%",
    },
    {
        "code": "sz300170", "name": "汉得信息", "sell_price": 17.71, "close": 17.36,
        "pnl_pct": -1.98, "pnl_amount": -1190,
        "reason": "错误归因: 以YTD弱势为由判定'趋势走坏'卖出，实为MA5>MA10>MA20多头/中性，8/13收盘17.12>MA20(15.96)位置74%",
    },
    {
        "code": "sz300711", "name": "广哈通信", "sell_price": 17.93, "close": 18.40,
        "pnl_pct": 2.62, "pnl_amount": 1551,
        "reason": "错误归因: 以YTD-22.76%为由判定'趋势走坏'卖出，实为价格站上全部均线(MA5/10/20/60)，卖出后涨+3%卖飞。违反YTD禁用铁律+卖出三铁律",
    },
    {
        "code": "sh600338", "name": "西藏珠峰", "sell_price": 13.63, "close": 13.67,
        "pnl_pct": 0.29, "pnl_amount": 144,
        "reason": "错误归因: 以YTD弱势为由判定'趋势走坏'卖出，实为MA5>MA10>MA20多头/中性，8/13收盘13.54>MA20(12.92)位置74%",
    },
]

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
for s in wrong_sells:
    code = s["code"].replace("sh", "").replace("sz", "")
    c.execute("""INSERT INTO experience_library
        (library_type, case_id, code, name, trade_link, pnl_summary,
         attribution_detail, controllability_score, core_facts, lesson,
         optimization_rule, scenario_tags, verification_status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "loss_case",
            f"WRONG-TREND-{code}",
            code, s["name"],
            f"8/14早盘卖出@{s['sell_price']}，收盘{s['close']}",
            f"卖飞{'+' if s['pnl_amount']>0 else ''}{s['pnl_amount']}元({s['pnl_pct']}%)",
            s["reason"],
            8,  # 可控度高=人为可避免
            f"卖出时8/13均线: {s['reason']}",
            "YTD是年度累计统计，不能作为趋势走坏判定依据。趋势判定只看MA5/10/20/60排列+近期量价+资金流",
            "pre_sell_check已接入C5趋势验证，趋势完好→HOLD禁止卖出",
            json.dumps(["YTD误判", "趋势误判", "早盘卖出"], ensure_ascii=False),
            "forward_testing",
            now,
        )
    )

con.commit()
print(f"已录入 {len(wrong_sells)} 笔错误归因到 experience_library")
con.close()
