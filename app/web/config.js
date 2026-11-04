const baseRenderSystem=renderSystem;

renderSystem=function(){
  baseRenderSystem();
  const jobs=$('#content .jobs')?.closest('.card');
  if(!jobs)return;
  const card=document.createElement('article');
  card.className='card config-entry';
  card.innerHTML=`<div><span class="label">SERVER-SIDE SECRETS</span><h3>API 与 Token 配置</h3><p>密钥只写入服务器运行配置文件，前端永远不会读取已有明文。</p></div><button class="primary" id="open-config">打开配置面板</button>`;
  jobs.before(card);
  $('#open-config').onclick=openConfigDialog;
};

function ensureConfigDialog(){
  let dialog=$('#config-dialog');
  if(dialog)return dialog;
  dialog=document.createElement('dialog');
  dialog.id='config-dialog';
  dialog.className='config-dialog';
  document.body.appendChild(dialog);
  return dialog;
}

function secretInput(name,label,configured,hint=''){return `<div class="secret-field"><label>${label}<span class="secret-state ${configured?'configured':''}">${configured?'已配置':'未配置'}</span><input type="password" name="${name}" autocomplete="new-password" spellcheck="false" placeholder="${configured?'留空保留现有密钥':esc(hint||'输入新密钥')}"></label><label class="clear-secret" style="display:flex;align-items:center;gap:6px;margin-top:6px!important"><input type="checkbox" data-clear-secret="${name}"> 清除已保存的${label}</label></div>`}

async function openConfigDialog(){
  const dialog=ensureConfigDialog();
  dialog.innerHTML='<div class="loading">正在读取服务器配置状态…</div>';
  dialog.showModal();
  try{
    const c=await api('/api/config');
    const insecure=!c.security.auth_enabled;
    dialog.innerHTML=`<div class="config-dialog-head"><div><p class="eyebrow">RUNTIME CONFIGURATION</p><h2>API 与 Token</h2></div><button type="button" class="dialog-close" id="close-config">×</button></div>
    <div class="config-security ${insecure?'warn':''}">${insecure?'当前未启用 APP_USERNAME / APP_PASSWORD。仅适合本机使用，部署公网前必须先在服务器配置登录认证。':'已启用登录认证。仍须通过 HTTPS 使用本页面。'}</div>
    <form id="config-form">
      <section class="config-section"><div class="config-section-title"><h3>Tushare / 兼容代理</h3><span class="tag ${c.tushare.configured?'strong':''}">${c.tushare.configured?'已配置':'待配置'}</span></div><div class="config-grid">
        ${secretInput('tushare_token','Tushare Token',c.tushare.configured)}
        <label>API Base URL<input name="tushare_base_url" value="${esc(c.tushare.base_url)}" required placeholder="https://api.tushare.pro"></label>
        <label>积分档位<input name="tushare_points_tier" type="number" min="0" value="${Number(c.tushare.points_tier||0)}"></label>
      </div></section>
      <section class="config-section"><div class="config-section-title"><h3>大模型</h3><span class="tag ${c.llm.configured?'strong':''}">${c.llm.configured?'可用配置':'待配置'}</span></div><label class="switch-row"><input name="llm_enabled" type="checkbox" ${c.llm.enabled?'checked':''}> 启用大模型情报、事件解释与个股问答</label><div class="config-grid">
        ${secretInput('llm_api_key','LLM API Key',c.llm.api_key_configured)}
        <label>API Base URL<input name="llm_base_url" value="${esc(c.llm.base_url)}" required placeholder="https://api.deepseek.com"></label>
        <label>模型名<input name="llm_model" value="${esc(c.llm.model)}" required placeholder="deepseek-chat"></label>
      </div></section>
      <section class="config-section"><div class="config-section-title"><h3>飞书双向机器人</h3><span class="tag ${c.feishu.configured?'strong':''}">${c.feishu.configured?'已配置':'待配置'}</span></div>
        <p class="muted">使用飞书「企业自建应用 + 机器人 + 长连接」，不是群自定义 Webhook。首次只填 App ID / Secret，私聊发送 /身份 获取用户和会话 ID。</p>
        <label class="switch-row"><input name="feishu_enabled" type="checkbox" ${c.feishu.enabled?'checked':''}> 启用双向收发（需要安装 requirements-feishu.txt）</label><div class="config-grid">
        <label>App ID<input name="feishu_app_id" value="${esc(c.feishu.app_id)}" placeholder="cli_..."></label>
        ${secretInput('feishu_app_secret','飞书 App Secret',c.feishu.app_secret_configured)}
        <label>允许用户 open_id<input name="feishu_allowed_users" value="${esc(c.feishu.allowed_users)}" placeholder="ou_...，多个用英文逗号分隔"></label>
        <label>通知目标 chat_id<input name="feishu_notify_chat_id" value="${esc(c.feishu.notify_chat_id)}" placeholder="oc_..."></label>
        </div><p class="muted">未加入白名单的用户只能获取自己的身份，不能查询持仓。持仓变更需二次确认；不自动下单。云部署需 HTTPS 和登录认证。详见 docs/feishu-channel.md。</p>
      </section>
      <details class="config-section"><summary>外围行情、OCR 与补充数据</summary><div class="config-grid details-grid">
        ${secretInput('alpha_vantage_api_key','Alpha Vantage Key',c.alpha_vantage.configured)}
        <label>外围代码<input name="alpha_vantage_symbols" value="${esc(c.alpha_vantage.symbols)}" placeholder="SPY,QQQ,DIA"></label>
        <label>OCR 模式<select name="ocr_mode"><option value="disabled" ${c.ocr.mode==='disabled'?'selected':''}>关闭</option><option value="baidu" ${c.ocr.mode==='baidu'?'selected':''}>百度 OCR</option><option value="vision" ${c.ocr.mode==='vision'?'selected':''}>视觉大模型</option></select></label>
        ${secretInput('baidu_ocr_api_key','百度 OCR API Key',c.ocr.baidu_api_key_configured)}
        ${secretInput('baidu_ocr_secret_key','百度 OCR Secret Key',c.ocr.baidu_secret_configured)}
        <label class="switch-row"><input name="akshare_enabled" type="checkbox" ${c.akshare.enabled?'checked':''}> 启用 AKShare 补充数据</label>
      </div></details>
      <section class="config-section"><div class="config-section-title"><h3>盘中监控</h3><span class="tag">单股顺序请求</span></div><label class="switch-row"><input name="monitor_enabled" type="checkbox" ${c.monitor.enabled?'checked':''}> 启用交易时段后台轮询</label><div class="config-grid compact">
        <label>轮询间隔（秒）<input name="monitor_interval_seconds" type="number" min="20" max="3600" value="${c.monitor.interval_seconds}"></label>
        <label>最多标的<input name="monitor_max_symbols" type="number" min="1" max="20" value="${c.monitor.max_symbols}"></label>
      </div></section>
      <div class="config-actions"><button type="button" class="ghost" id="cancel-config">取消</button><button class="primary" id="save-config">保存并立即应用</button></div>
      <p class="config-footnote">空白密钥不会覆盖现有值。服务端不会在响应、日志或页面中返回密钥明文。</p>
    </form>`;
    $('#close-config').onclick=$('#cancel-config').onclick=()=>dialog.close();
    $('#config-form').onsubmit=saveRuntimeConfig;
  }catch(error){dialog.innerHTML=`<button type="button" class="dialog-close" onclick="this.closest('dialog').close()">×</button><div class="notice">${esc(error.message)}</div>`}
}

async function saveRuntimeConfig(event){
  event.preventDefault();
  const form=event.currentTarget,fd=new FormData(form),button=$('#save-config');
  const body={
    tushare_base_url:String(fd.get('tushare_base_url')||'').trim(),
    tushare_points_tier:Number(fd.get('tushare_points_tier')||0),
    alpha_vantage_symbols:String(fd.get('alpha_vantage_symbols')||'').trim(),
    ocr_mode:String(fd.get('ocr_mode')||'disabled'),
    llm_enabled:form.elements.llm_enabled.checked,
    llm_base_url:String(fd.get('llm_base_url')||'').trim(),
    llm_model:String(fd.get('llm_model')||'').trim(),
    akshare_enabled:form.elements.akshare_enabled.checked,
    monitor_enabled:form.elements.monitor_enabled.checked,
    monitor_interval_seconds:Number(fd.get('monitor_interval_seconds')||60),
    monitor_max_symbols:Number(fd.get('monitor_max_symbols')||18),
    feishu_enabled:form.elements.feishu_enabled.checked,
    feishu_app_id:String(fd.get('feishu_app_id')||'').trim(),
    feishu_allowed_users:String(fd.get('feishu_allowed_users')||'').trim(),
    feishu_notify_chat_id:String(fd.get('feishu_notify_chat_id')||'').trim(),
    clear_secrets:[...form.querySelectorAll('[data-clear-secret]:checked')].map(x=>x.dataset.clearSecret),
  };
  for(const name of ['tushare_token','llm_api_key','alpha_vantage_api_key','baidu_ocr_api_key','baidu_ocr_secret_key','feishu_app_secret']){
    const value=String(fd.get(name)||'').trim();if(value)body[name]=value;
  }
  button.disabled=true;button.textContent='正在安全保存…';
  try{
    const result=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    ensureConfigDialog().close();
    notify(result.hot_reloaded?'配置已保存并立即生效':'配置已保存；当前轮询尚未结束，请重启服务后生效');
    await load();
  }catch(error){notify(error.message,true)}finally{button.disabled=false;button.textContent='保存并立即应用'}
}

if(state.view==='system'&&state.data)renderSystem();
if(new URLSearchParams(location.search).get('config')==='open')setTimeout(openConfigDialog,0);
