"""
yfinance MCP Server v2 — 全球股票/ETF 数据 (知微 2026-09-23 修复)
Yahoo Finance 为主源 (财务/分红/持仓独有); 实时行情与美股日K 增加新浪兜底
(2026-09-23 实测: Yahoo 被 SNI 阻断 RST, 新浪 hq.sinajs.cn 带 Referer 稳定可用)。
兜底字段为 2026-09-23 实抓实测映射, 非猜测:
  美股 gb_<sym>: 0名称 1最新 2涨跌% 4涨跌额 6最高 7最低 8_52周高 9_52周低
                10成交量 12市值 13PE 26昨收 3时间
  港股 rt_hk<5位>: 0英文名 1中文名 2今开 3昨收 4最高 5最低 6最新 7涨跌额 8涨跌%
  美股日K: US_MinKService.getDailyK → {d,o,h,l,c,v,a} 全史
Yahoo-only 工具(财务/分红/拆股/持仓)失败时返回明确的替代源指引。
"""

import json
import urllib.request

import pandas as pd
import requests
from datetime import datetime
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("yfinance-mcp")

SINA_HQ = "https://hq.sinajs.cn/list={}"
SINA_REFERER = "https://finance.sina.com.cn"
US_DAILYK = ("https://stock.finance.sina.com.cn/usstock/api/json_v2.php/"
             "US_MinKService.getDailyK?symbol={}")
FALLBACK_HINT = ("Yahoo Finance 当前网络不可达(SNI阻断), 本字段无新浪兜底。"
                 "替代源: 大智慧MCP(港/美股财报 getUsProfitData/getHkIncomeStatementData), "
                 "westock data_finance, stock-data-mcp stock_financials_us")


def _sina_raw(q: str) -> str:
    req = urllib.request.Request(SINA_HQ.format(q), headers={
        "User-Agent": "Mozilla/5.0", "Referer": SINA_REFERER})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("gbk", errors="ignore")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _sina_us_rt(sym: str) -> dict:
    sym = sym.lower()
    raw = _sina_raw(f"gb_{sym}")
    for line in raw.splitlines():
        if f'hq_str_gb_{sym}="' in line:
            f_ = line.split('"')[1].split(",")
            if len(f_) < 27:
                break
            return {"symbol": sym.upper(), "name": f_[0], "source": "sina",
                    "price": _f(f_[1]), "change_pct": _f(f_[2]),
                    "change": _f(f_[4]), "day_high": _f(f_[6]),
                    "day_low": _f(f_[7]), "week52_high": _f(f_[8]),
                    "week52_low": _f(f_[9]), "volume": _f(f_[10]),
                    "market_cap": _f(f_[12]), "pe_ratio": _f(f_[13]),
                    "prev_close": _f(f_[26]), "timestamp": f_[3]}
    raise ValueError(f"sina 未返回 {sym} 数据")


def _sina_hk_rt(sym: str) -> dict:
    code = sym.split(".")[0].lstrip("0").rjust(5, "0")
    raw = _sina_raw(f"rt_hk{code}")
    for line in raw.splitlines():
        if f'hq_str_rt_hk{code}="' in line:
            f_ = line.split('"')[1].split(",")
            if len(f_) < 9:
                break
            return {"symbol": sym, "name_en": f_[0], "name": f_[1],
                    "source": "sina", "open": _f(f_[2]), "prev_close": _f(f_[3]),
                    "day_high": _f(f_[4]), "day_low": _f(f_[5]),
                    "price": _f(f_[6]), "change": _f(f_[7]),
                    "change_pct": _f(f_[8]), "volume": _f(f_[12]),
                    "week52_high": _f(f_[15]) if len(f_) > 15 else None,
                    "week52_low": _f(f_[16]) if len(f_) > 16 else None,
                    "timestamp": f"{f_[17]} {f_[18]}" if len(f_) > 18 else ""}
    raise ValueError(f"sina 未返回 {sym} 数据")


def _classify(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith(".HK") or (s.isdigit() and len(s) <= 5):
        return "hk"
    if "-" in s or s.endswith(".US") or s.replace(".", "").isalpha():
        return "us"
    return "us"


@mcp.tool()
def get_realtime(symbol: str) -> str:
    """
    获取股票/ETF 实时行情 (Yahoo 优先, 美/港股自动新浪兜底)
    Args:
        symbol: 股票代码, 如 AAPL、00700.HK、SPY、BTC-USD
    Returns: 最新价、涨跌幅、成交量等 JSON
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info
        fields = {
            "symbol": symbol,
            "name": info.get("shortName") or info.get("longName"),
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "prev_close": info.get("previousClose") or info.get("regularMarketPreviousClose"),
            "open": info.get("open") or info.get("regularMarketOpen"),
            "day_high": info.get("dayHigh") or info.get("regularMarketDayHigh"),
            "day_low": info.get("dayLow") or info.get("regularMarketDayLow"),
            "volume": info.get("volume") or info.get("regularMarketVolume"),
            "change_pct": info.get("regularMarketChangePercent"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "dividend_yield": info.get("dividendYield"),
            "source": "yahoo",
            "timestamp": datetime.now().isoformat()
        }
        if fields.get("price"):
            return pd.DataFrame([fields]).to_json(orient="records", force_ascii=False)
        raise ValueError("yahoo 空价格")
    except Exception as e:
        YahooErr = f"{type(e).__name__}"[:40]
        try:
            cls = _classify(symbol)
            if cls == "hk":
                data = _sina_hk_rt(symbol)
            elif cls == "us" and "-" not in symbol.upper():
                data = _sina_us_rt(symbol)
            else:
                return (f'[{{"error":"Yahoo不可达({YahooErr})且 {symbol} 无新浪兜底",'
                        f'"hint":"加密货币/特殊标的请用 stock-data-mcp 或 westock"}}]')
            return pd.DataFrame([data]).to_json(orient="records", force_ascii=False)
        except Exception as e2:
            return (f'[{{"error":"双源失败 yahoo={YahooErr} sina={type(e2).__name__}:'
                    f'{str(e2)[:80]}","symbol":"{symbol}"}}]')


_PERIOD_ROWS = {"1d": 1, "5d": 5, "1mo": 22, "3mo": 66, "6mo": 132,
                "1y": 252, "2y": 504, "5y": 1260, "10y": 2520, "max": 99999}


@mcp.tool()
def get_history(symbol: str, period: str = "1mo", interval: str = "1d") -> str:
    """
    获取历史 K 线数据 (Yahoo 优先; 美股自动新浪日K兜底, 兜底仅日线)
    Args:
        symbol: 股票代码
        period: 周期 1d/5d/1mo/3mo/6mo/1y/2y/5y/10y/max
        interval: 频率 1m/2m/5m/15m/30m/60m/1d/1wk/1mo (兜底仅支持 1d)
    Returns: OHLCV + 涨跌幅 CSV
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if not df.empty:
            df.index = df.index.strftime("%Y-%m-%d %H:%M:%S")
            df.columns = [c.lower() for c in df.columns]
            return df.to_csv(index=True, header=True)
        raise ValueError("yahoo 空")
    except Exception:
        try:
            if _classify(symbol) != "us" or "-" in symbol.upper():
                return f"Error: Yahoo不可达, {symbol} 无美股日K兜底; 港股K线请用 tencent-finance-mcp get_history_kline 或 dzh-mcp"
            raw = urllib.request.urlopen(urllib.request.Request(
                US_DAILYK.format(symbol.lower()),
                headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read().decode("utf-8")
            rows = json.loads(raw)
            n = _PERIOD_ROWS.get(period, 252)
            rows = rows[-n:]
            df = pd.DataFrame([{"date": r["d"], "open": r["o"], "high": r["h"],
                                "low": r["l"], "close": r["c"],
                                "volume": r["v"], "amount": r.get("a")}
                               for r in rows])
            df["source"] = "sina"
            return df.to_csv(index=False, header=True)
        except Exception as e2:
            return f"Error: 双源失败 yahoo不可达 + sina:{type(e2).__name__}:{str(e2)[:80]}"


@mcp.tool()
def get_financials(symbol: str, statement: str = "income") -> str:
    """
    获取财务报表 (Yahoo 源; 失败时返回替代源指引)
    Args:
        symbol: 股票代码
        statement: income(利润表)/balance(资产负债表)/cash(现金流量表)
    Returns: 多期财务报表 CSV
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        if statement == "income":
            df = ticker.financials
        elif statement == "balance":
            df = ticker.balance_sheet
        elif statement == "cash":
            df = ticker.cashflow
        else:
            return f"Unknown statement: {statement}"
        if df is None or df.empty:
            return "No data"
        df.columns = [str(c.date()) for c in df.columns]
        return df.to_csv(index=True, header=True)
    except Exception as e:
        return f"Error: {str(e)[:120]}\n{FALLBACK_HINT}"


@mcp.tool()
def get_dividends(symbol: str) -> str:
    """
    获取分红历史 (Yahoo 源; 失败时返回替代源指引)
    Args:
        symbol: 股票代码
    Returns: 分红记录 CSV (日期/金额)
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        divs = ticker.dividends
        if divs is None or divs.empty:
            return "No dividends"
        df = divs.to_frame(name="dividend")
        df.index = df.index.strftime("%Y-%m-%d")
        return df.to_csv(index=True, header=True)
    except Exception as e:
        return f"Error: {str(e)[:120]}\n替代源: dzh-mcp getF10DividentsData(分红), westock data_dividend"


@mcp.tool()
def get_splits(symbol: str) -> str:
    """
    获取拆股历史 (Yahoo 源; 失败时返回替代源指引)
    Args:
        symbol: 股票代码
    Returns: 拆股记录 CSV
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        splits = ticker.splits
        if splits is None or splits.empty:
            return "No splits"
        df = splits.to_frame(name="split_ratio")
        df.index = df.index.strftime("%Y-%m-%d")
        return df.to_csv(index=True, header=True)
    except Exception as e:
        return f"Error: {str(e)[:120]}\n替代源: dzh-mcp getF10ShareStruchgData(股本结构变动)"


@mcp.tool()
def get_institutional_holders(symbol: str) -> str:
    """
    获取机构持仓 (Yahoo 源; 失败时返回替代源指引)
    Args:
        symbol: 股票代码
    Returns: 机构持仓 CSV
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        holders = ticker.institutional_holders
        if holders is None or holders.empty:
            return "No data"
        return holders.to_csv(index=False, header=True)
    except Exception as e:
        return f"Error: {str(e)[:120]}\n替代源: dzh-mcp getF10JgccmxData(机构持股)"


@mcp.tool()
def get_info(symbol: str) -> str:
    """
    获取公司详细信息（基本面、估值、行业等; Yahoo 源, 失败返回替代源指引）
    Args:
        symbol: 股票代码
    Returns: 关键信息 JSON
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info:
            return "No info"
        keys = [
            "symbol", "longName", "shortName", "industry", "sector",
            "marketCap", "enterpriseValue", "trailingPE", "forwardPE",
            "priceToBook", "dividendYield", "payoutRatio",
            "profitMargins", "returnOnEquity", "returnOnAssets",
            "revenueGrowth", "earningsGrowth", "grossMargins",
            "operatingMargins", "currentRatio", "debtToEquity",
            "freeCashflow", "operatingCashflow",
            "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyTwoWeekChange",
            "beta", "averageVolume", "averageVolume10days",
            "sharesOutstanding", "floatShares", "heldPercentInstitutions",
            "targetMeanPrice", "recommendationMean", "numberOfAnalystOpinions"
        ]
        result = {k: info.get(k) for k in keys if k in info}
        return pd.DataFrame([result]).to_json(orient="records", force_ascii=False)
    except Exception as e:
        return (f'[{{"error":"{str(e)[:100]}"}}]\n{FALLBACK_HINT} '
                f'或 stock-data-mcp stock_overview_us')


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
