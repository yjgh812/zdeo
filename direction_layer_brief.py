# -*- coding: utf-8 -*-
"""方向层三源合并简报 direction_layer_brief.py（2026-09-20 落地,主人"方向层三位一体"裁决）

方法论真源: 记忆 owner-methodology-mainline-first——方向层=主线(量能C1+情绪复盘并集)+消息面+复盘,
三源同位互补;输出=当日方向判断(主线方向/逆风方向/中性),供漏斗第一环消费。
红线: 方向=收敛视野+排除逆风,非追热点非过滤器;三源冲突时并列呈报不强行合一。

数据源(全只读):
  源1 主线 = reports/daily_review_<T>.json 的 industry_volume_c1(C1量能) + main_sectors(情绪复盘领涨)
  源2 复盘 = 同 json 的 risk_warnings + market_env
  源3 消息面 = reports/premarket_intel_<date>.md 若存在(方向段表格), 无则标N/A

用法: python direction_layer_brief.py [--date 20260921]
输出: data/direction_layer_latest.json + stdout 格式化三源段
退出码: 0=正常(N/A允许) 2=结构化失败(源文件缺失/损坏)
"""
import argparse, glob, json, os, re, sys
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
OUT = os.path.join(ROOT, "data", "direction_layer_latest.json")

def fail(msg):
    print(f"[direction_layer] FAIL: {msg}")
    sys.exit(2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="目标交易日(如20260921),默认=自动找最近复盘json")
    a = ap.parse_args()

    # 源1+2: 最近交易日复盘json
    # 11/3修复: glob命中daily_review_cases_20261023.json变体(cases_字典序>纯日期,c=99>'2'=50,
    # 排最后被jss[-1]选中) → 正则提日期None→AttributeError(11/2 08:05 DirectionBrief_0805 rc=1实锤)。
    # 根治=过滤纯日期文件再按mtime取最新: 变体名天然排除, 未来变体名免疫; 兜底fail结构化返回。
    jss = [p for p in glob.glob(os.path.join(ROOT, "reports", "daily_review_*.json"))
           if re.search(r"daily_review_(\d{8})\.json$", os.path.basename(p))]
    if not jss:
        fail("无 daily_review_YYYYMMDD.json(源1主线+源2复盘均缺失)")
    jpath = max(jss, key=os.path.getmtime)
    jdate = re.search(r"daily_review_(\d{8})\.json$", os.path.basename(jpath)).group(1)
    try:
        d = json.load(open(jpath, encoding="utf-8"))
    except Exception as e:
        fail(f"复盘json损坏 {jpath}: {e}")

    c1 = d.get("industry_volume_c1") or {}
    c1_inds = c1.get("c1_industries") or []
    volr_top = c1.get("volr_top3") or []
    main_secs = d.get("main_sectors") or []
    risks = d.get("risk_warnings") or []
    menv = (d.get("market_env") or "")[:160]

    # 源3: 消息面(三级兜底: 当日morning_brief定稿→draft→最近premarket_intel)
    target = a.date or jdate
    pi_tag, pi_lines, pi_src = "N/A", [], "none"
    mb_final = os.path.join(ROOT, "reports", f"morning_brief_{target}.md")
    mb_draft = os.path.join(ROOT, "reports", f"morning_brief_{target}_draft.md")
    mb_path = mb_final if os.path.exists(mb_final) else (mb_draft if os.path.exists(mb_draft) else None)
    if mb_path:
        pi_src = "morning_brief" + ("(定稿)" if mb_path == mb_final else "(draft)")
        pi_tag = mb_path
        kw = ("主线", "领涨", "涨", "半导体", "AI", "政策", "宏观", "美股", "加息", "降息", "地缘", "中东", "油价", "利好", "利空", "净流入", "主线判定", "宏观环境", " REGIME", "REGIME")
        try:
            for ln in open(mb_path, encoding="utf-8"):
                s = ln.strip()
                if not (s.startswith(("- ", "* ", "1.", "2.", "3.", "4.", "5.")) or s.startswith("##")):
                    continue
                if any(k in s for k in kw) and len(s) > 12 and "无数据" not in s and "N/A" not in s:
                    pi_lines.append(s.lstrip("-*0123456789. ").strip()[:80])
                if len(pi_lines) >= 8:
                    break
        except Exception:
            pi_lines = []
    if not pi_lines:
        pis = sorted(glob.glob(os.path.join(ROOT, "reports", "premarket_intel_*.md")))
        if pis:
            ppath = pis[-1]
            pi_date = re.search(r"premarket_intel_(\d+)\.md", ppath).group(1)
            stale_days = (datetime.now() - datetime.strptime(pi_date, "%Y%m%d")).days if pi_date.isdigit() else 99
            pi_tag = f"{ppath}(D{stale_days})"
            pi_src = "premarket_intel"
            try:
                for ln in open(ppath, encoding="utf-8"):
                    if not ln.strip().startswith("|"):
                        continue
                    cells = [c.strip() for c in ln.strip().strip("|").split("|")]
                    if len(cells) >= 3 and cells[0] not in ("关联", "") and not set(cells[0]) <= {"-", ":"}:
                        pi_lines.append(f"{cells[0]}: {cells[2][:70]}")
            except Exception:
                pi_lines = []

    # 源4(2026-10-18 呈请B): 板块六维状态(呈现级附加section; 失败降级unavailable不阻塞主三源合同)
    try:
        from sector_six_dims import build_six_dims
        six = build_six_dims(flow_hints=[r for r in risks if "行业流出" in r or "行业流入" in r])
        six_src = "sector_six_dims"
    except Exception as e:
        six = {"status": "unavailable", "reason": str(e)[:200]}
        six_src = f"unavailable({type(e).__name__})"

    # 合成(三源并列, 冲突不合一)
    out = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "target_date": a.date or jdate,
        "review_date": jdate,
        "source1_mainline": {
            "c1_volume": c1_inds,
            "volr_top3": [(v.get("industry"), round(v.get("volr20", 0), 3)) for v in volr_top],
            "sentiment_leaders": main_secs[:5],
        },
        "source2_review": {
            "market_env": menv,
            "risk_warnings": risks[:6],
        },
        "source3_news": {"src": pi_src, "file": pi_tag, "direction_rows": pi_lines[:8], "note": "三级兜底: 当日morning_brief(定稿/draft)→premarket_intel→N/A(执行前以preflight查二/查三为准)"},
        "sector_six_dims": {"src": six_src, **six},
        "usage": "方向层=收敛视野+排除逆风,非追热点非过滤器;三源冲突并列呈报;sector_six_dims=六维呈现附加层零交易权(呈请B 2026-10-18),R41等既有消费字段不受影响",
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=== 方向层三源合并简报 (方向层三位一体: 主线+消息面+复盘) ===")
    print(f"[目标日 {out['target_date']} | 复盘基期 {jdate} | 生成 {out['generated_at']}]")
    print(f"源1 主线(C1量能): {('、'.join(c1_inds)) if c1_inds else '无C1(全部行业量能未达1.3)'}")
    print(f"源1 主线(量能Top3): " + "、".join(f"{i}({v})" for i, v in out["source1_mainline"]["volr_top3"]))
    print(f"源1 主线(情绪复盘领涨): {'、'.join(main_secs[:5]) or 'N/A'}")
    print(f"源2 复盘(环境): {menv}")
    print(f"源2 复盘(逆风): {'；'.join(risks[:4]) or 'N/A'}")
    print(f"源3 消息面: {pi_tag}")
    for ln in pi_lines[:4]:
        print(f"    - {ln}")
    if not pi_lines:
        print("    - N/A(无结构化消息面产物; 执行前以preflight查二宏观/查三个股新闻为准)")
    print(f"[产物: {OUT}]")

if __name__ == "__main__":
    main()
