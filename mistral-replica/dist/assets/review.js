/* Database-backed review UI. Account changes are applied only by the server. */
(() => {
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  let onlineCrmBase = null;
  const fields = [['name','Facility name'],['street','Street'],['city','City'],['state','State'],['zip','ZIP'],['care','Care offerings'],['parent','Parent'],['phone','Phone']];
  let requests = [], contacts = [], lastData = '', busy = false;
  const columnMap={billing_street:'street',billing_city:'city',billing_state:'state',billing_zip:'zip',care_type:'care',parent_name:'parent'};
  const dbFields=Object.fromEntries(Object.entries(columnMap).map(([k,v])=>[v,k]));
  const displayRecord = a => a ? Object.fromEntries(Object.entries(a).map(([k,v])=>[columnMap[k]||k,['lifetime_revenue','outstanding_ar'].includes(k)&&v!=null?'$'+Number(v).toLocaleString('en-US'):v])) : null;
  const displayValues = values => Object.fromEntries(Object.entries(values||{}).map(([k,v])=>[columnMap[k]||k,v]));
  function adapt(p) {
    const e=p.supporting_evidence,ops=p.proposed_changes,cl=p.classification;
    const create=ops.find(o=>o.action==='create');
    const kind=cl==='duplicate_resolution'?'duplicate':create?(ops.some(o=>o.action==='update')?'create-link':'create'):ops.length?'update':'none';
    const type=kind==='duplicate'?'duplicate':kind==='create-link'?'chow':kind==='create'?'location':kind==='none'?'confident':cl==='absent_from_website'?'orphan':'fix';
    const web=e.website?{...e.website,care:JSON.parse(e.website.care_offerings||'[]').join(', '),parent:'Bellhaven Senior Living (Parent Account)'}:null;
    const changes=displayValues(create?.values||ops.find(o=>o.action==='update')?.values||{});
    return {id:p.proposal_id,crmId:p.account_id,type,kind,crm:displayRecord(e.crm),web,changes,
      title:p.title,classification:cl==='confident_match'&&ops.length?'Website information update':cl.replaceAll('_',' '),rationale:p.explanation,evidence:e.bullets,
      sourceUrl:p.source_url,raw:p,operations:ops,
      billing:kind==='create-link'||!!changes.parent_id,
      duplicates:e.accounts?.map(a=>({id:a.account_id,data:displayRecord(a)}))};
  }
  function ingest(data) {
    const signature=JSON.stringify([data.mode,data.crm_link_base,data.production,data.proposals,data.accounts,data.contacts]);
    if(signature===lastData)return;
    lastData=signature;onlineCrmBase=data.crm_link_base||null;contacts=data.contacts||[];state.mode=data.mode||"test";state.production=data.production;
    // Automatic no-change matches are remembered for scans, but are not review requests.
    requests=data.proposals.filter(p=>!p.decision?.automatic).map(adapt);state.decisions={};
    for(const r of requests){
      const d=r.raw.decision;if(!d)continue;
      const create=d.approved_changes.find(o=>o.action==='create');
      const vals=displayValues(create?.values||d.approved_changes.find(o=>o.action==='update')?.values||{});
      const history=r.raw.history.find(h=>h.result==='succeeded');
      const newAccount=history?.before_values.find(x=>x.values===null&&!x.contact_id)?.account_id;
      const survivor=r.kind==='duplicate'?(d.choice==='reviewed'?'both':d.approved_changes.find(o=>o.values?.duplicate_of_account)?.values.duplicate_of_account||''):null;
      state.decisions[r.id]={choice:d.choice==='rejected'?'declined':d.choice==='approved'&&r.kind!=='duplicate'&&!r.operations.some(o=>o.action==='resolve_administrator')&&JSON.stringify(d.approved_changes)!==JSON.stringify(r.operations)?'manual':d.choice,
        values:vals,changes:vals,note:d.reviewer_note||'',submitted:!!d.submitted_at,date:d.decided_at,
        automatic:!!d.automatic,
        reviewer:d.automatic?'Automatic':d.reviewer_note==='Account resolved as a duplicate.'?'Automatic duplicate resolution':'Local reviewer',
        facility:r.crm?.name||r.web?.name||r.id,crmId:r.crmId||survivor,newCrmId:newAccount,before:r.crm||{},
        write:d.approved_changes.length>0,session:d.submitted_at?d.decision_id:null,survivor,
        phoneAccountId:d.approved_changes.find(o=>o.duplicate_resolution)?.duplicate_resolution.phone_account_id||'',
        beforeRecords:Object.fromEntries((r.duplicates||[]).map(c=>[c.id,c.data])),
        duplicateOperations:d.approved_changes.filter(o=>o.action==='update'),revision:d.decided_at};
    }
    state.selected=new Set([...state.selected].filter(id=>requests.some(r=>r.id===id&&!state.decisions[id]?.submitted)));
    render();
  }
  async function remote(path,body) {
    const data=await parent.Bellhaven.mutate(path,body);state.error='';ingest(data);return data;
  }
  function stageItem(r,choice,values) {
    const draft=r.kind==='duplicate'?duplicateDraft(r):null;
    if (!values && ['approved','manual'].includes(choice)) values=cardEditValues(r);
    return {proposal_id:r.id,version:r.raw.version,revision:state.decisions[r.id]?.revision||null,
      choice:choice==='declined'?'rejected':choice==='manual'?'approved':choice,
      note:draft?.note??state.drafts[r.id]?.reviewNote??'',survivor:draft?.pick,
      ...(draft?.phoneAccountId?{phone_account_id:draft.phoneAccountId}:{}),
      administrator_resolution:state.administratorDrafts[r.id],
      ...(values?{manual_values:Object.fromEntries(Object.entries(values).filter(([k])=>!['parent','parent_id'].includes(k)).map(([k,v])=>[dbFields[k]||k,v]))}:{})};
  }
  const typeLabels = {
    1:'Resolve possible duplicate',
    2:'Update information',
    3:'Update address',
    4:'Mark inactive',
    5:'Rename and update',
    6:'Possible CHOW with no financial data',
    7:'CHOW with financial data and AR',
    8:'Create new missing account',
    9:'Update parent',
    10:'Update address and parent'
  };
  const state = {mode:'test',production:null,previewing:false,recoveryIds:{},view:'home',bucket:'review',type:'2',allExpanded:true,expanded:null,normalized:new Set(),selected:new Set(),modal:false,modalOpen:null,candidate:{},duplicateDrafts:{},administratorDrafts:{},decisions:{},editing:null,editingDecision:null,drafts:{},reports:[],notice:'',error:'',submissionSuccess:false,historyExpanded:null,historyFilter:'all',submitting:false,priorityIds:[],sort:'Most recent'};
  const root = $('review-app');
  const chevron = open => `<svg class="review-chevron${open ? ' is-open' : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg>`;
  const icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M14 3H5v18h14V8l-5-5Z"/><path d="M14 3v5h5M8 12h8M8 16h5"/></svg>';
  const action = (name,label,id='',primary=false,disabled=false) => `<button type="button" class="review-button${primary?' primary':''}" data-action="${name}" ${id?`data-id="${id}"`:''} ${disabled?'disabled':''}>${label}</button>`;
  const pretty = key => Object.fromEntries(fields)[key] || ({parent_id:'Parent account ID',status:'Status',note:'Note',chow_current_account:'CHOW current account',duplicate_of_account:'Duplicate of account',lifetime_revenue:'Lifetime revenue',outstanding_ar:'Outstanding receivables',id:'Account ID',account_id:'Account ID',phone:'Phone',administrator:'Administrator',contact_id:'Contact ID',title:'Role',email:'Email',is_active:'Active',created_by_candidate:'Created by candidate',updated_at:'Last updated',source_url:'Source URL',fetched_at:'Website retrieved',contact_count:'Number of contacts',created_at:'Created',last_activity:'Last activity'}[key] || key.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase()));
  const value = input => input === '' || input == null ? '<span class="request-muted">Not provided</span>' : escape(input);
  const duplicateDraft = r => {const d=state.decisions[r.id];return d&&state.editingDecision!==r.id?{pick:d.survivor||'',note:d.note||'',phoneAccountId:d.phoneAccountId||''}:state.duplicateDrafts[r.id]||{pick:'',note:'',phoneAccountId:''};};
  const duplicateContacts = r => r.raw.supporting_evidence.contacts || contacts.filter(c=>r.duplicates.some(a=>a.id===c.account_id));
  const duplicatePhoneConflict = r => new Set(r.duplicates.map(c=>r.raw.comparison_values?.accounts?.[c.id]?.phone??c.data.phone??'')).size>1;
  const duplicateReady = r => r.kind!=='duplicate'||(!!duplicateDraft(r).note.trim()&&(duplicateDraft(r).pick==='both'||(r.duplicates.some(c=>c.id===duplicateDraft(r).pick)&&(!duplicatePhoneConflict(r)||r.duplicates.some(c=>c.id===duplicateDraft(r).phoneAccountId)))));
  const currentCRM = r => r.kind==='duplicate'?(r.duplicates.find(c=>c.id===duplicateDraft(r).pick)||r.duplicates[0]).data: r.candidates?.find(c=>c.id===state.candidate[r.id])?.data || r.crm;
  const currentId = r => r.kind==='duplicate'?(duplicateDraft(r).pick==='both'?null:duplicateDraft(r).pick||null):state.candidate[r.id] || r.crmId;
  const isCreate = r => ['create','create-link'].includes(r.kind);
  const staged = () => requests.filter(r=>state.decisions[r.id]&&!state.decisions[r.id].submitted);
  const changesFor = r => {
    const d=state.decisions[r.id];
    if(r.kind==='duplicate') return {};
    if(d?.choice==='declined'||d?.choice==='reviewed'||r.kind==='none'||r.kind==='review') return {};
    return d?.values || r.changes;
  };
  const hasWrite = r => {const d=state.decisions[r.id];if(r.kind==='duplicate')return !!(d?.choice==='approved'&&d.survivor&&d.survivor!=='both');return d&&['approved','manual'].includes(d.choice)&&r.raw.decision.approved_changes.length>0;};
  const countUpdates = () => staged().filter(hasWrite).length;
  const choiceLabel = d => d.automatic?'Automatically approved — no changes':({approved:'Approved',declined:'Declined',manual:'Manually edited',reviewed:'Reviewed — no change'})[d.choice];
  const bucketRows = () => requests.filter(r=>['ready','decided'].includes(r.raw.review_state)).filter(r=>state.bucket==='all'||(['review','open'].includes(state.bucket)?!state.decisions[r.id]:state.bucket==='completed'?!!state.decisions[r.id]&&!state.decisions[r.id].submitted:state.bucket==='closed'?!!state.decisions[r.id]?.submitted:false));
  function announce(message) {$('review-announcement').textContent=message;}
  function signal() {
    requestAnimationFrame(()=>{
      parent.postMessage({type:'crm-controls-update'},'*');
      parent.postMessage({type:'crm-content-height',height:Math.ceil($('crm-review-content').getBoundingClientRect().height)},'*');
    });
  }
  function filters() {
    const open=requests.filter(r=>!state.decisions[r.id]).length, completed=staged().length,closed=requests.filter(r=>state.decisions[r.id]?.submitted).length;
    const filter=(key,label,count,child=false)=>`<button type="button" class="review-filter${state.bucket===key?' is-active':''}" data-action="bucket" data-key="${key}" aria-pressed="${state.bucket===key}"><span>${label}</span><span class="review-count">${count}</span></button>`;
    return `<aside class="crm-filters"><div class="crm-filters-heading"><h2>Filters</h2></div><div class="review-filter-group" aria-label="Request filters">
      ${filter('review','Requests to review',open)}
      <button type="button" class="review-filter${state.bucket==='all'?' is-active':''}" data-action="all" aria-expanded="${state.allExpanded}" aria-controls="request-status-filters">${chevron(state.allExpanded)}<span>All requests</span><span class="review-count">${requests.length}</span></button>
      <div class="review-filter-children" id="request-status-filters" ${state.allExpanded?'':'hidden'}>${filter('open','Open',open,true)}${filter('completed','Completed',completed,true)}${filter('closed','Closed',closed,true)}</div></div>
      <div><div class="request-section-title"><h2 class="review-filter-heading">By card type</h2>${state.type?'<button type="button" class="review-link" data-action="clear-type">Clear</button>':''}</div><div class="review-filter-group review-filter-types">${['2','3','4','5','6','7','8','9','10','1'].map(key=>[key,typeLabels[key]]).map(([key,label])=>`<button type="button" class="review-filter${state.type===key?' is-active':''}" data-action="type" data-key="${key}" aria-pressed="${state.type===key}"><span>${label}</span><span class="review-count">${bucketRows().filter(r=>String(cardType(r))===key).length}</span></button>`).join('')}</div></div></aside>`;
  }
  const contactOperations = r => (state.decisions[r.id] && state.editingDecision!==r.id ? r.raw.decision.approved_changes : r.operations).filter(o=>['create_contact','update_contact','resolve_administrator'].includes(o.action));
  const savedContacts = r => r.raw.supporting_evidence.contacts || contacts.filter(c=>c.account_id===r.crmId);
  const activeAdministrators = r => savedContacts(r).filter(c=>c.is_active===1 && (c.title||'').trim().toLowerCase()==='administrator');
  function writeList(r) {
    const ops=cardPreviewOperations(r);
    return ops.map(op=>{
      if(op.action==='resolve_administrator')return ['REVIEW','Administrator','Choose which contact to use and which former administrators to deactivate'];
      if(op.action==='resolve_duplicates')return ['REVIEW','Choose survivor','Mark other accounts Inactive and link them to the survivor'];
      if(op.action==='append_note')return ['NOTE',op.account_id,op.text];
      const isContact=op.action.endsWith('_contact');
      const title=op.action==='create'?'New local account':op.action==='create_contact'?`New contact: ${op.values.name}${op.source_contact_id?' (copy of '+op.source_contact_id+')':''}`:isContact?op.contact_id:op.account_id;
      return [op.action.startsWith('create')?'CREATE':'UPDATE',title,Object.entries(op.values).map(([k,v])=>(isContact&&k==='name'?'Name':pretty(columnMap[k]||k))+': '+(v&&typeof v==='object'?'new account ID':k==='is_active'?(v?'Active':'Inactive'):v??'Not provided')).join(' · ')];
    });
  }
  function administratorReady(r) {
    if(!r.operations.some(o=>o.action==='resolve_administrator'))return true;
    const d=state.administratorDrafts[r.id];
    return !!(d?.contact_id && d.confirmed && !d.deactivate_ids.includes(d.contact_id));
  }
  function contactChanges(r) {
    const ops=contactOperations(r);
    if(!ops.length)return '';
    const resolve=ops.find(o=>o.action==='resolve_administrator');
    if(resolve){
      const draft=state.administratorDrafts[r.id]||{contact_id:'',deactivate_ids:[],confirmed:false};
      const all=savedContacts(r),allowed=all.filter(c=>resolve.candidate_ids.includes(c.contact_id));
      return `<section class="request-contact-changes"><h3>Administrator</h3><p>Website administrator: ${escape(resolve.name)}</p><label>Contact to use <select data-administrator-pick="${r.id}"><option value="">Choose…</option><option value="new" ${draft.contact_id==='new'?'selected':''}>Create a new contact</option>${allowed.map(c=>`<option value="${escape(c.contact_id)}" ${draft.contact_id===c.contact_id?'selected':''}>${escape(c.name)} · ${escape(c.title)} · ${escape(c.contact_id)}</option>`).join('')}</select></label><p>Choose former administrators to mark inactive. Unchecked contacts stay active.</p>${all.filter(c=>resolve.administrator_ids.includes(c.contact_id)).map(c=>`<label class="request-toggle"><input type="checkbox" data-administrator-former="${r.id}" value="${escape(c.contact_id)}" ${draft.deactivate_ids.includes(c.contact_id)?'checked':''}>${escape(c.name)} · ${escape(c.contact_id)}</label>`).join('')}<label class="request-toggle"><input type="checkbox" data-administrator-confirm="${r.id}" ${draft.confirmed?'checked':''}>I have checked the contact to use and which former administrators to deactivate.</label></section>`;
    }
    const rows=ops.flatMap(op=>{
      const old=savedContacts(r).find(c=>c.contact_id===op.contact_id);
      const label=op.action==='create_contact'?'New contact':`${old?.name||op.contact_id} · ${op.contact_id}`;
      return Object.entries(op.values).filter(([k,v])=>!['account_id','created_by_candidate'].includes(k)&&v!=null).sort(([a],[b])=>['name','title','is_active','phone','email'].indexOf(a)-['name','title','is_active','phone','email'].indexOf(b)).map(([k,v])=>`<tr><td>${escape(label)}</td><td>${k==='name'?'Name':escape(pretty(k))}</td><td>${op.action==='create_contact'?'—':comparisonValue(k,old?.[k])}</td><td><strong>${comparisonValue(k,v)}</strong></td></tr>`);
    }).join('');
    return `<section class="request-contact-changes"><h3>Administrator changes</h3><div class="request-table-wrap"><table class="request-table"><thead><tr><th>Contact</th><th>Field</th><th>Current value</th><th>Proposed value</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }
  // Build comparison rows from every available business field, not a fixed subset.
  const comparisonMeta = new Set(['run_id','snapshot_id','raw_html','address_fields_present','care_offerings']);
  const comparisonText = input => input == null || input === '' ? '' : typeof input==='object' ? JSON.stringify(input) : String(input);
  const websiteComparableFields = new Set([...fields.map(([key])=>key),'phone','administrator']);
  const websiteOnlyFields = new Set(['source_url','fetched_at']);
  const importantFields = ['name','street','city','state','zip','parent','parent_id'];
  const compareKey = (key, raw, normalized) => comparisonText(normalized && Object.hasOwn(normalized,key) ? normalized[key] : raw);
  function comparisonFields(records, changes = {}, comparableKeys = null, normalizedRecords = []) {
    const keys = new Set([...fields.map(([key])=>key), ...records.flatMap(r=>Object.keys(r||{})), ...Object.keys(changes)]);
    return [...keys].filter(key=>!comparisonMeta.has(key)&&!key.startsWith('normalized_'))
      .map(key=>{
        const values=records.map(r=>r?.[key]);
        const comparable=!comparableKeys || (comparableKeys.has(key) && records.every(Boolean));
        const different=comparable && values.some((v,i)=>compareKey(key,v,normalizedRecords[i])!==compareKey(key,values[0],normalizedRecords[0]));
        const contact=key.match(/^contact:(\d+):(.+)$/);
        return {key,values,different,label:contact?`Contact ${Number(contact[1])+1} · ${contact[2]==='name'?'Name':pretty(contact[2])}`:pretty(key)};
      }).sort((a,b)=>Number(b.different)-Number(a.different) || (a.different ? (importantFields.includes(a.key)?importantFields.indexOf(a.key):99)-(importantFields.includes(b.key)?importantFields.indexOf(b.key):99) : 0));
  }
  function withContacts(record, id, sourceContacts=contacts) {
    if (!record) return null;
    const result={...record};
    const linked=sourceContacts.filter(c=>c.account_id===id).sort((a,b)=>(a.title||'').localeCompare(b.title||'')||(a.name||'').localeCompare(b.name||'')||a.contact_id.localeCompare(b.contact_id));
    result.contact_count=linked.length;
    linked.forEach((contact,index)=>Object.entries(contact).forEach(([key,v])=>{result[`contact:${index}:${key}`]=v;}));
    return result;
  }
  function comparisonValue(key, input) {
    const field=key.split(':').pop();
    if (input!=null && input!=='' && ['created_by_candidate','is_active'].includes(field)) return Number(input)?'Yes':'No';
    if (input!=null && input!=='' && ['lifetime_revenue','outstanding_ar'].includes(field) && !String(input).startsWith('$')) return escape('$'+Number(input).toLocaleString('en-US'));
    if (input && typeof input==='object') return input.created_account?'New account ID (assigned on approval)':escape(JSON.stringify(input));
    return value(input);
  }
  function compare(r) {
    const crm = withContacts(currentCRM(r),currentId(r),savedContacts(r)), ch = {...cardValues(r)};
    if(crm)crm.administrator=activeAdministrators(r).map(c=>c.name).join('; ');
    if(contactOperations(r).length)ch.administrator=r.web?.administrator;
    const normalized=r.raw.comparison_values||{};
    const rows = comparisonFields([r.web,crm],ch,websiteComparableFields,[normalized.website,normalized.crm]).map(({key,label,values:[b,a],different}) => {
      const hasProposed=ch[key]!==undefined && (isCreate(r) || compareKey(key,ch[key],normalized.proposed)!==compareKey(key,a,normalized.crm));
      const proposed = hasProposed ? `<strong>${comparisonValue(key,ch[key])}</strong>` : '—';
      const unavailable='<span class="request-muted" title="This source does not provide this field">—</span>';
      const websiteCell=websiteComparableFields.has(key)||websiteOnlyFields.has(key)?comparisonValue(key,b):unavailable;
      const crmCell=websiteOnlyFields.has(key)?unavailable:comparisonValue(key,a);
      return `<tr class="${different?'is-different':''}"><td>${escape(label)}</td><td>${websiteCell}</td><td>${crmCell}</td><td class="proposed-plain">${proposed}</td></tr>`;
    }).join('');
    return `<div class="request-section-title"><h3>Field comparison</h3></div>
      <div class="request-table-wrap"><table class="request-table"><thead><tr><th>CRM field</th><th>Website<br><a class="request-source-link" href="${escape(r.sourceUrl || 'https://analyst-assessment-production.up.railway.app/communities')}" target="_blank" rel="noopener noreferrer">${r.sourceUrl?'View':'View directory'}</a></th><th>CRM at review ${crmViews(r.crmId)}</th><th>${isCreate(r)?'New CRM account values':'Proposed new CRM field'}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function rationaleSection(r) {
    const facts=[['Matching logic',cardMatch(r)],['Why this action',escape(r.rationale)]];
    if(cardType(r)===6)facts.push(['Financial data',cardMoney(r)]);
    return `<section class="card-drawer-section" aria-labelledby="rationale-${r.id}"><h3 id="rationale-${r.id}">Rationale</h3>${cardFacts(facts)}</section>`;
  }
  function proposal(r) {
    const d = state.decisions[r.id];
    if (d && d.choice === 'declined') return '<div class="request-proposal"><p class="request-rationale">You declined this suggestion. The CRM record stays unchanged.</p></div>';
    const writes = writeList(r);
    const writesBlock = writes.length ? `<details class="request-writes"><summary>View writes on approval</summary><div class="request-writes-body">${writes.map(w => '<div class="request-write"><span class="request-verb">' + w[0] + '</span><span><strong>' + escape(w[1]) + '</strong><br>' + escape(w[2]) + '</span></div>').join('')}</div></details>` : '';
    return writesBlock?`<div class="request-proposal">${writesBlock}</div>`:'';
  }
  function editor(r) {
    const draft=state.drafts[r.id];
    return `<form class="request-editor" data-edit-form="${r.id}"><h4>${isCreate(r)?'Edit the new account':'Edit CRM values manually'}</h4><p class="request-note">${isCreate(r)?'These values will create a new account.':'Start from the suggested values. Review all changes below before saving.'} Ownership follows the reviewed billing rule.</p><div class="request-form-grid">${[...fields,['status','Status'],['note','Note']].map(([key,label])=>`<label class="${key==='note'?'full-width':''}">${label}${key==='status'?`<select name="status"><option ${draft.status==='Active'?'selected':''}>Active</option><option ${draft.status==='Inactive'?'selected':''}>Inactive</option><option ${draft.status==='Needs Review'?'selected':''}>Needs Review</option></select>`:key==='note'?`<textarea name="note" rows="2">${escape(draft.note)}</textarea>`:`<input name="${key}" value="${escape(draft[key])}" ${['name','street','city','state'].includes(key)?'required':''} ${key==='parent'?'readonly':''}>`}</label>`).join('')}</div><div class="request-editor-diff" id="editor-diff-${r.id}">${manualDiff(r)}</div><div class="request-actions"><button type="submit" class="review-button primary">Save manual decision</button>${action('cancel-manual','Cancel',r.id)}</div></form>`;
  }
  function manualValues(r) {
    const draft=state.drafts[r.id];
    const clean=Object.fromEntries(Object.entries(draft).filter(([k])=>[...fields.map(([f])=>f),'status','note'].includes(k)).map(([k,v])=>[k,String(v).trim()]));
    if(isCreate(r)) return clean;
    return Object.fromEntries(Object.entries(clean).filter(([k,v])=>String(r.crm?.[k]??'')!==v));
  }
  function manualDiff(r) {
    const values=manualValues(r), keys=Object.keys(values).filter(k=>k!=='parent_id');
    return keys.length ? `<strong>${isCreate(r)?'New account values':'Your changes'}</strong><ul>${keys.map(k=>`<li>${escape(pretty(k))}: ${isCreate(r)?'':`${escape(r.crm?.[k]||'Not provided')} → `}<strong>${escape(values[k]||'Empty')}</strong></li>`).join('')}</ul>`:'No values changed yet.';
  }
  function decisionControls(r) {
    const d=state.decisions[r.id];
    if(d&&state.editingDecision!==r.id) return `<div class="request-decision"><div class="request-actions"><span class="request-status">${escape(choiceLabel(d))} · ${d.submitted?'Submitted':'Not submitted'}</span>${!d.submitted?action('edit-decision','Edit decision',r.id):''}</div></div>`;
    return `<div class="request-decision"><h3>Your decision</h3>${r.kind==='review'?`<p class="request-note">Record this case for follow-up without changing the CRM.</p><label class="request-toggle">Review note <input aria-label="Review note" data-review-note="${r.id}" value="${escape(state.drafts[r.id]?.reviewNote||'')}" placeholder="Optional context"></label><div class="request-actions" style="margin-top:12px">${action('reviewed','Save review — no change',r.id,true)}</div>`:`<div class="request-actions">${action('approve',r.kind==='none'?'Confirm match — no change':'Approve suggestion',r.id,true)}${action('decline','Decline suggestion',r.id)}</div>`}<label class="request-toggle">Reviewer note <input aria-label="Review note" data-review-note="${r.id}" value="${escape(state.drafts[r.id]?.reviewNote||'')}" placeholder="Optional context"></label>${state.editingDecision===r.id?action('cancel-decision','Keep previous decision',r.id):''}${state.editing===r.id?editor(r):''}</div>`;
  }
  function changeSummary(r) {
    if(r.kind==='duplicate')return {label:null,from:'',to:duplicateOutcome(r)};
    const crm = currentCRM(r) || {};
    const ch = {...changesFor(r)};
    if(contactOperations(r).length){ch.administrator=r.web?.administrator;crm.administrator=activeAdministrators(r).map(c=>c.name).join('; ')||'No active administrator';}
    if (r.kind==='create-link') return {label:'account',from:'Existing account under '+(crm.parent||'unknown parent'),to:'New account under Bellhaven; preserve and link old account'+(contactOperations(r).length?'; add administrator '+r.web.administrator:'')};
    if (isCreate(r)) return {label:'account',from:'No CRM account yet',to:'New account under '+(ch.parent||'Bellhaven')+(contactOperations(r).length?'; add administrator '+r.web.administrator:'')};
    const keys=Object.keys(ch).filter(k=>k!=='parent_id').sort((a,b)=>(importantFields.includes(a)?importantFields.indexOf(a):99)-(importantFields.includes(b)?importantFields.indexOf(b):99));
    if(keys.length) return {label:keys.length===1?pretty(keys[0]).toLowerCase():'values',from:keys.map(k=>crm[k]??'Not provided').join(' · '),to:keys.length===1?String(ch[keys[0]]):keys.map(k=>pretty(k)+': '+ch[k]).join(' · ')};
    return {label:null,from:'',to:'No CRM changes'};
  }

  function changeMarkup(ch) {
    if (!ch.label) return escape(ch.to);
    return `<span class="request-change"><span class="request-change-label">Change ${escape(ch.label)}</span><span class="request-change-row"><span class="request-change-label">From:</span> <span class="request-change-value">${escape(ch.from)}</span></span><span class="request-change-row"><span class="request-change-label">To:</span> <span class="request-change-value">${escape(ch.to)}</span></span></span>`;
  }

  function evidenceLines(r) {
    return (r.evidence||[]).map(text=>[
      'CRM parent differs or is missing.',
      'The parent recorded in the CRM differs from Bellhaven or is missing.'
    ].includes(text)?(String(r.crm?.parent_id??'').trim()?'CRM parent differs from Bellhaven.':'CRM parent is missing.'):text);
  }
  function ownershipCheck(r) {
    if (!r.billing) return '';
    const crm=r.crm||{},raw=r.raw.supporting_evidence.crm||{};
    const zeroReason=Number(raw.lifetime_revenue)===0 && Number(raw.outstanding_ar)===0
      ?'both lifetime revenue and outstanding receivables are zero'
      :Number(raw.lifetime_revenue)===0?'lifetime revenue is zero':'outstanding receivables are zero';
    const text=r.kind==='create-link'
      ?'The Change of Ownership (CHOW) rule applies here because this account has both revenue history and unpaid receivables.'
      :`A separate CHOW account is not required because ${zeroReason}.`;
    return `<div class="request-ownership"><strong>Ownership check</strong><br>Lifetime revenue: ${escape(crm.lifetime_revenue||'Not provided')}<br>Outstanding receivables: ${escape(crm.outstanding_ar||'Not provided')}<br>${escape(text)}</div>`;
  }
  function billingLines(r) {
    const crm = currentCRM(r) || {};
    if (!crm.lifetime_revenue || crm.lifetime_revenue === '$0') return '';
    return '<small>Lifetime revenue ' + escape(crm.lifetime_revenue) + '</small><small>Outstanding AR ' + escape(crm.outstanding_ar) + '</small>';
  }
  function duplicateOutcome(r) {
    if(state.decisions[r.id]?.choice==='declined'&&state.editingDecision!==r.id)return 'No changes — duplicate resolution declined';
    const draft=duplicateDraft(r),pick=draft.pick;
    if(pick==='both')return 'Both records kept active — no duplicate links added';
    if(!pick)return 'Choose a survivor to see the outcome';
    const losers=r.duplicates.filter(c=>c.id!==pick).map(c=>c.id).join(', ');
    const copyCount=duplicateContacts(r).filter(c=>c.account_id!==pick).length;
    const phone=draft.phoneAccountId?r.duplicates.find(c=>c.id===draft.phoneAccountId)?.data.phone:null;
    return `Keep ${pick} active · retain ${duplicateContacts(r).length} contacts${copyCount?` (${copyCount} copied with new IDs)`:''}${draft.phoneAccountId?` · phone: ${phone||'Not provided'}`:''} · mark ${losers} Inactive and link to ${pick}`;
  }
  function duplicateContactPreview(r) {
    const draft=duplicateDraft(r),d=state.decisions[r.id];
    if(!draft.pick||draft.pick==='both'||(d?.choice==='declined'&&state.editingDecision!==r.id))return '';
    const all=duplicateContacts(r),copies=all.filter(c=>c.account_id!==draft.pick);
    const oldDecision=d&&state.editingDecision!==r.id&&!r.raw.decision.approved_changes.some(o=>o.duplicate_resolution);
    if(oldDecision)return '<section class="duplicate-preservation"><h3>Contacts</h3><p>This earlier decision does not include contact preservation. Edit and approve it again before submitting.</p></section>';
    const history=r.raw.history.find(h=>h.result==='succeeded');
    return `<section class="duplicate-preservation"><h3>Contacts retained on ${escape(draft.pick)}</h3><p>${copies.length?`${copies.length} contact${copies.length===1?'':'s'} copied with new contact IDs, preserving names, roles, emails, phones and active status. Original contacts stay on the Inactive account as inactive copies.`:'All contacts already belong to the survivor.'}</p>${all.length?`<div class="request-table-wrap"><table class="request-table duplicate-contacts"><thead><tr><th>Name / role</th><th>Email / phone</th><th>Current account</th><th>Outcome on survivor</th></tr></thead><tbody>${all.map(c=>{
      const copy=c.account_id!==draft.pick;
      const index=r.raw.decision?.approved_changes.findIndex(o=>o.action==='create_contact'&&o.source_contact_id===c.contact_id);
      const newId=index>=0?history?.after_values[index]?.contact_id:null;
      return `<tr><td><strong>${value(c.name)}</strong><br>${value(c.title)}<br><small>${escape(c.contact_id)}</small></td><td>${value(c.email)}<br>${value(c.phone)}</td><td>${escape(c.account_id)}</td><td>${copy?(newId?'Copied · '+escape(newId):'Copy with new contact ID'):'Keep existing contact ID'}<br>${c.is_active===1?'Active':'Inactive'}</td></tr>`;
    }).join('')}</tbody></table></div>`:'<p>No contacts on these accounts.</p>'}</section>`;
  }
  function duplicateOperations(r) {
    const d=state.decisions[r.id],draft=duplicateDraft(r);
    if(!draft.pick||draft.pick==='both'||d?.choice==='declined')return [];
    return r.duplicates.filter(c=>c.id!==draft.pick).map(c=>({account_id:c.id,values:{status:'Inactive',duplicate_of_account:draft.pick,note:[c.data.note,draft.note.trim()].filter(Boolean).join('\n')}}));
  }
  function duplicateDecisionDetail(r,d) {
    if(d.choice==='declined')return '<p>No CRM changes. Duplicate resolution was declined.</p>';
    if(d.survivor==='both')return '<p>Both records remain active. No duplicate links are added.</p>';
    return `<p>Surviving account: ${escape(d.survivor)}. Both records are preserved.</p><table class="decisions-diff"><thead><tr><th>Account</th><th>Field</th><th>Before</th><th>${d.submitted?'Submitted value':'Staged value'}</th></tr></thead><tbody>${d.duplicateOperations.flatMap(op=>Object.entries(op.values).map(([field,after])=>`<tr><td>${escape(op.account_id)}</td><td>${escape(pretty(field))}</td><td>${value(d.beforeRecords[op.account_id][field])}</td><td>${value(after)}</td></tr>`)).join('')}</tbody></table>`;
  }
  function duplicateCard(r) {
    const expanded=state.expanded===r.id,d=state.decisions[r.id],draft=duplicateDraft(r),both=draft.pick==='both';
    const locked=!!d&&state.editingDecision!==r.id;
    const comparisons=comparisonFields(r.duplicates.map(c=>withContacts(c.data,c.id,duplicateContacts(r))),{},null,r.duplicates.map(c=>r.raw.comparison_values?.accounts?.[c.id]));
    const accountValue=(c,key)=>key==='address'?[c.data.street,c.data.city,c.data.state,c.data.zip].filter(Boolean).join(', '):c.data[key];
    return `<div class="request-row"><label class="request-pick"><input type="checkbox" aria-label="Select duplicate resolution for ${escape(r.web.name)}" data-select="${r.id}" ${state.selected.has(r.id)?'checked':''}></label><article class="request-card duplicate-card${expanded?' is-expanded':''}" id="card-${r.id}">
      <div class="duplicate-head" data-duplicate-summary="${r.id}"><div class="duplicate-main"><div class="duplicate-titlebar">
        <button type="button" class="duplicate-title" data-action="expand" data-id="${r.id}" aria-expanded="${expanded}" aria-controls="details-${r.id}">${escape(r.title)}</button>
        <label class="duplicate-selector">Survivor<select data-duplicate-pick="${r.id}" aria-label="Survivor for ${escape(r.web.name)}" ${locked?'disabled':''}><option value="">Choose…</option>${r.duplicates.map(c=>`<option value="${c.id}" ${draft.pick===c.id?'selected':''}>${escape(c.id)}</option>`).join('')}<option value="both" ${both?'selected':''}>Keep both — not duplicates</option></select></label>
      </div><div class="duplicate-accounts">${r.duplicates.map(c=>{
        const keep=draft.pick===c.id,lose=draft.pick&&!both&&!keep;
        return `<section class="duplicate-account${keep?' is-survivor':''}${lose?' is-loser':''}" aria-label="CRM account ${c.id}"><div class="duplicate-account-top"><strong>${escape(c.id)}</strong><span class="duplicate-tag${keep?' is-survivor':''}">${both?'Kept':keep?'Survivor':lose?(d?.submitted?'Inactive duplicate':'Mark Inactive'):''}</span></div><strong>${escape(c.data.name)}</strong><span>${escape(accountValue(c,'address'))}</span><span>Parent: ${escape(c.data.parent)}</span>${expanded?`<span>Last updated ${escape(c.data.updated_at||"Not provided")}</span><span>Lifetime revenue ${escape(c.data.lifetime_revenue)} · AR ${escape(c.data.outstanding_ar)}</span>`:''}</section>`;
      }).join('')}</div>${draft.pick&&!both?`<div class="duplicate-phone"><label class="duplicate-selector">Facility phone${duplicatePhoneConflict(r)?`<select data-duplicate-phone="${r.id}" aria-label="Facility phone for ${escape(r.web.name)}" ${locked?'disabled':''}><option value="">Choose the phone to keep…</option>${r.duplicates.map(c=>`<option value="${c.id}" ${draft.phoneAccountId===c.id?'selected':''}>${escape(c.data.phone||'Not provided')} · ${escape(c.id)}</option>`).join('')}</select>`:`<strong>${value(r.duplicates.find(c=>c.id===draft.pick)?.data.phone)}</strong>`}</label><p>${escape(duplicateOutcome(r))}</p></div>`:''}<label class="duplicate-note"><span>Note</span><input data-duplicate-note="${r.id}" aria-label="Duplicate resolution note for ${escape(r.web.name)}" value="${escape(draft.note)}" placeholder="${both?'Why both records should remain active':'Why this record survives — saved with the decision'}" ${locked?'readonly':''}></label>
      ${d?`<div class="request-actions"><span class="request-status">${escape(choiceLabel(d))} · ${d.submitted?'Submitted':'Not submitted'}</span>${!d.submitted&&locked?action('edit-decision','Edit decision',r.id):state.editingDecision===r.id?action('cancel-decision','Keep previous decision',r.id):''}</div>`:''}
      </div><div class="duplicate-actions">${locked?'':`<button type="button" class="request-mini primary" data-action="approve" data-id="${r.id}" ${duplicateReady(r)?'':'disabled'}>Approve</button><button type="button" class="request-mini" data-action="decline" data-id="${r.id}">Decline</button>`}</div></div>
      <div class="request-expanded duplicate-expanded" id="details-${r.id}" ${expanded?'':'hidden'}>${duplicateContactPreview(r)}${rationaleSection(r)}<div class="duplicate-comparison-heading"><h3>Field comparison</h3><span>${escape(duplicateOutcome(r))}</span></div>
      <div class="request-table-wrap"><table class="request-table duplicate-table"><thead><tr><th scope="col">CRM field</th>${r.duplicates.map(c=>`<th scope="col">${escape(c.id)} ${crmViews(c.id)}</th>`).join('')}</tr></thead><tbody>${comparisons.map(({key,label,values,different})=>{
        return `<tr class="${different?'duplicate-difference':''}"><th scope="row">${escape(label)}</th>${r.duplicates.map((c,index)=>`<td class="${draft.pick&&!both&&draft.pick!==c.id?'duplicate-muted':''}">${comparisonValue(key,values[index])}</td>`).join('')}</tr>`;
      }).join('')}</tbody></table></div>${cardLinks(r)}</div></article></div>`;
  }
  // Layout numbers follow the supplied Card 2–10 design references. The saved
  // proposal, rather than its title or sample facility name, determines the layout.
  function cardType(r) {
    if(r.kind==='duplicate')return 1;
    if(r.kind==='create-link')return 7;
    if(r.kind==='create')return 8;
    if(r.type==='orphan')return 4;
    const ch=r.changes, address=['street','city','state','zip'].some(k=>Object.hasOwn(ch,k));
    const parent=Object.hasOwn(ch,'parent_id')||Object.hasOwn(ch,'parent');
    return address?(parent?10:3):Object.hasOwn(ch,'name')?(parent?6:5):parent?9:2;
  }
  const cardEditableFields = new Set(['name','street','city','state','zip','phone','care','note']);
  const cardEdits = {};
  const fullAddress = record => [record?.street,record?.city,record?.state,record?.zip].filter(Boolean).join(', ');
  function cardEditValues(r) {
    const previous=state.editingDecision===r.id?state.decisions[r.id]?.values:undefined;
    const edits={...Object.fromEntries(Object.entries(previous||{}).filter(([k])=>cardEditableFields.has(k))),...cardEdits[r.id]};
    if(!edits || (state.decisions[r.id]&&state.editingDecision!==r.id))return undefined;
    const result=Object.fromEntries(Object.entries(edits).map(([k,v])=>[k,String(v??'').trim()]).filter(([k,v])=>v!==String(r.changes[k]??'')));
    for(const [key,v] of Object.entries(result)) {
      if(['name','street','city','state'].includes(key)&&!v)throw new Error(`${pretty(key)} cannot be empty.`);
      if(v.length>10000)throw new Error(`${pretty(key)} is too long.`);
    }
    return Object.keys(result).length?result:undefined;
  }
  function cardValues(r) {
    return {...changesFor(r),...(!state.decisions[r.id]||state.editingDecision===r.id?cardEdits[r.id]:{})};
  }
  function cardPreviewOperations(r) {
    const ops=state.decisions[r.id]?r.raw.decision.approved_changes:r.operations;
    const edits=cardEdits[r.id];
    if(!edits||(state.decisions[r.id]&&state.editingDecision!==r.id))return ops;
    const target=ops.find(o=>o.action==='create')||ops.find(o=>o.action==='update');
    const values=Object.fromEntries(Object.entries(edits).filter(([k])=>cardEditableFields.has(k)).map(([k,v])=>[dbFields[k]||k,v.trim()]));
    return ops.map(op=>op===target?{...op,values:{...op.values,...values}}:op);
  }
  function cardAccountRows(r, combineAddress=false) {
    const ch=cardValues(r),crm=r.crm||{};
    const order=['name','street','city','state','zip','parent','care','phone','status','note'];
    const rows=order.filter(k=>Object.hasOwn(ch,k)).map(key=>({key,label:pretty(key),from:crm[key],to:ch[key],website:r.web?.[key]}));
    if(combineAddress&&rows.some(f=>['street','city','state','zip'].includes(f.key))) {
      const first=rows.findIndex(f=>['street','city','state','zip'].includes(f.key));
      const condensed=rows.filter(f=>!['street','city','state','zip'].includes(f.key));
      condensed.splice(first,0,{key:'address',label:'Address',from:fullAddress(crm),to:fullAddress({...crm,...ch}),website:fullAddress(r.web)});
      return condensed;
    }
    return rows;
  }
  function cardContactRows(r) {
    return contactOperations(r).map(op=>{
      const old=savedContacts(r).find(c=>c.contact_id===op.contact_id);
      if(op.action==='resolve_administrator')return {key:'administrator',label:'Administrator',from:activeAdministrators(r).map(c=>c.name).join('; '),to:op.name,verb:'Review',hint:'Choose the contact and confirm former administrators below.'};
      if(op.action==='create_contact')return {key:'administrator',label:'Administrator',from:'',to:op.values.name,verb:'Add',hint:'Create an active Administrator contact.'};
      if(op.values.is_active===0)return {key:'administrator',label:'Former administrator',from:old?.name||op.contact_id,to:'Inactive',hint:'Keep the contact on the account.'};
      return {key:'administrator',label:'Administrator',from:old?.name||op.contact_id,to:op.values.name||old?.name,verb:'Update',hint:Object.entries(op.values).filter(([k])=>k!=='name').map(([k,v])=>`${pretty(k)}: ${k==='is_active'?(v?'Active':'Inactive'):v}`).join(' · ')};
    });
  }
  function cardChangeRows(rows) {
    return `<div class="card-change-list">${rows.map(f=>`<div class="card-change-row"><span class="card-label">${escape(f.label)}</span><span class="card-before">${f.verb?escape(f.verb):value(f.from)}</span><span class="card-arrow" aria-hidden="true">${f.verb?'':'→'}</span><span class="card-after">${value(f.to)}</span></div>`).join('')}</div>`;
  }
  function cardFacts(items, extra='') {
    return `<div class="card-facts ${extra}">${items.filter(([,v])=>v).map(([label,v])=>`<span class="card-label">${escape(label)}</span><div>${v}</div>`).join('')}</div>`;
  }
  // Use saved matching evidence, including wording from older runs.
  const matchingChecklistLabels = {
    "Names match.": [["Same facility name", true]],
    "The website and CRM record have the same facility name.": [["Same facility name", true]],
    "Addresses match.": [["Same address", true]],
    "The website and CRM record have the same address.": [["Same address", true]],
    "Names and addresses match.": [["Same facility name", true], ["Same address", true]],
    "The website and CRM record have the same facility name and address.": [["Same facility name", true], ["Same address", true]],
    "Names match, but addresses differ.": [["Same facility name", true], ["Address differs", false]],
    "The website and CRM record have the same facility name, but their addresses differ.": [["Same facility name", true], ["Address differs", false]],
    "Addresses differ; no CRM address matches.": [["Address differs; no CRM address matches", false]],
    "Their addresses differ, and no CRM account matches the website address.": [["Address differs; no CRM address matches", false]],
    "Names differ or CRM name is missing.": [["Facility name differs or is missing in CRM", false]],
    "The facility name in the CRM differs from the website name or is missing.": [["Facility name differs or is missing in CRM", false]],
    "Addresses match, but names differ or CRM name is missing.": [["Facility name differs or is missing in CRM", false], ["Same address", true]],
    "The website and CRM record have the same address, but the facility name in the CRM differs or is missing.": [["Facility name differs or is missing in CRM", false], ["Same address", true]],
    "Addresses match; names match.": [["Same facility name", true], ["Same address", true]],
    "Addresses match; names differ.": [["Facility name differs", false], ["Same address", true]],
    "The website and CRM record have the same address, but their facility names differ.": [["Facility name differs", false], ["Same address", true]],
    "Addresses match; names are missing in CRM.": [["Facility name is missing in CRM", false], ["Same address", true]],
    "The website and CRM record have the same address, but the facility name is missing from the CRM.": [["Facility name is missing in CRM", false], ["Same address", true]],
    "CRM parent is Bellhaven.": [["CRM parent is Bellhaven", true]],
    "The CRM account belongs to Bellhaven.": [["CRM parent is Bellhaven", true]],
    "CRM parent of the record is Bellhaven.": [["CRM parent is Bellhaven", true]],
    "CRM parent differs from Bellhaven.": [["CRM parent differs from Bellhaven", false]],
    "CRM parent is missing.": [["CRM parent is missing", false]],
    "No CRM name matches.": [["No CRM facility name matches", false]],
    "No eligible CRM account matches the facility name on the website.": [["No CRM facility name matches", false]],
    "No CRM address matches.": [["No CRM address matches", false]],
    "No eligible CRM account matches the facility address on the website.": [["No CRM address matches", false]],
  };
  function cardMatch(r) {
    const items=evidenceLines(r).flatMap(text=>matchingChecklistLabels[text]||[[text,null]]);
    if(!items.length)return '';
    return `<ul class="matching-checklist" role="list">${items.map(([label,matched])=>`<li><span>${escape(label)}</span><span class="matching-check-icon${matched===true?' is-match':''}" aria-hidden="true">${matched===true?'✓':matched===false?'×':'•'}</span></li>`).join('')}</ul>`;
  }
  function cardMoney(r) {
    const crm=r.crm||{};
    return `Lifetime revenue ${escape(crm.lifetime_revenue??'Not provided')} · Outstanding AR ${escape(crm.outstanding_ar??'Not provided')}`;
  }
  function cardInput(r,f) {
    const locked=!!state.decisions[r.id]&&state.editingDecision!==r.id;
    if(!cardEditableFields.has(f.key)||locked)return `<strong>${value(f.to)}</strong>`;
    const edited=Object.hasOwn(cardEdits[r.id]||{},f.key)&&cardEdits[r.id][f.key]!==String(changesFor(r)[f.key]??'');
    return `<span class="card-input-wrap"><input aria-label="${escape(f.label)} to write" data-card-edit="${r.id}" data-field="${f.key}" value="${escape(f.to)}" ${['name','street','city','state'].includes(f.key)?'required':''}><span class="card-edit-hint">${edited?'Edited by you':'Proposed value'}</span></span>`;
  }
  function cardAccountPanel(r,existing=false) {
    const record=existing?r.crm:cardValues(r);
    const rows=[['Name',record?.name],['Parent',record?.parent],['Address',fullAddress(record)],['Phone',record?.phone],['Care',record?.care]];
    if(!existing&&contactOperations(r).some(o=>o.action==='create_contact'))rows.push(['Admin','Add '+r.web?.administrator]);
    return `<section class="card-account-panel${existing?' is-existing':''}"><h3>${existing?'Existing account — kept for billing':'New account — to be created'}</h3>${cardFacts(rows.map(([k,v])=>[k,value(v)]))}${existing?`${state.expanded===r.id?`<p class="card-money">${cardMoney(r)}</p>`:''}<span class="card-account-id">${escape(r.crmId)}</span>`:''}</section>`;
  }
  function cardTitle(r,type) {
    const name=r.crm?.name||r.web?.name||'Unnamed facility';
    if(r.kind==='none')return `No changes needed for ${name}`;
    return ({2:`Update information on ${name}`,3:`Update address for ${name}`,4:`Mark ${name} inactive`,5:`${typeLabels[5]} ${name}`,6:`${typeLabels[6]} for ${name}`,7:r.title,8:`Create account for ${r.web?.name||name}`,9:`Update parent for ${name}`,10:`Update address and parent for ${name}`})[type];
  }
  function cardDrawer(r,type) {
    const rows=cardAccountRows(r);
    const structured=[6,7,8,10].includes(type);
    let body='';
    if(type===4) {
      const note=r.operations.find(o=>o.action==='append_note')?.text;
      body=cardChangeRows(rows)+cardFacts([['Note',note?`<p class="card-note-preview">${escape(note)}</p><small>Appended to the account note when this decision is submitted.</small>`:'']]);
    } else if(structured) {
      body=`<section class="card-write-section"><h4>${isCreate(r)?'New account creation actions':'Account field updates'}</h4><p class="card-section-caption">Review the proposed values. Edit fields before approving.</p>${rows.map(f=>`<div class="card-write-row${isCreate(r)?' is-create':''}"><span class="card-label">${escape(f.label)}</span>${isCreate(r)?'':`<span class="card-before">${value(f.from)}</span><span class="card-arrow" aria-hidden="true">→</span>`}${cardInput(r,f)}</div>`).join('')}</section>`;
      if(type===7)body+=`<section class="card-write-section card-divider"><h4>Link the existing account</h4>${cardFacts([['Current account link',`${value(r.crm?.chow_current_account)} <span class="card-arrow">→</span> New account ID, assigned on submission`],['Left untouched','Name, parent, address, status, phone, care offerings and financial history.']])}</section>`;
    } else {
      body=rows.map(f=>`<section class="card-field-detail"><h4>${escape(f.label)}</h4>${cardFacts([['Website',value(f.website)],['CRM at review',value(f.from)],['Write',cardInput(r,f)]])}</section>`).join('');
    }
    const contactRows=cardContactRows(r);
    if(contactRows.length)body+=`<section class="card-write-section card-divider"><h4>Contact actions</h4>${contactOperations(r).some(o=>o.action==='resolve_administrator')?contactChanges(r):contactRows.map(f=>cardFacts([[f.verb||'Set inactive',`${value(f.to==='Inactive'?f.from:f.to)}<small>${escape(f.hint)}${f.to==='Inactive'?' Status: Active → Inactive.':''}</small>`]])).join('')}</section>`;
    body=`<section class="card-drawer-section card-divider" aria-labelledby="actions-${r.id}"><h3 id="actions-${r.id}">Suggested Actions</h3>${body||'<p class="card-match-line">No CRM changes are suggested.</p>'}</section>`;
    body=rationaleSection(r)+body;
    // Keep the complete record comparison available without making it the drawer's main layout.
    body+=`<details class="card-full-record card-divider"><summary>Full field comparison</summary>${compare(r)}${proposal(r)}</details>`;
    body+=cardLinks(r);
    body+=decisionControls(r);
    return body;
  }
  function card(r) {
    if(r.kind==='duplicate')return duplicateCard(r);
    const expanded=state.expanded===r.id,d=state.decisions[r.id],type=cardType(r),locked=d&&state.editingDecision!==r.id;
    const rows=[...cardAccountRows(r,true),...cardContactRows(r)];
    let summary=isCreate(r)?`<div class="card-account-panels${type===8?' is-single':''}">${cardAccountPanel(r)}${type===7?cardAccountPanel(r,true):''}</div>`:cardChangeRows(rows);
    if(type===6)summary=cardFacts([['Update',`${escape(r.crm?.name)} <span class="card-account-id">${escape(r.crmId)}</span>`]])+summary;
    if(type===4||(expanded&&type===9))summary+=cardFacts([['Financial data',`<span class="${Number(r.raw.supporting_evidence.crm?.outstanding_ar)>0?'card-money':''}">${cardMoney(r)}</span>`]]);
    if(expanded&&type===9)summary+=cardFacts([['Ownership check','Update this account; a separate CHOW account is not required.']]);
    return `<div class="request-row"><label class="request-pick"><input type="checkbox" aria-label="Select ${escape(r.crm?.name||r.web?.name||'request')}" data-select="${r.id}" ${state.selected.has(r.id)?'checked':''}></label><article class="request-card design-card card-type-${type}${expanded?' is-expanded':''}" data-card-type="${type}" id="card-${r.id}"><div class="card-summary" data-card-summary="${r.id}"><div class="card-topline"><div class="card-heading"><button type="button" class="card-title" data-action="expand" data-id="${r.id}" aria-expanded="${expanded}" aria-controls="details-${r.id}">${escape(cardTitle(r,type))}</button><span class="card-subline">${escape(fullAddress(r.crm||r.web))}</span></div><div class="card-actions">${locked?`<span class="request-status">${escape(choiceLabel(d))} · ${d.submitted?'Submitted':'Not submitted'}</span>${!d.submitted?action('edit-decision','Edit decision',r.id):''}`:`<button class="request-mini primary" type="button" data-action="approve" data-id="${r.id}">Approve</button><button class="request-mini" type="button" data-action="decline" data-id="${r.id}">Decline</button>`}</div></div>${d?.choice==='declined'&&locked?'<p class="card-match-line">No CRM changes. This suggestion was declined.</p>':summary}</div><div class="request-expanded card-drawer" id="details-${r.id}" ${expanded?'':'hidden'}>${expanded?cardDrawer(r,type):''}</div></article></div>`;
  }
  function modalRow(r) {
    const d = state.decisions[r.id];
    const crm = currentCRM(r) || {};
    const ch = changeSummary(r);
    const open = state.modalOpen === r.id;
    const billing = (crm.lifetime_revenue && crm.lifetime_revenue !== '$0') ? '<span>Lifetime revenue ' + escape(crm.lifetime_revenue) + '</span><span>Outstanding AR ' + escape(crm.outstanding_ar) + '</span>' : '';
    const detail = open ? '<div class="submit-detail"><span><span class="request-eyebrow">Target Facility</span><span>' + escape([crm.street, crm.city, crm.state].filter(Boolean).join(', ') || 'No CRM address') + '</span>' + billing + '</span><span><span class="request-eyebrow">Proposed change</span>' + changeMarkup(ch) + '</span><span><span class="request-eyebrow">Evidence</span>' + evidenceLines(r).map(l => '<span class="request-evidence-line"><span>✓</span><span>' + escape(l) + '</span></span>').join('') + '</span></div>' + (r.kind==='duplicate'?duplicateContactPreview(r):'') : '';
    return '<div class="submit-row"><button type="button" class="submit-row-main" data-action="modal-expand" data-id="' + r.id + '"><span class="submit-row-name">' + escape(crm.name || (r.web ? r.web.name : r.id)) + '</span><span class="submit-row-change">' + escape(ch.label ? ch.label + ' → ' + ch.to : ch.to) + '</span></button><button type="button" class="submit-row-delete" ' + (isProduction()&&['running','paused'].includes(state.production?.status)?'disabled ':'') + 'data-action="drop-decision" data-id="' + r.id + '">Delete</button>' + detail + '</div>';
  }
  const isProduction = () => state.mode==='production';
  const submissionItems = () => staged().map(r=>({proposal_id:r.id,revision:state.decisions[r.id].revision}));
  async function prepareCalls() {
    state.previewing=true;state.error='';render();
    try {await remote('/api/production/preview',{items:submissionItems()});}
    finally {state.previewing=false;render();}
  }
  function equivalentCode(r) {
    const lines=['import json, os, requests', '', 'body = json.loads('+JSON.stringify(JSON.stringify(r.body))+')'];
    for(const [key,value] of Object.entries(r.body))if(value&&typeof value==='object'&&value.result_of){
      lines.push('body['+JSON.stringify(key)+'] = result_'+value.result_of+'["account_id"]');
    }
    lines.push('', 'response = requests.request(', '    '+JSON.stringify(r.method)+',', '    '+JSON.stringify(r.url)+',',
      '    headers={"Authorization": "Bearer " + os.environ["BELLHAVEN_API_TOKEN"]},',
      '    json=body, timeout=(10, 30), allow_redirects=False,', ')', 'response.raise_for_status()', 'result_'+r.ordinal+' = response.json()');
    return lines.join('\n');
  }
  function apiPreview() {
    const p=state.production;
    if(state.previewing)return '<div class="api-preview"><p>Reading the CRM and preparing your exact API calls… No writes are being sent.</p></div>';
    if(!p||p.status==='invalidated')return '<div class="api-preview"><p>Prepare a fresh API preview before confirming.</p>'+action('refresh-api-preview','Prepare API preview')+'</div>';
    const progress=Object.fromEntries(p.progress.map(r=>[r.ordinal,r]));
    const active=['running','paused'].includes(p.status);
    return '<section class="api-preview"><h3>Production API calls · '+escape(p.status)+'</h3><p>These saved requests run in this order, only after confirmation. Authentication is supplied by the server. Each write is checked with a GET before continuing.</p><p>A <code>result_of</code> value means the actual account ID returned by that numbered call. No other request values are regenerated.</p>'+
      (p.error?'<p class="api-error" role="alert">'+escape(p.error)+'</p>':'')+
      '<details class="api-plan-id"><summary>Saved submission reference</summary><p>'+escape(p.plan_id)+' · SHA-256 '+escape(p.digest)+'</p></details>'+
      p.plan.requests.map(r=>{const step=progress[r.ordinal];return '<article class="api-call"><h4>'+r.ordinal+'. '+escape(r.method)+' '+escape(r.path)+' <span>'+escape(step.status)+'</span></h4><pre tabindex="0">'+escape(r.method+' '+r.url+'\nAuthorization: Bearer [server credential]\nContent-Type: application/json\n\n'+JSON.stringify(r.body,null,2))+'</pre>'+
        '<details><summary>Equivalent Python request</summary><pre>'+escape(equivalentCode(r))+'</pre></details>'+
        (step.resolved_body&&step.resolved_body!==JSON.stringify(r.body)?'<details><summary>Resolved body sent</summary><pre>'+escape(JSON.stringify(JSON.parse(step.resolved_body),null,2))+'</pre></details>':'')+
        '<p>Verification: <code>GET '+escape(r.method==='PATCH'?r.url:r.url+'/'+(step.remote_id||'[ID returned by call '+r.ordinal+']'))+'</code></p>'+
        (step.remote_id?'<p>CRM record: '+escape(step.remote_id)+'</p>':'')+
        (step.error?'<p class="api-error">'+escape(step.error)+'</p>':'')+
        (r.method==='POST'&&['uncertain','sending'].includes(step.status)&&!step.remote_id?'<label>Created record ID, after checking the CRM <input data-recovery-id="'+r.ordinal+'" value="'+escape(state.recoveryIds[r.ordinal]||'')+'" placeholder="Paste the new account/contact ID"></label>':'')+'</article>';}).join('')+
      (!p.plan.requests.length?'<p>These decisions require no CRM writes.</p>':'')+
      (p.status==='preview'?action('refresh-api-preview','Refresh API preview'):'')+
      (active&&p.progress.every(r=>['pending','failed'].includes(r.status))?action('cancel-api-plan','Discard unexecuted plan'):'')+
      '<p>Requests are separate CRM operations. If one fails, this screen keeps the verified results and stops the remaining calls.</p></section>';
  }
  function modal() {
    const batch = staged();
    const groups = [
      ['Approvals — writes to CRM', batch.filter(r => ['approved','manual'].includes(state.decisions[r.id].choice))],
      ['Declines — no changes to CRM', batch.filter(r => state.decisions[r.id].choice === 'declined')],
      ['Review only — note added', batch.filter(r => state.decisions[r.id].choice === 'reviewed')]
    ].filter(g => g[1].length);
    const u = countUpdates();
    return '<div class="submit-overlay"><div class="submit-modal" role="dialog" aria-label="Review and submit"><div class="submit-modal-head"><span>Review and submit</span><button type="button" class="submit-close" data-action="close-submit" aria-label="Close">×</button></div><div class="submit-modal-body">' +
      (state.error?'<p role="alert" style="color:#9b342b;padding:12px 24px">'+escape(state.error)+'</p>':'')+
      (isProduction()?apiPreview():'')+
      groups.map(g => '<div class="submit-group"><div class="submit-group-head"><span>' + g[0] + '</span><span>' + g[1].length + '</span></div>' + g[1].map(modalRow).join('') + '</div>').join('') +
      '</div><div class="submit-modal-foot"><span>'+(isProduction()?(state.production?.status==='complete'?'All API calls verified':'Confirmation sends these calls to the live CRM'):'Changes apply to demo.sqlite on submission')+'</span><span class="submit-foot-actions">' + action('close-submit','Close') + action('submit', state.submitting ? 'Submitting…' : isProduction() ? (state.production?.status==='complete'?'Completed':['paused','running'].includes(state.production?.status)?'Check results and resume':'Confirm and run API calls') : u ? 'Submit ' + u + ' and update test database' : 'Submit ' + batch.length + (batch.length === 1 ? ' decision' : ' decisions'), '', true, !batch.length || state.submitting || (isProduction()&&(state.previewing||!['preview','paused','running'].includes(state.production?.status)))) + '</span></div></div></div>';
  }
  function home() {
    const rows=bucketRows().filter(r=>!state.type||String(cardType(r))===state.type).sort((a,b)=>Number(state.priorityIds.includes(b.id))-Number(state.priorityIds.includes(a.id)) || (state.sort==='Facility name A–Z'?(a.crm?.name||a.web?.name||'').localeCompare(b.crm?.name||b.web?.name||''):state.sort==='Card type'?typeLabels[cardType(a)].localeCompare(typeLabels[cardType(b)]):state.sort==='Highest risk'?Number(b.kind==='create-link')-Number(a.kind==='create-link'):Number(b.kind==='duplicate')-Number(a.kind==='duplicate')));
    const titles={review:'Requests to review',all:'All requests',open:'Open requests',completed:'Completed requests',closed:'Closed requests'};
    const n=staged().length,u=countUpdates();
    return `${isProduction()&&['running','paused'].includes(state.production?.status)?'<div class="review-notice"><span>Production submission '+escape(state.production.status)+'. Review the saved calls and results before continuing.</span>'+action('open-submit','View submission')+'</div>':''}<div class="crm-home-grid">${filters()}<section><div class="crm-list-heading"><h2>${titles[state.bucket]} <span>(${rows.length})</span>${rows.length?`<button type="button" class="request-selectall" data-action="select-all">${rows.every(item=>state.selected.has(item.id))?'Clear selection':'Select all'}</button>`:''}</h2><div class="review-heading-actions">${state.selected.size?`<span class="request-batch-count"><strong>${state.selected.size} selected</strong></span>${action('batch-approve','Approve selected','',true)}${action('batch-decline','Decline selected')}${action('batch-clear','Clear')}`:`${n?action('open-submit',`Review and submit (${n})`,'',true):`<label class="review-sort">Sort<select class="review-sort-select">${["Most recent","Highest risk","Facility name A–Z","Card type"].map(v=>`<option ${state.sort===v?"selected":""}>${v}</option>`).join("")}</select></label>`}`}</div></div>${state.notice?`<div class="review-notice"><span>${escape(state.notice)}</span><button type="button" class="review-link" data-action="${state.submissionSuccess?'view-decisions':'bucket'}" data-key="completed">${state.submissionSuccess?'View Decisions':'View completed'}</button></div>`:''}<div class="request-list">${rows.length?rows.map(card).join(''):'<div class="review-empty">No requests in this view.</div>'}</div></section></div>`;
  }
  function summaryChanges(r) {
    if(r.kind==='duplicate')return state.decisions[r.id].choice==='declined'?'No write · Duplicate resolution declined':duplicateOutcome(r);
    if(!hasWrite(r))return state.decisions[r.id].choice==='declined'?'No write · Suggestion declined':'No write · Review recorded';
    return `${isCreate(r)?'Create account':'Update '+r.crmId}${r.kind==='create-link'?' + link '+r.crmId:''}: `+Object.entries(changesFor(r)).filter(([k,v])=>k!=='parent_id'&&(isCreate(r)||String(v)!==String(r.crm?.[k]??''))).map(([k,v])=>`${pretty(k)} ${isCreate(r)?'':`${r.crm?.[k]||'Empty'} → `}${v||'Empty'}`).join('; ')+(contactOperations(r).length?'; '+writeList(r).filter(w=>w[1]?.includes('contact')||r.raw.decision.approved_changes.some(o=>o.contact_id===w[1])).map(w=>w.join(' · ')).join('; '):'');
  }
  function submission() {
    const selected=staged(),u=countUpdates();
    return `<section class="review-page">${action('back','← Back to review')}<h2>Review your submission</h2><p>${selected.length} decision${selected.length===1?'':'s'} saved. ${u} account update${u===1?'':'s'} staged. Declined requests and no-change reviews are recorded without a CRM write.</p><div class="review-notice"><span>${isProduction()?'Review the saved API calls before confirming production changes.':'Submit saves these decisions and approved changes to demo.sqlite.'}</span></div><div class="request-table-wrap"><table class="request-table"><thead><tr><th>Facility</th><th>Decision</th><th>Changes to submit</th></tr></thead><tbody>${selected.map(r=>`<tr><td>${escape(r.crm?.name||r.web?.name)}</td><td>${escape(choiceLabel(state.decisions[r.id]))}</td><td>${escape(summaryChanges(r))}</td></tr>`).join('')}</tbody></table></div><div class="review-submit-actions">${action('back','Keep reviewing','',false,state.submitting)}${action('submit',state.submitting?'Submitting…':'Submit decisions','',true,state.submitting||!selected.length)}</div></section>`;
  }
  const decisionDate = date => new Intl.DateTimeFormat('en-GB',{dateStyle:'medium',timeStyle:'short'}).format(new Date(date));
  function crmLink(id, label = id) {
    if (!id) return '<span class="decisions-muted">No CRM account</span>';
    // Accounts created in Test mode do not have an online page.
    if (id.startsWith('LOCAL-')) return `<span class="decisions-muted">${escape(label)} · No online CRM page</span>`;
    if (!onlineCrmBase) return `<span class="decisions-muted" title="Add your CRM API key to the local .env file and restart the server to enable online CRM links.">${escape(label)} · Local key required</span>`;
    return `<a class="decisions-crm-link" href="${escape(onlineCrmBase+encodeURIComponent(id))}" target="_blank" rel="noopener noreferrer" aria-label="View online CRM record ${escape(id)}">${escape(label)}</a>`;
  }
  function crmViews(id) {
    return `<br>${crmLink(id,'View online CRM')}`;
  }
  function cardLinks(r) {
    const accountIds=[...new Set([...(r.duplicates?.map(c=>c.id)||[r.crmId]),state.decisions[r.id]?.newCrmId])]
      .filter(id=>id&&!id.startsWith('LOCAL-'));
    const links=accountIds.map((id,index)=>crmLink(id,accountIds.length>1?`CRM record ${index+1}`:'CRM record'));
    const website=r.sourceUrl||r.web?.source_url;
    if(website)links.push(`<a class="decisions-crm-link" href="${escape(website)}" target="_blank" rel="noopener noreferrer">Website</a>`);
    return links.length?`<section class="card-drawer-section card-divider"><h3>Links</h3><div class="request-actions">${links.join('')}</div></section>`:'';
  }
  function decisions() {
    const entries=requests.filter(r=>state.decisions[r.id]&&!state.decisions[r.id].automatic).sort((a,b)=>state.decisions[b.id].date.localeCompare(state.decisions[a.id].date));
    const shown=entries.filter(r=>state.historyFilter==='all'||state.decisions[r.id].choice===state.historyFilter||(state.historyFilter==='approved'&&state.decisions[r.id].automatic));
    return `<section class="decisions-page"><div class="decisions-toolbar"><div class="decisions-tabs" role="group" aria-label="Filter decisions">${[['all','All'],['approved','Approved'],['declined','Declined'],['manual','Manually edited'],['reviewed','Reviewed']].map(([key,label])=>`<button type="button" data-action="history-filter" data-key="${key}" aria-pressed="${state.historyFilter===key}">${label}</button>`).join('')}</div>${isProduction()&&state.production?action("view-api-results","View latest API submission"):""}<span class="decisions-muted decisions-preview">${isProduction()?'Production mode · Verified API results':'Test mode · Saved in demo.sqlite'}</span></div><div class="decisions-scroll"><table class="decisions-table"><thead><tr><th>Facility</th><th>Requested change</th><th>Decision</th><th>Decided by</th><th>Date</th><th>Submission</th></tr></thead><tbody>${shown.length?shown.map(r=>{
      const d=state.decisions[r.id],open=state.historyExpanded===r.id;
      const keys=Object.keys(d.changes).filter(k=>k!=='parent_id');
      return `<tr class="decisions-row"><td><button type="button" class="decisions-name" data-action="history-expand" data-id="${r.id}" aria-expanded="${open}" aria-controls="decision-details-${r.id}">${chevron(open)}<span>${escape(d.facility)}</span></button><div class="decisions-record">${crmLink(d.crmId)}${d.newCrmId?`<br>${crmLink(d.newCrmId,'New account · '+d.newCrmId)}`:''}</div></td><td><span class="decisions-change">${escape(r.classification)}</span></td><td><span class="decisions-badge ${d.choice}">${escape(choiceLabel(d))}</span></td><td>${escape(d.reviewer)}</td><td class="decisions-date">${escape(decisionDate(d.date))}</td><td><span class="decisions-submission${d.submitted?' is-submitted':''}">${d.submitted?'Submitted':'Pending submission'}</span></td></tr>${open?`<tr class="decisions-detail" id="decision-details-${r.id}"><td colspan="6"><div class="decisions-detail-inner"><h3>${escape(r.title)}</h3><p>${escape(r.rationale)}</p>${decisionDetail(r)}${d.note?`<p><strong>Reviewer note:</strong> ${escape(d.note)}</p>`:''}<div class="decisions-detail-meta"><span>${escape(r.id)}</span><span>${d.session?escape(d.session)+' · '+(d.write?(isProduction()?'API changes verified':'Local changes saved'):'Decision recorded; no CRM write'):'Awaiting submission'}</span></div></div></td></tr>`:''}`;
    }).join(''):`<tr><td colspan="6" class="decisions-empty">${entries.length?'No decisions match this filter.':'No decisions yet. Approve, decline, or edit a request on Home to see it here.'}</td></tr>`}</tbody></table></div></section>`;
  }
  function decisionDetail(r) {
    const history=r.raw.history.find(h=>h.result==='succeeded');
    if(history){
      return `<table class="decisions-diff"><thead><tr><th>Record</th><th>Field</th><th>Before</th><th>After</th></tr></thead><tbody>${history.after_values.flatMap((a,i)=>Object.entries(a.values).filter(([k,v])=>history.before_values[i]?.values?.[k]!==v).map(([k,v])=>`<tr><td>${escape(a.contact_id||a.account_id)}</td><td>${escape(a.contact_id&&k==='name'?'Name':pretty(columnMap[k]||k))}</td><td>${comparisonValue(k,history.before_values[i]?.values?.[k])}</td><td>${comparisonValue(k,v)}</td></tr>`)).join('')}</tbody></table>`;
    }
    return r.raw.decision.approved_changes.length?`<p>${writeList(r).map(w=>escape(w.join(' · '))).join('<br>')}</p>`:'<p>No account changes. The decision is recorded.</p>';
  }
  function render(focusSelector, resetScroll = false) {
    // Replacing content inside a growing iframe must not move the host's viewport.
    const hostScroller = window.frameElement?.closest('main')?.querySelector('[class*="scrollbar-gutter:stable"]');
    const savedScroll = resetScroll ? 0 : hostScroller?.scrollTop;
    root.innerHTML=(state.view==='decisions'?decisions():state.view==='submit'?submission():home())+(state.modal?modal():'');
    if(focusSelector)root.querySelector(focusSelector)?.focus({preventScroll:true});
    signal();
    if (hostScroller) requestAnimationFrame(() => requestAnimationFrame(() => {
      hostScroller.scrollTo({top:savedScroll, behavior:'instant'});
    }));
  }
  async function saveDecision(r,choice,values) {
    await remote('/api/decisions/stage',{items:[stageItem(r,choice,values)]});
    delete cardEdits[r.id];
    state.editing=null;state.editingDecision=null;
    state.notice=isProduction()?'Decision staged. Review the API calls before confirming production changes.':'Decision staged. Submit to apply approved changes to the test database.';
    render(null,true);announce(state.notice);
  }
  root.addEventListener('click',async event=>{
    let button=event.target.closest('[data-action]');
    // The whole duplicate summary toggles, except its independent controls.
    if(!button&&!event.target.closest('button,input,select,textarea,label,a,summary')){
      button=event.target.closest('[data-duplicate-summary],[data-card-summary]')?.querySelector('[data-action="expand"]');
    }
    if(!button||button.disabled||state.submitting||busy)return;
    busy=true;
    try {
    const {action:act,id,key}=button.dataset,r=requests.find(item=>item.id===id);
    let focus;
    if(act==='view-decisions') {state.view='decisions';parent.postMessage({type:'crm-navigate',view:'decisions'},'*');}
    else if(act==='history-filter') {state.historyFilter=key;}
    else if(act==='history-expand') {state.historyExpanded=state.historyExpanded===id?null:id;}
    else if(act==='expand') {state.expanded=state.expanded===id?null:id;focus=`[data-action="expand"][data-id="${id}"]`;}
    else if(act==='bucket') {state.bucket=key;state.notice='';focus=`[data-action="bucket"][data-key="${key}"]`;}
    else if(act==='all') {state.bucket='all';state.allExpanded=!state.allExpanded;state.notice='';focus='[data-action="all"]';}
    else if(act==='type') {state.type=state.type===key?null:key;focus=`[data-action="type"][data-key="${key}"]`;}
    else if(act==='clear-type') state.type=null;
    else if(act==='select-all') {const list=bucketRows().filter(item=>!state.type||String(cardType(item))===state.type);const all=list.every(item=>state.selected.has(item.id));list.forEach(item=>all?state.selected.delete(item.id):state.selected.add(item.id));focus='[data-action="select-all"]';}
    else if(act==='open-submit') {state.modal=true;state.modalOpen=null;parent.postMessage({type:'crm-review-scroll-top'},'*');parent.postMessage({type:'crm-modal',open:true},'*');if(isProduction()&&!['paused','running'].includes(state.production?.status))await prepareCalls();}
    else if(act==='view-api-results') {state.modal=true;parent.postMessage({type:'crm-modal',open:true},'*');}
    else if(act==='refresh-api-preview') {await prepareCalls();}
    else if(act==='cancel-api-plan') {await remote('/api/production/cancel',{plan_id:state.production.plan_id,digest:state.production.digest});}
    else if(act==='close-submit') {state.modal=false;parent.postMessage({type:'crm-modal',open:false},'*');}
    else if(act==='modal-expand') {state.modalOpen=state.modalOpen===id?null:id;}
    else if(act==='drop-decision') {await remote('/api/decisions/remove',{proposal_id:id,revision:state.decisions[id].revision});if(!staged().length){state.modal=false;parent.postMessage({type:'crm-modal',open:false},'*');}}
    else if(act==='batch-clear') {state.selected.clear();}
    else if(act==='batch-approve'||act==='batch-decline') {
      const selected=requests.filter(r=>state.selected.has(r.id)&&!state.decisions[r.id]?.submitted);
      if(act==='batch-approve'&&selected.some(r=>!administratorReady(r)))throw new Error('Complete the administrator selection before approving.');
      if(act==='batch-approve'&&selected.some(r=>!duplicateReady(r)))throw new Error('Choose a survivor or keep both, resolve any facility phone difference, and add a note before approving duplicates.');
      await remote('/api/decisions/stage',{items:selected.map(r=>stageItem(r,act==='batch-decline'?'declined':r.kind==='none'||(r.kind==='duplicate'&&duplicateDraft(r).pick==='both')?'reviewed':'approved'))});
      state.selected.clear();state.notice='Decisions staged. Submit to apply approved changes.';render(null,true);return;
    }
    else if(act==='approve') {if(!administratorReady(r)){state.expanded=id;render();announce('Choose the administrator and confirm the former contacts to deactivate.');return;}if(!duplicateReady(r)){announce('Choose a survivor or keep both, resolve any facility phone difference, and add a note.');return;}await saveDecision(r,['none','review'].includes(r.kind)||(r.kind==='duplicate'&&duplicateDraft(r).pick==='both')?'reviewed':'approved');return;}
    else if(act==='decline') {await saveDecision(r,'declined');return;}
    else if(act==='reviewed') {await saveDecision(r,'reviewed');return;}
    else if(act==='manual') {
      state.editing=id;
      const starting=state.decisions[id]?.choice==='manual'?{...(r.crm||{}),...state.decisions[id].values}:isCreate(r)?{...r.changes,note:r.changes.note||''}:{...r.crm,...r.changes};
      state.drafts[id]={...Object.fromEntries([...fields,['status'],['note']].map(([k])=>[k,starting?.[k]||''])),...cardEdits[id],reviewNote:state.drafts[id]?.reviewNote||''};
      focus=`[data-edit-form="${id}"] input[name="name"]`;
    }
    else if(act==='cancel-manual') {state.editing=null;focus=`[data-action="manual"][data-id="${id}"]`;}
    else if(act==='edit-decision') {
      const resolution=r.operations.find(o=>o.action==='resolve_administrator');
      if(resolution){
        const ops=r.raw.decision.approved_changes;
        const reuse=ops.find(o=>o.action==='update_contact'&&o.values.is_active!==0);
        state.administratorDrafts[id]={contact_id:ops.some(o=>o.action==='create_contact')?'new':reuse?.contact_id||'',deactivate_ids:ops.filter(o=>o.action==='update_contact'&&o.values.is_active===0).map(o=>o.contact_id),confirmed:false};
      }
      state.drafts[id]={reviewNote:state.decisions[id].note};if(r.kind==='duplicate'){const d=state.decisions[id];state.duplicateDrafts[id]={pick:d.survivor||'',note:d.note,phoneAccountId:d.phoneAccountId||''};}state.editingDecision=id;focus=`[data-action="approve"][data-id="${id}"]`;}
    else if(act==='cancel-decision') {delete cardEdits[id];state.editingDecision=null;state.editing=null;}
    else if(act==='submit-preview') {state.modal=true;parent.postMessage({type:'crm-modal',open:true},'*');if(isProduction())await prepareCalls();}
    else if(act==='back') {state.view='home';}
    else if(act==='submit') {
      const batch=staged();if(!batch.length)return;
      state.submitting=true;render();
      try {
        if(isProduction()&&!['preview','paused','running'].includes(state.production?.status))throw new Error('Prepare and review the API preview first.');
        await remote('/api/decisions/submit',isProduction()?{plan_id:state.production.plan_id,digest:state.production.digest,confirm:true,recovery_ids:state.recoveryIds}:{items:submissionItems()});
        state.priorityIds=requests.filter(r=>batch.some(b=>b.kind==='duplicate'&&r.raw.depends_on_proposal_id===b.id)&&!state.decisions[r.id]).map(r=>r.id);
        state.type=null;state.bucket='review';state.expanded=state.priorityIds[0]||null;
        state.notice=state.priorityIds.length?'Duplicate resolution saved. The released proposals are shown first for separate review.':(isProduction()?'Submitted. API changes were verified and saved to the production mirror and decision history.':'Submitted. Approved changes and decision history are saved in demo.sqlite.');
        state.modal=false;state.selected.clear();state.submissionSuccess=true;
        parent.postMessage({type:'crm-modal',open:false},'*');
        state.view='home';
      } finally {try{if(isProduction())await parent.Bellhaven.refresh();}finally{state.submitting=false;render(null,true);announce(state.notice);}}
      return;
    }
    else return;
    render(focus, ['bucket','all','type','clear-type','submit-preview','back','view-decisions'].includes(act));
    } catch(error){state.error=error.message;parent.Bellhaven?.error(error.message);announce(error.message);render();}
    finally {busy=false;}
  });
  root.addEventListener('input',event=>{
    if(event.target.dataset.cardEdit){
      const id=event.target.dataset.cardEdit,key=event.target.dataset.field,r=requests.find(r=>r.id===id);
      if(!cardEditableFields.has(key))return;
      (cardEdits[id]??={})[key]=event.target.value;
      const hint=event.target.parentElement.querySelector('.card-edit-hint');
      if(hint)hint.textContent=event.target.value!==String(changesFor(r)[key]??'')?'Edited by you':'Proposed value';
      const summary=event.target.closest('.design-card').querySelector('.card-summary');
      const changes=summary.querySelector('.card-change-list'),panels=summary.querySelector('.card-account-panels');
      if(changes)changes.outerHTML=cardChangeRows([...cardAccountRows(r,true),...cardContactRows(r)]);
      if(panels)panels.innerHTML=cardAccountPanel(r)+(r.kind==='create-link'?cardAccountPanel(r,true):'');
      const fullRecord=event.target.closest('.design-card').querySelector('.card-full-record');
      if(fullRecord)fullRecord.innerHTML='<summary>Full field comparison</summary>'+compare(r)+proposal(r);
      signal();return;
    }
    if(event.target.dataset.recoveryId){state.recoveryIds[event.target.dataset.recoveryId]=event.target.value.trim();return;}
    if(event.target.dataset.duplicateNote){
      const id=event.target.dataset.duplicateNote,r=requests.find(r=>r.id===id);
      state.duplicateDrafts[id]={...duplicateDraft(r),note:event.target.value};
      const approve=root.querySelector(`[data-action="approve"][data-id="${id}"]`);if(approve)approve.disabled=!duplicateReady(r);
      return;
    }
    const form=event.target.closest('[data-edit-form]');
    if(form){const r=requests.find(r=>r.id===form.dataset.editForm);state.drafts[r.id][event.target.name]=event.target.value;$(`editor-diff-${r.id}`).innerHTML=manualDiff(r);signal();}
    if(event.target.dataset.reviewNote){const id=event.target.dataset.reviewNote;state.drafts[id]??={};state.drafts[id].reviewNote=event.target.value;}
  });
  root.addEventListener('change',event=>{
    const adminId=event.target.dataset.administratorPick||event.target.dataset.administratorFormer||event.target.dataset.administratorConfirm;
    if(adminId){
      const draft=state.administratorDrafts[adminId]??={contact_id:'',deactivate_ids:[],confirmed:false};
      if(event.target.dataset.administratorPick){draft.contact_id=event.target.value;draft.confirmed=false;}
      if(event.target.dataset.administratorFormer){draft.deactivate_ids=event.target.checked?[...new Set([...draft.deactivate_ids,event.target.value])]:draft.deactivate_ids.filter(id=>id!==event.target.value);draft.confirmed=false;}
      if(event.target.dataset.administratorConfirm)draft.confirmed=event.target.checked;
      render();return;
    }

    if(event.target.matches('.review-sort-select')){state.sort=event.target.value;render();return;}
    if(event.target.dataset.duplicatePick){
      const id=event.target.dataset.duplicatePick,r=requests.find(r=>r.id===id);
      state.duplicateDrafts[id]={...duplicateDraft(r),pick:event.target.value};
      state.expanded=id;
      render(`[data-duplicate-pick="${id}"]`);announce(duplicateOutcome(r));return;
    }
    if(event.target.dataset.duplicatePhone){
      const id=event.target.dataset.duplicatePhone,r=requests.find(r=>r.id===id);
      state.duplicateDrafts[id]={...duplicateDraft(r),phoneAccountId:event.target.value};
      render(`[data-duplicate-phone="${id}"]`);return;
    }
    const normalized=event.target.dataset.normalize,candidate=event.target.dataset.candidate,pick=event.target.dataset.select;
    if(pick){if(event.target.checked)state.selected.add(pick);else state.selected.delete(pick);render(`[data-select="${pick}"]`);}
    if(normalized){if(event.target.checked)state.normalized.add(normalized);else state.normalized.delete(normalized);render(`[data-normalize="${normalized}"]`);}
    if(candidate){state.candidate[candidate]=event.target.value;render(`[data-candidate="${candidate}"][value="${event.target.value}"]`);}
  });
  root.addEventListener('submit',async event=>{
    const form=event.target.closest('[data-edit-form]');if(!form)return;event.preventDefault();
    const r=requests.find(r=>r.id===form.dataset.editForm),draft=state.drafts[r.id];
    for(const key of ['name','street','city','state']) {if(!draft[key].trim()){const input=form.elements.namedItem(key);input.setCustomValidity('Enter a value.');input.reportValidity();input.setCustomValidity('');return;}}
    const values=manualValues(r);if(!Object.keys(values).length){const message='Change at least one CRM value before saving.';$(`editor-diff-${r.id}`).textContent=message;announce(message);return;}
    if(busy)return;busy=true;try{await saveDecision(r,'manual',values);}catch(error){parent.Bellhaven?.error(error.message);announce(error.message);}finally{busy=false;}
  });
  root.addEventListener('toggle',signal,true);
  window.addEventListener('message',event=>{
    if(event.source!==parent)return;
    if(event.data?.type==='crm-data'){ingest(event.data.data);return;}
    if(parent.Bellhaven?.data)ingest(parent.Bellhaven.data);
    if(event.data?.type==='crm-home'){state.view='home';render(null,true);}
    if(event.data?.type==='crm-decisions'){state.view='decisions';render(null,true);}
  });
  render();
  if(parent.Bellhaven?.data)ingest(parent.Bellhaven.data);
})();
