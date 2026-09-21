/* Same-origin mode-aware client. The server selects the database, never the page. */
(() => {
  let token = null, current = null, polling = false, refreshing = null, resetting = false;
  const notice = document.createElement('p');
  notice.id = 'app-error'; notice.setAttribute('role','alert');
  notice.style.cssText='padding:12px 16px;color:#9b342b;background:#fff0ed;border-radius:6px;display:none';
  document.querySelector('main header').after(notice);
  const error = message => {notice.textContent=message||'';notice.style.display=message?'':'none';};
  async function request(path, data) {
    const response = await fetch(path,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...(token?{'X-Bellhaven-Session':token}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'The request failed');
    return result;
  }
  function publish(data) {
    current=data;
    document.dispatchEvent(new CustomEvent('crm-data',{detail:data}));
    document.getElementById('crm-content')?.contentWindow.postMessage({type:'crm-data',data},location.origin);
  }
  async function refresh() {
    if(!token || resetting)return;
    if(refreshing)return refreshing;
    refreshing=request('/api/state').then(publish).finally(()=>{refreshing=null;});
    return refreshing;
  }
  async function select(mode = "test") {
    const session=await request('/api/session',{mode});
    token=session.token; error('');
    await refresh();
    if(!polling){polling=true;setInterval(()=>refresh().catch(e=>error(e.message)),1500);}
    return session;
  }
  async function mutate(path,data) {
    if(resetting)throw new Error('Test mode is resetting. Please wait.');
    // Finish any older read before publishing a newer transaction result.
    if(refreshing)await refreshing;
    const result=await request(path,data);publish(result);error('');return result;
  }
  const resetButton=document.getElementById('settings-reset-button');
  const resetHelp=document.getElementById('settings-reset-help');
  const resetStatus=document.getElementById('settings-reset-status');
  function resetControls() {
    const active=current?.runs.some(run=>run.status==='running');
    resetButton.disabled=resetting || current?.mode!=='test' || !!active;
    resetButton.textContent=resetting?'Resetting…':'Reset';
    resetHelp.textContent=current?.mode==='production'?'Reset is available only in Test mode. Production data is not changed.':active?'Wait for the active run to finish before resetting.':'Restore the original test data. A backup is saved before resetting.';
  }
  async function reset() {
    if(resetting)return;
    resetting=true;resetControls();resetStatus.textContent='';
    try {
      if(refreshing)await refreshing;
      const data=await request('/api/test/reset',{confirm:true});
      publish(data);error('');
      // Reload the review frame to discard unsaved forms and cached selections.
      const frame=document.getElementById('crm-content');
      if(frame)frame.srcdoc=frame.srcdoc;
      resetStatus.textContent='Test mode reset. Backup saved in data/backups/'+data.reset.backup_file+'. Open Runs and choose Run Scraper to start again.';
    } finally {resetting=false;resetControls();}
  }
  document.addEventListener('crm-data',resetControls);
  window.Bellhaven={select,refresh,mutate,request,error,reset,get data(){return current;},get selected(){return !!token;}};
  const production=document.querySelector('[data-mode="production"]');
  production.disabled=location.protocol==='file:';
  document.getElementById('production-mode-description').textContent='Load the live CRM and review changes. Production mode sends approved changes through the API at the end, after confirmation.';
  document.getElementById('test-mode-description').textContent='Test mode does NOT use the CRM API. Review and apply changes only to your local test database.';
  resetControls();
  const settings=document.createElement('div');settings.className='settings-card';
  settings.innerHTML='<div class="settings-card-copy"><h2>Daily schedule</h2><p>Not connected yet. Start a pipeline manually from Runs.</p></div>';
  document.getElementById('crm-settings').append(settings);
  const tbody=document.querySelector('#crm-sources tbody');tbody.replaceChildren();
  document.addEventListener('crm-data',({detail:data})=>{
    tbody.replaceChildren();
    for(const source of data.sources){
      const tr=document.createElement('tr');
      for(const text of [source.name||'Unnamed facility',source.source_url,new Date(source.fetched_at).toLocaleString(),source.snapshot_id]){
        const td=document.createElement('td');td.textContent=text;tr.append(td);
      }
      tbody.append(tr);
    }
    if(!data.sources.length){const tr=document.createElement('tr');const td=document.createElement('td');td.colSpan=4;td.textContent='No completed website snapshot yet.';tr.append(td);tbody.append(tr);}
  });
  if(location.protocol==='file:'){
    document.querySelector('[data-mode="test"]').disabled=true;
    error('Start .venv/bin/python server.py, then open http://localhost:8000 to use the application.');
  }
})();
