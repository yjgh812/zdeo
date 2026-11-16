#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy_agent_teams_dashboard.py — 把 AgentTeams 活动面板部署到 Node-RED (18800)。

路由:
  GET /agent-teams/data  → exec 跑 tulong_agent_teams.py dashboard-data → JSON
  GET /agent-teams/ui    → template 渲染单页面板(深色, 5秒轮询)

部署方式: Node-RED Admin API POST /flows (v2 原子替换), 部署前自动备份 flows.json。
仅追加 agent_teams 节点, 现有屠龙工作台 tab 不动; 重复执行先清理旧 agent_teams 节点。
"""
import json
import shutil
import urllib.request
from pathlib import Path

NRED = "http://localhost:18800"
FLOWS_FILE = Path("D:/zcodesj/node-red-data/flows.json")
BACKUP = FLOWS_FILE.with_name("flows.json.bak-agentteams")

TAB_ID = "at_tab"
NODE_IDS = {TAB_ID, "at_http_data", "at_exec", "at_resp_data",
            "at_http_ui", "at_tpl", "at_resp_ui", "at_teams_inj", "at_teams_exec"}

PANEL_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>屠龙 AgentTeams 活动面板</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background:#0d1117; color:#c9d1d9; font:14px/1.6 "Microsoft YaHei",sans-serif; padding:20px; }
  h1 { font-size:20px; color:#58a6ff; margin-bottom:4px; }
  .sub { color:#8b949e; font-size:12px; margin-bottom:18px; }
  .team { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; margin-bottom:18px; }
  .team h2 { font-size:16px; color:#f0f6fc; margin-bottom:2px; }
  .goal { color:#8b949e; font-size:13px; margin-bottom:10px; }
  .bar { height:8px; background:#21262d; border-radius:4px; overflow:hidden; margin:8px 0 14px; }
  .bar>div { height:100%; background:#238636; transition:width .4s; }
  .grid { display:grid; grid-template-columns:1fr 1.4fr; gap:16px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:#8b949e; font-weight:normal; border-bottom:1px solid #30363d; padding:4px 8px; }
  td { border-bottom:1px solid #21262d; padding:4px 8px; vertical-align:top; }
  .tag { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; }
  .done    { background:#1b4721; color:#7ee787; }
  .claimed { background:#1c3a5e; color:#79c0ff; }
  .pending { background:#2d333b; color:#8b949e; }
  .blocked { background:#4a2c00; color:#ffa657; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
  .working { background:#58a6ff; box-shadow:0 0 6px #58a6ff; }
  .idle { background:#484f58; }
  .unread { color:#ffa657; font-weight:bold; }
  .events { margin-top:14px; border-top:1px dashed #30363d; padding-top:10px; font-size:12px; color:#8b949e; }
  .events div { padding:1px 0; }
  .archived-head { color:#8b949e; font-size:15px; margin:20px 0 8px; border-bottom:1px solid #30363d; padding-bottom:4px; }
  .team.archived-card { opacity:.82; border-style:dashed; }
  .team.archived-card h2::after { content:"已归档"; font-size:11px; color:#8b949e; border:1px solid #484f58; border-radius:3px; padding:0 5px; margin-left:8px; vertical-align:middle; }
  .team .tasks-output { max-width:340px; word-break:break-all; }
  .empty { color:#484f58; padding:40px; text-align:center; }
  .agentid { color:#6e7681; font-size:11px; }
  details summary { cursor:pointer; }
</style>
</head>
<body>
<h1>屠龙 AgentTeams 活动面板</h1>
<div class="sub">磁盘真相: D:/zcodesj/.agent-teams &nbsp;|&nbsp; 每5秒自动刷新 &nbsp;|&nbsp; <span id="ts"></span></div>
<div id="root"><div class="empty">暂无活动团队 — 用 tulong_agent_teams.py create-team 建队后自动出现</div></div>
<script>
const KIND = {team_created:"建队", member_added:"加成员", agent_bound:"绑Agent", member_status:"成员状态",
  task_created:"建任务", task_claimed:"领任务", task_done:"完任务", task_status_set:"改任务状态",
  message_sent:"发消息", team_archived:"归档"};
const STAT = {done:"done", claimed:"进行中", pending:"待领取", blocked:"被阻塞"};
function esc(s){ return String(s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function teamCard(t, arch){
    const pct = t.progress.total ? Math.round(100*t.progress.done/t.progress.total) : 0;
    // 拓扑线性化:依赖全在已排序列中的任务依次入列
    const order = [], rest = [...t.tasks];
    while(rest.length){
      const idx = rest.findIndex(k=>k.dependsOn.every(d=>order.includes(d)));
      if(idx<0){ order.push(...rest.map(k=>k.id)); break; }
      order.push(rest.splice(idx,1)[0].id);
    }
    const chain = order.map(id=>{const k=t.tasks.find(x=>x.id===id);
      return `<span class="tag ${k.display}" title="${esc(k.title)}">${id} ${k.status==="done"?"✓":""}</span>`;}).join("<span style='color:#484f58'> → </span>");
    const members = t.members.map(m=>`<tr>
      <td><span class="dot ${m.status}"></span>${esc(m.name)} <span class="agentid">${m.id}${m.agentId?" · "+esc(m.agentId):""}</span></td>
      <td>${esc(m.role)}</td>
      <td>${m.currentTask?esc(m.currentTask):"—"}</td>
      <td>${m.unread?`<span class="unread">${m.unread} 未读</span>`:"0"}</td></tr>`).join("");
    const tasks = t.tasks.map(k=>`<tr>
      <td>${esc(k.id)}</td><td>${esc(k.title)}</td>
      <td>${esc(k.owner||"—")}</td>
      <td>${k.dependsOn.length?esc(k.dependsOn.join(",")):"—"}</td>
      <td><span class="tag ${k.display}">${STAT[k.display]||k.display}</span></td>
      <td class="tasks-output">${esc((k.output||"").slice(0,200))}</td></tr>`).join("");
    const evs = t.recentEvents.slice().reverse().map(e=>
      `<div>[${esc(e.ts)}] ${KIND[e.kind]||e.kind} ${esc(JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k])=>!["ts","kind"].includes(k)))))}</div>`).join("");
    const msgs = (t.recentMessages||[]).map(m=>
      `<div>[${esc(m.ts)}] <b>${esc(m.from)}</b> → <b>${esc(m.to)}</b>: ${esc(m.body.slice(0,110))}${m.body.length>110?"…":""}</div>`).join("");
    return `<div class="team${arch?" archived-card":""}">
      <h2>${esc(t.id)} ${t.captainUnread?`<span class="unread">队长信箱 ${t.captainUnread} 未读</span>`:""}</h2>
      <div class="goal">目标: ${esc(t.goal)} · 建于 ${esc(t.createdAt)}${arch?" · 归档 "+esc(t.archivedAt||""):""} · 进度 ${t.progress.done}/${t.progress.total}</div>
      ${t.tasks.length?`<div style="margin:2px 0 8px;font-size:13px;">执行链: ${chain}</div>`:""}
      <div class="bar"><div style="width:${pct}%"></div></div>
      <div class="grid"><div><table><tr><th>成员</th><th>角色</th><th>当前</th><th>信箱</th></tr>${members}</table></div>
      <div><table><tr><th>ID</th><th>任务</th><th>负责</th><th>依赖</th><th>状态</th><th>产出</th></tr>${tasks}</table></div></div>
      ${msgs?`<div class="events">📨 直达消息流:<br>${msgs}</div>`:""}
      <div class="events">${evs}</div></div>`;
}
async function tick(){ try{ const r=await fetch("/agent-teams/data"); render(await r.json()); }catch(e){ document.getElementById("ts").textContent="取数失败: "+e; } }
function render(d){
  document.getElementById("ts").textContent = "数据时间: " + d.generatedAt;
  const el = document.getElementById("root");
  let html = "";
  if(d.activeTeams && d.activeTeams.length){ html += d.activeTeams.map(t=>teamCard(t,false)).join(""); }
  else { html += '<div class="empty">暂无活动团队 — 用 tulong_agent_teams.py create-team 建队后自动出现</div>'; }
  if(d.archivedTeams && d.archivedTeams.length){
    html += `<div class="archived-head">📦 归档团队 (${d.archivedTeams.length}) — 点开查看完整成员聊天与任务产出</div>` +
      d.archivedTeams.slice().reverse().map(t=>`<details><summary style="padding:6px 0;color:#58a6ff;">${esc(t.id)} · ${esc((t.goal||"").slice(0,50))}</summary>${teamCard(t,true)}</details>`).join("");
  }
  el.innerHTML = html;
}
tick(); setInterval(tick, 5000);
</script>
</body>
</html>"""


def build_nodes():
    tab = {"id": TAB_ID, "type": "tab", "label": "AgentTeams 面板", "disabled": False, "info": ""}
    # 数据路由: http in -> exec -> http response
    http_data = {"id": "at_http_data", "type": "http in", "name": "GET /agent-teams/data",
                 "url": "/agent-teams/data", "method": "get", "upload": False, "swaggerDoc": "",
                 "wires": [["at_exec"]], "z": TAB_ID}
    exec_node = {"id": "at_exec", "type": "exec", "name": "跑 dashboard-data",
                 "command": "python D:/zcodesj/tulong_agent_teams.py dashboard-data",
                 "addpay": False, "append": "", "useSpawn": "false", "timer": "", "winHide": False,
                 "oldStyle": False, "name_": "", "wires": [["at_resp_data"]], "z": TAB_ID}
    resp_data = {"id": "at_resp_data", "type": "http response", "name": "返回JSON",
                 "statusCode": "", "headers": {"Content-Type": "application/json; charset=utf-8"},
                 "wires": [], "z": TAB_ID}
    # 界面路由: http in -> template -> http response
    http_ui = {"id": "at_http_ui", "type": "http in", "name": "GET /agent-teams/ui",
               "url": "/agent-teams/ui", "method": "get", "upload": False, "swaggerDoc": "",
               "wires": [["at_tpl"]], "z": TAB_ID}
    tpl = {"id": "at_tpl", "type": "template", "name": "AgentTeams 面板HTML",
           "field": "payload", "fieldType": "msg", "format": "html", "syntax": "none",
           "template": PANEL_HTML, "output": "str", "wires": [["at_resp_ui"]], "z": TAB_ID}
    resp_ui = {"id": "at_resp_ui", "type": "http response", "name": "返回HTML",
               "statusCode": "", "headers": {"Content-Type": "text/html; charset=utf-8"},
               "wires": [], "z": TAB_ID}
    # 屠龙工作台第5按钮: AgentTeams 团队状态 (与现有4按钮同模式, 放tulong_tab, 输出接n_dbg)
    btn_inj = {"id": "at_teams_inj", "type": "inject", "name": "🤖 AgentTeams 团队状态",
               "props": [{"p": "payload"}, {"p": "topic", "vt": "str"}], "repeat": "", "crontab": "",
               "once": False, "onceDelay": 0.1, "topic": "", "payload": "", "payloadType": "date",
               "wires": [["at_teams_exec"]], "z": "tulong_tab"}
    btn_exec = {"id": "at_teams_exec", "type": "exec", "name": "tulong_agent_teams list-teams",
                "command": "python D:/zcodesj/tulong_agent_teams.py list-teams --all",
                "addpay": False, "append": "", "useSpawn": "false", "timer": "", "winHide": False,
                "oldStyle": False, "wires": [["n_dbg"], [], []], "z": "tulong_tab"}
    return [tab, http_data, exec_node, resp_data, http_ui, tpl, resp_ui, btn_inj, btn_exec]


def main():
    # 1. 备份
    shutil.copy2(FLOWS_FILE, BACKUP)
    # 2. GET 当前 flows + revision token
    req = urllib.request.Request(f"{NRED}/flows")
    with urllib.request.urlopen(req, timeout=10) as r:
        flows = json.loads(r.read())
        rev = r.headers.get("Node-RED-Flow-Version") or r.headers.get("ETag", "").strip('"')
    # 3. 剔除旧的 agent_teams 节点, 追加新节点 (幂等)
    flows = [n for n in flows if n.get("id") not in NODE_IDS]
    flows.extend(build_nodes())
    # 4. POST v1 全量部署 (body 直接是 flows 数组, 该版本 Node-RED 不支持 v2 rev)
    body = json.dumps(flows).encode()
    req = urllib.request.Request(f"{NRED}/flows", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Node-RED-API-Version": "v1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        print("部署响应:", r.status, r.read().decode()[:200])
    # 5. 验证
    for path in ("/agent-teams/ui", "/agent-teams/data"):
        code = urllib.request.urlopen(NRED + path, timeout=15).status
        print(f"验证 GET {path} -> {code}")


if __name__ == "__main__":
    main()
