/* Runs are saved pipeline records; polling reads progress without simulating it. */
(() => {
  let rows=[],filter='all',lastRuns='';const expanded=new Set();
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const stages=['scraping','normalization','matching'];
  const date=v=>v?new Date(v).toLocaleString():'In progress';
  const body=document.getElementById('runs-body');
  const startButton=document.getElementById('runs-new-button');startButton.textContent='Run Scraper';
  function render(){
    const visible=rows.filter(r=>filter==='all'||(filter==='pipeline'?r.run_type==='pipeline':r.run_type==='scraper'));
    body.innerHTML=visible.map(r=>`<tr class="run-row"><td><button class="run-id" data-run="${esc(r.run_id)}" aria-expanded="${expanded.has(r.run_id)}">${expanded.has(r.run_id)?'▾':'▸'} ${esc(r.run_id.slice(0,8))}</button></td><td>${esc(r.run_type)}</td><td><span class="run-status ${r.status==='complete'?'succeeded':r.status==='running'?'running':'failed'}">${esc(r.status)}</span></td><td class="run-outcome">${r.status==='running'?esc(r.current_stage)+' · ':''}${r.locations_saved}/${r.locations_found} locations saved${r.matching_status==='complete'?` · ${r.new_proposals} new proposals · ${r.existing_proposals} reused · ${r.decided_proposals_skipped} already decided`:''}</td><td>${r.matching_status==='complete'?'<button class="runs-secondary" data-review>Review proposals</button>':''}</td></tr><tr class="run-detail" ${expanded.has(r.run_id)?'':'hidden'}><td colspan="5"><dl><div><dt>Run ID</dt><dd>${esc(r.run_id)}</dd></div><div><dt>Started</dt><dd>${esc(date(r.started_at))}</dd></div><div><dt>Finished</dt><dd>${esc(date(r.finished_at))}</dd></div><div><dt>Database</dt><dd>demo.sqlite</dd></div></dl><p>${stages.map(s=>esc(s)+': '+esc(r[s+'_status'])).join(' → ')}</p><p>${r.crm_records_normalized} CRM accounts and ${r.website_records_normalized} website records normalized.</p>${r.errors.map(e=>`<p>${esc(e.severity||'error')}: ${esc(e.message)} ${esc(e.source_url||e.account_id||'')}</p>`).join('')}</td></tr>`).join('');
    document.getElementById('runs-empty').hidden=visible.length>0;
    const active=rows.find(r=>r.status==='running'),r=active||rows[0];
    startButton.disabled=!!active||!window.Bellhaven?.selected;
    const done=r?stages.filter(s=>r[s+'_status']==='complete').length:0;
    const progress=r?.status==='complete'?100:Math.floor(done/3*100+(r?.current_stage==='scraping'&&r.locations_found?Math.min(1,r.locations_saved/r.locations_found)*33:0));
    document.getElementById('sidebar-run-title').textContent=active?'Pipeline running':r?`Run ${r.status}`:'No workflow running';
    document.getElementById('sidebar-run-progress').setAttribute('aria-valuenow',progress);
    document.getElementById('sidebar-run-fill').style.width=progress+'%';
    document.getElementById('sidebar-run-percent').textContent=progress+'%';
    document.getElementById('sidebar-run-step').textContent=active?`${r.current_stage} · ${r.locations_saved}/${r.locations_found} saved`:r?.status==='complete'?'Proposals ready on Home':r?'Open Runs for details':'Choose Test mode to start';
  }
  document.querySelectorAll('[data-run-filter]').forEach(tab=>tab.addEventListener('click',()=>{
    filter=tab.dataset.runFilter;document.querySelectorAll('[data-run-filter]').forEach(t=>t.setAttribute('aria-selected',String(t===tab)));render();
  }));
  body.addEventListener('click',event=>{const b=event.target.closest('[data-run]');if(b){expanded.has(b.dataset.run)?expanded.delete(b.dataset.run):expanded.add(b.dataset.run);render();}if(event.target.closest('[data-review]'))document.dispatchEvent(new CustomEvent('crm-show-view',{detail:'home'}));});
  startButton.addEventListener('click',async()=>{startButton.disabled=true;try{await window.Bellhaven.request('/api/runs',{});await window.Bellhaven.refresh();}catch(e){window.Bellhaven.error(e.message);}finally{render();}});
  document.addEventListener('crm-data',({detail})=>{const next=JSON.stringify(detail.runs);if(next===lastRuns)return;lastRuns=next;rows=detail.runs;render();});
  render();
})();
