# -*- coding: utf-8 -*-
"""quantrank1_pipeline.py — 量化综合排名第一(原版)·标准选股管线入口 (SOP二百三十五)。
三道工序: ①EV全A重算(analysis/evrank_original_20261021/evrank_allmarket_20261103.py)→②买卖点判定层逐只过滤
→③标注榜单落盘。铁律: 每次选股输出必带判定档位; CHASE_OK必带【实验追买】标注; NO_TRADE不放行。
用法:
  python analysis/quantrank1_pipeline.py --reuse      # 复用当日EV榜单(快路径, 秒级)
  python analysis/quantrank1_pipeline.py --full       # EV全A重算+判定(慢, 数分钟)
  python analysis/quantrank1_pipeline.py --reuse --skip-held   # 再剔已持仓
"""
import argparse
import io
import json
import os
import subprocess
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "analysis"))

EV_SCRIPT = os.path.join(ROOT, "analysis", "evrank_original_20261021", "evrank_allmarket_20261103.py")
EV_OUT = os.path.join(ROOT, "analysis", "evrank_original_20261021", "evrank_allmarket_out.json")
PIPE_OUT = os.path.join(ROOT, "data", "quantrank1_pipeline_latest.json")


def held_codes() -> set:
    """当前持仓码集合(group_pnl_report最新落盘)。"""
    path = os.path.join(ROOT, "data", "group_pnl_report_20261102.json")
    if not os.path.exists(path):
        return set()
    rep = json.load(open(path, encoding="utf-8"))
    return {str(s["code"])[-6:] for gv in rep["groups_source"].values()
            for s in gv.get("stocks", [])}


def ensure_ev(full: bool) -> list:
    """EV榜单: full=强制重算; 否则复用当日已有(同asof才有效, 跨日提示重算)。"""
    if full or not os.path.exists(EV_OUT):
        subprocess.run([sys.executable, EV_SCRIPT], check=True)
    out = json.load(open(EV_OUT, encoding="utf-8"))
    if not full:
        print(f"[EV] 复用已有榜单 asof={out.get('asof')} (跨日请用 --full 重算)")
    return out.get("ev_rank", [])


def judge_rank(ev_rank: list, skip_held: bool) -> list:
    """判定层逐只扫描+标注。"""
    import entry_exit_gate as g
    held = held_codes() if skip_held else set()
    rows = []
    for x in ev_rank:
        code = x["code"]
        if code in held:
            rows.append(dict(rank=x["rank"], code=code, name=x["name"],
                             exp=x["exp"], verdict="HELD_ALREADY", tag="(已持跳过)"))
            continue
        try:
            s = g.load_structure(code)
            v = g.make_verdict(s)
            rows.append(dict(rank=x["rank"], code=code, name=x["name"], exp=x["exp"],
                             price=s["price"], pivot=[s["pivot_zd"], s["pivot_zg"]],
                             verdict=v["action"],
                             tag="【实验追买】" if v.get("experiment") else "",
                             buy_zone=v["buy_zone"],
                             chase_limit=v.get("chase_limit"),
                             stop_loss=v["stop_loss"], note=v["note"]))
        except Exception as e:
            rows.append(dict(rank=x["rank"], code=code, name=x["name"],
                             exp=x["exp"], verdict="ERROR", tag="", note=str(e)[:80]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--skip-held", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    ev = ensure_ev(full=a.full)
    rows = judge_rank(ev, skip_held=a.skip_held)
    rows = rows[: a.top]
    print()
    print("===== 量化综合排名第一(原版)·选股管线标注榜单 =====")
    print(f"{'名次':<4}{'代码':<8}{'名称':<10}{'EV':<8}{'档位':<11}{'标注':<8}{'买区/追买限价':<16}{'止损'}")
    for r in rows:
        bz = r.get("buy_zone") or (f"追买{r.get('chase_limit')}" if r.get("chase_limit") else "-")
        print(f"{r['rank']:<4}{r['code']:<8}{r['name']:<10}{r['exp']:<+8.1%}{r['verdict']:<11}{r['tag']:<8}{str(bz):<16}{r.get('stop_loss','-')}")
    passable = [r for r in rows if r["verdict"] in ("BUY_ZONE", "CHASE_OK")]
    if passable:
        best = passable[0]
        print()
        print(f"可买榜第一: {best['code']} {best['name']} EV+{best['exp']:.1%} "
              f"{best['verdict']}{best['tag']} 买区/限价{best.get('buy_zone') or best.get('chase_limit')} 止损{best.get('stop_loss')}")
    json.dump(dict(generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   rows=rows),
              open(PIPE_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print()
    print("落盘:", PIPE_OUT)


if __name__ == "__main__":
    main()
