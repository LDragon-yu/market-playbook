let lastQaAnswer=null;
let lastSeenAlertId=null;
const originalRender=render;
render=function(){
  if(state.view==='portfolio')return renderPortfolio();
  if(state.view==='qa')return renderStockQA();
  return originalRender();
};

function renderPortfolio(){
  const p=state.data.portfolio||{},w=p.worker||{},positions=p.positions||[],monitors=p.monitors||[],alerts=p.alerts||[],decisions=p.decisions||[];
  $('#content').innerHTML=`<div class="live-strip"><span class="pulse"></span><div><b>单股实时哨兵</b><small>${!w.enabled?'自动轮询未启用，可手动检查':w.waiting_reason||(`每 ${w.interval_seconds} 秒轮询；最近运行 ${w.last_run||'尚未运行'}`)}</small></div><div class="live-actions"><button class="ghost" id="enable-notify">浏览器提醒</button><button class="primary" id="cycle-now">立即检查一轮</button></div></div>
  <div class="grid kpi-grid">${kpi('当前持仓',`${positions.length} 只`,'持仓优先进入监控')}${kpi('监控标的',`${new Set(monitors.map(x=>x.ts_code)).size} 只`,`${monitors.length} 个角色关系`)}${kpi('未确认预警',`${alerts.filter(x=>!x.acknowledged).length} 条`,'涨停、炸板、回撤与T+1')}${kpi('模型状态',p.llm_configured?'已启用':'规则模式',p.llm_configured?'事件触发大模型':'配置 API 后启用语义决策')}</div>
  <div class="grid portfolio-layout"><article class="card"><div class="panel-head"><h3>录入 / 更新持仓</h3><span class="tag">本地保存</span></div><form id="position-form"><div class="position-form-grid"><label>股票代码<input name="ts_code" required placeholder="000592.SZ"></label><label>策略模式<select name="strategy_mode"><option value="comprehensive">综合模式</option><option value="relay">连板接力</option><option value="trend">容量趋势</option></select></label><label>总股数<input name="total_qty" type="number" min="0" value="0"></label><label>可卖股数<input name="sellable_qty" type="number" min="0" value="0"></label><label>成本价<input name="cost_price" type="number" min="0" step="0.001" value="0"></label><label>买入日期<input name="buy_date" type="date"></label><label class="full">买入逻辑<textarea name="thesis" style="min-height:70px" placeholder="为什么买入、预期是什么"></textarea></label><label class="full">失效条件<input name="invalidation" placeholder="出现什么情况说明逻辑失效"></label></div><button class="primary wide">保存持仓</button></form></article>
  <article class="card"><div class="panel-head"><h3>当前持仓</h3><small>区分总数量和T+1可卖数量</small></div>${positions.map(positionRow).join('')||'<p class="muted">尚未录入持仓。录入后会自动加入最高优先级监控。</p>'}</article></div>
  <div class="grid two-col" style="margin-top:16px"><article class="card"><div class="panel-head"><h3>今日监控池</h3><small>持仓 · 龙头 · 确认 · 二选一 · 卡位</small></div><div class="monitor-grid">${monitors.map(m=>`<div class="monitor-item"><span class="priority-mark">P${m.priority}</span><b>${esc(m.name)}</b><small>${esc(m.ts_code)} · ${esc(m.role)}</small><small>${esc(m.sector||'待确认板块')}${m.peer_code?` ↔ ${esc(m.peer_code)}`:''}</small></div>`).join('')||'<p class="muted">运行盘后复盘或录入持仓后生成。</p>'}</div></article><article class="card"><div class="panel-head"><h3>最近预警</h3><small>${alerts.length} 条</small></div>${alerts.slice(0,12).map(a=>`<div class="alert-item"><span class="severity ${a.severity}">${esc(a.severity)}</span><div><b>${esc(a.title)}</b><small>${esc(a.created_at)}</small></div><small>${esc(a.ts_code)}</small></div>`).join('')||'<p class="muted">暂无实时风险事件。</p>'}</article></div>
  <article class="card" style="margin-top:16px"><div class="panel-head"><h3>实时决策记录</h3><span class="tag ${p.llm_configured?'strong':'llm-off'}">${p.llm_configured?'大模型 + 规则':'仅规则'}</span></div>${decisions.slice(0,12).map(d=>{const x=d.payload||{};return `<div class="decision-card"><h4>${esc(x.action)} · ${esc(x.ts_code)}</h4><p>${esc(x.summary||'')}<br>置信度 ${Math.round(Number(x.confidence||0)*100)}% · 有效至 ${esc(x.expires_at||'—')}</p></div>`}).join('')||'<p class="muted">重要事件发生后生成，建议过期后必须重新分析。</p>'}</article>`;
  $('#position-form').onsubmit=savePositionForm;$('#cycle-now').onclick=runMonitorCycle;
  const notifyButton=$('#enable-notify');
  if(notifyButton){notifyButton.onclick=enableBrowserNotifications;notifyButton.style.display=('Notification' in window)?'':'none'}
  const newest=Math.max(0,...alerts.map(x=>Number(x.id)||0));
  if(lastSeenAlertId===null)lastSeenAlertId=newest;
  document.querySelectorAll('[data-close-position]').forEach(b=>b.onclick=()=>closePosition(Number(b.dataset.closePosition)));
}
function positionRow(x){return `<div class="position-row"><div class="position-name"><b>${esc(x.name)}</b><small>${esc(x.ts_code)} · ${esc(x.strategy_mode)}</small></div><div><span class="mini-label">成本</span>${Number(x.cost_price).toFixed(3)}</div><div><span class="mini-label">总数</span>${x.total_qty}</div><div><span class="mini-label">可卖</span>${x.sellable_qty}</div><div><span class="mini-label">买入日</span>${esc(x.buy_date||'—')}</div><button class="danger-button" data-close-position="${x.id}">清仓</button></div>`}
async function savePositionForm(e){e.preventDefault();const fd=new FormData(e.target),value=Object.fromEntries(fd);['total_qty','sellable_qty','cost_price'].forEach(k=>value[k]=Number(value[k]||0));try{await api('/api/positions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});notify('持仓已保存并加入监控池');await load()}catch(x){notify(x.message,true)}}
async function closePosition(id){if(!confirm('确认将该持仓标记为已清仓？'))return;try{await api(`/api/positions/${id}`,{method:'DELETE'});notify('持仓已关闭');await load()}catch(x){notify(x.message,true)}}
async function runMonitorCycle(){const b=$('#cycle-now');b.disabled=true;b.textContent='逐只抓取中…';try{const r=await api('/api/monitor/cycle',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const stale=(r.features||[]).filter(x=>x.stale).length;notify(`完成 ${r.symbols} 只，新增 ${r.new_alerts.length} 条预警，耗时 ${r.elapsed_seconds} 秒${stale?`；${stale} 只为过期快照，未触发建议`:''}${r.errors.length?'；部分失败':''}`);await load()}catch(x){notify(x.message,true)}finally{if(b){b.disabled=false;b.textContent='立即检查一轮'}}}

async function enableBrowserNotifications(){
 if(!('Notification' in window))return notify('当前浏览器不支持系统通知',true);
 const result=await Notification.requestPermission();notify(result==='granted'?'浏览器提醒已启用':'未获得通知权限',result!=='granted');
}
async function pollMonitorDashboard(){
 if(state.view!=='portfolio'||document.activeElement?.closest('#position-form'))return;
 try{
  const next=await api('/api/dashboard'),alerts=next.portfolio?.alerts||[];
  const unseen=lastSeenAlertId===null?[]:alerts.filter(x=>Number(x.id)>lastSeenAlertId);
  if(alerts.length)lastSeenAlertId=Math.max(lastSeenAlertId||0,...alerts.map(x=>Number(x.id)||0));
  state.data=next;renderPortfolio();
  if(unseen.length){
   notify(`收到 ${unseen.length} 条新风险预警`,unseen.some(x=>x.severity==='danger'));
   if('Notification' in window&&Notification.permission==='granted'){
    const item=unseen[0];new Notification(`见势：${item.title}`,{body:`${item.ts_code} · ${item.severity}`});
   }
  }
 }catch(_error){}
}
setInterval(pollMonitorDashboard,15000);
function renderStockQA(){
 const p=state.data.portfolio||{},history=p.questions||[];
 $('#content').innerHTML=`<div class="grid qa-layout"><article class="card"><div class="panel-head"><h3>个股研究助手</h3><span class="tag ${p.llm_configured?'strong':'llm-off'}">${p.llm_configured?'大模型已连接':'大模型待配置'}</span></div><form id="qa-form"><label>股票代码<input name="ts_code" required placeholder="000592.SZ"></label><label>问题<textarea name="question" required placeholder="例如：它今天为什么弱于福建水泥？如果我有持仓，明天什么条件下应该降低预期？"></textarea></label><div class="quick-questions"><button type="button" class="quick-question">今天为什么强或弱？</button><button type="button" class="quick-question">它在板块中的角色是什么？</button><button type="button" class="quick-question">当前持仓风险如何？</button><button type="button" class="quick-question">明天高开或低开怎么处理？</button></div><button class="primary wide">刷新行情并分析</button></form><p class="muted">回答会引用最新单股快照、持仓、题材、盘前预案及情报；不会自动下单。</p></article><article class="card qa-answer"><div class="panel-head"><h3>分析结果</h3><small>${lastQaAnswer?`数据截至 ${esc(lastQaAnswer.data_as_of)}`:'等待提问'}</small></div>${lastQaAnswer?renderQaAnswer(lastQaAnswer):'<p class="muted">选择股票并提问。未配置模型时仍会返回结构化行情事实。</p>'}</article></div><article class="card qa-history"><div class="panel-head"><h3>历史问答</h3><small>用于盘后验证，不事后改写</small></div>${history.map(h=>`<div class="qa-history-item"><b>${esc(h.ts_code)} · ${esc(h.question)}</b><p>${esc(h.answer)}</p><small>${esc(h.asked_at)}</small></div>`).join('')||'<p class="muted">暂无历史问答。</p>'}</article>`;
 $('#qa-form').onsubmit=askStock;document.querySelectorAll('.quick-question').forEach(b=>b.onclick=()=>{$('#qa-form textarea').value=b.textContent});
}
function renderQaAnswer(a){return `<div class="answer-main">${esc(a.answer||a.conclusion||'')}</div><h4>关键事实</h4><ul class="evidence-list">${(a.facts||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h4>风险与未知</h4><ul class="evidence-list">${[...(a.risks||[]),...(a.unknowns||[])].map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p class="muted">模式：${esc(a.analysis_mode)} · 本回答不自动触发交易</p>`}
async function askStock(e){e.preventDefault();const b=e.submitter;b.disabled=true;b.textContent='刷新行情并分析…';const value=Object.fromEntries(new FormData(e.target));try{lastQaAnswer=await api('/api/stocks/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});await load();state.view='qa';renderStockQA()}catch(x){notify(x.message,true)}finally{b.disabled=false;b.textContent='刷新行情并分析'}}

document.querySelectorAll('.nav').forEach(n=>n.onclick=()=>{document.querySelectorAll('.nav').forEach(x=>x.classList.remove('active'));n.classList.add('active');state.view=n.dataset.view;$('#page-title').textContent={review:'盘后复盘',plan:'盘前预案',portfolio:'持仓与监控',qa:'个股问答',intel:'情报中心',system:'数据与任务'}[state.view];const main=$('#run-main');main.textContent=state.view==='plan'?'生成盘前预案':state.view==='portfolio'?'运行实时检查':state.view==='qa'?'刷新数据':'生成盘后复盘';main.style.display=state.view==='qa'?'none':'';render()});
$('#run-main').onclick=()=>{if(state.view==='portfolio')return runMonitorCycle();openJob(state.view==='plan'?'pre-market':'post-market')};
