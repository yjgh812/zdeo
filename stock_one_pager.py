#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
stock_one_pager.py — 个股九维一页纸（纯呈现层，零交易权）

源自外部「个股深度分析」skill 的九维框架移植（2026-10-20 主人「全部全量继续」令），
数据全走屠龙可审计源（东财 datacenter/F10/push2 直连免 token + stock_name_lookup），
财务抓取函数复用 financial_quick_read.py。

九维映射：
  ①公司概况(F10 jbzl) ②行情与估值(push2实时) ③财务核心与异常(MAINFINADATA+简版检测, 详单见 financial_quick_read.py)
  ④同业对标(RPT_LICO_FN_CPD 行业中位) ⑤主营构成(F10 zygcfx Top5) ⑥盈利预测与评级(F10 jgyc/pjtj)
  ⑦近期公告(np-anotice 近8条) ⑧行业归属与产业链(声明: 图谱会话层 westock data_industry_chain 可查)
  ⑨边界声明

用法：python stock_one_pager.py --code 600519 [--no-peer]
退出码：0=成功; 2=名称查无/核心数据缺失
"""
import argparse
import json
import statistics
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
import financial_quick_read as fqr  # noqa: E402  复用已验收的东财抓取与格式化函数
import time

from financial_quick_read import (  # noqa: E402
    MAIN_COLS, em_datacenter, fetch_balance_cf, fetch_peer, num, pct_s, yi)
from stock_name_lookup import get_name  # noqa: E402

REPORT_DIR = Path(r"D:\zcodesj\reports")
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
_THROTTLE = [0.0]   # 上次请求时间戳(节流防东财间歇限流)


def _get(url, timeout=15, retries=1):
    for i in range(retries + 1):
        wait = 0.7 - (time.time() - _THROTTLE[0])
        if wait > 0:
            time.sleep(wait)
        _THROTTLE[0] = time.time()
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            if raw[:2] == b"\x1f\x8b":
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "ignore"))
        except Exception:
            if i >= retries:
                raise
            time.sleep(2.0)
    raise RuntimeError("unreachable")
DISCLAIMER = (
    "> ⚠️ 纯呈现零交易权：九维一页纸仅为研究速览，不触发任何交易动作；盈利预测/评级为"
    "券商观点聚合非本系统结论；异常检测为规则化提示。数据=公开披露与东财聚合，缺失即 N/A。"
)


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def push2_quote(secucode: str):
    """实时行情走腾讯源(qt.gtimg, 项目在役源, 反爬远轻于东财push2)。
    返回 dict: name/price/prev/mktcap/pe/pb/turn (无数据字段缺失)。"""
    mkt = "sh" if secucode.endswith(".SH") else ("bj" if secucode.endswith(".BJ") else "sz")
    code = secucode.split(".")[0]
    url = f"https://qt.gtimg.cn/q={mkt}{code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "ignore")
        f = raw.split("~")
        if len(f) < 50:
            return {}
        return {"name": f[1], "price": float(f[3]), "prev": float(f[4]),
                "turn": float(f[38]) if f[38] else None,
                "pe": float(f[39]) if f[39] else None,
                "mktcap": float(f[45]) * 1e8 if f[45] else None,   # 亿→元
                "pb": float(f[46]) if f[46] else None}
    except Exception:
        return {}


def f10_page(page: str, secucode: str):
    code2 = ("SH" if secucode.endswith(".SH") else "SZ") + secucode.split(".")[0]
    url = (f"https://emweb.securities.eastmoney.com/PC_HSF10/{page}/PageAjax?code=" + code2)
    try:
        return _get(url, timeout=15)
    except Exception:
        return {}


def ann_list(code: str, n=8):
    url = ("https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size="
           + str(n) + "&page_index=1&ann_type=A&client_source=web&stock_list=" + code)
    try:
        d = _get(url, timeout=10)
        return [(r.get("notice_date", "")[:10], r.get("title", ""))
                for r in ((d.get("data") or {}).get("list") or [])]
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--no-peer", action="store_true")
    args = ap.parse_args()
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    code = args.code.strip()
    name = get_name(code)
    if not name:
        print(f"[FAIL] 名称接口查无 {code}")
        sys.exit(2)
    secucode = code + (".SH" if code.startswith(("6", "9")) else ".SZ")

    # ①概况 ③财务 ④同业 ⑥预测 复用/抓取
    main_rows, _ = em_datacenter("RPT_F10_FINANCE_MAINFINADATA",
                                 f'(SECUCODE="{secucode}")', cols=MAIN_COLS, pagesize=4)
    if not main_rows:
        print(f"[N/A] {name}({code}) 无财务主要指标")
        sys.exit(2)
    cur = main_rows[0]
    report_date = (cur.get("REPORT_DATE") or "")[:10]
    fqr._peer_self = secucode   # fetch_peer 用其宿主模块全局识别本股行业
    survey_list = f10_page("CompanySurvey", secucode).get("jbzl") or []
    survey = survey_list[0] if survey_list else {}
    bcf = fetch_balance_cf(secucode, [report_date, fqr.prev_period(report_date)])
    bs = bcf.get("zcfzbAjaxNew") or [{}]
    cf = bcf.get("xjllbAjaxNew") or [{}]
    b0, c0 = (bs[0] if bs else {}), (cf[0] if cf else {})
    ba = f10_page("BusinessAnalysis", secucode).get("zygcfx") or []
    pf = f10_page("ProfitForecast", secucode)
    jgyc = pf.get("jgyc") or []
    pjtj = pf.get("pjtj") or []
    quote = push2_quote(secucode)
    anns = ann_list(code)
    peer_lines = []
    if not args.no_peer:
        try:
            ind, med = fetch_peer(True, report_date)
            if ind and ind in med:
                m = med[ind]
                peer_lines.append(f"行业={ind}（样本{m['n']}家）: 营收同比中位 {pct_s(m.get('YSTZ'))} | "
                                  f"净利同比中位 {pct_s(m.get('SJLTZ'))} | 毛利率中位 {pct_s(m.get('XSMLL'))} | "
                                  f"ROE中位 {pct_s(m.get('WEIGHTAVG_ROE'))}")
            else:
                peer_lines.append("行业中位数据缺失 N/A")
        except Exception as e:
            peer_lines.append(f"同业获取失败({type(e).__name__}) N/A")

    # 简版异常提示（详单运行 financial_quick_read.py）
    flags = []
    rev_tz, npf_tz = num(cur.get("TOTALOPERATEREVETZ")), num(cur.get("PARENTNETPROFITTZ"))
    npf, ocf = num(cur.get("PARENTNETPROFIT")), num(c0.get("NETCASH_OPERATE"))
    gw, ta = num(b0.get("GOODWILL")), num(b0.get("TOTAL_ASSETS"))
    if rev_tz is not None and npf_tz is not None and rev_tz > 0 and npf_tz < 0:
        flags.append("营收正增但净利负增")
    if npf and npf > 0 and ocf is not None and ocf < 0:
        flags.append("净利正但经营现金流为负")
    if gw is not None and ta and gw / ta > 0.1:
        flags.append(f"商誉占总资产 {gw / ta * 100:.1f}%")

    L = [f"# 个股九维一页纸 · {name}({code}) [{report_date}]", "", DISCLAIMER, ""]
    L.append("## ① 公司概况")
    L.append(f"- 简称 {survey.get('SECURITY_NAME_ABBR') or name} | 东财行业 {survey.get('EM2016') or 'N/A'}"
             f" | 注册地 {survey.get('PROVINCE') or 'N/A'}")
    L.append(f"- 公司简介：{(survey.get('ORG_PROFILE') or 'N/A')[:200]}")
    L.append("")
    L.append("## ② 行情与估值（实时）")
    if quote:
        chg = ((quote.get("price") / quote.get("prev") - 1) * 100
               if quote.get("price") and quote.get("prev") else None)
        L.append(f"- 现价 {quote.get('price')}（今日 {pct_s(chg)}）| 总市值 {yi(quote.get('mktcap'))}"
                 f" | PE(TTM) {quote.get('pe')} | PB {quote.get('pb')} | 换手 {pct_s(quote.get('turn'))}")
    else:
        L.append("- 实时行情获取失败 N/A")
    L.append("")
    L.append("## ③ 财务核心与异常提示")
    L.append(f"- 营收 {yi(num(cur.get('TOTALOPERATEREVE')))}（同比 {pct_s(rev_tz)}）| 归母净利 "
             f"{yi(npf)}（同比 {pct_s(npf_tz)}）| ROE加权 {pct_s(num(cur.get('ROEJQ')))}")
    L.append(f"- 毛利率 {pct_s(num(cur.get('XSMLL')))} | 净利率 {pct_s(num(cur.get('XSJLL')))} | "
             f"负债率 {pct_s(num(cur.get('ZCFZL')))} | 现金流/净利 "
             f"{f'{ocf / npf:.2f}倍' if (ocf is not None and npf not in (None, 0)) else 'N/A'}")
    L.append("- 异常提示：" + ("；".join(flags) if flags else "简版三项未触发（七维详单: python financial_quick_read.py --code）"))
    L.append("")
    L.append("## ④ 同业对标")
    L.extend("  " + p for p in peer_lines)
    L.append("")
    L.append("## ⑤ 主营构成（最新披露期 Top5）")
    if ba:
        latest_bd = max((r.get("REPORT_DATE") or "") for r in ba if r.get("REPORT_DATE"))
        rows5 = [r for r in ba if r.get("REPORT_DATE") == latest_bd
                 and r.get("MAINOP_TYPE") == "2"][:5]
    else:
        rows5 = []
    if rows5:
        L.append(f"- 报告期 {latest_bd[:10]}")
        L.append("|业务|收入|占比|")
        L.append("|---|---|---|")
        for r in rows5:
            inc = num(r.get("MAIN_BUSINESS_INCOME"))
            ratio = num(r.get("MBI_RATIO"))
            L.append(f"|{r.get('ITEM_NAME')}|{yi(inc)}|{f'{ratio*100:.1f}%' if ratio is not None else 'N/A'}|")
    else:
        L.append("- 主营构成数据缺失 N/A")
    L.append("")
    L.append("## ⑥ 盈利预测与评级（券商观点聚合，非本系统结论）")
    avg = next((r for r in jgyc if r.get("ORG_CODE") == "00000000"), None)
    if avg:
        L.append(f"- 机构覆盖 {sum(1 for r in jgyc if r.get('ORG_CODE') != '00000000')} 家"
                 f"（近六月一致预期，发布 {str(avg.get('PUBLISH_DATE'))[:10]}）")
        L.append("|年度|一致预期EPS|对应PE|")
        L.append("|---|---|---|")
        for i in range(1, 5):
            yr, eps, pe = avg.get(f"YEAR{i}"), num(avg.get(f"EPS{i}")), num(avg.get(f"PE{i}"))
            if yr and eps:
                L.append(f"|{yr}|{eps:.2f}|{pe:.1f}|")
    else:
        L.append("- 盈利预测数据缺失 N/A")
    L.append("")
    L.append("## ⑦ 近期公告（近8条）")
    if anns:
        for d, t in anns:
            L.append(f"- {d} {t}")
    else:
        L.append("- 公告获取失败 N/A")
    L.append("")
    L.append("## ⑧ 行业归属与产业链")
    L.append(f"- 东财行业 {survey.get('EM2016') or 'N/A'}；产业链上中下游图谱可在屠龙会话用 "
             f"westock data_industry_chain（theme/graph）查询，本件不重复建设。")
    L.append("")
    L.append("## ⑨ 边界声明")
    L.append("- 本件为呈现层速览；任何信号化使用须另走 prereg+影子+主人裁决。")
    L.append("")

    out = "\n".join(L)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rp = REPORT_DIR / f"STOCK_ONE_PAGER_{code}.md"
    rp.write_text(out, encoding="utf-8")
    print(out)
    print(f"[OK] {rp}")


if __name__ == "__main__":
    main()
