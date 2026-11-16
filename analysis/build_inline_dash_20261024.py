# -*- coding: utf-8 -*-
"""会话内联可视化仪表盘生成器 (2026-10-24): v4 报告数据 → visualize 契约 HTML fragment
数据源: reports/backtest_fullhist_2016_2026_20261020 (只读) | 输出: 会话可视化目录 fragment
图表为原生 SVG/D3(主题变量着色), 非 PNG 内嵌; 数据降采样(净值周频)+直方分箱在生成侧完成。
"""
import os, sys, json, sqlite3
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261020"
VIZ = r"D:\zcodesj\.zcode\v2\visualizations\908221d88bd571f800a1bdfcd9243485e0cc5bd2117af4b7458f25b3cca7f781"
os.makedirs(VIZ, exist_ok=True)
FLAG = "U100BP_r30a3_cap30"

s8 = json.load(open(os.path.join(BASE, "summary_round8.json"), encoding="utf-8"))
fm = s8[FLAG]["metrics"]
eq = pd.read_csv(os.path.join(BASE, f"equity_{FLAG}.csv"))
tr = pd.read_csv(os.path.join(BASE, f"trades_{FLAG}.csv"), dtype={"code": str})

def isod(i):
    s = str(int(i)); return f"{s[:4]}-{s[4:6]}-{s[6:]}"

# --- 净值三系列: 旗舰/M6/SSE 统一周频网格(共享x数组, 供跨系列tooltip) ---
eqM6 = pd.read_csv(os.path.join(BASE, "equity_M6_wfa_top20.csv"))
ci = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro", uri=True)
idx = pd.read_sql_query("SELECT date,close FROM index_daily WHERE index_code='000001' AND date>=20160101 AND date<=20261020", ci)
ci.close(); idx["date"] = idx["date"].astype(int)
sse = idx.drop_duplicates("date").set_index("date")["close"]

grid = eq["date"].iloc[::5].tolist()
if grid[-1] != eq["date"].iloc[-1]: grid.append(int(eq["date"].iloc[-1]))
grid = np.array(grid)
f_nav = np.interp(grid, eq["date"], eq["equity"]) / 1e5
m6_full = pd.Series(eqM6["equity"].to_numpy() / 1e5, index=eqM6["date"])
m6_nav = m6_full.reindex(m6_full.index.union(grid)).ffill().reindex(grid).to_numpy()
sse_n_full = sse / sse.iloc[0]
sse_nav = sse_n_full.reindex(sse_n_full.index.union(grid)).ffill().reindex(grid).to_numpy()
xs = [isod(d) for d in grid]

# --- 逐年(卖出年)统计 + 链式年度收益 ---
eqy = eq.copy(); eqy["y"] = eqy["date"] // 10000
yr_ret, prev = {}, None
for y, s_ in eqy.groupby("y")["equity"]:
    e0 = prev if prev is not None else 100000.0
    yr_ret[int(y)] = float(s_.iloc[-1] / e0 - 1); prev = float(s_.iloc[-1])
tr["sy"] = tr["sell_date"] // 10000
years = sorted(set(tr["sy"]) | set(yr_ret))
year_rows = []
for y in years:
    sub = tr[tr["sy"] == y]
    nw = int((sub["pnl_pct"] > 0).sum()) if len(sub) else 0
    year_rows.append(dict(year=int(y), n=int(len(sub)), wins=nw, losses=int(len(sub) - nw),
                          wr=round(nw / len(sub) * 100, 1) if len(sub) else None,
                          pnl=round(float(sub["pnl"].sum()), 0) if len(sub) else 0.0,
                          ret=round(yr_ret.get(int(y), 0.0) * 100, 1)))

# --- 直方分箱(40) ---
hist, edges = np.histogram(tr["pnl_pct"] * 100, bins=40)
bins = [dict(x0=round(float(edges[i]), 1), x1=round(float(edges[i + 1]), 1), n=int(hist[i])) for i in range(40)]

# --- 散点(689) ---
sc = [[int(h), round(float(p) * 100, 1), int(w > 0), str(c), str(nm)]
      for h, p, w, c, nm in zip(tr["hold_days"], tr["pnl_pct"], tr["pnl_pct"] > 0, tr["code"], tr["name"])]

payload = dict(
    xs=xs, navF=[round(float(v), 4) for v in f_nav], navM6=[round(float(v), 4) for v in m6_nav],
    navSSE=[round(float(v), 4) for v in sse_nav],
    years=[r["year"] for r in year_rows], yret=[r["ret"] for r in year_rows],
    year_rows=year_rows, bins=bins, scatter=sc,
    wr_y=[[r["year"], r["wr"]] for r in year_rows if r["wr"] is not None],
    kpi=dict(final=round(fm["final_equity"]), tr=round(fm["total_return"] * 100, 1),
             cagr=round(fm["cagr"] * 100, 2), mdd=round(fm["mdd"] * 100, 1),
             wr=round(fm["win_rate"] * 100, 1), n=int(fm["n_trades"]),
             pf=round(fm["profit_factor"], 2), hold=round(fm["avg_hold"], 1),
             pk=int(fm["mdd_peak_date"]), tg=int(fm["mdd_trough_date"])))

HTML = """<div id="tulongV4Dash">
<h3>屠龙旗舰统一账户 · 全域回测总览（2016-01-01 ~ 2026-10-20，初始 100,000 元）</h3>
<div class="viz-grid">
  <div class="card viz-stat"><div class="text-small text-muted">期末净值 / 总收益</div><div class="viz-stat-value tabular-nums">¥401,640 · +301.6%</div></div>
  <div class="card viz-stat"><div class="text-small text-muted">年化复合 / 盈亏比</div><div class="viz-stat-value tabular-nums">+13.76% · PF 3.59</div></div>
  <div class="card viz-stat"><div class="text-small text-muted">最大回撤</div><div class="viz-stat-value tabular-nums">-16.2%</div><div class="text-small text-muted">2018-01-24 → 2019-01-03</div></div>
  <div class="card viz-stat"><div class="text-small text-muted">全周期胜率 / 笔数</div><div class="viz-stat-value tabular-nums">64.3% · 689 笔</div><div class="text-small text-muted">平均持仓 19.8 交易日</div></div>
</div>
<table class="table table-sm">
  <thead><tr><th>年度</th><th>笔数</th><th>盈/亏</th><th>胜率</th><th class="text-end">年度盈亏(元)</th><th class="text-end">年度收益</th></tr></thead>
  <tbody id="tb-years"></tbody>
</table>
<div id="ch-nav"><h3>账户净值（对数）</h3><div class="viz-row" id="lg-nav" role="group" aria-label="净值系列"></div></div>
<div id="ch-yret"><h3>年度收益</h3></div>
<div id="ch-wr"><h3>胜率时序（按卖出年）</h3></div>
<div id="ch-dd"><h3>回撤曲线</h3></div>
<div id="ch-hist"><h3>单笔盈亏分布</h3></div>
<div id="ch-sc"><h3>盈亏散点（持仓时长 × 单笔盈亏）</h3></div>
<div class="tooltip" role="tooltip" id="tip"></div>
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<script>
(function(){
const P = __PAYLOAD__;
const root = document.getElementById('tulongV4Dash');
const tip = root.querySelector('#tip');
const css = v => getComputedStyle(root).getPropertyValue(v).trim();
const M = {l:56,r:14,t:10,b:30};
const iso = t => new Date(t);
const fmtD = d3.timeFormat('%Y-%m');

// ---- yearly table ----
const tb = root.querySelector('#tb-years');
tb.innerHTML = P.year_rows.map(r => '<tr><td class="tabular-nums">'+r.year+'</td><td class="tabular-nums">'+r.n+
  '</td><td class="tabular-nums">'+r.wins+' / '+r.losses+'</td><td class="tabular-nums">'+(r.wr==null?'—':r.wr.toFixed(1)+'%')+
  '</td><td class="text-end tabular-nums">'+(r.pnl>=0?'+':'')+Math.round(r.pnl).toLocaleString()+
  '</td><td class="text-end tabular-nums" style="color:'+(r.ret>=0?'var(--green)':'var(--red)')+'">'+(r.ret>=0?'+':'')+r.ret.toFixed(1)+'%</td></tr>').join('');

// ---- chart scaffolding ----
function frame(g, w, h){ g.append('rect').attr('data-chart-frame',1).attr('x',M.l).attr('y',M.t)
  .attr('width',w-M.l-M.r).attr('height',h-M.t-M.b).attr('fill','none').attr('stroke','var(--border)'); }
function axisTitles(g, w, h, xt, yt){
  g.append('text').attr('class','axis-title').attr('data-axis','x').attr('x',M.l+(w-M.l-M.r)/2).attr('y',h-2)
    .attr('text-anchor','middle').attr('fill','var(--foreground)').attr('font-size','12px').text(xt);
  g.append('text').attr('class','axis-title').attr('data-axis','y').attr('x',12).attr('y',M.t+(h-M.t-M.b)/2)
    .attr('text-anchor','middle').attr('transform','rotate(-90 12 '+(M.t+(h-M.t-M.b)/2)+')')
    .attr('fill','var(--foreground)').attr('font-size','12px').text(yt); }
const REDRAW = {};
function chart(id, h, draw){
  const host = root.querySelector('#'+id);
  let svg = host.querySelector('svg');
  const run = () => {
    const w = Math.max(host.clientWidth, 320);
    if(svg) svg.remove();
    svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    host.appendChild(svg);
    svg.setAttribute('viewBox','0 0 '+w+' '+h);
    svg.setAttribute('width',w); svg.setAttribute('height',h);
    draw(d3.select(svg), w, h);
  };
  REDRAW[id] = run;
  new ResizeObserver(run).observe(host); run();
}

// ---- 1. nav (log) : 3 series + legend toggles + cross-series tooltip ----
const vis = {F:true, M6:true, SSE:true};
const lg = root.querySelector('#lg-nav');
[['F','旗舰 U100BP','--viz-series-1'],['M6','M6 WFA Top20','--viz-series-2'],['SSE','上证(归一)','--viz-series-3']].forEach(([k,lab,cv])=>{
  const b = document.createElement('button'); b.type='button'; b.className='nav-link'; b.setAttribute('aria-pressed','true');
  b.style.background='transparent'; b.style.border='none'; b.style.padding='2px 8px'; b.style.cursor='pointer';
  b.innerHTML = '<span style="display:inline-block;width:10px;height:10px;margin-right:5px;background:var('+cv+');border-radius:2px"></span>'+lab;
  b.onclick = () => { vis[k] = !vis[k]; b.setAttribute('aria-pressed', String(vis[k]));
    b.style.opacity = vis[k] ? 1 : .35; REDRAW['ch-nav'] && REDRAW['ch-nav'](); };
  lg.appendChild(b);
});
chart('ch-nav', 320, (g, w, h) => {
  const S = {F:[P.xs,P.navF,'--viz-series-1',1.8], M6:[P.xs,P.navM6,'--viz-series-2',1.2], SSE:[P.xs,P.navSSE,'--viz-series-3',1.1]};
  const x = d3.scaleTime().domain(d3.extent(P.xs, iso)).range([M.l, w-M.r]);
  const ys = d3.scaleLog().domain([0.3, d3.max([...P.navF,...P.navM6,...P.navSSE])*1.05]).range([h-M.b, M.t]);
  frame(g,w,h);
  g.append('g').attr('transform','translate(0,'+(h-M.b)+')').call(d3.axisBottom(x).ticks(Math.min(8, Math.floor(w/90))).tickFormat(fmtD))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  g.append('g').attr('transform','translate('+M.l+',0)').call(d3.axisLeft(ys).ticks(5).tickFormat(d3.format('.1f')))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  axisTitles(g,w,h,'日期','净值(起点1.0, 对数)');
  for(const k of ['F','M6','SSE']){ if(!vis[k]) continue; const [xs,vals,cv,lw] = S[k];
    const pts = d3.zip(xs.map(iso), vals);
    g.append('path').datum(pts).attr('fill','none').attr('stroke','var('+cv+')')
      .attr('stroke-width',lw).attr('stroke-dasharray', k==='SSE'?'4 3':null)
      .attr('d', d3.line().x(d=>x(d[0])).y(d=>ys(Math.max(d[1],0.3)))); }
  // cross-series overlay
  const hit = g.append('rect').attr('data-chart-hit',1).attr('data-chart-hover-overlay','cross-series')
    .attr('x',M.l).attr('y',M.t).attr('width',w-M.l-M.r).attr('height',h-M.t-M.b).attr('fill','transparent');
  const guide = g.append('line').attr('data-chart-hover-guide',1).attr('stroke','var(--border)').style('display','none');
  const marks = {}; ['F','M6','SSE'].forEach(k=>{ marks[k]=g.append('circle').attr('data-chart-hover-marker',1)
    .attr('r',3.5).attr('fill','var('+S[k][2]+')').style('display','none'); });
  const move = ev => {
    const [mx] = d3.pointer(ev, g.node());
    if(mx < M.l || mx > w-M.r){ leave(); return; }
    const t = x.invert(mx), i = d3.bisector(d => d[0]).center(P.xs.map(iso), t);
    const rows = [];
    for(const k of ['F','M6','SSE']){ if(!vis[k]) continue;
      const v = S[k][1][i]; marks[k].attr('cx',x(iso(P.xs[i]))).attr('cy',ys(Math.max(v,0.3))).style('display',null);
      rows.push('<div>'+({F:'旗舰',M6:'M6',SSE:'上证'})[k]+' <b class="tabular-nums">'+v.toFixed(3)+'</b></div>'); }
    guide.attr('x1',x(iso(P.xs[i]))).attr('x2',x(iso(P.xs[i]))).attr('y1',M.t).attr('y2',h-M.b).style('display',null);
    tip.innerHTML = '<div class="text-small">'+P.xs[i]+'</div>'+rows.join('');
    tip.style.display='block';
  };
  const leave = () => { guide.style('display','none'); Object.values(marks).forEach(m=>m.style('display','none')); tip.style.display='none'; };
  hit.on('pointermove', move).on('pointerleave', leave).on('pointerdown', move);
});

// ---- 2. yearly return bars (direct labels) ----
chart('ch-yret', 240, (g, w, h) => {
  const x = d3.scaleBand().domain(P.years.map(String)).range([M.l, w-M.r]).padding(0.3);
  const y = d3.scaleLinear().domain([Math.min(-5, d3.min(P.yret)*1.15), Math.max(5, d3.max(P.yret)*1.1)]).range([h-M.b, M.t]);
  frame(g,w,h);
  g.append('g').attr('transform','translate(0,'+y(0)+')').call(d3.axisBottom(x).tickSize(0))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  g.append('g').attr('transform','translate('+M.l+',0)').call(d3.axisLeft(y).ticks(5).tickFormat(d=>d+'%'))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  axisTitles(g,w,h,'年度','收益率');
  const bw = x.bandwidth();
  g.selectAll('rect.bar').data(P.yret).join('rect').attr('class','bar')
    .attr('x',(d,i)=>x(String(P.years[i]))).attr('width',bw)
    .attr('y',d=>y(Math.max(d,0))).attr('height',d=>Math.abs(y(d)-y(0)))
    .attr('rx',2).attr('fill',d=>'var('+(d>=0?'--green':'--red')+')').attr('fill-opacity',0.8)
    .attr('data-tooltip',(d,i)=>P.years[i]+' 年度收益 '+d.toFixed(1)+'%');
  g.selectAll('text.bl').data(P.yret).join('text').attr('class','bl')
    .attr('x',(d,i)=>x(String(P.years[i]))+bw/2).attr('text-anchor','middle').attr('font-size','11px')
    .attr('y',d=>y(Math.max(d,0))-3).attr('fill','var(--foreground)').text(d=>(d>=0?'+':'')+d.toFixed(0));
});

// ---- 3. winrate by year + refs ----
chart('ch-wr', 240, (g, w, h) => {
  const dat = P.wr_y.filter(r=>r[1]!=null);
  const x = d3.scaleLinear().domain(d3.extent(dat, d=>d[0])).range([M.l, w-M.r]);
  const y = d3.scaleLinear().domain([0, 100]).range([h-M.b, M.t]);
  frame(g,w,h);
  g.append('g').attr('transform','translate(0,'+(h-M.b)+')').call(d3.axisBottom(x).tickFormat(d3.format('d')).ticks(11))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  g.append('g').attr('transform','translate('+M.l+',0)').call(d3.axisLeft(y).ticks(5).tickFormat(d=>d+'%'))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  axisTitles(g,w,h,'卖出年度','胜率');
  [[64.3,'全周期 64.3%','var(--muted-foreground)'],[78,'任务标尺 78%','var(--red)']].forEach(([v,lab,c])=>{
    g.append('line').attr('x1',M.l).attr('x2',w-M.r).attr('y1',y(v)).attr('y2',y(v))
      .attr('stroke',c).attr('stroke-dasharray','4 3');
    g.append('text').attr('x',w-M.r-4).attr('y',y(v)-4).attr('text-anchor','end')
      .attr('font-size','11px').attr('fill',c).text(lab); });
  g.append('path').datum(dat).attr('fill','none').attr('stroke','var(--viz-series-1)').attr('stroke-width',1.6)
    .attr('d', d3.line().x(d=>x(d[0])).y(d=>y(d[1])));
  g.selectAll('circle.w').data(dat).join('circle').attr('class','w').attr('r',3)
    .attr('cx',d=>x(d[0])).attr('cy',d=>y(d[1])).attr('fill','var(--viz-series-1)')
    .attr('data-tooltip',d=>d[0]+' 胜率 '+d[1].toFixed(1)+'%');
});

// ---- 4. drawdown area ----
chart('ch-dd', 240, (g, w, h) => {
  const e = P.navF, peak = []; let pk = -1e9; const dd = [];
  e.forEach(v=>{ pk = Math.max(pk, v); peak.push(pk); });
  e.forEach((v,i)=>dd.push((v/peak[i]-1)*100));
  const x = d3.scaleTime().domain(d3.extent(P.xs, iso)).range([M.l, w-M.r]);
  const y = d3.scaleLinear().domain([d3.min(dd)*1.1, 0]).range([h-M.b, M.t]);
  frame(g,w,h);
  g.append('g').attr('transform','translate(0,'+(h-M.b)+')').call(d3.axisBottom(x).ticks(8).tickFormat(fmtD))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  g.append('g').attr('transform','translate('+M.l+',0)').call(d3.axisLeft(y).ticks(5).tickFormat(d=>d+'%'))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  axisTitles(g,w,h,'日期','回撤');
  const pts = d3.zip(P.xs.map(iso), dd);
  const ar = d3.area().x(d=>x(d[0])).y0(y(0)).y1(d=>y(d[1]));
  g.append('path').datum(pts).attr('fill','var(--red)').attr('fill-opacity',0.18).attr('d',ar);
  g.append('path').datum(pts).attr('fill','none').attr('stroke','var(--red)').attr('stroke-width',1).attr('d',d3.line().x(d=>x(d[0])).y(d=>y(d[1])));
  const im = dd.indexOf(d3.min(dd));
  g.append('text').attr('x',x(iso(P.xs[im]))).attr('y',y(dd[im])-6).attr('text-anchor','middle')
    .attr('font-size','11px').attr('fill','var(--foreground)').text(dd[im].toFixed(1)+'%');
});

// ---- 5. pnl histogram ----
chart('ch-hist', 240, (g, w, h) => {
  const x = d3.scaleLinear().domain(d3.extent(P.bins, b=>b.x0)).range([M.l, w-M.r]);
  const y = d3.scaleLinear().domain([0, d3.max(P.bins,b=>b.n)*1.05]).range([h-M.b, M.t]);
  frame(g,w,h);
  g.append('g').attr('transform','translate(0,'+(h-M.b)+')').call(d3.axisBottom(x).ticks(8).tickFormat(d=>d+'%'))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  g.append('g').attr('transform','translate('+M.l+',0)').call(d3.axisLeft(y).ticks(4))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  axisTitles(g,w,h,'单笔盈亏','笔数');
  const bw = Math.max(1, (w-M.l-M.r)/P.bins.length-1);
  g.selectAll('rect.hb').data(P.bins).join('rect').attr('class','hb')
    .attr('x',b=>x(b.x0)).attr('width',bw).attr('y',b=>y(b.n)).attr('height',b=>y(0)-y(b.n))
    .attr('fill',b=>'var('+(b.x0>=0?'--green':'--red')+')').attr('fill-opacity',0.65)
    .attr('data-tooltip',b=>b.x0+'%~'+b.x1+'%：'+b.n+' 笔');
});

// ---- 6. scatter (nearest-point) ----
chart('ch-sc', 280, (g, w, h) => {
  const x = d3.scaleLinear().domain([0, d3.max(P.scatter,d=>d[0])*1.02]).range([M.l, w-M.r]);
  const y = d3.scaleLinear().domain([d3.min(P.scatter,d=>d[1])*1.05, d3.max(P.scatter,d=>d[1])*1.05]).range([h-M.b, M.t]);
  frame(g,w,h);
  g.append('g').attr('transform','translate(0,'+(h-M.b)+')').call(d3.axisBottom(x).ticks(8))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  g.append('g').attr('transform','translate('+M.l+',0)').call(d3.axisLeft(y).ticks(6).tickFormat(d=>d+'%'))
    .call(ax=>ax.selectAll('text').attr('fill','var(--muted-foreground)').attr('font-size','11px'))
    .call(ax=>ax.selectAll('line,path').attr('stroke','var(--border)'));
  axisTitles(g,w,h,'持仓交易日','单笔盈亏');
  g.append('line').attr('x1',M.l).attr('x2',w-M.r).attr('y1',y(0)).attr('y2',y(0)).attr('stroke','var(--border)');
  const cells = g.selectAll('circle.s').data(P.scatter).join('circle').attr('class','s')
    .attr('cx',d=>x(d[0])).attr('cy',d=>y(d[1])).attr('r',2.6)
    .attr('fill',d=>'var('+(d[2]?'--green':'--red')+')').attr('fill-opacity',0.5);
  const hit = g.append('rect').attr('data-chart-hit',1).attr('x',M.l).attr('y',M.t)
    .attr('width',w-M.l-M.r).attr('height',h-M.t-M.b).attr('fill','transparent');
  hit.on('pointermove', ev => {
    const [mx,my] = d3.pointer(ev, g.node());
    let bi = -1, bd = 1e9;
    P.scatter.forEach((d,i)=>{ const dx = x(d[0])-mx, dy = y(d[1])-my, dd2 = dx*dx+dy*dy;
      if(dd2 < bd){ bd = dd2; bi = i; } });
    if(bi < 0) return; const d = P.scatter[bi];
    tip.innerHTML = '<div class="text-small">'+d[3]+' '+d[4]+'</div><div class="tabular-nums">'+d[1].toFixed(2)+'% · 持仓 '+d[0]+' 日</div>';
    tip.style.display = 'block';
  }).on('pointerleave', () => tip.style.display='none');
});
})();
</script>
</div>
"""

out = os.path.join(VIZ, "tulong-backtest-v4-dashboard.html")
frag = HTML.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write(frag)
size = os.path.getsize(out)
print("FRAGMENT:", out, size, "bytes", "| OK" if size < 1_000_000 else "| OVER 1MB!")
print("points: xs=%d scatter=%d bins=%d years=%d" % (len(xs), len(sc), len(bins), len(years)))
