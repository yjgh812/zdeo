# -*- coding: utf-8 -*-
"""source_race_judgment.py — 赛马判罚候选生成器(纯呈现层, 零交易权)

输入: data/settle_t20_report_*.json (T+20满窗结算=唯一权威, 按内容asof取最新, 规避文件名测试残留)
规则: data/source_retirement_proposal_20261031.json (阈值从condition字段正则解析=单一真源,
      解析失败结构化退出 fail-closed, 不硬编码第二份阈值)
输出: 终端候选表 + data/source_race_judgment_<asof>.json
边界: 零交易权——不迁移源、不改闸门、不接executor;
      判罚动作=人工编辑 _common.OBSERVE_SOURCES + 上报主人终审(SOP二百二十六⑧)
用法: python analysis/source_race_judgment.py [--report <path.json>] [--drytest]
"""
import os, sys, json, re, glob, time, argparse
from collections import OrderedDict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
RULES = os.path.join(ROOT, "data", "source_retirement_proposal_20261031.json")


def parse_cond(cond, promote):
    """从规则condition文本解析阈值(11/26判罚单一真源=规则JSON, 不复制常量)。
    退役/观察形: 满窗>=8笔 且 均值<=-3% 且 胜率<=35%   (退役候选可无胜率项)
    提名形:      满窗>=5笔 且 均值>=+3% 且 胜率>=60%
    解析失败 -> ValueError 结构化报错, 禁止静默取默认值。"""
    m_n = re.search(r"满窗\s*>=\s*(\d+)\s*笔", cond)
    m_mean_le = re.search(r"均值\s*<=\s*(-?\d+(?:\.\d+)?)\s*%", cond)
    m_mean_ge = re.search(r"均值\s*>=\s*\+?(-?\d+(?:\.\d+)?)\s*%", cond)
    m_win = re.search(r"胜率\s*<=\s*(\d+(?:\.\d+)?)\s*%|胜率\s*>=\s*(\d+(?:\.\d+)?)\s*%", cond)
    if not m_n:
        raise ValueError("无法解析满窗阈值: %s" % cond)
    out = {"n": int(m_n.group(1))}
    if promote:
        if not (m_mean_ge and m_win and m_win.group(2)):
            raise ValueError("无法解析提名条件: %s" % cond)
        out["mean_min"] = float(m_mean_ge.group(1))
        out["win_min"] = float(m_win.group(2))
    else:
        if not m_mean_le:
            raise ValueError("无法解析退役/观察条件: %s" % cond)
        out["mean_max"] = float(m_mean_le.group(1))
        if m_win and m_win.group(1):
            out["win_max"] = float(m_win.group(1))
        elif m_win:
            # 退役条件含非预期"胜率>=X"形态: 当前规则无此形, 出现即结构化拒绝(m7备注修复, 防静默误判)
            raise ValueError("退役/观察条件含非预期胜率形态(>=): %s" % cond)
        else:
            out["win_max"] = None
    return out


def load_rules():
    r = json.load(open(RULES, encoding="utf-8"))
    retire = [{"level": it["level"], **parse_cond(it["condition"], promote=False)}
              for it in r.get("retire_rules", [])]
    promote = [{"level": it["level"], **parse_cond(it["condition"], promote=True)}
               for it in r.get("promote_rules", [])]
    return retire, promote


def judge(st, retire, promote):
    """命中level列表(可能多命中); 退役候选优先呈现。st={n,mean,win}。"""
    n, mean, win = st.get("n", 0), st.get("mean"), st.get("win")
    if not n or mean is None or win is None:
        return []
    hits = []
    for r_ in promote:
        if n >= r_["n"] and mean >= r_["mean_min"] and win >= r_["win_min"]:
            hits.append(r_["level"])
    for r_ in retire:
        if n >= r_["n"] and mean <= r_["mean_max"] and (
                r_.get("win_max") is None or win <= r_["win_max"]):
            hits.append(r_["level"])
    order = {"退役候选": 0, "观察": 1}
    hits.sort(key=lambda h: order.get(h, 2))
    return hits


def latest_report():
    # 守卫: asof > 今日 = --asof 测试残留(如20270601), 不作为最新报告(m7同款教训: 按内容取需界)
    today = int(time.strftime("%Y%m%d"))
    best, best_asof = None, -1
    for f in glob.glob(os.path.join(ROOT, "data", "settle_t20_report_*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        a = int(d.get("asof") or 0)
        if a > best_asof and a <= today:
            best, best_asof = d, a
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="", help="指定settle报告路径; 缺省=内容asof最新")
    ap.add_argument("--drytest", action="store_true",
                    help="合成三臂+边界演练(纯逻辑验证, 非生产数据, 不落盘)")
    args = ap.parse_args()

    retire, promote = load_rules()
    print("规则解析: 退役/观察 %d 条 + 提名 %d 条" % (len(retire), len(promote)))
    for r_ in retire + promote:
        if "mean_max" in r_:
            mtxt = "<=%.1f%%" % r_["mean_max"]
            wtxt = ("<=%.1f%%" % r_["win_max"]) if r_.get("win_max") is not None else "不限"
        else:
            mtxt = ">=+%.1f%%" % r_["mean_min"]
            wtxt = ">=%.1f%%" % r_["win_min"]
        print("  %-6s n>=%s 均值%s 胜率%s" % (r_["level"], r_["n"], mtxt, wtxt))

    if args.drytest:
        rep = {"asof": 0, "sources": {
            "drytest_observe":  {"settled": {"n": 8, "mean": -4.0, "win": 30.0, "excess": -8.0}},
            "drytest_retire":   {"settled": {"n": 8, "mean": -7.0, "win": 25.0, "excess": -13.0}},
            "drytest_promote":  {"settled": {"n": 6, "mean": 4.0, "win": 65.0, "excess": 2.0}},
            "drytest_boundary": {"settled": {"n": 8, "mean": -3.0, "win": 35.0, "excess": -6.5}},
            "manual_confirmed": {"settled": {"n": 20, "mean": -9.0, "win": 10.0}},
            "drytest_below":    {"settled": {"n": 3, "mean": -9.0, "win": 10.0}},
        }}
    else:
        rep = json.load(open(args.report, encoding="utf-8")) if args.report else latest_report()
        if not rep:
            print("无可用 settle 报告 → 结构化退出")
            return 2

    asof = rep.get("asof", 0)
    hits_out = OrderedDict()
    scanned, pending_only, excluded_manual = 0, 0, []
    for src, v in rep.get("sources", {}).items():
        st = v.get("settled", {})
        if not st.get("n"):
            pending_only += 1
            continue
        scanned += 1
        if src.startswith("manual"):
            excluded_manual.append(src)
            continue
        hits = judge(st, retire, promote)
        if hits:
            hits_out[src] = {"hits": hits, "n": st["n"], "mean": st["mean"], "win": st["win"],
                             "excess": st.get("excess")}

    print("")
    print("=== 赛马判罚候选 (asof=%s) ===" % asof)
    print("已结算源=%d 未满窗源=%d 人工仓排除=%s" % (scanned, pending_only, excluded_manual or "无"))
    if hits_out:
        for src, h in hits_out.items():
            xs = h.get("excess")
            xtxt = (" 超额%+.2f%%" % xs) if xs is not None else ""
            print("  %-26s %s | n=%d 均值=%+.2f%% 胜率=%.1f%%%s"
                  % (src, "/".join(h["hits"]), h["n"], h["mean"], h["win"], xtxt))
        print("→ 处置: 退役候选/观察=上报主人终审后人工编辑 _common.OBSERVE_SOURCES; 提名=上报主人加预算")
    else:
        print("  零判罚候选(全部未达线或未满窗)")
    if args.drytest:
        assert "drytest_observe" in hits_out and "观察" in hits_out["drytest_observe"]["hits"], "观察臂未命中"
        assert "drytest_retire" in hits_out and hits_out["drytest_retire"]["hits"][0] == "退役候选", "退役臂未命中或优先级错"
        assert "drytest_promote" in hits_out and "加预算提名" in hits_out["drytest_promote"]["hits"], "提名臂未命中"
        assert "drytest_boundary" in hits_out and "观察" in hits_out["drytest_boundary"]["hits"], "边界(-3.0/35.0)未命中"
        assert "manual_confirmed" in excluded_manual and "manual_confirmed" not in hits_out, "人工仓未排除"
        assert "drytest_below" not in hits_out, "未达线源误命中"
        print("drytest PASS: 观察/退役/提名三臂+边界命中 + 人工仓排除 + 未达线零误伤")

    if not args.drytest:
        out = os.path.join(ROOT, "data", "source_race_judgment_%s.json" % asof)
        json.dump({"asof": asof, "generated_at": time.strftime("%Y-%m-%d %H:%M"),
                   "rules_parsed": {"retire": retire, "promote": promote},
                   "scanned": scanned, "pending_only": pending_only,
                   "excluded_manual": excluded_manual, "candidates": hits_out,
                   "authority": "零交易权呈现层; 判罚=人工编辑_common.OBSERVE_SOURCES+主人终审"},
                  open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("落盘: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
