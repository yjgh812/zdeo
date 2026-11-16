# -*- coding: utf-8 -*-
"""财联社重点电报每日归档器 (20261011 主人令'b'=批准财联社前瞻影子, 数据层)

定位: 前瞻影子**数据层**——每日归档财联社加红(A)/重点(B)电报, 为未来严定义漏斗事件
  提供个股新闻暴露特征(提及数/加红条数/主题命中); 历史回填已证不可行(cls_more 无日期
  寻址, 见 reports/cls 探针结论与记忆 cls-news-probe-20261011), 本件从今日起 forward-only 造测试集。
源: 财联社官方API, 签名语义唯一 owner=cls_news_mcp._cls_request (本件复用不复制)。
归档: data/cls_news_archive.jsonl (append, 按 id 幂等去重, 断点安全; 每行=id/ctime/dt/level/title/brief/stock_list/subjects)。
prereg: data/PREREG_CLS_NEWS_SHADOW_20261011.json (60交易日三门, 零交易权, 判读权主人;
  判读件=analysis/cls_news_shadow_judge.py 于影子满 60 交易日前落地, 到期呈请)。
挂载: daily_chain[cls_news] 槽位 (timeout 300, 失败不阻断, 同 combo 先例)。
自检: 写后读回核行数; 新增 0=合法幂等态; 结构化错误 exit 1(链日志可查)。
"""
import os
import sys
import json
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from cls_news_mcp import _cls_request  # 签名语义单一owner, 禁复制

ARCHIVE = r"D:\zcodesj\data\cls_news_archive.jsonl"
RN = 30        # CLS 单次上限(实测 rn=100 返回空, 30 可用)
MAX_PAGES = 40  # 回补封顶(约1-2天电报量, 防失控)


def main():
    seen = set()
    n_exist = 0
    if os.path.exists(ARCHIVE):
        with open(ARCHIVE, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["id"])
                    n_exist += 1
                except Exception:
                    continue
    # 回补循环: 从最新往回翻页, 直到撞到已归档交集/空批/封顶
    collected, last_ct, pages = {}, None, 0
    while pages < MAX_PAGES:
        extra = {"rn": RN}
        if last_ct is not None:
            extra["last_time"] = last_ct
        items = _cls_request("/v1/roll/get_roll_list", extra)
        if not items:
            break
        pages += 1
        hit_old = False
        for it in items:
            iid = it.get("id")
            ct = it.get("ctime")
            if iid in seen:
                hit_old = True
                continue
            if it.get("level") in ("A", "B") and iid is not None and iid not in collected:
                collected[iid] = {
                    "id": iid,
                    "ctime": ct,
                    "dt": datetime.fromtimestamp(ct or 0).strftime("%Y-%m-%d %H:%M"),
                    "level": it.get("level"),
                    "title": (it.get("title") or "")[:120],
                    "brief": (it.get("brief") or it.get("content") or "")[:500],
                    "stock_list": it.get("stock_list") or [],
                    "subjects": [s.get("subject_name") for s in (it.get("subjects") or []) if isinstance(s, dict)],
                }
            if isinstance(ct, int) and (last_ct is None or ct < last_ct):
                last_ct = ct
        if hit_old:
            break
    rows = sorted(collected.values(), key=lambda r: r["ctime"] or 0)
    if rows:
        with open(ARCHIVE, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    # 自检: 读回核数(空文件且无新增=合法首跑空态)
    n_after = 0
    if os.path.exists(ARCHIVE):
        with open(ARCHIVE, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    n_after += 1
    assert n_after == n_exist + len(rows), f"读回核数失败 {n_after} != {n_exist}+{len(rows)}"
    ab = [f"{r['level']}:{(r['title'] or r['brief'])[:24]}" for r in rows[-3:]]
    print(f"[cls_archive] pages={pages} 存量={n_exist} 新增AB={len(rows)} 总={n_after} 最新样例={ab if ab else '无'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[cls_archive] FAIL {type(e).__name__}: {e}")
        sys.exit(1)
