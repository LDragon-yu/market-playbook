// The channel is separate from browser notifications: one Feishu bot for all devices.
const channelBaseSystem=renderSystem;
renderSystem=function(){
  channelBaseSystem();
  const card=document.createElement('article');
  card.className='card';card.id='channel-status';card.style.marginTop='16px';
  card.innerHTML='<h3>飞书双向通道</h3><p class="muted">正在读取状态…</p>';
  $('#content').append(card);
  refreshChannelStatus();
};

const channelBaseIntel=renderIntel;
renderIntel=function(){
  channelBaseIntel();
  const card=document.createElement('article');
  card.className='card';card.id='channel-materials';card.style.marginTop='16px';
  card.innerHTML='<h3>飞书素材收件箱</h3><p class="muted">正在读取…</p>';
  $('#content').append(card);
  refreshChannelStatus();
};

const channelStateNames={connected:'长连接已建立',connecting:'连接中',starting:'启动中',missing_sdk:'未安装飞书 SDK',connection_failed:'连接失败',start_failed:'启动失败',processing_error:'处理异常'};
const materialStateNames={received:'已收录，处理中',pending_transcript:'待补充字幕',pending_text:'待补充正文',analyzed:'已解析入库',analysis_failed:'分析失败'};
function channelTime(value){return value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'—'}

async function refreshChannelStatus(){
  if(!['system','intel'].includes(state.view)||document.hidden)return;
  try{
    const c=await api('/api/channel/status');
    const card=$('#channel-status');
    if(card){
      const status=!c.enabled?'未启用':!c.configured?'待配置':!c.process_alive?'进程未运行':!c.heartbeat_fresh?'等待心跳':channelStateNames[c.worker?.state]||'等待连接';
      card.innerHTML=`<div class="panel-head"><h3>飞书双向通道</h3><span class="tag ${c.worker?.state==='connected'&&c.heartbeat_fresh?'strong':''}">${esc(status)}</span></div>
        <p>${esc(c.worker?.detail||'在 API 配置面板中启用，私聊机器人发送 /身份，然后填写用户白名单及通知会话。')}</p>
        <p class="muted">授权用户 ${c.authorized_users} 位 · 通知目标 ${c.notification_destination_configured?'已设置':'未设置'} · 已发送 ${c.outbox_counts.sent||0} · 待发送 ${c.outbox_counts.queued||0} · 失败 ${c.outbox_counts.failed||0} · 过期 ${c.outbox_counts.expired||0}</p>
        <p class="muted">中断请求 ${c.interrupted_requests} 条：为避免重复修改持仓，中断请求不自动重放，请重新提交。</p>
        <button type="button" class="ghost" id="channel-test">发送测试通知</button>
        <p class="muted">收件指令：/帮助、/持仓、/确认、/问、/文案、/材料、/字幕。只能研究辅助，不自动交易。</p>
        ${(c.messages||[]).filter(x=>x.error).map(x=>`<p class="muted">${channelTime(x.created)} · ${esc(x.status)} · ${esc(x.error)}</p>`).join('')}`;
      $('#channel-test').onclick=async function(){
        this.disabled=true;
        try{const r=await api('/api/channel/test',{method:'POST'});notify(r.status==='queued'?'测试通知已入队，请查看飞书及发送状态':r.reason||'通知未启用');await refreshChannelStatus()}
        catch(e){notify(e.message,true)}finally{this.disabled=false}
      };
    }
    const inbox=$('#channel-materials');
    if(inbox)inbox.innerHTML=`<div class="panel-head"><h3>飞书素材收件箱</h3><small>链接收录不等于已取得内容</small></div>${(c.materials||[]).map(m=>`<div class="intel-item"><div class="intel-meta"><b>${esc(m.id)}</b><span class="tag ${m.status==='analyzed'?'strong':''}">${esc(materialStateNames[m.status]||m.status)}</span></div><p style="overflow-wrap:anywhere">${esc(m.url)}</p><p>${esc(m.detail)}</p><small>${channelTime(m.created)}${m.intelligence_id?` · 情报 #${m.intelligence_id}`:''}</small>${m.status!=='analyzed'?`<p class="muted">在原飞书会话发送：/字幕 ${esc(m.id)} 正文或转写内容</p>`:''}</div>`).join('')||'<p class="muted">在飞书发送公开文章或视频链接。正文取得成功才分析；视频目前需你补充字幕或转写。</p>'}`;
  }catch(e){const card=$('#channel-status')||$('#channel-materials');if(card)card.innerHTML=`<h3>飞书通道</h3><p>${esc(e.message)}</p>`}
}

setInterval(refreshChannelStatus,20000);
if(state.data&&state.view==='system')renderSystem();
if(state.data&&state.view==='intel')renderIntel();
